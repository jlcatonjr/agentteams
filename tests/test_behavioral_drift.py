"""Tests for Cluster A Phase 3 — behavioral drift detection.

Gate (from the master plan): flags an injected trajectory divergence on a
canonical scenario AND passes a conforming run. Also exercises the Cluster C
``audit_handoff_chain`` reuse for typed-payload continuity.
"""

from __future__ import annotations

from datetime import date

import pytest

from agentteams.eval_suite import build_eval_suite
from agentteams.behavioral_drift import (
    BEHAVIOR_BROKEN_CHAIN,
    BEHAVIOR_CHAIN_DIVERGENCE,
    BEHAVIOR_MISSING_RETURN,
    BEHAVIOR_NO_TRAJECTORY,
    detect_behavioral_drift,
    reconstruct_chain,
)

# Frozen clock so audit_handoff_chain's dated PAYLOAD_UNTYPED severity is
# deterministic (pre-2026-07-01 => WARN, but conforming runs emit none).
_TODAY = date(2026, 5, 19)


def _suite_with_handoffs() -> dict:
    # transform.cross_refs=[ingest] -> chain [ingest-expert, transform-expert]
    # load.cross_refs=[transform]   -> chain [transform-expert, load-expert]
    return build_eval_suite({
        "project_name": "DataPipeline",
        "framework": "copilot-vscode",
        "workstream_expert_slugs": ["ingest-expert", "transform-expert", "load-expert"],
        "components": [
            {"slug": "ingest", "cross_refs": []},
            {"slug": "transform", "cross_refs": ["ingest"]},
            {"slug": "load", "cross_refs": ["transform"]},
        ],
    })


def _suite_no_handoffs() -> dict:
    return build_eval_suite({
        "project_name": "Flat",
        "framework": "claude",
        "workstream_expert_slugs": ["a-expert", "b-expert"],
        "components": [{"slug": "a", "cross_refs": []}, {"slug": "b", "cross_refs": []}],
    })


def test_reconstruct_chain_orders_by_sequence():
    traj = {"handoff_edges": [
        {"sequence": 1, "from_agent": "transform-expert", "to_agent": "load-expert"},
        {"sequence": 0, "from_agent": "ingest-expert", "to_agent": "transform-expert"},
    ]}
    assert reconstruct_chain(traj) == ["ingest-expert", "transform-expert", "load-expert"]


def test_conforming_run_has_no_findings():
    traj = {
        "session_slug": "s1",
        "root_agent": "orchestrator",
        "handoff_edges": [
            {"sequence": 0, "from_agent": "ingest-expert",
             "to_agent": "transform-expert", "mediated_by": "orchestrator",
             "payload_schema_id": "schemas/handoff-payloads/x.v1.schema.json"},
        ],
    }
    assert detect_behavioral_drift(traj, _suite_with_handoffs(), today=_TODAY) == []


def test_injected_chain_divergence_is_flagged():
    # Skips transform-expert -> matches no expected chain.
    traj = {"session_slug": "s", "handoff_edges": [
        {"sequence": 0, "from_agent": "ingest-expert",
         "to_agent": "load-expert", "mediated_by": "orchestrator"},
    ]}
    findings = detect_behavioral_drift(traj, _suite_with_handoffs(), today=_TODAY)
    assert any(f.code == BEHAVIOR_CHAIN_DIVERGENCE for f in findings)


def test_missing_orchestrator_mediation_is_flagged():
    # Correct chain but peer-to-peer (no mediated_by, no return edge).
    traj = {"session_slug": "s", "handoff_edges": [
        {"sequence": 0, "from_agent": "ingest-expert", "to_agent": "transform-expert"},
    ]}
    findings = detect_behavioral_drift(traj, _suite_with_handoffs(), today=_TODAY)
    assert any(f.code == BEHAVIOR_MISSING_RETURN for f in findings)


def test_broken_contiguity_is_flagged():
    traj = {"session_slug": "s", "handoff_edges": [
        {"sequence": 0, "from_agent": "ingest-expert", "to_agent": "transform-expert",
         "mediated_by": "orchestrator"},
        {"sequence": 1, "from_agent": "load-expert", "to_agent": "report-expert",
         "mediated_by": "orchestrator"},
    ]}
    findings = detect_behavioral_drift(traj, _suite_with_handoffs(), today=_TODAY)
    assert any(f.code == BEHAVIOR_BROKEN_CHAIN for f in findings)


def test_payload_mismatch_along_trajectory_uses_cluster_c():
    # No handoff scenarios -> no chain checks; isolate the audit_handoff_chain
    # reuse. Two contiguous edges carrying different payload $ids -> mismatch.
    traj = {"session_slug": "s", "handoff_edges": [
        {"sequence": 0, "from_agent": "a-expert", "to_agent": "b-expert",
         "payload_schema_id": "schemas/handoff-payloads/a.v1.schema.json"},
        {"sequence": 1, "from_agent": "b-expert", "to_agent": "c-expert",
         "payload_schema_id": "schemas/handoff-payloads/b.v1.schema.json"},
    ]}
    findings = detect_behavioral_drift(traj, _suite_no_handoffs(), today=_TODAY)
    assert any(f.code == "PAYLOAD_MISMATCH" for f in findings)
    # No spurious behavioral findings when there are no chain expectations.
    assert not any(f.code.startswith("BEHAVIOR_") for f in findings)


def test_no_trajectory_but_expected_chain_is_flagged():
    findings = detect_behavioral_drift(
        {"session_slug": "s", "handoff_edges": []},
        _suite_with_handoffs(), today=_TODAY,
    )
    assert [f.code for f in findings] == [BEHAVIOR_NO_TRAJECTORY]


def test_empty_suite_and_empty_trajectory_is_clean():
    assert detect_behavioral_drift(
        {"session_slug": "s", "handoff_edges": []},
        _suite_no_handoffs(), today=_TODAY,
    ) == []
