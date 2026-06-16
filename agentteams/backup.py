"""
backup.py — output-directory backup / restore / verify / prune / off-machine mirror.

Extracted verbatim from emit.py (CH-07) — the data-safety backup subsystem (the
W21/W22 retention work). emit.py re-exports every public name here so callers
(cli/, build_team, drift, tests) resolve ``emit.<symbol>`` unchanged. Atomic
primitives come from agentteams.atomicio (no import back into emit -> no cycle).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agentteams.atomicio import _atomic_copy, _atomic_write_text, _resolve_path

__all__ = [
    "BackupResult", "PruneResult", "DEFAULT_BACKUP_KEEP_LAST",
    "BACKUP_MANIFEST_NAME", "BACKUP_MANIFEST_SCHEMA_VERSION",
    "backup_output_dir", "restore_backup", "list_backups", "verify_backup",
    "prune_backups",
]


_BACKUP_DIR_NAME = ".agentteams-backups"

#: Default number of most-recent backups prune_backups keeps (remediation Plan 3).
DEFAULT_BACKUP_KEEP_LAST = 10


@dataclass
class BackupResult:
    """Result of a backup operation.

    Attributes:
        backup_path:  Absolute path to the timestamped backup directory, or None
                      if no backup was taken (e.g. output_dir did not exist).
        files_backed_up: Number of files copied into the backup.
        skipped:         True if backup was suppressed (--no-backup or dry_run).
        extra_files_removed: Number of files deleted from output_dir during a
                             restore because they were absent from the backup.
    """
    backup_path: Path | None = None
    files_backed_up: int = 0
    skipped: bool = False
    extra_files_removed: int = 0


BACKUP_MANIFEST_NAME = "_manifest.json"
BACKUP_MANIFEST_SCHEMA_VERSION = "1.0"

# Reserved subtree inside a backup for files that live OUTSIDE output_dir but
# under its parent (e.g. ``../CLAUDE.md``, ``../skills/<tool>.md``). Storing them
# under this prefix — keyed by their path relative to ``output_dir.parent`` —
# lets restore map them back to the correct location instead of flattening them
# into the agents dir.
_EXTERNAL_BACKUP_PREFIX = "__external__"


def _backup_rel(src: Path, output_dir: Path) -> Path:
    """Return the in-backup relative path for *src* (handles out-of-tree files)."""
    try:
        return src.relative_to(output_dir)
    except ValueError:
        try:
            return Path(_EXTERNAL_BACKUP_PREFIX) / src.relative_to(output_dir.parent)
        except ValueError:
            return Path(_EXTERNAL_BACKUP_PREFIX) / src.name


def _restore_dest(output_dir: Path, rel: Path) -> Path:
    """Map an in-backup relative path back to its on-disk destination."""
    if rel.parts and rel.parts[0] == _EXTERNAL_BACKUP_PREFIX:
        return output_dir.parent / Path(*rel.parts[1:])
    return output_dir / rel


def _write_backup_manifest(
    backup_path: Path,
    file_pairs: list[tuple[Path, Path]],
    *,
    reason: str,
    framework: str,
    output_root: Path,
    description_path: str | None,
) -> Path:
    """Write the Plan 2 manifest sidecar inside *backup_path*.

    ``file_pairs`` is a list of (source_abs, backup_abs) Paths recorded in
    backup order. SHA-256 is computed from the backup copy (which is
    byte-identical to the source via ``shutil.copy2``); using the backup avoids
    a TOCTOU window where the source might mutate between copy and hash.
    """
    import json as _json
    from datetime import timezone as _tz

    try:
        from agentteams import __version__ as _agentteams_version
    except (ImportError, AttributeError):
        _agentteams_version = None

    files: list[dict[str, Any]] = []
    total_bytes = 0
    for src, dst in file_pairs:
        try:
            data = dst.read_bytes()
        except OSError:
            continue
        size = len(data)
        sha = hashlib.sha256(data).hexdigest()
        total_bytes += size
        try:
            src_rel = str(src.relative_to(output_root))
        except ValueError:
            src_rel = str(src)
        try:
            dst_rel = str(dst.relative_to(backup_path))
        except ValueError:
            dst_rel = dst.name
        files.append({
            "source_path": src_rel,
            "backup_path": dst_rel,
            "source_size_bytes": size,
            "source_sha256": sha,
        })

    manifest = {
        "artifact_type": "backup-manifest",
        "manifest_schema_version": BACKUP_MANIFEST_SCHEMA_VERSION,
        "agentteams_version": str(_agentteams_version) if _agentteams_version else None,
        "framework": framework,
        "description_path": description_path,
        "output_root": str(output_root),
        "reason": reason,
        "timestamp_utc": datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_files": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }
    out = backup_path / BACKUP_MANIFEST_NAME
    _atomic_write_text(out, _json.dumps(manifest, indent=2) + "\n")
    return out


def backup_output_dir(
    output_dir: Path,
    *,
    files_to_backup: list[str] | None = None,
    dry_run: bool = False,
    reason: str = "unspecified",
    framework: str = "",
    description_path: str | None = None,
) -> BackupResult:
    """Copy existing agent files to a timestamped backup directory before a write.

    The backup is placed at ``<output_dir>/<_BACKUP_DIR_NAME>/YYYYMMDD-HHMMSS/``.
    If *files_to_backup* is given, only those paths (relative to *output_dir*)
    are backed up; otherwise every file in *output_dir* is copied (excluding the
    backup directory itself and ``references/build-log.json``).

    Args:
        output_dir:       Absolute path to the agents output directory.
        files_to_backup:  Optional list of relative paths (from render output) to
                          selectively back up.  Pass ``None`` to back up everything.
        dry_run:          If True, report what would be backed up without writing.

    Returns:
        BackupResult describing what was done.
    """
    result = BackupResult()

    # Resolve symlinks once so `_backup_rel` compares like-for-like against the
    # (already-resolved) source paths from `_resolve_path`. Without this, a
    # symlinked output root (macOS /tmp→/private/tmp, symlinked $HOME, …) makes
    # every in-tree file look out-of-tree and get mis-filed under __external__.
    output_dir = output_dir.resolve()

    if not output_dir.exists():
        result.skipped = True
        return result

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = output_dir / _BACKUP_DIR_NAME / ts
    # Ensure uniqueness if two backups happen within the same second
    if backup_path.exists():
        counter = 1
        while backup_path.exists():
            backup_path = output_dir / _BACKUP_DIR_NAME / f"{ts}-{counter}"
            counter += 1

    if dry_run:
        result.skipped = True
        print(f"[DRY RUN] BACKUP {output_dir} → {backup_path}")
        return result

    if files_to_backup is not None:
        # Selective backup: only files that are about to be overwritten.
        # Always include the CSV log files from references/ — they are not
        # template-rendered so they never appear in files_to_backup, but
        # they MUST be captured so that restore_backup can roll them back
        # to their pre-emit state (preventing duplicate rows on re-migration).
        from agentteams.liaison_logs import CHANGELOG_CSV, COORD_LOG_CSV
        _csv_extras = [
            Path("references") / CHANGELOG_CSV,
            Path("references") / COORD_LOG_CSV,
        ]
        paths: list[Path] = []
        seen: set[Path] = set()
        for rel in list(files_to_backup) + [str(p) for p in _csv_extras]:
            target = _resolve_path(output_dir, rel)
            if target.exists() and target not in seen:
                paths.append(target)
                seen.add(target)
    else:
        # Full backup of everything in output_dir (excluding backup dir itself)
        backup_root = output_dir / _BACKUP_DIR_NAME
        paths = [
            p for p in output_dir.rglob("*")
            if p.is_file() and not p.is_relative_to(backup_root)
        ]

    if not paths:
        result.skipped = True
        return result

    backup_path.mkdir(parents=True, exist_ok=True)

    file_pairs: list[tuple[Path, Path]] = []
    for src in paths:
        # Out-of-tree files (../CLAUDE.md, ../skills/<tool>.md) are stored under
        # the __external__ prefix so restore can map them back precisely.
        rel = _backup_rel(src, output_dir)
        dest = backup_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        file_pairs.append((src, dest))
        result.files_backed_up += 1

    # Plan 2: sidecar manifest documenting what was backed up and why.
    try:
        _write_backup_manifest(
            backup_path, file_pairs,
            reason=reason,
            framework=framework,
            output_root=output_dir,
            description_path=description_path,
        )
    except OSError as exc:
        # Manifest failure is non-fatal — the backup files themselves are
        # safely on disk; the operator still has a recoverable backup.
        print(f"  !  Backup manifest write failed: {exc}", file=sys.stderr)

    result.backup_path = backup_path
    print(f"  ✓  Backup created: {backup_path} ({result.files_backed_up} file(s))")
    _mirror_backup(output_dir, backup_path)
    return result


def _mirror_backup(output_dir: Path, backup_path: Path) -> None:
    """Best-effort off-machine copy of a just-created backup (remediation Plan 3).

    If ``AGENTTEAMS_BACKUP_MIRROR`` is set (e.g. a NAS/external/synced folder), copy
    *backup_path* to ``<mirror>/<output-dir-slug>/<timestamp>/`` so the recovery net
    survives a local disk loss. Namespaced by an ``output_dir``-derived slug to avoid
    collisions when mirroring many workspaces to one target. **Non-fatal**: a mirror
    failure warns and never breaks the primary operation (mirroring a recovery copy
    must not jeopardise it).
    """
    target = os.environ.get("AGENTTEAMS_BACKUP_MIRROR")
    if not target:
        return
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(output_dir)).strip("_") or "root"
    dest = Path(target) / slug / backup_path.name
    try:
        shutil.copytree(backup_path, dest, dirs_exist_ok=True)
        print(f"  ✓  Backup mirrored: {dest}")
    except OSError as exc:
        print(f"  !  Backup mirror failed (non-fatal): {exc}", file=sys.stderr)


def list_backups(output_dir: Path) -> list[tuple[str, Path, int]]:
    """Return all available backups for *output_dir*, newest first.

    Args:
        output_dir: Absolute path to the agents output directory.

    Returns:
        List of ``(timestamp_str, backup_path, file_count)`` tuples, sorted
        newest-first.  Empty list if no backups exist.
    """
    backup_root = output_dir / _BACKUP_DIR_NAME
    if not backup_root.exists():
        return []
    entries = []
    for child in sorted(backup_root.iterdir(), reverse=True):
        if child.is_dir():
            count = sum(1 for p in child.rglob("*") if p.is_file())
            entries.append((child.name, child, count))
    return entries


def verify_backup(backup_path: Path) -> list[dict[str, str]]:
    """Verify a backup's own integrity (read-only): each backed-up file's bytes
    vs its recorded ``source_sha256`` in ``_manifest.json``.

    Confirms the backup is *restorable* (catches backup bit-rot/tamper). Returns
    one entry per recorded file with keys ``source_path``, ``status``
    (``PASS`` / ``FAIL`` / ``MISSING``) and ``note``. Returns an empty list when
    the backup has no ``_manifest.json`` (older backup; cannot verify). Note the
    manifest stores the FULL sha256 (not the 16-char build-log form).
    """
    import json

    manifest_path = backup_path / BACKUP_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []

    results: list[dict[str, str]] = []
    for entry in manifest.get("files", []):
        rel = entry.get("backup_path", "")
        recorded = entry.get("source_sha256", "")
        base = {"source_path": entry.get("source_path", rel), "backup_rel": rel}
        copy_path = backup_path / rel
        if not copy_path.exists():
            results.append({**base, "status": "MISSING", "note": "backup copy absent"})
            continue
        try:
            data = copy_path.read_bytes()
        except OSError as exc:
            results.append({**base, "status": "MISSING", "note": f"unreadable: {exc}"})
            continue
        if hashlib.sha256(data).hexdigest() == recorded:
            results.append({**base, "status": "PASS", "note": ""})
        else:
            results.append({**base, "status": "FAIL", "note": "bytes do not match recorded source_sha256 (bit-rot/tamper)"})
    return results


@dataclass
class PruneResult:
    """Outcome of :func:`prune_backups`."""
    deleted: list[str] = field(default_factory=list)   # timestamps deleted (or would-delete under dry_run)
    kept: list[str] = field(default_factory=list)       # timestamps retained
    dry_run: bool = False


def _parse_backup_timestamp(name: str) -> datetime | None:
    """Parse a backup dir name (``YYYYMMDD-HHMMSS[-N]``) to a datetime, else None.

    The ``[:15]`` slice ignores the optional ``-N`` same-second collision suffix.
    """
    try:
        return datetime.strptime(name[:15], "%Y%m%d-%H%M%S")
    except ValueError:
        return None


def prune_backups(
    output_dir: Path,
    *,
    keep_last: int = DEFAULT_BACKUP_KEEP_LAST,
    keep_within_days: int | None = None,
    dry_run: bool = False,
) -> PruneResult:
    """Delete old backups under *output_dir*, keeping the recovery net bounded.

    Retain rule (union, fail-safe): a backup is KEPT if it is among the
    ``keep_last`` newest **OR** (when *keep_within_days* is set) its timestamp is
    within *keep_within_days* days. Everything else is deleted. The single
    most-recent backup is **always** kept (even ``keep_last == 0``). A backup
    whose age cannot be determined (unparseable name and no mtime) is KEPT
    (fail-safe). ``dry_run`` reports would-delete without deleting.
    """
    result = PruneResult(dry_run=dry_run)
    output_dir = output_dir.resolve()
    backups = list_backups(output_dir)  # newest-first: [(ts_name, path, file_count)]
    if not backups:
        return result

    now = datetime.now()
    for idx, (ts_name, bpath, _count) in enumerate(backups):
        keep = idx == 0 or idx < keep_last          # always-newest + keep_last window
        if not keep and keep_within_days is not None:
            parsed = _parse_backup_timestamp(ts_name)
            if parsed is None:
                try:
                    parsed = datetime.fromtimestamp(bpath.stat().st_mtime)
                except OSError:
                    parsed = None
            # indeterminate age → fail-safe keep; else keep if within the window
            keep = parsed is None or (now - parsed).days <= keep_within_days

        if keep:
            result.kept.append(ts_name)
        else:
            if not dry_run:
                shutil.rmtree(bpath, ignore_errors=True)
            result.deleted.append(ts_name)
    return result


def restore_backup(
    backup_path: Path,
    output_dir: Path,
    *,
    remove_extra: bool = False,
) -> int:
    """Restore files from a backup directory into *output_dir*.

    Copies every file from *backup_path* back to its original location under
    *output_dir*, overwriting current content.

    When *remove_extra* is ``True``, files that exist in *output_dir* but
    were absent from the backup are deleted after the restore.  This produces
    a snapshot-complete rollback — the output directory matches exactly what
    was backed up.  The backup directory itself and ``references/build-log.json``
    are excluded from the deletion scan.

    Args:
        backup_path:  Absolute path to the timestamped backup directory.
        output_dir:   Absolute path to the agents output directory to restore into.
        remove_extra: If True, remove files in output_dir that were not in the backup.

    Returns:
        Number of files restored (does not include files removed).

    Raises:
        FileNotFoundError: If *backup_path* does not exist.
    """
    if not backup_path.exists():
        raise FileNotFoundError(f"Backup not found: {backup_path}")

    # Resolve symlinks so `_restore_dest` and the `remove_extra` scan operate on
    # the same canonical root the backup was taken against (see backup_output_dir).
    output_dir = output_dir.resolve()

    # Collect the set of relative paths present in the backup. Plan 2's
    # sidecar manifest (_manifest.json) is metadata ABOUT the backup, not
    # restored content — exclude it from the restore set.
    backup_rels: set[Path] = set()
    for src in backup_path.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(backup_path)
        if rel == Path(BACKUP_MANIFEST_NAME):
            continue
        backup_rels.add(rel)

    # Restore all backed-up files
    count = 0
    for rel in backup_rels:
        src = backup_path / rel
        dest = _restore_dest(output_dir, rel)
        _atomic_copy(src, dest)
        count += 1

    # Remove files that exist in output_dir but were absent from the backup
    if remove_extra:
        backup_root = output_dir / _BACKUP_DIR_NAME
        _skip_rel = Path("references") / "build-log.json"
        for candidate in list(output_dir.rglob("*")):
            if not candidate.is_file():
                continue
            # Never touch the backup archive itself
            if candidate.is_relative_to(backup_root):
                continue
            try:
                rel = candidate.relative_to(output_dir)
            except ValueError:
                continue
            # Preserve build-log.json — it records run history, not agent content
            if rel == _skip_rel:
                continue
            if rel not in backup_rels:
                candidate.unlink()

    return count
