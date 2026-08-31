"""Executable OS-sandbox ENFORCEMENT tests (Cluster B / RH-4 seed).

`test_workspace_privilege_scoping.py` asserts the *emitted* sandbox config is
correct (a unit test of what `frameworks/claude.py` writes).  These tests are a
different, stronger layer: they run the **real host sandbox mechanism** and
assert that a write/read is *actually* denied.  They are the "doc-verified ->
verified" bridge the privilege backlog (P3-3 / P3-6 / RH-4) calls for.

Discipline (non-negotiable):
- Every test is **host-gated** with ``skipif`` and NEVER silently passes on a
  host that cannot run the mechanism.  A skip is reported as a skip, not a pass.
- These test the *mechanism* (Seatbelt / bubblewrap deny semantics), which is a
  necessary condition for the emitted config to work.  They do NOT yet prove
  Claude Code derives the sandbox args from agentteams' JSON (that needs Claude
  Code in-guest — tracked as P3-4 / B-4); do not read a green here as
  "the product's enforcement is verified end-to-end".

Findings encoded here (macOS 15 / Seatbelt, 2026-08-26):
- **P3-6 (deny-over-allow precedence) HOLDS**: ``(deny file-write* (subpath R))``
  layered over ``(allow default)`` blocks a write to ``R``.
- **P3-3 (path canonicalization is LOAD-BEARING)**: the SAME deny written with an
  *unresolved* path (macOS symlinks ``/tmp`` -> ``/private/tmp``, and ``~`` is
  only expanded by the caller) is a **silent no-op** — the write succeeds.  An
  unresolved ``~/``-relative ``denyRead`` entry can therefore be inert while the
  emitted config looks protective.  This is why the P3-3 fix emits
  ``expanduser``-resolved absolute paths.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# --- host gates ------------------------------------------------------------

_IS_MACOS = sys.platform == "darwin"
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_HAS_SEATBELT = _IS_MACOS and Path(_SANDBOX_EXEC).exists()

_BWRAP = "/usr/bin/bwrap"
_HAS_BWRAP = sys.platform.startswith("linux") and Path(_BWRAP).exists()

_seatbelt = pytest.mark.skipif(
    not _HAS_SEATBELT, reason="macOS Seatbelt (sandbox-exec) not available on this host"
)
_bwrap = pytest.mark.skipif(
    not _HAS_BWRAP, reason="Linux bubblewrap (bwrap) not available on this host"
)


# --- Seatbelt helpers ------------------------------------------------------

def _run_seatbelt(profile: str, params: dict[str, str], argv: list[str]) -> subprocess.CompletedProcess:
    """Run ``argv`` under a Seatbelt profile with ``-D`` param substitutions."""
    cmd = [_SANDBOX_EXEC]
    for k, v in params.items():
        cmd += ["-D", f"{k}={v}"]
    cmd += ["-p", profile, *argv]
    return subprocess.run(cmd, capture_output=True, text=True)


_DENY_WRITE_PROFILE = '(version 1)(allow default)(deny file-write* (subpath (param "R")))'
_DENY_READ_PROFILE = '(version 1)(allow default)(deny file-read* (subpath (param "R")))'


# --- macOS Seatbelt: the mechanism works on canonical paths ----------------

@_seatbelt
def test_seatbelt_denies_write_to_realpath_subpath(tmp_path: Path) -> None:
    """P3-6: a deny over allow-default blocks a write when the path is canonical."""
    protected = (tmp_path / "protected").resolve()
    protected.mkdir()
    target = protected / "x.txt"
    res = _run_seatbelt(
        _DENY_WRITE_PROFILE, {"R": str(protected)},
        ["/bin/sh", "-c", f"echo hi > '{target}'"],
    )
    assert res.returncode != 0, f"expected non-zero rc, got {res.returncode}: {res.stderr}"
    assert not target.exists(), "Seatbelt did NOT block the write — deny-over-allow failed"


@_seatbelt
def test_seatbelt_unresolved_path_deny_is_a_noop(tmp_path: Path) -> None:
    """P3-3 (the load-bearing finding): the SAME deny with an *unresolved* path is
    a silent no-op — the write succeeds.  This is a REGRESSION GUARD: it pins the
    reason agentteams must emit ``expanduser``/``realpath``-resolved deny paths.
    If a future OS makes the unresolved form enforce, this test flips and we
    revisit the P3-3 mitigation."""
    real = (tmp_path / "protected").resolve()
    real.mkdir()
    target = real / "x.txt"
    # An unresolved alias for the same dir: /tmp is a symlink to /private/tmp on macOS.
    # Construct one via /tmp if tmp_path lives under it; else use a non-canonical
    # ``..`` detour that resolves to the same place but is not the subpath literal.
    unresolved = str(Path("/tmp") / real.relative_to(real.anchor)) if str(real).startswith("/private/tmp") else f"{real}/./"
    res = _run_seatbelt(
        _DENY_WRITE_PROFILE, {"R": unresolved},
        ["/bin/sh", "-c", f"echo hi > '{target}'"],
    )
    # We assert the no-op (write allowed) ONLY when we actually built a genuinely
    # non-canonical alias; otherwise skip rather than assert a false finding.
    if unresolved.rstrip("/") == str(real):
        pytest.skip("could not construct a non-canonical alias for this tmp path")
    assert target.exists(), (
        "unexpected: the unresolved-path deny blocked the write — the P3-3 "
        "canonicalization finding no longer holds; revisit the mitigation"
    )


@_seatbelt
def test_seatbelt_denies_read_of_protected_file(tmp_path: Path) -> None:
    """P3 read-exclusion mechanism: a file-read* deny blocks reading a protected file."""
    protected = (tmp_path / "secrets").resolve()
    protected.mkdir()
    secret = protected / "token"
    secret.write_text("s3cr3t\n", encoding="utf-8")
    res = _run_seatbelt(
        _DENY_READ_PROFILE, {"R": str(protected)},
        ["/bin/cat", str(secret)],
    )
    assert res.returncode != 0, "expected the read to be denied (non-zero rc)"
    assert "s3cr3t" not in res.stdout, "Seatbelt did NOT block the read — secret leaked"


@_seatbelt
def test_seatbelt_write_deny_applies_to_child_process(tmp_path: Path) -> None:
    """Escape attempt: a child process spawned inside the sandbox inherits the deny
    (the confinement is process-tree-wide, not just the top command)."""
    protected = (tmp_path / "protected").resolve()
    protected.mkdir()
    target = protected / "viachild.txt"
    # Parent sh spawns a child python that attempts the write.
    child = f"import pathlib; pathlib.Path('{target}').write_text('x')"
    res = _run_seatbelt(
        _DENY_WRITE_PROFILE, {"R": str(protected)},
        ["/bin/sh", "-c", f"{sys.executable} -c \"{child}\""],
    )
    assert res.returncode != 0, "expected child-process write to be denied"
    assert not target.exists(), "child process escaped the write confinement"


# --- Linux bubblewrap: read-deny by non-binding ----------------------------

@_bwrap
def test_bwrap_unbound_path_is_unreadable(tmp_path: Path) -> None:
    """bubblewrap denies reads by NOT binding a path into the namespace (unlike
    Seatbelt's rule-based deny).  A path left unbound is absent inside the guest.
    This is the Linux mechanism arm; the argument-construction link (Claude Code
    deriving these binds from agentteams' JSON) remains untested — see P3-4/B-4."""
    secret = (tmp_path / "secret.txt").resolve()
    secret.write_text("s3cr3t\n", encoding="utf-8")
    # Minimal read-only root, /usr bound, but NOT the secret's dir.
    res = subprocess.run(
        [_BWRAP, "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
         "--ro-bind", "/lib", "/lib", "--proc", "/proc", "--dev", "/dev",
         "--unshare-all", "/bin/cat", str(secret)],
        capture_output=True, text=True,
    )
    assert "s3cr3t" not in res.stdout, "bwrap guest could read an unbound secret path"
