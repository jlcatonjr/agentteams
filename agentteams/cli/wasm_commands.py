"""wasm_commands.py — handlers for ``--wasm-install``, ``--wasm-status`` and ``--wasm-pin-update``.

These are explicit operator commands (D4). They are dispatched in ``cli/app.py`` among the standalone
modes, before the generation pipeline, so rendering never downloads anything. This is the only module
that calls :func:`agentteams.wasm_runtime.install` or :func:`agentteams.wasm_runtime.pin_update`
(``tests/test_wasm_runtime.py`` greps for that).

Exit codes: 0 installed/ok, 1 verification failure or refusal, 2 usage, 3 network.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

from agentteams import wasm_runtime

EXIT_OK = 0
EXIT_VERIFY = 1
EXIT_USAGE = 2
EXIT_NETWORK = 3


def _root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "project", None) or os.getcwd()).resolve()


def _components(spec: str | None, default: str) -> list[str]:
    """Parse a comma list (or ``all``) into components.

    Raises:
        ValueError: An unknown component name.
    """
    raw = (spec or default).strip()
    if raw == "all":
        return list(wasm_runtime.COMPONENTS)
    comps = [c.strip() for c in raw.split(",") if c.strip()]
    bad = [c for c in comps if c not in wasm_runtime.COMPONENTS]
    if bad or not comps:
        raise ValueError(f"unknown component(s) {bad or raw!r}; choose from {', '.join(wasm_runtime.COMPONENTS)} or all")
    return comps


def _confirm(args: argparse.Namespace, question: str) -> int | None:
    """Return ``None`` to proceed, or an exit code to stop.

    Without ``--yes`` a non-tty refuses; a tty is asked ``y/N``.
    """
    if getattr(args, "yes", False):
        return None
    if not sys.stdin.isatty():
        print(f"Error: {question} needs confirmation; re-run with --yes (non-interactive)", file=sys.stderr)
        return EXIT_USAGE
    answer = input(f"{question} [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("Aborted.", file=sys.stderr)
        return EXIT_VERIFY
    return None


def _fail(exc: wasm_runtime.WasmRuntimeError) -> int:
    print(f"Error: {exc}", file=sys.stderr)
    return EXIT_NETWORK if isinstance(exc, wasm_runtime.WasmNetworkError) else EXIT_VERIFY


def run_wasm_install(args: argparse.Namespace, *,
                     fetch: Callable[[str, int, Path], str] | None = None) -> int:
    """``agentteams --wasm-install [COMPONENTS] [--project DIR] [--dry-run] [--yes]``.

    Args:
        args: Parsed CLI arguments.
        fetch: Download override (tests only).

    Returns:
        The exit code.

    Raises:
        Nothing: a refusal (``WasmRuntimeError``) or network failure is printed and mapped to an exit code.
    """
    try:
        comps = _components(args.wasm_install, "wasmtime")
    except ValueError as exc:
        print(f"Error: --wasm-install: {exc}", file=sys.stderr)
        return EXIT_USAGE
    root = _root(args)
    try:
        plan = wasm_runtime.install_plan(root, comps)
    except wasm_runtime.WasmRuntimeError as exc:
        return _fail(exc)
    print("WASM runtime install plan (pinned; verified by size + sha256 before extraction):")
    for line in plan:
        print(f"  {line}")
    if getattr(args, "dry_run", False):
        print("Dry run: no network access, nothing written.")
        return EXIT_OK
    stop = _confirm(args, f"Download and install {', '.join(comps)} into {root / wasm_runtime.INSTALL_REL}?")
    if stop is not None:
        return stop
    try:
        report = wasm_runtime.install(root, comps, fetch=fetch)
    except wasm_runtime.WasmRuntimeError as exc:
        return _fail(exc)
    for note in report.notices:
        print(f"  notice: {note}")
    for q in report.quarantined:
        print(f"  quarantined: {q}")
    for c in report.installed:
        print(f"  ✓  installed {c}")
    for c in report.already_ok:
        print(f"  ✓  {c} already installed and verified")
    return EXIT_OK


def run_wasm_status(args: argparse.Namespace) -> int:
    """``agentteams --wasm-status [COMPONENTS] [--project DIR]``. Read-only, network-free.

    Args:
        args: Parsed CLI arguments.

    Returns:
        0 when every requested component is ``ok``, 2 on a usage error, else 1.

    Raises:
        Nothing: an unreadable pins file is printed and mapped to an exit code.
    """
    try:
        comps = _components(args.wasm_status, "all")
    except ValueError as exc:
        print(f"Error: --wasm-status: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        states = wasm_runtime.status(_root(args))
    except wasm_runtime.WasmRuntimeError as exc:
        return _fail(exc)
    rc = EXIT_OK
    for c in comps:
        state = states[c]
        print(f"  {c:<11} {state}")
        if not state.startswith("ok "):
            rc = EXIT_VERIFY
    return rc


def run_wasm_pin_update(args: argparse.Namespace, *,
                        fetch: Callable[[str, int, Path], str] | None = None,
                        api_get: Callable[[str], object] | None = None) -> int:
    """``agentteams --wasm-pin-update COMPONENT VERSION [--remediation IDS] [--tofu] [--pins-out PATH] [--yes]``.

    Writes the pins file only from a verified download and prints the unified diff; never commits.
    ``--pins-out`` defaults to the packaged pins file only inside a git work tree. ``--remediation`` needs a
    recorded ``wasm-pin-remediation`` decision in ``<--project>/references/security-decisions.log.csv``.

    Args:
        args: Parsed CLI arguments.
        fetch: Download override (tests only).
        api_get: Release-metadata override (tests only).

    Returns:
        The exit code.

    Raises:
        Nothing: a refusal (``WasmRuntimeError``) or network failure is printed and mapped to an exit code.
    """
    component, version = args.wasm_pin_update
    if component not in wasm_runtime.COMPONENTS:
        print(f"Error: --wasm-pin-update: unknown component {component!r}", file=sys.stderr)
        return EXIT_USAGE
    out = getattr(args, "pins_out", None)
    pins_path = Path(out).resolve() if out else wasm_runtime.default_pins_out()
    if pins_path is None:
        print("Error: --wasm-pin-update: the packaged pins file is not in a git work tree; pass --pins-out PATH "
              "(an installed package is never modified in place)", file=sys.stderr)
        return EXIT_USAGE
    if not pins_path.is_file():
        print(f"Error: --pins-out {pins_path} does not exist (copy the packaged pins file there first)",
              file=sys.stderr)
        return EXIT_USAGE
    remediation = [g.strip() for g in (getattr(args, "remediation", None) or "").split(",") if g.strip()]
    stop = _confirm(args, f"Download {component} {version} from GitHub releases and rewrite {pins_path}?")
    if stop is not None:
        return stop
    try:
        diff = wasm_runtime.pin_update(component, version, remediation=remediation,
                                       tofu=bool(getattr(args, "tofu", False)), pins_path=pins_path,
                                       fetch=fetch, api_get=api_get, decision_dir=_root(args))
    except wasm_runtime.WasmRuntimeError as exc:
        return _fail(exc)
    print(diff or "(no change)")
    print(f"Wrote {pins_path}. Not committed: the diff needs @security review, then the integrity "
          "manifest is re-pinned (--write-integrity-manifest).")
    return EXIT_OK
