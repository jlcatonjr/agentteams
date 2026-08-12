"""A.5 regression tests: Claude model/disallowedTools/permissionMode capture and render.

Report section 4.2: model, disallowedTools, permissionMode are recognized by
Claude Code per this project's own adapter docstring but had zero capture
and zero render-side slot — they were silently dropped on every round trip.

A.3's raw_front_matter escape hatch captures them on export. A.5 adds
render-side support in claude.py's _inject_claude_front_matter so they
survive the import leg.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.interop import export_to_cai, import_from_cai


def _seed_cai_with_claude_fields() -> dict:
    """Seed CAI with Claude-specific front-matter keys in raw_front_matter."""
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-12T00:00:00+00:00",
        "source_framework": "claude",
        "source_dir": "seed",
        "instructions_binding": {
            "source_name": "CLAUDE.md",
            "content": "# Project Instructions\n\nCLAUDE_FIELDS_SEED\n",
        },
        "agents": [
            {
                "slug": "orchestrator",
                "name": "Orchestrator",
                "description": "Routes work.",
                "body_markdown": "# Orchestrator\n\nCLAUDE_FIELDS_ORCH_TOKEN\n",
                "capabilities": {"tool_scopes": ["read", "edit", "agent"]},
                "handoffs": [],
                "invariant_core_markdown": None,
                "raw_front_matter": {
                    "model": "claude-opus-4-5",
                    "disallowedTools": "Bash",
                    "permissionMode": "default",
                },
                "source_path": "orchestrator.md",
            },
        ],
        "skills": [],
        "mcp_servers": [],
        "references": [],
        "framework_extensions": {},
    }


def test_claude_optional_keys_survive_round_trip(tmp_path: Path):
    """model, disallowedTools, permissionMode must survive canonical→native→canonical."""
    seed = _seed_cai_with_claude_fields()

    # Materialize to canonical
    canon_dir = tmp_path / "canon"
    materialize_canonical(seed, canon_dir)
    loaded = load_canonical(canon_dir)

    # Import to native claude
    native_dir = tmp_path / "native" / ".claude" / "agents"
    result = import_from_cai(loaded, "claude", native_dir, overwrite=True)
    assert result.errors == [], f"import errors: {result.errors}"

    # Read the reimported file and check the fields are present
    reimported = (native_dir / "orchestrator.md").read_text()
    assert "model: claude-opus-4-5" in reimported, (
        f"model not found in reimported claude file"
    )
    assert "disallowedTools: Bash" in reimported, (
        f"disallowedTools not found in reimported claude file"
    )
    assert "permissionMode: default" in reimported, (
        f"permissionMode not found in reimported claude file"
    )
