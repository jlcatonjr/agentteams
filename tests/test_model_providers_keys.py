"""Key custody (P06): env-only source, redacted repr, no pickling, scrubbed errors, CI refusal."""

from __future__ import annotations

import copy
import json
import pickle
import re
import traceback
import urllib.error
from email.message import Message
from io import BytesIO

import pytest

from agentteams import model_providers as mp
from tests._model_provider_fakes import Recorder, block_network, provider, user

KEY = "sk-synthetic-NOT-A-REAL-KEY-0123456789"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def test_missing_key_names_the_variable_not_a_value() -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        mp.get_provider("anthropic", environ={})
    assert err.value.kind == "config" and "ANTHROPIC_API_KEY" in err.value.message


def test_gemini_key_precedence() -> None:
    p = mp.get_provider("gemini", environ={"GEMINI_API_KEY": "gem-key-1", "GOOGLE_API_KEY": "goo-key-2"})
    assert p._key == "goo-key-2"
    assert mp.get_provider("gemini", environ={"GEMINI_API_KEY": "gem-key-1"})._key == "gem-key-1"


def test_keyless_presets_need_no_env() -> None:
    assert mp.get_provider("ollama", environ={})._key == ""
    assert mp.get_provider("stub", environ={}).name == "stub"


def test_key_with_header_injection_is_refused_without_echo() -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        mp.get_provider("openai", environ={"OPENAI_API_KEY": "abc\r\nX-Evil: 1"})
    assert err.value.kind == "config" and "X-Evil" not in str(err.value)


def test_repr_pickle_copy_never_expose_the_key() -> None:
    p = mp.get_provider("anthropic", environ={"ANTHROPIC_API_KEY": KEY})
    assert KEY not in repr(p) and KEY not in str(p) and "redacted" in repr(p)
    assert not hasattr(p, "__dict__")
    for attempt in (lambda: pickle.dumps(p), lambda: copy.copy(p), lambda: copy.deepcopy(p)):
        with pytest.raises(TypeError):
            attempt()


def test_from_key() -> None:
    p = mp.Provider.from_key("openrouter", KEY)
    assert p.base_url == "https://openrouter.ai/api/v1" and p._key == KEY
    with pytest.raises(mp.ModelProviderError) as err:
        mp.Provider.from_key("openrouter", "")
    assert err.value.kind == "config"


def _http_error(url: str, status: int, body: bytes, headers: dict | None = None) -> urllib.error.HTTPError:
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(url, status, "err", msg, BytesIO(body))


def test_key_echoed_in_an_error_body_is_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.openai.com/v1/chat/completions"
    body = (b'{"error": {"type": "invalid_request_error", "message": "Incorrect API key provided: ' + KEY.encode()
            + b'; header Authorization: Bearer ' + KEY.encode() + b'"}}')
    Recorder(monkeypatch, _http_error(url, 401, body))
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("openai", KEY), "m", [user("x")], max_tokens=8, timeout=5)
    assert err.value.kind == "auth" and err.value.status == 401 and err.value.attempts == 1
    assert KEY not in str(err.value) and KEY[:20] not in repr(err.value.args)
    assert "<redacted>" in err.value.message


def test_scrub_header_echo_forms() -> None:
    for text in ("x-api-key: abcd1234efgh", "x-goog-api-key=abcd1234efgh", '"api_key": "abcd1234efgh"',
                 "Authorization: Bearer abcd1234efgh"):
        assert "abcd1234efgh" not in mp._scrub(text)


def test_ci_refuses_a_live_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("anthropic"), "m", [user("x")], max_tokens=8, timeout=5)
    assert err.value.kind == "config" and mp.CI_ALLOW_ENV in err.value.message and rec.requests == []
    assert mp.complete(mp.get_provider("stub"), "stub", [user("x")], max_tokens=8, timeout=5).stop == "end_turn"


def test_ci_override_allows(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, {"content": [], "stop_reason": "end_turn"})
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv(mp.CI_ALLOW_ENV, "1")
    mp.complete(provider("anthropic"), "m", [user("x")], max_tokens=8, timeout=5)
    assert len(rec.requests) == 1


