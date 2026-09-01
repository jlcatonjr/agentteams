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

import sys
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
    # goose: `mcp` wires operator-specified mcp_servers[] into recipes as opt-in
    # extensions (Goose already grants CLI via the `developer` builtin, so this is never
    # a default). `sandbox` (P1-1, 2026-08-27) gates the macOS Seatbelt confinement
    # emission (frameworks/_goose_sandbox_emit.py): confined/exclusive expand to
    # `goose:sandbox` (see _sandbox_token_for). The token is platform-independent — it
    # records the confinement REQUEST — but ENFORCEMENT (emitting sandbox.sb + the
    # GOOSE_SANDBOX config example) fires only on macOS; on Linux/Windows the request
    # degrades to the privilege_profile_advisory (honest fail-closed), since Goose has
    # no native OS sandbox there. The `goose` namespace lands here ahead of the goose
    # bridge phase (goose-integration.plan §5); bridge `goose:` tokens are still owed.
    "goose": frozenset({"mcp", "sandbox"}),
    # codex: only `mcp` so far — wires operator-specified mcp_servers[] into
    # .codex/config.toml (codex_mcp_emit.py). That module has always parsed the
    # literal "codex:mcp" token itself, but the token could never reach it: CLI
    # parsing validates against this table first (see host-features.md audit,
    # api-doc-conformity-sweep, 2026-08-14) — this entry was simply never added
    # when codex support landed elsewhere, so the token was rejected before emission.
    "codex": frozenset({"mcp"}),
    # C-4 (2026-08-26): `sandbox` removed from the bridge namespaces. The bridge writes only
    # subagent stubs into a foreign repo and never emits a `sandbox` settings block (bridge.py
    # wires subagents/hooks/schedule/cache-split/… but not sandbox), so accepting a
    # `bridge:…:sandbox` token gave the operator a *validating* token that confined nothing —
    # a silent false-confinement signal. Privilege scoping is a native, workspace-scoped
    # emission and an explicit non-goal of a bridge (bridge-refresh-safety.md); the token is
    # dropped so requesting bridge confinement now fails loudly instead of no-op'ing.
    "bridge:copilot-vscode-to-claude": frozenset(
        {"subagents", "hooks", "schedule", "mcp", "critic", "cache-split", "todo-projection", "parallelize"}
    ),
    "bridge:copilot-vscode-to-copilot-cli": frozenset({"manifest-routing"}),
    "bridge:copilot-cli-to-claude": frozenset({"subagents", "hooks"}),
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


def _sandbox_token_for(framework: str | None) -> str:
    """Return the sandbox host-feature token a confined/exclusive profile expands to.

    The ``_PROFILE_FEATURE_TOKENS`` table carries the ``claude:sandbox`` token because
    Claude Code was the first (and long the only) framework with an agentteams-configured
    OS sandbox. P1-1 makes the expansion framework-aware: ``goose`` maps to its own
    ``goose:sandbox`` token (macOS Seatbelt via ``GOOSE_SANDBOX``, emitted by
    ``frameworks/_goose_sandbox_emit.py``). Every other framework (and a missing
    framework) keeps ``claude:sandbox`` — preserving the historical framework-agnostic
    behavior: the token is harmless on a framework whose emitter never reads it, and the
    unenforceable-host request is surfaced by :func:`privilege_profile_advisory` either
    way. Note this is platform-INDEPENDENT: the token records the confinement REQUEST;
    :func:`is_sandbox_capable` decides whether the current OS can ENFORCE it.
    """
    return "goose:sandbox" if framework == "goose" else "claude:sandbox"


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


