"""Tests for agentteams.session_scan (repo at-large issue scan, git subprocess fully mocked)."""

from __future__ import annotations

import json
import subprocess
import warnings
from pathlib import Path

import pytest

from agentteams import session_scan as ss


def _cp(rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=["git"], returncode=rc, stdout=stdout, stderr=stderr)


# ------------------------------ changelog ------------------------------

def test_scan_changelog_known_issues_skips_struck_and_stops_at_next_heading(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "### Known Issues / Bugs\n\n"
        "- ~~**BUG: fixed thing**~~ — resolved in this release.\n"
        "- **KNOWN ISSUE: still broken** — needs a fix.\n\n"
        "### Added\n\n"
        "- Some unrelated feature.\n",
        encoding="utf-8",
    )
    issues = ss._scan_changelog_known_issues(tmp_path)
    assert len(issues) == 1
    assert issues[0].source == "changelog"
    assert "still broken" in issues[0].detail


def test_scan_changelog_no_heading_returns_empty(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n### Added\n\n- Something.\n")
    assert ss._scan_changelog_known_issues(tmp_path) == []


def test_scan_changelog_missing_file_returns_empty(tmp_path):
    assert ss._scan_changelog_known_issues(tmp_path) == []


# ------------------------------ steps.csv ------------------------------

def test_scan_pending_blocked_steps_filters_by_status(tmp_path):
    d = tmp_path / "tmp" / "by-week" / "2026-W99"
    d.mkdir(parents=True)
    csv_path = d / "x.steps.csv"
    csv_path.write_text(
        "step,agent,action,inputs,outputs,status,notes\n"
        "1,navigator,do a thing,a,b,done,\n"
        "2,security,review it,a,b,pending,\n"
        "3,cleanup,tidy up,a,b,blocked,waiting on review\n",
        encoding="utf-8",
    )
    issues = ss._scan_pending_blocked_steps(tmp_path)
    assert len(issues) == 2
    assert {i.detail.split(":")[0] for i in issues} == {"security", "cleanup"}


def test_scan_pending_blocked_steps_excludes_given_paths(tmp_path):
    d = tmp_path / "tmp" / "by-week" / "2026-W99"
    d.mkdir(parents=True)
    csv_path = d / "excluded.steps.csv"
    csv_path.write_text(
        "step,agent,action,inputs,outputs,status,notes\n"
        "1,navigator,do a thing,a,b,pending,\n",
        encoding="utf-8",
    )
    issues = ss._scan_pending_blocked_steps(tmp_path, exclude_paths={csv_path})
    assert issues == []


def test_scan_pending_blocked_steps_warns_but_continues_on_overflow_row(tmp_path):
    d = tmp_path / "tmp"
    d.mkdir(parents=True)
    csv_path = d / "x.steps.csv"
    csv_path.write_text(
        "step,agent,action,inputs,outputs,status,notes\n"
        '1,navigator,"do a thing, with a stray comma",a,b,pending,extra,overflow\n',
        encoding="utf-8",
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        issues = ss._scan_pending_blocked_steps(tmp_path)
    assert any("more fields than the header" in str(w.message) for w in caught)
    assert len(issues) == 1


# ------------------------------ git status ------------------------------

def test_scan_git_status_flags_untracked_tmp_and_modified_outside_known(tmp_path):
    stdout = (
        "?? tmp/scratch-file.md\n"
        " M examples/software-project/expected/security.agent.md\n"
        " M agentteams/scan.py\n"
    )
    runner = lambda argv: _cp(stdout=stdout)
    issues = ss._scan_git_status(
        tmp_path,
        known_output_paths={"examples/software-project/expected/security.agent.md"},
        runner=runner,
    )
    paths = {i.path for i in issues}
    assert "tmp/scratch-file.md" in paths
    assert "agentteams/scan.py" in paths
    assert "examples/software-project/expected/security.agent.md" not in paths


def test_scan_git_status_ignores_untracked_outside_tmp(tmp_path):
    runner = lambda argv: _cp(stdout="?? some-new-file.md\n")
    issues = ss._scan_git_status(tmp_path, runner=runner)
    assert issues == []


def test_scan_git_status_returns_empty_on_git_failure(tmp_path):
    runner = lambda argv: _cp(rc=1, stderr="not a git repo")
    assert ss._scan_git_status(tmp_path, runner=runner) == []


def test_scan_git_status_passes_repo_root_to_runner(tmp_path):
    seen = {}
    def runner(argv):
        seen["argv"] = argv
        return _cp(stdout="")
    ss._scan_git_status(tmp_path, runner=runner)
    assert seen["argv"] == ["-C", str(tmp_path), "status", "--short"]


# ------------------------------ scan_repo_issues (integration) ------------------------------

def test_scan_repo_issues_combines_all_three_sources(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(
        "### Known Issues / Bugs\n\n- **KNOWN ISSUE: X** — needs a fix.\n\n### Added\n\n- Y\n"
    )
    d = tmp_path / "tmp" / "by-week" / "2026-W99"
    d.mkdir(parents=True)
    (d / "x.steps.csv").write_text(
        "step,agent,action,inputs,outputs,status,notes\n"
        "1,security,review it,a,b,pending,\n",
        encoding="utf-8",
    )
    runner = lambda argv: _cp(stdout="?? tmp/scratch.md\n")
    issues = ss.scan_repo_issues(tmp_path, runner=runner)
    sources = {i.source for i in issues}
    assert sources == {"changelog", "steps_csv", "git_status"}


# ------------------------------ main() CLI ------------------------------

def test_main_prints_json_and_returns_0(tmp_path, capsys, monkeypatch):
    (tmp_path / "CHANGELOG.md").write_text("### Known Issues / Bugs\n\n- **X** — y.\n")
    monkeypatch.setattr(ss, "_run_git", lambda argv: _cp(stdout=""))
    rc = ss.main([str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert isinstance(out, list)
    assert out and out[0]["source"] == "changelog"
