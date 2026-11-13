"""test_redteam_f3_exemptions.py — evidence for F-3 exemptions recorded on 2026-11-12 (#50).

``references/redteam-canonical-resolution-exemptions.csv`` exempts ``vulnscan.project_policy.repo_dir`` and
``vulnscan.registry.pick_fix_checkout`` from F-3's ``vcs-status`` construct. These tests hold the reasons given
there: ``repo_dir`` finds a linked worktree (whose ``.git`` is a file) and stops at the nearest repository,
and ``pick_fix_checkout`` prefers a main checkout over a linked worktree.
"""
from __future__ import annotations

from pathlib import Path

from agentteams.vulnscan.project_policy import repo_dir
from agentteams.vulnscan.registry import pick_fix_checkout


def test_repo_dir_finds_a_linked_worktree(tmp_path: Path) -> None:
    """A linked worktree's .git is a file; repo_dir still returns the worktree, not the scan root."""
    wt = tmp_path / "wt"
    (wt / "pkg").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert repo_dir(wt / "pkg" / "package-lock.json", tmp_path) == wt


def test_repo_dir_stops_at_the_nearest_repository(tmp_path: Path) -> None:
    """A nested repository wins over its parent, and a path in no repository falls back to the root."""
    outer, inner = tmp_path / "outer", tmp_path / "outer" / "vendor" / "inner"
    (inner / "src").mkdir(parents=True)
    (outer / ".git").mkdir()
    (inner / ".git").mkdir()
    assert repo_dir(inner / "src" / "requirements.txt", tmp_path) == inner
    assert repo_dir(outer / "vendor" / "x.txt", tmp_path) == outer
    (tmp_path / "loose").mkdir()
    assert repo_dir(tmp_path / "loose" / "x.txt", tmp_path) == tmp_path


def test_pick_fix_checkout_prefers_the_main_checkout(tmp_path: Path) -> None:
    """Of a main checkout and a shorter-path linked worktree, the main checkout (.git directory) is chosen."""
    main, wt = tmp_path / "longer-main-checkout", tmp_path / "wt"
    (main / ".git").mkdir(parents=True)
    wt.mkdir()
    (wt / ".git").write_text("gitdir: x\n")
    assert pick_fix_checkout([wt, main]) == main
