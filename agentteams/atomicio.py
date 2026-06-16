"""
atomicio.py — low-level atomic file write/copy + path resolution.

Extracted from emit.py (CH-07) so the backup subsystem can reuse the atomic
primitives without importing emit (which re-exports backup) — keeping the import
graph acyclic: atomicio is a leaf, backup -> atomicio, emit -> atomicio + backup.

A reader or an interrupt must see either the complete old file or the complete
new file — never a half-written/truncated one. We write to a temp file in the
SAME directory (same filesystem, so os.replace is atomic) and rename onto the
destination. CH-24: no broad except — cleanup uses finally + suppress(OSError).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

__all__ = ["_target_mode", "_atomic_write_text", "_atomic_copy", "_resolve_path"]


def _target_mode(path: Path) -> int:
    """Permission bits to apply to an atomically-written file.

    Preserve the destination's current mode when it already exists; otherwise
    use the umask-derived default ``0o666 & ~umask`` — matching ``open(.., 'w')``
    — so atomic writes do not leave files at ``mkstemp``'s 0600.
    """
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        prev = os.umask(0)
        os.umask(prev)
        return 0o666 & ~prev


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write *text* to *path* atomically (temp-in-same-dir + os.replace + fsync)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _target_mode(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    success = False
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        success = True
    finally:
        if not success:
            with contextlib.suppress(OSError):
                tmp.unlink()


def _atomic_copy(src: Path, dest: Path) -> None:
    """Copy *src* onto *dest* atomically, preserving content + mode + mtime
    (``shutil.copy2`` semantics). Used by restore so a crash mid-restore never
    truncates a live file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=f".{dest.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    success = False
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
        success = True
    finally:
        if not success:
            with contextlib.suppress(OSError):
                tmp.unlink()

def _resolve_path(output_dir: Path, rel_path: str) -> Path:
    """Resolve a relative path that may start with '../'."""
    # Paths like '../copilot-instructions.md' should go one level above agents dir
    clean = Path(rel_path)
    return (output_dir / clean).resolve()
