"""Golden request bodies per wire family (P06). Offline: openers are faked, nothing reaches a provider."""

from __future__ import annotations

import pytest

from agentteams import model_providers as mp
from tests._model_provider_fakes import TOOL, Recorder, block_network, provider, user

ANTHROPIC_OK = {"id": "msg_1", "model": "m-reported", "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 3, "output_tokens": 1}}
OPENAI_OK = {"id": "c1", "model": "m", "choices": [{"message": {"role": "assistant", "content": "hi"},
                                                   "finish_reason": "stop"}], "usage": {}}
GEMINI_OK = {"candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}, "finishReason": "STOP"}]}

# A tool round with a provider id that is NOT a safe neutral id, so the remap path is exercised.
ROUND = [user("go"),
         {"role": "assistant", "content": [
             {"type": "text", "text": "calling"},
             {"type": "tool_use", "id": "tu_1_1", "name": "run_code", "input": {"src": "1+1"},
              "_provider_id": "call:weird/id"}]},
         {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "tu_1_1", "content": "2", "is_error": False},
             {"type": "text", "text": "now explain"}]}]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def test_anthropic_text_with_system(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, ANTHROPIC_OK)
    mp.complete(provider("anthropic"), "m", [user("hello")], system="be brief", max_tokens=64, timeout=5)
    req = rec.requests[0]
    assert req["url"] == "https://api.anthropic.com/v1/messages"
    assert req["headers"]["x-api-key"] == "test-key-0123456789"
    assert req["headers"]["anthropic-version"] == "2023-06-01"
    assert req["body"] == {"model": "m", "max_tokens": 64, "system": "be brief",
                           "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]}


def test_anthropic_tools_and_round(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, ANTHROPIC_OK)
    mp.complete(provider("anthropic"), "m", ROUND, tools=[TOOL], max_tokens=64, timeout=5, temperature=0.5)
    body = rec.body
    assert body["tools"] == [{"name": "run_code", "description": TOOL.description,
                              "input_schema": dict(TOOL.input_schema)}]
    assert body["temperature"] == 0.5
    assert body["messages"][1]["content"][1] == {"type": "tool_use", "id": "call:weird/id", "name": "run_code",
                                                 "input": {"src": "1+1"}}
    assert body["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "call:weird/id",
                                                 "content": "2", "is_error": False}


def test_openai_preset_uses_max_completion_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, OPENAI_OK)
    mp.complete(provider("openai"), "m", [user("hello")], system="sys", max_tokens=32, timeout=5)
    req = rec.requests[0]
    assert req["url"] == "https://api.openai.com/v1/chat/completions"
    assert req["headers"]["authorization"] == "Bearer test-key-0123456789"
    assert req["body"] == {"model": "m", "max_completion_tokens": 32,
                           "messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]}


def test_openrouter_requests_usage_and_uses_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, OPENAI_OK)
    mp.complete(provider("openrouter"), "m", [user("x")], max_tokens=8, timeout=5)
    assert rec.requests[0]["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert rec.body["max_tokens"] == 8 and rec.body["usage"] == {"include": True}


def test_openai_tools_and_round(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, OPENAI_OK)
    mp.complete(provider("xai"), "m", ROUND, tools=[TOOL], max_tokens=8, timeout=5)
    body = rec.body
    assert body["tools"] == [{"type": "function", "function": {"name": "run_code", "description": TOOL.description,
                                                               "parameters": dict(TOOL.input_schema)}}]
    assert body["messages"] == [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "calling", "tool_calls": [
            {"id": "call:weird/id", "type": "function",
             "function": {"name": "run_code", "arguments": '{"src": "1+1"}'}}]},
        {"role": "tool", "tool_call_id": "call:weird/id", "content": "2"},
        {"role": "user", "content": "now explain"}]


def test_openai_error_result_is_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, OPENAI_OK)
    msgs = [dict(m) for m in ROUND]
    msgs[2] = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1_1", "content": "boom",
                                            "is_error": True}]}
    mp.complete(provider("openai"), "m", msgs, max_tokens=8, timeout=5)
    assert rec.body["messages"][-1] == {"role": "tool", "tool_call_id": "call:weird/id",
                                        "content": "[tool error] boom"}


def test_keyless_local_preset_sends_no_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, OPENAI_OK)
    mp.complete(provider("ollama"), "m", [user("x")], max_tokens=8, timeout=5)
    assert rec.requests[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in rec.requests[0]["headers"]


def test_gemini_text_system_and_header_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, GEMINI_OK)
    mp.complete(provider("gemini"), "models/g-1.0", [user("hello")], system="sys", max_tokens=16, timeout=5,
                temperature=0.0)
    req = rec.requests[0]
    assert req["url"] == "https://generativelanguage.googleapis.com/v1beta/models/g-1.0:generateContent"
    assert "key=" not in req["url"] and req["headers"]["x-goog-api-key"] == "test-key-0123456789"
    assert req["body"] == {"contents": [{"role": "user", "parts": [{"text": "hello"}]}],
                           "generationConfig": {"maxOutputTokens": 16, "temperature": 0.0},
                           "systemInstruction": {"parts": [{"text": "sys"}]}}


