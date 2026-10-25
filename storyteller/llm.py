"""LLM backends behind one small interface.

  OllamaBackend        real local Gemma via Ollama's /api/chat (structured JSON output)
  OfflineReplayBackend clearly-labelled demo backend that replays real Gemma stories recorded by
                       the team (data/fixtures/gemma_stories.json); it never invents text.
Tests use their own scripted fake backend (tests/fakes.py).
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .logs import get_logger

log = get_logger("llm")


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.user_message = message
        self.retryable = retryable


class NoRecording(LLMError):
    """The offline backend has no recorded story for this request."""


@dataclass
class LLMResponse:
    text: str
    model: str
    backend: str
    latency_s: float
    tokens: int | None = None
    tok_per_s: float | None = None
    meta: dict = field(default_factory=dict)


class LLMBackend(ABC):
    name: str = "base"
    label: str = ""
    model: str = ""

    @abstractmethod
    def generate(self, system: str, prompt: str, schema: dict | None = None, temperature: float = 0.7,
                 seed: int | None = None, context: dict | None = None, model: str | None = None) -> LLMResponse:
        ...

    def health(self) -> dict:
        return {"ok": True, "detail": ""}

    def models(self) -> list[str]:
        return [self.model]


class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(self, url: str, model: str, fast_model: str | None = None, timeout: float = 240.0,
                 transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self.model = model
        self.fast_model = fast_model
        self.label = f"Ollama - {model}"
        self._http = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), transport=transport)

    def health(self) -> dict:
        try:
            tags = self._http.get(f"{self.url}/api/tags", timeout=3.0).json()
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "detail": f"Ollama is not reachable at {self.url}. Start it with `ollama serve`."}
        names = {m.get("name") for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            return {"ok": False, "detail": f"Model {self.model} is not pulled. Run `ollama pull {self.model}`.",
                    "installed": sorted(n for n in names if n)}
        return {"ok": True, "detail": f"{self.model} ready", "installed": sorted(n for n in names if n)}

    def models(self) -> list[str]:
        h = self.health()
        installed = h.get("installed") or []
        wanted = [m for m in (self.model, self.fast_model) if m]
        return [m for m in wanted if m in installed] or wanted

    def generate(self, system, prompt, schema=None, temperature=0.7, seed=None, context=None, model=None):
        model = model or self.model
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": 1024, "num_ctx": 4096},
            "keep_alive": "30m",
        }
        if seed is not None:
            body["options"]["seed"] = seed
        if schema:
            body["format"] = schema
        t0 = time.perf_counter()
        try:
            r = self._http.post(f"{self.url}/api/chat", json=body)
        except httpx.TimeoutException as e:
            raise LLMError(f"Gemma ({model}) timed out. Try the faster {self.fast_model or 'model'}.", retryable=True) from e
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach Ollama at {self.url}. Is `ollama serve` running?") from e
        if r.status_code == 404:
            raise LLMError(f"Ollama does not have {model}. Run `ollama pull {model}`.")
        if r.status_code >= 400:
            raise LLMError(f"Ollama returned HTTP {r.status_code}: {r.text[:200]}", retryable=r.status_code >= 500)
        data = r.json()
        wall = time.perf_counter() - t0
        tokens = data.get("eval_count")
        dur = data.get("eval_duration")
        return LLMResponse(
            text=(data.get("message") or {}).get("content", ""),
            model=model, backend=self.name, latency_s=round(wall, 2), tokens=tokens,
            tok_per_s=round(tokens / (dur / 1e9), 1) if tokens and dur else None,
            meta={"prompt_tokens": data.get("prompt_eval_count"), "done_reason": data.get("done_reason")},
        )


class OfflineReplayBackend(LLMBackend):
    """Replays recorded real Gemma output (strict JSON prompt) for the planets in the fixtures.

    Attempt k replays recorded seed k for that planet and model, so the verifier loop runs on
    genuine model output. Retry feedback cannot influence a recording; the UI labels this.
    """
    name = "offline"

    MODEL_MAP = {"gemma3:4b": "gemma3-4b-local", "gemma3:1b": "gemma3-1b-local"}

    def __init__(self, fixtures_path: Path, model: str = "gemma3:4b", variant: str = "B_strict_json"):
        self.model = model
        self.variant = variant
        self.label = f"Offline replay - recorded {model} stories"
        rows = json.loads(Path(fixtures_path).read_text()) if Path(fixtures_path).exists() else []
        self.rows = rows
        self._index: dict[tuple[str, str, str, bool], list[dict]] = {}
        for r in rows:
            key = (r["model"], r["variant"], r["planet"], bool(r.get("kid")))
            self._index.setdefault(key, []).append(r)
        for v in self._index.values():
            v.sort(key=lambda r: r.get("seed", 0))

    def planets(self, model: str | None = None) -> list[str]:
        m = self.MODEL_MAP.get(model or self.model, model or self.model)
        return sorted({k[2] for k in self._index if k[0] == m and k[1] == self.variant})

    def models(self) -> list[str]:
        return list(self.MODEL_MAP)

    def recording(self, planet: str, model: str | None = None, variant: str | None = None,
                  kid: bool = False, attempt: int = 1) -> dict | None:
        m = self.MODEL_MAP.get(model or self.model, model or self.model)
        rows = self._index.get((m, variant or self.variant, planet, kid), [])
        return rows[attempt - 1] if 0 < attempt <= len(rows) else None

    def generate(self, system, prompt, schema=None, temperature=0.7, seed=None, context=None, model=None):
        ctx = context or {}
        if ctx.get("purpose") == "search":
            raise NoRecording("Offline replay cannot interpret free text; using the rule parser.")
        planet, attempt, kid = ctx.get("planet"), int(ctx.get("attempt", 1)), bool(ctx.get("kid"))
        rec = self.recording(planet, model=model, kid=kid, attempt=attempt)
        if rec is None:
            what = "kid-mode " if kid else ""
            raise NoRecording(f"No recorded {what}Gemma story #{attempt} for {planet} in offline mode.")
        text = rec.get("raw") or json.dumps({"story": rec["story"], "numbers_used": []})
        return LLMResponse(text=text, model=model or self.model, backend=self.name,
                           latency_s=round(rec.get("wall_s") or 0, 2), tokens=rec.get("eval_count"),
                           tok_per_s=round(rec["tok_s"], 1) if rec.get("tok_s") else None,
                           meta={"replayed_seed": rec.get("seed"), "recorded": True})


def make_backend(settings) -> LLMBackend:
    choice = settings.llm_backend
    if choice in ("ollama", "auto"):
        ob = OllamaBackend(settings.ollama_url, settings.ollama_model, settings.ollama_fast_model, settings.ollama_timeout)
        if choice == "ollama":
            return ob
        h = ob.health()
        if h["ok"]:
            log.info("llm_backend_selected", backend="ollama", model=settings.ollama_model)
            return ob
        log.warning("llm_backend_fallback", reason=h["detail"], backend="offline")
    return OfflineReplayBackend(settings.fixtures_path, model=settings.ollama_model)
