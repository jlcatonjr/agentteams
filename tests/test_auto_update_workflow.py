"""Structural checks for the framework-auto-update workflow (T2.D3)."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(".github/workflows/framework-auto-update.yml")


def test_workflow_file_exists():
    assert WORKFLOW.exists()


def test_workflow_yaml_parses():
    yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_schedule_enabled_after_t3a1():
    """T3a.1 (2026-05-25): branch protection on main was set and verified,
    so the schedule trigger is now active. Disabling it again should be a
    deliberate decision documented in a plan.
    """
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = doc.get("on", doc.get(True, {}))
    assert isinstance(triggers, dict)
    assert "workflow_dispatch" in triggers
    assert "schedule" in triggers, "T3a.1 activated cron; expected `schedule:` in `on:`"
    schedule_entries = triggers["schedule"]
    assert any("cron" in entry for entry in schedule_entries), "schedule must define cron"


def test_workflow_uses_explicit_ci_marker():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "AGENTTEAMS_ALLOW_CI_APPLY" in text
    assert "auto/framework-update-" in text  # branch naming convention


def test_workflow_minimal_permissions():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    perms = doc.get("permissions", {})
    assert perms.get("contents") == "write"
    assert perms.get("pull-requests") == "write"
    # No other permissions granted.
    assert set(perms.keys()) <= {"contents", "pull-requests"}


def test_workflow_targets_only_main_via_pr():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--base main" in text
    assert "ref: main" in text
    # The push step must target the auto branch, never main directly.
    assert "git push --set-upstream origin \"$BRANCH\"" in text
