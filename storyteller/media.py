"""xAI Grok Imagine (images) and xAI Voice (TTS) clients, with an on-disk cache and a mock mode.

The API key is only ever read from settings (XAI_API_KEY in the server environment). It is never
returned to the browser, never written to the cache, and redacted from logs and error messages.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .logs import get_logger, redact, register_secret

log = get_logger("media")
MOCK_VOICES = [{"voice_id": "eve", "name": "Eve (mock)"}]


class MediaError(Exception):
    def __init__(self, message: str, status: int = 502, retryable: bool = False):
        super().__init__(message)
        self.user_message = redact(message)
        self.status = status
        self.retryable = retryable


@dataclass
class MediaResult:
    url: str
    mode: str  # "xai" | "mock"
    cached: bool
    kind: str  # "image" | "audio"
    content_type: str
    prompt: str | None = None
    voice: str | None = None
    ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _friendly(status: int, body: str, what: str) -> MediaError:
    if status in (401, 403):
        return MediaError(f"xAI rejected the API key for {what} (HTTP {status}). Check XAI_API_KEY in .env.", 502)
    if status == 429:
        return MediaError(f"xAI rate limit reached for {what}. Wait a moment and try again.", 429, retryable=True)
    if status == 400:
        return MediaError(f"xAI could not process the {what} request: {body[:160]}", 502)
    if status >= 500:
        return MediaError(f"xAI {what} service error (HTTP {status}). Try again shortly.", 502, retryable=True)
    return MediaError(f"Unexpected xAI response for {what} (HTTP {status}).", 502)


class XAIClient:
    def __init__(self, api_key: str, base_url: str = "https://api.x.ai/v1", timeout: float = 90.0,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise MediaError("XAI_API_KEY is not set.", 503)
        register_secret(api_key)
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0), transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "exoplanet-storyteller/1.0"})
        # image URLs are on another host: download without the API key
        self._dl = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=transport, follow_redirects=True)

    def _post(self, path: str, payload: dict, what: str, retries: int = 1) -> httpx.Response:
        for attempt in range(retries + 1):
            try:
                r = self._http.post(f"{self.base_url}{path}", json=payload)
            except httpx.TimeoutException as e:
                if attempt < retries:
                    continue
                raise MediaError(f"xAI {what} timed out. Try again.", 504, retryable=True) from e
            except httpx.HTTPError as e:
                raise MediaError(f"Could not reach xAI for {what}: {type(e).__name__}.", 502, retryable=True) from e
            if r.status_code < 400:
                return r
            err = _friendly(r.status_code, r.text, what)
            if err.retryable and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise err
        raise MediaError(f"xAI {what} failed.", 502)

    def generate_image(self, prompt: str, model: str = "grok-imagine-image") -> tuple[bytes, str]:
        r = self._post("/images/generations", {"model": model, "prompt": prompt, "n": 1}, "image generation")
        try:
            item = r.json()["data"][0]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise MediaError("xAI image response had no image in it.", 502) from e
        if item.get("b64_json"):
            import base64
            return base64.b64decode(item["b64_json"]), "image/jpeg"
        url = item.get("url")
        if not url:
            raise MediaError("xAI image response had no image URL.", 502)
        try:
            img = self._dl.get(url)
            img.raise_for_status()
        except httpx.HTTPError as e:
            raise MediaError("Generated image could not be downloaded from xAI (URLs are temporary).", 502, True) from e
        return img.content, img.headers.get("content-type", "image/jpeg").split(";")[0]

    def tts(self, text: str, voice_id: str = "eve", language: str = "en") -> bytes:
        r = self._post("/tts", {"text": text, "language": language, "voice_id": voice_id}, "narration")
        ctype = r.headers.get("content-type", "")
        if "json" in ctype or len(r.content) < 64:
            raise MediaError("xAI narration response did not contain audio.", 502)
        return r.content

    def voices(self) -> list[dict]:
        try:
            r = self._http.get(f"{self.base_url}/tts/voices", timeout=15.0)
        except httpx.HTTPError as e:
            raise MediaError("Could not list xAI voices.", 502) from e
        if r.status_code >= 400:
            raise _friendly(r.status_code, r.text, "voice list")
        data = r.json()
        items = data.get("voices", data.get("data", data)) if isinstance(data, dict) else data
        out = []
        for v in items if isinstance(items, list) else []:
            if isinstance(v, str):
                out.append({"voice_id": v, "name": v})
            elif isinstance(v, dict):
                vid = v.get("voice_id") or v.get("id") or v.get("name")
                if vid:
                    out.append({"voice_id": vid, "name": v.get("name") or vid})
        return out


def mock_wav(seconds: float = 1.6, rate: int = 22050) -> bytes:
    """A soft two-note chime: a clearly-not-a-voice placeholder for mock narration."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        n = int(seconds * rate)
        for i in range(n):
            t = i / rate
            f = 660 if t < seconds / 2 else 880
            env = math.exp(-3.2 * (t % (seconds / 2)))
            frames += struct.pack("<h", int(9000 * env * math.sin(2 * math.pi * f * t)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


def narration_script(story: str) -> str:
    """Story text with xAI inline [pause] tags between paragraphs."""
    paras = [p.strip() for p in story.split("\n") if p.strip()]
    return " [pause] ".join(paras)[:4000]


class MediaService:
    def __init__(self, settings, client: XAIClient | None = None):
        self.settings = settings
        self.mock = settings.mock_media
        self.dir = Path(settings.cache_dir) / "media"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._client = client
        self._voices: list[dict] | None = None

    @property
    def client(self) -> XAIClient:
        if self._client is None:
            self._client = XAIClient(self.settings.xai_api_key, self.settings.xai_base_url, self.settings.xai_timeout)
        return self._client

    def _key(self, *parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]

    def _find(self, stem: str) -> Path | None:
        for p in self.dir.glob(stem + ".*"):
            if p.suffix != ".json":
                return p
        return None

    def _save(self, stem: str, data: bytes, ctype: str, meta: dict) -> Path:
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/svg+xml": ".svg",
               "audio/mpeg": ".mp3", "audio/wav": ".wav"}.get(ctype, ".bin")
        path = self.dir / f"{stem}{ext}"
        path.write_bytes(data)
        (self.dir / f"{stem}.json").write_text(json.dumps(meta))
        return path

    def image(self, rec: dict, direction: dict, svg_factory) -> MediaResult:
        mode = "mock" if self.mock else "xai"
        stem = f"img-{mode}-" + self._key(mode, self.settings.xai_image_model, direction["prompt"])
        t0 = time.perf_counter()
        hit = self._find(stem)
        if hit:
            return MediaResult(f"/media/{hit.name}", mode, True, "image", _ctype(hit), direction["prompt"])
        if self.mock:
            data, ctype = svg_factory(rec, direction).encode(), "image/svg+xml"
        else:
            data, ctype = self.client.generate_image(direction["prompt"], self.settings.xai_image_model)
        path = self._save(stem, data, ctype, {"planet": rec["pl_name"], "prompt": direction["prompt"], "mode": mode})
        ms = round((time.perf_counter() - t0) * 1000)
        log.info("image_generated", planet=rec["pl_name"], mode=mode, bytes=len(data), ms=ms)
        return MediaResult(f"/media/{path.name}", mode, False, "image", ctype, direction["prompt"], ms=ms)

    def narrate(self, text: str, voice: str | None = None) -> MediaResult:
        voice = voice or self.settings.xai_voice_id
        mode = "mock" if self.mock else "xai"
        script = narration_script(text)
        stem = f"tts-{mode}-" + self._key(mode, voice, script)
        hit = self._find(stem)
        if hit:
            return MediaResult(f"/media/{hit.name}", mode, True, "audio", _ctype(hit), voice=voice)
        t0 = time.perf_counter()
        if self.mock:
            data, ctype = mock_wav(), "audio/wav"
        else:
            data, ctype = self.client.tts(script, voice_id=voice), "audio/mpeg"
        path = self._save(stem, data, ctype, {"voice": voice, "chars": len(script), "mode": mode})
        ms = round((time.perf_counter() - t0) * 1000)
        log.info("narration_generated", mode=mode, voice=voice, chars=len(script), bytes=len(data), ms=ms)
        return MediaResult(f"/media/{path.name}", mode, False, "audio", ctype, voice=voice, ms=ms)

    def voices(self) -> list[dict]:
        if self.mock:
            return MOCK_VOICES
        if self._voices is None:
            try:
                self._voices = self.client.voices() or [{"voice_id": self.settings.xai_voice_id, "name": self.settings.xai_voice_id}]
            except MediaError as e:
                log.warning("voices_unavailable", error=e.user_message)
                return [{"voice_id": self.settings.xai_voice_id, "name": self.settings.xai_voice_id}]
        return self._voices


def _ctype(p: Path) -> str:
    return {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml",
            ".mp3": "audio/mpeg", ".wav": "audio/wav"}.get(p.suffix, "application/octet-stream")
