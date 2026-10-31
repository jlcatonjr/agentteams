"""Neutral-request validation for :mod:`agentteams.model_providers` (runs before any network I/O)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from agentteams.model_providers._anthropic import _opaque_blocks
from agentteams.model_providers._common import (
    _RESERVED_KEYS, MAX_PROVIDER_ID_CHARS, MAX_PROVIDER_SIG_CHARS, MAX_TOKENS_RANGE, SAFE_ID_RE, TOOL_NAME_RE,
    ModelProviderError, ToolSpec, _text_ok,
)
from agentteams.model_providers._gemini import _GEMINI_SIG_RE

def _sig_ok(sig: object) -> bool:
    """A ``_provider_sig`` is a Gemini base64 signature or a JSON list of Anthropic thinking blocks, size-capped."""
    if not isinstance(sig, str) or not sig or len(sig) > MAX_PROVIDER_SIG_CHARS or "\x00" in sig:
        return False
    if sig.startswith("["):
        return _opaque_blocks(sig) is not None
    return _GEMINI_SIG_RE.fullmatch(sig) is not None



def _check_request(messages: Sequence[Mapping[str, Any]], system: str | None, tools: Sequence[ToolSpec],
                   max_tokens: int, model: str, temperature: float | None) -> None:
    """Validate a neutral request before any network I/O (nothing is spent on a malformed call).

    Raises:
        ModelProviderError: ``bad_request`` describing the first problem found.
    """
    def bad(msg: str) -> ModelProviderError:
        return ModelProviderError("bad_request", msg)

    if not isinstance(model, str) or not model.strip() or not _text_ok(model) or not model.isprintable():
        raise ModelProviderError("config", "a model id is required (no default model exists)")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or \
            not MAX_TOKENS_RANGE[0] <= max_tokens <= MAX_TOKENS_RANGE[1]:
        raise bad(f"max_tokens must be an integer in [{MAX_TOKENS_RANGE[0]}, {MAX_TOKENS_RANGE[1]}]")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                                    or not math.isfinite(temperature) or not 0 <= temperature <= 2):
        raise bad("temperature must be a finite number in [0, 2]")
    if system is not None and not _text_ok(system):
        raise bad("system must be a NUL-free string")
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, ToolSpec) or not isinstance(tool.name, str) or not TOOL_NAME_RE.fullmatch(tool.name):
            raise bad("every tool must be a ToolSpec with a name matching [A-Za-z_][A-Za-z0-9_]{0,63}")
        if tool.name in names:
            raise bad(f"duplicate tool name {tool.name!r}")
        names.add(tool.name)
        if not _text_ok(tool.description) or not isinstance(tool.input_schema, Mapping) or \
                tool.input_schema.get("type") != "object":
            raise bad(f"tool {tool.name!r}: description must be a string and input_schema an object schema")
        if "\x00" in json.dumps(tool.input_schema, allow_nan=False):
            raise bad(f"tool {tool.name!r}: input_schema contains NUL")
    if not isinstance(messages, Sequence) or not messages:
        raise bad("messages must be a non-empty list")
    pending: set[str] = set()            # tool_use ids of the previous assistant turn
    for i, msg in enumerate(messages):
        role = msg.get("role") if isinstance(msg, Mapping) else None
        if role != ("user" if i % 2 == 0 else "assistant") or set(msg) != {"role", "content"}:
            raise bad(f"messages[{i}]: roles must alternate user/assistant starting with user, keys role+content")
        content = msg["content"]
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes)) or not content:
            raise bad(f"messages[{i}]: content must be a non-empty list of blocks")
        seen: set[str] = set()
        for j, block in enumerate(content):
            where = f"messages[{i}].content[{j}]"
            kind = block.get("type") if isinstance(block, Mapping) else None
            if kind == "text":
                extra = set(block) - {"type", "text"}
                if "text" not in block or not _text_ok(block["text"]) or \
                        extra - ({"_provider_sig"} if role == "assistant" else set()) or \
                        (extra and not _sig_ok(block["_provider_sig"])):
                    raise bad(f"{where}: a text block is {{type, text}} with a NUL-free string")
            elif kind == "tool_use" and role == "assistant":
                if not {"type", "id", "name", "input"} <= set(block) or \
                        set(block) - {"type", "id", "name", "input"} - _RESERVED_KEYS:
                    raise bad(f"{where}: a tool_use block is {{type, id, name, input}}")
                if not isinstance(block["id"], str) or not SAFE_ID_RE.fullmatch(block["id"]) or block["id"] in seen:
                    raise bad(f"{where}: tool_use id must be unique and match [A-Za-z0-9_-]{{1,64}}")
                if not isinstance(block["name"], str) or not TOOL_NAME_RE.fullmatch(block["name"]) or \
                        not isinstance(block["input"], Mapping):
                    raise bad(f"{where}: tool_use needs a valid name and an object input")
                pid = block.get("_provider_id")
                if (pid is not None and not (_text_ok(pid) and 0 < len(pid) <= MAX_PROVIDER_ID_CHARS)) or \
                        ("_provider_sig" in block and not _sig_ok(block["_provider_sig"])):
                    raise bad(f"{where}: _provider_id / _provider_sig malformed or too long")
                seen.add(block["id"])
            elif kind == "tool_result" and role == "user":
                if not {"type", "tool_use_id", "content"} <= set(block) or \
                        set(block) - {"type", "tool_use_id", "content", "is_error"}:
                    raise bad(f"{where}: a tool_result block is {{type, tool_use_id, content, is_error?}}")
                if block["tool_use_id"] not in pending:
                    raise bad(f"{where}: tool_result answers no tool_use of the preceding assistant turn")
                if not _text_ok(block["content"]) or not isinstance(block.get("is_error", False), bool):
                    raise bad(f"{where}: tool_result content must be a NUL-free string, is_error a bool")
                pending.discard(block["tool_use_id"])
            else:
                raise bad(f"{where}: block type {kind!r} is not allowed in a {role} turn")
        if role == "user":
            pending = set()
        else:
            pending = seen
    try:
        json.dumps(list(messages), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise bad("messages must be JSON-serialisable with finite numbers") from exc
