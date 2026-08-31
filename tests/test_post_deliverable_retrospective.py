"""Regression tests: the Post-Deliverable Retrospective subroutine is wired into the
generated orchestrator (Workflows 1/2/3, never Workflow 4), the repo-liaison agent gets
a same-repo, no-@security Protocol 5, and the new reference doc exists and is emitted."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.liaison_logs import (
    AGENTTEAMS_REMEDIATION_CSV,
    AGENTTEAMS_REMEDIATION_HEADERS,
    init_csv_stubs,
)

_EXPECTED = Path("examples/software-project/expected")
pytestmark = pytest.mark.skipif(
    not (_EXPECTED / "orchestrator.agent.md").exists(),
    reason="software-project example snapshot not present",
)


def _orch() -> str:
    return (_EXPECTED / "orchestrator.agent.md").read_text(encoding="utf-8")


def _liaison() -> str:
    return (_EXPECTED / "repo-liaison.agent.md").read_text(encoding="utf-8")


def _reference() -> str:
    return (_EXPECTED / "references" / "retrospective-remediation.reference.md").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# orchestrator.template.md — subroutine placement and wiring
# ---------------------------------------------------------------------------

def test_subroutine_is_inside_the_available_workflows_fence():
    """Must be inside the fenced region so --update --merge propagates it to existing teams
    (the Constitutional Rules block is USER-EDITABLE and would NOT propagate)."""
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    subroutine = orch.index("### Post-Deliverable Retrospective")
    assert begin < subroutine < end


def test_workflow_1_and_2_invoke_it_before_standard_doc_sync_closeout():
    orch = _orch()
    workflow_1 = orch.index("### Workflow 1: Produce a Deliverable")
    workflow_2 = orch.index("### Workflow 2: Revise a Deliverable")
    workflow_3 = orch.index("### Workflow 3: Technical Accuracy Audit")
    workflow_1_text = orch[workflow_1:workflow_2]
    workflow_2_text = orch[workflow_2:workflow_3]
    assert "→ **Post-Deliverable Retrospective**" in workflow_1_text
    assert "→ **Standard Doc-Sync Closeout**" not in workflow_1_text
    assert "→ **Post-Deliverable Retrospective**" in workflow_2_text
    assert "→ **Standard Doc-Sync Closeout**" not in workflow_2_text


def test_workflow_3_corrections_made_branch_invokes_it():
    orch = _orch()
    workflow_3 = orch.index("### Workflow 3: Technical Accuracy Audit")
    workflow_4 = orch.index("### Workflow 4: Compile Final Output")
    workflow_3_text = orch[workflow_3:workflow_4]
    assert "If any corrections were made → **Post-Deliverable Retrospective**" in workflow_3_text


def test_workflow_4_does_not_invoke_it():
    """Workflow 4 assembles already-retrospected deliverables and has no audit chain of its
    own — retrospecting again there would double-count (see plan Design section 1)."""
    orch = _orch()
    workflow_4 = orch.index("### Workflow 4: Compile Final Output")
    workflow_5 = orch.index("### Workflow 5: Consistency Review")
    workflow_4_text = orch[workflow_4:workflow_5]
    assert "Post-Deliverable Retrospective" not in workflow_4_text
    assert "→ **Invoke Workflow 11: Final Check**" in workflow_4_text


def test_subroutine_states_its_own_exclusions_explicitly():
    """Adversarial finding: don't exclude Workflow 4 / Workflows 5-10 by silence."""
    orch = _orch()
    subroutine_start = orch.index("### Post-Deliverable Retrospective")
    subroutine_end = orch.index("### Standard Doc-Sync Closeout")
    subroutine_text = orch[subroutine_start:subroutine_end]
    assert "Does not apply" in subroutine_text
    assert "Workflow 4" in subroutine_text
    assert "Workflows 5" in subroutine_text or "Workflows 5–10" in subroutine_text


def test_subroutine_has_ad_hoc_reachability_clause():
    """Closes the original work-summarizer-trigger bypass class: must fire even for ad-hoc
    sessions that never literally enter Workflow 1/2/3's numbered steps."""
    orch = _orch()
    subroutine_start = orch.index("### Post-Deliverable Retrospective")
    subroutine_end = orch.index("### Standard Doc-Sync Closeout")
    subroutine_text = orch[subroutine_start:subroutine_end]
    assert "ad hoc" in subroutine_text.lower() or "ad-hoc" in subroutine_text.lower()
    assert "without literally entering Workflow 1/2/3" in subroutine_text


