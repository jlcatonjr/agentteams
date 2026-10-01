"""control_plane_io.py — write-if-absent control-plane stubs and the in-sandbox write preflight.

Two small, stdlib-only I/O helpers for the sandbox control plane (PR-D, 2026-09-30):

* :func:`write_control_plane_stubs` writes comment-only stubs of the operator-only rosters
  (``security-approvers.txt``, ``authorized-managers.txt``, ``management-authority.json``) into a
  SANDBOXED team's ``references/`` so every launcher/Seatbelt entry exists (the launcher refuses
  to start when a required entry is missing; a missing Seatbelt literal cannot be ro-bound). It
  never overwrites: each file is created with ``O_CREAT|O_EXCL|O_NOFOLLOW``, so an existing
  roster, or a planted symlink (even a dangling one), is left untouched. A stub reads exactly like
  an absent file in every reader (``tests/test_prd_trust_roots.py``).
* :func:`probe_team_write_denied` detects a write-denied team dir BEFORE an ``--update`` writes
  anything, so an in-sandbox run refuses cleanly instead of leaving a partial tree.

Deliberately NOT routed through ``extra_output_files``/``emit_all``: that path overwrites or
auto-fences an existing file, which would clobber an operator-populated roster.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

#: errno values that mean "the OS refused this write" (read-only mount, Seatbelt/LSM deny).
#: EACCES is only meaningful for the directory probe: a mode-444 FILE is still replaced by the
#: atomic rename the writers use, so a file probe ignores it.
_DIR_DENIED = frozenset({errno.EROFS, errno.EACCES, errno.EPERM})
_FILE_DENIED = frozenset({errno.EROFS, errno.EPERM})


def stubs_enabled(framework: str, manifest: dict[str, Any]) -> bool:
    """Return True iff ``framework``'s own sandbox predicate is on for ``manifest``.

    Args:
        framework: The team's framework id.
        manifest: The team manifest (``host_features`` / ``privilege_profile``).

    Returns:
        For every team framework (claude, goose, copilot-vscode, copilot-cli, codex): True
        whenever the switch is emitted (the manifest carries ``enforce_decision_signing``) or
        confinement is requested, WHATEVER that team's own profile or platform. A launcher
        emitted by ANY confined team in the repo requires these entries once the team's switch
        exists, so no team (cooperative claude/goose included, since 2026-09-30) may brick it.
        A stub reads exactly like an absent file. False for every other framework (agents-md is
        out of scope).
    """
    if framework in ("claude", "goose", "copilot-vscode", "copilot-cli", "codex"):
        from agentteams.frameworks._linux_sandbox_emit import _sandbox_confinement_requested

        return "enforce_decision_signing" in manifest or _sandbox_confinement_requested(manifest)
    return False


def _create_exclusive(path: Path, text: str) -> bool:
    """Create ``path`` with ``text`` only if nothing (not even a symlink) is there."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def write_control_plane_stubs(
    output_dir: Path, framework: str, manifest: dict[str, Any], *, dry_run: bool = False
) -> list[Path]:
    """Write the comment-only roster stubs that are absent (write-if-absent, never overwrite).

    Args:
        output_dir: The team's agents dir.
        framework: The team's framework id (gates on that framework's sandbox predicate).
        manifest: The team manifest.
        dry_run: Report what would be created without writing.

    Returns:
        The paths created (or that would be created). Empty when the predicate is off.

    Raises:
        OSError: A stub could not be created for a reason other than "already exists".
    """
    # The launcher requires the rosters whenever the team's SWITCH exists on disk, so honour that
    # too: a manifest built without the switch key (multi_sync) must not leave a team unhealed.
    switch_on_disk = (output_dir / "references" / "agent-privilege.json").is_file()
    if not (stubs_enabled(framework, manifest) or switch_on_disk):
        return []
    from agentteams.frameworks._sandbox_emit import CONTROL_PLANE_STUB_TEXT

    refs = output_dir / "references"
    created: list[Path] = []
    for name, text in CONTROL_PLANE_STUB_TEXT.items():
        path = refs / name
        if os.path.lexists(path):
            continue
        if dry_run:
            created.append(path)
            continue
        refs.mkdir(parents=True, exist_ok=True)
        if _create_exclusive(path, text):
            created.append(path)
    return created


def _probe_dir(directory: Path) -> OSError | None:
    """mkstemp + unlink in ``directory``; return the OSError when the OS denies the create."""
    try:
        fd, name = tempfile.mkstemp(prefix=".agentteams-write-probe-", dir=directory)
    except OSError as exc:
        return exc if exc.errno in _DIR_DENIED else None
    os.close(fd)
    try:
        os.unlink(name)
    except OSError as exc:  # a denied unlink is a denial too; anything else is "can't tell"
        return exc if exc.errno in _DIR_DENIED else None
    return None


def _probe_file(path: Path) -> OSError | None:
    """Open an existing REGULAR file write-only, non-truncating, non-blocking, no-follow.

    Never creates or modifies anything (no ``O_CREAT``/``O_TRUNC``; nothing written). A FIFO,
    socket or symlink is never opened (lstat first, and ``O_NONBLOCK|O_NOFOLLOW`` as a belt), so
    a planted FIFO cannot hang the run. ENOENT (e.g. a Seatbelt-denied path that does not exist
    yet), ELOOP and ENXIO mean "can't tell", never "writable" and never "denied".
    """
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
    except OSError:
        return None
    flags = os.O_WRONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return exc if exc.errno in _FILE_DENIED else None
    os.close(fd)
    return None


def probe_team_write_denied(output_dir: Path) -> tuple[Path, OSError] | None:
    """Return ``(path, error)`` for the first write-denied team location, else None.

    Probes the team dir and its ``references/`` with a create+unlink, then each existing
    control-plane file there (the switch, the verify-key store sentinel, the rosters) with a
    non-mutating write-only open. A team dir that does not exist yet is "can't tell" (None).

    Args:
        output_dir: The team's agents dir.

    Returns:
        The denied path and the OS error, or None when no denial was observed.
    """
    from agentteams.frameworks._sandbox_emit import (
        CONTROL_PLANE_STUB_TEXT,
        VERIFY_KEY_STORE_SENTINEL_REL,
    )

    for directory in (output_dir, output_dir / "references"):
        if directory.is_dir() and not directory.is_symlink():
            err = _probe_dir(directory)
            if err is not None:
                return directory, err
    rels = ["references/agent-privilege.json", VERIFY_KEY_STORE_SENTINEL_REL,
            *(f"references/{name}" for name in CONTROL_PLANE_STUB_TEXT)]
    for rel in rels:
        err = _probe_file(output_dir / rel)
        if err is not None:
            return output_dir / rel, err
    return None


__all__ = ["probe_team_write_denied", "stubs_enabled", "write_control_plane_stubs"]
