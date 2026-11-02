"""Reply fixtures -> normalised blocks / stop / usage per wire family (P06).

Fixture shapes follow the providers' docs as checked 2026-10-31 (URLs in ``agentteams/model_providers.py``'s
docstring); fields those docs did not settle are marked in the module with ``UNVERIFIED:``. Synthetic values only.
"""

from __future__ import annotations

import json

import pytest

from agentteams import model_providers as mp
from tests._model_provider_fakes import Recorder, block_network, provider, user


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    block_network(monkeypatch)


def _one(monkeypatch: pytest.MonkeyPatch, name: str, reply: dict, headers: dict | None = None) -> mp.ModelReply:
    Recorder(monkeypatch, reply, headers=headers)
    return mp.complete(provider(name), "requested-model", [user("q")], max_tokens=8, timeout=5)


# ---------------------------------------------------------------- Anthropic
# shape from platform.claude.com/docs/en/api/messages + /api/errors (request-id header), verified 2026-10-31

def test_anthropic_tool_use_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"id": "msg_01", "type": "message", "role": "assistant", "model": "reported-model",
             "content": [{"type": "thinking", "thinking": "...", "signature": "x"},
                         {"type": "text", "text": "Let me run it."},
                         {"type": "tool_use", "id": "toolu_01D7", "name": "run_code", "input": {"src": "1"}}],
             "stop_reason": "tool_use",
             "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2,
                       "cache_creation_input_tokens": 1}}
    r = _one(monkeypatch, "anthropic", reply, {"request-id": "req_011"})
    thinking = json.dumps([{"type": "thinking", "thinking": "...", "signature": "x"}])
    assert r.content == ({"type": "text", "text": "Let me run it.", "_provider_sig": thinking},
                         {"type": "tool_use", "id": "toolu_01D7", "name": "run_code", "input": {"src": "1"}})
    assert (r.stop, r.raw_stop, r.dropped_blocks) == ("tool_use", "tool_use", 0)
    assert r.usage == mp.Usage(None, 10, 5, 2, 1)
    assert (r.model, r.request_id, r.provider) == ("reported-model", "req_011", "anthropic")


@pytest.mark.parametrize("raw,stop", [("end_turn", "end_turn"), ("stop_sequence", "end_turn"),
                                      ("max_tokens", "max_tokens"), ("model_context_window_exceeded", "max_tokens"),
                                      ("refusal", "refusal"), ("pause_turn", "other"), ("new_value", "other")])
def test_anthropic_stop_map(monkeypatch: pytest.MonkeyPatch, raw: str, stop: str) -> None:
    r = _one(monkeypatch, "anthropic", {"content": [], "stop_reason": raw})
    assert (r.stop, r.raw_stop) == (stop, raw)
    assert r.model == "requested-model"


# ---------------------------------------------------------------- OpenAI-compatible
# shape from developers.openai.com/api/reference/.../chat/.../completions/methods/create, verified 2026-10-31

def test_openai_tool_calls_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"id": "chatcmpl-1", "model": "reported", "choices": [{"index": 0, "finish_reason": "tool_calls",
             "message": {"role": "assistant", "content": None, "refusal": None, "tool_calls": [
                 {"id": "call_abc", "type": "function", "function": {"name": "run_code", "arguments": '{"src":"x"}'}},
                 {"id": "call_c", "type": "custom", "custom": {"name": "z", "input": ""}}]}}],
             "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16,
                       "prompt_tokens_details": {"cached_tokens": 5}}}
    r = _one(monkeypatch, "openai", reply)
    assert r.content == ({"type": "tool_use", "id": "call_abc", "name": "run_code", "input": {"src": "x"}},)
    assert (r.stop, r.dropped_blocks, r.request_id, r.model) == ("tool_use", 1, "chatcmpl-1", "reported")
    assert r.usage == mp.Usage(None, 7, 4, 5, 0)


def test_openrouter_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    # shape from openrouter.ai/docs/api-reference/chat-completion (ChatUsage.cost), verified 2026-10-31
    reply = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4, "cost": 0.0012}}
    r = _one(monkeypatch, "openrouter", reply)
    assert r.usage.cost == 0.0012 and r.content == ({"type": "text", "text": "ok"},)


@pytest.mark.parametrize("args", ['{"a": 1, "a": 2}', "{not json", '{"x": NaN}', "[1, 2]"])
def test_openai_bad_arguments_are_protocol_errors(monkeypatch: pytest.MonkeyPatch, args: str) -> None:
    reply = {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "run_code", "arguments": args}}]}}]}
    with pytest.raises(mp.ModelProviderError) as err:
        _one(monkeypatch, "openai", reply)
    assert err.value.kind == "protocol" and err.value.attempts == 1


@pytest.mark.parametrize("raw,stop", [("stop", "end_turn"), ("length", "max_tokens"), ("content_filter", "refusal"),
                                      ("tool_calls", "tool_use"), ("weird", "other")])
def test_openai_stop_map(monkeypatch: pytest.MonkeyPatch, raw: str, stop: str) -> None:
    r = _one(monkeypatch, "openai", {"choices": [{"message": {"content": "x"}, "finish_reason": raw}]})
    assert r.stop == stop


