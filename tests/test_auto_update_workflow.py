"""Structural checks for the framework-auto-update workflow (T2.D3).

Uses plain string assertions (matching the style of test_bridge_automation.py)
to avoid adding PyYAML as a test dependency. Anything that genuinely needs
structured YAML access can come later via a test extras group.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/framework-auto-update.yml")


def test_workflow_file_exists():
    assert WORKFLOW.exists()


def test_workflow_top_level_keys_present():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.startswith("name: Framework Auto-Update")
    assert re.search(r"^on:\s*$", text, re.MULTILINE)
    assert re.search(r"^permissions:\s*$", text, re.MULTILINE)
    assert re.search(r"^jobs:\s*$", text, re.MULTILINE)


def test_schedule_enabled_after_t3a1():
    """T3a.1 (2026-05-25): branch protection was set on `main` and the cron
    trigger is now active. Re-disabling cron should be a deliberate edit.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    # The `on:` block must include a non-commented `schedule:` entry with a cron line.
    on_block = re.search(r"^on:\n((?:[ \t].*\n)+)", text, re.MULTILINE)
    assert on_block, "missing on: block"
    on_body = on_block.group(1)
    assert re.search(r"^\s+schedule:\s*$", on_body, re.MULTILINE), \
        "T3a.1 activated cron; expected `schedule:` under `on:`"
    assert re.search(r'^\s+- cron:\s*"[^"]+"\s*$', on_body, re.MULTILINE), \
        "schedule must define a cron entry"
    assert re.search(r"^\s+workflow_dispatch:\s*$", on_body, re.MULTILINE), \
        "workflow_dispatch must remain available for manual runs"


def test_workflow_uses_explicit_ci_marker():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "AGENTTEAMS_ALLOW_CI_APPLY" in text
    assert "auto/framework-update-" in text  # branch naming convention


def test_workflow_minimal_permissions():
    text = WORKFLOW.read_text(encoding="utf-8")
    perms = re.search(r"^permissions:\n((?:[ \t].*\n)+)", text, re.MULTILINE)
    assert perms, "missing permissions: block"
    body = perms.group(1)
    assert re.search(r"^\s+contents:\s+write\s*$", body, re.MULTILINE)
    assert re.search(r"^\s+pull-requests:\s+write\s*$", body, re.MULTILINE)
    # Reject any other granted permissions.
    permission_lines = [
        ln for ln in body.splitlines()
        if re.match(r"\s+[a-z-]+:", ln) and not ln.lstrip().startswith("#")
    ]
    assert len(permission_lines) == 2, \
        f"expected exactly contents+pull-requests, got: {permission_lines}"


def test_workflow_applies_supervised_labels():
    """T5.2 / IV.2: auto-PRs must be labeled framework-update and
    automerge:false so the operator can filter on the discovery surface
    and so future reviewers can't confuse them with regular PRs.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '--label "framework-update"' in text
    assert '--label "automerge:false"' in text


def test_workflow_targets_only_main_via_pr():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--base main" in text
    assert "ref: main" in text
    assert "git push --set-upstream origin \"$BRANCH\"" in text