def test_gemini_schema_stripping_and_round(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, GEMINI_OK)
    msgs = [dict(m) for m in ROUND]
    msgs[1] = {"role": "assistant", "content": [
        {"type": "tool_use", "id": "tu_1_0", "name": "run_code", "input": {"src": "1"}, "_provider_id": None,
         "_provider_sig": "c2lnbmF0dXJl"}]}
    msgs[2] = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1_0", "content": "oops",
                                            "is_error": True}]}
    mp.complete(provider("gemini"), "g", msgs, tools=[TOOL], max_tokens=16, timeout=5)
    body = rec.body
    params = body["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]
    assert params == {"type": "object", "properties": {"src": {"type": "string", "description": "source"},
                                                       "lang": {"type": "string",
                                                                "enum": ["python", "javascript"]}},
                      "required": ["src"]}
    assert body["contents"][1] == {"role": "model", "parts": [
        {"functionCall": {"name": "run_code", "args": {"src": "1"}}, "thoughtSignature": "c2lnbmF0dXJl"}]}
    assert body["contents"][2] == {"role": "user", "parts": [
        {"functionResponse": {"name": "run_code", "response": {"error": "oops"}}}]}


def test_gemini_keeps_provider_call_id(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = Recorder(monkeypatch, GEMINI_OK)
    msgs = [user("go"), {"role": "assistant", "content": [
        {"type": "tool_use", "id": "abc", "name": "run_code", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "abc", "content": "ok"}]}]
    mp.complete(provider("gemini"), "g", msgs, max_tokens=16, timeout=5)
    assert rec.body["contents"][1]["parts"][0]["functionCall"]["id"] == "abc"
    assert rec.body["contents"][2]["parts"][0]["functionResponse"] == {
        "name": "run_code", "id": "abc", "response": {"output": "ok"}}


@pytest.mark.parametrize("name,reply", [("anthropic", ANTHROPIC_OK), ("openai", OPENAI_OK), ("gemini", GEMINI_OK)])
def test_body_keys_are_allowlisted(monkeypatch: pytest.MonkeyPatch, name: str, reply: dict) -> None:
    rec = Recorder(monkeypatch, reply)
    mp.complete(provider(name), "m", ROUND, system="s", tools=[TOOL], max_tokens=8, timeout=5, temperature=1)
    allowed = {"anthropic": {"model", "max_tokens", "system", "messages", "tools", "temperature"},
               "openai": {"model", "max_completion_tokens", "messages", "tools", "temperature"},
               "gemini": {"contents", "generationConfig", "systemInstruction", "tools"}}[name]
    assert set(rec.body) == allowed
    assert "stream" not in str(rec.body)


def test_id_round_trip_through_decode_then_encode(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call.with.dots", "type": "function", "function": {"name": "run_code", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}]}
    rec = Recorder(monkeypatch, reply, OPENAI_OK)
    p = provider("openai")
    first = mp.complete(p, "m", [user("go")], max_tokens=8, timeout=5)
    use = first.content[0]
    assert use["id"] == "tu_1_0" and use["_provider_id"] == "call.with.dots"
    history = [user("go"), {"role": "assistant", "content": list(first.content)},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use["id"], "content": "r"}]}]
    mp.complete(p, "m", history, max_tokens=8, timeout=5)
    assert rec.body["messages"][1]["tool_calls"][0]["id"] == "call.with.dots"
    assert rec.body["messages"][2] == {"role": "tool", "tool_call_id": "call.with.dots", "content": "r"}


@pytest.mark.parametrize("messages,why", [
    ([], "non-empty"),
    ([{"role": "assistant", "content": [{"type": "text", "text": "x"}]}], "alternate"),
    ([user("a"), user("b")], "alternate"),
    ([{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "r"}]}], "answers no"),
    ([user("a\x00")], "NUL"),
    ([{"role": "user", "content": [{"type": "image", "source": {}}]}], "not allowed"),
    ([user("a"), {"role": "assistant", "content": [{"type": "tool_use", "id": "bad id", "name": "t", "input": {}}]}],
     "unique"),
])
def test_local_validation_before_io(monkeypatch: pytest.MonkeyPatch, messages: list, why: str) -> None:
    rec = Recorder(monkeypatch)
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("anthropic"), "m", messages, max_tokens=8, timeout=5)
    assert err.value.kind == "bad_request" and why in err.value.message
    assert rec.requests == []


def test_validation_of_tools_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch)
    p = provider("anthropic")
    for kwargs in ({"max_tokens": 0}, {"max_tokens": 1_000_001}, {"max_tokens": True},
                   {"max_tokens": 8, "tools": [TOOL, TOOL]},
                   {"max_tokens": 8, "tools": [mp.ToolSpec("bad-name", "", {"type": "object"})]},
                   {"max_tokens": 8, "system": "a\x00"}, {"max_tokens": 8, "temperature": float("nan")}):
        with pytest.raises(mp.ModelProviderError) as err:
            mp.complete(p, "m", [user("x")], timeout=5, **kwargs)
        assert err.value.kind == "bad_request"
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(p, "", [user("x")], max_tokens=8, timeout=5)
    assert err.value.kind == "config" and "no default model" in err.value.message
