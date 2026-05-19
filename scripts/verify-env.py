#!/usr/bin/env python3
"""Preflight verification of the local development environment.

Asserts the minimum Python and git versions declared in
``docs_src/verification-environment.md``. Exits non-zero with a clear
remediation hint when a precondition is unmet. Used as the first step in CI
and runnable locally.

Usage::

    python scripts/verify-env.py            # human-readable output
    python scripts/verify-env.py --quiet    # suppress success line
    python scripts/verify-env.py --json     # machine-readable report

Exit codes::

    0  all preconditions met
    1  one or more preconditions unmet
    2  unexpected error (subprocess failure, parse error)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from typing import Tuple

MIN_PYTHON: Tuple[int, int] = (3, 11)
# git 2.23 introduced ``git switch`` / ``git restore`` and stabilised the
# ``--literal-pathspecs`` interaction with ``-z`` that the pipeline relies on.
MIN_GIT: Tuple[int, int] = (2, 23)


def _check_python(actual: Tuple[int, int] | None = None) -> dict:
    have = actual if actual is not None else sys.version_info[:2]
    ok = have >= MIN_PYTHON
    return {
        "name": "python",
        "ok": ok,
        "required": ".".join(map(str, MIN_PYTHON)),
        "found": ".".join(map(str, have)),
        "hint": (
            ""
            if ok
            else "Install Python >= "
            f"{'.'.join(map(str, MIN_PYTHON))} (see docs_src/verification-environment.md)."
        ),
    }


_GIT_VERSION_RE = re.compile(r"git version (\d+)\.(\d+)")


def _detect_git_version(runner=subprocess.run) -> Tuple[int, int] | None:
    if shutil.which("git") is None:
        return None
    try:
        proc = runner(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _GIT_VERSION_RE.search(proc.stdout or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def _check_git(version: Tuple[int, int] | None | object = ...) -> dict:
    have = _detect_git_version() if version is ... else version
    if have is None:
        return {
            "name": "git",
            "ok": False,
            "required": ".".join(map(str, MIN_GIT)),
            "found": "not detected",
            "hint": (
                "git not found on PATH. Install git >= "
                f"{'.'.join(map(str, MIN_GIT))} "
                "(see docs_src/verification-environment.md)."
            ),
        }
    ok = have >= MIN_GIT
    return {
        "name": "git",
        "ok": ok,
        "required": ".".join(map(str, MIN_GIT)),
        "found": ".".join(map(str, have)),
        "hint": (
            ""
            if ok
            else f"Upgrade git to >= {'.'.join(map(str, MIN_GIT))} "
            "(see docs_src/verification-environment.md)."
        ),
    }


def run_checks() -> list[dict]:
    return [_check_python(), _check_git()]


def _format_human(checks: list[dict]) -> str:
    lines = []
    for c in checks:
        marker = "✓" if c["ok"] else "✗"
        lines.append(f"  {marker} {c['name']}: required >= {c['required']}, found {c['found']}")
        if not c["ok"] and c["hint"]:
            lines.append(f"      → {c['hint']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quiet", action="store_true", help="suppress output on success")
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(argv)

    try:
        checks = run_checks()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"verify-env: unexpected error: {exc}", file=sys.stderr)
        return 2

    all_ok = all(c["ok"] for c in checks)

    if args.json:
        print(json.dumps({"ok": all_ok, "checks": checks}, indent=2))
    elif all_ok:
        if not args.quiet:
            print("verify-env: OK")
            print(_format_human(checks))
    else:
        print("verify-env: FAIL", file=sys.stderr)
        print(_format_human(checks), file=sys.stderr)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
