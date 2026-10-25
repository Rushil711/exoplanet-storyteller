"""xAI image and voice clients, against a fake HTTP transport. No real key, no real network."""
import json

import httpx
import pytest

from storyteller.art import art_direction, mock_svg
from storyteller.logs import redact, register_secret
from storyteller.media import MediaError, MediaService, XAIClient, mock_wav


class _Router:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, request.url.host, request.url.path)
        action = self.routes.get(key)
        if action is None:
            return httpx.Response(404, json={"error": "no route", "key": list(key)})
        if isinstance(action, Exception):
            raise action
        return action(request) if callable(action) else action


def _client(routes, tmp_path, mock=False):
    from storyteller.config import Settings
    settings = Settings(mock_media=mock, xai_api_key="xai-testkey-should-not-leak", cache_dir=tmp_path,
                        log_file=None, xai_timeout=5)
    register_secret(settings.xai_api_key)
    router = _Router(routes)
    transport = httpx.MockTransport(router)
    xai = None if mock else XAIClient(settings.xai_api_key, settings.xai_base_url, settings.xai_timeout, transport)
    return MediaService(settings, client=xai), router, settings


def test_image_is_downloaded_without_the_api_key(tmp_path, planets):
    jpeg = b"\xff\xd8\xff\xd9fake-jpeg"

    def generate(_req):
        return httpx.Response(200, json={"data": [{"url": "https://imgen.x.ai/tmp/planet.jpeg"}]})

    def download(req):
        assert "authorization" not in {k.lower() for k in req.headers}
        return httpx.Response(200, content=jpeg, headers={"content-type": "image/jpeg"})

    media, router, _s = _client({
        ("POST", "api.x.ai", "/v1/images/generations"): generate,
        ("GET", "imgen.x.ai", "/tmp/planet.jpeg"): download,
    }, tmp_path)
    rec = planets["TRAPPIST-1 e"]
    direction = art_direction(rec, "deep red")
    result = media.image(rec, direction, mock_svg)
    assert result.mode == "xai" and result.cached is False
    assert result.url.endswith(".jpg")
    body = (tmp_path / "media" / result.url.split("/")[-1]).read_bytes()
    assert body == jpeg
    again = media.image(rec, direction, mock_svg)
    assert again.cached is True and again.url == result.url
    assert sum(1 for c in router.calls if c.url.host == "imgen.x.ai") == 1
    post = next(c for c in router.calls if c.method == "POST")
    assert post.headers["authorization"] == "Bearer xai-testkey-should-not-leak"
    saved = (tmp_path / "media" / (result.url.split("/")[-1].split(".")[0] + ".json")).read_text()
    assert "xai-testkey" not in saved


def test_tts_returns_mp3_bytes(tmp_path):
    mp3 = b"\xff\xfb" + b"\x00" * 80

    def synth(req):
        assert json.loads(req.content)["voice_id"] == "eve"
        return httpx.Response(200, content=mp3, headers={"content-type": "audio/mpeg"})

    media, _router, _s = _client({("POST", "api.x.ai", "/v1/tts"): synth}, tmp_path)
    result = media.narrate("A short story about a real planet.", voice="eve")
    assert result.url.endswith(".mp3")
    assert (tmp_path / "media" / result.url.split("/")[-1]).read_bytes() == mp3


def test_rejected_key_and_timeout_become_friendly_errors(tmp_path):
    media, _router, _s = _client({
        ("POST", "api.x.ai", "/v1/tts"): httpx.Response(401, json={"error": "bad key xai-testkey-should-not-leak"}),
    }, tmp_path)
    with pytest.raises(MediaError) as ei:
        media.narrate("hello")
    assert ei.value.status == 502
    assert "XAI_API_KEY" in ei.value.user_message
    assert "xai-testkey" not in ei.value.user_message

    media2, _r, _s = _client({
        ("POST", "api.x.ai", "/v1/tts"): httpx.TimeoutException("slow"),
    }, tmp_path)
    with pytest.raises(MediaError) as ei2:
        media2.narrate("hello")
    assert ei2.value.status == 504
    assert ei2.value.retryable


def test_mock_mode_never_calls_xai(tmp_path, planets):
    media, router, _s = _client({}, tmp_path, mock=True)
    rec = planets["Kepler-785 b"]
    direction = art_direction(rec, "orange")
    image = media.image(rec, direction, mock_svg)
    audio = media.narrate("Kepler-785 b is 13.02 Earth radii wide.")
    assert router.calls == []
    assert image.mode == "mock" and image.url.endswith(".svg")
    svg = (tmp_path / "media" / image.url.split("/")[-1]).read_text()
    assert svg.startswith("<svg") and "radialGradient" in svg
    assert "ocean" not in direction["prompt"].split("rather than")[0]
    wav = (tmp_path / "media" / audio.url.split("/")[-1]).read_bytes()
    assert wav[:4] == b"RIFF"
    assert mock_wav()[:4] == b"RIFF"
    assert media.voices()[0]["voice_id"] == "eve"


def test_art_prompt_is_derived_from_the_record(planets):
    cold = planets["TRAPPIST-1 e"]
    direction = art_direction(cold, "deep red")
    assert "deep red" in direction["prompt"]
    assert "TRAPPIST-1 e" in direction["prompt"]
    fields = {d["field"] for d in direction["derivation"]}
    assert {"st_teff", "pl_rade", "pl_eqt"} <= fields
    hot = dict(cold, pl_eqt=2000, st_teff=9000, pl_rade=12)
    assert "incandescent" in art_direction(hot, "blue-white")["mood"]


def test_secrets_are_redacted():
    register_secret("xai-supersecretvalue")
    text = redact("Authorization: Bearer xai-supersecretvalue and also xai-anotherkey99999999")
    assert "supersecret" not in text
    assert "anotherkey" not in text
    assert text.count("[REDACTED]") >= 2
