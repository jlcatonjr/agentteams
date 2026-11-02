"""Deterministic, offline stub model for :mod:`agentteams.model_providers` (never touches the network)."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agentteams.atomicio import read_regular_nofollow
from agentteams.model_providers._common import (
    SAFE_ID_RE, STOPS, ModelProviderError, ModelReply, ToolSpec, Usage, _count, _strict_loads, _text_ok,
    _tool_name_ok,
)

MAX_STUB_SCRIPT_BYTES = 64 * 1024



def _finite_count(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _validate_reply_dict(item: Any, where: str) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) - {"content", "stop", "usage"} or "content" not in item:
        raise ModelProviderError("protocol", f"{where}: a stub reply is {{content, stop?, usage?}}")
    if not isinstance(item["content"], list):
        raise ModelProviderError("protocol", f"{where}: content must be a list")
    for block in item["content"]:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "text" and set(block) == {"type", "text"} and _text_ok(block["text"]):
            continue
        if kind == "tool_use" and set(block) == {"type", "id", "name", "input"} and \
                isinstance(block["id"], str) and SAFE_ID_RE.fullmatch(block["id"]) and \
                _tool_name_ok(block["name"]) and isinstance(block["input"], dict):
            continue
        raise ModelProviderError("protocol", f"{where}: content holds a block that is not text or tool_use")
    if item.get("stop", "end_turn") not in STOPS:
        raise ModelProviderError("protocol", f"{where}: stop must be one of {', '.join(STOPS)}")
    usage = item.get("usage", {})
    if not isinstance(usage, dict) or set(usage) - {"input_tokens", "output_tokens", "cost"} or \
            not all(_finite_count(v) for v in usage.values()):
        raise ModelProviderError("protocol", f"{where}: usage is {{input_tokens?, output_tokens?, cost?}} "
                                 "with finite non-negative numbers")
    return item



def _load_stub_script(path: Path) -> list[dict[str, Any]]:
    """Read a stub script file: a regular file (no symlink), at most 64 KiB, strict JSON list of reply dicts.

    Raises:
        ModelProviderError: ``protocol`` when unreadable, oversize, malformed or not the reply shape.
    """
    try:
        raw = read_regular_nofollow(path, MAX_STUB_SCRIPT_BYTES)
        script = _strict_loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ModelProviderError("protocol", f"stub script unreadable or malformed: {type(exc).__name__}: "
                                 f"{str(exc)[:120]}", provider="stub") from exc
    if not isinstance(script, list):
        raise ModelProviderError("protocol", "stub script must be a JSON list of reply dicts", provider="stub")
    return [_validate_reply_dict(item, f"stub script[{i}]") for i, item in enumerate(script)]



StubScript = Sequence[Mapping[str, Any]] | Callable[[list, str | None, tuple], Mapping[str, Any]] | None



class StubProvider:
    """Deterministic, offline model. Never touches the network.

    ``script`` may be a list of reply dicts (``{content, stop?, usage?}``) consumed in order, a callable
    ``(messages, system, tools) -> reply dict``, or ``None`` for echo mode (``"stub:" + sha256(last user text)[:12]``).
    Every request it receives is appended to :attr:`requests`.
    """

    def __init__(self, script: StubScript = None) -> None:
        """Build the stub.

        Args:
            script: Reply list, callable, or ``None`` for echo mode.

        Raises:
            ModelProviderError: ``protocol`` when a scripted reply is malformed.
        """
        if script is not None and not callable(script):
            script = [_validate_reply_dict(dict(item), f"stub script[{i}]") for i, item in enumerate(script)]
        self._script = script
        self._next = 0
        self.requests: list[dict[str, Any]] = []

    def reply(self, model: str, messages: Sequence[Mapping[str, Any]], system: str | None,
              tools: Sequence[ToolSpec], max_tokens: int) -> ModelReply:
        """Answer one request.

        Args:
            model: Model id (recorded only).
            messages: Neutral messages.
            system: System prompt.
            tools: Tool specs.
            max_tokens: Output ceiling (recorded only).

        Returns:
            The scripted or echo reply.

        Raises:
            ModelProviderError: ``protocol`` when the script is exhausted or a callable returns a bad reply.
        """
        snapshot = json.loads(json.dumps(list(messages)))
        self.requests.append({"model": model, "messages": snapshot, "system": system,
                              "tools": [t.name for t in tools], "max_tokens": max_tokens})
        if self._script is None:
            item = self._echo(snapshot, system)
        elif callable(self._script):
            item = _validate_reply_dict(dict(self._script(snapshot, system, tuple(tools))), "stub callable reply")
        else:
            if self._next >= len(self._script):
                raise ModelProviderError("protocol", "stub script exhausted", provider="stub")
            item = self._script[self._next]
            self._next += 1
        usage = item.get("usage", {})
        cost = usage.get("cost")
        return ModelReply(tuple(json.loads(json.dumps(item["content"]))), item.get("stop", "end_turn"),
                          item.get("stop", "end_turn"),
                          Usage(float(cost) if isinstance(cost, (int, float)) else None,
                                _count(usage, "input_tokens"), _count(usage, "output_tokens")),
                          "stub", model, None, 0)

    @staticmethod
    def _echo(messages: list[dict[str, Any]], system: str | None) -> dict[str, Any]:
        last_user = next((m for m in reversed(messages) if m["role"] == "user"), {"content": []})
        text = "".join(b.get("text", "") for b in last_user["content"] if b.get("type") == "text")
        answer = "stub:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        in_bytes = len((system or "").encode()) + sum(
            len(b.get("text", b.get("content", "")).encode()) for m in messages for b in m["content"]
            if isinstance(b.get("text", b.get("content", "")), str))
        return {"content": [{"type": "text", "text": answer}], "stop": "end_turn",
                "usage": {"input_tokens": in_bytes // 4, "output_tokens": len(answer) // 4}}
