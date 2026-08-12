"""A.4 regression tests: runtime-handoffs.json sidecar read-back.

Report section 4.3: the ``references/runtime-handoffs.json`` sidecar (written
by ``import_from_cai`` for manifest-delivery frameworks) was never read back
by ``export_to_cai``, so handoffs vanished on every native→canonical round trip
for claude, copilot-cli, agents-md, and codex (4 of 6 frameworks).

These tests verify the sidecar is read back and handoffs survive the round trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.interop import export_to_cai, import_from_cai

_MANIFEST_FRAMEWORKS = ("claude", "copilot-cli", "agents-md", "codex")


def _seed_cai_with_handoffs() -> dict:
    """Seed CAI with handoffs that will go through the sidecar path."""
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-12T00:00:00+00:00",
        "source_framework": "claude",
        "source_dir": "seed",
        "instructions_binding": {
            "source_name": "CLAUDE.md",
            "content": "# Project Instructions\n\nSIDEFAR_SEED\n",
        },
        "agents": [
            {
                "slug": "orchestrator",
                "name": "Orchestrator",
                "description": "Routes work to worker.",
                "body_markdown": "# Orchestrator\n\nSIDECAR_ORCH_TOKEN\n",
                "capabilities": {"tool_scopes": ["read", "edit", "agent"]},
                "handoffs": [{
                    "to": "worker", "label": "Delegate to worker",
                    "prompt": "Do the work.", "send": False,
                }],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "orchestrator.md",
            },
            {
                "slug": "worker",
                "name": "Worker",
                "description": "Does the work.",
                "body_markdown": "# Worker\n\nSIDECAR_WORKER_TOKEN\n",
                "capabilities": {"tool_scopes": ["read"]},
                "handoffs": [],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "worker.md",
            },
        ],
        "skills": [],
        "mcp_servers": [],
        "references": [],
        "framework_extensions": {},
    }


@pytest.mark.parametrize("framework", _MANIFEST_FRAMEWORKS)
def test_sidecar_handoffs_survive_round_trip(tmp_path: Path, framework: str):
    """For manifest-delivery frameworks, handoffs written to the sidecar on
    import must be read back on a subsequent export."""
    seed = _seed_cai_with_handoffs()

    # Materialize to canonical
    canon_dir = tmp_path / "canon"
    materialize_canonical(seed, canon_dir)
    loaded = load_canonical(canon_dir)

    # Import to native (writes the sidecar)
    _AGENTS_REL = {
        "claude": Path(".claude/agents"),
        "copilot-cli": Path(".github/copilot"),
        "agents-md": Path(".agents"),
        "codex": Path(".agents"),
    }
    native_dir = tmp_path / "native" / _AGENTS_REL[framework]
    result = import_from_cai(loaded, framework, native_dir, overwrite=True)
    assert result.errors == [], f"{framework}: import errors: {result.errors}"

    # Verify the sidecar was written
    sidecar = native_dir.parent / "references" / "runtime-handoffs.json"
    assert sidecar.is_file(), f"{framework}: sidecar not written at {sidecar}"
    sidecar_data = json.loads(sidecar.read_text())
    assert len(sidecar_data["agents"]) > 0, f"{framework}: sidecar has no agents"

    # Export back from the native dir
    final_cai = export_to_cai(native_dir, framework)
    orch = [a for a in final_cai["agents"] if a["slug"] == "orchestrator"]
    assert orch, f"{framework}: orchestrator not found in exported CAI"
    orch = orch[0]
    assert orch["handoffs"], (
        f"{framework}: orchestrator handoffs empty after round trip — "
        f"sidecar was not read back"
    )
    # Verify the handoff target
    targets = [h["to"] for h in orch["handoffs"]]
    assert "worker" in targets, (
        f"{framework}: handoff to 'worker' missing after round trip: {orch['handoffs']}"
    )
