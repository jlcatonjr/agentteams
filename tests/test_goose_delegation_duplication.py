"""A.1 regression test: goose synthesized-block duplication bug.

Report section 4.2: the synthesized "## Delegation & references (Goose)" body
block is not stripped before re-appending on re-render, so it compounds on
every native→canonical→native cycle for any non-orchestrator agent with its
own outgoing handoffs (a "depth-2" delegation shape). Reproduced: 616 → 955
→ 1294 bytes over 3 cycles on a worker1→worker2 chain.

This test creates a depth-2 delegation fixture (worker1 with its own handoff
to worker2) and asserts the rendered body size is stable across 3
native→canonical→native cycles for goose.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.interop import export_to_cai, import_from_cai


def _depth2_seed_cai() -> dict:
    """A CAI with a depth-2 delegation: orchestrator → worker1 → worker2.

    worker1 is a non-orchestrator agent with its own outgoing handoff to
    worker2 — the exact shape that hid the duplication bug from the existing
    test suite (whose fixture only has orchestrator→worker, a depth-1 shape).
    """
    return {
        "schema_version": "2.0",
        "created_at": "2026-08-12T00:00:00+00:00",
        "source_framework": "claude",
        "source_dir": "seed",
        "instructions_binding": {
            "source_name": "CLAUDE.md",
            "content": "# Project Instructions\n\nDEPTH2_SEED\n",
        },
        "agents": [
            {
                "slug": "orchestrator",
                "name": "Orchestrator",
                "description": "Routes work to worker1.",
                "body_markdown": "# Orchestrator\n\nDEPTH2_ORCH_TOKEN\n",
                "capabilities": {"tool_scopes": ["read", "edit", "agent"]},
                "handoffs": [{
                    "to": "worker1", "label": "Delegate to worker1",
                    "prompt": "", "send": False,
                }],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "orchestrator.md",
            },
            {
                "slug": "worker1",
                "name": "Worker One",
                "description": "Executes tasks and delegates to worker2.",
                "body_markdown": "# Worker One\n\nDEPTH2_W1_BODY_TOKEN doing the work.\n",
                "capabilities": {"tool_scopes": ["read", "execute"]},
                "handoffs": [{
                    "to": "worker2", "label": "Delegate to worker2",
                    "prompt": "", "send": False,
                }],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "worker1.md",
            },
            {
                "slug": "worker2",
                "name": "Worker Two",
                "description": "Does the actual work.",
                "body_markdown": "# Worker Two\n\nDEPTH2_W2_BODY_TOKEN.\n",
                "capabilities": {"tool_scopes": ["read"]},
                "handoffs": [],
                "invariant_core_markdown": None,
                "raw_front_matter": {},
                "source_path": "worker2.md",
            },
        ],
        "skills": [],
        "mcp_servers": [],
        "references": [],
        "framework_extensions": {"goose": {"recipe_extensions": ["developer"]}},
    }


def test_goose_delegation_block_does_not_compound_across_cycles(tmp_path: Path):
    """Render a depth-2 delegation CAI to goose, export back, repeat 3 times,
    and assert the worker1 recipe size is stable (no compounding duplication)."""
    seed = _depth2_seed_cai()

    # Build the initial native goose tree
    canon_dir = tmp_path / "canon-0"
    materialize_canonical(seed, canon_dir)
    loaded_cai = load_canonical(canon_dir)

    native_dir = tmp_path / "native-0" / ".goose" / "recipes"
    result = import_from_cai(loaded_cai, "goose", native_dir, overwrite=True)
    assert result.errors == [], f"initial goose import failed: {result.errors}"

    # Read worker1's recipe and count "## Delegation & references (Goose)" blocks
    w1_file = native_dir / "worker1.yaml"
    assert w1_file.exists(), "worker1.yaml was not produced"
    content_0 = w1_file.read_text()
    count_0 = content_0.count("## Delegation & references (Goose)")
    assert count_0 == 1, (
        f"cycle 0: expected exactly 1 Delegation block, found {count_0}"
    )

    # Now do 2 more native→canonical→native cycles and assert stability.
    # Note: cycle 0→1 may have a one-time normalization (goose's native format
    # has no slot for handoff `label`, which comes back as the bare slug —
    # documented coarseness in _LOSSY_FIELDS, not a duplication bug). The
    # duplication bug manifests as *compounding* growth; we assert the size
    # is stable from cycle 1 onward and that the block count stays at 1.
    prev_native_dir = native_dir
    for cycle in range(1, 3):
        # native → canonical
        cai = export_to_cai(prev_native_dir, "goose")
        canon_dir = tmp_path / f"canon-{cycle}"
        materialize_canonical(cai, canon_dir)
        loaded = load_canonical(canon_dir)

        # canonical → native
        new_native_dir = tmp_path / f"native-{cycle}" / ".goose" / "recipes"
        result = import_from_cai(loaded, "goose", new_native_dir, overwrite=True)
        assert result.errors == [], f"cycle {cycle} goose import failed: {result.errors}"

        w1_file = new_native_dir / "worker1.yaml"
        size_n = w1_file.stat().st_size
        content_n = w1_file.read_text()
        count_n = content_n.count("## Delegation & references (Goose)")

        assert count_n == 1, (
            f"cycle {cycle}: expected exactly 1 Delegation block, found {count_n} "
            f"(duplication compounding)"
        )
        # From cycle 1 onward, the size must be stable (no compounding).
        # Cycle 1 establishes the baseline; cycle 2 must match cycle 1.
        if cycle == 1:
            stable_size = size_n
        else:
            assert size_n == stable_size, (
                f"cycle {cycle}: worker1 recipe size changed: "
                f"{stable_size} → {size_n} bytes (duplication compounding)"
            )
        prev_native_dir = new_native_dir
