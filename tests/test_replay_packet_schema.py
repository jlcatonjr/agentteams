"""Regression tests for the Cluster A Phase 1 replay-packet extension.

Locks the ``agent_session_trajectory`` contract added (Option A) to
``schemas/post-production-audit-decision-replay-packet.schema.json``. No
production code reads this schema yet (consumers are Phase 2), so these
tests are the only thing pinning the Phase 1 deliverable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas/post-production-audit-decision-replay-packet.schema.json"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _audit_decision_packet() -> dict:
    """Minimal valid packet with no agent session (pre-Phase-1 shape)."""
    return {
        "audit_slug": "data-pipeline-session-001",
        "timestamp": "2026-05-18T12:00:00Z",
        "window_start": "2026-05-18T11:00:00Z",
        "window_end": "2026-05-18T12:00:00Z",
        "query_hashes": {},
        "schema_version": "1.0.0",
        "environment_metadata": {"tool_versions": {}, "database_system": "PostgreSQL 14.5"},
    }


def test_schema_is_valid_draft7():
    import jsonschema

    jsonschema.Draft7Validator.check_schema(_schema())


def test_audit_decision_only_packet_still_validates():
    """agent_session_trajectory is optional: legacy audit packets remain valid."""
    import jsonschema

    jsonschema.Draft7Validator(_schema()).validate(_audit_decision_packet())


def test_multi_step_handoff_chain_is_recoverable():
    """The demoted scenario's assertion #1: the ingest->transform->load chain is
    recoverable from agent_session_trajectory.handoff_edges, independent of array order."""
    import jsonschema

    packet = _audit_decision_packet()
    packet["agent_session_trajectory"] = {
        "session_slug": "data-pipeline-run",
        "root_agent": "orchestrator",
        "handoff_edges": [
            # deliberately out of array order; recovery is by `sequence`
            {
                "sequence": 1,
                "from_agent": "transform-expert",
                "to_agent": "load-expert",
                "mediated_by": "orchestrator",
            },
            {
                "sequence": 0,
                "from_agent": "ingest-expert",
                "to_agent": "transform-expert",
                "mediated_by": "orchestrator",
                "payload_schema_id": "conflict-audit-result/v1.schema.json",
            },
        ],
    }
    jsonschema.Draft7Validator(_schema()).validate(packet)

    edges = sorted(packet["agent_session_trajectory"]["handoff_edges"], key=lambda e: e["sequence"])
    chain = [edges[0]["from_agent"]] + [e["to_agent"] for e in edges]
    assert chain == ["ingest-expert", "transform-expert", "load-expert"]
    # contiguity invariant the recovery rule depends on
    for a, b in zip(edges, edges[1:]):
        assert a["to_agent"] == b["from_agent"]


def test_handoff_edge_requires_core_fields():
    import jsonschema

    packet = _audit_decision_packet()
    packet["agent_session_trajectory"] = {
        "session_slug": "s",
        "handoff_edges": [{"sequence": 0, "to_agent": "load-expert"}],  # from_agent missing
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(_schema()).validate(packet)


def test_trajectory_requires_handoff_edges():
    import jsonschema

    packet = _audit_decision_packet()
    packet["agent_session_trajectory"] = {"session_slug": "s"}  # handoff_edges missing
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(_schema()).validate(packet)


def test_trajectory_rejects_unknown_fields():
    """additionalProperties:false must hold at packet, trajectory, and edge levels."""
    import jsonschema

    validator = jsonschema.Draft7Validator(_schema())

    unknown_edge = _audit_decision_packet()
    unknown_edge["agent_session_trajectory"] = {
        "session_slug": "s",
        "handoff_edges": [
            {"sequence": 0, "from_agent": "a", "to_agent": "b", "bogus": True},
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(unknown_edge)

    unknown_top = _audit_decision_packet()
    unknown_top["not_a_real_field"] = 1
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(unknown_top)
