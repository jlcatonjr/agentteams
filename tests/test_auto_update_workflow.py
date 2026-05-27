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


def test_workflow_applies_framework_update_label():
    """T5.2 / IV.2 + rc.4: auto-PRs must be labeled framework-update so
    the operator can filter on the discovery surface. The automerge:false
    label was removed in rc.4 when the daily pipeline began implementing
    revisions automatically; the workflow now merges its own PR
    immediately after creation.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert '--label "framework-update"' in text
    assert '--label "automerge:false"' not in text, (
        "rc.4 removed the automerge:false label; the workflow auto-merges its own PR"
    )


def test_workflow_auto_merges_its_own_pr():
    """rc.4: the workflow runs `gh pr merge` immediately after creating
    the PR. CI does not fire on GITHUB_TOKEN-created PRs (GitHub's
    infinite-loop safeguard), so there is no CI gate to await.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'gh pr merge "$pr_num" --merge --delete-branch' in text


def test_workflow_emits_post_execution_step_summary():
    """rc.4: GITHUB_STEP_SUMMARY captures the PR URL, hash, merge commit,
    and the merged diff so each run log records exactly what landed on main.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'GITHUB_STEP_SUMMARY' in text
    assert 'Auto-update execution report' in text
    assert 'gh pr diff' in text


def test_workflow_dispatches_ci_after_automerge():
    """rc.5: after the auto-merge lands the commit on main, the workflow
    must dispatch ci.yml against main. workflow_dispatch is exempt from
    the GITHUB_TOKEN filter, so this restores the post-merge safety net
    that the rc.4 CHANGELOG claimed but did not actually deliver.
    """
    text = WORKFLOW.read_text(encoding="utf-8")
    # The dispatch must come AFTER the merge (substring order check).
    merge_idx = text.find('gh pr merge "$pr_num" --merge --delete-branch')
    dispatch_idx = text.find('gh workflow run ci.yml --ref main')
    assert merge_idx >= 0, "merge step missing"
    assert dispatch_idx >= 0, "ci.yml dispatch step missing"
    assert dispatch_idx > merge_idx, (
        "CI dispatch must run AFTER the merge so it tests the merge commit"
    )


def test_ci_workflow_accepts_dispatch():
    """rc.5: ci.yml must declare workflow_dispatch so the framework-auto-update
    workflow can fire it programmatically. Without this trigger the
    dispatch in framework-auto-update is a no-op.
    """
    ci_path = WORKFLOW.parent / "ci.yml"
    text = ci_path.read_text(encoding="utf-8")
    import re as _re
    on_block = _re.search(r"^on:\n((?:[ \t#].*\n)+)", text, _re.MULTILINE)
    assert on_block, "ci.yml has no on: block"
    assert _re.search(r"^\s*workflow_dispatch:\s*$", on_block.group(1), _re.MULTILINE), \
        "ci.yml must include workflow_dispatch under on:"


def test_workflow_targets_only_main_via_pr():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--base main" in text
    assert "ref: main" in text
    assert "git push --set-upstream origin \"$BRANCH\"" in text
