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
import csv
import io
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

__all__ = [
    "_target_mode", "_atomic_write_text", "_atomic_copy", "_resolve_path",
    "atomic_rewrite_csv_rows",
]


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
    """Write *text* to *path* atomically (temp-in-same-dir + os.replace + fsync).

    A file that is BOTH a script by extension (``.sh``/``.py``/``.bash``/``.zsh`` or
    extensionless) AND begins with a ``#!`` shebang is made executable: the execute bits are set
    to mirror the read bits (``0o644`` → ``0o755``, a preserved ``0o664`` → ``0o775``). This keeps
    emitted launchers/hooks (e.g. the framework-neutral ``sandbox/confine-run.sh`` and the
    constitutional-gate hook) runnable on a FRESH emit, where the umask default would otherwise
    leave a new file at ``0o644``. The extension guard bounds the content sniff: a data/doc file
    (``.md``/``.csv``/``.json``/agent ``.md``) is never marked executable even if its first two
    bytes happen to be ``#!`` — the +x path is limited to files that are already script-shaped.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = _target_mode(path)
    _SCRIPT_SUFFIXES = ("", ".sh", ".py", ".bash", ".zsh")
    if text.startswith("#!") and path.suffix.lower() in _SCRIPT_SUFFIXES:
        mode |= (mode & 0o444) >> 2
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

def atomic_rewrite_csv_rows(
    path: Path, rows: list[dict[str, Any]], fieldnames: list[str],
) -> None:
    """Rewrite a CSV ledger's rows safely: verify-in-memory, then write once.

    2026-08-11 finding: this project's audit/remediation CSV ledgers
    (``conflict-log.csv``, ``agentteams-remediation-log.csv``, plan
    ``steps.csv`` files) have hit the same failure class three times —
    unescaped commas in a free-text field split a row into extra columns,
    and a naive ``csv.DictWriter`` rewrite (``open(path, 'w')`` truncates
    immediately, before the writer can raise on the bad row) destroys the
    file rather than just failing to update it. This function renders
    *rows* to a CSV string in memory, re-parses that string and refuses to
    touch disk at all if any row doesn't round-trip to exactly
    ``len(fieldnames)`` columns, and only then delegates to
    `_atomic_write_text` (temp-file-in-same-dir + ``os.replace``) — so a
    malformed row already present in *rows*, or a bug in the caller that
    would produce one, is caught before the destination file is ever
    opened for writing, not after.

    2026-08-13 finding: the ``lineterminator="\\n"`` below was hardcoded regardless of
    *path*'s existing convention, so rewriting even one row of an otherwise
    CRLF-terminated CSV silently reformatted the entire file to LF — an unrequested,
    unrelated-to-the-edit reformat. Fixed by sniffing *path*'s current line ending (if
    it already exists) and preserving it; a brand-new file still defaults to LF.

    Raises:
        ValueError: a row holds a non-``str`` value for a declared field, or
            a rendered row didn't round-trip to exactly ``len(fieldnames)``
            columns — *path* is left untouched either way.
    """
    for i, row in enumerate(rows):
        for key in fieldnames:
            if key in row and not isinstance(row[key], str):
                raise ValueError(
                    f"row {i} field {key!r} is {type(row[key]).__name__}, not str "
                    f"({row[key]!r}) — csv.DictWriter would silently str()-convert "
                    f"this instead of erroring, so refusing to write {path}"
                )

    line_terminator = "\n"
    if path.exists():
        with open(path, "rb") as existing:
            raw = existing.read()
        if b"\r\n" in raw:
            line_terminator = "\r\n"

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator=line_terminator)
    writer.writeheader()
    writer.writerows(rows)
    text = buf.getvalue()

    reparsed = list(csv.reader(io.StringIO(text)))
    header, data_rows = reparsed[0], reparsed[1:]
    if header != fieldnames:
        raise ValueError(f"rendered header {header!r} != expected {fieldnames!r}")
    bad = [i for i, r in enumerate(data_rows) if len(r) != len(fieldnames)]
    if bad:
        raise ValueError(
            f"{len(bad)} rendered row(s) did not round-trip to {len(fieldnames)} "
            f"columns (first bad index: {bad[0]}) — refusing to write {path}"
        )
    if len(data_rows) != len(rows):
        raise ValueError(
            f"rendered {len(data_rows)} data rows but {len(rows)} were given — "
            f"refusing to write {path}"
        )

    _atomic_write_text(path, text)


def _resolve_path(output_dir: Path, rel_path: str) -> Path:
    """Resolve a relative path that may start with '../', with a containment guard.

    Generated layouts legitimately reach up to two levels above the agents dir
    (``../copilot-instructions.md``/``../CLAUDE.md``, ``../../.vscode/tasks.json``,
    goose ``../../AGENTS.md`` / ``../../.goosehints``), so the allowed boundary is
    ``output_dir.parent.parent``. A path that resolves outside that boundary (an
    absolute path, or deeper ``../`` traversal) is rejected — defense-in-depth so a
    future descriptor-derived path can't silently become an arbitrary-file
    overwrite. Callers still own the provenance of ``rel_path``.
    """
    resolved = (output_dir / Path(rel_path)).resolve()
    boundary = output_dir.resolve().parent.parent
    if not resolved.is_relative_to(boundary):
        raise ValueError(
            f"resolved path {resolved} escapes the allowed boundary {boundary} "
            f"(output_dir={output_dir}, rel_path={rel_path!r})"
        )
    return resolved
