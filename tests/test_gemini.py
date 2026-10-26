"""Gemini generateContent client. No real key and no real network."""
import json

import httpx
import pytest

from storyteller.config import Settings
from storyteller.llm import GeminiBackend, LLMError, OllamaBackend, make_backend
from storyteller.logs import redact


def _backend(handler, key="test-gemini-key-not-real", model="gemma-4-26b-a4b-it"):
    return GeminiBackend(key, model, transport=httpx.MockTransport(handler))


def test_generate_calls_generate_content_and_skips_thoughts():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [
                {"thought": True, "text": "hidden"},
                {"text": '{"story": "A checked world.", "numbers_used": []}'},
            ]}}],
            "usageMetadata": {"candidatesTokenCount": 12, "promptTokenCount": 40},
        })

    resp = _backend(handler).generate("Be exact.", "Tell it.", schema={"type": "object"}, temperature=0.4)
    assert "generativelanguage.googleapis.com" in seen["url"]
    assert "/v1beta/models/gemma-4-26b-a4b-it:generateContent" in seen["url"]
    assert seen["key"] == "test-gemini-key-not-real"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "Be exact."
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert "responseSchema" not in seen["body"]["generationConfig"]
    assert resp.backend == "gemini"
    assert resp.model == "gemma-4-26b-a4b-it"
    assert resp.tokens == 12
    assert "A checked world." in resp.text
    assert "test-gemini-key-not-real" not in resp.text


def test_thinking_config_rejection_is_retried_without_it():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append("thinkingConfig" in body["generationConfig"])
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"message": "thinkingConfig is not supported"}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": '{"story": "Second try."}'}]}}],
        })

    resp = _backend(handler).generate("sys", "prompt")
    assert calls == [True, False]
    assert "Second try." in resp.text


def test_error_text_does_not_keep_the_key():
    key = "test-gemini-key-not-real"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad request involving {key}")

    with pytest.raises(LLMError) as ei:
        _backend(handler, key=key).generate("sys", "prompt")
    assert key not in ei.value.user_message
    assert "[REDACTED]" in ei.value.user_message
    assert key not in redact(f"header {key}")


def test_missing_key_does_not_call_the_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("network was called")

    backend = _backend(handler, key="")
    assert backend.health()["ok"] is False
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        backend.generate("sys", "prompt")


def test_make_backend_defaults_to_gemini_and_keeps_ollama():
    assert Settings().llm_backend == "gemini"
    gemini = make_backend(Settings(llm_backend="gemini", gemini_api_key="k", gemma_model="gemma-4-26b-a4b-it"))
    assert isinstance(gemini, GeminiBackend)
    assert gemini.model == "gemma-4-26b-a4b-it"
    ollama = make_backend(Settings(llm_backend="ollama"))
    assert isinstance(ollama, OllamaBackend)
    auto = make_backend(Settings(llm_backend="auto", gemini_api_key="k", gemma_model="gemma-4-26b-a4b-it"))
    assert isinstance(auto, GeminiBackend)
