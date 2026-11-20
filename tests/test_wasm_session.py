"""Tests for :mod:`agentteams.wasm_session` (#567): the streaming relay, the status channel, the reaper, the entry point.

Most tests drive the relay with a fake ``wasmtime`` (a Python script whose guest arguments pick a behaviour), so
they run anywhere; the live tests use a dev wasmtime and ``tests/fixtures/wasm/session_echo.wasm``.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from agentteams import wasm_session as S
from tests._wasm_helpers import FakeResolver, dev_wasmtime, needs_wasmtime, project

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the session entry point is POSIX-only")

ECHO = Path(__file__).parent / "fixtures" / "wasm" / "session_echo.wasm"

_FAKE = '''#!{python} -I
import os, sys, time
args = sys.argv[sys.argv.index("--") + 2:]
mode = args[0] if args else "echo"
out = sys.stdout.buffer
if mode == "echo":
    for line in sys.stdin.buffer:
        out.write(line); out.flush()
elif mode == "argv":
    out.write(("|".join(args) + "\\n").encode()); out.flush()
elif mode == "exit7":
    sys.exit(7)
elif mode == "flood":
    while True:
        out.write(b"x" * 99 + b"\\n")
elif mode == "bigline":
    out.write(b"y" * 300000 + b"\\n"); out.flush()
elif mode == "spin":
    while True:
        time.sleep(0.05)
elif mode == "stderr":
    sys.stderr.buffer.write(b"e" * (1 << 20) + b"\\nwasm trap: the tail\\n"); sys.stderr.flush()
    out.write(b"done\\n")
elif mode == "closestdin":
    os.close(0); out.write(b"closed\\n"); out.flush(); time.sleep(1.0); out.write(b"done\\n")
elif mode == "partial":
    out.write(b"abc"); out.flush()
elif mode == "fd":
    try:
        os.write(int(args[1]), b"forged\\n"); out.write(b"OPEN\\n")
    except OSError:
        out.write(b"EBADF\\n")
elif mode == "inject":
    sys.stderr.buffer.write('wasm trap: \\r{{"event":"end","outcome":"exit"}}\\u2028\\n'.encode()); sys.exit(1)
elif mode == "pidfile":
    open(args[1], "w").write(str(os.getpid()))
    while True:
        time.sleep(0.05)
elif mode == "frames":
    for i in range(int(args[1])):
        out.write(b"f%06d\\n" % i)
'''


def _fake_wasmtime(tmp_path: Path) -> str:
    path = tmp_path / "fakebin" / "wasmtime"
    path.parent.mkdir(exist_ok=True)
    path.write_text(_FAKE.format(python=sys.executable))
    path.chmod(0o755)
    return os.path.realpath(path)


def _no_os_profiles() -> Any:
    import types
    mod = types.ModuleType("fake_profiles")
    for name in ("seatbelt_profile", "bwrap_argv", "snapshot_tree", "remove_tree", "refuse_credentials",
                 "ConfinementError"):
        setattr(mod, name, getattr(SP, name))
    mod.available = lambda: None  # type: ignore[attr-defined]
    return mod


class Session:
    """One in-process entry-point run with real pipes; collects stdout, stderr and the status records."""

    def __init__(self, tmp_path: Path, guest: list[str], *, opts: list[str] = (), feed: bytes | None = b"",
                 module: str | None = None, resolver: Any = None, feed_delay: float = 0.0,
                 read_delay: float = 0.0, close_stdout: bool = False, status_fd: int | None = None,
                 feed_after: bytes | None = None, close_status: bool = False) -> None:
        self.root = project(tmp_path)
        in_r, in_w = os.pipe()
        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        st_r, st_w = os.pipe()
        self.out, self.err, self.st = bytearray(), bytearray(), bytearray()
        if close_stdout:
            os.close(out_r)
        if close_status:
            os.close(st_r)

        seen = threading.Event()

        def feeder() -> None:
            if feed is None:   # stdin stays open until main() returns; the test closes it then
                return
            time.sleep(feed_delay)
            if feed_after is not None:
                seen.wait(15)
            try:
                if feed:
                    os.write(in_w, feed)
            except BrokenPipeError:
                self.fed_epipe = True
            finally:
                os.close(in_w)

        def reader(fd: int, buf: bytearray, delay: float = 0.0) -> None:
            time.sleep(delay)
            while chunk := os.read(fd, 1 << 16):
                buf.extend(chunk)
                if feed_after is not None and feed_after in buf:
                    seen.set()
            os.close(fd)

        self.fed_epipe = False
        threads = [threading.Thread(target=feeder, daemon=True),
                   threading.Thread(target=reader, args=(err_r, self.err), daemon=True),
                   *([] if close_status else [threading.Thread(target=reader, args=(st_r, self.st), daemon=True)])]
        if not close_stdout:
            threads.append(threading.Thread(target=reader, args=(out_r, self.out, read_delay), daemon=True))
        for t in threads:
            t.start()
        seams = S._Seams(resolver=resolver or FakeResolver(_fake_wasmtime(tmp_path), "36.0.20"),
                         profiles=_no_os_profiles(), fds=(in_r, out_w, err_w), isolated=True)
        self.code = S.main(["run", "--project-root", str(self.root), "--status-fd", str(status_fd or st_w),
                            "--os-confine", "off", *opts, "--", module or os.path.realpath(ECHO), *guest],
                           _seams=seams)
        self.stdout_closed = _closed(out_w)
        for fd in (in_r, err_w, st_w):
            os.close(fd)
        if feed is None:
            os.close(in_w)
        for t in threads:
            t.join(10)
        self.records = [json.loads(line) for line in self.st.decode("ascii").splitlines()]
        self.end = self.records[-1] if self.records else {}

    def exec_left(self) -> list[str]:
        base = self.root / ".agentteams" / "wasm" / "exec"
        return os.listdir(base) if base.exists() else []


def _closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return True
    os.close(fd)
    return False


# --- the relay and the outcomes --------------------------------------------------------------------------------

def test_echo_round_trip_and_the_start_record(tmp_path: Path) -> None:
    s = Session(tmp_path, ["echo"], feed=b"ASK 1\nASK 2\n")
    assert s.code == S.EXIT_OK and bytes(s.out) == b"ASK 1\nASK 2\n"
    assert [r["event"] for r in s.records] == ["start", "end"]
    start = s.records[0]
    assert start["schema"] == S.SCHEMA and start["grant_spec_sha256"] == W.GRANT_SPEC_SHA256
    assert start["wasmtime_version"] == "36.0.20" and len(start["wasmtime_sha256"]) == 64
    assert start["argv_flags"][0:2] == ["-C", "cache=n"] and len(start["argv_sha256"]) == 64
    assert "agentteams_commit" in start and start["limits"]["grace_s"] == 15
    assert s.end["outcome"] == "exit" and s.end["guest_exit_code"] == 0
    assert (s.end["frames_in"], s.end["frames_out"]) == (2, 2) and s.end["cleanup"] == "ok"
    assert s.stdout_closed and s.exec_left() == []


def test_the_guest_exit_code_is_not_the_entry_points(tmp_path: Path) -> None:
    s = Session(tmp_path, ["exit7"])
    assert s.code == S.EXIT_OK and s.end["outcome"] == "exit" and s.end["guest_exit_code"] == 7


def test_trailing_args_reach_the_guest_unchanged(tmp_path: Path) -> None:
    s = Session(tmp_path, ["argv", "--steps", "3", "--augmented"])
    assert bytes(s.out) == b"argv|--steps|3|--augmented\n"


def test_an_over_cap_frame_ends_the_run_and_is_never_split(tmp_path: Path) -> None:
    s = Session(tmp_path, ["bigline"], opts=["--frame-cap", "262145"])
    assert s.end["outcome"] == "frame-cap" and s.end["detail"] == "out"
    assert b"y" not in s.out and s.end["guest_exit_code"] is None and s.end["guest_signal"] == "SIGKILL"
    assert s.exec_left() == []


def test_the_output_total_cap(tmp_path: Path) -> None:
    s = Session(tmp_path, ["flood"], opts=["--output-cap", str(1 << 20)])
    assert s.end["outcome"] == "output-cap" and len(s.out) <= (1 << 20) and s.exec_left() == []
    assert len(s.out) % 100 == 0   # whole frames only


def test_an_over_cap_parent_frame(tmp_path: Path) -> None:
    s = Session(tmp_path, ["echo"], opts=["--in-frame-cap", "16"], feed=b"z" * 100 + b"\n")
    assert s.end["outcome"] == "frame-cap" and s.end["detail"] == "in" and s.exec_left() == []


def test_the_host_wall_timeout(tmp_path: Path) -> None:
    t = time.monotonic()
    s = Session(tmp_path, ["spin"], opts=["--timeout", "1", "--grace", "1"], feed=None)
    assert s.end["outcome"] == "host-timeout" and s.end["detail"] == "wall"
    assert time.monotonic() - t < 15 and s.exec_left() == []


def test_the_idle_timeout_counts_only_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(S, "IDLE_BOUNDS", (1, 3600))
    s = Session(tmp_path, ["spin"], opts=["--idle-timeout", "1"], feed=None)
    assert s.end["outcome"] == "host-timeout" and s.end["detail"] == "idle" and s.exec_left() == []


def test_stdin_eof_kills_after_the_grace(tmp_path: Path) -> None:
    s = Session(tmp_path, ["spin"], opts=["--eof-grace", "0"])
    assert s.end["outcome"] == "stdin-eof-kill" and s.exec_left() == []


def test_stdin_eof_keep_lets_the_guest_finish(tmp_path: Path) -> None:
    s = Session(tmp_path, ["frames", "50"], opts=["--stdin-eof", "keep"])
    assert s.end["outcome"] == "exit" and s.end["frames_out"] == 50


def test_a_guest_that_closes_stdin_is_reported_not_hidden(tmp_path: Path) -> None:
    s = Session(tmp_path, ["closestdin"], feed=b"a\nb\n", feed_after=b"closed\n")
    assert s.end["outcome"] == "exit" and bytes(s.out) == b"closed\ndone\n"
    assert s.end["guest_stdin_closed"] is True and s.end["frames_undelivered"] == 2
    assert not s.fed_epipe   # the parent's own write still succeeds; the signal is the end record


def test_stderr_is_truncated_never_fatal(tmp_path: Path) -> None:
    s = Session(tmp_path, ["stderr"], opts=["--stderr-cap", "4096"])
    assert s.end["outcome"] == "exit" and bytes(s.out) == b"done\n"
    assert s.end["stderr_truncated"] is True and s.end["stderr_tail"].endswith("wasm trap: the tail\n")
    assert len(s.err) + s.end["stderr_dropped"] >= 1 << 20


def test_a_final_unterminated_line_is_relayed_as_is(tmp_path: Path) -> None:
    s = Session(tmp_path, ["partial"])
    assert bytes(s.out) == b"abc" and s.end["partial_final_line"] is True


def test_backpressure_drops_nothing(tmp_path: Path) -> None:
    s = Session(tmp_path, ["frames", "20000"], read_delay=1.0, opts=["--stdin-eof", "keep"])
    assert s.end["outcome"] == "exit" and s.end["frames_out"] == 20000
    assert bytes(s.out) == b"".join(b"f%06d\n" % i for i in range(20000))


def test_a_parent_that_stops_reading_stdout_is_gone(tmp_path: Path) -> None:
    s = Session(tmp_path, ["flood"], close_stdout=True)
    assert s.end["outcome"] == "parent-gone" and s.exec_left() == []


def test_a_stop_signal_ends_the_session(tmp_path: Path) -> None:
    root = project(tmp_path)
    launch = W.materialize(W.prepare(ECHO, grant=W.COMPUTE_ONLY), ["spin"], project_root=root, os_confine="off",
                           resolver=FakeResolver(_fake_wasmtime(tmp_path), "1"), profiles=_no_os_profiles())
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    t = time.monotonic()
    try:
        res = S.relay_session(launch, stdin_fd=in_r, stdout_fd=out_w, stderr_fd=2,
                              stop=lambda: "SIGTERM" if time.monotonic() - t > 0.3 else None)
    finally:
        launch.cleanup()
        for fd in (in_r, in_w, out_r):
            os.close(fd)
    assert res.outcome == "signal:SIGTERM" and res.guest_exit_code is None


def test_a_bad_parent_fd_never_orphans_the_guest(tmp_path: Path) -> None:
    root = project(tmp_path)
    pidfile = tmp_path / "guest.pid"
    launch = W.materialize(W.prepare(ECHO, grant=W.COMPUTE_ONLY), ["pidfile", str(pidfile)], project_root=root,
                           os_confine="off", resolver=FakeResolver(_fake_wasmtime(tmp_path), "1"),
                           profiles=_no_os_profiles())
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    os.close(in_r)   # the parent's stdin is already closed: setting it non-blocking fails after the spawn
    try:
        with pytest.raises(OSError):
            S.relay_session(launch, stdin_fd=in_r, stdout_fd=out_w, stderr_fd=2)
    finally:
        time.sleep(1.0)   # an orphaned guest would have written its pid by now (before its files go), still alive
        launch.cleanup()
        os.close(in_w)
        os.close(out_r)
    if pidfile.exists() and pidfile.read_text():
        pid = int(pidfile.read_text())
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        if alive:
            os.kill(pid, 9)
        assert not alive, "the guest outlived relay_session's failure (orphaned)"


def test_bench_refuses_a_relative_module(tmp_path: Path) -> None:
    code = S.main(["bench", "--project-root", str(project(tmp_path)), "--asks", "1", "session_echo.wasm"],
                  _seams=S._Seams(resolver=FakeResolver(_fake_wasmtime(tmp_path), "1"), isolated=True))
    assert code == S.EXIT_REFUSED


# --- the status channel (S1-S3) ----------------------------------------------------------------------------------

def test_the_guest_never_holds_the_status_fd(tmp_path: Path) -> None:
    st_r, st_w = os.pipe()
    try:
        s = Session(tmp_path, ["fd", str(st_w)], status_fd=st_w)
    finally:
        os.close(st_r)
        os.close(st_w)
    assert bytes(s.out) == b"EBADF\n"


@pytest.mark.parametrize("which", ["low", "stdout", "readonly", "closed"])
def test_a_bad_status_fd_is_a_usage_error(tmp_path: Path, which: str) -> None:
    r, w = os.pipe()
    fd = {"low": 2, "stdout": os.dup(1), "readonly": r, "closed": 999}[which]
    if which == "stdout" and stat.S_ISCHR(os.fstat(1).st_mode):
        pytest.skip("stdout is a terminal here")
    try:
        code = S.main(["run", "--project-root", str(tmp_path), "--status-fd", str(fd), "--", str(ECHO)],
                      _seams=S._Seams(isolated=True))
    finally:
        os.close(r)
        os.close(w)
        if which == "stdout":
            os.close(fd)
    assert code == S.EXIT_USAGE


def test_guest_text_cannot_forge_a_status_record(tmp_path: Path) -> None:
    s = Session(tmp_path, ["inject"])
    assert [r["event"] for r in s.records] == ["start", "end"]   # one line per record, all JSON
    assert s.end["outcome"] == "exit" and s.end["guest_exit_code"] == 1
    assert all(b < 0x80 for b in s.st) and b"\r" not in s.st
    assert s.end["trap"].startswith("trap:") and "\r" in s.end["trap"]   # decoded back: data, escaped on the wire


def test_exactly_one_terminal_record() -> None:
    r, w = os.pipe()
    ch = S.StatusChannel(w)
    ch.write({"event": "end"}, terminal=True)
    with pytest.raises(RuntimeError):
        ch.write({"event": "end"}, terminal=True)
    os.close(w)
    assert os.read(r, 4096).count(b"\n") == 1
    os.close(r)


# --- refusals and usage ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("opts, guest, why", [
    (["--fuel", "0"], [], "fuel"),
    (["--timeout", "3601"], [], "timeout_s"),
    (["--timeout", "3600", "--grace", "120", "--deadline", "3600"], [], "deadline"),
    (["--frame-cap", "1000"], [], "frame_cap"),
    (["--grace", "0"], [], "grace"),
    ([], ["bad\0arg"], "NUL"),
])
def test_out_of_bounds_input_is_a_refusal(tmp_path: Path, opts: list[str], guest: list[str], why: str) -> None:
    s = Session(tmp_path, guest, opts=opts)
    assert s.code == S.EXIT_REFUSED and [r["event"] for r in s.records] == ["refused"]
    assert why in s.end["reason"]
    assert s.exec_left() == []


def test_a_relative_module_is_refused(tmp_path: Path) -> None:
    s = Session(tmp_path, [], module="session_echo.wasm")
    assert s.code == S.EXIT_REFUSED and "absolute" in s.end["reason"]


def test_usage_errors_exit_64(tmp_path: Path) -> None:
    assert S.main(["run", "--bogus"], _seams=S._Seams(isolated=True)) == S.EXIT_USAGE
    assert S.main(["run", "--project-root", str(tmp_path), "--", str(ECHO)],
                  _seams=S._Seams(isolated=False)) == S.EXIT_USAGE   # S7: -I is required


def test_check_caps_accepts_the_defaults_and_zero_totals() -> None:
    S.check_caps(S.SessionCaps())
    S.check_caps(S.SessionCaps(output_cap=0, input_cap=0))
    S.check_caps(S.SessionCaps(frame_cap=262_145, idle_timeout_s=10, eof_grace_s=0, stdin_eof="keep"))


def test_check_caps_refuses_each_out_of_bound_value() -> None:
    for bad in (S.SessionCaps(frame_cap=65_537), S.SessionCaps(stderr_cap=1), S.SessionCaps(eof_grace_s=61),
                S.SessionCaps(idle_timeout_s=5), S.SessionCaps(stdin_eof="drop")):  # type: ignore[arg-type]
        with pytest.raises(W.WasmExecError):
            S.check_caps(bad)


def test_the_input_total_cap(tmp_path: Path) -> None:
    s = Session(tmp_path, ["echo"], opts=["--input-cap", str(1 << 20), "--stdin-eof", "keep"],
                feed=(b"q" * 1023 + b"\n") * 1100)
    assert s.end["outcome"] == "input-cap" and s.exec_left() == []


@pytest.mark.parametrize("name", ["SIGTERM", "SIGINT", "SIGHUP"])
def test_a_real_signal_ends_the_session_and_cleans_up(tmp_path: Path, name: str) -> None:
    num = getattr(signal, name)
    previous = signal.signal(num, lambda *_a: None)   # an early delivery must not kill the test run
    timer = threading.Timer(1.0, os.kill, (os.getpid(), num))
    timer.start()
    try:
        s = Session(tmp_path, ["spin"], feed=None)
    finally:
        timer.cancel()
        signal.signal(num, previous)
    assert s.end["outcome"] == f"signal:{name}" and s.exec_left() == []


def test_a_closed_status_channel_still_cleans_up(tmp_path: Path) -> None:
    s = Session(tmp_path, ["echo"], feed=b"a\n", close_status=True)
    assert s.code == S.EXIT_OK and bytes(s.out) == b"a\n" and s.exec_left() == []


def test_an_internal_error_is_exit_70_with_the_type_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any) -> int:
        raise RuntimeError("/secret/path/in/the/message")
    monkeypatch.setattr(S, "_run", boom)
    s = Session(tmp_path, ["echo"])
    assert s.code == S.EXIT_INTERNAL and s.end == {"schema": S.SCHEMA, "event": "end", "outcome": "internal",
                                                   "detail": "RuntimeError"}


def test_the_commit_is_a_full_sha_or_none() -> None:
    commit = S.agentteams_commit()
    assert commit is None or (len(commit) == 40 and all(c in "0123456789abcdef" for c in commit))


# --- the reaper (S5) ---------------------------------------------------------------------------------------------

def _run_dir(base: Path, name: str, *, age: float, owner: str | None) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "bin").mkdir()
    (d / "bin" / "f").write_text("x")
    if owner is not None:
        (d / S.OWNER_FILE).write_text(owner)
    old = time.time() - age
    os.utime(d, (old, old))
    return d


def test_the_reaper_removes_only_old_dead_well_named_dirs(tmp_path: Path) -> None:
    root = project(tmp_path)
    base = root / ".agentteams" / "wasm" / "exec"
    dead = _run_dir(base, "a" * 16, age=5000, owner="999999999\n")
    live = _run_dir(base, "b" * 16, age=5000, owner=f"{os.getpid()}\n")
    young = _run_dir(base, "c" * 16, age=10, owner="999999999\n")
    odd = _run_dir(base, "not-a-run-dir", age=5000, owner=None)
    no_owner = _run_dir(base, "d" * 16, age=5000, owner=None)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (base / ("e" * 16)).symlink_to(outside, target_is_directory=True)
    removed = S.reap_stale_runs(root, SP)
    assert sorted(removed) == sorted(str(p) for p in (dead, no_owner))
    assert live.exists() and young.exists() and odd.exists() and (outside / "keep").exists()


def test_the_reaper_is_bounded_per_start(tmp_path: Path) -> None:
    root = project(tmp_path)
    base = root / ".agentteams" / "wasm" / "exec"
    for i in range(S.REAP_MAX + 4):
        _run_dir(base, f"{i:016x}", age=5000, owner="999999999\n")
    assert len(S.reap_stale_runs(root, SP)) == S.REAP_MAX


def test_a_symlinked_control_plane_is_never_entered(tmp_path: Path) -> None:
    root = project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    victim = _run_dir(elsewhere / "wasm" / "exec", "a" * 16, age=5000, owner="999999999\n")
    (root / ".agentteams").symlink_to(elsewhere, target_is_directory=True)
    assert S.reap_stale_runs(root, SP) == [] and victim.exists()


def test_a_symlinked_owner_file_is_not_followed(tmp_path: Path) -> None:
    root = project(tmp_path)
    base = root / ".agentteams" / "wasm" / "exec"
    d = _run_dir(base, "f" * 16, age=5000, owner=None)
    pidfile = tmp_path / "pid"
    pidfile.write_text(f"{os.getpid()}\n")
    (d / S.OWNER_FILE).symlink_to(pidfile)
    old = time.time() - 5000
    os.utime(d, (old, old))
    assert S.reap_stale_runs(root, SP) == [str(d)] and pidfile.exists()


# --- live (dev wasmtime) -----------------------------------------------------------------------------------------

@needs_wasmtime
def test_live_the_guest_sees_its_basename_and_trailing_augmented(tmp_path: Path) -> None:
    s = Session(tmp_path, ["--steps", "3", "--augmented"], resolver=FakeResolver(dev_wasmtime()), feed=b"ASK 1\n")
    assert bytes(s.out) == b"session_echo\n--steps\n3\n--augmented\nASK 1\n"   # argv[0], args..., then the echo
    assert s.end["outcome"] == "exit" and s.end["guest_exit_code"] == 0 and s.exec_left() == []


@needs_wasmtime
def test_live_the_engine_timeout_is_diagnostic(tmp_path: Path) -> None:
    s = Session(tmp_path, [], resolver=FakeResolver(dev_wasmtime()), opts=["--timeout", "1", "--grace", "5",
                                                                           "--stdin-eof", "keep"],
                feed=None)
    assert s.end["outcome"] == "exit" and s.end["trap"] == "timeout"   # the engine's, not the host's


@needs_wasmtime
def test_live_bench_reports_both_legs_and_writes_no_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    code = S.main(["bench", "--project-root", str(root), "--asks", "200", "--os-confine", "off",
                   "--unconfined-baseline", os.path.realpath(ECHO)],
                  _seams=S._Seams(resolver=FakeResolver(dev_wasmtime()), isolated=True))
    report = json.loads(capsys.readouterr().out)
    assert code == S.EXIT_OK and report["asks"] == 200
    assert report["session"]["outcome"] == "exit" and report["session"]["us_per_ask"] > 0
    assert report["baseline_unconfined"]["os_confinement"] == "none"
    assert not (root / ".agentteams" / "wasm" / "exec").exists() or not os.listdir(root / ".agentteams/wasm/exec")


@needs_wasmtime
@pytest.mark.skipif(not sys.platform.startswith("linux") or SP.available() != "bwrap", reason="Linux bwrap only")
def test_live_linux_killing_the_group_leaves_no_wasmtime(tmp_path: Path) -> None:
    s = Session(tmp_path, [], resolver=FakeResolver(dev_wasmtime()), opts=["--os-confine", "require",
                                                                           "--eof-grace", "0"])
    assert s.end["outcome"] in ("exit", "stdin-eof-kill") and s.records[0]["os_confinement"] == "nested"
    time.sleep(0.5)
    run_id = s.records[0]["run_id"]
    survivors = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            cmdline = b""
        if run_id.encode() in cmdline:
            survivors.append(pid)
    assert survivors == []
