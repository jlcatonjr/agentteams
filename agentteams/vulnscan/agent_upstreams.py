"""The gateway's upstream wiring per runtime dialect, and the ``agent.*`` keys it reads (split from agent_run).

A driver's dialect (:attr:`agentteams.vulnscan.agent_drivers.Driver.dialect`) decides which gateway route
serves the agent and where the gateway forwards it (plan design 2, D2: pass-through, never translated):

| Dialect | Gateway flags | Upstream | Key file (mounted into the gateway only) |
|---|---|---|---|
| ``chat`` (goose) | ``--model --key-file --upstream`` (unchanged) | ``agent.upstream`` | ``agent.key_file`` |
| ``messages`` (claude) | ``--dialects messages --upstream-messages --key-file-messages --max-output-tokens --anthropic-betas`` (+ ``--models``, ``--prices``) | ``agent.upstream_messages`` (default ``https://openrouter.ai/api/v1``) | ``agent.key_file_messages``; else ``~/.config/agentteams-secrets/anthropic.env`` for ``api.anthropic.com``, else ``agent.key_file`` |

Keys read here (absent keys take :data:`DEFAULTS`): ``upstream_messages``, ``key_file_messages``,
``models`` (further allowed model ids; the gateway always allows ``agent.model``), ``prices`` (a price
table file, :func:`agentteams.vulnscan.gateway_dialects.load_prices`), ``anthropic_betas`` (``anthropic-beta``
values the Messages route allows; None: :data:`DEFAULT_ANTHROPIC_BETAS`), ``max_output_tokens`` (the
gateway's output ceiling, and the CLI's own output setting, e.g. ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``) and
``max_turns`` (the CLI's turn cap where it has one).

Revision 13 (D2): a Messages-dialect runtime runs Anthropic models only, so ``agent.model`` (and every
``agent.models`` entry) must be a Claude model id (``claude-...`` or ``anthropic/...``). ``api.anthropic.com``
reports no ``usage.cost``, so it needs ``agent.prices`` (or ``agent.allow_unmetered``): without either, every
reply would count as unmetered and the run would stop after ``agent.max_unmetered_calls`` replies.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

SECRETS_DIR = "~/.config/agentteams-secrets"
DEFAULT_UPSTREAM_MESSAGES = "https://openrouter.ai/api/v1"
MAX_OUTPUT_TOKENS_CEILING = 128_000
MAX_TURNS_CEILING = 1000
#: ``anthropic-beta`` values Claude Code still sends with ``CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1``
#: (header-only capabilities: interleaved thinking, effort, extended context; no server tool rides on a
#: beta value alone, and the Messages route refuses server tools and unknown body keys regardless). The
#: operator widens or narrows the list with ``agent.anthropic_betas``; the acceptance script prints any
#: value the gateway refused.
DEFAULT_ANTHROPIC_BETAS = ("claude-code-20250219", "interleaved-thinking-2025-05-14", "effort-2025-11-24",
                           "context-1m-2025-08-07")
DEFAULTS: dict[str, Any] = {"upstream_messages": "", "key_file_messages": "", "models": [], "prices": "",
                            "anthropic_betas": None, "max_output_tokens": 32_000, "max_turns": 100}
#: In-gateway paths of the mounted files.
CHAT_KEY_IN_GW = "/run/secrets/openrouter.env"
MESSAGES_KEY_IN_GW = "/run/secrets/messages.env"
PRICES_IN_GW = "/run/secrets/prices.json"
_BETA_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def _get(a: dict[str, Any], key: str) -> Any:
    return a.get(key, DEFAULTS[key])


def upstream_messages(a: dict[str, Any]) -> str:
    """Return the Messages upstream base URL (``agent.upstream_messages``, default OpenRouter's)."""
    return str(_get(a, "upstream_messages") or DEFAULT_UPSTREAM_MESSAGES)


def _host(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def is_anthropic_api(url: str) -> bool:
    """Return whether ``url`` is Anthropic's own API (``api.anthropic.com``)."""
    return _host(url) == "api.anthropic.com"


def messages_key_file(a: dict[str, Any]) -> Path:
    """Return the host key file for the Messages upstream (see the module table)."""
    explicit = str(_get(a, "key_file_messages") or "")
    if explicit:
        return Path(explicit).expanduser()
    if is_anthropic_api(upstream_messages(a)):
        return Path(SECRETS_DIR).expanduser() / "anthropic.env"
    return Path(str(a.get("key_file") or "")).expanduser()


def max_output_tokens(a: dict[str, Any]) -> int:
    """Return ``agent.max_output_tokens`` (validated by :func:`validate`)."""
    return int(_get(a, "max_output_tokens"))


def max_turns(a: dict[str, Any]) -> int:
    """Return ``agent.max_turns`` (validated by :func:`validate`)."""
    return int(_get(a, "max_turns"))


def anthropic_betas(a: dict[str, Any]) -> list[str]:
    """Return the ``anthropic-beta`` values the Messages route allows."""
    betas = _get(a, "anthropic_betas")
    return list(DEFAULT_ANTHROPIC_BETAS if betas is None else betas)


def secret_paths(a: dict[str, Any]) -> tuple[str, ...]:
    """Return every host file the gateway mounts (key files, the price table) for the team-dir check."""
    paths = [str(a.get("key_file") or "")]
    if str(_get(a, "key_file_messages") or "") or is_anthropic_api(upstream_messages(a)):
        paths.append(str(messages_key_file(a)))
    if _get(a, "prices"):
        paths.append(str(_get(a, "prices")))
    return tuple(p for p in paths if p)


def gateway_routes(a: dict[str, Any], dialect: str) -> tuple[list[str], list[str]]:
    """Return ``(docker mounts, gateway flags)`` that wire the gateway for one dialect.

    Args:
        a: Agent settings.
        dialect: The runtime driver's dialect.

    Returns:
        The ``-v`` mounts (each key file and the price table, read-only) and the gateway's route, model,
        key and ceiling flags. For ``chat`` both are exactly what the gateway argv held before drivers
        existed (golden-tested).

    Raises:
        ValueError: An unknown dialect (``responses`` has no driver yet).
    """
    model = str(a["model"])
    if dialect == "chat":
        key = Path(str(a["key_file"])).expanduser()
        return (["-v", f"{key}:{CHAT_KEY_IN_GW}:ro"],
                ["--model", model, "--key-file", CHAT_KEY_IN_GW, "--upstream", str(a["upstream"])])
    if dialect != "messages":
        raise ValueError(f"no gateway wiring for dialect {dialect!r}")
    mounts = ["-v", f"{messages_key_file(a)}:{MESSAGES_KEY_IN_GW}:ro"]
    extra = [m for m in (_get(a, "models") or []) if m != model]
    flags = ["--model", model, *(["--models", ",".join(extra)] if extra else []),
             "--dialects", "messages", "--upstream-messages", upstream_messages(a),
             "--key-file-messages", MESSAGES_KEY_IN_GW, "--max-output-tokens", str(max_output_tokens(a)),
             "--anthropic-betas", ",".join(anthropic_betas(a))]
    prices = str(_get(a, "prices") or "")
    if prices:
        mounts += ["-v", f"{Path(prices).expanduser()}:{PRICES_IN_GW}:ro"]
        flags += ["--prices", PRICES_IN_GW]
    return mounts, flags


def is_claude_model(model: object) -> bool:
    """Return whether ``model`` names an Anthropic model (``claude-...`` or OpenRouter's ``anthropic/...``)."""
    m = str(model).strip().lower()
    return m.startswith(("claude-", "anthropic/"))


def _int_in(a: dict[str, Any], key: str, ceiling: int) -> None:
    v = _get(a, key)
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= ceiling:
        raise ValueError(f"agent.{key} must be an integer 1..{ceiling} (got {repr(v)[:80]})")


def validate(a: dict[str, Any], dialect: str) -> None:
    """Check the keys this module reads, and the dialect's model rule (called by ``validate_agent``).

    Args:
        a: The merged agent settings.
        dialect: The runtime driver's dialect.

    Raises:
        ValueError: A value has the wrong type or range; the message names ``agent.<key>``.
    """
    for key in ("upstream_messages", "key_file_messages", "prices"):
        if not isinstance(_get(a, key), str):
            raise ValueError(f"agent.{key} must be a string (got {repr(_get(a, key))[:80]})")  # noqa: TRY004
    up = _get(a, "upstream_messages")
    parts = urllib.parse.urlsplit(up) if up else None
    if parts is not None and (parts.scheme not in ("http", "https") or not parts.hostname):
        raise ValueError(f"agent.upstream_messages must be an http:// or https:// URL with a host (got {up[:80]!r})")
    models = _get(a, "models")
    if not isinstance(models, list) or not all(isinstance(m, str) and m.strip() and "," not in m for m in models):
        raise ValueError(f"agent.models must be a list of model ids (got {repr(models)[:80]})")
    betas = _get(a, "anthropic_betas")
    if betas is not None and (not isinstance(betas, list)
                              or not all(isinstance(b, str) and _BETA_RE.fullmatch(b) for b in betas)):
        raise ValueError(f"agent.anthropic_betas must be null or a list of beta names (got {repr(betas)[:80]})")
    _int_in(a, "max_output_tokens", MAX_OUTPUT_TOKENS_CEILING)
    _int_in(a, "max_turns", MAX_TURNS_CEILING)
    if dialect != "messages":
        return
    bad = [m for m in (a.get("model"), *models) if not is_claude_model(m)]
    if bad:
        raise ValueError(f"agent.framework {a.get('framework')} runs Anthropic models only (revision 13): "
                         f"agent.model / agent.models must be claude-... or anthropic/... ids (got {bad[0]!r:.80})")
    if is_anthropic_api(upstream_messages(a)) and not _get(a, "prices") and not a.get("allow_unmetered"):
        raise ValueError("agent.prices is required when agent.upstream_messages is api.anthropic.com: it reports "
                         "no cost, so without a price table every reply is unmetered (or set allow_unmetered)")
