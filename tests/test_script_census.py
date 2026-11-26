"""test_script_census.py — ``python -m agentteams.script_census``: inventory scripts and mark copies.

The fixture tree is small and built per test; no test walks a real tree. It holds one of each thing
the inventory must leave out (a venv, a nested checkout, a symbolic link pointing outside the root,
an oversize file, a non-script) and one of each copy kind (a worktree copy, a byte-identical copy of
a tracked file).
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import script_census as sc

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def _git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    """A repository with tracked tools, loose scripts, and everything the inventory must leave out."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "tools" / "report.py").write_text("print('report')\n", encoding="utf-8")
    (repo / "tools" / "twin.py").write_text("print('report')\n", encoding="utf-8")       # tracked, same bytes
    (repo / "tmp" / "week1").mkdir(parents=True)
    (repo / "tmp" / "week1" / "report_copy.py").write_text("print('report')\n", encoding="utf-8")  # untracked copy
    (repo / "tmp" / "week1" / "query.sh").write_text("#!/bin/sh\necho hi", encoding="utf-8")       # no final newline
    (repo / "tmp" / "week1" / "runme").write_text("#!/usr/bin/env python3\n", encoding="utf-8")    # shebang, no ext
    (repo / "tmp" / "week1" / "README").write_text("notes\n", encoding="utf-8")                    # no ext, no shebang
    (repo / "tmp" / "week1" / "data.csv").write_text("a,b\n", encoding="utf-8")
    (repo / "tmp" / "week1" / "big.py").write_text("x = 1\n" * 300, encoding="utf-8")
    (repo / "tmp" / ".venv" / "lib").mkdir(parents=True)
    (repo / "tmp" / ".venv" / "lib" / "site.py").write_text("pass\n", encoding="utf-8")
    (repo / "tmp" / "vendored" / ".git").mkdir(parents=True)                                       # nested checkout
    (repo / "tmp" / "vendored" / "tool.py").write_text("pass\n", encoding="utf-8")
    (repo / "tmp" / "subproject").mkdir()
    (repo / "tmp" / "subproject" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")   # nested project
    (repo / "tmp" / "subproject" / "main.py").write_text("pass\n", encoding="utf-8")
    (repo / "tmp" / ".worktrees" / "wt").mkdir(parents=True)
    (repo / "tmp" / ".worktrees" / "wt" / "old.py").write_text("print('old')\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    (repo / "tmp" / "week1" / "link.py").symlink_to(outside / "secret.py")
    (repo / "tmp" / "linkdir").symlink_to(outside, target_is_directory=True)
    _git(repo, "init", "-q")
    _git(repo, "add", "tools")
    return repo


def test_inventory_lists_scripts_and_counts_everything_left_out(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tree)
    rows, pruned = sc.inventory(["tmp"], max_bytes=1000)
    assert [r["path"] for r in rows] == [".worktrees/wt/old.py", "week1/query.sh", "week1/report_copy.py", "week1/runme"]
    assert dict(pruned) == {"pruned-dir": 1, "nested-checkout": 2, "symlink": 2, "oversize": 1, "other-extension": 2}
    by_path = {r["path"]: r for r in rows}
    body = b"#!/bin/sh\necho hi"
    assert by_path["week1/query.sh"] == {
        "path": "week1/query.sh", "size": str(len(body)), "lines": "2", "ext": ".sh", "root": "tmp",
        "sha256": hashlib.sha256(body).hexdigest()}
    assert by_path["week1/runme"]["ext"] == "" and by_path["week1/report_copy.py"]["lines"] == "1"
    assert all(set(r) == set(sc.INVENTORY_COLUMNS) for r in rows)


def test_a_symlink_is_never_read_and_the_output_is_stable(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tree)
    first = sc.inventory(["tmp", "tools"])
    assert first == sc.inventory(["tmp", "tools"])
    rows, _ = first
    assert not any("secret" in r["path"] or "link" in r["path"] for r in rows)
    secret = hashlib.sha256(b"SECRET = 1\n").hexdigest()
    assert secret not in {r["sha256"] for r in rows}
    assert [r["root"] for r in rows] == sorted((r["root"] for r in rows), key=["tmp", "tools"].index)


def test_extensions_can_be_chosen_and_a_bad_root_is_refused(tree: Path) -> None:
    rows, pruned = sc.inventory([tree / "tmp" / "week1"], extensions=[".csv"])
    assert [r["path"] for r in rows] == ["data.csv", "runme"] and pruned["other-extension"] == 4
    with pytest.raises(sc.CensusError, match="not a directory"):
        sc.inventory([tree / "nope"])


def test_mark_copies_names_each_kind(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tree)
    rows, _ = sc.inventory(["tmp", "tools"], max_bytes=1000)
    (tree / "tmp" / "week1" / "runme").unlink()
    kinds = {f"{r['root']}/{r['path']}": r["copy_kind"] for r in sc.mark_copies(rows, repo=tree)}
    assert kinds == {
        "tmp/.worktrees/wt/old.py": "snapshot",
        "tmp/week1/query.sh": "authored",
        "tmp/week1/report_copy.py": "tracked-duplicate",   # untracked, bytes equal tools/report.py
        "tmp/week1/runme": "missing",
        "tools/report.py": "authored",                     # tracked twins are both authored
        "tools/twin.py": "authored",
    }
    extra = {f"{r['root']}/{r['path']}": r["copy_kind"]
             for r in sc.mark_copies(rows, repo=tree, snapshot_markers=(*sc.SNAPSHOT_MARKERS, "week*"))}
    assert extra["tmp/week1/query.sh"] == "snapshot"


def test_mark_copies_refuses_what_is_not_a_repository_or_not_an_inventory(tree: Path, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(sc.CensusError, match="not a git repository"):
        sc.mark_copies([{"path": "a.py", "root": str(plain)}], repo=plain)
    with pytest.raises(sc.CensusError, match="not an inventory"):
        sc.mark_copies([{"file": "a.py"}], repo=tree)


def test_git_is_not_redirected_by_the_environment(tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert sorted(p for paths in sc.tracked_blobs(tree).values() for p in paths) == ["tools/report.py", "tools/twin.py"]


def test_cli_pipes_inventory_into_mark_copies(tree: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(tree)
    assert sc.main(["inventory", "tmp", "--max-bytes", "1000"]) == 0
    inv = capsys.readouterr()
    assert inv.out.splitlines()[0] == "path,size,lines,sha256,ext,root"
    assert "# 4 script(s) under 1 root(s)" in inv.err and "# left out, symlink: 2" in inv.err
    monkeypatch.setattr("sys.stdin", io.StringIO(inv.out))
    assert sc.main(["mark-copies", "--repo", "."]) == 0
    marked = capsys.readouterr()
    table = list(csv.DictReader(io.StringIO(marked.out)))
    assert list(table[0]) == [*sc.INVENTORY_COLUMNS, "copy_kind"]
    assert [r["copy_kind"] for r in table] == ["snapshot", "authored", "tracked-duplicate", "authored"]
    assert "# tracked-duplicate: 1" in marked.err
    monkeypatch.setattr("sys.stdin", io.StringIO("a,b\n1,2\n"))
    assert sc.main(["mark-copies"]) == 2
    assert sc.main(["inventory", "nope"]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_a_root_that_is_a_symlink_is_refused(tree: Path, tmp_path: Path) -> None:
    link = tmp_path / "rootlink"
    link.symlink_to(tree / "tmp", target_is_directory=True)
    for spelled in (str(link), f"{link}/"):
        with pytest.raises(sc.CensusError, match="symbolic link"):
            sc.inventory([spelled])


def test_a_folder_that_cannot_be_listed_is_counted(tree: Path) -> None:
    locked = tree / "tmp" / "locked"
    locked.mkdir()
    (locked / "hidden.py").write_text("pass\n", encoding="utf-8")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("this user can list a folder with no permissions")
        _, pruned = sc.inventory([tree / "tmp"], max_bytes=1000)
        assert pruned["unreadable-dir"] == 1
    finally:
        locked.chmod(0o755)


def test_the_listing_covers_the_whole_repository_from_a_subfolder(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `git ls-files` lists only the folder it runs in; from tmp/ the tracked tools/ would be missed.
    monkeypatch.chdir(tree)
    rows, _ = sc.inventory(["tmp", "tools"], max_bytes=1000)
    kinds = {f"{r['root']}/{r['path']}": r["copy_kind"] for r in sc.mark_copies(rows, repo=tree / "tmp")}
    assert kinds["tmp/week1/report_copy.py"] == "tracked-duplicate" and kinds["tools/report.py"] == "authored"


def test_rows_from_stdin_cannot_point_outside_their_root(tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tree)
    for bad in ("../outside/secret.py", str(tmp_path / "outside" / "secret.py"), ""):
        with pytest.raises(sc.CensusError, match="not a relative path"):
            sc.mark_copies([{"path": bad, "root": "tmp"}], repo=tree)
    # A file reached through a link is not read: the inventory would never have listed it.
    (tree / "tmp" / "week1" / "report_copy.py").unlink()
    (tree / "tmp" / "week1" / "report_copy.py").symlink_to(tree / "tools" / "report.py")
    via_dir = {"path": "linkdir/secret.py", "root": "tmp"}
    via_file = {"path": "week1/report_copy.py", "root": "tmp"}
    assert [r["copy_kind"] for r in sc.mark_copies([via_dir, via_file], repo=tree)] == ["missing", "missing"]
    big = {"path": "week1/big.py", "root": "tmp"}
    assert sc.mark_copies([big], repo=tree, max_bytes=1000)[0]["copy_kind"] == "missing"
    assert sc.mark_copies([big], repo=tree)[0]["copy_kind"] == "authored"
