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

import os
from collections.abc import Mapping, Sequence
from types import ModuleType
from typing import Any

from agentteams.model_providers import _anthropic, _gemini, _openai
from agentteams.model_providers._anthropic import ANTHROPIC_VERSION
from agentteams.model_providers._common import (
    CI_ALLOW_ENV, KINDS, MAX_PROVIDER_ID_CHARS, MAX_PROVIDER_SIG_CHARS, STOPS, TIER_ENV_PREFIX, TIERS,
    ModelProviderError, ModelReply, ToolSpec, Usage,
)
from agentteams.model_providers._config import (
    PROVIDERS, Preset, Provider, check_base_url, get_provider, is_loopback_url, load_key, model_for_tier,
    preset_needs_key,
)
from agentteams.model_providers._stub import StubProvider
from agentteams.model_providers._transport import BACKOFF_CAP_S, MAX_RESPONSE_BYTES, MAX_RETRIES, _post_json
from agentteams.model_providers._validate import _check_request

__all__ = [
    "ANTHROPIC_VERSION", "BACKOFF_CAP_S", "CI_ALLOW_ENV", "KINDS", "MAX_PROVIDER_ID_CHARS", "MAX_PROVIDER_SIG_CHARS",
    "MAX_RESPONSE_BYTES", "MAX_RETRIES", "PROVIDERS", "STOPS", "TIERS", "TIER_ENV_PREFIX", "ModelProviderError",
    "ModelReply", "Preset", "Provider", "StubProvider", "ToolSpec", "Usage", "check_base_url", "complete",
    "get_provider", "is_loopback_url", "load_key", "model_for_tier", "preset_needs_key",
]

#: Wire family -> module providing ``endpoint(provider, model)``, ``encode(...)`` and ``decode(...)``.
_FAMILIES: dict[str, ModuleType] = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}


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
    # Read at call time from the process environment (not get_provider's ``environ``): the gate is about where this
    # process runs. Loopback is not exempt: a local relay (e.g. a proxy on 127.0.0.1) can still spend upstream.
    if os.environ.get("CI", "").strip() and os.environ.get(CI_ALLOW_ENV, "") != "1":
        raise ModelProviderError("config", f"refusing a live model call under CI; set {CI_ALLOW_ENV}=1 to allow",
                                 provider=preset.name)
    family = _FAMILIES[preset.family]
    body = family.encode(model, messages, system, tools, max_tokens, temperature, preset)
    url, auth = family.endpoint(provider, model)
    resp = _post_json(provider, url, auth, body, float(timeout))   # clears ``auth`` before returning or raising
    try:
        return family.decode(resp.payload, resp.headers, len(messages), preset, model)
    except ModelProviderError as exc:
        exc.provider, exc.attempts = preset.name, resp.attempts
        exc.rescrub(provider._key)
        raise
