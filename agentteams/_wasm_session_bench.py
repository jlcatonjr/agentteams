"""``python -I -m agentteams.wasm_session bench``: per-ask relay overhead (internal; split from ``wasm_session``).

It sends ``x\\n`` to an echo-style module and waits for the echo, N times, through a streaming session. With
``--unconfined-baseline`` it repeats that through a bare ``wasmtime run`` spawned from the same ``materialize`` with
``os_confine="off"``: the same grant, empty environment and verified copies, but no OS sandbox. That leg exists only
for the comparison, prints a warning, and writes no session status record (@security S8). Every leg kills its
process group and removes its run directory on every path, SIGTERM included (N3).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any

from agentteams import wasm_exec as W
from agentteams import wasm_session as S


def _ping_pong(write_fd: int, read_fd: int, asks: int) -> float:
    """Send ``x\\n`` and wait for its echo, *asks* times; return the seconds taken."""
    reader = os.fdopen(read_fd, "rb", buffering=1 << 16, closefd=False)
    t = time.monotonic()
    for _ in range(asks):
        os.write(write_fd, b"x\n")
        if reader.readline() != b"x\n":
            raise W.WasmExecError("bench: the module is not an echo guest")
    return time.monotonic() - t


def _relayed(launch: W.Launch, asks: int) -> dict[str, Any]:
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    null = os.open(os.devnull, os.O_WRONLY)
    took: list[float] = []

    def drive() -> None:
        try:
            took.append(_ping_pong(in_w, out_r, asks))
        finally:   # EOF ends the guest even when the echo check fails
            os.close(in_w)

    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    try:
        res = S.relay_session(launch, stdin_fd=in_r, stdout_fd=out_w, stderr_fd=null)
    finally:
        driver.join(5)
        for fd in (in_r, out_r, null):
            os.close(fd)
    return {"secs": took[0] if took else None, "outcome": res.outcome,
            "relay_latency_p50": res.relay_latency_p50, "relay_latency_p99": res.relay_latency_p99}


def _bare(launch: W.Launch, asks: int) -> dict[str, Any]:
    proc = subprocess.Popen(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True, bufsize=0)
    try:
        secs = _ping_pong(proc.stdin.fileno(), proc.stdout.fileno(), asks)
        proc.stdin.close()
        proc.wait(30)
    finally:   # an unconfined guest never outlives the bench (N3)
        W._kill_group(proc)
        proc.wait()
    return {"secs": secs, "outcome": "exit"}


def bench(args: argparse.Namespace, seams: Any) -> int:
    """Run the ``bench`` subcommand and print one JSON report on stdout.

    Args:
        args: Parsed ``bench`` arguments (``project_root``, ``asks``, ``os_confine``, ``timeout``,
            ``unconfined_baseline``, ``module``).
        seams: ``wasm_session._Seams`` (tests inject a resolver here).

    Returns:
        0 after the report; 3 when the module, grant or a leg is refused; 64 for ``--asks`` out of bounds; 130 when
        interrupted.

    Raises:
        Nothing. SIGINT/SIGTERM unwind through every leg's cleanup and return 130.
    """
    if not 1 <= args.asks <= 10_000_000:
        S._note("usage: --asks must be in [1, 10000000]")
        return S.EXIT_USAGE
    signal.signal(signal.SIGTERM, signal.default_int_handler)   # unwind through the finally blocks
    root = os.path.realpath(args.project_root)
    resolver, profiles, runtime_errors = S._runtime(seams, root)

    def leg(os_confine: str, relayed: bool) -> dict[str, Any]:
        launch = W.materialize(prepared, [], project_root=root, os_confine=os_confine,  # type: ignore[arg-type]
                               resolver=resolver, profiles=profiles)
        try:
            got = _relayed(launch, args.asks) if relayed else _bare(launch, args.asks)
        finally:
            launch.cleanup()
        secs = got.pop("secs")
        return {"os_confinement": launch.os_confinement, "wall_s": secs,
                "us_per_ask": round(secs * 1e6 / args.asks, 3) if secs else None, **got}

    try:
        limits = W.Limits(fuel=W.LIMIT_BOUNDS["fuel"][1], max_memory_bytes=W.DEFAULT_LIMITS.max_memory_bytes,
                          timeout_s=args.timeout)
        prepared = W.prepare(args.module, grant=W.Grant((), limits))
        report: dict[str, Any] = {"asks": args.asks, "module_sha256": prepared.module_sha256,
                                  "session": leg(args.os_confine, relayed=True)}
        if args.unconfined_baseline:
            S._note("WASM SANDBOX: the bench baseline runs wasmtime without OS confinement (comparison only)")
            report["baseline_unconfined"] = leg("off", relayed=False)
    except (W.WasmExecError, *runtime_errors, OSError) as exc:
        S._note(f"bench refused: {exc}")
        return S.EXIT_REFUSED
    except KeyboardInterrupt:   # SIGINT/SIGTERM: every leg has already cleaned up; no traceback with paths (R3)
        S._note("bench interrupted")
        return 130
    sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    return S.EXIT_OK
