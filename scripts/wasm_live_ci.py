#!/usr/bin/env python3
"""wasm_live_ci.py — helpers for the live WASM CI jobs (`wasm-live`, `macos-seatbelt` in `.github/workflows/ci.yml`).

Four subcommands, all stdlib-only. The live test files and the skip allowlists live here (``LIVE_FILES``,
``COMMON_ALLOW``, ``SANDBOX_ALLOW``), so the two jobs cannot drift apart.

``files``
    Print ``LIVE_FILES``, space-separated, for ``pytest $(python scripts/wasm_live_ci.py files)``.

``env ROOT``
    Print the ``KEY=VALUE`` lines the live tests read (append them to ``$GITHUB_ENV``): the installed wasmtime
    binary and CPython-WASI directory, resolved through ``agentteams.wasm_runtime`` from the ``--wasm-install``
    project ``ROOT`` (so no install layout is assumed here), plus ``AGENTTEAMS_WASM_LIVE_PROJECT`` and
    ``AGENTTEAMS_WASM_LIVE=1``.

``skips JUNIT --sandbox {bwrap,seatbelt} [--allow NODEID ...]``
    Fail when any test in pytest's ``--junitxml`` report was skipped and its nodeid is not allowlisted
    (``COMMON_ALLOW``, the sandbox's ``SANDBOX_ALLOW`` entries, and any ``--allow``). A live
    test that skips because a precondition is missing would otherwise leave the job green for nothing. An empty
    report fails too. An allowlisted nodeid that did not skip is printed, not failed.

``timing ROOT --expect-reason REASON``
    The R13 full-step timing: five Python steps as the ``run_wasm`` server runs them (Stage-0 prepare, pre-exec
    re-hash, OS nesting, wasmtime). Fails if a step fails or does not report ``os_confinement=nested`` with
    ``os_reason`` REASON (for example ``nested:bwrap``). The worst time is logged, not gated: shared runners are
    too noisy for the 2 s gate the operator runs.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

#: The live WASM test files both jobs run (the report's list; each skips without a dev wasmtime).
LIVE_FILES = (
    "tests/test_wasm_exec_nested.py", "tests/test_wasm_exec_live.py", "tests/test_wasm_guests_live.py",
    "tests/test_wasm_agent_guest_wasm.py", "tests/test_wasm_mcp_server.py", "tests/test_proposal_run_wasm.py",
    "tests/test_wasm_agent_e2e.py", "tests/test_wasm_delegation_e2e.py", "tests/test_wasm_mcp_e2e.py",
    "tests/test_wasm_runtime_matrix.py", "tests/test_wasm_runtime_live.py",
)

#: Tests that skip wherever an OS sandbox is usable: the no-sandbox path, Stage 1 in-process spies (a nestable
#: sandbox moves Stage 1 into a subprocess), and a live provider CI never configures.
COMMON_ALLOW = (
    "tests/test_wasm_exec_live.py::test_available_none_when_require_and_no_os_sandbox",
    "tests/test_wasm_agent_e2e.py::test_arch_b_native_render_run_tool_call_and_delegation",
    "tests/test_wasm_agent_e2e.py::test_arch_b_live_render_run_tool_call_and_delegation",
    "tests/test_wasm_agent_e2e.py::test_arch_b_live_provider_opt_in",
)

#: Per-sandbox extras. Empty: the network-deny and credential-deny nested cases now run under both bwrap and
#: Seatbelt (remediation row bwrap-nested-deny), so a skip of either fails both jobs.
SANDBOX_ALLOW: dict[str, tuple[str, ...]] = {
    "bwrap": (),
    "seatbelt": (),
}


def env_lines(root: Path) -> list[str]:
    """The environment lines the live WASM tests read, for an installed project.

    Args:
        root: The project ``--wasm-install all`` installed into.

    Returns:
        ``KEY=VALUE`` lines for ``$GITHUB_ENV``.

    Raises:
        agentteams.wasm_runtime.WasmRuntimeError: When a component is missing or fails verification.
    """
    from agentteams import wasm_runtime

    root = root.resolve()
    return [f"AGENTTEAMS_WASM_DEV_WASMTIME={wasm_runtime.resolve(root, 'wasmtime')}",
            f"AGENTTEAMS_WASM_DEV_PYTHON={wasm_runtime.resolve(root, 'python').parent}",
            f"AGENTTEAMS_WASM_LIVE_PROJECT={root}",
            "AGENTTEAMS_WASM_LIVE=1"]


def _nodeid(classname: str, name: str, root: Path) -> str:
    """Rebuild a pytest nodeid from a junit ``classname`` (``tests.test_x`` or ``tests.test_x.TestC``) and name."""
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):  # the longest dotted prefix that is a file is the module
        if (root / Path(*parts[:i])).with_suffix(".py").is_file():
            return "::".join(["/".join(parts[:i]) + ".py", *parts[i:], name])
    return f"{classname}::{name}"


def check_skips(junit: Path, allow: list[str], root: Path) -> int:
    """Fail on any skipped test whose nodeid is not allowlisted.

    Args:
        junit: pytest's ``--junitxml`` report.
        allow: The nodeids that may skip on this runner.
        root: The checkout the nodeids are relative to.

    Returns:
        0 when every skip is allowlisted and at least one test ran, else 1.

    Raises:
        xml.etree.ElementTree.ParseError: On a malformed report.
        OSError: When the report cannot be read.
    """
    cases = list(ET.parse(junit).getroot().iter("testcase"))
    skipped = [_nodeid(c.get("classname", ""), c.get("name", ""), root) for c in cases
               if c.find("skipped") is not None]
    print(f"{len(cases)} tests, {len(skipped)} skipped")
    if not cases:
        print("::error::the junit report has no test cases")
        return 1
    for nodeid in sorted(set(allow) - set(skipped)):
        print(f"allowlisted but not skipped (fine): {nodeid}")
    unexplained = [n for n in skipped if n not in allow]
    for nodeid in skipped:
        print(f"{'skip (allowlisted)' if nodeid in allow else '::error::unexplained skip'}: {nodeid}")
    return 1 if unexplained else 0


def step_timing(root: Path, expect_reason: str, steps: int = 5) -> int:
    """Run the R13 full-step timing and check every step nested under the expected OS sandbox.

    Args:
        root: The installed project.
        expect_reason: The ``os_reason`` every step must report (``nested:bwrap`` or ``nested:seatbelt``).
        steps: How many steps to run.

    Returns:
        0 when every step succeeded nested, else 1.

    Raises:
        agentteams.wasm_guests.WasmGuestError: When a guest cannot be prepared.
    """
    from agentteams import _sandbox_profiles as SP, wasm_exec as W, wasm_guests as G, wasm_runtime

    root, worst, bad = root.resolve(), 0.0, 0
    resolver = wasm_runtime.Resolver(root)
    for i in range(steps):
        t = time.perf_counter()
        res = G.run_code("python", "import json;print(json.dumps({'ok': 1}))", None, grant=W.COMPUTE_ONLY,
                         project_root=root, os_confine="auto", resolver=resolver, executor=W, profiles=SP)
        dt = time.perf_counter() - t
        worst = max(worst, dt)
        print(f"step {i}: {dt:.3f}s exit={res.exit_code} os={res.os_confinement} ({res.os_reason}) "
              f"out={res.stdout[:24]!r}")
        if res.exit_code != 0 or b'"ok": 1' not in res.stdout:
            print(f"::error::step {i} failed: {res.stderr[-300:]!r}")
            bad += 1
        elif (res.os_confinement, res.os_reason) != ("nested", expect_reason):
            print(f"::error::step {i} was not nested under {expect_reason}")
            bad += 1
    seen = f"os=nested ({expect_reason})" if not bad else f"{bad} step(s) failed or not nested under {expect_reason}"
    line = f"R13 full-step timing: worst {worst:.3f}s over {steps} steps, {seen}; time not gated"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"- {line}\n")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments (default: ``sys.argv[1:]``).

    Returns:
        The process exit code.

    Raises:
        SystemExit: On bad arguments (argparse).
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("files")
    p = sub.add_parser("env")
    p.add_argument("root", type=Path)
    p = sub.add_parser("skips")
    p.add_argument("junit", type=Path)
    p.add_argument("--sandbox", required=True, choices=sorted(SANDBOX_ALLOW))
    p.add_argument("--allow", action="append", default=[], metavar="NODEID")
    p.add_argument("--root", type=Path, default=Path.cwd(), help="checkout the nodeids are relative to")
    p = sub.add_parser("timing")
    p.add_argument("root", type=Path)
    p.add_argument("--expect-reason", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "files":
        print(" ".join(LIVE_FILES))
        return 0
    if args.cmd == "env":
        print("\n".join(env_lines(args.root)))
        return 0
    if args.cmd == "skips":
        allow = [*COMMON_ALLOW, *SANDBOX_ALLOW[args.sandbox], *args.allow]
        return check_skips(args.junit, allow, args.root)
    return step_timing(args.root, args.expect_reason)


if __name__ == "__main__":
    sys.exit(main())
