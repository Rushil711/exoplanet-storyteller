"""Runtime settings, read once from the environment (and `.env`, server-side only)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    llm_backend: str = "auto"  # auto | ollama | offline
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    ollama_fast_model: str = "gemma3:1b"
    ollama_timeout: float = 240.0
    max_retries: int = 2
    xai_api_key: str = field(default="", repr=False)
    xai_base_url: str = "https://api.x.ai/v1"
    xai_image_model: str = "grok-imagine-image"
    xai_voice_id: str = "eve"
    xai_timeout: float = 90.0
    mock_media: bool = True
    archive_offline: bool = False
    tap_url: str = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
    tap_timeout: float = 30.0
    cache_dir: Path = ROOT / ".cache"
    skill_dir: Path = ROOT / "skills" / "exoplanet-story-grounding"
    snapshot_path: Path = ROOT / "data" / "pscomppars_snapshot.json.gz"
    fixtures_path: Path = ROOT / "data" / "fixtures" / "gemma_stories.json"
    log_file: Path | None = ROOT / "logs" / "harness.jsonl"

    @property
    def media_mode(self) -> str:
        return "mock" if self.mock_media else "xai"

    @classmethod
    def from_env(cls, load_dotenv_file: bool = True) -> "Settings":
        if load_dotenv_file:
            load_dotenv(ROOT / ".env", override=False)
        key = os.getenv("XAI_API_KEY", "").strip()
        mock_raw = os.getenv("APP_MOCK_MEDIA", "auto").strip().lower()
        mock = (not key) if mock_raw in ("", "auto") else mock_raw in ("1", "true", "yes", "on")
        s = cls(
            llm_backend=os.getenv("LLM_BACKEND", "auto").strip().lower(),
            ollama_url=os.getenv("OLLAMA_URL", cls.ollama_url).rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", cls.ollama_model),
            ollama_fast_model=os.getenv("OLLAMA_FAST_MODEL", cls.ollama_fast_model),
            ollama_timeout=float(os.getenv("OLLAMA_TIMEOUT", cls.ollama_timeout)),
            max_retries=int(os.getenv("STORY_MAX_RETRIES", cls.max_retries)),
            xai_api_key=key,
            xai_base_url=os.getenv("XAI_BASE_URL", cls.xai_base_url).rstrip("/"),
            xai_image_model=os.getenv("XAI_IMAGE_MODEL", cls.xai_image_model),
            xai_voice_id=os.getenv("XAI_VOICE_ID", cls.xai_voice_id),
            xai_timeout=float(os.getenv("XAI_TIMEOUT", cls.xai_timeout)),
            mock_media=mock,
            archive_offline=_bool("ARCHIVE_OFFLINE"),
            tap_url=os.getenv("TAP_URL", cls.tap_url),
            tap_timeout=float(os.getenv("TAP_TIMEOUT", cls.tap_timeout)),
        )
        if os.getenv("CACHE_DIR"):
            s.cache_dir = Path(os.environ["CACHE_DIR"])
        if os.getenv("LOG_FILE") is not None:
            s.log_file = Path(os.environ["LOG_FILE"]) if os.environ["LOG_FILE"] else None
        return s