def test_openai_refusal_field(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _one(monkeypatch, "openai", {"choices": [{"message": {"content": None, "refusal": "I can't help."},
                                                  "finish_reason": "stop"}]})
    assert r.stop == "refusal" and r.content == ({"type": "text", "text": "I can't help."},)


def test_openai_missing_choices_is_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        _one(monkeypatch, "openai", {"choices": []})
    assert err.value.kind == "protocol"


# ---------------------------------------------------------------- Gemini
# shape from ai.google.dev/api/generate-content + v1beta discovery doc (FunctionCall.id optional,
# Part.thoughtSignature, GenerateContentResponse.responseId/modelVersion), verified 2026-10-31

def test_gemini_function_call_without_id(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"candidates": [{"content": {"role": "model", "parts": [
        {"text": "thinking...", "thought": True},
        {"text": "Running."},
        {"functionCall": {"name": "run_code", "args": {"src": "1"}}, "thoughtSignature": "c2ln"}]},
        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 20, "cachedContentTokenCount": 4, "candidatesTokenCount": 6,
                          "thoughtsTokenCount": 3, "totalTokenCount": 29},
        "modelVersion": "g-reported", "responseId": "resp-1"}
    r = _one(monkeypatch, "gemini", reply)
    assert r.content == ({"type": "text", "text": "Running."},
                         {"type": "tool_use", "id": "tu_1_1", "name": "run_code", "input": {"src": "1"},
                          "_provider_id": None, "_provider_sig": "c2ln"})
    assert (r.stop, r.raw_stop, r.dropped_blocks) == ("tool_use", "STOP", 1)
    assert r.usage == mp.Usage(None, 16, 9, 4, 0)
    assert (r.model, r.request_id) == ("g-reported", "resp-1")


@pytest.mark.parametrize("raw,stop", [("STOP", "end_turn"), ("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"),
                                      ("RECITATION", "refusal"), ("PROHIBITED_CONTENT", "refusal"),
                                      ("MALFORMED_FUNCTION_CALL", "other")])
def test_gemini_stop_map(monkeypatch: pytest.MonkeyPatch, raw: str, stop: str) -> None:
    r = _one(monkeypatch, "gemini", {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": raw}]})
    assert r.stop == stop


def test_gemini_blocked_prompt_is_a_refusal_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _one(monkeypatch, "gemini", {"promptFeedback": {"blockReason": "SAFETY"}})
    assert (r.stop, r.raw_stop, r.content) == ("refusal", "SAFETY", ())


def test_gemini_no_candidates_is_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(mp.ModelProviderError) as err:
        _one(monkeypatch, "gemini", {"candidates": []})
    assert err.value.kind == "protocol"


def test_bad_tool_name_from_model_is_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"content": [{"type": "tool_use", "id": "t", "name": "../evil", "input": {}}], "stop_reason": "tool_use"}
    with pytest.raises(mp.ModelProviderError) as err:
        _one(monkeypatch, "anthropic", reply)
    assert err.value.kind == "protocol"


def test_anthropic_thinking_blocks_go_back_verbatim_and_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # "pass back every thinking and redacted_thinking block ... exactly as received" (api/errors, 2026-10-31)
    think = [{"type": "thinking", "thinking": "plan", "signature": "sig1"}, {"type": "redacted_thinking", "data": "d"}]
    first = {"content": [*think, {"type": "tool_use", "id": "toolu_1", "name": "run_code", "input": {}},
                         {"type": "server_tool_use", "id": "s", "name": "web_search", "input": {}}],
             "stop_reason": "tool_use"}
    rec = Recorder(monkeypatch, first, {"content": [], "stop_reason": "end_turn"})
    p = provider("anthropic")
    r = mp.complete(p, "m", [user("q")], max_tokens=8, timeout=5)
    assert r.dropped_blocks == 1 and r.content[0]["_provider_sig"]
    history = [user("q"), {"role": "assistant", "content": list(r.content)},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}]
    mp.complete(p, "m", history, max_tokens=8, timeout=5)
    assert rec.body["messages"][1]["content"] == [*think, {"type": "tool_use", "id": "toolu_1", "name": "run_code",
                                                           "input": {}}]


def test_gemini_text_signature_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"candidates": [{"content": {"parts": [{"text": "a", "thoughtSignature": "QUJD"}]},
                             "finishReason": "STOP"}]}
    rec = Recorder(monkeypatch, reply, reply)
    p = provider("gemini")
    r = mp.complete(p, "g", [user("q")], max_tokens=8, timeout=5)
    assert r.content == ({"type": "text", "text": "a", "_provider_sig": "QUJD"},)
    mp.complete(p, "g", [user("q"), {"role": "assistant", "content": list(r.content)}, user("more")],
                max_tokens=8, timeout=5)
    assert rec.body["contents"][1]["parts"] == [{"text": "a", "thoughtSignature": "QUJD"}]


def test_reserved_keys_refused_in_user_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    Recorder(monkeypatch)
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(provider("anthropic"), "m", [{"role": "user", "content": [
            {"type": "text", "text": "x", "_provider_sig": "[]"}]}], max_tokens=8, timeout=5)
    assert err.value.kind == "bad_request"
