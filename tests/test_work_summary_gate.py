"""Regression tests: the daily work-summary capture is a reliable, blocking closeout
gate in every generated team — fenced (so it propagates via --update --merge),
conditional on executed work (read-only sessions skip), reachable for ad-hoc sessions,
and git-first (a commit-bearing day is never 'planning-only')."""
from __future__ import annotations

from pathlib import Path

import pytest

_EXPECTED = Path("examples/software-project/expected")
pytestmark = pytest.mark.skipif(
    not (_EXPECTED / "orchestrator.agent.md").exists(),
    reason="software-project example snapshot not present",
)


def _orch() -> str:
    return (_EXPECTED / "orchestrator.agent.md").read_text(encoding="utf-8")


def test_work_summary_capture_is_a_blocking_gate():
    orch = _orch()
    assert "Daily work-summary capture (closeout gate)" in orch
    # Blocking — same altitude as the security/hygiene/CI-CD gates, not a soft "invoke before closeout".
    assert "This blocks closeout: the session is not complete" in orch


def test_work_summary_gate_is_conditional_no_false_block():
    """A read-only / no-execution session must skip cleanly (no false closeout block)."""
    orch = _orch()
    assert "executed work" in orch
    assert "read-only / no-execution session skips this cleanly" in orch


def test_work_summary_gate_is_inside_the_available_workflows_fence():
    """Must be inside the fenced region so --update --merge propagates it to existing teams
    (the Constitutional Rules block is USER-EDITABLE and would NOT propagate)."""
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    gate = orch.index("Daily work-summary capture (closeout gate)")
    assert begin < gate < end


def test_final_check_is_reachable_for_ad_hoc_sessions():
    """Closes the reachability bypass: Workflow 11 must run at the close of ANY executed-work
    session, not only as the terminal of a numbered workflow."""
    orch = _orch()
    assert "did not enter a numbered workflow" in orch


def test_work_summary_runs_after_the_cicd_gate():
    """Ordering: capture is the terminal closeout act, after the CI/CD gate, so fix-commits
    from CI/CD remediation are recorded."""
    orch = _orch()
    cicd = orch.index("CI/CD deployment verification (closeout gate)")
    ws = orch.index("Daily work-summary capture (closeout gate)")
    assert cicd < ws  # CI/CD gate precedes the work-summary capture


def test_work_summarizer_is_git_first():
    ws = (_EXPECTED / "work-summarizer.agent.md").read_text(encoding="utf-8")
    assert "Git is the primary executed-work signal" in ws
    # a commit-bearing day is summarized regardless of plan-file presence/location
    assert "Any session with commits/merges to this repository requires a daily summary" in ws
    assert "absence never downgrades a commit-bearing day" in ws
