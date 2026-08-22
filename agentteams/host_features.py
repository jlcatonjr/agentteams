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
        "goose",
        "codex",
        "bridge:copilot-vscode-to-claude",
        "bridge:copilot-vscode-to-copilot-cli",
        "bridge:copilot-cli-to-claude",
        "bridge:copilot-vscode-to-goose",
        "bridge:claude-to-goose",
        "bridge:copilot-cli-to-goose",
    }
)

_KNOWN_FEATURES: dict[str, frozenset[str]] = {
    "claude": frozenset({"hooks", "subagents", "schedule", "mcp", "critic", "cache-split", "todo-projection", "parallelize", "sandbox"}),
    "copilot-vscode": frozenset({"chat-modes", "inline-yaml-handoffs"}),
    "copilot-cli": frozenset({"manifest-routing"}),
    # goose: only `mcp` so far — wires operator-specified mcp_servers[] into recipes
    # as opt-in extensions (Goose already grants CLI via the `developer` builtin, so
    # this is never a default). The `goose` namespace lands here ahead of the goose
    # bridge phase (goose-integration.plan §5); bridge `goose:` tokens are still owed.
    "goose": frozenset({"mcp"}),
    # codex: only `mcp` so far — wires operator-specified mcp_servers[] into
    # .codex/config.toml (codex_mcp_emit.py). That module has always parsed the
    # literal "codex:mcp" token itself, but the token could never reach it: CLI
    # parsing validates against this table first (see host-features.md audit,
    # api-doc-conformity-sweep, 2026-08-14) — this entry was simply never added
    # when codex support landed elsewhere, so the token was rejected before emission.
    "codex": frozenset({"mcp"}),
    "bridge:copilot-vscode-to-claude": frozenset(
        {"subagents", "hooks", "schedule", "mcp", "critic", "cache-split", "todo-projection", "parallelize", "sandbox"}
    ),
    "bridge:copilot-vscode-to-copilot-cli": frozenset({"manifest-routing"}),
    "bridge:copilot-cli-to-claude": frozenset({"subagents", "hooks", "sandbox"}),
    # goose-target bridges: `mcp` wires selected MCP servers into the emitted
    # bridge-orchestrator recipe (opt-in). `subagents` additionally emits one thin
    # stub recipe per source agent into .goose/recipes/ (pointers to the canonical
    # source; opt-in, default off). The `developer` (CLI) extension is always
    # emitted by the bridge recipe regardless of these tokens.
    "bridge:copilot-vscode-to-goose": frozenset({"mcp", "subagents"}),
    "bridge:claude-to-goose": frozenset({"mcp", "subagents"}),
    "bridge:copilot-cli-to-goose": frozenset({"mcp", "subagents"}),
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


#: Host-feature tokens each privilege_profile expands to. ``cooperative`` expands to
#: nothing (today's behavior, no OS boundary). ``confined`` and ``exclusive`` both request
#: the Claude sandbox (same token). They diverge at EMISSION, not here: ``exclusive`` adds
#: OS read-exclusion (``denyRead`` of protected paths — P3a) in ``claude._build_sandbox_block``
#: and triggers the P3b inbound-hardening advisory. The read-exclusion seals THIS team's
#: reads (outbound); the inbound "others can't read my tree" property is operator filesystem
#: hardening, which agentteams advises but does not enforce.
_PROFILE_FEATURE_TOKENS: dict[str, tuple[str, ...]] = {
    "cooperative": (),
    "confined": ("claude:sandbox",),
    "exclusive": ("claude:sandbox",),
}


#: The privilege_profile values the schema accepts. ``None`` is not in the set because a
#: missing profile is not a typo — it defaults to ``cooperative`` (see
#: :func:`validate_privilege_profile`). Any OTHER unrecognized value IS a typo and must
#: fail closed (CC-6): silently downgrading ``"exclusve"`` to unconfined looks like the
#: operator requested confinement while granting none.
VALID_PRIVILEGE_PROFILES: frozenset[str] = frozenset(_PROFILE_FEATURE_TOKENS)


def validate_privilege_profile(profile: str | None) -> str:
    """Return the normalized privilege_profile, hard-erroring on an unknown value (CC-6).

    Args:
        profile: The requested profile, or ``None``. ``None`` and ``""`` normalize to
            ``"cooperative"`` (a missing profile is a default, not a mistake). Any other
            value not in :data:`VALID_PRIVILEGE_PROFILES` raises.

    Returns:
        The validated profile string (one of :data:`VALID_PRIVILEGE_PROFILES`).

    Raises:
        ValueError: ``profile`` is a non-empty value that is not a recognized profile.
    """
    normalized = profile or "cooperative"
    if normalized not in VALID_PRIVILEGE_PROFILES:
        allowed = ", ".join(sorted(VALID_PRIVILEGE_PROFILES))
        raise ValueError(
            f"unknown privilege_profile {profile!r}; expected one of: {allowed}. "
            "Refusing to silently downgrade an unrecognized profile to unconfined."
        )
    return normalized


def expand_privilege_profile(profile: str | None) -> list[str]:
    """Return the host-feature tokens a privilege_profile implies.

    Args:
        profile: One of ``cooperative`` (or ``None`` → treated as cooperative),
            ``confined``, ``exclusive``. An unknown value expands to ``[]``. Callers that
            parse operator input should first run :func:`validate_privilege_profile` so an
            unrecognized profile fails closed rather than silently expanding to nothing.

    Returns:
        The list of ``<ns>:<feature>`` tokens to union into the active feature set.
    """
    return list(_PROFILE_FEATURE_TOKENS.get(profile or "cooperative", ()))


#: Frameworks for which agentteams can emit OS-enforced write-confinement today.
#: Only Claude Code exposes a native sandbox (Seatbelt/bubblewrap) that agentteams
#: configures; every other target degrades a confined/exclusive profile to advisory.
SANDBOX_CAPABLE_FRAMEWORKS: frozenset[str] = frozenset({"claude"})


def privilege_profile_advisory(
    profile: str | None,
    framework_id: str,
    host_features: Iterable[str] | None = None,
) -> dict[str, str] | None:
    """Return an advisory dict when confinement is requested but unenforceable on this host.

    Confinement has OS-level enforcement only on a sandbox-capable framework (Claude
    Code). A request made either way — via ``privilege_profile`` confined/exclusive OR
    a directly-passed ``claude:sandbox`` host-feature token — on any other target emits
    no boundary, so it is advisory only: a state that must be surfaced, never silent.

    Args:
        profile: The active ``privilege_profile`` (``cooperative``/``confined``/``exclusive``).
        framework_id: The target framework id (e.g. ``claude``, ``goose``).
        host_features: The effective host-feature tokens; a direct ``claude:sandbox``
            token here also counts as a confinement request. ``None`` is treated as empty.

    Returns:
        An advisory ``{"code", "message"}`` dict when confinement is requested that the
        target cannot enforce, else ``None``.
    """
    requested = profile in {"confined", "exclusive"} or "claude:sandbox" in (host_features or [])
    if requested and framework_id not in SANDBOX_CAPABLE_FRAMEWORKS:
        how = f"privilege_profile={profile!r}" if profile in {"confined", "exclusive"} else "claude:sandbox"
        return {
            "code": "privilege-profile-unenforced-host",
            "message": (
                f"{how} requests workspace write-confinement, but the {framework_id!r} "
                "framework has no OS-level sandbox that agentteams can configure. No "
                "enforcement is emitted for this target — the request is ADVISORY ONLY "
                "here. OS-enforced confinement is currently available only on the 'claude' "
                "framework (Claude Code sandbox)."
            ),
        }
    return None


def merge_profile_features(features: Iterable[str], profile: str | None) -> list[str]:
    """Union explicit feature tokens with a privilege_profile's expansion.

    Order-preserving and idempotent: explicit tokens keep their position and a
    profile-implied token already present is not duplicated. ``cooperative`` adds
    nothing and therefore never strips an explicitly requested ``claude:sandbox``.

    Args:
        features: The explicitly-selected tokens (e.g. from --target-host-features).
        profile: The privilege_profile whose expansion is unioned in.

    Returns:
        The merged, deduped token list.
    """
    merged = list(features)
    for tok in expand_privilege_profile(profile):
        if tok not in merged:
            merged.append(tok)
    return merged


__all__ = [
    "HostFeatureError",
    "SANDBOX_CAPABLE_FRAMEWORKS",
    "expand_privilege_profile",
    "is_enabled",
    "merge_profile_features",
    "parse_tokens",
    "privilege_profile_advisory",
    "validate",
]
