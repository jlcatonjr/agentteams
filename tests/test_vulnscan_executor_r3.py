"""agentteams.vulnscan executor, round 3: staging under ignored directories and failed-attempt cleanup.

Shares fixtures and helpers with ``test_vulnscan_executor.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, fix  # noqa: E402
from tests.test_vulnscan_executor import (  # noqa: E402
    GIT,
    _assert_refused_cleanly,
    _executor_worktrees,
    _install,
    _pin_fix,
    _repo_with_remote,
    _sh,
    env,
)
from tests.test_vulnscan_executor_r2 import _committed  # noqa: E402

pytestmark = pytest.mark.skipif(not GIT, reason="git not available")
SUB = "Vendored/tool/requirements.txt"


def _ignored_dir_repo(env: dict) -> Path:
    """A repo whose .gitignore ignores ``Vendored`` while a pin file inside it is tracked."""
    repo = _repo_with_remote(env["tmp"])
    (repo / SUB).parent.mkdir(parents=True)
    (repo / SUB).write_text("PyJWT==2.3.0\n")
    (repo / ".gitignore").write_text("Vendored\n")
    _sh(repo, "add", "-f", SUB, ".gitignore")
    _sh(repo, "commit", "-q", "-m", "vendored tool")
    _sh(repo, "push", "-q", "origin", "main")
    return repo


def _sub_fix(root: Path, **kw: object) -> fix.FixResult:
    req = root / SUB
    req.write_text(req.read_text().replace("PyJWT==2.3.0", "PyJWT==2.15.0"))
    (root / "Vendored" / "tool" / "dev-requirements.txt").write_text("evil==1.0\n")   # new, ignored: never staged
    return fix.FixResult(changes=[fix.PinChange(SUB, "PyJWT", "2.3.0", "2.15.0")], applied=True)


def test_a_tracked_pin_under_an_ignored_directory_is_staged_without_force(env: dict,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _ignored_dir_repo(env)
    _install(env, ops=["stage-fix"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _sub_fix)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "done" and res["changed"] == [SUB], res
    files = _sh(repo, "diff", "--name-only", f"{res['base']}..{res['branch']}").split()
    assert files == [SUB]                                             # the ignored new file was not added
    assert "PyJWT==2.15.0" in _sh(repo, "show", f"{res['branch']}:{SUB}")


def test_a_failure_after_branching_removes_the_branch_and_a_retry_succeeds(env: dict,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[], max_uses=5)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    real = executor._stage_changed

    def boom(wt: Path, base: str, changed: list[str]) -> None:
        raise executor.ExecError("git add failed: simulated")

    monkeypatch.setattr(executor, "_stage_changed", boom)
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="c" * 32)
    assert res["status"] == "failed" and "simulated" in res["detail"], res
    _assert_refused_cleanly(repo, "c" * 32)                           # no vulnscan/* branch, no worktree
    monkeypatch.setattr(executor, "_stage_changed", real)
    again = executor.act("stage-fix", str(repo), env["cfg"])
    assert again["status"] == "done" and again["branch"].startswith("vulnscan/"), again
    assert _executor_worktrees(repo) == [str(Path(again["worktree"]).resolve())]


def test_cleanup_never_deletes_a_branch_that_moved_or_is_checked_out_elsewhere(env: dict, tmp_path: Path) -> None:
    repo, base = _committed(tmp_path / "r", {"requirements.txt": b"a==1\n"})
    _sh(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "later")
    _sh(repo, "branch", "vulnscan/moved")                             # not at base: somebody else's
    executor._drop_failed_branch(repo, "vulnscan/moved", base)
    _sh(repo, "branch", "vulnscan/held", base)
    _sh(repo, "worktree", "add", "-q", str(tmp_path / "mine"), "vulnscan/held")
    executor._drop_failed_branch(repo, "vulnscan/held", base)         # checked out outside the executor
    assert set(_sh(repo, "branch", "--list", "vulnscan/*", "--format=%(refname:short)").split()) == {
        "vulnscan/moved", "vulnscan/held"}
    _sh(repo, "branch", "vulnscan/own", base)
    executor._drop_failed_branch(repo, "vulnscan/own", base)          # at base, held nowhere: this attempt's
    assert "vulnscan/own" not in _sh(repo, "branch", "--list", "vulnscan/*")


def test_a_branch_made_by_someone_else_mid_attempt_survives_the_failure(env: dict,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Between the free-name check and ``checkout -b`` another process takes the name: cleanup must not delete it."""
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    real = executor._release_own_branch
    taken: list[str] = []

    def racer(r: Path, name: str) -> None:
        real(r, name)
        _sh(repo, "branch", name, "origin/main")
        taken.append(name)

    monkeypatch.setattr(executor, "_release_own_branch", racer)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "failed" and taken, res
    assert _sh(repo, "branch", "--list", taken[0]).strip(), "another process's branch was deleted"


def test_a_repo_configured_signing_program_never_runs(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    marker = env["tmp"] / "gpg-ran"
    prog = env["tmp"] / "fake-gpg"
    prog.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    prog.chmod(0o755)
    _sh(repo, "config", "commit.gpgsign", "true")
    _sh(repo, "config", "gpg.program", str(prog))
    _install(env, ops=["stage-fix"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "done", res
    assert not marker.exists()


def test_a_repo_configured_upload_pack_or_ext_transport_never_runs(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository's config cannot make the executor's fetch run a program (uploadpack, ext:: remote)."""
    repo = _repo_with_remote(env["tmp"])
    marker = env["tmp"] / "ran"
    prog = env["tmp"] / "evil-upload-pack"
    prog.write_text(f"#!/bin/sh\ntouch '{marker}'\nexec git-upload-pack \"$@\"\n")
    prog.chmod(0o755)
    _sh(repo, "config", "remote.origin.uploadpack", str(prog))
    _install(env, ops=["stage-fix"], hosts=[], max_uses=5)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "done", res
    assert not marker.exists()                                         # git's own upload-pack ran
    _sh(repo, "config", "remote.origin.url", f"ext::sh -c touch% {marker}")
    _sh(repo, "config", "protocol.ext.allow", "always")
    executor.act("stage-fix", str(repo), env["cfg"])
    assert not marker.exists()                                         # ext:: is not an allowed transport


def test_an_npm_that_failed_to_run_is_reported_as_such(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    err = 'npm error double-loading config "/x/npmrc" as "global", previously loaded as "user"'
    monkeypatch.setattr(fix, "fix_path", lambda root, **kw: fix.FixResult(npm=[(".", f"Exit\n{err}\n")], applied=True))
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "failed" and res["detail"].startswith("npm failed: npm error double-loading"), res


def test_stage_changed_uses_literal_paths_and_checks_the_staged_set(env: dict, tmp_path: Path) -> None:
    repo, base = _committed(tmp_path / "r", {"requirements.txt": b"a==1\n", "requirements-dev.txt": b"b==1\n"})
    for name in ("requirements.txt", "requirements-dev.txt"):
        (repo / name).write_text("x==2\n")
    with pytest.raises(executor.ExecError):                           # a glob never matches other files
        executor._stage_changed(repo, base, ["requirements*.txt"])
    assert _sh(repo, "diff", "--cached", "--name-only") == ""
    executor._stage_changed(repo, base, ["requirements.txt"])
    assert _sh(repo, "diff", "--cached", "--name-only").split() == ["requirements.txt"]
    _sh(repo, "add", "requirements-dev.txt")                          # something else staged: refused
    with pytest.raises(executor.ExecError, match="staged paths differ"):
        executor._stage_changed(repo, base, ["requirements.txt"])
