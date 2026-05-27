"""Structural checks for the pr-reminders workflow."""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/pr-reminders.yml")


def test_workflow_exists():
    assert WORKFLOW.exists()


def test_workflow_has_schedule_and_dispatch():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^\s+- cron:", text, re.MULTILINE), "missing cron"
    assert re.search(r"^\s+workflow_dispatch:", text, re.MULTILINE), "missing workflow_dispatch"


def test_workflow_permissions_minimal():
    """Reminders never write code — contents must be read-only; only
    pull-requests is writable. Plan-audit §K2."""
    text = WORKFLOW.read_text(encoding="utf-8")
    perms = re.search(r"^permissions:\n((?:[ \t].*\n)+)", text, re.MULTILINE)
    assert perms, "missing permissions block"
    body = perms.group(1)
    assert "contents: read" in body
    assert "pull-requests: write" in body
    assert "contents: write" not in body  # must NOT write code


def test_workflow_never_merges_or_pushes():
    text = WORKFLOW.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        assert "gh pr merge" not in line, f"reminders must not merge: {line!r}"
        assert "git push" not in line, f"reminders must not push: {line!r}"


def test_workflow_invokes_reminder_script():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/post_pr_reminders.py" in text


def test_workflow_supports_dry_run_input():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "dry_run:" in text
    assert "--dry-run" in text


def test_workflow_emits_step_summary():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "GITHUB_STEP_SUMMARY" in text
    assert "PR reminder run" in text


def test_workflow_exposes_interval_env():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "REMINDER_INTERVAL_HOURS" in text
