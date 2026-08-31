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

from dataclasses import asdict, dataclass, field
from typing import Any

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

    def to_manifest_entry(self) -> dict[str, Any]:
        """Return a dict matching the team-manifest ``mcp_candidates`` item schema."""
        return asdict(self)


def _strict_bool(value: Any) -> bool:
    """Truthy only for a real ``True`` — stringy ``"false"`` etc. are False."""
    return value is True


def _slug(value: str) -> str:
    out = "".join(c if (c.isalnum() or c == "-") else "-" for c in value.strip().lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "integration"


def evaluate_hint(hint: dict[str, Any], *, target_host_count: int = 1) -> McpCandidate:
    """Evaluate a single integration hint into an :class:`McpCandidate`.

    ``target_host_count`` is the number of target hosts/frameworks this build
    emits for; >1 is itself evidence of cross-host reuse.
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
        return McpCandidate(candidate_id, DEFER_TO_SECURITY_REVIEW, rationale, signals)

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
        return McpCandidate(candidate_id, BUILD_MCP, rationale, signals)

    missing = []
    if not cross_host_reuse:
        missing.append("cross-host reuse (single host, <2 components)")
    if not statefulness:
        missing.append("statefulness (no auth/session/pooling benefit)")
    rationale = (
        "Direct, host-overseen API calls are preferred: missing "
        f"{' and '.join(missing)}. MCP is not justified (report §3)."
    )
    return McpCandidate(candidate_id, USE_DIRECT_API, rationale, signals)


def detect_mcp_candidates(
    description: dict[str, Any], *, target_host_count: int = 1
) -> list[McpCandidate]:
    """Evaluate all ``mcp_hints`` in a project description.

    Returns ``[]`` when no hints are declared (the default — direct API).
    Duplicate ``candidate_id`` slugs are disambiguated with a numeric suffix so
    a punctuation/case collision cannot silently drop a recommendation.
    """
    hints = description.get("mcp_hints") or []
    candidates: list[McpCandidate] = []
    seen: dict[str, int] = {}
    for hint in hints:
        cand = evaluate_hint(hint, target_host_count=target_host_count)
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
    "detect_mcp_candidates",
]
