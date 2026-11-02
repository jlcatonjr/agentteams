"""Provider presets, key custody and model-tier resolution for :mod:`agentteams.model_providers`."""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.model_providers._common import (
    LOOPBACK_HOSTS, TIER_ENV_PREFIX, TIERS, ModelProviderError, _is_loopback,
)
from agentteams.model_providers._stub import StubProvider, _load_stub_script

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
            base_url: A base URL that already passed :func:`check_base_url` (``None`` for the stub).
            key: The API key, or ``""`` for a keyless preset.
            stub: The stub backend (``stub`` family only).

        Raises:
            ModelProviderError: ``config`` for a base URL or key that fails the same checks as :func:`get_provider`.
        """
        if base_url is not None:
            base_url = check_base_url(base_url)
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
        """The preset name.

        Returns:
            ``self.preset.name``.
        """
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



def _preset(name: str) -> Preset:
    preset = PROVIDERS.get(name) if isinstance(name, str) else None
    if preset is None:
        raise ModelProviderError("config", f"unknown provider {name!r}; known: {', '.join(PROVIDERS)}")
    return preset



def check_base_url(url: str) -> str:
    """Return ``url`` without a trailing slash when it is an allowed base URL.

    Allowed: ``https://`` to any host, or ``http://`` to a loopback host; no userinfo, query or fragment.

    Rule (C-4, @security S5): a base URL comes only from operator configuration — a preset, the operator's
    environment, or an explicit operator argument. It is never taken from read content (a brief field an agent
    can write, guest or model output, a fetched page, an adjacent repository): the provider key is sent to this
    host, so a content-chosen URL would exfiltrate it. These checks bound the URL's shape, not its trust. The one
    caller outside ``model_providers`` is the ``--run-wasm-agent`` launcher (``cli/wasm_agent_commands.py`` via
    ``wasm_agent.team.resolve_base_url``): its operator path is ``AGENTTEAMS_WASM_BASE_URL`` or
    ``--wasm-base-url``, read at run time. The brief (``wasm_runtime.base_url``) may name one only for a keyless
    preset (:func:`preset_needs_key` False) on a loopback host, so no key can follow it; the launcher re-applies
    that rule to the run's effective preset (``--wasm-provider``).

    Args:
        url: The candidate base URL.

    Returns:
        The URL without a trailing slash.

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



def is_loopback_url(url: str | None) -> bool:
    """Return whether *url*'s host is a loopback host (``127.0.0.1``, ``::1``, ``localhost``).

    Args:
        url: A URL, or None.

    Returns:
        True for a loopback host.

    Raises:
        Nothing.
    """
    return _is_loopback(url)


def preset_needs_key(name: str) -> bool:
    """Return whether the preset *name* sends an API key (its ``key_env`` is not empty).

    Args:
        name: A preset name in :data:`PROVIDERS`.

    Returns:
        True when a key goes to the preset's base URL; False for a keyless preset (``ollama``, ``lmstudio``,
        ``stub``).

    Raises:
        ModelProviderError: ``config`` for an unknown preset.
    """
    return bool(_preset(name).key_env)



def _resolve_base_url(preset: Preset, override: str | None) -> str:
    url = override if override is not None else preset.base_url
    if url is None:
        raise ModelProviderError("config", f"{preset.name}: a base_url must be configured", provider=preset.name)
    return check_base_url(url)



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
