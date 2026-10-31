"""Gemini ``generateContent`` encode/decode for :mod:`agentteams.model_providers`."""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agentteams.model_providers._common import (
    Block, ModelProviderError, ModelReply, ToolSpec, Usage, _count, _header, _neutral_tool_use, _str_or_none,
    _tool_name_ok,
)

if TYPE_CHECKING:
    from agentteams.model_providers._config import Preset, Provider

GEMINI_MODEL_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")

#: JSON Schema keywords kept when a tool schema goes to Gemini; everything else (``additionalProperties``,
#: ``$schema``, ``default``, ...) is dropped so one tool schema works across families.
GEMINI_SCHEMA_KEYS = frozenset({"type", "properties", "required", "description", "items", "enum"})

_GEMINI_SIG_RE = re.compile(r"[A-Za-z0-9+/=_-]+")

_GEMINI_REFUSALS = frozenset({"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY",
                              "IMAGE_PROHIBITED_CONTENT", "LANGUAGE"})



def _gemini_schema(schema: Any) -> Any:
    """Keep only :data:`GEMINI_SCHEMA_KEYS`, recursively through ``properties`` and ``items``."""
    if not isinstance(schema, Mapping):
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k not in GEMINI_SCHEMA_KEYS:
            continue
        if k == "properties" and isinstance(v, Mapping):
            out[k] = {pk: _gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = _gemini_schema(v)
        else:
            out[k] = v
    return out



def _gemini_model_path(model: str) -> str:
    name = model[len("models/"):] if model.startswith("models/") else model
    if not GEMINI_MODEL_RE.fullmatch(name) or any(seg in ("", ".", "..") for seg in name.split("/")):
        raise ModelProviderError("config", "Gemini model id must match [A-Za-z0-9._/-]+ with no dot or empty "
                                 "path segments")
    return urllib.parse.quote(name, safe="/")



def _is_gemini_sig(sig: object) -> bool:
    # A thoughtSignature is base64 (bytes field); an Anthropic JSON list is never sent to Gemini.
    return isinstance(sig, str) and _GEMINI_SIG_RE.fullmatch(sig) is not None



def endpoint(provider: Provider, model: str) -> tuple[str, dict[str, str]]:
    """Return the ``generateContent`` URL and the ``x-goog-api-key`` header (never the ``?key=`` query).

    Args:
        provider: The provider (its key goes into the returned headers only).
        model: Model id, checked and quoted into the path.

    Returns:
        ``(url, headers)``.

    Raises:
        ModelProviderError: ``config`` for a model id that could leave ``/models/``.
    """
    return (f"{provider.base_url}/models/{_gemini_model_path(model)}:generateContent",
            {"x-goog-api-key": provider._key})


def encode(model: str, messages: Sequence[Mapping[str, Any]], system: str | None,
                   tools: Sequence[ToolSpec], max_tokens: int, temperature: float | None,
                   preset: Preset) -> dict[str, Any]:
    contents = []
    for i, msg in enumerate(messages):
        parts: list[dict[str, Any]] = []
        if msg["role"] == "assistant":
            for b in msg["content"]:
                if b["type"] == "text":
                    text_part: dict[str, Any] = {"text": b["text"]}
                    if _is_gemini_sig(b.get("_provider_sig")):
                        text_part["thoughtSignature"] = b["_provider_sig"]
                    parts.append(text_part)
                else:
                    call: dict[str, Any] = {"name": b["name"], "args": dict(b["input"])}
                    pid = b["_provider_id"] if "_provider_id" in b else b["id"]
                    if pid is not None:
                        call["id"] = pid
                    part: dict[str, Any] = {"functionCall": call}
                    # UNVERIFIED: echoing thoughtSignature on the functionCall part, as received, is what the
                    # thinking guide implies ("signatures ... living inside functionCall parts"); not shown in REST.
                    if _is_gemini_sig(b.get("_provider_sig")):
                        part["thoughtSignature"] = b["_provider_sig"]
                    parts.append(part)
            contents.append({"role": "model", "parts": parts})
            continue
        prev = {b["id"]: b for b in messages[i - 1]["content"] if b["type"] == "tool_use"} if i else {}
        for b in msg["content"]:
            if b["type"] == "text":
                parts.append({"text": b["text"]})
                continue
            use = prev[b["tool_use_id"]]
            # FunctionResponse.response: "any keys of their choice"; an "error" key for a failed call (discovery doc).
            resp: dict[str, Any] = {"name": use["name"],
                                    "response": {"error" if b.get("is_error") else "output": b["content"]}}
            pid = use["_provider_id"] if "_provider_id" in use else use["id"]
            if pid is not None:
                resp["id"] = pid
            parts.append({"functionResponse": resp})
        contents.append({"role": "user", "parts": parts})
    body: dict[str, Any] = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
    if temperature is not None:
        body["generationConfig"]["temperature"] = temperature
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        # parametersJsonSchema takes JSON Schema as-is (discovery doc); stripping keeps one schema portable.
        body["tools"] = [{"functionDeclarations": [
            {"name": t.name, "description": t.description, "parametersJsonSchema": _gemini_schema(t.input_schema)}
            for t in tools]}]
    return body



def decode(payload: Any, headers: Mapping[str, str], turn: int, preset: Preset,
                   model: str) -> ModelReply:
    if not isinstance(payload, dict):
        raise ModelProviderError("protocol", "generateContent reply is not an object")
    meta = payload.get("usageMetadata") if isinstance(payload.get("usageMetadata"), dict) else {}
    prompt = _count(meta, "promptTokenCount")
    cached = min(prompt, _count(meta, "cachedContentTokenCount"))
    # UNVERIFIED: that thoughtsTokenCount is billed as output alongside candidatesTokenCount.
    usage_obj = Usage(None, prompt - cached, _count(meta, "candidatesTokenCount") + _count(meta, "thoughtsTokenCount"),
                      cached, 0)
    reported = _str_or_none(payload.get("modelVersion")) or model
    request_id = _str_or_none(payload.get("responseId")) or _header(headers, "x-request-id")
    candidates = payload.get("candidates")
    if not candidates:
        feedback = payload.get("promptFeedback") if isinstance(payload.get("promptFeedback"), dict) else {}
        block = feedback.get("blockReason")
        if isinstance(block, str) and block:
            return ModelReply((), "refusal", block, usage_obj, preset.name, reported, request_id, 0)
        raise ModelProviderError("protocol", "generateContent reply has no candidates")
    cand = candidates[0] if isinstance(candidates, list) and isinstance(candidates[0], dict) else None
    if cand is None:
        raise ModelProviderError("protocol", "generateContent candidates[0] is not an object")
    parts = cand.get("content", {}).get("parts", []) if isinstance(cand.get("content"), dict) else []
    content: list[Block] = []
    dropped = 0
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict) or part.get("thought") is True:
            dropped += 1
        elif isinstance(part.get("text"), str):
            sig = part.get("thoughtSignature")
            if part["text"] or isinstance(sig, str):
                text_block: Block = {"type": "text", "text": part["text"]}
                if isinstance(sig, str) and sig:
                    text_block["_provider_sig"] = sig
                content.append(text_block)
        elif isinstance(part.get("functionCall"), dict):
            call = part["functionCall"]
            if not _tool_name_ok(call.get("name")):
                raise ModelProviderError("protocol", "functionCall with an invalid name")
            args = call.get("args", {})
            if not isinstance(args, dict):
                raise ModelProviderError("protocol", "functionCall args is not an object")
            sig = part.get("thoughtSignature")
            content.append(_neutral_tool_use(call.get("id"), call["name"], args, turn, len(content),
                                             sig if isinstance(sig, str) else None))
        else:
            dropped += 1
    raw_stop = cand.get("finishReason")
    raw_stop = raw_stop if isinstance(raw_stop, str) else ""
    if raw_stop in _GEMINI_REFUSALS:
        stop = "refusal"
    elif raw_stop == "MAX_TOKENS":
        stop = "max_tokens"
    elif raw_stop == "STOP":
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
    else:
        stop = "other"
    return ModelReply(tuple(content), stop, raw_stop, usage_obj, preset.name, reported, request_id, dropped)
