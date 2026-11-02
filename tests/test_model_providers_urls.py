"""Base-URL egress policy (P06): HTTPS, loopback-only HTTP, no credentials/query/fragment, safe Gemini paths."""

from __future__ import annotations

import pytest

from agentteams import model_providers as mp
from tests._model_provider_fakes import Recorder, block_network, provider, user


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


@pytest.mark.parametrize("url", [
    "http://example.com/v1", "http://10.0.0.1:8080/v1", "ftp://example.com", "https://user:pw@example.com/v1",
    "https://example.com/v1?key=x", "https://example.com/v1#frag", "https://example.com/../v1", "https:///v1",
    "https://exa mple.com", "https://example.com:99999/v1", "file:///etc/passwd", "https://exämple.com",
])
def test_refused_base_urls(url: str) -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        mp.get_provider("openai-compatible", environ={"AGENTTEAMS_MODEL_API_KEY": "k-123456"}, base_url=url)
    assert err.value.kind == "config"


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/v1", "http://localhost:1234/v1", "http://[::1]:11434/v1",
                                 "https://llm.internal.example/v1/"])
def test_accepted_base_urls(url: str) -> None:
    p = mp.get_provider("openai-compatible", environ={"AGENTTEAMS_MODEL_API_KEY": "k-123456"}, base_url=url)
    assert p.base_url == url.rstrip("/")


def test_openai_compatible_requires_a_base_url() -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        mp.get_provider("openai-compatible", environ={"AGENTTEAMS_MODEL_API_KEY": "k-123456"})
    assert err.value.kind == "config" and "base_url" in err.value.message


def test_unknown_preset() -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        mp.get_provider("nope", environ={})
    assert err.value.kind == "config"


@pytest.mark.parametrize("model", ["../secrets", "a/../b", "a//b", "m?key=1", "m#x", "m:streamGenerateContent",
                                   "models/", "a b"])
def test_gemini_model_path_refused(monkeypatch: pytest.MonkeyPatch, model: str) -> None:
    rec = Recorder(monkeypatch)
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("gemini"), model, [user("x")], max_tokens=8, timeout=5)
    assert err.value.kind in ("config", "bad_request") and rec.requests == []


def test_requests_go_to_the_fixed_path_only(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, {"content": [], "stop_reason": "end_turn"})
    p = mp.Provider.from_key("anthropic", "k-123456", base_url="https://gw.example/v1/")
    mp.complete(p, "m", [user("x")], max_tokens=8, timeout=5)
    assert rec.requests[0]["url"] == "https://gw.example/v1/messages"
