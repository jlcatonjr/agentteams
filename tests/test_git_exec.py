"""CH-08: one git core; read-only callers in agent-writable repos are hardened, committing ones are not."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentteams import git_exec
from agentteams.git_exec import HARDENED_ENV, HARDENING_CONFIG, git_argv, run_git


def test_argv_shapes():
    assert git_argv(Path("/r"), "status") == ["git", "-C", "/r", "status"]
    assert git_argv(None, "status") == ["git", "status"]
    hardened = git_argv(Path("/r"), "ls-files", hardened=True)
    assert hardened[:len(HARDENING_CONFIG) + 1] == ["git", *HARDENING_CONFIG]
    assert "core.fsmonitor=false" in hardened and "core.hooksPath=/dev/null" in hardened


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []

    def fake_run(argv, **kw):
        calls.append({"argv": argv, **kw})
        return subprocess.CompletedProcess(argv, 0, stdout=b"" if kw.get("text") is False else "", stderr="")

    monkeypatch.setattr(git_exec.subprocess, "run", fake_run)
    return calls


def test_hardened_env_is_layered_over_the_given_env(captured):
    run_git(Path("/r"), "status", hardened=True, env={"PATH": "/usr/bin"})
    assert captured[0]["env"] == {"PATH": "/usr/bin", **HARDENED_ENV}


def test_proposals_snapshot_git_is_hardened_and_keeps_its_scrubbed_env(captured, tmp_path):
    from agentteams import proposals
    proposals._git(tmp_path, {"PATH": "/usr/bin"}, "rev-parse", "--show-prefix")
    call = captured[0]
    assert "core.fsmonitor=false" in call["argv"] and call["cwd"] == tmp_path
    # The snapshot must see the repo's real hooks path (it watches `--git-path hooks` for a planted hook).
    assert not any(a.startswith("core.hooksPath") for a in call["argv"])
    assert call["env"] == {"PATH": "/usr/bin", **HARDENED_ENV} and call["stdin"] == subprocess.DEVNULL
    assert call["text"] is False and call["timeout"] == 60


def test_source_provenance_git_is_hardened(captured, tmp_path):
    from agentteams import source_provenance
    source_provenance._git(tmp_path, "rev-parse", "HEAD")
    assert "core.hooksPath=/dev/null" in captured[0]["argv"]


def test_fleet_git_is_not_hardened_because_it_commits(captured, tmp_path):
    from agentteams import fleet
    fleet._git(tmp_path, "commit", "-m", "x")
    assert "core.hooksPath=/dev/null" not in captured[0]["argv"]   # commit hooks must still run


def test_operator_signing_git_stays_hardened(captured, tmp_path):
    from agentteams.cli import operator_signing
    operator_signing._git(tmp_path, "status")
    assert "core.fsmonitor=false" in captured[0]["argv"] and captured[0]["timeout"] == 10


# --- the shared custody read and atomic write (atomicio, CH-08) --------------------------------------

import os  # noqa: E402

from agentteams import atomicio  # noqa: E402
from agentteams.atomicio import FileTooLargeError, read_regular_nofollow, write_new_atomic  # noqa: E402


def test_custody_read_refuses_symlink_fifo_size_and_a_failing_check(tmp_path):
    real = tmp_path / "real"
    real.write_bytes(b"x" * 10)
    assert read_regular_nofollow(real, 10) == b"x" * 10
    with pytest.raises(FileTooLargeError):
        read_regular_nofollow(real, 9)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(OSError):
        read_regular_nofollow(link, 100)                   # O_NOFOLLOW
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="not a regular file"):
        read_regular_nofollow(fifo, 100)                   # O_NONBLOCK: refused, never hangs

    def refuse(st):
        raise PermissionError("custody")
    with pytest.raises(PermissionError, match="custody"):
        read_regular_nofollow(real, 100, check=refuse)    # the caller's own error passes through unchanged


def test_atomic_write_never_follows_a_planted_temp_name(tmp_path, monkeypatch):
    monkeypatch.setattr(atomicio.secrets, "token_hex", lambda n: "fixed")
    target = tmp_path / "elsewhere"
    (tmp_path / ".out.fixed.tmp").symlink_to(target)         # planted where the temp file will go
    with pytest.raises(FileExistsError):
        write_new_atomic(tmp_path, "out", b"data")
    assert not target.exists() and not (tmp_path / "out").exists()


def test_atomic_write_writes_with_the_mode(tmp_path):
    path = write_new_atomic(tmp_path, "out", b"data", mode=0o600)
    assert path.read_bytes() == b"data" and oct(path.stat().st_mode & 0o777) == "0o600"


def test_a_ledger_key_file_over_4k_is_refused(tmp_path, monkeypatch):
    from agentteams import proposals as P
    key = tmp_path / "keys" / "proposal-ledger.key"
    key.parent.mkdir()
    key.write_text("a" * 5000)
    key.chmod(0o600)
    monkeypatch.setattr(P, "KEY_DIR", str(key.parent))
    monkeypatch.setattr(P, "_FILE_KEY", None)
    with pytest.raises(P.ProposalError, match="larger than 4096 bytes"):
        P.use_key_file(str(key))


# --- end to end against real git: a planted core.fsmonitor never runs --------------------------------


def test_a_planted_fsmonitor_does_not_run_under_hardening(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "evil.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    run_git(repo, "status", "--porcelain", hardened=True, timeout=30)
    assert not marker.exists()                                  # hardened: the repo's fsmonitor is overridden
    run_git(repo, "status", "--porcelain", timeout=30)
    assert marker.exists()                                      # control: unhardened git does run it


def test_override_hooks_false_keeps_the_other_overrides():
    argv = git_argv(Path("/r"), "rev-parse", hardened=True, override_hooks=False)
    assert "core.fsmonitor=false" in argv and "core.untrackedCache=false" in argv
    assert not any(a.startswith("core.hooksPath") for a in argv)



def test_the_snapshot_status_never_runs_a_planted_post_index_change_hook(tmp_path):
    # proposals._git keeps the real hooks path (its watch needs it); GIT_OPTIONAL_LOCKS=0 is what stops `status`
    # writing the index and so running post-index-change. The control run (plain git) shows the hook is live.
    import time
    from agentteams import proposals
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    (repo / "f").write_text("a")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=repo, check=True)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-index-change"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)

    def make_stat_dirty():
        os.utime(repo / "f", (946684800, 946684800))
        time.sleep(1.1)
        (repo / "f").touch()

    make_stat_dirty()
    proposals._git(repo, {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "")},
                   "status", "--porcelain=v1", "--untracked-files=all", "-z", "--", ".")
    assert not marker.exists()
    make_stat_dirty()
    subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True)
    assert marker.exists()                                    # control: without the lock suppression it runs
