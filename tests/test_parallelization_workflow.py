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


# --- Workflow 0B: Coordinated Concurrency for Overlapping Work ---------------


def test_workflow_0b_exists():
    orch = _orch()
    assert "Workflow 0B" in orch
    assert "Coordinated Concurrency for Overlapping Work" in orch


def test_workflow_0b_is_inside_available_workflows_fence():
    """Must be fenced so --update --merge propagates it to existing teams."""
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    gate = orch.index("Workflow 0B: Coordinated Concurrency for Overlapping Work")
    assert begin < gate < end


def test_coordinated_concurrency_routing_row_is_inside_routing_fence():
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN routing_table_rows")
    end = orch.index("<!-- AGENTTEAMS:END routing_table_rows")
    row = orch.index("Coordinated concurrent dispatch of overlapping plan steps")
    assert begin < row < end


def test_workflow_0b_fences_are_at_v3():
    """Both fences carrying the 0B feature were bumped v=1 -> v=2 (0B), then v=2 -> v=3 when the
    spawn-authority query funnel added its routing rows + Workflows 12/13 to the same fences."""
    orch = _orch()
    assert "<!-- AGENTTEAMS:BEGIN routing_table_rows v=6" in orch
    assert "<!-- AGENTTEAMS:BEGIN available_workflows v=10" in orch  # v=6: Sandbox Operator Handoff; v=7: vulnscan PR review; v=8: Workflow 0C metaplan; v=9: Operator Decision Query; v=10: API Impact Review


def test_reference_template_has_coordinated_concurrency_sections():
    body = REF.read_text(encoding="utf-8")
    assert "## Coordinated concurrency for overlapping work" in body
    assert "When NOT to coordinate" in body
    assert "coordination ledger" in body
    assert "coordination_candidates" in body


def test_no_rule_18_added_to_constitutional_rules():
    """R4 guard: the numbered constitutional-rules list stops at 17; Rule 18 was dropped."""
    orch = _orch()
    # Scope to the unfenced constitutional-rules region, the same way the
    # Rule 16 test isolates that block: it runs from the section heading up to
    # the next fenced region (authority_hierarchy).
    region_start = orch.index("### Constitutional Rules")
    region_end = orch.index("<!-- AGENTTEAMS:BEGIN authority_hierarchy")
    region = orch[region_start:region_end]
    assert "17." in region  # sanity: the list really does run to 17 here
    # No numbered rule 18 anywhere in the region (line-anchored and inline).
    assert "18. " not in region
    assert "\n18." not in region


def test_workflow_0c_metaplan_is_fenced_and_routed():
    """Workflow 0C must sit in the fenced workflows region and have a routing row, so
    --update --merge carries it to existing teams."""
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    assert begin < orch.index("### Workflow 0C: Complex-Task Metaplan") < end
    rbegin = orch.index("<!-- AGENTTEAMS:BEGIN routing_table_rows")
    rend = orch.index("<!-- AGENTTEAMS:END routing_table_rows")
    assert rbegin < orch.index("`@orchestrator` → Workflow 0C") < rend
    assert "steps 5–8 run as **Workflow 0C (Complex-Task Metaplan)** instead" in orch


def test_workflow_0c_audits_every_target_plan_before_implementing():
    orch = _orch()
    wf = orch[orch.index("### Workflow 0C"):orch.index("### Pre-Execution Security Check")]
    assert "**and every\nplan it targets**" in wf
    assert "`<meta-slug>.steps.csv`" in wf
    assert "**runs alone in its own round**" in wf
    assert "python -m agentteams.parallel_plan <every item .steps.csv>" in wf
    # Audit and revision precede implementation, and implementation keeps the per-step cadence.
    assert wf.index("**Audit the metaplan and every target plan.**") < wf.index("**Revise.**") \
        < wf.index("**Implement the metaplan.**")
    assert "per member and one `@adversarial` pass over the remaining steps" in wf


def test_reference_template_documents_metaplans():
    body = REF.read_text(encoding="utf-8")
    assert "## Metaplans: several plans in one session (Workflow 0C)" in body
    assert "genuine cross-plan concurrency needs a separate substrate" not in body


def test_operator_decision_query_is_fenced_routed_and_wired():
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    assert begin < orch.index("### Operator Decision Query") < end
    assert "`@orchestrator` → Operator Decision Query" in orch
    proto = orch[orch.index("### Operator Decision Query"):orch.index("### Sandbox Operator Handoff")]
    assert "`AskUserQuestion`" in proto and "`(Recommended)`" in proto
    assert "The agent holding the operator's session asks" in proto
    assert "An approval (waiver, trust root" in proto  # never assumed when no operator is present
    # Workflows 0, 0C, 12 and 14 all hand their operator decisions to it.
    assert orch.count("**Operator Decision Query**") >= 5


def test_operator_decision_reference_is_emitted_and_fenced():
    from agentteams import output_plan
    ref = (TEMPLATES / "universal" / "operator-decision.reference.template.md").read_text(encoding="utf-8")
    assert "<!-- AGENTTEAMS:BEGIN operator_decision v=1 -->" in ref
    assert "<!-- AGENTTEAMS:END operator_decision -->" in ref
    for tool in ("`AskUserQuestion`", "`vscode/askQuestions`", "`ask_user`", "`request_user_input`"):
        assert tool in ref
    assert "numbered list" in ref and "needs_user_review=yes" in ref
    assert "references/operator-decision.reference.md" in Path(output_plan.__file__).read_text(encoding="utf-8")
