"""Shared types, limits and helpers for :mod:`agentteams.model_providers` (no I/O here)."""

from __future__ import annotations

import json
import math
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agentteams.vulnscan.gateway_dialects import Usage

Block = dict  # {"type":"text",...} | {"type":"tool_use",...} | {"type":"tool_result",...}

#: Normalised stop reasons in :attr:`ModelReply.stop`.
STOPS = ("end_turn", "tool_use", "max_tokens", "refusal", "other")

#: Error kinds of :class:`ModelProviderError` (closed set).
KINDS = ("config", "auth", "not_found", "bad_request", "too_large", "rate_limited", "overloaded", "server",
         "timeout", "network", "protocol", "refused")

#: Model tiers a team configures (no default model id exists for any of them).
TIERS = ("primary", "cheap")

TIER_ENV_PREFIX = "AGENTTEAMS_WASM_MODEL_"

CI_ALLOW_ENV = "AGENTTEAMS_ALLOW_LIVE_MODEL_IN_CI"

MAX_MESSAGE_CHARS = 300

MAX_TOKENS_RANGE = (1, 1_000_000)

TOOL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

SAFE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_RESERVED_KEYS = frozenset({"_provider_id", "_provider_sig"})

#: Caps on the reserved keys (a forged value must not become a large side channel; P05's event frame is 4 MiB).
MAX_PROVIDER_ID_CHARS = 256

MAX_PROVIDER_SIG_CHARS = 1024 * 1024



class ModelProviderError(Exception):
    """A model call failed; ``kind`` is one of :data:`KINDS` and ``message`` never carries a key."""

    def __init__(self, kind: str, message: str, *, status: int | None = None, provider: str = "",
                 attempts: int = 0, retry_after: float | None = None) -> None:
        """Build the error.

        Args:
            kind: One of :data:`KINDS`.
            message: Human-readable reason; capped at :data:`MAX_MESSAGE_CHARS` (callers scrub it first).
            status: HTTP status, when there was one.
            provider: Preset name.
            attempts: HTTP attempts made (each may have been billed).
            retry_after: The provider's ``Retry-After`` in seconds, when it sent one.

        Raises:
            ValueError: ``kind`` is not in :data:`KINDS`.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown ModelProviderError kind {kind!r}")
        self.kind = kind
        self.message = message[:MAX_MESSAGE_CHARS]
        self.status = status
        self.provider = provider
        self.attempts = attempts
        self.retry_after = retry_after
        super().__init__(f"{kind}: {self.message}")

    def rescrub(self, key: str) -> None:
        """Scrub ``key`` from :attr:`message` and from ``args`` (which ``str()`` reads).

        Args:
            key: The secret to remove.

        Returns:
            None.
        """
        self.message = _scrub(self.message, key)
        self.args = (f"{self.kind}: {self.message}",)



@dataclass(frozen=True)
class ToolSpec:
    """A tool the model may call: a name, a description and a JSON Schema for its input."""

    name: str
    description: str
    input_schema: Mapping[str, Any]



@dataclass(frozen=True)
class ModelReply:
    """One model turn, normalised across families.

    ``content`` holds only ``text`` and ``tool_use`` blocks; ``stop`` is one of :data:`STOPS` and ``raw_stop`` the
    provider's own value; ``dropped_blocks`` counts reply parts of other types (server-tool results, Gemini
    thought summaries, unknown types), which are discarded as data and never acted on. Continuation data the
    provider needs back (thinking blocks, thought signatures) rides in ``_provider_sig`` instead.
    """

    content: tuple[Block, ...]
    stop: str
    raw_stop: str
    usage: Usage
    provider: str
    model: str
    request_id: str | None = None
    dropped_blocks: int = 0



def _no_dup_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key in JSON object")
        out[k] = v
    return out



def _no_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name} in JSON")



def _strict_loads(text: str | bytes) -> Any:
    """Parse JSON refusing duplicate keys and NaN/Infinity.

    Raises:
        ValueError: Malformed JSON, a duplicate key or a non-finite number.
    """
    return json.loads(text, object_pairs_hook=_no_dup_pairs, parse_constant=_no_constant)



_HEADER_ECHO = re.compile(r"(?i)(x-api-key|x-goog-api-key|authorization|api[_-]?key)(\"?\s*[:=]\s*\"?)"
                          r"(bearer\s+)?[^\s\"',;}]+")

_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")



def _scrub(text: str, key: str = "") -> str:
    """Remove ``key`` and any header-style credential echo from ``text`` and cap its length."""
    if key and len(key) >= 4:
        text = text.replace(key, "<redacted>")
    text = _HEADER_ECHO.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    text = _BEARER.sub("Bearer <redacted>", text)
    return text[:MAX_MESSAGE_CHARS]



def _text_ok(value: object) -> bool:
    return isinstance(value, str) and "\x00" not in value



def _is_loopback(url: str | None) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return host in LOOPBACK_HOSTS



def _provider_ids(messages: Sequence[Mapping[str, Any]], index: int) -> dict[str, Any]:
    """``{neutral id: provider id}`` for the tool_use blocks of assistant turn ``index`` (``None``: provider sent none)."""
    out: dict[str, Any] = {}
    if 0 <= index < len(messages):
        for block in messages[index]["content"]:
            if block.get("type") == "tool_use":
                out[block["id"]] = block["_provider_id"] if "_provider_id" in block else block["id"]
    return out



def _neutral_tool_use(raw_id: object, name: str, args: Mapping[str, Any], turn: int, n: int,
                      sig: str | None = None) -> Block:
    """Build a tool_use block, replacing an unsafe or missing provider id with ``tu_<turn>_<n>``."""
    block: Block = {"type": "tool_use", "id": "", "name": name, "input": dict(args)}
    if isinstance(raw_id, str) and SAFE_ID_RE.fullmatch(raw_id):
        block["id"] = raw_id
    else:
        block["id"] = f"tu_{turn}_{n}"
        block["_provider_id"] = raw_id if isinstance(raw_id, str) and raw_id else None
    if sig is not None:
        block["_provider_sig"] = sig
    return block



def _tool_name_ok(name: object) -> bool:
    return isinstance(name, str) and TOOL_NAME_RE.fullmatch(name) is not None



def _count(obj: Mapping[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return 0
    return int(value)



def _str_or_none(value: object) -> str | None:
    return value[:200] if isinstance(value, str) and value else None



def _header(headers: Mapping[str, str], name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name:
            return _str_or_none(v)
    return None
