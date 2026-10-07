"""MCP-suitability detection rubric (pure, dependency-free).

Implements the decision protocol in ``references/mcp-auto-detection-report.md``
§5: given an integration hint, decide whether a project should BUILD an MCP
server, USE a direct API call, or DEFER the decision to operator security
review. This module ONLY recommends — it never provisions anything.

The decision is a necessary-condition gate, NOT a flat signal count (the flat
``positives - negatives`` count was rejected in the report's adversarial audit,
§9/A1):

    if hard_gate:                            DEFER_TO_SECURITY_REVIEW
    elif cross_host_reuse and statefulness:  BUILD_MCP
    else:                                    USE_DIRECT_API

``cross_host_reuse`` and ``statefulness`` are the two necessary conditions.
The large/dynamic operation surface is a tiebreaker that only matters when both
necessary conditions already hold (§5.1); when committed lazy disclosure is
absent it does NOT flip the three-way decision (§5.2 is the binding rule), but
it raises a structured ``efficiency_risk`` signal so a downstream emitter can
force ``progressive_disclosure='lazy'`` (§4.1).

**Fail-closed posture.** This function may be called on raw, un-schema-validated
dicts. Security-critical fields therefore fail closed: a missing or
unrecognized ``trust_tier`` or an unrecognized ``max_side_effect`` triggers the
hard gate (DEFER), rather than defaulting to the permissive case. Boolean
fields are coerced strictly (only a real ``True`` is true) so a stringy
``"false"`` cannot silently flip a recommendation.

Input hints follow the ``mcp_hints`` item shape in
``schemas/project-description.schema.json``. Output candidates follow the
``mcp_candidates`` item shape in ``schemas/team-manifest.schema.json``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from agentteams.mcp_need import REFUSE, USE_CLI, USE_RUNNER_PATH
from agentteams.write_policy import ORCHESTRATOR_SLUGS

#: An agent slug, when no roster is given (direct callers).
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

BUILD_MCP = "BUILD_MCP"
USE_DIRECT_API = "USE_DIRECT_API"
DEFER_TO_SECURITY_REVIEW = "DEFER_TO_SECURITY_REVIEW"

_THIRD_PARTY_TIERS = frozenset({"third-party-vetted", "third-party-untrusted"})
_VALID_TIERS = frozenset({"first-party"}) | _THIRD_PARTY_TIERS
_VALID_SIDE_EFFECTS = frozenset({"read", "write", "destructive"})


@dataclass
class McpCandidate:
    """One integration's MCP-suitability recommendation."""

    candidate_id: str
    recommendation: str
    rationale: str
    signals: dict[str, bool] = field(default_factory=dict)
    #: Under ``write_policy: "orchestrator-only"`` only: the agents the hint names (``used_by_components``) and
    #: each one's decision under the agent MCP-need protocol (``mcp_need.reference_doc``). ``None`` otherwise,
    #: so manifests without the switch are unchanged.
    agents: list[str] | None = None
    per_agent: list[dict[str, str]] | None = None

    def to_manifest_entry(self) -> dict[str, Any]:
        """Return a dict matching the team-manifest ``mcp_candidates`` item schema (unset optional fields omitted)."""
        return {k: v for k, v in asdict(self).items() if v is not None}


def _strict_bool(value: Any) -> bool:
    """Truthy only for a real ``True`` — stringy ``"false"`` etc. are False."""
    return value is True


