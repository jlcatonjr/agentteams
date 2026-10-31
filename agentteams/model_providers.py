"""model_providers.py — one stdlib-only client for the model APIs the WASM agent host calls (METAPLAN §2.5).

The WASM agent host (P05) owns the conversation and calls :func:`complete` once per model turn. This module
speaks three wire families and a deterministic stub behind one neutral request/reply shape:

| Family | Presets | Endpoint |
|---|---|---|
| ``anthropic`` | ``anthropic`` | ``{base}/messages`` (Messages API) |
| ``openai`` | ``openai``, ``openrouter``, ``xai``, ``ollama``, ``lmstudio``, ``openai-compatible`` | ``{base}/chat/completions`` |
| ``gemini`` | ``gemini`` | ``{base}/models/{model}:generateContent`` |
| ``stub`` | ``stub`` | none: offline, deterministic (:class:`StubProvider`) |

**Neutral shape.** Messages use the Anthropic Messages block shape (``text``, ``tool_use``, ``tool_result``),
strictly alternating ``user``/``assistant`` and starting with ``user``, so the host's DLP filter runs on them
unchanged. Reply blocks may carry two reserved keys: ``_provider_id`` on a ``tool_use`` (the provider's own call
id when it was not a safe id, or ``None`` when the provider sent none) and ``_provider_sig`` on a ``text`` or
``tool_use`` (opaque continuation data the provider requires back verbatim: Gemini's ``thoughtSignature``, or the
JSON list of Anthropic ``thinking`` / ``redacted_thinking`` blocks that preceded the block). Both come from
:func:`complete`'s reply and go back to the same provider on the next request; the host strips them before a
guest sees a block and restores them from its transcript. They are model output (C-4 data), never interpreted.

**Model ids are never defaulted.** :func:`complete` requires ``model``; :func:`model_for_tier` resolves the
``primary`` / ``cheap`` tiers from the brief's configuration or ``AGENTTEAMS_WASM_MODEL_<TIER>`` and raises
``config`` when neither names one.

**Key custody.** A key lives only in a :class:`Provider` (``__slots__``, redacted ``repr``, not picklable). The
operator-facing source is the environment (:func:`get_provider`); :meth:`Provider.from_key` exists for the host
stage that receives its key over a pipe. Keys never reach a guest, a reply, a log or an exception: error text is
scrubbed (:func:`_scrub`) and capped. The module writes no files.

**Egress.** HTTPS only, except plain HTTP to a loopback host; no userinfo, query or fragment in a base URL; every
request URL is a fixed path joined to that base; redirects are refused (one would carry the key header to another
host); bodies are size-capped; request bodies are built from an allowlist (no ``stream``, no provider-side tools).

**Provider facts** (endpoints, headers, shapes, stop reasons). Checked 2026-10-31 against the official docs:
``platform.claude.com/docs/en/api/{messages,errors,versioning}`` and ``.../build-with-claude/handling-stop-reasons``;
``developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create``;
``openrouter.ai/docs/api-reference/chat-completion``; ``ai.google.dev/api/generate-content``,
``ai.google.dev/gemini-api/docs/api-key`` and the Generative Language v1beta discovery document;
``docs.ollama.com/api/openai-compatibility``; ``lmstudio.ai/docs/developer/openai-compat``; ``docs.x.ai``.
Items those pages did not settle are marked ``UNVERIFIED:`` where they are used.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import os
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.atomicio import read_regular_nofollow
from agentteams.vulnscan.gateway_dialects import ANTHROPIC_VERSIONS, Usage

__all__ = [
    "ANTHROPIC_VERSION", "KINDS", "PROVIDERS", "STOPS", "TIERS", "ModelProviderError", "ModelReply", "Preset",
    "Provider", "StubProvider", "ToolSpec", "complete", "get_provider", "load_key", "model_for_tier",
]

Block = dict  # {"type":"text",...} | {"type":"tool_use",...} | {"type":"tool_result",...}

#: The Messages API version sent. The repo already pins exactly this one (``gateway_dialects.ANTHROPIC_VERSIONS``).
#: 2026-10-21 also exists (verified) and removes ``temperature``; moving to it is a separate, reviewed change.
ANTHROPIC_VERSION = "2023-06-01"
assert ANTHROPIC_VERSION in ANTHROPIC_VERSIONS  # noqa: S101 - import-time pin check, not input validation

#: Normalised stop reasons in :attr:`ModelReply.stop`.
STOPS = ("end_turn", "tool_use", "max_tokens", "refusal", "other")
#: Error kinds of :class:`ModelProviderError` (closed set).
KINDS = ("config", "auth", "not_found", "bad_request", "too_large", "rate_limited", "overloaded", "server",
         "timeout", "network", "protocol", "refused")
#: Model tiers a team configures (no default model id exists for any of them).
TIERS = ("primary", "cheap")
TIER_ENV_PREFIX = "AGENTTEAMS_WASM_MODEL_"
CI_ALLOW_ENV = "AGENTTEAMS_ALLOW_LIVE_MODEL_IN_CI"

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_STUB_SCRIPT_BYTES = 64 * 1024
MAX_MESSAGE_CHARS = 300
MAX_TOKENS_RANGE = (1, 1_000_000)
MAX_RETRIES = 2
BACKOFF_CAP_S = 30.0
RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})
USER_AGENT = "agentteams-model-providers/1"

TOOL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
GEMINI_MODEL_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
#: JSON Schema keywords kept when a tool schema goes to Gemini; everything else (``additionalProperties``,
#: ``$schema``, ``default``, ...) is dropped so one tool schema works across families.
GEMINI_SCHEMA_KEYS = frozenset({"type", "properties", "required", "description", "items", "enum"})
_RESERVED_KEYS = frozenset({"_provider_id", "_provider_sig"})
#: Caps on the reserved keys (a forged value must not become a large side channel; P05's event frame is 4 MiB).
MAX_PROVIDER_ID_CHARS = 256
MAX_PROVIDER_SIG_CHARS = 1024 * 1024
_GEMINI_SIG_RE = re.compile(r"[A-Za-z0-9+/=_-]+")


# ---------------------------------------------------------------- types


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


@dataclass(frozen=True)
class Preset:
    """A provider preset: wire family, base URL, where its key comes from, and wire quirks."""

    name: str
    family: str                       # "anthropic" | "openai" | "gemini" | "stub"
    base_url: str | None              # None: the operator must configure one
    key_env: tuple[str, ...]          # first present wins; () means keyless
    max_tokens_param: str = "max_tokens"
    extra_body: Mapping[str, Any] = field(default_factory=dict)


PROVIDERS: dict[str, Preset] = {
    "anthropic": Preset("anthropic", "anthropic", "https://api.anthropic.com/v1", ("ANTHROPIC_API_KEY",)),
    # max_tokens is deprecated in favour of max_completion_tokens (OpenAI reference, verified 2026-10-31).
    "openai": Preset("openai", "openai", "https://api.openai.com/v1", ("OPENAI_API_KEY",), "max_completion_tokens"),
    # usage.include asks OpenRouter for usage.cost; the vulnscan gateway forces the same (gateway_dialects:231).
    "openrouter": Preset("openrouter", "openai", "https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",),
                         extra_body={"usage": {"include": True}}),
    # UNVERIFIED: xAI's Chat Completions tool-call shape matching OpenAI's (docs.x.ai documents Responses only).
    "xai": Preset("xai", "openai", "https://api.x.ai/v1", ("XAI_API_KEY",)),
    # Ollama documents max_tokens (not max_completion_tokens) and tools on /v1/chat/completions.
    "ollama": Preset("ollama", "openai", "http://127.0.0.1:11434/v1", ()),
    # LM Studio's examples assume port 1234; tool support is server-dependent.
    "lmstudio": Preset("lmstudio", "openai", "http://127.0.0.1:1234/v1", ()),
    "openai-compatible": Preset("openai-compatible", "openai", None, ("AGENTTEAMS_MODEL_API_KEY",)),
    # GOOGLE_API_KEY takes precedence when both are set (ai.google.dev/gemini-api/docs/api-key, verified).
    "gemini": Preset("gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta",
                     ("GOOGLE_API_KEY", "GEMINI_API_KEY")),
    "stub": Preset("stub", "stub", None, ()),
}


class Provider:
    """A configured provider: preset, vetted base URL and the key (held privately, never shown)."""

    __slots__ = ("preset", "base_url", "_key", "_stub")

    def __init__(self, preset: Preset, base_url: str | None, key: str, stub: StubProvider | None = None) -> None:
        """Build a provider; use :func:`get_provider` or :meth:`from_key` instead of calling this directly.

        Args:
            preset: The preset.
            base_url: A base URL that already passed :func:`_check_base_url` (``None`` for the stub).
            key: The API key, or ``""`` for a keyless preset.
            stub: The stub backend (``stub`` family only).

        Raises:
            ModelProviderError: ``config`` for a base URL or key that fails the same checks as :func:`get_provider`.
        """
        if base_url is not None:
            base_url = _check_base_url(base_url)
        _check_key_shape(key, preset.name)
        self.preset = preset
        self.base_url = base_url
        self._key = key
        self._stub = stub

    @classmethod
    def from_key(cls, name: str, key: str, *, base_url: str | None = None) -> Provider:
        """Build a provider from a key handed over by the host (P05 Stage 1 receives it over a pipe).

        Args:
            name: Preset name in :data:`PROVIDERS`.
            key: The API key (``""`` only for a keyless preset).
            base_url: Optional override; checked like the preset's own.

        Returns:
            The provider.

        Raises:
            ModelProviderError: ``config`` for an unknown preset, a bad base URL, or a missing/malformed key.
        """
        preset = _preset(name)
        if preset.family == "stub":
            return cls(preset, None, "", StubProvider())
        url = _resolve_base_url(preset, base_url)
        if preset.key_env and not key:
            raise ModelProviderError("config", f"{name}: no API key was handed over", provider=name)
        _check_key_shape(key, name)
        return cls(preset, url, key)

    @property
    def name(self) -> str:
        """The preset name."""
        return self.preset.name

    def __repr__(self) -> str:
        """Return a representation with the key redacted."""
        return f"Provider({self.preset.name!r}, {self.base_url!r}, key=<redacted>)"

    __str__ = __repr__

    def __reduce__(self) -> Any:
        """Refuse pickling: a pickled provider would carry the key out of host memory.

        Raises:
            TypeError: Always.
        """
        raise TypeError("Provider is not picklable (it holds an API key)")

    def __copy__(self) -> Provider:
        """Refuse copying for the same reason as pickling.

        Raises:
            TypeError: Always.
        """
        raise TypeError("Provider is not copyable (it holds an API key)")

    def __deepcopy__(self, memo: dict) -> Provider:
        """Refuse deep copying for the same reason as pickling.

        Raises:
            TypeError: Always.
        """
        raise TypeError("Provider is not copyable (it holds an API key)")


# ---------------------------------------------------------------- configuration


def _preset(name: str) -> Preset:
    preset = PROVIDERS.get(name) if isinstance(name, str) else None
    if preset is None:
        raise ModelProviderError("config", f"unknown provider {name!r}; known: {', '.join(PROVIDERS)}")
    return preset


def _check_base_url(url: str) -> str:
    """Return ``url`` without a trailing slash when it is an allowed base URL.

    Allowed: ``https://`` to any host, or ``http://`` to a loopback host; no userinfo, query or fragment.

    Raises:
        ModelProviderError: ``config`` otherwise.
    """
    if not isinstance(url, str) or any(c in url for c in "\r\n\t ") or not url.isascii():
        raise ModelProviderError("config", "base URL must be a plain ASCII URL")
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("https", "http") or not host:
        raise ModelProviderError("config", "base URL must be https:// (or http:// to a loopback host) with a host")
    if parts.scheme == "http" and host not in LOOPBACK_HOSTS:
        raise ModelProviderError("config", f"plain http is allowed only to a loopback host, not {host!r}")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ModelProviderError("config", "base URL must not carry credentials")
    if parts.query or parts.fragment or "?" in url or "#" in url:
        raise ModelProviderError("config", "base URL must not carry a query or fragment")
    try:
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError as exc:
        raise ModelProviderError("config", "base URL has a malformed port") from exc
    if "%" in parts.path or any(seg in (".", "..") for seg in parts.path.split("/")):
        raise ModelProviderError("config", "base URL path must not contain dot segments or percent-escapes")
    return url.rstrip("/")


def _resolve_base_url(preset: Preset, override: str | None) -> str:
    url = override if override is not None else preset.base_url
    if url is None:
        raise ModelProviderError("config", f"{preset.name}: a base_url must be configured", provider=preset.name)
    return _check_base_url(url)


def _is_loopback(url: str | None) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return host in LOOPBACK_HOSTS


def _check_key_shape(key: str, provider: str) -> None:
    # A key goes into an HTTP header: refuse anything that could split or smuggle one. Never echo it.
    if key and (not key.isascii() or not key.isprintable() or any(c.isspace() for c in key)):
        raise ModelProviderError("config", f"{provider}: the API key contains whitespace or non-printable "
                                 "characters", provider=provider)


def load_key(preset: Preset, environ: Mapping[str, str]) -> str:
    """Return the preset's API key from ``environ`` (the first of ``preset.key_env`` that is set and non-empty).

    Args:
        preset: The preset.
        environ: The environment to read.

    Returns:
        The key, or ``""`` for a keyless preset.

    Raises:
        ModelProviderError: ``config`` naming the variable(s), never a value, when no key is set or it is malformed.
    """
    if not preset.key_env:
        return ""
    for var in preset.key_env:
        value = environ.get(var, "")
        if value.strip():
            key = value.strip()
            _check_key_shape(key, preset.name)
            return key
    raise ModelProviderError("config", f"{preset.name}: no API key; set {' or '.join(preset.key_env)}",
                             provider=preset.name)


def get_provider(name: str, *, environ: Mapping[str, str] | None = None, base_url: str | None = None,
                 script_path: str | Path | None = None) -> Provider:
    """Build a provider from a preset, loading its key from the environment.

    Args:
        name: Preset name in :data:`PROVIDERS`.
        environ: Environment to read the key from (default ``os.environ``).
        base_url: Optional base URL override, vetted like the preset's.
        script_path: ``stub`` only: a JSON list of reply dicts for operator smoke tests (else echo mode).

    Returns:
        The provider.

    Raises:
        ModelProviderError: ``config`` for an unknown preset, ``script_path`` with a non-stub preset, a bad base
            URL, or a missing key; ``protocol`` for an unreadable or malformed stub script.
    """
    preset = _preset(name)
    if preset.family == "stub":
        if base_url is not None:
            raise ModelProviderError("config", "base_url is not valid for the stub provider", provider=name)
        script = _load_stub_script(Path(script_path)) if script_path is not None else None
        return Provider(preset, None, "", StubProvider(script))
    if script_path is not None:
        raise ModelProviderError("config", "script_path is only valid for the stub provider", provider=name)
    url = _resolve_base_url(preset, base_url)
    key = load_key(preset, os.environ if environ is None else environ)
    return Provider(preset, url, key)


def model_for_tier(tier: str, configured: Mapping[str, str | None] | None = None, *,
                   environ: Mapping[str, str] | None = None) -> str:
    """Resolve a tier's model id: ``AGENTTEAMS_WASM_MODEL_<TIER>`` first, then the brief's configuration.

    There is deliberately no fallback: a tier nobody configured is an error, never a built-in model id.

    Args:
        tier: One of :data:`TIERS`.
        configured: ``{tier: model id or None}`` from the brief / rendered runtime file.
        environ: Environment (default ``os.environ``).

    Returns:
        The model id.

    Raises:
        ModelProviderError: ``config`` for an unknown tier or one with no model configured.
    """
    if tier not in TIERS:
        raise ModelProviderError("config", f"unknown model tier {tier!r}; known: {', '.join(TIERS)}")
    env = os.environ if environ is None else environ
    value = (env.get(TIER_ENV_PREFIX + tier.upper()) or "").strip() or ((configured or {}).get(tier) or "").strip()
    if not value:
        raise ModelProviderError("config", f"no model configured for tier {tier} "
                                 f"(set it in the brief or {TIER_ENV_PREFIX}{tier.upper()})")
    return value


# ---------------------------------------------------------------- strict JSON + scrubbing


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


# ---------------------------------------------------------------- request validation


def _text_ok(value: object) -> bool:
    return isinstance(value, str) and "\x00" not in value


def _sig_ok(sig: object) -> bool:
    """A ``_provider_sig`` is a Gemini base64 signature or a JSON list of Anthropic thinking blocks, size-capped."""
    if not isinstance(sig, str) or not sig or len(sig) > MAX_PROVIDER_SIG_CHARS or "\x00" in sig:
        return False
    if sig.startswith("["):
        try:
            _anthropic_opaque(sig)
        except ModelProviderError:
            return False
        return True
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


# ---------------------------------------------------------------- HTTP transport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: a redirect could carry the key header to another host."""

    def redirect_request(self, req: urllib.request.Request, fp: object, code: int, msg: str,
                         headers: object, newurl: str) -> None:
        """Raise instead of following (urllib hook).

        Raises:
            ModelProviderError: ``protocol``, always.
        """
        raise ModelProviderError("protocol", f"refusing HTTP {code} redirect", status=code)


