"""Anthropic Messages encode/decode for :mod:`agentteams.model_providers` (``{base}/messages``)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agentteams.vulnscan.gateway_dialects import ANTHROPIC_VERSIONS

from agentteams.model_providers._common import (
    Block, ModelProviderError, ModelReply, ToolSpec, Usage, _count, _header, _neutral_tool_use, _provider_ids,
    _str_or_none, _strict_loads, _tool_name_ok,
)

if TYPE_CHECKING:
    from agentteams.model_providers._config import Preset, Provider

#: The Messages API version sent. The repo already pins exactly this one (``gateway_dialects.ANTHROPIC_VERSIONS``).
#: 2026-10-21 also exists (verified) and removes ``temperature``; moving to it is a separate, reviewed change.
ANTHROPIC_VERSION = "2023-06-01"
assert ANTHROPIC_VERSION in ANTHROPIC_VERSIONS  # noqa: S101 - import-time pin check, not input validation

#: Anthropic block types returned verbatim on the next request ("pass back every thinking and redacted_thinking
#: block ... exactly as received" with tool use: platform.claude.com/docs/en/api/errors, verified 2026-10-31).
_ANTHROPIC_OPAQUE: dict[str, tuple[str, ...]] = {"thinking": ("thinking", "signature"),
                                                  "redacted_thinking": ("data",)}

_ANTHROPIC_STOPS = {"end_turn": "end_turn", "stop_sequence": "end_turn", "tool_use": "tool_use",
                    "max_tokens": "max_tokens", "model_context_window_exceeded": "max_tokens",
                    "refusal": "refusal"}



def endpoint(provider: Provider, model: str) -> tuple[str, dict[str, str]]:
    """Return the Messages URL and auth headers (``x-api-key`` + ``anthropic-version``; api/errors curl, verified).

    Args:
        provider: The provider (its key goes into the returned headers only).
        model: Unused (the model rides in the body).

    Returns:
        ``(url, headers)``.
    """
    return f"{provider.base_url}/messages", {"x-api-key": provider._key, "anthropic-version": ANTHROPIC_VERSION}


def encode(model: str, messages: Sequence[Mapping[str, Any]], system: str | None,
                      tools: Sequence[ToolSpec], max_tokens: int, temperature: float | None,
                      preset: Preset) -> dict[str, Any]:
    out_messages = []
    for i, msg in enumerate(messages):
        ids = _provider_ids(messages, i - 1) if msg["role"] == "user" else {}
        blocks: list[dict[str, Any]] = []
        for b in msg["content"]:
            blocks.extend(_anthropic_opaque(b.get("_provider_sig")))
            if b["type"] == "text":
                blocks.append({"type": "text", "text": b["text"]})
            elif b["type"] == "tool_use":
                pid = b.get("_provider_id") or b["id"]
                blocks.append({"type": "tool_use", "id": pid, "name": b["name"], "input": dict(b["input"])})
            else:
                blocks.append({"type": "tool_result", "tool_use_id": ids.get(b["tool_use_id"]) or b["tool_use_id"],
                               "content": b["content"], "is_error": bool(b.get("is_error", False))})
        out_messages.append({"role": msg["role"], "content": blocks})
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": out_messages}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [{"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
                         for t in tools]
    if temperature is not None:
        body["temperature"] = temperature
    return body



def _opaque_shape(block: object) -> dict[str, str] | None:
    """Return a thinking block reduced to its exact documented keys, or ``None`` when it is not one."""
    if not isinstance(block, dict):
        return None
    keys = _ANTHROPIC_OPAQUE.get(block.get("type"))  # type: ignore[arg-type]
    if keys is None or not all(isinstance(block.get(k), str) for k in keys):
        return None
    return {"type": block["type"], **{k: block[k] for k in keys}}



def _opaque_blocks(sig: str) -> list[dict[str, Any]] | None:
    """Parse a JSON-list ``_provider_sig``; ``None`` unless it is exactly a non-empty list of thinking blocks."""
    try:
        blocks = _strict_loads(sig)
    except ValueError:
        return None
    if not isinstance(blocks, list) or not blocks or any(_opaque_shape(b) != b for b in blocks):
        return None
    return blocks


def _anthropic_opaque(sig: object) -> list[dict[str, Any]]:
    """The thinking blocks carried in ``_provider_sig`` (a Gemini signature yields none).

    Raises:
        ModelProviderError: ``bad_request`` when a JSON-list signature is not exactly a list of thinking blocks
            with their documented keys (a forged value is refused, not forwarded).
    """
    if not isinstance(sig, str) or not sig.startswith("["):
        return []
    blocks = _opaque_blocks(sig)
    if blocks is None:
        raise ModelProviderError("bad_request", "_provider_sig is not a list of thinking blocks")
    return blocks



def decode(payload: Any, headers: Mapping[str, str], turn: int, preset: Preset,
                      model: str) -> ModelReply:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise ModelProviderError("protocol", "Messages reply has no content list")
    content: list[Block] = []
    dropped = 0
    opaque: list[dict[str, Any]] = []       # thinking blocks the API requires back unmodified with tool use
    for block in payload["content"]:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind in _ANTHROPIC_OPAQUE:
            shaped = _opaque_shape(block)
            if shaped is None:
                raise ModelProviderError("protocol", f"{kind} block without its documented string fields")
            opaque.append(shaped)
            continue
        if kind == "text" and isinstance(block.get("text"), str):
            out: Block = {"type": "text", "text": block["text"]}
        elif kind == "tool_use":
            if not _tool_name_ok(block.get("name")) or not isinstance(block.get("input"), dict):
                raise ModelProviderError("protocol", "tool_use block with a bad name or input")
            out = _neutral_tool_use(block.get("id"), block["name"], block["input"], turn, len(content))
        else:
            dropped += 1
            continue
        if opaque:
            out["_provider_sig"] = json.dumps(opaque, ensure_ascii=False, allow_nan=False)
            opaque = []
        content.append(out)
    dropped += len(opaque)                  # trailing thinking with nothing after it: not needed back
    raw_stop = payload.get("stop_reason")
    raw_stop = raw_stop if isinstance(raw_stop, str) else ""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    usage_obj = Usage(None, _count(usage, "input_tokens"), _count(usage, "output_tokens"),
                      _count(usage, "cache_read_input_tokens"), _count(usage, "cache_creation_input_tokens"))
    request_id = _header(headers, "request-id") or _str_or_none(payload.get("id"))
    return ModelReply(tuple(content), _ANTHROPIC_STOPS.get(raw_stop, "other"), raw_stop, usage_obj, preset.name,
                      _str_or_none(payload.get("model")) or model, request_id, dropped)