def _slug(value: str) -> str:
    out = "".join(c if (c.isalnum() or c == "-") else "-" for c in value.strip().lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "integration"




def classify_agent_need(agent: str, hint: dict[str, Any]) -> dict[str, str]:
    """One agent's decision for one hint, by the MCP-need protocol, before anything is measured.

    The protocol's order: the orchestrator uses the CLI (Q0); a capability that writes is refused as a server,
    since writes go through proposals (Q1); the hard gate defers to @security (Q2); everything else keeps the
    runner path until the ledger shows a cost over the operator's threshold (Q3). A brief hint is never
    evidence, so generation can't get past Q3.

    Args:
        agent: The agent slug.
        hint: The ``mcp_hints`` entry.

    Returns:
        ``{"agent", "decision", "rationale"}``.

    Raises:
        Nothing.
    """
    side_effect = hint.get("max_side_effect")  # absent: unknown, so it fails closed to Q2 (not "read")
    trust_tier = hint.get("trust_tier")
    if agent in ORCHESTRATOR_SLUGS:
        return {"agent": agent, "decision": USE_CLI, "rationale": "the orchestrator uses the agentteams CLI (Q0)"}
    if side_effect in ("write", "destructive"):
        return {"agent": agent, "decision": REFUSE,
                "rationale": f"max_side_effect={side_effect!r}: writes go through proposals, not a server (Q1)"}
    if trust_tier not in _VALID_TIERS or trust_tier in _THIRD_PARTY_TIERS or side_effect not in _VALID_SIDE_EFFECTS:
        return {"agent": agent, "decision": DEFER_TO_SECURITY_REVIEW,
                "rationale": f"hard gate (trust_tier={trust_tier!r}, max_side_effect={side_effect!r}) (Q2)"}
    return {"agent": agent, "decision": USE_RUNNER_PATH,
            "rationale": "no ledger measurement yet; keep the runner path and wait (Q3)"}


def evaluate_hint(hint: dict[str, Any], *, target_host_count: int = 1, write_policy: bool = False,
                  roster: list[str] | None = None, components: list[str] | None = None) -> McpCandidate:
    """Evaluate a single integration hint into an :class:`McpCandidate`.

    Args:
        hint: An ``mcp_hints`` entry.
        target_host_count: Target hosts/frameworks this build emits for; >1 is itself evidence of cross-host
            reuse.
        write_policy: Under ``"orchestrator-only"``: keep the hint's agents and decide each one
            (:func:`classify_agent_need`).
        roster: The team's agent slugs; only these (and the orchestrator) are kept as agents.
        components: The team's component slugs; a component names its workstream expert (``<slug>-expert``).

    Returns:
        The candidate.

    Raises:
        Nothing.
    """
    integration = str(hint.get("integration", "")).strip()
    candidate_id = _slug(integration)

    used_by = hint.get("used_by_components")
    used_by_count = len(used_by) if isinstance(used_by, list) else 0
    cross_host_reuse = target_host_count > 1 or used_by_count >= 2
    statefulness = _strict_bool(hint.get("stateful"))
    large_dynamic_surface = _strict_bool(hint.get("large_dynamic_surface"))
    commits_lazy_disclosure = _strict_bool(hint.get("commits_lazy_disclosure"))
    efficiency_risk = large_dynamic_surface and not commits_lazy_disclosure

    # Fail closed: missing/unrecognized security fields trigger the gate.
    trust_tier = hint.get("trust_tier")
    trust_unknown = trust_tier not in _VALID_TIERS
    trust_gate = trust_tier in _THIRD_PARTY_TIERS or trust_unknown

    side_effect = hint.get("max_side_effect", "read")
    side_unknown = side_effect not in _VALID_SIDE_EFFECTS
    side_gate = side_effect == "destructive" or side_unknown

    hard_gate = trust_gate or side_gate

    signals = {
        "cross_host_reuse": cross_host_reuse,
        "statefulness": statefulness,
        "large_dynamic_surface": large_dynamic_surface,
        "commits_lazy_disclosure": commits_lazy_disclosure,
        "efficiency_risk": efficiency_risk,
        "hard_gate": hard_gate,
    }

    if hard_gate:
        reasons = []
        if trust_tier in _THIRD_PARTY_TIERS:
            reasons.append(f"trust_tier={trust_tier!r}")
        if trust_unknown:
            reasons.append(f"missing/unrecognized trust_tier ({trust_tier!r}) — failing closed")
        if side_effect == "destructive":
            reasons.append("a destructive operation surface")
        if side_unknown:
            reasons.append(f"unrecognized max_side_effect ({side_effect!r}) — failing closed")
        rationale = (
            f"Hard gate: {'; '.join(reasons)}. Requires explicit operator "
            "security authorization before any MCP server is built or activated "
            "(report §5.1, §5.3)."
        )
        return _with_agents(McpCandidate(candidate_id, DEFER_TO_SECURITY_REVIEW, rationale, signals), hint,
                            write_policy, roster, components)

    if cross_host_reuse and statefulness:
        rationale = (
            "Both necessary conditions hold (cross-host reuse and statefulness), "
            "so an MCP server is warranted over duplicated direct-API wrappers."
        )
        if efficiency_risk:
            rationale += (
                " RISK: large/dynamic tool surface without a lazy-disclosure "
                "commitment is an efficiency anti-pattern (report §4.1); the "
                "emitted server MUST use progressive_disclosure='lazy'."
            )
        elif large_dynamic_surface and commits_lazy_disclosure:
            rationale += (
                " A large/dynamic surface with committed lazy disclosure further "
                "strengthens the case (report §5.1)."
            )
        return _with_agents(McpCandidate(candidate_id, BUILD_MCP, rationale, signals), hint, write_policy, roster,
                            components)

    missing = []
    if not cross_host_reuse:
        missing.append("cross-host reuse (single host, <2 components)")
    if not statefulness:
        missing.append("statefulness (no auth/session/pooling benefit)")
    rationale = (
        "Direct, host-overseen API calls are preferred: missing "
        f"{' and '.join(missing)}. MCP is not justified (report §3)."
    )
    return _with_agents(McpCandidate(candidate_id, USE_DIRECT_API, rationale, signals), hint, write_policy, roster,
                        components)


def _with_agents(cand: McpCandidate, hint: dict[str, Any], write_policy: bool, roster: list[str] | None,
                 components: list[str] | None) -> McpCandidate:
    """Under the switch, keep the hint's agents and decide each one; otherwise return *cand* unchanged.

    ``used_by_components`` names components, whose agent is ``<slug>-expert``; a value that is already an agent
    slug on the roster (or the orchestrator) is kept as is. Anything else names no agent of this team: it is
    listed in the rationale as ignored, never invented as a register row.
    """
    if not write_policy:
        return cand
    used_by = hint.get("used_by_components")
    values = [v.strip() for v in used_by if isinstance(v, str) and v.strip()] if isinstance(used_by, list) else []
    known = set(roster or []) | set(ORCHESTRATOR_SLUGS)
    comps = set(components or [])
    agents: list[str] = []
    ignored: list[str] = []
    for value in values:
        agent = f"{value}-expert" if value in comps else value
        if agent in known or (roster is None and _SLUG_RE.fullmatch(agent)):
            agents.append(agent)
        else:
            ignored.append(value)
    cand.agents = list(dict.fromkeys(agents))
    cand.per_agent = [classify_agent_need(a, hint) for a in cand.agents]
    if ignored:
        cand.rationale += (" Ignored used_by_components naming no agent of this team: "
                           + ", ".join(repr(v[:40]) for v in dict.fromkeys(ignored)) + ".")
    return cand


def detect_mcp_candidates(
    description: dict[str, Any], *, target_host_count: int = 1, write_policy: bool | None = None,
    roster: list[str] | None = None, components: list[str] | None = None,
) -> list[McpCandidate]:
    """Evaluate all ``mcp_hints`` in a project description.

    Returns ``[]`` when no hints are declared (the default — direct API).
    Duplicate ``candidate_id`` slugs are disambiguated with a numeric suffix so
    a punctuation/case collision cannot silently drop a recommendation.

    Args:
        description: The project description.
        target_host_count: Target hosts/frameworks this build emits for.
        write_policy: Whether the orchestrator-only switch is in effect for this framework. ``None`` reads the
            description; ``analyze`` passes the effective value (``write_policy_frameworks`` can turn it off).
        roster: The team's agent slugs, for the per-agent decisions under the switch.
        components: The team's component slugs (a component names its ``<slug>-expert`` agent).

    Returns:
        The candidates.

    Raises:
        Nothing.
    """
    hints = description.get("mcp_hints") or []
    if write_policy is None:  # direct callers; analyze passes the EFFECTIVE switch (write_policy_frameworks)
        write_policy = description.get("write_policy") == "orchestrator-only"
    candidates: list[McpCandidate] = []
    seen: dict[str, int] = {}
    for hint in hints:
        cand = evaluate_hint(hint, target_host_count=target_host_count, write_policy=write_policy, roster=roster,
                             components=components)
        if cand.candidate_id in seen:
            seen[cand.candidate_id] += 1
            cand.candidate_id = f"{cand.candidate_id}-{seen[cand.candidate_id]}"
        else:
            seen[cand.candidate_id] = 1
        candidates.append(cand)
    return candidates


__all__ = [
    "BUILD_MCP",
    "USE_DIRECT_API",
    "DEFER_TO_SECURITY_REVIEW",
    "McpCandidate",
    "evaluate_hint",
    "classify_agent_need",
    "USE_CLI",
    "REFUSE",
    "USE_RUNNER_PATH",
    "detect_mcp_candidates",
]
