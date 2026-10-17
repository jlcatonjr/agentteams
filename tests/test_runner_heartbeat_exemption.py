"""M6-F6: the runner's own heartbeat must not count as a confined command's undeclared write.

The runner's heartbeat thread rewrites ``.agentteams/runner.heartbeat`` every few seconds. ``run_request`` diffs a
before/after snapshot that watches ``.agentteams`` by stat, so before the fix any confined command that outlasted a
tick failed with "command wrote outside its declared writes: .agentteams/runner.heartbeat" (mathAgents M6, rc.13).

These tests simulate "a sandbox ran" without needing one (``_sandbox_for`` returns a name, ``wrap`` is the identity),
and simulate the runner's heartbeat with a thread that rewrites the file while the command sleeps.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from agentteams import confinement as C
from agentteams import proposal_runner
from agentteams import proposals as P

PY = sys.executable
PY_DIRS = sorted({os.path.dirname(os.path.dirname(os.path.realpath(PY))), os.path.dirname(PY)})
SLEEP = "import time; time.sleep(0.6)"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv(P.KEY_ENV[0], "test-key-0123456789abcdef0123456")
    root = tmp_path / "proj"
    (root / "out").mkdir(parents=True)
    (root / "out" / ".keep").write_text("")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root


def _policy(code: str, *, allow_unconfined: bool = False) -> P.Policy:
    brief = {
        "agent_policies": {"x": {"commands": [{"prefix": [PY, "-c", code], "args": [], "writes": ["out/*"]}]}},
        "confined_programs": {"x": {"exec": PY_DIRS, "write": ["out"]}},
    }
    if allow_unconfined:
        brief["allow_unconfined_runs"] = True
    return P.load_policy(brief)


def _run(root, code, **kw):
    req = {"kind": "command-request", "dispatch": P.issue_dispatch(root, "x"), "argv": [PY, "-c", code],
           "purpose": "test", "expected_writes": []}
    return P.run_request(req, root=root, policy=_policy(code, **kw), confine=True)


def _simulate(monkeypatch, sandbox: str | None):
    monkeypatch.setattr(P, "_sandbox_for", lambda policy: sandbox)
    monkeypatch.setattr(C, "wrap", lambda command, **kw: command)


def _beating(root):
    """The runner's heartbeat: rewrite the file (new inode, as the runner does) until stopped."""
    hb = root / P.RUNNER_HEARTBEAT_REL
    hb.parent.mkdir(exist_ok=True)
    hb.write_text("0")
    stop = threading.Event()

    def beat():
        n = 0
        while not stop.wait(0.1):
            n += 1
            tmp = hb.with_name(hb.name + ".tmp")
            tmp.write_text(str(n))
            os.replace(tmp, hb)

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    return stop, t


def test_runner_uses_the_shared_constant():
    assert proposal_runner.HEARTBEAT_REL == P.RUNNER_HEARTBEAT_REL


def test_snapshot_waits_for_a_beat_in_flight(project):
    """A beat holds RUNNER_STATE_LOCK; a snapshot taken meanwhile waits for it, so it never sees the temp file."""
    env = {**os.environ}
    done = threading.Event()
    with P.RUNNER_STATE_LOCK:
        t = threading.Thread(target=lambda: (P._snapshot(project, env), done.set()), daemon=True)
        t.start()
        assert not done.wait(0.3)  # blocked behind the beat
    assert done.wait(5)


def test_heartbeat_during_a_sandboxed_command_is_not_an_undeclared_write(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    stop, t = _beating(project)
    try:
        result = _run(project, SLEEP)
    finally:
        stop.set()
        t.join()
    assert result["exit"] == 0 and result["undeclared_writes"] == []


def test_other_control_plane_change_still_fails_under_a_sandbox(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    (project / ".agentteams").mkdir(exist_ok=True)
    code = "open('.agentteams/planted.txt', 'w').write('x')"
    with pytest.raises(P.UndeclaredWritesError, match=r"\.agentteams/planted\.txt"):
        _run(project, code)


def test_heartbeat_change_still_fails_under_the_unconfined_opt_out(project, monkeypatch):
    _simulate(monkeypatch, None)
    stop, t = _beating(project)
    try:
        with pytest.raises(P.UndeclaredWritesError, match=r"\.agentteams/runner\.heartbeat"):
            _run(project, SLEEP, allow_unconfined=True)
    finally:
        stop.set()
        t.join()


def test_short_command_without_a_tick_is_unaffected(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    result = _run(project, "pass")
    assert result["exit"] == 0 and result["undeclared_writes"] == []


def test_a_beat_during_a_snapshot_skips_only_the_legacy_file(tmp_path):
    """While a snapshot holds the lock, the status beat is still written and the legacy beat is skipped, not waited."""
    runner = proposal_runner.Runner.__new__(proposal_runner.Runner)
    runner.root = tmp_path
    with P.RUNNER_STATE_LOCK:
        runner._heartbeat()
    assert (tmp_path / proposal_runner.STATUS_HEARTBEAT_REL).is_file()
    assert not (tmp_path / P.RUNNER_HEARTBEAT_REL).exists()
    runner._heartbeat()
    assert (tmp_path / P.RUNNER_HEARTBEAT_REL).is_file()
