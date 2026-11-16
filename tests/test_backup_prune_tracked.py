"""An automatic backup prune never deletes git-tracked backups, and never runs silently (#541, 2026-11-16).

A v1.1.4 render in a downstream team deleted 280 tracked files under `.github/agents/.agentteams-backups/`:
`backup_before_projection` and `sync_roster` call `prune_backups` (keep the newest 10, `rmtree` the rest with errors
ignored) and drop its result. Now a backup directory holding a tracked file is never deleted and does not count
toward the window, what a prune did is printed, a failed deletion is recorded, and anything git can't answer
deletes nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import backup
from agentteams.backup import TRACKED_BACKUPS_KEPT, prune_backups, prune_report

_TRACKED = [f"20260101-0000{i:02d}" for i in range(1, 13)]     # 12, older
_UNTRACKED = [f"20260201-0000{i:02d}" for i in range(1, 4)]    # 3, newer


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo_with_backups(tmp_path: Path) -> Path:
    """A git repo whose agents dir holds 12 tracked and 3 untracked backup dirs; returns the agents dir."""
    repo = tmp_path / "repo"
    agents = repo / ".github" / "agents"
    root = agents / backup.BACKUP_DIR_NAME
    for name in _TRACKED + _UNTRACKED:
        (root / name).mkdir(parents=True)
        (root / name / "a.agent.md").write_text(f"backup {name}\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    for name in _TRACKED:
        _git(repo, "add", "-f", str(root / name))
    _git(repo, "commit", "-q", "-m", "track 12 backups")
    return agents


def _left(agents: Path) -> set[str]:
    return {p.name for p in (agents / backup.BACKUP_DIR_NAME).iterdir() if p.is_dir()}


def test_tracked_backups_all_survive_and_untracked_ones_are_pruned_to_the_keep_count(tmp_path: Path):
    agents = _repo_with_backups(tmp_path)
    result = prune_backups(agents, keep_last=2)
    assert set(_TRACKED) <= _left(agents), "a git-tracked backup was deleted"
    assert _left(agents) - set(_TRACKED) == set(_UNTRACKED[-2:])
    assert result.deleted == [_UNTRACKED[0]]
    assert sorted(result.kept_tracked) == _TRACKED   # all 12 lie beyond the old 2-backup window
    assert not result.failed and not result.tracking_unknown


def test_the_default_window_keeps_everything_here_and_names_the_tracked_ones_beyond_it(tmp_path: Path):
    agents = _repo_with_backups(tmp_path)
    result = prune_backups(agents)                   # keep_last=10 over 3 untracked: nothing to delete
    assert _left(agents) == set(_TRACKED + _UNTRACKED) and not result.deleted
    assert sorted(result.kept_tracked) == _TRACKED[:5]   # newest-first index 10..14 under the old rule


def test_outside_a_git_repo_the_prune_is_unchanged(tmp_path: Path):
    agents = tmp_path / "agents"
    for name in _TRACKED:
        (agents / backup.BACKUP_DIR_NAME / name).mkdir(parents=True)
    result = prune_backups(agents, keep_last=3)
    assert _left(agents) == set(_TRACKED[-3:]) and len(result.deleted) == 9 and not result.kept_tracked


def test_nothing_is_deleted_when_git_cannot_say_what_is_tracked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agents = _repo_with_backups(tmp_path)
    monkeypatch.setattr(backup, "_tracked_backup_names", lambda root: None)
    result = prune_backups(agents, keep_last=1)
    assert _left(agents) == set(_TRACKED + _UNTRACKED) and result.tracking_unknown and not result.deleted
    assert any("git could not list tracked files" in line for line in prune_report(result, agents))


def test_a_failed_deletion_is_recorded_not_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    agents = _repo_with_backups(tmp_path)

    def boom(path, *args, **kwargs):
        raise PermissionError(13, "denied", str(path))

    monkeypatch.setattr(shutil, "rmtree", boom)
    result = prune_backups(agents, keep_last=2)
    assert result.failed and result.failed[0].startswith(_UNTRACKED[0]) and not result.deleted
    assert any(line.startswith("could not prune backup") for line in prune_report(result, agents))


def test_the_automatic_prune_before_a_projection_reports_the_tracked_backups(tmp_path: Path, capsys):
    agents = _repo_with_backups(tmp_path)
    (agents / "a.agent.md").write_text("---\nname: a\n---\n", encoding="utf-8")
    backup.backup_before_projection(agents, "copilot-vscode")
    err = capsys.readouterr().err
    assert TRACKED_BACKUPS_KEPT in err
    assert set(_TRACKED) <= _left(agents)


def test_a_missing_git_binary_deletes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agentteams.git_exec as git_exec

    agents = _repo_with_backups(tmp_path)

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(git_exec, "run_git", no_git)
    result = prune_backups(agents, keep_last=1)
    assert result.tracking_unknown and not result.deleted and _left(agents) == set(_TRACKED + _UNTRACKED)


def test_a_broken_git_link_is_not_read_as_outside_a_repo(tmp_path: Path):
    """@adversarial #541: `not a git repository: <path>` (a pruned worktree) must not mean "nothing is tracked"."""
    agents = tmp_path / "wt" / ".github" / "agents"
    for name in _TRACKED:
        (agents / backup.BACKUP_DIR_NAME / name).mkdir(parents=True)
    (tmp_path / "wt" / ".git").write_text(f"gitdir: {tmp_path}/gone/.git/worktrees/wt\n", encoding="utf-8")
    result = prune_backups(agents, keep_last=1)
    assert result.tracking_unknown and not result.deleted and _left(agents) == set(_TRACKED)


def test_a_submodule_at_a_backup_path_counts_as_tracked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import agentteams.git_exec as git_exec

    agents = tmp_path / "agents"
    for name in _TRACKED[:3]:
        (agents / backup.BACKUP_DIR_NAME / name).mkdir(parents=True)
    gitlink = _TRACKED[0]   # oldest; `ls-files` prints a gitlink as a bare name with no "/"
    monkeypatch.setattr(git_exec, "run_git",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=f"{gitlink}\0", stderr=""))
    result = prune_backups(agents, keep_last=1)
    assert gitlink in _left(agents) and result.kept_tracked == [gitlink]


def test_kept_tracked_honours_keep_within_days(tmp_path: Path):
    agents = _repo_with_backups(tmp_path)
    result = prune_backups(agents, keep_last=1, keep_within_days=100000)   # every backup is inside the age window
    assert result.kept_tracked == [] and not result.deleted
