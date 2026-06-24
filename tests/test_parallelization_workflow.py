"""Tests for the Workflow 0A (Parallelization Analysis) orchestrator wiring.

These assert against the *templates* (the source of truth that propagates to
every generated team), so they do not depend on regenerated example fixtures.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES = REPO_ROOT / "agentteams" / "templates"
ORCH = TEMPLATES / "universal" / "orchestrator.template.md"
INSTR = TEMPLATES / "copilot-instructions.template.md"
REF = TEMPLATES / "universal" / "parallelization.reference.template.md"


def _orch() -> str:
    return ORCH.read_text(encoding="utf-8")


def test_workflow_0a_exists():
    assert "Workflow 0A: Parallelization Analysis" in _orch()


def test_workflow_0a_is_inside_available_workflows_fence():
    """Must be in the fenced region so --update --merge propagates it to existing teams."""
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    gate = orch.index("Workflow 0A: Parallelization Analysis")
    assert begin < gate < end


def test_per_member_conflict_audit_cadence_is_preserved():
    """The effect-audit must stay per member at wave join, not batched at wave end."""
    orch = _orch()
    assert "per member at wave join" in orch
    assert "@conflict-auditor" in orch


def test_destructive_steps_are_singleton_carveouts():
    orch = _orch()
    assert "singleton" in orch
    assert "--bridge-refresh" in orch


def test_pre_execution_requirement_mentions_optional_depends_on():
    orch = _orch()
    # inside the fenced Pre-Execution Requirement, the optional column is described
    assert "optional `depends_on`" in orch


def test_routing_row_is_inside_routing_fence():
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN routing_table_rows")
    end = orch.index("<!-- AGENTTEAMS:END routing_table_rows")
    row = orch.index("Parallel dispatch of independent plan steps")
    assert begin < row < end


def test_rule_16_is_outside_the_fence_new_teams_only():
    """Rule 16 is a non-load-bearing summary in the unfenced constitutional block."""
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    rule = orch.index("Independent work may proceed in parallel")
    assert rule < begin  # constitutional block precedes the first fence


def test_workflow_10_has_cross_plan_parallelization_scan():
    assert "Cross-plan parallelization scan" in _orch()


def test_copilot_instructions_mentions_optional_depends_on():
    assert "optional `depends_on`" in INSTR.read_text(encoding="utf-8")


def test_parallelization_reference_template_exists():
    assert REF.exists()
    body = REF.read_text(encoding="utf-8")
    assert "parallel_plan" in body
    assert "fail-safe" in body.lower()
