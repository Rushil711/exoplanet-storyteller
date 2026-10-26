"""The story harness: tools, session memory, and the write -> verify -> retry loop.

The system prompt is not stored here. `Skill.system_prompt()` reads it from
`skills/exoplanet-story-grounding/SKILL.md`, which is the source of truth.

Tools
-----
fetch_planet(name)              live TAP record, snapshot fallback
search_planets(text)            natural language -> validated intent -> ADQL -> rows
verify_story(story, record)     the skill's verifier, plus rewrite feedback
generate_image(record)          data-derived prompt, xAI or the mock SVG, cached
narrate(text, voice)            xAI Voice or the mock chime, cached

`tell()` is the action that runs the tools: fetch, write with the skill prompt, verify,
retry with the verifier's feedback up to N times, then fall back to the template story.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import art, compare, query
from .archive import ArchiveError
from .llm import LLMError, NoRecording
from .logs import get_logger
from .media import MediaError

log = get_logger("harness")

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "story": {"type": "string"},
        "numbers_used": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"value": {"type": "string"}, "field": {"type": "string"}},
                "required": ["value", "field"],
            },
        },
    },
    "required": ["story", "numbers_used"],
}

EXAMPLES = [
    "the hottest planet discovered in 2020",
    "a world that might hold liquid water",
    "the nearest planet found by direct imaging",
    "a Jupiter-sized world within 50 light-years",
]


def extract_story(text: str) -> str | None:
    """Pull the story string out of a model reply. None if it isn't usable."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.S).strip()
    try:
        import json
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("story"), str) and data["story"].strip():
            return data["story"].strip()
    except (ValueError, TypeError):
        pass
    m = re.search(r'"story"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.S)
    if m:
        import json
        try:
            story = json.loads('"' + m.group(1) + '"').strip()
            if story:
                return story
        except ValueError:
            pass
    return None


@dataclass
class Attempt:
    n: int
    passed: bool
    score: int
    unsupported: int
    derived: int
    qualitative: list
    feedback: list
    words: int
    latency_s: float | None = None
    model: str | None = None
    backend: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StoryResult:
    planet: dict
    record_source: str
    story: str
    origin: str  # "model" or "template"
    kid: bool
    verification: dict
    attempts: list
    fallback_reason: str | None
    highlights: list
    session_note: str | None
    llm: dict
    facts: list
    model: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["attempts"] = [a.to_dict() if isinstance(a, Attempt) else a for a in self.attempts]
        return d


class Harness:
    def __init__(self, skill, archive, llm, media, memory, settings):
        self.skill = skill
        self.archive = archive
        self.llm = llm
        self.media = media
        self.memory = memory
        self.settings = settings
        self.max_retries = settings.max_retries

    # ---- tools -------------------------------------------------------------------------------
    def fetch_planet(self, name: str) -> tuple[dict, str]:
        t = log.timer()
        with t:
            rec, source = self.archive.fetch_planet(name)
        log.info("tool_call", tool="fetch_planet", planet=rec.get("pl_name"), source=source, ms=t.elapsed_ms)
        return rec, source

    def search_planets(self, text: str) -> dict:
        """Map a request to a whitelisted intent, build the ADQL in code, run it."""
        text = (text or "").strip()
        if not text:
            raise ArchiveError("Type a request first, for example: the hottest planet discovered in 2020.", 400)
        if len(text) > 400:
            raise ArchiveError("That request is too long. Try one sentence.", 400)
        intent = query.parse_rules(text)
        parser = "rules"
        if intent.is_empty and getattr(self.llm, "name", "") in ("ollama", "gemini"):
            try:
                intent = query.parse_with_llm(text, self.llm)
                parser = "gemma"
            except (ValueError, LLMError) as e:
                log.warning("search_llm_rejected", error=str(e)[:200])
        if intent.is_empty:
            log.info("tool_call", tool="search_planets", parser=parser, empty=True)
            return {"understood": False, "parser": parser, "description": [], "notes": intent.notes,
                    "examples": EXAMPLES, "adql": None, "source": None, "results": [],
                    "message": "I couldn't map that to archive fields. Try one of these."}
        adql = query.build_adql(intent, self.skill.verifier.FIELDS)
        source, rows, warning = "bundled snapshot", None, None
        if not self.archive.offline:
            try:
                rows = self.archive.tap(adql)
                source = "NASA TAP (live)"
            except ArchiveError as e:
                warning = e.user_message + " Showing results from the bundled snapshot instead."
        if rows is None:
            rows = query.run_local(intent, self.archive.catalog)
        else:
            # TOP cannot be used here: this TAP service applies it before ORDER BY.
            rows = rows[: intent.limit]
        results = [self._card(r) for r in rows]
        log.info("tool_call", tool="search_planets", parser=parser, filters=len(intent.filters),
                 method=intent.method, sort=intent.sort_field, rows=len(results), source=source)
        return {
            "understood": True, "parser": parser, "description": intent.describe(), "notes": intent.notes,
            "examples": [], "adql": adql, "source": source, "warning": warning, "results": results,
            "message": None,
        }

    def verify_story(self, story: str, rec: dict, extra_names=()) -> dict:
        t = log.timer()
        with t:
            verdict = self.skill.verify(story, rec, extra_names=extra_names)
        log.info("tool_call", tool="verify_story", planet=rec.get("pl_name"), score=verdict["score"],
                 passed=verdict["passed"], unsupported=verdict["n_unsupported"], ms=t.elapsed_ms)
        return verdict

    def generate_image(self, name: str) -> dict:
        rec, source = self.fetch_planet(name)
        direction = art.art_direction(rec, self.skill.star_color(rec.get("st_teff")))
        try:
            result = self.media.image(rec, direction, art.mock_svg)
        except MediaError:
            raise
        log.info("tool_call", tool="generate_image", planet=rec["pl_name"], mode=result.mode, cached=result.cached)
        out = result.to_dict()
        out["planet"] = rec["pl_name"]
        out["record_source"] = source
        out["derivation"] = direction["derivation"]
        out["label"] = ("Mock render, drawn from the archive numbers" if result.mode == "mock"
                        else "Grok Imagine, from a prompt built out of the archive numbers")
        return out

    def narrate(self, text: str, voice: str | None = None) -> dict:
        text = (text or "").strip()
        if not text:
            raise MediaError("There is no story to narrate yet.", 400)
        if len(text) > 5000:
            raise MediaError("That story is too long to narrate.", 400)
        result = self.media.narrate(text, voice=voice)
        log.info("tool_call", tool="narrate", mode=result.mode, cached=result.cached, voice=result.voice)
        out = result.to_dict()
        out["label"] = ("Placeholder chime. Mock mode is on, so no xAI voice was called." if result.mode == "mock"
                        else f"Narrated by the xAI voice {result.voice}.")
        return out

    def narrate_told(self, session_id: str | None, name: str, voice: str | None = None) -> dict:
        rec, _ = self.fetch_planet(name)
        item = next((i for i in self.memory.told(session_id) if i["pl_name"] == rec["pl_name"]), None)
        if not item or not item.get("story"):
            raise MediaError("Tell this planet's story before asking for narration.", 400)
        return self.narrate(item["story"], voice=voice)

    # ---- the loop -----------------------------------------------------------------------------
    def tell(self, name: str, session_id: str | None = None, kid: bool = False,
             model: str | None = None, seed: int | None = None) -> StoryResult:
        rec, source = self.fetch_planet(name)
        model = self._resolve_model(model)
        note, extra_names = self.memory.llm_context(session_id, rec)
        system = self.skill.system_prompt(kid=kid)
        attempts: list[Attempt] = []
        story: str | None = None
        verdict: dict | None = None
        fallback: str | None = None
        llm_info: dict = {"backend": self.llm.name, "model": model, "label": getattr(self.llm, "label", self.llm.name)}

        feedback: list[str] | None = None
        for n in range(1, self.max_retries + 2):
            prompt = self.skill.user_prompt(rec, session_context=note, feedback=feedback, previous_story=story)
            try:
                resp = self.llm.generate(
                    system, prompt, schema=STORY_SCHEMA, temperature=0.4, seed=seed, model=model,
                    context={"purpose": "story", "planet": rec["pl_name"], "attempt": n, "kid": kid})
            except NoRecording as e:
                fallback = e.user_message
                log.info("attempt_skipped", planet=rec["pl_name"], attempt=n, reason="no_recording")
                break
            except LLMError as e:
                attempts.append(Attempt(n=n, passed=False, score=0, unsupported=0, derived=0, qualitative=[],
                                        feedback=[], words=0, error=e.user_message, model=model, backend=self.llm.name))
                log.warning("attempt_error", planet=rec["pl_name"], attempt=n, error=e.user_message)
                fallback = e.user_message
                if e.retryable and n <= self.max_retries:
                    feedback = [e.user_message]
                    continue
                break
            text = extract_story(resp.text)
            llm_info.update({"latency_s": resp.latency_s, "tokens": resp.tokens, "tok_per_s": resp.tok_per_s,
                             "recorded": bool(resp.meta.get("recorded"))})
            if text is None:
                feedback = ["Your reply was not valid JSON with a story string. Return only the JSON object."]
                attempts.append(Attempt(n=n, passed=False, score=0, unsupported=0, derived=0, qualitative=[],
                                        feedback=feedback, words=0, latency_s=resp.latency_s, model=resp.model,
                                        backend=resp.backend, error="Could not read a story from the model reply."))
                log.info("attempt", planet=rec["pl_name"], attempt=n, parse=False, ms=int(resp.latency_s * 1000))
                continue
            verdict = self.verify_story(text, rec, extra_names=extra_names)
            story = text
            feedback = verdict["feedback"]
            attempts.append(Attempt(
                n=n, passed=verdict["passed"], score=verdict["score"], unsupported=verdict["n_unsupported"],
                derived=verdict["n_derived"], qualitative=verdict["qualitative_flags"], feedback=feedback,
                words=len(text.split()), latency_s=resp.latency_s, model=resp.model, backend=resp.backend))
            log.info("attempt", planet=rec["pl_name"], attempt=n, score=verdict["score"], passed=verdict["passed"],
                     unsupported=verdict["n_unsupported"], words=len(text.split()), backend=resp.backend)
            if verdict["passed"]:
                break

        origin = "model"
        if not (verdict and verdict.get("passed")):
            story = self.skill.template_story(rec, kid=kid)
            verdict = self.verify_story(story, rec, extra_names=extra_names)
            origin = "template"
            if not fallback:
                fallback = ("Gemma did not produce a fully grounded story within the retry limit, "
                            "so this one was assembled directly from the archive record.")
            log.info("template_fallback", planet=rec["pl_name"], attempts=len(attempts), template_passed=verdict["passed"])

        if llm_info.get("recorded"):
            llm_info["note"] = ("Replayed a real Gemma story recorded on the team's machine. "
                                "In this mode the feedback is computed and shown, but a recording cannot change.")
        self.memory.remember(session_id, rec, {"story": story, "score": verdict["score"], "kid": kid, "origin": origin})
        highlights = self.memory.highlights(session_id, rec)
        log.info("story_done", planet=rec["pl_name"], origin=origin, score=verdict["score"], attempts=len(attempts), kid=kid)
        return StoryResult(
            planet=rec, record_source=source, story=story, origin=origin, kid=kid, verification=verdict,
            attempts=attempts, fallback_reason=fallback if origin == "template" else None, highlights=highlights,
            session_note=note, llm=llm_info, facts=self.fact_rows(rec), model=model)

    # ---- helpers ------------------------------------------------------------------------------
    def fact_rows(self, rec: dict) -> list[dict]:
        rows = []
        for f, (label, unit) in self.skill.verifier.FIELD_INFO.items():
            rows.append({"field": f, "label": label, "unit": unit, "value": rec.get(f)})
        rows.append({"field": "star_color", "label": "Star color (from its temperature)", "unit": "",
                     "value": self.skill.star_color(rec.get("st_teff")), "derived": True})
        return rows

    def compare(self, name: str) -> dict:
        rec, source = self.fetch_planet(name)
        out = compare.compare_with_earth(rec)
        out["record_source"] = source
        return out

    def planet_of_the_day(self) -> dict:
        rec = None
        # Offline mode can only replay planets that were actually recorded.
        if getattr(self.llm, "name", "") == "offline" and hasattr(self.llm, "planets"):
            pool = [self.archive.by_name[n] for n in self.llm.planets() if n in self.archive.by_name]
            if pool:
                import datetime as _dt
                import hashlib as _hashlib
                day = _dt.date.today().isoformat()
                rec = pool[int(_hashlib.sha256(("offline:" + day).encode()).hexdigest(), 16) % len(pool)]
        if rec is None:
            rec = self.archive.planet_of_the_day()
        log.info("tool_call", tool="planet_of_the_day", planet=rec["pl_name"])
        return self._card(rec)

    def _resolve_model(self, model: str | None) -> str:
        model = (model or self.llm.model).strip()
        known = set(self.llm.models()) | {self.llm.model}
        if model not in known:
            raise ArchiveError(f"Unknown model '{model}'. Available: {', '.join(sorted(known))}.", 400)
        return model

    @staticmethod
    def _card(r: dict) -> dict:
        keep = ("pl_name", "hostname", "disc_year", "discoverymethod", "sy_dist", "pl_rade", "pl_eqt",
                "pl_orbper", "st_teff", "st_spectype")
        return {k: r.get(k) for k in keep}
