"""Streaming session mode for :mod:`agentteams.wasm_exec`: one long-lived guest, framed stdio, a host status channel.

``wasm_exec.run`` is batch-shaped: it writes the whole stdin, closes it, and collects output under one cap. A
consumer whose guest asks the host a question and blocks in ``fd_read(0)`` for the reply, many times per process,
needs the process kept alive and its stdio relayed frame by frame (#567). This module adds that, reusing
``wasm_exec``'s prepare/materialize unchanged: the same pinned grant, limits, empty environment, verified copies and
OS nesting. It adds no wasmtime flag.

Channels (the contract on #567): fd 0 is relayed to the guest's stdin and the guest's stdout to fd 1, byte for byte,
one complete line at a time; wasmtime's stderr is forwarded to fd 2 best effort and never blocks; host status records
(JSON Lines, :data:`SCHEMA`) go to a status fd the guest never holds. Everything the guest writes is data (C-4); only
the host-set ``outcome`` values may drive a decision, and ``trap`` stays diagnostic.

Entry point: ``python -I -m agentteams.wasm_session run --project-root DIR [options] -- MODULE [guest args...]``.
Status: beta (1.3.0).
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
import re
import selectors
import signal
import stat
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Literal, Sequence

_T_IMPORT = time.monotonic()

from agentteams import wasm_exec as W  # noqa: E402  (after the import-time stamp)

#: The status-record schema; a breaking change to flags, records or exit codes bumps its major.
SCHEMA = "agentteams-wasm-session/1"

#: Entry-point exit codes. The guest's own code appears only in ``end.guest_exit_code``.
EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_INTERNAL = 0, 3, 64, 70

#: Inclusive byte bounds per cap; ``0`` (no total cap) is also allowed for the two totals.
CAP_BOUNDS: dict[str, tuple[int, int]] = {
    "frame_cap": (262_145, 16 << 20),        # 65,536 four-byte characters plus the newline
    "in_frame_cap": (16, 16 << 20),
    "output_cap": (1 << 20, 4 << 30),
    "input_cap": (1 << 20, 4 << 30),
    "stderr_cap": (4 << 10, 16 << 20),
}
GRACE_BOUNDS, EOF_GRACE_BOUNDS, IDLE_BOUNDS = (1, W.MAX_COMPILE_GRACE_S), (0, 60), (10, 3600)
#: How long the frame in flight may take to reach the parent after the guest is gone.
DRAIN_S = 2.0
STDERR_TAIL = 4096
_CHUNK = 1 << 16
#: The reaper: run-directory names ``secrets.token_hex(8)`` makes, the age floor (longer than any session can
#: live: 3600 s engine + 120 s grace + 60 s EOF grace + slack) and the most directories removed per start.
_RUN_ID_RE = re.compile(r"^[0-9a-f]{16}$", re.ASCII)
REAP_MIN_AGE_S, REAP_MAX = 3840, 16
OWNER_FILE = "owner"

@dataclass(frozen=True)
class SessionCaps:
    """Relay limits for one session; :func:`check_caps` validates them (bytes and seconds)."""

    frame_cap: int = 1 << 20
    in_frame_cap: int = 64 << 10
    output_cap: int = 256 << 20
    input_cap: int = 64 << 20
    stderr_cap: int = 256 << 10
    stdin_eof: Literal["kill", "keep"] = "kill"
    eof_grace_s: int = 5
    idle_timeout_s: int | None = None


def check_caps(caps: SessionCaps) -> None:
    """Refuse caps outside :data:`CAP_BOUNDS` and the grace/idle bounds.

    Args:
        caps: The caps to check.

    Returns:
        None.

    Raises:
        WasmExecError: Naming the first value out of bounds.
    """
    for name, (lo, hi) in CAP_BOUNDS.items():
        v = getattr(caps, name)
        zero_ok = name in ("output_cap", "input_cap") and v == 0
        if type(v) is not int or not (zero_ok or lo <= v <= hi):
            raise W.WasmExecError(f"{name}={v!r} outside [{lo}, {hi}]{' (or 0)' if name.endswith('put_cap') else ''}")
    if caps.stdin_eof not in ("kill", "keep"):
        raise W.WasmExecError(f"stdin_eof={caps.stdin_eof!r} must be 'kill' or 'keep'")
    if type(caps.eof_grace_s) is not int or not EOF_GRACE_BOUNDS[0] <= caps.eof_grace_s <= EOF_GRACE_BOUNDS[1]:
        raise W.WasmExecError(f"eof_grace_s={caps.eof_grace_s!r} outside {list(EOF_GRACE_BOUNDS)}")
    idle = caps.idle_timeout_s
    if idle is not None and (type(idle) is not int or not IDLE_BOUNDS[0] <= idle <= IDLE_BOUNDS[1]):
        raise W.WasmExecError(f"idle_timeout_s={idle!r} outside {list(IDLE_BOUNDS)}")


@dataclass
class SessionResult:
    """What :func:`relay_session` observed. ``outcome`` other than ``exit`` is host-set; ``trap`` is diagnostic."""

    outcome: str = "exit"
    detail: str | None = None
    guest_exit_code: int | None = None
    guest_signal: str | None = None
    trap: str | None = None
    frames_out: int = 0
    frames_in: int = 0
    frames_undelivered: int = 0
    frames_dropped: int = 0
    bytes_out: int = 0
    bytes_in: int = 0
    partial_final_line: bool = False
    last_frame_incomplete: bool = False
    guest_stdin_closed: bool = False
    stderr_dropped: int = 0
    stderr_truncated: bool = False
    stderr_tail: bytes = b""
    wall_s: float = 0.0
    spawn_to_first_frame: float | None = None
    relay_latency_p50: float | None = None
    relay_latency_p99: float | None = None


# --- the relay -----------------------------------------------------------------------------------------------------

def _set_nonblocking(fd: int) -> int:
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    return flags


def _pctl(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 6)


def _parent_death_hook() -> Callable[[], None] | None:
    """Linux only: a ``preexec_fn`` that ties the child (bwrap or wasmtime) to this process with PR_SET_PDEATHSIG.

    libc and the parent pid are resolved here, before the fork, so the child only calls ``prctl`` and ``getppid``.
    Safe because the entry point spawns before it starts anything else (no threads).
    """
    if not sys.platform.startswith("linux"):
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    prctl, parent = libc.prctl, os.getpid()

    def hook() -> None:
        if prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0 or os.getppid() != parent:  # 1 = PR_SET_PDEATHSIG
            os._exit(127)
    return hook


class _Relay:
    """One session's single-threaded ``selectors`` loop (see :func:`relay_session`)."""

    def __init__(self, proc: Any, fds: tuple[int, int, int], caps: SessionCaps, timeout_s: int,
                 stop: Callable[[], str | None]) -> None:
        self.proc, self.caps, self.stop = proc, caps, stop
        self.p_in, self.p_out, self.p_err = fds
        self.g_in, self.g_out, self.g_err = proc.stdin.fileno(), proc.stdout.fileno(), proc.stderr.fileno()
        self.r = SessionResult()
        self.t0 = time.monotonic()
        self.deadline = self.t0 + timeout_s
        self.last_activity = self.t0
        self.out_buf, self.out_q, self.out_off = bytearray(), deque(), 0   # guest -> parent
        self.in_buf, self.in_q, self.in_off = bytearray(), deque(), 0      # parent -> guest
        self.err_kept, self.err_tail = 0, bytearray()   # stderr bytes counted against the cap; the rolling tail
        self.out_eof = self.err_eof = self.p_in_eof = self.g_in_closed = False
        self.p_out_gone = False
        self.killed_at: float | None = None
        self.eof_deadline: float | None = None
        self.latencies: list[float] = []

    # -- ending -------------------------------------------------------------------------------------------------
    def kill(self, outcome: str, detail: str | None = None) -> None:
        if self.killed_at is None:   # the first host-set outcome wins, as in wasm_exec._execute
            self.r.outcome, self.r.detail = outcome, detail
            self.killed_at = time.monotonic()
        W._kill_group(self.proc)
        self._close_guest_stdin()

    def _close_guest_stdin(self) -> None:
        if not self.g_in_closed:
            self.g_in_closed = True
            self.in_q.clear()
            self.proc.stdin.close()

    # -- guest stdout -> parent -----------------------------------------------------------------------------------
    def _read_guest_out(self) -> None:
        try:
            chunk = os.read(self.g_out, _CHUNK)
        except BlockingIOError:
            return
        if not chunk:
            self.out_eof = True
            if self.out_buf and self.killed_at is None:   # a final unterminated line, relayed as is
                self.r.partial_final_line = True
                self._queue_out(bytes(self.out_buf))
            self.out_buf.clear()
            return
        if self.killed_at is not None:   # after a kill, nothing new is framed
            return
        self.r.bytes_out += len(chunk)
        if self.caps.output_cap and self.r.bytes_out > self.caps.output_cap:
            return self.kill("output-cap")
        self.out_buf += chunk
        while (nl := self.out_buf.find(b"\n")) >= 0:
            if nl + 1 > self.caps.frame_cap:
                return self.kill("frame-cap", "out")
            self._queue_out(bytes(self.out_buf[:nl + 1]))
            del self.out_buf[:nl + 1]
        if len(self.out_buf) > self.caps.frame_cap:
            return self.kill("frame-cap", "out")

    def _queue_out(self, frame: bytes) -> None:
        self.out_q.append(frame)
        self.r.frames_out += 1
        self.last_activity = time.monotonic()
        if self.r.spawn_to_first_frame is None:
            self.r.spawn_to_first_frame = round(self.last_activity - self.t0, 6)

    def _write_parent_out(self) -> None:
        while self.out_q:
            frame = self.out_q[0]
            try:
                n = os.write(self.p_out, frame[self.out_off:])
            except BlockingIOError:
                return
            except BrokenPipeError:
                self.p_out_gone = True
                self.out_q.clear()
                return self.kill("parent-gone", "stdout")
            self.out_off += n
            if self.out_off < len(frame):
                return
            self.out_q.popleft()
            self.out_off = 0

    # -- parent -> guest stdin ------------------------------------------------------------------------------------
    def _read_parent_in(self) -> None:
        try:
            chunk = os.read(self.p_in, _CHUNK)
        except BlockingIOError:
            return
        now = time.monotonic()
        if not chunk:
            self.p_in_eof = True
            if self.in_buf:   # an unterminated last frame is still data; deliver it
                self._queue_in(bytes(self.in_buf), now)
                self.in_buf.clear()
            if not self.in_q:
                self._on_parent_eof(now)
            return
        self.r.bytes_in += len(chunk)
        if self.caps.input_cap and self.r.bytes_in > self.caps.input_cap:
            return self.kill("input-cap")
        self.in_buf += chunk
        while (nl := self.in_buf.find(b"\n")) >= 0:
            if nl + 1 > self.caps.in_frame_cap:
                return self.kill("frame-cap", "in")
            self._queue_in(bytes(self.in_buf[:nl + 1]), now)
            del self.in_buf[:nl + 1]
        if len(self.in_buf) > self.caps.in_frame_cap:
            return self.kill("frame-cap", "in")

    def _queue_in(self, frame: bytes, now: float) -> None:
        self.r.frames_in += 1
        self.last_activity = now
        if self.g_in_closed:
            self.r.frames_undelivered += 1
        else:
            self.in_q.append((frame, now))

    def _on_parent_eof(self, now: float) -> None:
        self._close_guest_stdin()
        if self.caps.stdin_eof == "kill" and self.eof_deadline is None:
            self.eof_deadline = now + self.caps.eof_grace_s

    def _write_guest_in(self) -> None:
        while self.in_q and not self.g_in_closed:
            frame, read_at = self.in_q[0]
            try:
                n = os.write(self.g_in, frame[self.in_off:])
            except BlockingIOError:
                return
            except BrokenPipeError:   # the guest closed its stdin (I5): count, keep relaying its stdout
                self.r.guest_stdin_closed = True
                self.r.frames_undelivered += len(self.in_q)
                self.in_off = 0
                self.g_in_closed = True
                self.in_q.clear()
                self.proc.stdin.close()
                return
            self.in_off += n
            if self.in_off < len(frame):
                return
            self.in_q.popleft()
            self.in_off = 0
            self.latencies.append(time.monotonic() - read_at)
        if self.p_in_eof and not self.in_q:
            self._on_parent_eof(time.monotonic())

    # -- stderr ---------------------------------------------------------------------------------------------------
    def _read_guest_err(self) -> None:
        try:
            chunk = os.read(self.g_err, _CHUNK)
        except BlockingIOError:
            return
        if not chunk:
            self.err_eof = True
            return
        room = self.caps.stderr_cap - self.err_kept - STDERR_TAIL
        self.err_kept += max(0, min(room, len(chunk)))
        if len(chunk) > max(room, 0):
            self.r.stderr_truncated = True
        self.err_tail = (self.err_tail + chunk)[-STDERR_TAIL:]
        try:   # forwarded best effort: a parent that does not read fd 2 never stalls the guest
            n = os.write(self.p_err, chunk)
        except (BlockingIOError, BrokenPipeError):
            n = 0
        self.r.stderr_dropped += len(chunk) - n

    # -- the loop -------------------------------------------------------------------------------------------------
    def run(self) -> SessionResult:
        sel = selectors.DefaultSelector()
        for fd in (self.g_out, self.g_err, self.p_in):
            sel.register(fd, selectors.EVENT_READ)
        masks = {self.g_out: selectors.EVENT_READ, self.g_err: selectors.EVENT_READ, self.p_in: selectors.EVENT_READ}

        def want(fd: int, event: int, on: bool) -> None:
            old = masks.get(fd, 0)
            new = (old | event) if on else (old & ~event)
            if new == old:
                return
            masks[fd] = new
            if old and new:
                sel.modify(fd, new)
            elif old:
                sel.unregister(fd)
            else:
                sel.register(fd, new)

        while True:
            now = time.monotonic()
            if (why := self.stop()) and self.killed_at is None:
                self.kill(f"signal:{why}")
            if self.killed_at is None:
                if now >= self.deadline:
                    self.kill("host-timeout", "wall")
                elif self.caps.idle_timeout_s and now - self.last_activity >= self.caps.idle_timeout_s:
                    self.kill("host-timeout", "idle")
                elif self.eof_deadline is not None and now >= self.eof_deadline and self.proc.poll() is None:
                    self.kill("stdin-eof-kill")
            drained = not self.out_q
            guest_done = self.out_eof and self.err_eof
            if guest_done and drained:
                break
            if self.killed_at is not None and now - self.killed_at >= DRAIN_S and self.out_eof:
                break
            if self.killed_at is not None and now - self.killed_at >= DRAIN_S + W.POST_KILL_DRAIN_S:
                break   # a wedged pipe holder: stop waiting, teardown follows
            want(self.g_out, selectors.EVENT_READ, not self.out_eof and not self.out_q)
            want(self.g_err, selectors.EVENT_READ, not self.err_eof)
            want(self.p_in, selectors.EVENT_READ, not self.p_in_eof and not self.in_q and self.killed_at is None)
            want(self.p_out, selectors.EVENT_WRITE, bool(self.out_q) and not self.p_out_gone)
            want(self.g_in, selectors.EVENT_WRITE, bool(self.in_q) and not self.g_in_closed)
            for key, events in sel.select(timeout=0.25):
                fd = key.fd
                if events & selectors.EVENT_WRITE and fd == self.p_out:
                    self._write_parent_out()
                if events & selectors.EVENT_WRITE and fd == self.g_in and not self.g_in_closed:
                    self._write_guest_in()
                if events & selectors.EVENT_READ:
                    {self.g_out: self._read_guest_out, self.g_err: self._read_guest_err,
                     self.p_in: self._read_parent_in}[fd]()
        sel.close()
        if self.out_q:   # frames the parent never took before the drain ended
            self.r.last_frame_incomplete = self.out_off > 0
            self.r.frames_dropped = len(self.out_q) - (1 if self.out_off > 0 else 0)
        return self.r


