"""OpenAI-compatible Chat Completions encode/decode for :mod:`agentteams.model_providers`."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agentteams.model_providers._common import (
    Block, ModelProviderError, ModelReply, ToolSpec, Usage, _count, _header, _neutral_tool_use, _provider_ids,
    _str_or_none, _strict_loads, _tool_name_ok,
)

if TYPE_CHECKING:
    from agentteams.model_providers._config import Preset, Provider

_OPENAI_STOPS = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use",
                 "length": "max_tokens", "content_filter": "refusal"}



def endpoint(provider: Provider, model: str) -> tuple[str, dict[str, str]]:
    """Return the Chat Completions URL and ``Authorization: Bearer`` header (none for a keyless preset).

    Args:
        provider: The provider (its key goes into the returned headers only).
        model: Unused (the model rides in the body).

    Returns:
        ``(url, headers)``.
    """
    key = provider._key
    return f"{provider.base_url}/chat/completions", ({"Authorization": f"Bearer {key}"} if key else {})


def encode(model: str, messages: Sequence[Mapping[str, Any]], system: str | None,
                   tools: Sequence[ToolSpec], max_tokens: int, temperature: float | None,
                   preset: Preset) -> dict[str, Any]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for i, msg in enumerate(messages):
        texts = [b["text"] for b in msg["content"] if b["type"] == "text"]
        if msg["role"] == "assistant":
            calls = [{"id": b.get("_provider_id") or b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"], allow_nan=False)}}
                     for b in msg["content"] if b["type"] == "tool_use"]
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) if texts else None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        ids = _provider_ids(messages, i - 1)
        for b in msg["content"]:
            if b["type"] == "tool_result":
                # Chat Completions has no is_error flag on a tool message; the marker keeps the signal.
                text = ("[tool error] " + b["content"]) if b.get("is_error") else b["content"]
                out.append({"role": "tool", "tool_call_id": ids.get(b["tool_use_id"]) or b["tool_use_id"],
                            "content": text})
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    body: dict[str, Any] = {"model": model, preset.max_tokens_param: max_tokens, "messages": out}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                           "parameters": dict(t.input_schema)}} for t in tools]
    if temperature is not None:
        body["temperature"] = temperature
    body.update(json.loads(json.dumps(preset.extra_body)))
    return body



def decode(payload: Any, headers: Mapping[str, str], turn: int, preset: Preset,
                   model: str) -> ModelReply:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or \
            not isinstance(choices[0].get("message"), dict):
        raise ModelProviderError("protocol", "Chat Completions reply has no choices[0].message")
    message = choices[0]["message"]
    content: list[Block] = []
    dropped = 0
    text = message.get("content")
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    elif isinstance(text, list):          # some compatible servers return content parts
        for part in text:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                content.append({"type": "text", "text": part["text"]})
            else:
                dropped += 1
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        content.append({"type": "text", "text": refusal})
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        raise ModelProviderError("protocol", "tool_calls is not a list")
    for call in calls:
        if not isinstance(call, dict) or call.get("type", "function") != "function" or \
                not isinstance(call.get("function"), dict):
            dropped += 1          # custom/unknown tool-call types are never requested; drop as data
            continue
        fn = call["function"]
        if not _tool_name_ok(fn.get("name")):
            raise ModelProviderError("protocol", "tool call with an invalid function name")
        raw_args = fn.get("arguments")
        try:
            args = _strict_loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else {}
        except ValueError as exc:
            raise ModelProviderError("protocol", f"unparseable arguments for tool {fn['name']!r}") from exc
        if not isinstance(args, dict):
            raise ModelProviderError("protocol", f"arguments for tool {fn['name']!r} are not a JSON object")
        content.append(_neutral_tool_use(call.get("id"), fn["name"], args, turn, len(content)))
    raw_stop = choices[0].get("finish_reason")
    raw_stop = raw_stop if isinstance(raw_stop, str) else ""
    stop = _OPENAI_STOPS.get(raw_stop, "other")
    if isinstance(refusal, str) and refusal:
        stop = "refusal"
    elif stop == "end_turn" and any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"     # some compatible servers report "stop" alongside tool calls
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    prompt = _count(usage, "prompt_tokens")
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = min(prompt, _count(details, "cached_tokens"))
    cost = usage.get("cost")
    cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and \
        math.isfinite(cost) and cost >= 0 else None
    usage_obj = Usage(cost, prompt - cached, _count(usage, "completion_tokens"), cached, 0)
    # UNVERIFIED: the request-id header name for OpenAI-compatible servers; the body "id" is used.
    request_id = _str_or_none(payload.get("id")) or _header(headers, "x-request-id")
    return ModelReply(tuple(content), stop, raw_stop, usage_obj, preset.name,
                      _str_or_none(payload.get("model")) or model, request_id, dropped)