#: Remote presets honour the environment's proxy settings (this environment's egress proxy needs them).
_OPENER = urllib.request.build_opener(_NoRedirect)
#: Loopback presets never go through a proxy.
_LOOPBACK_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))
#: Replaced in tests so backoff does not wait.
_sleep: Callable[[float], None] = time.sleep


@dataclass
class _Response:
    status: int
    headers: Mapping[str, str]
    payload: Any
    attempts: int


def _retry_after(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None else None
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None  # the HTTP-date form is not honoured; the backoff applies instead
    return seconds if seconds is not None and math.isfinite(seconds) and seconds >= 0 else None


def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "not_found"
    if status == 413:
        return "too_large"
    if status == 429:
        return "rate_limited"
    if status in (503, 529):
        return "overloaded"
    if status == 408:
        return "timeout"
    if status >= 500:
        return "server"
    return "bad_request"


_REFUSAL_MARKERS = ("content_filter", "content_policy", "safety", "responsibleai", "prohibited")


def _error_text(raw: bytes) -> str:
    try:
        body = _strict_loads(raw)
    except ValueError:
        return raw.decode("utf-8", errors="replace")
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):  # Anthropic, OpenAI and Google all nest {"error": {..., "message": ...}}
        parts = [str(err.get(k)) for k in ("type", "code", "status") if isinstance(err.get(k), (str, int))]
        return " ".join(parts + [str(err.get("message", ""))]).strip()
    if isinstance(err, str):
        return err
    return raw.decode("utf-8", errors="replace")