def expand_privilege_profile(profile: str | None, framework: str | None = None) -> list[str]:
    """Return the host-feature tokens a privilege_profile implies.

    Args:
        profile: One of ``cooperative`` (or ``None`` → treated as cooperative),
            ``confined``, ``exclusive``. An unknown value expands to ``[]``. Callers that
            parse operator input should first run :func:`validate_privilege_profile` so an
            unrecognized profile fails closed rather than silently expanding to nothing.
        framework: The target framework id. ``confined``/``exclusive`` expand to that
            framework's sandbox token (``goose`` → ``goose:sandbox``); ``None`` or any other
            framework keeps ``claude:sandbox`` (the historical default). This is a REQUEST,
            platform-independent — enforceability is decided by :func:`is_sandbox_capable`.

    Returns:
        The list of ``<ns>:<feature>`` tokens to union into the active feature set.
    """
    base = _PROFILE_FEATURE_TOKENS.get(profile or "cooperative", ())
    if not base:
        return []
    token = _sandbox_token_for(framework)
    return [token if tok == "claude:sandbox" else tok for tok in base]


def is_sandbox_capable(framework_id: str, platform: str | None = None) -> bool:
    """Return True iff agentteams can emit an OS-enforced sandbox for ``framework_id`` HERE.

    This is the single, platform-aware decision function behind the emit-vs-fail-closed
    gating:

    * **Linux — framework-NEUTRAL, any framework.** agentteams emits a provider-agnostic
      ``bwrap`` launcher (repo-root ``sandbox/confine-run.sh``, via ``_linux_sandbox_emit.py``:
      read-only root + rootless netns + NoNewPrivs + credential read-exclusion) that wraps ANY
      process, so Linux enforceability does not depend on the framework. This is deliberately
      not goose-gated and not ``.goose/``-pathed (operator correction 2026-08-31: no harness
      preference in agentteams). Enforcement-VERIFIED on a live kernel (baseAgent
      ``layerc-escape-tests.sh`` [5][6], 6/6, incl. a real ``goose`` process).
    * ``claude`` — always capable (Claude Code configures its own Seatbelt/bubblewrap
      sandbox on macOS/Linux; native Windows degrades to advisory inside Claude Code's own
      behavior, which agentteams still emits a block for).
    * ``goose`` — capable on **macOS** (Apple Seatbelt: ``sandbox-exec`` + ``GOOSE_SANDBOX``,
      emitted by ``_goose_sandbox_emit.py``). On **Windows** there is no emittable OS boundary,
      so a confined/exclusive goose team there fails closed / advises, never claims a boundary
      (honest fail-closed). (Linux is covered framework-neutrally by the branch above.)
    * anything else — not capable (off Linux).

    Args:
        framework_id: The target framework id.
        platform: Override for the platform string (defaults to live ``sys.platform``).
            Supplied by tests to exercise the Linux/Windows branch deterministically.

    Returns:
        Whether an OS boundary can be emitted for this framework on this platform.
    """
    plat = sys.platform if platform is None else platform
    if plat.startswith("linux"):
        # Framework-neutral: the emitted bwrap launcher wraps any process (correction #2).
        return True
    if framework_id == "claude":
        return True
    if framework_id == "goose":
        return plat == "darwin"
    return False


#: Frameworks for which agentteams can emit OS-enforced write-confinement on THIS host.
#: On **Linux** every framework qualifies framework-neutrally (the emitted bwrap launcher
#: wraps any process), so on a Linux host both sampled ids below resolve True. Claude Code
#: always qualifies (native Seatbelt/bubblewrap). Goose additionally qualifies on **macOS**
#: (Apple Seatbelt via GOOSE_SANDBOX); on **Windows** a confined/exclusive goose profile
#: still degrades to the privilege_profile_advisory (honest fail-closed). This set only
#: samples ("claude", "goose") for backward compatibility — the authoritative, framework-
#: complete decision path is :func:`is_sandbox_capable` (which returns True for ANY framework
#: on Linux); prefer calling it directly over reading this convenience set.
SANDBOX_CAPABLE_FRAMEWORKS: frozenset[str] = frozenset(
    fw for fw in ("claude", "goose") if is_sandbox_capable(fw)
)


