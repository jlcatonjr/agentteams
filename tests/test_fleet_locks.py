"""Tests for the fleet's release-lock and --fleet-exclude guards (agentteams/_fleet_locks.py, fleet.py).

Motivating incident: the 2026-11-09 weekly fleet run re-rendered repositories that release-lock agentteams in
``toolchain.lock`` with a different build, rolling their pinned agent teams back.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from agentteams import _fleet_locks, fleet
from agentteams.cli.fleet_switch import add_fleet_arguments

LOCK = ("# Toolchain pin\nresearchteam=jlcatonjr/researchteam@" + "a" * 40 + "\n"
        "agentteams=jlcatonjr/agentteams@" + "9b16fb54" + "c" * 32 + "\nagentteams.tag=v1.1.0\n")


def _mk_agent(dirpath: Path, name: str = "a.agent.md", body: str = "x\n") -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / name).write_text(body, encoding="utf-8")


def _ws(path: Path) -> Path:
    _mk_agent(path / ".github" / "agents", "_build-description.json", "{}")
    _mk_agent(path / ".github" / "agents")
    return path


def _init_repo(repo: Path) -> None:
    for args in (("init", "-q"), ("config", "user.email", "t@t.test"), ("config", "user.name", "t"),
                 ("add", "-A"), ("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "init", "--no-verify")):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


class _Args:
    def __init__(self, **kw):
        self.fleet = kw.get("fleet")
        self.fleet_frameworks = "both"
        self.fleet_report = kw.get("fleet_report")
        self.yes = kw.get("yes", False)
        self.dry_run = False
        self.shrink_policy = "preserve"
        self.fleet_allow_no_verify = False
        self.fleet_exclude = kw.get("fleet_exclude")
        self.fleet_ignore_locks = kw.get("fleet_ignore_locks")
        self.fleet_skip_parent = kw.get("fleet_skip_parent", False)


def _run(tmp_path, monkeypatch, **kw):
    ran: list[list[str]] = []
    monkeypatch.setattr(fleet, "_run_main", lambda argv: ran.append(argv) or (0, "ok"))
    report = tmp_path / "_report"
    fleet.run_fleet(_Args(fleet=str(tmp_path), fleet_report=str(report), **kw), parser=None)
    data = json.loads(next(report.glob("*/report.json")).read_text())
    rows = {Path(w["path"]).name: [t["target"] for t in w["targets"]] for w in data["workspaces"]}
    return ran, rows


# --- read_release_lock --------------------------------------------------------

def test_lock_at_repo_root_governs_a_nested_workspace(tmp_path):
    repo = tmp_path / "repo"
    nested = _ws(repo / "Projects" / "p1")
    (repo / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    _init_repo(repo)
    lock = _fleet_locks.read_release_lock(nested, tmp_path)
    assert lock == {"tag": "v1.1.0", "commit": "9b16fb54" + "c" * 32, "lock": str((repo / "toolchain.lock").resolve())}


def test_a_fleet_parent_below_the_repo_root_still_sees_the_root_lock(tmp_path):
    """Adversarial finding 1: `--fleet <repo>/Projects` must not stop the search at the parent."""
    repo = tmp_path / "repo"
    nested = _ws(repo / "Projects" / "p1")
    (repo / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    _init_repo(repo)
    assert _fleet_locks.read_release_lock(nested, repo / "Projects")["tag"] == "v1.1.0"


def test_run_fleet_below_a_locked_repo_root_skips_it(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _ws(repo / "Projects" / "p1")
    (repo / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    _init_repo(repo)
    ran: list = []
    monkeypatch.setattr(fleet, "_run_main", lambda argv: ran.append(argv) or (0, "ok"))
    fleet.run_fleet(_Args(fleet=str(repo / "Projects"), fleet_report=str(tmp_path / "_r"), yes=True), parser=None)
    assert ran == []


def test_a_nearer_lock_without_agentteams_does_not_hide_the_repo_pin(tmp_path):
    """Adversarial finding 2: a sub-tool's toolchain.lock is passed over."""
    repo = tmp_path / "repo"
    nested = _ws(repo / "tools" / "x")
    (nested / "toolchain.lock").write_text("othertool=foo@bar\n", encoding="utf-8")
    (repo / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    _init_repo(repo)
    assert _fleet_locks.read_release_lock(nested, tmp_path)["tag"] == "v1.1.0"


def test_a_lock_above_the_git_toplevel_is_not_applied(tmp_path):
    (tmp_path / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    repo = _ws(tmp_path / "repo")
    _init_repo(repo)
    assert _fleet_locks.read_release_lock(repo, tmp_path) is None


def test_non_git_search_stops_at_the_fleet_parent(tmp_path):
    (tmp_path / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    parent = tmp_path / "root"
    ws = _ws(parent / "plain")
    assert _fleet_locks.read_release_lock(ws, parent) is None
    (parent / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    assert _fleet_locks.read_release_lock(ws, parent)["tag"] == "v1.1.0"


def test_a_lock_without_an_agentteams_pin_is_not_a_release_lock(tmp_path):
    ws = _ws(tmp_path / "ws")
    (ws / "toolchain.lock").write_text("researchteam=jlcatonjr/researchteam@abc\n", encoding="utf-8")
    assert _fleet_locks.read_release_lock(ws, tmp_path) is None


def test_a_commit_only_lock_still_counts(tmp_path):
    ws = _ws(tmp_path / "ws")
    (ws / "toolchain.lock").write_text("agentteams=jlcatonjr/agentteams@deadbeef\n", encoding="utf-8")
    lock = _fleet_locks.read_release_lock(ws, tmp_path)
    assert lock["tag"] == "" and lock["commit"] == "deadbeef"
    assert "(deadbeef)" in _fleet_locks.lock_detail(lock)


def test_a_pin_without_an_at_sign_has_no_commit(tmp_path):
    ws = _ws(tmp_path / "ws")
    (ws / "toolchain.lock").write_text("agentteams=jlcatonjr/agentteams\nagentteams.tag=v1.0.4\n", encoding="utf-8")
    assert _fleet_locks.read_release_lock(ws, tmp_path) == {
        "tag": "v1.0.4", "commit": "", "lock": str((ws / "toolchain.lock").resolve())}


# --- excluded_by ----------------------------------------------------------------

def test_exclude_matches_relative_path_or_name():
    assert _fleet_locks.excluded_by(Path("agentteams-wt"), ["agentteams*"]) == "agentteams*"
    assert _fleet_locks.excluded_by(Path("vk/old-repo"), ["vk/old*"]) == "vk/old*"
    assert _fleet_locks.excluded_by(Path("vk/old-repo"), ["old-*"]) == "old-*"
    assert _fleet_locks.excluded_by(Path("vk/new"), ["old*", "agentteams*"]) is None
    with pytest.raises(TypeError):
        _fleet_locks.excluded_by(Path("a"), "agentteams*")


# --- discovery and run_fleet ----------------------------------------------------

def test_excluded_folder_is_not_descended_and_is_recorded(tmp_path):
    _ws(tmp_path / "keep")
    _ws(tmp_path / "agentteams-work")
    _ws(tmp_path / "agentteams-work" / "nested")
    excluded: list = []
    found = fleet.discover_workspaces(tmp_path, exclude=["agentteams*"], excluded=excluded)
    assert [p.name for p in found] == ["keep"]
    assert [(p.name, pat) for p, pat in excluded] == [("agentteams-work", "agentteams*")]


def test_the_parent_itself_is_never_excluded(tmp_path):
    parent = _ws(tmp_path / "agentteams-x")
    assert fleet.discover_workspaces(parent, exclude=["agentteams*"]) == [parent.resolve()]


def test_release_locked_workspace_gets_a_skip_row_and_no_update(tmp_path, monkeypatch):
    locked = _ws(tmp_path / "locked")
    (locked / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    _init_repo(locked)
    _ws(tmp_path / "free")
    ran, rows = _run(tmp_path, monkeypatch, yes=True)
    assert rows["locked"] == ["(release-locked)"]
    assert all(str(locked) not in " ".join(argv) for argv in ran)
    assert any(str(tmp_path / "free") in " ".join(argv) for argv in ran)
    # Skipped before the snapshot: no fleet-snapshot ref was written in the locked repo.
    refs = subprocess.run(["git", "-C", str(locked), "for-each-ref", fleet.SNAPSHOT_REF_PREFIX],
                          capture_output=True, text=True).stdout
    assert refs == ""


def test_ignore_locks_renders_only_the_matching_locked_workspace(tmp_path, monkeypatch):
    """Adversarial finding 3 (Rule 12): the opt-out is scoped by glob, not all-or-nothing."""
    for name in ("locked", "other"):
        (_ws(tmp_path / name) / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    ran, rows = _run(tmp_path, monkeypatch, fleet_ignore_locks=["locked"])
    assert "(release-locked)" not in rows["locked"] and rows["other"] == ["(release-locked)"]
    assert any(str(tmp_path / "locked") in " ".join(argv) for argv in ran)
    assert all(str(tmp_path / "other") not in " ".join(argv) for argv in ran)


def test_ignore_locks_matches_the_locked_repo_not_a_workspace_name(tmp_path, monkeypatch):
    """Rule 12 re-check: a workspace-name glob must not lift every repository's lock."""
    for repo in ("alpha", "beta"):
        root = tmp_path / repo
        _ws(root / "docs")
        (root / "toolchain.lock").write_text(LOCK, encoding="utf-8")
        _init_repo(root)
    ran, rows = _run(tmp_path, monkeypatch, fleet_ignore_locks=["docs"])
    assert ran == [] and rows["docs"] == ["(release-locked)"]
    ran, _ = _run(tmp_path, monkeypatch, fleet_ignore_locks=["alpha"])
    joined = " ".join(" ".join(a) for a in ran)
    assert str(tmp_path / "alpha" / "docs") in joined and str(tmp_path / "beta") not in joined


def test_run_fleet_reports_excluded_folders(tmp_path, monkeypatch):
    _ws(tmp_path / "agentteams-copy")
    _ws(tmp_path / "free")
    ran, rows = _run(tmp_path, monkeypatch, fleet_exclude=["agentteams*"])
    assert rows["agentteams-copy"] == ["(excluded)"]
    assert all("agentteams-copy" not in " ".join(argv) for argv in ran)


def test_cli_flags_parse():
    parser = argparse.ArgumentParser()
    add_fleet_arguments(parser)
    ns = parser.parse_args(["--fleet", "x", "--fleet-exclude", "a*", "--fleet-exclude", "b", "--fleet-ignore-locks", "*"])
    assert ns.fleet_exclude == ["a*", "b"] and ns.fleet_ignore_locks == ["*"]
    ns = parser.parse_args(["--fleet", "x"])
    assert ns.fleet_exclude is None and ns.fleet_ignore_locks is None


def test_skip_parent_leaves_the_parent_workspace_alone(tmp_path, monkeypatch):
    """--fleet-skip-parent: DIR's own (non-git) team gets a SKIP row; workspaces below still run."""
    _ws(tmp_path)
    _ws(tmp_path / "child")
    ran, _ = _run(tmp_path, monkeypatch, fleet_skip_parent=True)
    report = json.loads(next((tmp_path / "_report").glob("*/report.json")).read_text())
    rows = {w["path"]: [t["target"] for t in w["targets"]] for w in report["workspaces"]}
    assert rows[str(tmp_path.resolve())] == ["(parent-skipped)"]
    # Every target path (--project, and --output, which bridge targets use instead) is inside the child.
    paths = {Path(a[a.index(flag) + 1]).resolve() for a in ran for flag in ("--project", "--output") if flag in a}
    child = (tmp_path / "child").resolve()
    assert paths and all(p == child or child in p.parents for p in paths)


def test_without_skip_parent_the_parent_workspace_is_updated(tmp_path, monkeypatch):
    _ws(tmp_path)
    ran, _ = _run(tmp_path, monkeypatch)
    report = json.loads(next((tmp_path / "_report").glob("*/report.json")).read_text())
    assert all(t["target"] != "(parent-skipped)" for w in report["workspaces"] for t in w["targets"])
    assert ran



# --- @security row 692: fail-closed lock detection, visible lifts ----------------

def test_a_lock_path_that_is_not_a_regular_file_fails_closed(tmp_path):
    ws = _ws(tmp_path / "dir-lock")
    (ws / "toolchain.lock").mkdir()
    assert _fleet_locks.read_release_lock(ws, tmp_path)["tag"] == ""
    ws2 = _ws(tmp_path / "dangling")
    (ws2 / "toolchain.lock").symlink_to(ws2 / "missing")
    assert _fleet_locks.read_release_lock(ws2, tmp_path) is not None
    ws3 = _ws(tmp_path / "loop")
    (ws3 / "toolchain.lock").symlink_to(ws3 / "toolchain.lock")
    assert _fleet_locks.read_release_lock(ws3, tmp_path) is not None
    assert "skipped" in _fleet_locks.lock_detail(_fleet_locks.read_release_lock(ws, tmp_path))


def test_an_unparsable_lock_fails_closed(tmp_path):
    ws = _ws(tmp_path / "garbled")
    (ws / "toolchain.lock").write_text("agentteams v1.1.0\n", encoding="utf-8")
    assert _fleet_locks.read_release_lock(ws, tmp_path) is not None
    ws2 = _ws(tmp_path / "binary")
    (ws2 / "toolchain.lock").write_bytes(b"agentteams.tag=\xff\xfe\n")
    assert _fleet_locks.read_release_lock(ws2, tmp_path) is not None


def test_an_empty_planted_git_file_does_not_cut_the_search_short(tmp_path):
    repo = tmp_path / "repo"
    nested = _ws(repo / "Projects" / "p1")
    (repo / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    _init_repo(repo)
    (repo / "Projects" / ".git").write_text("", encoding="utf-8")
    assert _fleet_locks.read_release_lock(nested, tmp_path)["tag"] == "v1.1.0"


def test_a_gitdir_file_still_marks_a_toplevel(tmp_path):
    (tmp_path / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    sub = _ws(tmp_path / "sub")
    (sub / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")
    assert _fleet_locks.read_release_lock(sub, tmp_path) is None


def test_a_lifted_lock_is_a_review_row_per_workspace(tmp_path, monkeypatch):
    ws = _ws(tmp_path / "locked")
    (ws / "toolchain.lock").write_text(LOCK, encoding="utf-8")
    ran, _ = _run(tmp_path, monkeypatch, fleet_ignore_locks=["locked"])
    report = json.loads(next((tmp_path / "_report").glob("*/report.json")).read_text())
    rows = [t for w in report["workspaces"] for t in w["targets"] if t["target"] == "(lock-lifted)"]
    assert len(rows) == 1 and rows[0]["status"] == "REVIEW"
    assert "'locked'" in rows[0]["detail"] and "toolchain.lock" in rows[0]["detail"]
    assert ran


def test_fleet_locks_is_integrity_pinned():
    """Security row 692 item 4: an unpinned edit to the lock guard could quietly lift every lock."""
    from agentteams.integrity import ENFORCEMENT_MODULES
    assert "agentteams/_fleet_locks.py" in ENFORCEMENT_MODULES


def test_a_fifo_lock_fails_closed_without_blocking(tmp_path):
    """@security row-692 review: a FIFO named toolchain.lock is not a regular file, so it is never opened."""
    import os
    ws = _ws(tmp_path / "fifo")
    os.mkfifo(ws / "toolchain.lock")
    assert _fleet_locks.read_release_lock(ws, tmp_path) is not None
