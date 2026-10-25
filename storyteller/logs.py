"""Structured JSON-lines logging with secret redaction.

Every harness step logs one event: `log.info("tool_call", tool="fetch_planet", ms=812, ...)`
becomes `{"ts": ..., "level": "info", "event": "tool_call", "tool": "fetch_planet", "ms": 812}`.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

_SECRETS: set[str] = set()
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I)
_XAI_KEY = re.compile(r"xai-[A-Za-z0-9]{8,}")


def register_secret(value: str | None) -> None:
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for s in _SECRETS:
        text = text.replace(s, "[REDACTED]")
    text = _BEARER.sub(r"\1[REDACTED]", text)
    return _XAI_KEY.sub("[REDACTED]", text)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


class EventLogger:
    def __init__(self, logger: logging.Logger):
        self._log = logger

    def _emit(self, level: int, event: str, **fields) -> None:
        self._log.log(level, event, extra={"fields": fields})

    def info(self, event: str, **fields) -> None:
        self._emit(logging.INFO, event, **fields)

    def warning(self, event: str, **fields) -> None:
        self._emit(logging.WARNING, event, **fields)

    def error(self, event: str, **fields) -> None:
        self._emit(logging.ERROR, event, **fields)

    def timer(self) -> "_Timer":
        return _Timer()


class _Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = round((time.perf_counter() - self.t0) * 1000)

    @property
    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)


_configured = False


def setup_logging(log_file: Path | None = None, level: int = logging.INFO) -> None:
    global _configured
    root = logging.getLogger("storyteller")
    if _configured:
        return
    root.setLevel(level)
    root.propagate = False
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(_JsonFormatter())
    root.addHandler(h)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(_JsonFormatter())
        root.addHandler(fh)
    _configured = True


def get_logger(name: str) -> EventLogger:
    return EventLogger(logging.getLogger(f"storyteller.{name}"))
