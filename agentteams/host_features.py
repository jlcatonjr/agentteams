"""Host-feature subselectors for per-target emission gating.

Subselectors are namespaced as ``<ns>:<feature>`` tokens, where ``ns`` is one
of ``claude``, ``copilot-vscode``, ``copilot-cli``, or
``bridge:<source>-to-<target>``. Default emission is unchanged when no
features are selected — every feature gate is opt-in.

The parsed feature set lives on the manifest as ``host_features`` (list of
strings). Downstream emitters call :func:`is_enabled` to decide whether to
emit optional artifacts (settings.json hooks, schedule routines, MCP config,
CSV<->Todo projection, etc.). This module is pure and dependency-free.
"""

from __future__ import annotations

from typing import Iterable

_VALID_NAMESPACES = frozenset(
    {
        "claude",
        "copilot-vscode",
        "copilot-cli",
        "bridge:copilot-vscode-to-claude",
        "bridge:copilot-vscode-to-copilot-cli",
        "bridge:copilot-cli-to-claude",
    }
)

_KNOWN_FEATURES: dict[str, frozenset[str]] = {
    "claude": frozenset({"hooks", "subagents", "schedule", "mcp", "critic", "cache-split", "todo-projection"}),
    "copilot-vscode": frozenset({"chat-modes", "inline-yaml-handoffs"}),
    "copilot-cli": frozenset({"manifest-routing"}),
    "bridge:copilot-vscode-to-claude": frozenset(
        {"subagents", "hooks", "schedule", "mcp", "critic", "cache-split", "todo-projection"}
    ),
    "bridge:copilot-vscode-to-copilot-cli": frozenset({"manifest-routing"}),
    "bridge:copilot-cli-to-claude": frozenset({"subagents", "hooks"}),
}


class HostFeatureError(ValueError):
    """Raised when a subselector token is malformed or unknown."""


def parse_tokens(raw: str | None) -> list[str]:
    """Parse a CSV string of subselectors into a normalized, deduped list.

    Empty / None input returns ``[]`` (default emission).
    Unknown namespaces or features raise :class:`HostFeatureError`.
    """
    if not raw:
        return []
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_tok in raw.split(","):
        tok = raw_tok.strip()
        if not tok:
            continue
        validate(tok)
        if tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
    return tokens


def validate(token: str) -> None:
    """Validate a single ``<ns>:<feature>`` token; raise on error."""
    # Namespace may itself contain a colon (bridge:src-to-tgt), so split on
    # the last colon to separate feature from namespace.
    if ":" not in token:
        raise HostFeatureError(
            f"host feature token {token!r} must be of the form <namespace>:<feature>"
        )
    ns, feature = token.rsplit(":", 1)
    if ns not in _VALID_NAMESPACES:
        valid = ", ".join(sorted(_VALID_NAMESPACES))
        raise HostFeatureError(
            f"unknown host-feature namespace {ns!r} in token {token!r}; valid: {valid}"
        )
    known = _KNOWN_FEATURES.get(ns, frozenset())
    if feature not in known:
        valid = ", ".join(sorted(known)) or "(none defined)"
        raise HostFeatureError(
            f"unknown feature {feature!r} for namespace {ns!r}; valid: {valid}"
        )


def is_enabled(features: Iterable[str], namespace: str, feature: str) -> bool:
    """Return True iff ``<namespace>:<feature>`` is in the active set."""
    target = f"{namespace}:{feature}"
    return target in set(features)


__all__ = [
    "HostFeatureError",
    "is_enabled",
    "parse_tokens",
    "validate",
]