def privilege_profile_advisory(
    profile: str | None,
    framework_id: str,
    host_features: Iterable[str] | None = None,
    *,
    platform: str | None = None,
) -> dict[str, str] | None:
    """Return an advisory dict when confinement is requested but unenforceable on this host.

    OS-level enforcement exists on a sandbox-capable framework/platform only —
    :func:`is_sandbox_capable`: ``claude`` everywhere, ``goose`` on macOS only. A request
    made either way — via ``privilege_profile`` confined/exclusive OR a directly-passed
    ``claude:sandbox``/``goose:sandbox`` host-feature token — on a target that cannot enforce
    it HERE emits no boundary, so it is advisory only: a state that must be surfaced, never
    silent.

    Args:
        profile: The active ``privilege_profile`` (``cooperative``/``confined``/``exclusive``).
        framework_id: The target framework id (e.g. ``claude``, ``goose``).
        host_features: The effective host-feature tokens; a direct ``claude:sandbox`` or
            ``goose:sandbox`` token here also counts as a confinement request. ``None`` is
            treated as empty.
        platform: Override for the platform string (defaults to live ``sys.platform``);
            forwarded to :func:`is_sandbox_capable` so callers/tests can evaluate the
            Linux/Windows branch deterministically.

    Returns:
        An advisory ``{"code", "message"}`` dict when confinement is requested that the
        target cannot enforce, else ``None``.
    """
    hf = list(host_features or [])
    sandbox_token = next((t for t in ("claude:sandbox", "goose:sandbox") if t in hf), None)
    requested = profile in {"confined", "exclusive"} or sandbox_token is not None
    if requested and not is_sandbox_capable(framework_id, platform):
        how = (
            f"privilege_profile={profile!r}"
            if profile in {"confined", "exclusive"}
            else (sandbox_token or "claude:sandbox")
        )
        plat = sys.platform if platform is None else platform
        return {
            "code": "privilege-profile-unenforced-host",
            "message": (
                f"{how} requests workspace write-confinement, but agentteams cannot emit an "
                f"OS-level sandbox for the {framework_id!r} framework on this platform "
                f"({plat}). No enforcement is emitted for this target — the request is "
                "ADVISORY ONLY here. OS-enforced confinement is available framework-neutrally "
                "on **Linux** (an emitted provider-agnostic bwrap launcher, "
                "'sandbox/confine-run.sh', wraps any process), on the 'claude' framework "
                "everywhere (Claude Code sandbox), and on the 'goose' framework on macOS "
                "(Apple Seatbelt via GOOSE_SANDBOX). On **Windows** and every other "
                "non-Linux target without a native sandbox, confine from OUTSIDE the process "
                "instead: a container plus seccomp-bpf + Landlock and egress filtering."
            ),
        }
    return None


def merge_profile_features(
    features: Iterable[str], profile: str | None, framework: str | None = None
) -> list[str]:
    """Union explicit feature tokens with a privilege_profile's expansion.

    Order-preserving and idempotent: explicit tokens keep their position and a
    profile-implied token already present is not duplicated. ``cooperative`` adds
    nothing and therefore never strips an explicitly requested ``claude:sandbox``.

    Args:
        features: The explicitly-selected tokens (e.g. from --target-host-features).
        profile: The privilege_profile whose expansion is unioned in.
        framework: The target framework id, forwarded to :func:`expand_privilege_profile`
            so ``goose`` unions ``goose:sandbox`` (not ``claude:sandbox``). ``None`` keeps
            the historical ``claude:sandbox`` expansion.

    Returns:
        The merged, deduped token list.
    """
    merged = list(features)
    for tok in expand_privilege_profile(profile, framework):
        if tok not in merged:
            merged.append(tok)
    return merged


__all__ = [
    "HostFeatureError",
    "SANDBOX_CAPABLE_FRAMEWORKS",
    "expand_privilege_profile",
    "is_enabled",
    "is_sandbox_capable",
    "merge_profile_features",
    "parse_tokens",
    "privilege_profile_advisory",
    "validate",
]