def relay_session(launch: W.Launch, *, stdin_fd: int = 0, stdout_fd: int = 1, stderr_fd: int = 2,
                  caps: SessionCaps = SessionCaps(), stop: Callable[[], str | None] = lambda: None,
                  parent_death: bool = False) -> SessionResult:
    """Run an already-materialized launch as a streaming session; the caller owns ``launch.cleanup()``.

    One wasmtime process group per call, alive until the guest exits or the host ends it. The parent's fds are
    switched to non-blocking for the session and restored afterwards; *stdout_fd* is closed before this returns,
    so a parent reads stdout EOF before any terminal record the caller writes next.

    Args:
        launch: From :func:`agentteams.wasm_exec.materialize`.
        stdin_fd: Read for frames to the guest.
        stdout_fd: Written with the guest's stdout frames; closed on return.
        stderr_fd: Written with wasmtime's stderr, best effort.
        caps: Relay limits (:func:`check_caps`).
        stop: Polled each turn; a non-empty return (a signal name) ends the session as ``signal:<name>``.
        parent_death: Linux: tie the child to this process with PR_SET_PDEATHSIG (no other threads may run).

    Returns:
        A :class:`SessionResult`.

    Raises:
        WasmExecError: Bad caps, or wasmtime could not start.
    """
    import subprocess

    check_caps(caps)
    hook = _parent_death_hook() if parent_death else None
    group: dict[str, Any] = {"start_new_session": True}
    if hook is not None:
        group["preexec_fn"] = hook
    try:
        proc = subprocess.Popen(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, close_fds=True,
                                bufsize=0, **group)
    except OSError as exc:
        raise W.WasmExecError(f"wasmtime could not start: {exc}") from exc
    saved: dict[int, int] = {}
    try:   # everything after the spawn sits inside this try: a bad parent fd must never orphan the guest
        for fd in (stdin_fd, stdout_fd, stderr_fd):
            saved[fd] = _set_nonblocking(fd)
        for fd in (proc.stdin.fileno(), proc.stdout.fileno(), proc.stderr.fileno()):
            _set_nonblocking(fd)
        relay = _Relay(proc, (stdin_fd, stdout_fd, stderr_fd), caps, launch.timeout_s, stop)
        result = relay.run()
    finally:
        W._kill_group(proc)
        code = proc.wait()
        for fd, flags in saved.items():
            _restore_flags(fd, flags)
        _close_parent_stdout(stdout_fd)
    result.guest_exit_code = code if code >= 0 else None
    result.guest_signal = _signal_name(-code) if code < 0 else None
    result.stderr_tail = bytes(relay.err_tail)
    result.trap = W.classify_trap(code, result.stderr_tail)
    result.wall_s = round(time.monotonic() - relay.t0, 3)
    result.relay_latency_p50, result.relay_latency_p99 = _pctl(relay.latencies, 0.5), _pctl(relay.latencies, 0.99)
    return result


