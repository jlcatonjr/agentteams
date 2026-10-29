"""The pre-commit hook's API-impact clause: rendered in the refresh block, reports, never fails a commit."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import git_hooks

REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1"}


def test_clause_is_inside_the_refresh_block_before_its_exit():
    block = git_hooks._render_hook_block("/opt/at")
    clause = block.index("agentteams.api_impact --pre-commit")
    assert block.index(git_hooks._HOOK_BEGIN) < clause < block.index("exit $_at_rc")
    assert "python3 -P -m agentteams.api_impact" in block
    assert "git diff --cached --name-only 2>/dev/null)" in block  # unfiltered: deletions count
    assert "|| true" in block[clause:]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], env=_ENV, capture_output=True, text=True, check=False)


@pytest.mark.skipif(shutil.which("git") is None or sys.version_info < (3, 11), reason="git and Python 3.11+")
@pytest.mark.parametrize("at_path,expect", [(str(REPO_ROOT), "API impact"), ("/nonexistent", "api-impact")])
def test_commit_reports_impact_and_succeeds_even_when_the_check_fails(tmp_path: Path, at_path: str, expect: str):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "mod.py").write_text("def a():\n    pass\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "--no-verify", "-m", "base")
    git_hooks.install_pre_commit_hook(tmp_path, agentteams_path=at_path)
    (tmp_path / "mod.py").write_text("def a(x):\n    pass\n")
    _git(tmp_path, "add", "-A")
    proc = _git(tmp_path, "commit", "-q", "-m", "change a")
    assert proc.returncode == 0, proc.stderr
    if at_path == str(REPO_ROOT):
        assert expect in proc.stderr
    assert _git(tmp_path, "log", "--oneline").stdout.count("\n") == 2
