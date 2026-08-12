"""D.1 regression tests: agent-cai.schema.json runtime validation in canonical.py.

Verifies that malformed CAI dicts are caught at the materialize_canonical
write boundary and the load_canonical read boundary via jsonschema validation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import canonical
from agentteams.interop import export_to_cai

REPO = Path(__file__).resolve().parents[1]
_COPILOT = REPO / ".github" / "agents"

pytestmark = pytest.mark.skipif(
    not _COPILOT.is_dir(),
    reason="repo source team (.github/agents) not found",
)


def _valid_cai() -> dict:
    """Return a minimal valid CAI dict for mutation tests."""
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-12T00:00:00Z",
        "source_framework": "canonical",
        "instructions_binding": {"source_name": "AGENTS.md", "content": "# Test"},
        "agents": [
            {
                "slug": "test-agent",
                "name": "Test Agent",
                "body_markdown": "Body text.",
                "source_path": ".github/agents/test-agent.agent.md",
            }
        ],
    }


class TestSchemaValidationAtWrite:
    def test_valid_cai_passes(self, tmp_path):
        cai = _valid_cai()
        result = canonical.materialize_canonical(cai, tmp_path / "out")
        assert "team.cai.json" in result.written

    def test_missing_required_top_level_field_raises(self, tmp_path):
        cai = _valid_cai()
        del cai["schema_version"]
        with pytest.raises(ValueError, match="schema validation failed"):
            canonical.materialize_canonical(cai, tmp_path / "out")

    def test_missing_required_agent_field_raises(self, tmp_path):
        cai = _valid_cai()
        del cai["agents"][0]["slug"]
        with pytest.raises(ValueError, match="schema validation failed"):
            canonical.materialize_canonical(cai, tmp_path / "out")

    def test_wrong_schema_version_raises(self, tmp_path):
        cai = _valid_cai()
        cai["schema_version"] = "1.0"
        with pytest.raises(ValueError, match="schema validation failed"):
            canonical.materialize_canonical(cai, tmp_path / "out")

    def test_invalid_tool_scope_raises(self, tmp_path):
        cai = _valid_cai()
        cai["agents"][0]["capabilities"] = {"tool_scopes": ["frobnicate"]}
        with pytest.raises(ValueError, match="schema validation failed"):
            canonical.materialize_canonical(cai, tmp_path / "out")

    def test_dry_run_still_validates(self, tmp_path):
        cai = _valid_cai()
        del cai["created_at"]
        with pytest.raises(ValueError, match="schema validation failed"):
            canonical.materialize_canonical(cai, tmp_path / "out", dry_run=True)


class TestSchemaValidationAtRead:
    def test_valid_round_trip_passes_validation(self, tmp_path):
        cai = export_to_cai(_COPILOT, "copilot-vscode")
        out = tmp_path / "c"
        canonical.materialize_canonical(cai, out)
        # Should not raise
        cai2 = canonical.load_canonical(out)
        assert cai2["schema_version"] == "2.0"

    def test_corrupted_team_file_raises_on_load(self, tmp_path):
        out = tmp_path / "c"
        out.mkdir()
        # Write a team.cai.json missing required fields
        (out / "team.cai.json").write_text(
            json.dumps({"source_framework": "canonical"}) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="schema validation failed"):
            canonical.load_canonical(out)