def _signal_name(num: int) -> str:
    try:
        return signal.Signals(num).name
    except ValueError:
        return f"signal {num}"


def _close_parent_stdout(fd: int) -> None:
    try:
        os.close(fd)
    except OSError as exc:   # already closed by the parent; the original error, if any, still propagates
        _note(f"fd {fd} not closed ({exc.strerror})")


def _restore_flags(fd: int, flags: int) -> None:
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)
    except OSError as exc:   # the parent already closed it; nothing left to restore
        _note(f"fd {fd} flags not restored ({exc.strerror})")


def _note(text: str) -> None:
    try:
        os.write(2, f"agentteams.wasm_session: {text}\n".encode("utf-8", "replace"))
    except OSError:
        return None   # fd 2 is gone too; the terminal record still carries the outcome


def _is_dev_null(st: os.stat_result) -> bool:
    try:
        null = os.stat(os.devnull)
    except OSError:
        return False
    return (null.st_dev, null.st_ino) == (st.st_dev, st.st_ino)


# --- the status channel --------------------------------------------------------------------------------------------

class StatusChannel:
    """Host status records on a verified fd: one complete JSON line per record, written only by the entry point,
    and exactly one terminal record (S1-S3)."""

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.terminal = False
        self.gone = False

    @staticmethod
    def verify(fd: int) -> None:
        """Refuse an fd that is not a distinct, writable channel, then make it non-inheritable.

        Args:
            fd: The status fd.

        Returns:
            None.

        Raises:
            WasmExecError: fd ≤ 2, not open for writing, or the same file as fd 0, 1 or 2.
        """
        if type(fd) is not int or fd <= 2:
            raise W.WasmExecError(f"--status-fd {fd!r} must be 3 or more")
        try:
            st = os.fstat(fd)
            mode = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        except OSError as exc:
            raise W.WasmExecError(f"--status-fd {fd} is not open ({exc.strerror})") from exc
        if mode not in (os.O_WRONLY, os.O_RDWR):
            raise W.WasmExecError(f"--status-fd {fd} is not open for writing")
        for std in (0, 1, 2):
            try:
                other = os.fstat(std)
            except OSError:
                other = None
            if other is not None and (other.st_dev, other.st_ino) == (st.st_dev, st.st_ino) and \
                    not _is_dev_null(st):   # a shared terminal would mix host records with guest output
                raise W.WasmExecError(f"--status-fd {fd} is the same file as fd {std}; host records would mix "
                                      "with guest data")
        os.set_inheritable(fd, False)

    def write(self, record: dict[str, Any], *, terminal: bool = False) -> None:
        """Write one record as a single line; a second terminal record is refused.

        Args:
            record: The record (``event`` and ``schema`` are added).
            terminal: True for ``refused`` / ``end``.

        Returns:
            None.

        Raises:
            RuntimeError: A second terminal record.
        """
        if terminal and self.terminal:
            raise RuntimeError("a terminal status record was already written")
        self.terminal = self.terminal or terminal
        line = json.dumps({"schema": SCHEMA, **record}, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(",", ":")) + "\n"
        data = line.encode("ascii")
        try:
            os.set_blocking(self.fd, True)
            while data:
                data = data[os.write(self.fd, data):]
        except BrokenPipeError:
            self.gone = True