def _read_capped(resp: Any, cap: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(min(1 << 16, cap + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > cap:
            raise ModelProviderError("protocol", f"response larger than {cap} bytes (discarded)")


def _post_json(provider: Provider, model: str, body: Mapping[str, Any], timeout: float) -> _Response:
    """POST ``body`` as JSON to the provider's endpoint with bounded retries; return the decoded JSON reply.

    The key lives only in the request headers built here, and they are dropped from this frame's locals before any
    error leaves it, so a traceback rendered with locals does not show the key (the ``Provider`` repr is redacted).

    Raises:
        ModelProviderError: Any transport, HTTP or decoding failure, mapped to :data:`KINDS`, key scrubbed.
    """
    url, auth = _request(provider, model)
    all_headers: dict[str, str] | None = {"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": USER_AGENT, **auth}
    auth = {}
    try:
        return _send(provider, url, all_headers, body, timeout)
    finally:
        all_headers.clear()   # the same dict _send's frame holds, so its locals no longer show the key


def _send(provider: Provider, url: str, all_headers: dict[str, str], body: Mapping[str, Any],
          timeout: float) -> _Response:
    """The retry loop behind :func:`_post_json`; each attempt's request headers are cleared when it ends."""
    name = provider.preset.name
    data = json.dumps(body, allow_nan=False, ensure_ascii=False).encode("utf-8")
    opener = _LOOPBACK_OPENER if _is_loopback(url) else _OPENER
    deadline = time.monotonic() + timeout * (1 + MAX_RETRIES) + BACKOFF_CAP_S
    attempts = 0
    last: ModelProviderError | None = None
    while True:
        attempts += 1
        retry_after: float | None = None
        req: urllib.request.Request | None = None
        try:
            req = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
            try:
                resp = opener.open(req, timeout=timeout)
            except (TimeoutError, socket.timeout) as exc:            # before any response byte: retryable
                raise _Retryable(ModelProviderError("timeout", f"no response within {timeout}s")) from exc
            with resp:
                if resp.geturl() != url:
                    raise ModelProviderError("protocol", "response came from an unexpected URL")
                ctype = resp.headers.get("Content-Type", "")
                try:
                    raw = _read_capped(resp, MAX_RESPONSE_BYTES)
                except (TimeoutError, socket.timeout) as exc:       # mid-body: may be billed, never retried
                    raise ModelProviderError("timeout", "response body timed out") from exc
                except (OSError, http.client.HTTPException) as exc:  # likewise: a reply had started
                    raise ModelProviderError("network", f"response body read failed: {type(exc).__name__}") from exc
                if "json" not in ctype.lower():
                    raise ModelProviderError("protocol", f"response is not JSON (Content-Type {_scrub(ctype)!r})")
                try:
                    payload = _strict_loads(raw)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ModelProviderError("protocol", "response is not valid JSON") from exc
                return _Response(resp.status, dict(resp.headers.items()), payload, attempts)
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = _retry_after(exc.headers)
            try:
                text = _scrub(_error_text(exc.read(MAX_ERROR_BODY_BYTES)), provider._key)
            except (OSError, http.client.HTTPException):
                text = ""
            finally:
                exc.close()
            kind = _status_kind(status)
            if kind == "bad_request" and any(m in text.lower() for m in _REFUSAL_MARKERS):
                kind = "refused"
            err = ModelProviderError(kind, f"HTTP {status}: {text}" if text else f"HTTP {status}", status=status,
                                     provider=name, attempts=attempts, retry_after=retry_after)
            if status not in RETRY_STATUSES:
                raise err from None
            last = err
        except _Retryable as wrapped:
            last = wrapped.error
        except ModelProviderError as exc:
            exc.provider, exc.attempts = name, attempts
            raise
        except (urllib.error.URLError, http.client.HTTPException, ConnectionError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                last = ModelProviderError("timeout", f"no response within {timeout}s")
            else:
                last = ModelProviderError("network", _scrub(f"{type(exc).__name__}: {reason}", provider._key))
        finally:
            if req is not None:     # urllib's frames in a chained traceback hold this same object
                req.headers.clear()
                req.unredirected_hdrs.clear()
        last.provider, last.attempts = name, attempts
        if attempts > MAX_RETRIES:
            raise last
        delay = min(BACKOFF_CAP_S, retry_after if retry_after is not None else 2 ** attempts + random.random())
        if time.monotonic() + delay > deadline:
            raise last
        _sleep(delay)


class _Retryable(Exception):  # noqa: N818 - internal control-flow wrapper
    def __init__(self, error: ModelProviderError) -> None:
        super().__init__(error.message)
        self.error = error


# ---------------------------------------------------------------- tool-id mapping


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


# ---------------------------------------------------------------- Anthropic Messages


#: Anthropic block types returned verbatim on the next request ("pass back every thinking and redacted_thinking
#: block ... exactly as received" with tool use: platform.claude.com/docs/en/api/errors, verified 2026-10-31).
_ANTHROPIC_OPAQUE: dict[str, tuple[str, ...]] = {"thinking": ("thinking", "signature"),
                                                  "redacted_thinking": ("data",)}

_ANTHROPIC_STOPS = {"end_turn": "end_turn", "stop_sequence": "end_turn", "tool_use": "tool_use",
                    "max_tokens": "max_tokens", "model_context_window_exceeded": "max_tokens",
                    "refusal": "refusal"}


def _encode_anthropic(model: str, messages: Sequence[Mapping[str, Any]], system: str | None,
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


def _anthropic_opaque(sig: object) -> list[dict[str, Any]]:
    """The thinking blocks carried in ``_provider_sig`` (a Gemini signature yields none).

    Raises:
        ModelProviderError: ``bad_request`` when a JSON-list signature is not exactly a list of thinking blocks
            with their documented keys (a forged value is refused, not forwarded).
    """
    if not isinstance(sig, str) or not sig.startswith("["):
        return []
    try:
        blocks = _strict_loads(sig)
    except ValueError:
        blocks = None
    if not isinstance(blocks, list) or not blocks or any(_opaque_shape(b) != b for b in blocks):
        raise ModelProviderError("bad_request", "_provider_sig is not a list of thinking blocks")
    return blocks


def _decode_anthropic(payload: Any, headers: Mapping[str, str], turn: int, preset: Preset,
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


# ---------------------------------------------------------------- OpenAI-compatible Chat Completions


_OPENAI_STOPS = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use",
                 "length": "max_tokens", "content_filter": "refusal"}


def _encode_openai(model: str, messages: Sequence[Mapping[str, Any]], system: str | None,
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


def _decode_openai(payload: Any, headers: Mapping[str, str], turn: int, preset: Preset,
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


# ---------------------------------------------------------------- Gemini generateContent


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


def _encode_gemini(model: str, messages: Sequence[Mapping[str, Any]], system: str | None,
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


def _decode_gemini(payload: Any, headers: Mapping[str, str], turn: int, preset: Preset,
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


# ---------------------------------------------------------------- shared decode helpers


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


# ---------------------------------------------------------------- stub provider


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
    if not isinstance(usage, dict) or set(usage) - {"input_tokens", "output_tokens", "cost"}:
        raise ModelProviderError("protocol", f"{where}: usage is {{input_tokens?, output_tokens?, cost?}}")
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


# ---------------------------------------------------------------- entry point


_FAMILIES: dict[str, tuple[Callable[..., dict[str, Any]], Callable[..., ModelReply]]] = {
    "anthropic": (_encode_anthropic, _decode_anthropic),
    "openai": (_encode_openai, _decode_openai),
    "gemini": (_encode_gemini, _decode_gemini),
}


def _request(provider: Provider, model: str) -> tuple[str, dict[str, str]]:
    preset, base, key = provider.preset, provider.base_url, provider._key
    if preset.family == "anthropic":
        return f"{base}/messages", {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    if preset.family == "openai":
        return f"{base}/chat/completions", ({"Authorization": f"Bearer {key}"} if key else {})
    # Gemini: the key goes in the x-goog-api-key header, never the ?key= query (docs/api-key, verified).
    return f"{base}/models/{_gemini_model_path(model)}:generateContent", {"x-goog-api-key": key}


def complete(provider: Provider, model: str, messages: Sequence[Mapping[str, Any]], *, system: str | None = None,
             tools: Sequence[ToolSpec] = (), max_tokens: int, timeout: float,
             temperature: float | None = None) -> ModelReply:
    """Run one model turn and return the normalised reply.

    Stateless: everything the provider needs (including its own tool-call ids, via ``_provider_id``) is derived
    from ``messages``. A ``refusal`` stop returns normally; the caller decides what to do with it.

    Args:
        provider: From :func:`get_provider` or :meth:`Provider.from_key`.
        model: The provider's model id (required; there is no default). See :func:`model_for_tier`.
        messages: Neutral messages (Anthropic block shape), alternating and starting with ``user``.
        system: System prompt.
        tools: Tools the model may call.
        max_tokens: Output ceiling, 1..1,000,000.
        timeout: Per-attempt socket timeout in seconds (the caller caps it by its remaining budget).
        temperature: Optional sampling temperature in [0, 2].

    Returns:
        The reply.

    Raises:
        ModelProviderError: ``config`` / ``bad_request`` before any I/O; otherwise the mapped transport, HTTP or
            protocol failure, with ``attempts`` counting every (possibly billed) HTTP attempt.
    """
    if not isinstance(provider, Provider):
        raise ModelProviderError("config", "provider must be a Provider (see get_provider)")
    tools = tuple(tools)
    _check_request(messages, system, tools, max_tokens, model, temperature)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ModelProviderError("bad_request", "timeout must be a number of seconds in (0, 3600]")
    preset = provider.preset
    if preset.family == "stub":
        assert provider._stub is not None  # noqa: S101 - construction invariant
        return provider._stub.reply(model, messages, system, tools, max_tokens)
    # Loopback is not exempt: a local relay (e.g. a proxy on 127.0.0.1) can still spend on a paid upstream.
    if os.environ.get("CI", "").strip() and os.environ.get(CI_ALLOW_ENV, "") != "1":
        raise ModelProviderError("config", f"refusing a live model call under CI; set {CI_ALLOW_ENV}=1 to allow",
                                 provider=preset.name)
    encode, decode = _FAMILIES[preset.family]
    body = encode(model, messages, system, tools, max_tokens, temperature, preset)
    resp = _post_json(provider, model, body, float(timeout))
    try:
        return decode(resp.payload, resp.headers, len(messages), preset, model)
    except ModelProviderError as exc:
        exc.provider, exc.attempts = preset.name, resp.attempts
        exc.rescrub(provider._key)
        raise
