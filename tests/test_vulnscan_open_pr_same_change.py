"""open-pr and a change already open under another date (2026-10-28).

The staged branch is ``vulnscan/<YYYYMMDD>-<digest>``. traffic-file-enrichment-app's fix was re-staged on
2026-10-28 with the same digest as its open PR #25 (``vulnscan/20261027-a6c84de8a8``); open-pr matched open PRs by
exact branch name only, so it would have pushed the new branch and opened a duplicate PR.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, fix  # noqa: E402
from tests.test_vulnscan_executor import (  # noqa: E402
    _GH,
    _gh_calls,
    _install,
    _pin_fix,
    _repo_with_remote,
    env,  # noqa: F401  (the shared fixture)
)

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not available")


def _fake_gh(env: dict, monkeypatch: pytest.MonkeyPatch, author: tuple[str, str] = executor.GIT_IDENTITY) -> Path:
    """The executor tests' fake gh, also answering ``pr view`` with one commit by ``author`` and a verdict comment."""
    bindir = env["tmp"] / "ghbin"
    bindir.mkdir()
    view = {"state": "OPEN", "headRefName": "x", "isCrossRepository": False, "reviews": [],
            "comments": [{"body": "Repository orchestrator review: approve-staged"}],
            "commits": [{"authors": [{"name": author[0], "email": author[1]}]}]}
    (bindir / "view.json").write_text(json.dumps(view))
    (bindir / "gh").write_text(_GH.replace('  *) exit 2 ;;', '  "pr view") cat "$d/view.json" ;;\n  *) exit 2 ;;'))
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _earlier_pr(wt: Path, staged_branch: str, *, extra_commit: bool = False) -> str:
    """Push the staged commit as an earlier day's branch, list it as an open PR, and return its branch name."""
    earlier = f"vulnscan/20000101-{staged_branch.rsplit('-', 1)[-1]}"
    if extra_commit:   # someone added a commit to the earlier PR: not the same change any more
        (wt / "NOTES.md").write_text("added by hand\n")
        _git(wt, "add", "NOTES.md")
        _git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "extra")
        _git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{earlier}")
        _git(wt, "reset", "-q", "--hard", "HEAD~1")
    else:
        _git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{earlier}")
    return earlier


def test_an_open_pr_with_the_same_change_is_claimed(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"])
    earlier = _earlier_pr(Path(staged["worktree"]), staged["branch"])
    bindir = _fake_gh(env, monkeypatch)
    (bindir / "open.json").write_text(json.dumps([
        {"url": "https://github.example.com/o/svc/pull/6", "headRefName": earlier, "isCrossRepository": True},
        {"url": "https://github.example.com/o/svc/pull/7", "headRefName": earlier, "isCrossRepository": False}]))
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "done" and res["pr"].endswith("/pull/7") and res.get("existing") is True, res
    assert res["same_change_as"] == earlier
    assert not any(c.startswith("pr create") for c in _gh_calls(bindir))
    remote = env["tmp"] / "remote.git"
    assert _git(remote, "branch", "--list", staged["branch"]) == ""          # nothing new was pushed
    assert not Path(staged["worktree"]).exists()                             # and the staged state is done
    assert not executor.staged_file(repo.resolve()).exists()


def test_an_open_pr_with_a_commit_by_someone_else_is_not_claimed(env: dict,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    # the same content, but authored by another identity: the digest is public, so content alone is not enough
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"])
    earlier = _earlier_pr(Path(staged["worktree"]), staged["branch"])
    bindir = _fake_gh(env, monkeypatch, author=("someone", "someone@example.com"))
    (bindir / "open.json").write_text(json.dumps([
        {"url": "https://github.example.com/o/svc/pull/7", "headRefName": earlier, "isCrossRepository": False}]))
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "done" and res["pr"].endswith("/pull/9") and "existing" not in res, res


def test_an_open_pr_with_more_than_the_change_is_not_claimed(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"])
    earlier = _earlier_pr(Path(staged["worktree"]), staged["branch"], extra_commit=True)
    bindir = _fake_gh(env, monkeypatch)
    (bindir / "open.json").write_text(json.dumps([
        {"url": "https://github.example.com/o/svc/pull/7", "headRefName": earlier, "isCrossRepository": False}]))
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "done" and res["pr"].endswith("/pull/9") and "existing" not in res, res
    assert any(c.startswith("pr create") for c in _gh_calls(bindir))


def test_only_vulnscan_branches_with_the_same_digest_are_candidates(env: dict,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    # decoys that exist on the remote and hold the staged commit: only the branch name rules them out
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"])
    wt = Path(staged["worktree"])
    decoys = ["vulnscan/20000101-0000000000", "feature/x", f"other/20000101-{staged['branch'].rsplit('-', 1)[-1]}"]
    for name in decoys:
        _git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{name}")
    bindir = _fake_gh(env, monkeypatch)
    (bindir / "open.json").write_text(json.dumps([
        {"url": f"https://github.example.com/o/svc/pull/{n}", "headRefName": name, "isCrossRepository": False}
        for n, name in enumerate(decoys, start=3)]))
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "done" and res["pr"].endswith("/pull/9") and "existing" not in res, res


def test_an_unreadable_pr_list_opens_the_pr_as_before(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    executor.act("stage-fix", str(repo), env["cfg"])
    bindir = _fake_gh(env, monkeypatch)
    (bindir / "open.json").write_text("not json")
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "done" and res["pr"].endswith("/pull/9"), res


def test_a_change_already_merged_into_the_base_opens_nothing(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    # an earlier day's PR with this change was merged: the re-staged copy must not become a new PR
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"])
    _git(Path(staged["worktree"]), "push", "-q", "origin", "HEAD:refs/heads/main")   # merged by another route
    bindir = _fake_gh(env, monkeypatch)
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "done" and res.get("already_in_base") is True and res["pr"] == "", res
    assert not any(c.startswith("pr create") for c in _gh_calls(bindir))
    assert _git(env["tmp"] / "remote.git", "branch", "--list", staged["branch"]) == ""
    assert not executor.staged_file(repo.resolve()).exists()


def test_a_base_without_the_change_still_gets_its_pr(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    executor.act("stage-fix", str(repo), env["cfg"])
    _fake_gh(env, monkeypatch)
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "done" and res["pr"].endswith("/pull/9") and "already_in_base" not in res, res