# --- the reaper ----------------------------------------------------------------------------------------------------

def _owner_dead(run_dir: str) -> bool:
    try:
        fd = os.open(os.path.join(run_dir, OWNER_FILE), os.O_RDONLY | W._NOFOLLOW)
    except OSError:
        return True   # absent or a symlink (O_NOFOLLOW): never written by a live session, which is past the age floor
    try:
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(65)
    except OSError:
        return False   # unreadable: keep the directory rather than guess
    m = re.fullmatch(rb"([1-9][0-9]{0,9})\n?", raw)
    if m is None or int(m[1]) <= 1:
        return True
    try:
        os.kill(int(m[1]), 0)   # existence test only; never a real signal
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def reap_stale_runs(project_root: str | Path, profiles: ModuleType) -> list[str]:
    """Remove abandoned run directories under ``<root>/.agentteams/wasm/exec/`` (bounded, symlink-safe).

    A directory is removed only when its name matches ``[0-9a-f]{16}``, ``lstat`` says it is a real directory
    directly under the real ``exec/``, it is older than :data:`REAP_MIN_AGE_S`, and its owner is gone (a reused pid
    only keeps a directory, never removes a live one). At most :data:`REAP_MAX` per call, via ``profiles.remove_tree``.

    Args:
        project_root: The project root.
        profiles: The ``agentteams._sandbox_profiles`` module.

    Returns:
        The directories removed.

    Raises:
        Nothing.
    """
    base = os.path.realpath(str(project_root))
    try:
        for part in (*W._CONTROL_PLANE, "exec"):   # every component a real directory, nothing created (N1)
            base = os.path.join(base, part)
            if not stat.S_ISDIR(os.lstat(base).st_mode):
                return []
        names = sorted(os.listdir(base))
    except OSError:
        return []
    removed: list[str] = []
    now = time.time()
    for name in names:
        if len(removed) >= REAP_MAX:
            break
        path = os.path.join(base, name)
        try:
            st = os.lstat(path)
        except OSError:
            st = None
        if not _RUN_ID_RE.match(name) or st is None or not stat.S_ISDIR(st.st_mode):
            continue
        if now - st.st_mtime < REAP_MIN_AGE_S or not _owner_dead(path):
            continue
        try:
            profiles.remove_tree(path)
        except OSError as exc:
            _note(f"stale run directory {name} not removed ({type(exc).__name__})")
            continue
        removed.append(path)
    return removed


