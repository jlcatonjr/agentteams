#!/usr/bin/env python3
"""Diagnose and repair the known Goose tracker and local-proxy failure modes."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence


PORT = 8791
HEALTH_TIMEOUT_SECONDS = 2.0
PROCESS_TIMEOUT_SECONDS = 5.0
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
ROUTE_PROXY = SCRIPTS_DIR / "goose-openrouter-route-proxy.py"
KNOWN_SERVICES = {
    ROUTE_PROXY.resolve(): "goose-openrouter-route-proxy",
}
#: Trailing bytes left after a syntactically complete, valid tracker object -- always
#: stale leftovers from a non-atomic write (a newer, shorter write that did not
#: truncate an older, longer file), never legitimate additional data, because nothing
#: can legally follow a fully-closed top-level JSON value except whitespace. Matches
#: two shapes observed in the wild: a dangling ``": null}}}"`` tail (2026-08-08) and
#: bare leftover closing braces/whitespace with no other tokens (2026-08-10, same root
#: cause, different amount of stale suffix). Deliberately narrow: any quote, digit, or
#: other JSON token in the trailing text still refuses repair as "unsupported".
KNOWN_TRAILING_GARBAGE = re.compile(r"^(?:\s|\}|:\s*null)*$")


class _RecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _TrackerState:
    kind: str
    message: str
    source: bytes | None = None
    value: dict[str, object] | None = None


@dataclass(frozen=True)
class _ProcessInfo:
    pid: int
    script: Path
    service: str
    restart_command: tuple[str, ...]
    display: str


def _tracker_path() -> Path:
    data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return root / "goose" / "projects.json"


def _log_path(service: str) -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "goose" / "route-proxy.log"


def _valid_tracker(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("projects"), dict)


def _inspect_tracker(path: Path) -> _TrackerState:
    if not path.exists():
        return _TrackerState("missing", f"tracker absent (Goose may create it): {path}")
    try:
        source = path.read_bytes()
        text = source.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _TrackerState("unsupported", f"tracker is unreadable or not UTF-8: {exc}")

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        try:
            start = len(text) - len(text.lstrip())
            value, end = json.JSONDecoder().raw_decode(text, start)
        except (ValueError, json.JSONDecodeError) as exc:
            return _TrackerState("unsupported", f"tracker is truncated or malformed: {exc}")
        trailing = text[end:]
        if not _valid_tracker(value):
            return _TrackerState(
                "unsupported", "first JSON value is not a Goose projects object",
            )
        if not trailing.strip() or not KNOWN_TRAILING_GARBAGE.fullmatch(trailing):
            return _TrackerState(
                "unsupported", "tracker corruption is not the recognized ': null' tail",
            )
        return _TrackerState(
            "repairable", "valid projects object followed by the recognized corrupt tail",
            source, value,
        )

    if not _valid_tracker(value):
        return _TrackerState("unsupported", "valid JSON is not a Goose projects object")
    return _TrackerState("healthy", "tracker JSON and projects object are valid")


def _exclusive_backup(path: Path, source: bytes) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidates = [path.with_name(f"{path.name}.bak-{stamp}")]
    candidates.extend(
        path.with_name(f"{path.name}.bak-{stamp}-{os.urandom(4).hex()}")
        for _ in range(8)
    )
    for candidate in candidates:
        descriptor = None
        try:
            descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is None:
            continue
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            candidate.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)
        return candidate
    raise _RecoveryError("could not allocate a unique tracker backup name")


def _repair_tracker(path: Path, state: _TrackerState) -> Path:
    if state.kind != "repairable" or state.source is None or state.value is None:
        raise _RecoveryError("tracker state is not repairable")
    backup = _exclusive_backup(path, state.source)
    payload = (json.dumps(state.value, indent=2) + "\n").encode()
    temporary = path.with_name(f".{path.name}.tmp-{os.urandom(6).hex()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)

    try:
        if path.read_bytes() != state.source:
            raise _RecoveryError(
                f"tracker changed during repair; original preserved; backup: {backup}",
            )
        os.replace(temporary, path)
    except (OSError, _RecoveryError):
        temporary.unlink(missing_ok=True)
        raise

    verified = _inspect_tracker(path)
    if verified.kind != "healthy":
        raise _RecoveryError(
            f"post-repair validation failed; restore backup: {backup}",
        )
    return backup


def _run(
    command: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(command, capture_output=True, text=True, check=False)


def _listener_pids(
    port: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[int, ...]:
    if shutil.which("lsof") is None:
        raise _RecoveryError("lsof is required to verify listener ownership")
    result = _run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"], runner,
    )
    if result.returncode == 1:
        return ()
    if result.returncode != 0:
        raise _RecoveryError(f"lsof failed: {result.stderr.strip() or result.returncode}")
    pids = {int(line[1:]) for line in result.stdout.splitlines() if line.startswith("p")}
    return tuple(sorted(pids))


def _process_cwd(
    pid: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    result = _run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], runner)
    if result.returncode != 0:
        raise _RecoveryError(f"could not inspect cwd for listener PID {pid}")
    names = [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]
    if len(names) != 1:
        raise _RecoveryError(f"could not resolve cwd for listener PID {pid}")
    return Path(names[0])


def _process_info(
    pid: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> _ProcessInfo | None:
    result = _run(["ps", "-ww", "-p", str(pid), "-o", "command="], runner)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        arguments = shlex.split(result.stdout.strip())
    except ValueError:
        return None
    if not arguments or not Path(arguments[0]).name.lower().startswith("python"):
        return None
    cwd = _process_cwd(pid, runner)
    for index, argument in enumerate(arguments[1:], start=1):
        candidate = Path(argument).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve()
        if resolved not in KNOWN_SERVICES:
            continue
        interpreter_options = arguments[1:index]
        if any(not option.startswith("-") or option in {"-c", "-m"} for option in interpreter_options):
            return None
        restart = (arguments[0], *interpreter_options, str(resolved), *arguments[index + 1:])
        return _ProcessInfo(
            pid, resolved, KNOWN_SERVICES[resolved], restart,
            f"{Path(arguments[0]).name} {resolved.name}",
        )
    return None


def _health_service(port: int, timeout: float = HEALTH_TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/healthz",
        headers={"Accept": "application/json", "Connection": "close"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise _RecoveryError(f"proxy health endpoint returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise _RecoveryError(f"proxy health endpoint failed: {exc}") from exc
    service = payload.get("service") if isinstance(payload, dict) else None
    if not isinstance(service, str):
        raise _RecoveryError("proxy health response has no service identity")
    return service


def _resolve_listener(
    port: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> _ProcessInfo | None:
    """Resolve the listener's identity from the process table only (no /healthz probe).

    Returns the known-service ``_ProcessInfo``, or ``None`` when nothing is listening.
    Raises ``_RecoveryError`` for an ambiguous listener set or a listener whose process
    identity is not a known service -- the same refusals as ``_inspect_listener`` minus
    the health check. This is what lets ``--force-restart`` clear a hung (health-
    unreachable) proxy while still refusing to signal an unknown foreign listener.

    Args:
        port: TCP port whose listener to resolve.
        runner: Injected subprocess runner (test seam).

    Returns:
        The identity-resolved known-service process, or ``None`` if nothing listens.

    Raises:
        _RecoveryError: The listener set is ambiguous, or the sole listener is not a
            known service.
    """
    pids = _listener_pids(port, runner)
    if not pids:
        return None
    if len(pids) != 1:
        raise _RecoveryError(f"refusing ambiguous listener set on port {port}: {pids}")
    info = _process_info(pids[0], runner)
    if info is None:
        raise _RecoveryError(
            f"refusing unknown listener PID {pids[0]} on port {port}",
        )
    return info


def _inspect_listener(
    port: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> _ProcessInfo | None:
    info = _resolve_listener(port, runner)
    if info is None:
        return None
    service = _health_service(port)
    if service != info.service:
        raise _RecoveryError(
            f"listener PID {info.pid} identity mismatch: process={info.service}, health={service}",
        )
    return info


def _open_log(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    return descriptor


def _wait_for_listener(
    expected_pid: int,
    expected_service: str,
    process: subprocess.Popen[bytes],
    runner: Callable[..., subprocess.CompletedProcess[str]],
    timeout: float = PROCESS_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = "listener did not appear"
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise _RecoveryError(f"proxy exited during startup with status {returncode}")
        try:
            pids = _listener_pids(PORT, runner)
            if pids == (expected_pid,) and _health_service(PORT) == expected_service:
                return
        except _RecoveryError as exc:
            last_error = str(exc)
        time.sleep(0.1)
    raise _RecoveryError(f"proxy startup timed out: {last_error}")


def _start_proxy(
    command: Sequence[str],
    service: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    if _listener_pids(PORT, runner):
        raise _RecoveryError(f"port {PORT} became occupied before proxy startup")
    log = _log_path(service)
    descriptor = _open_log(log)
    try:
        process = popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=descriptor,
            stderr=subprocess.STDOUT,
            cwd=REPO_ROOT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        os.close(descriptor)
    _wait_for_listener(process.pid, service, process, runner)
    print(f"[repaired] started {service} PID {process.pid}; log: {log}")
    return process.pid


def _stop_proxy(
    original: _ProcessInfo,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    kill: Callable[[int, int], None] = os.kill,
) -> None:
    current = _inspect_listener(PORT, runner)
    if current is None or current.pid != original.pid or current.script != original.script:
        raise _RecoveryError("listener ownership changed before restart; refusing SIGTERM")
    kill(current.pid, 0)
    kill(current.pid, signal.SIGTERM)
    deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _listener_pids(PORT, runner):
            return
        time.sleep(0.1)
    raise _RecoveryError(f"port {PORT} is still held after bounded SIGTERM wait")


def _force_stop_proxy(
    original: _ProcessInfo,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    kill: Callable[[int, int], None] = os.kill,
) -> None:
    """SIGTERM (then SIGKILL) a listener re-verified BY PROCESS IDENTITY as our own.

    Unlike ``_stop_proxy``, this needs no ``/healthz`` answer, so it can clear a hung-
    but-listening proxy. It still refuses to signal a PID that is not the same known-
    service process originally resolved -- an unknown or changed listener is never
    signalled. SIGKILL is only reached after the bounded SIGTERM wait fails.

    Args:
        original: The identity-resolved process this call is authorized to stop.
        runner: Injected subprocess runner (test seam).
        kill: Injected signal sender (test seam).

    Raises:
        _RecoveryError: Listener ownership changed since *original* was resolved, or the
            port is still held after both SIGTERM and SIGKILL.
    """
    current = _resolve_listener(PORT, runner)
    if current is None or current.pid != original.pid or current.script != original.script:
        raise _RecoveryError("listener ownership changed before force-restart; refusing signal")
    # Residual PID-reuse TOCTOU (accepted, @security 2026-09-01): between this re-resolve and
    # the signal below the identified process could exit and its PID be reused. kill(pid, 0) is a
    # liveness probe, not a re-identity check, and does not close that window; the SIGKILL step is
    # additionally gated on the port still being held (below). No atomic fix exists on darwin
    # (pidfd_send_signal is Linux-only). Same class exists in _stop_proxy.
    kill(current.pid, 0)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        kill(current.pid, sig)
        deadline = time.monotonic() + PROCESS_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not _listener_pids(PORT, runner):
                return
            time.sleep(0.1)
    raise _RecoveryError(f"port {PORT} is still held after SIGTERM and SIGKILL")


def _default_route_command() -> tuple[str, ...]:
    return (sys.executable, "-u", str(ROUTE_PROXY.resolve()), "--port", str(PORT), "--quiet")


def _check_tracker(check_only: bool) -> tuple[bool, bool]:
    path = _tracker_path()
    state = _inspect_tracker(path)
    if state.kind in {"healthy", "missing"}:
        print(f"[ok] {state.message}")
        return True, False
    if state.kind == "unsupported":
        raise _RecoveryError(f"refusing tracker repair: {state.message}; path: {path}")
    if check_only:
        print(f"[needed] tracker repair: {state.message}; path: {path}")
        return False, True
    backup = _repair_tracker(path, state)
    print(f"[repaired] tracker; backup: {backup}")
    return True, True


def main(argv: Sequence[str] | None = None) -> int:
    """Run bounded Goose recovery.

    Args:
        argv: Optional command-line arguments.

    Returns:
        Zero on healthy or repaired state, one when check mode finds repairable
        state, two for a refused/unsupported state, or three for runtime failure.

    Raises:
        No exceptions intentionally escape; failures are reported on stderr.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="diagnose only; do not write files, signal processes, or start a proxy",
    )
    parser.add_argument(
        "--restart", action="store_true",
        help="restart a healthy, identity-verified known proxy",
    )
    parser.add_argument(
        "--force-restart", action="store_true",
        help="restart a known proxy even when it is hung/unhealthy; identity is verified "
             "from the process table, not /healthz. Starts the default proxy if none is "
             "listening; refuses an unknown or ambiguous listener.",
    )
    args = parser.parse_args(argv)
    if args.check and (args.restart or args.force_restart):
        parser.error("--check cannot be combined with a restart mode")
    if args.restart and args.force_restart:
        parser.error("--restart and --force-restart are mutually exclusive")

    try:
        _, tracker_needed = _check_tracker(args.check)
        if args.force_restart:
            # Identity-only resolve: no /healthz gate, so a hung-but-listening proxy
            # can be cleared. An unknown/ambiguous listener still raises here.
            listener = _resolve_listener(PORT)
            if listener is None:
                _start_proxy(_default_route_command(), "goose-openrouter-route-proxy")
            else:
                _force_stop_proxy(listener)
                _start_proxy(listener.restart_command, listener.service)
            return 0
        listener = _inspect_listener(PORT)
        if args.check:
            if listener is None:
                print(f"[needed] no proxy is listening on 127.0.0.1:{PORT}")
                return 1
            print(f"[ok] {listener.service} PID {listener.pid} owns 127.0.0.1:{PORT}")
            return 1 if tracker_needed else 0

        if listener is None and args.restart:
            raise _RecoveryError(
                "--restart requires a live known proxy so its exact command can be preserved; "
                "run without --restart to start the default route proxy",
            )
        if listener is None:
            _start_proxy(_default_route_command(), "goose-openrouter-route-proxy")
        elif args.restart:
            _stop_proxy(listener)
            _start_proxy(listener.restart_command, listener.service)
        else:
            print(f"[ok] {listener.service} PID {listener.pid} owns 127.0.0.1:{PORT}")
        return 0
    except _RecoveryError as exc:
        print(f"[refused] {exc}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[failed] {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())