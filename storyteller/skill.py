"""Loads the exoplanet-story-grounding Agent Skill and makes it the harness's source of truth.

The skill folder provides:
  SKILL.md                    -> system prompt (Role + Story rules + Output format), kid-mode rules,
                                 retry-feedback preamble, default retry count (metadata)
  scripts/verifier.py         -> verify(), feedback(), FIELD_INFO
  scripts/facts.py            -> FACTS block rendering, star color from st_teff
  scripts/template_story.py   -> safe fallback story
"""
from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import yaml

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REQUIRED_SECTIONS = ("Role", "Story rules", "Kid-friendly mode", "Output format", "Retry feedback")


class SkillError(ValueError):
    pass


def parse_skill_md(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        raise SkillError("SKILL.md must start with YAML frontmatter between --- lines")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise SkillError("frontmatter must be a mapping")
    return meta, m.group(2)


def validate_frontmatter(meta: dict, folder_name: str) -> list[str]:
    """Checks from the Agent Skills specification (agentskills.io/specification)."""
    errors = []
    name = meta.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not NAME_RE.match(name):
        errors.append("name must be 1-64 chars of lowercase letters, digits and single hyphens")
    elif name != folder_name:
        errors.append(f"name '{name}' must match the skill folder '{folder_name}'")
    desc = meta.get("description")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > 1024:
        errors.append("description must be a non-empty string of at most 1024 chars")
    comp = meta.get("compatibility")
    if comp is not None and (not isinstance(comp, str) or not (1 <= len(comp) <= 500)):
        errors.append("compatibility must be 1-500 chars")
    md = meta.get("metadata")
    if md is not None and (not isinstance(md, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in md.items())):
        errors.append("metadata must map strings to strings")
    allowed = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    extra = set(meta) - allowed
    if extra:
        errors.append(f"unknown frontmatter fields: {sorted(extra)}")
    return errors


def split_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current, buf = None, []
    for line in body.splitlines():
        h = re.match(r"^## (.+?)\s*$", line)
        if h:
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = h.group(1), []
        elif current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def _load_module(path: Path, alias: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise SkillError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Skill:
    path: Path
    meta: dict
    sections: dict[str, str]
    verifier: ModuleType
    facts: ModuleType
    template: ModuleType
    files: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.meta["name"]

    @property
    def version(self) -> str:
        return str((self.meta.get("metadata") or {}).get("version", "?"))

    @property
    def default_max_retries(self) -> int:
        return int((self.meta.get("metadata") or {}).get("default-max-retries", 2))

    def system_prompt(self, kid: bool = False) -> str:
        parts = [self.sections["Role"], "STORY RULES:\n" + self.sections["Story rules"]]
        if kid:
            parts.append("KID-FRIENDLY MODE:\n" + self.sections["Kid-friendly mode"])
        parts.append("OUTPUT FORMAT:\n" + self.sections["Output format"])
        return "\n\n".join(parts)

    def user_prompt(self, rec: dict, session_context: str | None = None,
                    feedback: list[str] | None = None, previous_story: str | None = None) -> str:
        out = ["FACTS:", self.facts.facts_block(rec)]
        if session_context:
            out += ["", "SESSION CONTEXT:", session_context]
        if feedback:
            out += ["", "RETRY FEEDBACK:", self.sections["Retry feedback"]]
            if previous_story:
                out += ["", "Previous story:", previous_story]
            out += ["", "Problems:"] + [f"- {f}" for f in feedback]
        out += ["", "Write the story now as JSON."]
        return "\n".join(out)

    def verify(self, story: str, rec: dict, extra_names=()) -> dict:
        res = self.verifier.verify(story, rec, extra_names=extra_names)
        res["feedback"] = self.verifier.feedback(res, rec)
        return res

    def template_story(self, rec: dict, kid: bool = False) -> str:
        return self.template.template_story(rec, kid=kid)

    def star_color(self, teff) -> str | None:
        return self.facts.star_color(teff)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.meta.get("description"),
            "path": str(self.path),
            "sections_used": list(REQUIRED_SECTIONS),
            "files": self.files,
        }


def load_skill(path: Path) -> Skill:
    path = Path(path)
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        raise SkillError(f"{skill_md} not found")
    meta, body = parse_skill_md(skill_md.read_text(encoding="utf-8"))
    errors = validate_frontmatter(meta, path.name)
    if errors:
        raise SkillError("; ".join(errors))
    sections = split_sections(body)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise SkillError(f"SKILL.md is missing sections: {missing}")
    scripts = path / "scripts"
    sys.path.insert(0, str(scripts))  # scripts import each other by plain module name
    try:
        verifier = _load_module(scripts / "verifier.py", "verifier")
        facts = _load_module(scripts / "facts.py", "facts")
        template = _load_module(scripts / "template_story.py", "template_story")
    finally:
        sys.path.remove(str(scripts))
    files = sorted(str(p.relative_to(path)) for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return Skill(path=path, meta=meta, sections=sections, verifier=verifier, facts=facts, template=template, files=files)
