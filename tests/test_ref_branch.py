"""test_ref_branch.py — the shared default-branch resolver and its staleness report.

A coverage verdict read from a stale checkout is wrong in a way nobody sees: the tool "missing"
locally exists on the default branch. These tests pin that the helper reads the remote-tracking
ref, says how old that ref is, never guesses a default branch, and is not redirected by an
inherited ``GIT_DIR``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentteams import ref_branch as rb

_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=_ENV, check=True)
    return proc.stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def pair(tmp_path: Path) -> tuple[Path, Path]:
    """An upstream repository on ``main`` and a clone of it."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _commit(upstream, "README.md", "one\n", "first")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], env=_ENV, check=True, capture_output=True)
    return upstream, clone


def test_default_ref_prefers_the_remote_tracking_ref(pair: tuple[Path, Path]) -> None:
    _, clone = pair
    assert rb.default_ref(clone) == "origin/main"
    _git(clone, "remote", "set-head", "origin", "-d")       # origin/HEAD gone: fall back to origin/main
    assert rb.default_ref(clone) == "origin/main"


def test_default_ref_falls_back_to_a_local_branch_and_never_guesses(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    _git(local, "init", "-q", "-b", "master")
    _commit(local, "a.txt", "a\n", "a")
    assert rb.default_ref(local) == "master"

    odd = tmp_path / "odd"
    odd.mkdir()
    _git(odd, "init", "-q", "-b", "trunk")
    _commit(odd, "a.txt", "a\n", "a")
    with pytest.raises(rb.RefBranchError, match="no origin/HEAD"):
        rb.default_ref(odd)
    with pytest.raises(rb.RefBranchError, match="not a git repository"):
        rb.default_ref(tmp_path)


def test_show_reads_the_default_branch_not_the_stale_checkout(pair: tuple[Path, Path]) -> None:
    upstream, clone = pair
    _commit(upstream, "tool.py", "print('new tool')\n", "add the tool")
    assert rb.show(clone, "tool.py") is None                 # not fetched yet: the ref itself is stale
    _git(clone, "fetch", "-q")
    assert not (clone / "tool.py").exists()                  # the checkout still lacks it
    assert rb.show(clone, "tool.py") == "print('new tool')\n"
    assert rb.show(clone, "README.md") == "one\n"
    assert rb.show(clone, "absent.py") is None


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../outside", "a/../../b", "-rf", "HEAD:README.md"])
def test_show_refuses_anything_but_a_plain_relative_path(pair: tuple[Path, Path], path: str) -> None:
    with pytest.raises(rb.RefBranchError):
        rb.show(pair[1], path)


def test_changed_since_lists_recent_commits_on_the_ref(pair: tuple[Path, Path]) -> None:
    upstream, clone = pair
    _commit(upstream, "tool.py", "x\n", "add the tool")
    _git(clone, "fetch", "-q")
    (line,) = rb.changed_since(clone, "tool.py", 2)
    assert line.endswith("add the tool")
    assert rb.changed_since(clone, "nothing-here.py", 2) == []
    with pytest.raises(rb.RefBranchError):
        rb.changed_since(clone, "tool.py", -1)


def test_freshness_reports_how_far_behind_and_how_old(pair: tuple[Path, Path]) -> None:
    upstream, clone = pair
    fresh = rb.freshness(clone)
    assert (fresh.ref, fresh.remote, fresh.branch, fresh.behind) == ("origin/main", True, "main", 0)
    assert fresh.fetch_age_hours is None and "never fetched" in fresh.describe()

    _commit(upstream, "tool.py", "x\n", "add the tool")
    _git(clone, "fetch", "-q")
    fresh = rb.freshness(clone)
    assert fresh.behind == 1 and fresh.fetch_age_hours is not None and 0 <= fresh.fetch_age_hours < 1
    assert "1 commit(s) behind" in fresh.describe() and "fetched 0.0 h ago" in fresh.describe()

    _git(clone, "checkout", "-q", "--detach")
    assert rb.freshness(clone).branch == "(detached)"


def test_a_local_only_repository_says_so(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    _git(local, "init", "-q", "-b", "main")
    _commit(local, "a.txt", "a\n", "a")
    fresh = rb.freshness(local)
    assert fresh.remote is False and fresh.fetch_age_hours is None
    assert "local branch only" in fresh.describe()


def test_an_inherited_git_dir_does_not_redirect_the_query(pair: tuple[Path, Path], tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _, clone = pair
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "trunk")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    assert rb.default_ref(clone) == "origin/main"
    assert rb.show(clone, "README.md") == "one\n"


def test_cli(pair: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    upstream, clone = pair
    assert rb.main([str(clone)]) == 0
    assert capsys.readouterr().out.startswith("origin/main (never fetched")
    assert rb.main([str(clone), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ref"] == "origin/main"

    assert rb.main([str(clone), "--max-fetch-age", "24"]) == 1          # never fetched counts as stale
    assert "STALE" in capsys.readouterr().err
    _git(clone, "fetch", "-q")
    assert rb.main([str(clone), "--max-fetch-age", "24"]) == 0
    capsys.readouterr()

    assert rb.main([str(clone), "--show", "README.md"]) == 0
    out = capsys.readouterr()
    assert out.out == "one\n" and "README.md at origin/main" in out.err
    assert rb.main([str(clone), "--show", "absent.py"]) == 1
    assert rb.main([str(tmp_path)]) == 2
    assert "not a git repository" in capsys.readouterr().err