def test_model_for_tier_has_no_default() -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        mp.model_for_tier("primary", {"primary": None}, environ={})
    assert err.value.kind == "config" and "no model configured for tier primary" in err.value.message
    assert mp.model_for_tier("cheap", {"cheap": "from-brief"}, environ={}) == "from-brief"
    assert mp.model_for_tier("cheap", {"cheap": "from-brief"},
                             environ={"AGENTTEAMS_WASM_MODEL_CHEAP": "from-env"}) == "from-env"
    with pytest.raises(mp.ModelProviderError):
        mp.model_for_tier("premium", {}, environ={})


def test_no_default_model_ids_in_the_module() -> None:
    # URLs (documentation citations such as ai.google.dev/gemini-api/...) are not model ids; drop them first.
    source = re.sub(r"\S*/\S*", " ", open(mp.__file__, encoding="utf-8").read())
    for pattern in ("claude-", "gpt-", "gemini-", "grok-", "llama3", "llama-", "sonnet", "opus", "haiku"):
        assert pattern not in source.lower(), pattern
    for preset in mp.PROVIDERS.values():
        assert not hasattr(preset, "model") and not hasattr(preset, "default_model")


# ---------------------------------------------------------------- @security review (2026-10-31) mitigations

def _assistant_with(block: dict) -> list[dict]:
    return [user("q"), {"role": "assistant", "content": [block]}, user("next")]


@pytest.mark.parametrize("sig", [
    json.dumps([{"type": "thinking", "thinking": "t", "signature": "s", "smuggled": "x" * 10}]),
    json.dumps([{"type": "text", "text": "not thinking"}]),
    json.dumps([]), "[not json", "x" * (mp.MAX_PROVIDER_SIG_CHARS + 1), "has spaces in it",
])
def test_forged_provider_sig_is_refused(monkeypatch: pytest.MonkeyPatch, sig: str) -> None:
    rec = Recorder(monkeypatch)
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("anthropic"), "m", _assistant_with({"type": "text", "text": "a", "_provider_sig": sig}),
                    max_tokens=8, timeout=5)
    assert err.value.kind == "bad_request" and rec.requests == []


def test_oversize_provider_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch)
    block = {"type": "tool_use", "id": "t1", "name": "run_code", "input": {},
             "_provider_id": "x" * (mp.MAX_PROVIDER_ID_CHARS + 1)}
    msgs = [user("q"), {"role": "assistant", "content": [block]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r"}]}]
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("anthropic"), "m", msgs, max_tokens=8, timeout=5)
    assert err.value.kind == "bad_request"


def test_traceback_locals_do_not_show_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://api.anthropic.com/v1/messages"
    Recorder(monkeypatch, _http_error(url, 400, b'{"error": {"message": "bad"}}'))
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("anthropic", KEY), "m", [user("x")], max_tokens=8, timeout=5)
    seen = []
    exc: BaseException | None = err.value
    while exc is not None:
        seen.append("".join(traceback.TracebackException.from_exception(exc, capture_locals=True).format()))
        exc = exc.__cause__ or exc.__context__
    assert seen and all(KEY not in text for text in seen)


def test_str_of_a_rescrubbed_error_drops_the_key() -> None:
    err = mp.ModelProviderError("protocol", f"echo {KEY}")
    err.rescrub(KEY)
    assert KEY not in str(err) and KEY not in err.message


def test_ci_gate_covers_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch)
    monkeypatch.setenv("CI", "1")
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("ollama"), "m", [user("x")], max_tokens=8, timeout=5)
    assert err.value.kind == "config" and rec.requests == []


def test_direct_construction_is_checked() -> None:
    with pytest.raises(mp.ModelProviderError):
        mp.Provider(mp.PROVIDERS["openai"], "http://example.com/v1", "k-123456")
    with pytest.raises(mp.ModelProviderError):
        mp.Provider(mp.PROVIDERS["openai"], "https://api.openai.com/v1", "bad key")
    with pytest.raises(mp.ModelProviderError):
        mp.Provider(mp.PROVIDERS["openai"], "https://api.openai.com/%2e%2e/v1", "k-123456")