def _write_owner(run_dir: str) -> None:
    fd = os.open(os.path.join(run_dir, OWNER_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL | W._NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{os.getpid()}\n")


# --- the entry point -----------------------------------------------------------------------------------------------

class _Usage(Exception):
    """A usage error (exit 64)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Any:   # no SystemExit(2): usage errors are exit 64
        raise _Usage(message)


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -I -m agentteams.wasm_session", add_help=True,
                description="Run one WASM guest as a streaming session (beta; contract on agentteams #567).")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)
    r = sub.add_parser("run", help="relay stdio to one long-lived guest; status records on --status-fd")
    r.add_argument("--project-root", required=True)
    r.add_argument("--status-fd", type=int, default=3)
    r.add_argument("--os-confine", choices=("auto", "require", "off"), default="auto")
    r.add_argument("--fuel", type=int, default=W.DEFAULT_LIMITS.fuel)
    r.add_argument("--max-memory", type=int, default=W.DEFAULT_LIMITS.max_memory_bytes)
    r.add_argument("--timeout", type=int, default=W.DEFAULT_LIMITS.timeout_s, help="engine seconds, 1..3600")
    r.add_argument("--grace", type=int, default=W.DEFAULT_COMPILE_GRACE_S, help="host seconds over --timeout")
    r.add_argument("--deadline", type=int, default=None, help="refuse when timeout + grace exceeds this")
    d = SessionCaps()
    for name in CAP_BOUNDS:
        r.add_argument("--" + name.replace("_", "-"), type=int, default=getattr(d, name))
    r.add_argument("--stdin-eof", choices=("kill", "keep"), default=d.stdin_eof)
    r.add_argument("--eof-grace", type=int, default=d.eof_grace_s)
    r.add_argument("--idle-timeout", type=int, default=None)
    r.add_argument("module")
    r.add_argument("guest_args", nargs=argparse.REMAINDER)
    b = sub.add_parser("bench", help="per-ask relay overhead with an echo-style module (prints JSON; no status fd)")
    b.add_argument("--project-root", required=True)
    b.add_argument("--asks", type=int, default=10_000)
    b.add_argument("--os-confine", choices=("auto", "require", "off"), default="auto")
    b.add_argument("--timeout", type=int, default=600)
    b.add_argument("--unconfined-baseline", action="store_true",
                   help="also time a bare wasmtime spawn with no OS sandbox (the comparison leg)")
    b.add_argument("module")
    return p


@dataclass(frozen=True)
class _Seams:
    """Test-only injection for :func:`main` (keyword-only, unreachable from the command line)."""

    resolver: Any = None
    profiles: ModuleType | None = None
    fds: tuple[int, int, int] = (0, 1, 2)
    isolated: bool | None = None


def _runtime(seams: _Seams, root: str) -> tuple[Any, ModuleType, tuple[type, ...]]:
    from agentteams import _sandbox_profiles, wasm_runtime
    resolver = seams.resolver if seams.resolver is not None else wasm_runtime.Resolver(Path(root))
    return resolver, seams.profiles or _sandbox_profiles, (wasm_runtime.WasmRuntimeError,)


def _sha256_file(path: str) -> str:
    fd = os.open(path, os.O_RDONLY | W._NOFOLLOW)
    try:
        return W._sha256_fd(fd)
    finally:
        os.close(fd)


def _wasmtime_path(argv: Sequence[str]) -> str:
    for i in range(1, len(argv) - 1):
        if argv[i] == "run" and argv[i + 1] == "-C" and os.path.isabs(argv[i - 1]):
            return argv[i - 1]
    raise W.WasmExecError("no wasmtime in the launch argv")


_SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP")


def main(argv: Sequence[str] | None = None, *, _seams: _Seams = _Seams()) -> int:
    """``python -I -m agentteams.wasm_session run …``: one guest as a streaming session.

    Args:
        argv: Command-line arguments (default ``sys.argv[1:]``).

    Returns:
        0 after an ``end`` record, 3 after a ``refused`` record, 64 on a usage error (no record), 70 on an internal
        error (best-effort ``end`` with ``outcome:"internal"``). The guest's code is only in ``end.guest_exit_code``.

    Raises:
        Nothing.
    """
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
        if not (sys.flags.isolated if _seams.isolated is None else _seams.isolated):
            raise _Usage("run the entry point with python -I")
        if args.cmd == "bench":
            from agentteams import _wasm_session_bench
            return _wasm_session_bench.bench(args, _seams)
        StatusChannel.verify(args.status_fd)
    except (_Usage, W.WasmExecError) as exc:
        _note(f"usage: {exc}")
        return EXIT_USAGE
    status = StatusChannel(args.status_fd)
    flag: dict[str, str] = {}
    for name in _SIGNALS:
        signal.signal(getattr(signal, name), lambda num, _f: flag.setdefault("sig", signal.Signals(num).name))
    timings: dict[str, float] = {"import": round(time.monotonic() - _T_IMPORT, 6)}
    try:
        return _run(args, status, flag, timings, _seams)
    # The process boundary: TypeError/LookupError/AttributeError are programming errors, caught here only so the
    # parent still gets a terminal record (and exit 70) instead of a traceback that could carry paths.
    except (OSError, RuntimeError, ValueError, TypeError, LookupError, AttributeError, W.WasmExecError) as exc:
        if not status.terminal:   # type only: a message could carry paths (cf. the #551 hook-log condition)
            status.write({"event": "end", "outcome": "internal", "detail": type(exc).__name__}, terminal=True)
        return EXIT_INTERNAL


def _run(args: argparse.Namespace, status: StatusChannel, flag: dict[str, str], timings: dict[str, float],
         seams: _Seams) -> int:
    resolver, profiles, runtime_errors = _runtime(seams, args.project_root)
    t = time.monotonic()
    try:
        if args.deadline is not None and args.timeout + args.grace > args.deadline:
            raise W.WasmExecError(f"timeout {args.timeout} + grace {args.grace} exceeds --deadline {args.deadline}")
        if not GRACE_BOUNDS[0] <= args.grace <= GRACE_BOUNDS[1]:
            raise W.WasmExecError(f"grace {args.grace} outside {list(GRACE_BOUNDS)}")
        caps = SessionCaps(**{n: getattr(args, n) for n in CAP_BOUNDS}, stdin_eof=args.stdin_eof,
                           eof_grace_s=args.eof_grace, idle_timeout_s=args.idle_timeout)
        check_caps(caps)
        limits = W.Limits(fuel=args.fuel, max_memory_bytes=args.max_memory, timeout_s=args.timeout)
        guest_args = list(args.guest_args[1:] if args.guest_args[:1] == ["--"] else args.guest_args)
        W._check_args(guest_args)
        if not os.path.isabs(args.module):
            raise W.WasmExecError("MODULE must be an absolute path")
        prepared = W.prepare(args.module, grant=W.Grant((), limits), compile_grace_s=args.grace)
        timings["prepare"] = round(time.monotonic() - t, 6)
        root = os.path.realpath(args.project_root)
        reap_stale_runs(root, profiles)   # never raises: a failure is noted, not a refusal
        t = time.monotonic()
        launch = W.materialize(prepared, guest_args, project_root=root, os_confine=args.os_confine,
                               resolver=resolver, profiles=profiles)
    except (W.WasmExecError, *runtime_errors, ValueError, OSError) as exc:
        status.write({"event": "refused", "reason": str(exc)[:500]}, terminal=True)
        return EXIT_REFUSED
    timings["materialize"] = round(time.monotonic() - t, 6)
    end: dict[str, Any] = {"event": "end"}
    try:
        if launch.run_dir:
            _write_owner(launch.run_dir)
        wasmtime = _wasmtime_path(launch.argv)
        start = {"event": "start", "agentteams_version": _version(), "agentteams_commit": agentteams_commit(),
                 "run_id": os.path.basename(launch.run_dir or ""),
                 "wasmtime_version": resolver.pinned_version("wasmtime"),
                 "wasmtime_sha256": _sha256_file(wasmtime), "module_sha256": launch.module_sha256,
                 "grant_spec_sha256": W.GRANT_SPEC_SHA256, "profile": "module",
                 "argv_flags": launch.argv[launch.argv.index(wasmtime) + 2:launch.argv.index(wasmtime) + 8],
                 "argv_sha256": _sha_text("\0".join(launch.argv)),
                 "limits": {**asdict(limits), "grace_s": args.grace},
                 "caps": asdict(caps), "os_confinement": launch.os_confinement, "os_reason": launch.os_reason,
                 "read_only_mode": launch.read_only_mode, "run_dir_bytes": _tree_bytes(launch.run_dir)}
        status.write(start)
        stdin_fd, stdout_fd, stderr_fd = seams.fds
        result = relay_session(launch, stdin_fd=stdin_fd, stdout_fd=stdout_fd, stderr_fd=stderr_fd, caps=caps,
                               stop=lambda: flag.get("sig"), parent_death=True)
        if status.gone and result.outcome == "exit":
            result.outcome = "parent-gone"
        end.update({k: v for k, v in asdict(result).items() if k != "stderr_tail"})
        end["stderr_tail"] = result.stderr_tail.decode("utf-8", "replace")
    finally:
        t = time.monotonic()
        try:
            launch.cleanup()
            end["cleanup"] = "ok"
        except OSError as exc:
            end["cleanup"] = f"failed: {type(exc).__name__}"
        timings["cleanup"] = round(time.monotonic() - t, 6)
    end["timings"] = timings
    status.write(end, terminal=True)
    return EXIT_OK


def agentteams_commit() -> str | None:
    """The agentteams commit this code came from, when it can be known without running git.

    A wheel or sdist installed from a VCS URL records it in PEP 610 ``direct_url.json`` (``vcs_info.commit_id``);
    a source checkout (an editable install) is read from its git files by ``update_lock.agentteams_commit`` (bounded,
    no-follow reads). Otherwise None.

    Returns:
        A 40-hex commit id, or None.

    Raises:
        Nothing.
    """
    from importlib import metadata

    from agentteams import update_lock

    try:
        raw = metadata.distribution("agentteams").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        raw = None
    try:
        commit = json.loads(raw).get("vcs_info", {}).get("commit_id") if raw else None
    except (ValueError, AttributeError):
        commit = None
    if not (isinstance(commit, str) and _SHA_RE.match(commit)):
        commit = update_lock.agentteams_commit()
    return commit if _SHA_RE.match(commit) else None


_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)


def _version() -> str:
    from agentteams import __version__
    return __version__


def _sha_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def _tree_bytes(path: str | None) -> int:
    sizes = []
    for dirpath, _dirs, files in os.walk(path or "", followlinks=False):
        for name in files:
            try:
                sizes.append(os.lstat(os.path.join(dirpath, name)).st_size)
            except OSError:
                sizes.append(0)   # vanished mid-walk; the count is advisory
    return sum(sizes)


if __name__ == "__main__":
    raise SystemExit(main())
