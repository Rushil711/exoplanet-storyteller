"""Shared fixtures. Tests never call Ollama, xAI, or the live NASA TAP."""
from __future__ import annotations

import os

# Set before storyteller.app is imported, so the module-level app stays offline.
os.environ.setdefault("LLM_BACKEND", "offline")
os.environ.setdefault("APP_MOCK_MEDIA", "1")
os.environ.setdefault("ARCHIVE_OFFLINE", "1")
os.environ.setdefault("LOG_FILE", "")

import json
from pathlib import Path

import pytest

from storyteller.config import ROOT, Settings
from storyteller.llm import LLMResponse
from storyteller.skill import load_skill

SNAP = ROOT / "data" / "pscomppars_snapshot.json.gz"


@pytest.fixture(scope="session")
def skill():
    return load_skill(ROOT / "skills" / "exoplanet-story-grounding")


@pytest.fixture(scope="session")
def planets(skill):
    import gzip
    rows = json.load(gzip.open(SNAP, "rt", encoding="utf-8"))
    return {r["pl_name"]: r for r in rows}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        llm_backend="offline",
        mock_media=True,
        archive_offline=True,
        log_file=None,
        cache_dir=tmp_path / "cache",
        xai_api_key="",
    )


class FakeLLM:
    """Scripted backend. Each generate() consumes one reply, or raises it."""

    name = "fake"
    label = "Fake LLM"
    model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def models(self):
        return [self.model]

    def health(self):
        return {"ok": True, "detail": "fake"}

    def generate(self, system, prompt, schema=None, temperature=0.7, seed=None,
                 context=None, model=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema,
                           "context": context, "model": model})
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResponse(text=item, model=model or self.model, backend=self.name, latency_s=0.01)


def story_json(text: str) -> str:
    return json.dumps({"story": text, "numbers_used": []})
