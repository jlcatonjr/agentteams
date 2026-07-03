"""Tests for scripts/batch_update.py — the multi-repo `--update --merge` driver.

Smoke coverage for the pure analysis helpers (`analyse_diff`, `diff_snapshots`)
that decide OK/WARN classification. The subprocess-driving `main()` is not
exercised here (it shells out to build_team.py across the whole repo tree).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "batch_update.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("batch_update", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bu = _load_module()


def test_analyse_diff_counts_added_and_removed():
    diff = (
        "diff --git a/.github/agents/x.agent.md b/.github/agents/x.agent.md\n"
        "--- a/.github/agents/x.agent.md\n"
        "+++ b/.github/agents/x.agent.md\n"
        "@@ -1,2 +1,2 @@\n"
        "-old substantive line here\n"
        "+new substantive line here\n"
        " context line unchanged\n"
    )
    result = bu.analyse_diff(diff)
    assert result["added_lines"] == 1
    assert result["removed_lines"] == 1
    assert ".github/agents/x.agent.md" in result["files_changed"]


def test_analyse_diff_flags_substantive_outside_fence_deletion():
    diff = (
        "diff --git a/.github/agents/x.agent.md b/.github/agents/x.agent.md\n"
        "+++ b/.github/agents/x.agent.md\n"
        "@@ -1,1 +0,0 @@\n"
        "-a meaningful hand-authored sentence that was removed\n"
    )
    result = bu.analyse_diff(diff)
    assert len(result["outside_fence_deletions"]) == 1


def test_analyse_diff_ignores_deletion_inside_agentteams_fence():
    # A deletion that occurs while inside an AGENTTEAMS-fenced block is expected
    # churn and must NOT be flagged as an outside-fence deletion.
    diff = (
        "diff --git a/.github/agents/x.agent.md b/.github/agents/x.agent.md\n"
        "+++ b/.github/agents/x.agent.md\n"
        "@@ -1,3 +1,2 @@\n"
        " <!-- AGENTTEAMS:BEGIN generated -->\n"
        "-generated content that lives inside the fence\n"
        " <!-- AGENTTEAMS:END generated -->\n"
    )
    result = bu.analyse_diff(diff)
    assert result["outside_fence_deletions"] == []


def test_diff_snapshots_reports_only_changed_files():
    pre = {"a.md": "one\ntwo\n", "b.md": "same\n"}
    post = {"a.md": "one\nCHANGED\n", "b.md": "same\n"}
    out = bu.diff_snapshots(pre, post)
    assert "a.md" in out
    assert "b.md" not in out
