"""Phase 1.1: confine-run.sh --coord-root binds sibling roots and FAILS CLOSED on a missing one.

The check lives in the launcher's OS-independent block, so a missing coordination target is a
clean exit-2 die BEFORE OS dispatch (never a bwrap sandbox-init crash, D-3) — verifiable on any
host via --check.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"


def _run(args):
    return subprocess.run(["bash", str(LAUNCHER), *args], capture_output=True, text=True)


def test_present_coord_root_is_bound(tmp_path):
    scratch = tmp_path / "scratch"; scratch.mkdir()
    sibling = tmp_path / "sibling"; sibling.mkdir()
    r = _run(["--scratch", str(scratch), "--coord-root", str(sibling), "--check", "--", "/bin/echo", "hi"])
    assert r.returncode == 0, r.stderr
    assert str(sibling) in r.stdout
    assert "coord-roots" in r.stdout


def test_missing_coord_root_fails_closed(tmp_path):
    scratch = tmp_path / "scratch"; scratch.mkdir()
    missing = tmp_path / "sibling" / "does-not-exist"
    r = _run(["--scratch", str(scratch), "--coord-root", str(missing), "--check", "--", "/bin/echo", "hi"])
    assert r.returncode == 2
    assert "does not exist" in r.stderr and "fail-closed" in r.stderr
    # It must NOT auto-create the missing sibling (that would mask a misconfig).
    assert not missing.exists()


def test_missing_coord_root_does_not_reach_os_dispatch(tmp_path):
    # A clean die (exit 2) from the generic block, never a bwrap/sandbox-exec init crash.
    scratch = tmp_path / "scratch"; scratch.mkdir()
    r = _run(["--scratch", str(scratch), "--coord-root", "/no/such/sibling", "--check", "--", "/bin/echo", "hi"])
    assert r.returncode == 2
    assert "bwrap" not in r.stderr.lower()  # not a sandbox-init failure
