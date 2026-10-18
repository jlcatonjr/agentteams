"""M6-F7: the Claude hook's notice log must not count as a confined command's undeclared write.

The generated hook (hooks_emit.py) appends ``.claude/hook-notices/<UTC date>.log`` while a long confined command runs.
``run_request`` watches ``.claude``, so before the fix the run was reported ok:false with that file as an undeclared
write. Only that exact file, for the UTC day(s) the command spanned, is exempt, only when a sandbox ran, and only when
it still exists afterwards. Everything else under ``.claude`` stays watched.

Simulation as in test_runner_heartbeat_exemption: ``_sandbox_for`` returns a name, ``wrap`` is the identity.
"""

from __future__ import annotations

import threading
import time

import pytest

from agentteams import proposals as P
from tests.test_runner_heartbeat_exemption import SLEEP, _run, _simulate, project  # noqa: F401  (fixture)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _writer(path, every: float = 0.1):
    """A concurrent writer appending to *path* until stopped (the hook, or anything else in the window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("seed\n")
    stop = threading.Event()

    def run():
        n = 0
        while not stop.wait(every):
            n += 1
            with path.open("a") as fh:
                fh.write(f"[{n}] notice\n")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return stop, t


def _commit_all(root):
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"], cwd=root, check=True)


def test_hook_notice_appends_during_a_sandboxed_command_are_not_undeclared(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    stop, t = _writer(project / ".claude" / "hook-notices" / f"{_today()}.log")
    try:
        result = _run(project, SLEEP)   # 0.6 s: several appends (ticks) land while the command runs
    finally:
        stop.set()
        t.join()
    assert result["exit"] == 0 and result["undeclared_writes"] == []


def test_settings_json_changed_in_the_same_window_is_still_flagged(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    notices = _writer(project / ".claude" / "hook-notices" / f"{_today()}.log")
    settings = _writer(project / ".claude" / "settings.json")
    try:
        with pytest.raises(P.UndeclaredWritesError, match=r"\.claude/settings\.json") as exc:
            _run(project, SLEEP)
    finally:
        for stop, t in (notices, settings):
            stop.set()
            t.join()
    assert exc.value.result["undeclared_writes"] == [".claude/settings.json"]   # the notice log is not listed


def test_another_days_notice_log_is_still_flagged(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    stop, t = _writer(project / ".claude" / "hook-notices" / "1999-01-01.log")
    try:
        with pytest.raises(P.UndeclaredWritesError, match=r"hook-notices/1999-01-01\.log"):
            _run(project, SLEEP)
    finally:
        stop.set()
        t.join()


def test_a_lookalike_path_is_still_flagged(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    stop, t = _writer(project / ".claude" / "hook-notices" / f"{_today()}.log.bak")
    try:
        with pytest.raises(P.UndeclaredWritesError, match=r"\.log\.bak"):
            _run(project, SLEEP)
    finally:
        stop.set()
        t.join()


def test_deleting_the_notice_log_is_still_flagged(project, monkeypatch):
    _simulate(monkeypatch, "seatbelt")
    log = project / ".claude" / "hook-notices" / f"{_today()}.log"
    log.parent.mkdir(parents=True)
    log.write_text("x\n")
    _commit_all(project)
    # Deleted inside the measured window (here by the simulated command): a deletion is never exempt.
    code = f"import os, time; time.sleep(0.2); os.remove('.claude/hook-notices/{_today()}.log')"
    with pytest.raises(P.UndeclaredWritesError, match=r"hook-notices/.*\.log"):
        _run(project, code)


def test_notice_appends_still_fail_under_the_unconfined_opt_out(project, monkeypatch):
    _simulate(monkeypatch, None)
    stop, t = _writer(project / ".claude" / "hook-notices" / f"{_today()}.log")
    try:
        with pytest.raises(P.UndeclaredWritesError, match=r"hook-notices"):
            _run(project, SLEEP, allow_unconfined=True)
    finally:
        stop.set()
        t.join()
