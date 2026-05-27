"""Structural checks for the advisory-pr workflow (rc.6)."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/advisory-pr.yml")


def test_workflow_file_exists():
    assert WORKFLOW.exists()


def test_workflow_has_schedule_and_dispatch():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^\s+- cron:", text, re.MULTILINE), "missing cron"
    assert re.search(r"^\s+workflow_dispatch:", text, re.MULTILINE), "missing workflow_dispatch"


def test_workflow_permissions_minimal():
    text = WORKFLOW.read_text(encoding="utf-8")
    perms = re.search(r"^permissions:\n((?:[ \t].*\n)+)", text, re.MULTILINE)
    assert perms, "missing permissions block"
    body = perms.group(1)
    assert "contents: write" in body
    assert "pull-requests: write" in body
    granted = [
        ln for ln in body.splitlines()
        if re.match(r"\s+[a-z-]+:", ln) and not ln.lstrip().startswith("#")
    ]
    assert len(granted) == 2, f"expected exactly contents + pull-requests, got: {granted}"


def test_workflow_does_not_auto_merge():
    """rc.6 contract: advisory PRs await human review. The workflow must
    NOT call `gh pr merge`. (Distinct from framework-auto-update which
    DOES auto-merge its PRs.)
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    # Allow `gh pr merge` to appear in comments documenting the choice,
    # but never as a runnable command. Reject any non-comment line that
    # contains the command.
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        assert "gh pr merge" not in line, (
            f"advisory-pr workflow must not auto-merge; offending line: {line!r}"
        )


def test_workflow_applies_advisory_labels():
    """Both `advisory` and `awaiting-human` labels must be applied so
    operators can filter and so future reviewers see the intent.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '--label "advisory"' in text
    assert '--label "awaiting-human"' in text


def test_workflow_emits_step_summary():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "GITHUB_STEP_SUMMARY" in text
    assert "Advisory PR run" in text


def test_workflow_uses_dedup_branch_pattern():
    """advisory/<hash> branch convention; distinct from
    auto/framework-update-<hash> so the two PR types never collide."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'branch="advisory/$PHASH"' in text
    assert "auto/framework-update" not in text  # belongs to other workflow
