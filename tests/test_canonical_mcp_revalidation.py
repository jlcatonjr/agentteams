"""MCP security_review re-validation on canonical import (plan §5.4 r2, H.4).

The mcp-server.schema.json allOf hard gates force security_review.required=true
for third-party trust tiers and for any destructive tool. Those gates must be
re-validated at canonical IMPORT — a hand-edited canonical team.cai.json that
weakens a security-review flag must fail re-import, not silently round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.canonical import materialize_canonical
from agentteams.interop import export_to_cai, import_from_cai

pytest.importorskip("jsonschema")


def _cai_with_server(server: dict) -> dict:
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-10T00:00:00+00:00",
        "source_framework": "claude",
        "source_dir": "src/.claude/agents",
        "instructions_binding": {"source_name": "", "content": ""},
        "agents": [
            {
                "slug": "orchestrator",
                "name": "Orchestrator",
                "description": "d",
                "body_markdown": "# Orchestrator\n\nBody.\n",
                "capabilities": {},
                "handoffs": [],
                "invariant_core_markdown": None,
                "source_path": "orchestrator.md",
            }
        ],
        "skills": [],
        "mcp_servers": [server],
    }


def _server(
    server_id: str,
    *,
    trust_tier: str = "third-party-vetted",
    side_effects: str = "read",
    security_required: bool = True,
) -> dict:
    return {
        "artifact_type": "mcp-server",
        "mcp_server_schema_version": "1.0",
        "server_id": server_id,
        "domain": "testing",
        "trust_tier": trust_tier,
        "transport": "stdio",
        "tools": [{"name": "do_thing", "side_effects": side_effects}],
        "scope": ["orchestrator"],
        "progressive_disclosure": "lazy",
        "security_review": {"required": security_required},
    }


# ---------------------------------------------------------------------------
# Direct CAI import
# ---------------------------------------------------------------------------

def test_valid_third_party_server_imports(tmp_path: Path):
    cai = _cai_with_server(_server("vetted-server"))
    result = import_from_cai(cai, "claude", tmp_path / ".claude" / "agents")
    assert result.errors == []


def test_weakened_third_party_server_fails_import(tmp_path: Path):
    cai = _cai_with_server(_server("vetted-server", security_required=False))
    with pytest.raises(ValueError, match="security_review"):
        import_from_cai(cai, "claude", tmp_path / ".claude" / "agents")


def test_weakened_destructive_tool_server_fails_import(tmp_path: Path):
    # First-party trust tier — only the destructive tool forces the gate.
    cai = _cai_with_server(
        _server("destructive-server", trust_tier="first-party",
                side_effects="destructive", security_required=False)
    )
    with pytest.raises(ValueError, match="security_review"):
        import_from_cai(cai, "claude", tmp_path / ".claude" / "agents")


def test_first_party_read_only_server_without_review_imports(tmp_path: Path):
    # Neither gate fires: first-party + read-only tools may legitimately
    # carry security_review.required=false.
    cai = _cai_with_server(
        _server("internal-server", trust_tier="first-party", security_required=False)
    )
    result = import_from_cai(cai, "claude", tmp_path / ".claude" / "agents")
    assert result.errors == []


# ---------------------------------------------------------------------------
# The plan's exact threat model: hand-edited canonical team.cai.json
# ---------------------------------------------------------------------------

def test_hand_edited_canonical_team_cai_json_fails_reimport(tmp_path: Path):
    cai = _cai_with_server(_server("vetted-server"))

    # Materialize a valid canonical directory (via the canonical target).
    canon = tmp_path / "proj" / ".agentteams" / "canonical"
    import_from_cai(cai, "canonical", canon)
    assert (canon / "team.cai.json").is_file()

    # Hand-edit weakens the security-review flag on disk.
    team_file = canon / "team.cai.json"
    team = json.loads(team_file.read_text(encoding="utf-8"))
    team["mcp_servers"][0]["security_review"]["required"] = False
    team_file.write_text(json.dumps(team, indent=2) + "\n", encoding="utf-8")

    # Re-import path: canonical source -> claude target must FAIL, not
    # silently round-trip the weakened server.  D.1 schema validation in
    # load_canonical catches the weakened flag at the READ boundary (during
    # export_to_cai), before import_from_cai's MCP re-validation would have
    # caught it at the IMPORT boundary — either way, the weakened flag is
    # caught.
    with pytest.raises(ValueError, match="security_review"):
        loaded = export_to_cai(canon)  # detect_framework -> canonical -> load
        import_from_cai(loaded, "claude", tmp_path / "out" / ".claude" / "agents")


def test_unweakened_canonical_round_trip_still_imports(tmp_path: Path):
    """Control for the threat-model test: the un-edited canonical dir imports fine."""
    cai = _cai_with_server(_server("vetted-server"))
    canon = tmp_path / "proj" / ".agentteams" / "canonical"
    import_from_cai(cai, "canonical", canon)
    loaded = export_to_cai(canon)
    result = import_from_cai(loaded, "claude", tmp_path / "out" / ".claude" / "agents")
    assert result.errors == []
