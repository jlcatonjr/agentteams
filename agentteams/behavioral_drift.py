"""Behavioral drift detection (Cluster A Phase 3).

Source/template drift (``drift.py``) answers "did a generated *file* change?".
This module answers the orthogonal question "did the team's *behavior* diverge
from what it was specified to do?" — by comparing a recorded run trajectory
(the Phase 1 ``agent_session_trajectory`` replay substrate) against the
framework-neutral eval-suite (the Phase 2 behavioral spec), and by re-using the
Cluster C ``audit_handoff_chain`` to check typed-payload continuity along the
*actual* edges walked.

Deliberately named ``behavioral_drift`` (not ``drift``) to avoid semantic
collision with template/structural/manifest drift.

Inputs
------
- ``trajectory``: an ``agent_session_trajectory`` dict
  ({session_slug, root_agent?, handoff_edges:[{sequence, from_agent,
  to_agent, payload_schema_id?, mediated_by?}]}).
- ``eval_suite``: the dict from ``eval_suite.build_eval_suite`` — its
  ``handoff-chain`` scenarios carry the expected ``chain`` + ``returns_to``.

A conforming run yields ``[]``. Any divergence yields ``Finding`` records
(reusing the Cluster C ``Finding`` dataclass for a uniform finding shape).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agentteams.handoff_payloads import Finding, audit_handoff_chain

# Behavioral finding codes (distinct namespace from PAYLOAD_* in Cluster C).
BEHAVIOR_BROKEN_CHAIN = "BEHAVIOR_BROKEN_CHAIN"
BEHAVIOR_CHAIN_DIVERGENCE = "BEHAVIOR_CHAIN_DIVERGENCE"
BEHAVIOR_MISSING_RETURN = "BEHAVIOR_MISSING_RETURN"
BEHAVIOR_NO_TRAJECTORY = "BEHAVIOR_NO_TRAJECTORY"


def reconstruct_chain(trajectory: dict[str, Any]) -> list[str]:
    """Return the ordered agent chain from ``handoff_edges``.

    Sorts by ``sequence`` then walks ``from_agent -> to_agent``. The chain is
    ``[edges[0].from_agent, edges[0].to_agent, edges[1].to_agent, ...]``.
    Empty trajectory -> ``[]``.
    """
    edges = sorted(
        trajectory.get("handoff_edges", []),
        key=lambda e: e.get("sequence", 0),
    )
    if not edges:
        return []
    chain = [edges[0].get("from_agent", "")]
    for e in edges:
        chain.append(e.get("to_agent", ""))
    return chain


def _expected_handoff_scenarios(eval_suite: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        s
        for s in eval_suite.get("scenarios", [])
        if s.get("category") == "handoff"
        and s.get("predicate", {}).get("kind") == "handoff-chain"
    ]


def _payload_findings(trajectory: dict[str, Any], today: date | None) -> list[Finding]:
    """Re-use Cluster C audit_handoff_chain over the ACTUAL edges.

    Each edge's ``payload_schema_id`` is the payload carried across it; map
    edge k to a step with out==in==payload_schema_id so audit_handoff_chain
    flags a typed-payload break between consecutive edges (mismatch) or an
    untyped handoff (missing id).
    """
    edges = sorted(
        trajectory.get("handoff_edges", []),
        key=lambda e: e.get("sequence", 0),
    )
    steps = [
        {
            "payload_schema_out": e.get("payload_schema_id", ""),
            "payload_schema_in": e.get("payload_schema_id", ""),
        }
        for e in edges
    ]
    return audit_handoff_chain(steps, today=today)


def detect_behavioral_drift(
    trajectory: dict[str, Any],
    eval_suite: dict[str, Any],
    *,
    today: date | None = None,
) -> list[Finding]:
    """Compare a run trajectory against the eval-suite's behavioral spec.

    Returns ``[]`` for a conforming run; one or more ``Finding`` otherwise.
    """
    findings: list[Finding] = []

    edges = sorted(
        trajectory.get("handoff_edges", []),
        key=lambda e: e.get("sequence", 0),
    )
    expected = _expected_handoff_scenarios(eval_suite)

    if not edges:
        if expected:
            findings.append(Finding(
                BEHAVIOR_NO_TRAJECTORY, "HARD",
                "eval-suite declares handoff chain(s) but the trajectory has "
                "no handoff_edges (the team did not run the expected workflow)",
            ))
        return findings

    # Contiguity: edge[i].to_agent must equal edge[i+1].from_agent.
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        if a.get("to_agent") != b.get("from_agent"):
            findings.append(Finding(
                BEHAVIOR_BROKEN_CHAIN, "HARD",
                f"edge {a.get('sequence')} -> {b.get('sequence')}: "
                f"to_agent {a.get('to_agent')!r} != next from_agent "
                f"{b.get('from_agent')!r} (trajectory is not a single chain)",
            ))

    actual_chain = reconstruct_chain(trajectory)

    # Chain divergence: if the suite expects handoff chains, the actual chain
    # must match one expected chain exactly (mediator-agnostic — orchestrator
    # mediation is recorded via mediated_by, not as chain nodes).
    if expected:
        expected_chains = [
            s["predicate"].get("chain", []) for s in expected
        ]
        if actual_chain not in expected_chains:
            findings.append(Finding(
                BEHAVIOR_CHAIN_DIVERGENCE, "HARD",
                f"actual chain {actual_chain} matches no expected chain "
                f"{expected_chains}",
            ))
        else:
            # Matched a chain — its returns_to mediation must be observable.
            matched = next(
                s for s in expected
                if s["predicate"].get("chain", []) == actual_chain
            )
            returns_to = matched["predicate"].get("returns_to")
            if returns_to:
                mediated = any(
                    e.get("mediated_by") == returns_to for e in edges
                )
                returned = any(
                    e.get("to_agent") == returns_to for e in edges
                )
                if not (mediated or returned):
                    findings.append(Finding(
                        BEHAVIOR_MISSING_RETURN, "HARD",
                        f"expected coordination mediated by {returns_to!r} "
                        f"(scenario {matched.get('id')!r}) but no edge is "
                        f"mediated_by or returns to it — peer-to-peer drift",
                    ))

    # Typed-payload continuity along the actual edges (Cluster C reuse).
    findings.extend(_payload_findings(trajectory, today))

    return findings


__all__ = [
    "BEHAVIOR_BROKEN_CHAIN",
    "BEHAVIOR_CHAIN_DIVERGENCE",
    "BEHAVIOR_MISSING_RETURN",
    "BEHAVIOR_NO_TRAJECTORY",
    "Finding",
    "reconstruct_chain",
    "detect_behavioral_drift",
]