def test_subroutine_has_no_op_path_for_empty_lists():
    """An ordinary session with nothing to report must be cheap, not a forced two-list dump."""
    orch = _orch()
    assert "No retrospective items this session" in orch


def test_subroutine_content_safety_check_is_folded_into_conflict_auditor_step():
    """Adversarial finding: no new @security gate for the common case — content-safety check
    lives inside the existing @conflict-auditor step instead."""
    orch = _orch()
    subroutine_start = orch.index("### Post-Deliverable Retrospective")
    subroutine_end = orch.index("### Standard Doc-Sync Closeout")
    subroutine_text = orch[subroutine_start:subroutine_end]
    assert "formula-injection" in subroutine_text
    assert "@security" in subroutine_text  # escalation path still named, for the edge case


def test_constitutional_rule_17_points_to_reference_doc():
    orch = _orch()
    assert "17. **Post-Deliverable Retrospective**" in orch
    assert "references/retrospective-remediation.reference.md" in orch


# ---------------------------------------------------------------------------
# repo-liaison.template.md — Protocol 5
# ---------------------------------------------------------------------------

def test_repo_liaison_protocol_5_present_and_cites_protocol_3():
    liaison = _liaison()
    assert "Protocol 5: Log AgentTeamsModule Remediation Items" in liaison
    protocol_5_start = liaison.index("Protocol 5: Log AgentTeamsModule Remediation Items")
    protocol_5_text = liaison[protocol_5_start:]
    assert "Protocol 3" in protocol_5_text


def test_repo_liaison_protocol_5_does_not_require_security():
    """It's a local, same-repo log append — not a cross-repository write — so it must not
    trigger the Invariant Core's 'never write to an adjacent repository without @security'
    rule the way Protocol 2 does."""
    liaison = _liaison()
    protocol_5_start = liaison.index("Protocol 5: Log AgentTeamsModule Remediation Items")
    protocol_2_start = liaison.index("Protocol 2: Update Adjacent Repository Documentation")
    protocol_3_start = liaison.index("Protocol 3: Orchestrator-to-Orchestrator Coordination")
    protocol_2_text = liaison[protocol_2_start:protocol_3_start]
    protocol_5_text = liaison[protocol_5_start:]
    # Protocol 2 (the actual cross-repo write) does invoke @security — sanity check the fixture.
    assert "@security" in protocol_2_text
    # Protocol 5 explicitly says it does not.
    assert "Do not invoke `@security`" in protocol_5_text


def test_repo_liaison_purpose_mentions_logging():
    liaison = _liaison()
    assert "**Log**" in liaison


# ---------------------------------------------------------------------------
# retrospective-remediation.reference.md — new reference doc
# ---------------------------------------------------------------------------

def test_reference_doc_exists_and_is_emitted():
    ref = _reference()
    assert "Category Definitions" in ref
    assert "CSV Schema" in ref


def test_reference_doc_states_self_referential_destination_exception():
    """Conflict-auditor's critical finding: the generic <output_dir>/references/ mechanism
    resolves to a gitignored path for AgentTeamsModule's own dogfood output — must be
    special-cased to the top-level references/ instead, and that exception must be spelled
    out so a future reader doesn't 'fix' it back to the generic path."""
    ref = _reference()
    assert "Self-referential exception" in ref
    assert "top-level" in ref
    assert "gitignored" in ref.lower()


def test_reference_doc_cross_links_agent_updater_and_repo_liaison():
    ref = _reference()
    assert "@repo-liaison" in ref
    assert "@agent-updater" in ref


# ---------------------------------------------------------------------------
# liaison_logs.py — the 4th CSV
# ---------------------------------------------------------------------------

def test_init_csv_stubs_creates_agentteams_remediation_log(tmp_path):
    refs = tmp_path / "references"
    created = init_csv_stubs(refs)
    assert AGENTTEAMS_REMEDIATION_CSV in created
    assert (refs / AGENTTEAMS_REMEDIATION_CSV).exists()


def test_agentteams_remediation_csv_is_emitted_for_generated_projects():
    csv_path = _EXPECTED / "references" / AGENTTEAMS_REMEDIATION_CSV
    assert csv_path.exists()
    header = csv_path.read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(AGENTTEAMS_REMEDIATION_HEADERS)
