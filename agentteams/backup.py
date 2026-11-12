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
    "prune_backups", "inert_backup_name", "backup_before_projection",
    "BACKUP_GITIGNORE_TEXT", "ensure_backup_root_ignored",
]


#: The pre-write snapshot directory. CH-05: this is the ONE definition — `fence_inject`,
#: `cli.artifacts`, `architecture` and `interop` all import it rather than restating the
#: literal, which had drifted into four independent spellings of the same string.
#:
#: Only the NAME is shared. Each consumer keeps its own set membership, because the questions
#: differ: `architecture._EXCLUDE_DIRS` prunes an import-graph walk, `interop._NON_AGENT_DIRS`
#: filters agent discovery, and `cli.artifacts._SCRATCH_DIR_NAMES` bounds index sources. They
#: overlap; they are not the same set, and collapsing them would be a worse coupling than the
#: duplication it removed.
BACKUP_DIR_NAME = ".agentteams-backups"
_BACKUP_DIR_NAME = BACKUP_DIR_NAME  # legacy private alias; existing importers resolve unchanged

#: Default number of most-recent backups prune_backups keeps (remediation Plan 3).
DEFAULT_BACKUP_KEEP_LAST = 10

#: Written into every backup root so the backups never show as untracked in a committed tree (a hub under
#: ``.agentteams/canonical/agents`` or any framework agents dir), whatever the consumer's ``.gitignore`` says.
BACKUP_GITIGNORE_TEXT = "# agentteams: local recovery copies; never commit (remove this file to opt in)\n*\n"


def ensure_backup_root_ignored(backup_root: Path) -> bool:
    """Write a self-ignoring ``.gitignore`` (``*``) into *backup_root* unless one is already there.

    Remediation row 662 (1): hub backups in ``.agentteams/canonical/agents/.agentteams-backups`` showed as
    untracked noise in a committed hub when the consumer's ``.gitignore`` lacked ``.agentteams-backups``. An
    existing ``.gitignore`` (or a symlink at that name) is never touched, so a consumer who removed or
    replaced it to commit their backups keeps that choice.

    Args:
        backup_root: The ``.agentteams-backups`` directory (created by the caller).

    Returns:
        True if the file was written, False if one already existed or the write failed (non-fatal: the
        backup itself is unaffected).
    """
    target = backup_root / ".gitignore"
    if target.exists() or target.is_symlink():
        return False
    try:
        _atomic_write_text(target, BACKUP_GITIGNORE_TEXT)
    except OSError as exc:
        print(f"  !  Could not write {target}: {exc}", file=sys.stderr)
        return False
    return True


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
# Files two levels above output_dir (the repo-root AGENTS.md of a codex ``.codex/agents`` or goose
# ``.goose/recipes`` team) — keyed relative to ``output_dir.parent.parent``. Before this prefix
# existed they fell back to ``__external__/<name>`` and restored one level too deep.
_EXTERNAL2_BACKUP_PREFIX = "__external2__"


# Backups live INSIDE the agents dir, and the agent runtimes load agent files from it recursively:
# Codex every `*.toml` under `.codex/agents/` (codex-rs agent-roles discovery.rs), and Claude Code every
# `*.md` under `.claude/agents/`, dot-directories included (measured 2026-10-12, Claude Code 2.1.296: a
# backed-up pre-switch `primary-producer.md` shadowed the narrowed one and kept Write, defeating the write
# policy). Goose recipes (`*.yaml`) and Copilot agents (`*.agent.md`) are treated the same. Loadable
# extensions are stored with this suffix and mapped back on restore; legacy backups are neutralized by
# :func:`neutralize_legacy_backups`.
_INERT_BACKUP_SUFFIX = ".agentteams-bak"
_LOADABLE_BACKUP_EXTENSIONS = (".toml", ".md", ".yaml", ".yml")


def _backup_rel(src: Path, output_dir: Path) -> Path:
    """Return the in-backup relative path for *src* (handles out-of-tree files)."""
    if src.is_relative_to(output_dir):
        rel = src.relative_to(output_dir)
    elif src.is_relative_to(output_dir.parent):
        rel = Path(_EXTERNAL_BACKUP_PREFIX) / src.relative_to(output_dir.parent)
    elif src.is_relative_to(output_dir.parent.parent):
        rel = Path(_EXTERNAL2_BACKUP_PREFIX) / src.relative_to(output_dir.parent.parent)
    else:
        rel = Path(_EXTERNAL_BACKUP_PREFIX) / src.name
    return rel.with_name(inert_backup_name(rel.name))


def inert_backup_name(name: str) -> str:
    """Return the in-backup file name for *name* (loadable extensions made inert).

    Args:
        name: A file name about to be copied into a backup directory.

    Returns:
        *name* with the inert suffix appended when Codex would otherwise load the copy.
    """
    return name + _INERT_BACKUP_SUFFIX if name.endswith(_LOADABLE_BACKUP_EXTENSIONS) else name


def lost_sidecar_name(rel: str, sid: str) -> str:
    """Return the in-backup name of the sidecar holding a lost fence body (always inert).

    Backups live inside the agents directory, which agent runtimes load recursively, so a
    ``<rel>.lost.<sid>.md`` sidecar was itself a loadable agent file (1.0 must-fix 377).

    Args:
        rel: The flattened, team-relative path of the file the body was lost from.
        sid: The fence section id (``unfenced-<id>`` for a dropped twin).

    Returns:
        ``<rel>.lost.<sid>.md`` with the inert backup suffix.
    """
    return inert_backup_name(f"{rel}.lost.{sid}.md")


#: The names :func:`lost_sidecar_name` writes (section ids and ``unfenced-<id>``), either naming.
_LOST_SIDECAR_RE = re.compile(r"\.lost\.(?:unfenced-)?[A-Za-z0-9_-]+\.md$")


def is_lost_sidecar(rel: Path, manifest_paths: frozenset[str] = frozenset()) -> bool:
    """True when *rel* (an in-backup relative path) is a lost-fence sidecar.

    A sidecar is never listed in the backup manifest, so a backed-up live file whose own name
    happens to look like one (``release.lost.notes.md``) is recognised by its manifest entry.

    Args:
        rel: The path relative to the backup directory.
        manifest_paths: The manifest's ``backup_path`` values (empty for a legacy backup).

    Returns:
        True for a sidecar under the inert or the pre-1.0 naming.
    """
    return str(rel) not in manifest_paths and bool(_LOST_SIDECAR_RE.search(_restored_rel(rel).name))


def _manifest_backup_paths(backup_path: Path) -> frozenset[str]:
    """The ``backup_path`` values listed in *backup_path*'s manifest (empty if absent or malformed)."""
    import json as _json

    try:
        files = _json.loads((backup_path / BACKUP_MANIFEST_NAME).read_text(encoding="utf-8")).get("files")
    except (OSError, ValueError, AttributeError):
        return frozenset()
    if not isinstance(files, list):
        return frozenset()
    return frozenset(e["backup_path"] for e in files if isinstance(e, dict) and isinstance(e.get("backup_path"), str))


def loadable_backup_files(output_dir: Path) -> list[Path]:
    """Backed-up files under *output_dir*'s backup tree that an agent runtime would still load.

    Args:
        output_dir: The agents directory (``.claude/agents``, ``.goose/recipes``, ``.codex/agents`` ...).

    Returns:
        Files under ``<output_dir>/.agentteams-backups/`` whose name ends in a loadable extension
        (:data:`_LOADABLE_BACKUP_EXTENSIONS`), sorted. Empty when there is no backup tree.
    """
    root = output_dir / _BACKUP_DIR_NAME
    if not root.is_dir():
        return []
    # Symlinks included (never followed): a loader may follow one, so it is renamed/reported like a file.
    return sorted(p for p in root.rglob("*") if (p.is_symlink() or p.is_file())
                  and p.name.endswith(_LOADABLE_BACKUP_EXTENSIONS))


#: Agent-runtime dirs whose loaders read agent files beneath them, dot-directories included (measured for
#: Claude Code and Codex; see the comment above). Grok Build loads `.grok/agents/*.md` (and `.claude/agents`);
#: whether it walks dot-subdirectories is unmeasured, so it is included: the rename never deletes, so the
#: fail-safe choice costs nothing (IM item 01 review).
RUNTIME_AGENT_DIRS: tuple[str, ...] = (".claude/agents", ".goose/recipes", ".codex/agents", ".grok/agents")


def _project_root_of(output_dir: Path) -> Path | None:
    """The project root an output dir belongs to: the dir itself when it holds a runtime dir (a bridge backs
    up the project root), else :func:`agentteams.prompt_roots.team_project_root`, else ``None``."""
    from agentteams.prompt_roots import team_project_root

    if any((output_dir / rel).is_dir() for rel in RUNTIME_AGENT_DIRS):
        return output_dir
    found = team_project_root(output_dir)
    return found[0] if found else None


def runtime_loadable_backups(project_root: Path) -> list[Path]:
    """Backed-up files an agent runtime would still load, across every runtime dir of a project.

    Args:
        project_root: The project root.

    Returns:
        :func:`loadable_backup_files` over each existing :data:`RUNTIME_AGENT_DIRS` entry, in order.

    Raises:
        OSError: An unreadable backup tree.
    """
    return [p for rel in RUNTIME_AGENT_DIRS for p in loadable_backup_files(project_root / rel)]


def neutralize_runtime_backups(project_root: Path, *, skip: Path | None = None) -> list[str]:
    """Run :func:`neutralize_legacy_backups` on every existing agent-runtime dir of a project.

    Args:
        project_root: The project root.
        skip: An agents dir already neutralized by the caller.

    Returns:
        The renamed files, as ``<runtime dir>/<backup root>/<path>``.

    Raises:
        OSError: When a rename or manifest write fails.
    """
    out: list[str] = []
    root = project_root.resolve()
    for rel in RUNTIME_AGENT_DIRS:
        agents = project_root / rel
        if not agents.is_dir() or (skip is not None and agents.resolve() == skip.resolve()):
            continue
        # Never leave the project (@security): a symlinked runtime dir or backup root pointing at a shared or
        # user-global location (e.g. output_dir = ~/.claude/agents makes $HOME the root) is skipped.
        backups = agents / _BACKUP_DIR_NAME
        if root not in agents.resolve().parents or (backups.exists() and root not in backups.resolve().parents):
            continue
        out += [f"{rel}/{_BACKUP_DIR_NAME}/{r}" for r in neutralize_legacy_backups(agents)]
    return out


def warn_runtime_backups(output_dir: Path) -> list[Path]:
    """Print a WARNING per backed-up agent file a runtime would still load, for ``--check`` (warn-first).

    Args:
        output_dir: The agents dir ``--check`` runs on; the project's other runtime dirs are checked too.

    Returns:
        The loadable files found (the output dir's own tree and the project's runtime dirs, de-duplicated).

    Raises:
        OSError: An unreadable backup tree.
    """
    root = _project_root_of(output_dir.resolve()) or output_dir
    found = list(dict.fromkeys([*loadable_backup_files(output_dir), *runtime_loadable_backups(root)]))
    for p in found:
        print(f"  WARNING: {p} loads as a live agent (a backup copy made before 2026-10-12); the next "
              "--update/--bridge-merge renames it *.agentteams-bak, never deletes it.", file=sys.stderr)
    return found


def neutralize_legacy_backups(output_dir: Path) -> list[str]:
    """Rename loadable files in existing backups to their inert names, keeping each manifest consistent.

    Backups made before ``.md``/``.yaml`` became inert (2026-10-12) can shadow live agents. This renames
    them in place (never deletes) and rewrites the ``backup_path`` entries of each backup's
    ``_manifest.json`` so ``--verify-backup`` and ``--restore-backup`` keep working.

    Args:
        output_dir: The agents directory.

    Returns:
        The renamed files, relative to the backup root.

    Raises:
        OSError: When a rename or manifest write fails.
    """
    import json as _json

    root = output_dir / _BACKUP_DIR_NAME
    renamed: list[str] = []
    for path in loadable_backup_files(output_dir):
        target = path.with_name(inert_backup_name(path.name))
        if target.exists() or target.is_symlink():
            continue  # never overwrite; loadable_backup_files (and --check-wiring) still report it
        path.rename(target)
        renamed.append(str(path.relative_to(root)))
        backup_dir = root / path.relative_to(root).parts[0]
        manifest_path = backup_dir / BACKUP_MANIFEST_NAME
        if manifest_path.is_file() and not manifest_path.is_symlink():
            try:
                manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
                files = manifest.get("files") if isinstance(manifest, dict) else None
            except (ValueError, UnicodeDecodeError):
                files = None
            if not isinstance(files, list):
                continue  # malformed manifest: the rename stands; --verify-backup reports the mismatch
            old_rel, new_rel = str(path.relative_to(backup_dir)), str(target.relative_to(backup_dir))
            for entry in files:
                if isinstance(entry, dict) and entry.get("backup_path") == old_rel:
                    entry["backup_path"] = new_rel
            _atomic_write_text(manifest_path, _json.dumps(manifest, indent=2) + "\n")
    return renamed


def _restored_rel(rel: Path) -> Path:
    """Undo :func:`_backup_rel`'s inert suffix on an in-backup relative path."""
    if rel.name.endswith(_INERT_BACKUP_SUFFIX):
        return rel.with_name(rel.name[: -len(_INERT_BACKUP_SUFFIX)])
    return rel


def _restore_dest(output_dir: Path, rel: Path) -> Path:
    """Map an in-backup relative path back to its on-disk destination."""
    rel = _restored_rel(rel)
    if rel.parts and rel.parts[0] == _EXTERNAL_BACKUP_PREFIX:
        return output_dir.parent / Path(*rel.parts[1:])
    if rel.parts and rel.parts[0] == _EXTERNAL2_BACKUP_PREFIX:
        return output_dir.parent.parent / Path(*rel.parts[1:])
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

    # Legacy backups (before .md/.yaml became inert) can shadow live agents: neutralize them first, in this
    # dir AND in every agent-runtime dir of the same project. A Copilot-primary repo with a Claude bridge
    # never backs up .claude/agents itself, so its old stubs were never renamed (IM item 01, G-14-1).
    renamed: list[str] = []
    try:
        if not dry_run:  # a dry run writes nothing, renames included (@security)
            renamed = neutralize_legacy_backups(output_dir)
            project_root = _project_root_of(output_dir)
            if project_root is not None:
                renamed += neutralize_runtime_backups(project_root, skip=output_dir)
    except OSError as exc:
        print(f"  WARNING: couldn't neutralize loadable files in {output_dir / _BACKUP_DIR_NAME}: {exc}",
              file=sys.stderr)
    else:
        if renamed:
            print(f"  ✓  Neutralized {len(renamed)} loadable backup file(s) under {_BACKUP_DIR_NAME}/ "
                  f"(renamed *{_INERT_BACKUP_SUFFIX}) so no agent runtime loads them as agents:", file=sys.stderr)
            for rel in renamed:
                print(f"       {rel}", file=sys.stderr)

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
    ensure_backup_root_ignored(backup_path.parent)

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
    sidecar_rels: set[Path] = set()
    manifest_paths = _manifest_backup_paths(backup_path)
    for src in backup_path.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(backup_path)
        if rel == Path(BACKUP_MANIFEST_NAME):
            continue
        if is_lost_sidecar(rel, manifest_paths):
            sidecar_rels.add(_restored_rel(rel))  # recovery material, never restored live (377)
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
        restored_rels = {_restored_rel(r) for r in backup_rels}
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
            if rel in sidecar_rels:
                # A live file named like a skipped sidecar: keep it and say so, never delete it.
                print(f"  ⚠  {rel}: kept (its name matches a lost-fence sidecar that was not restored)",
                      file=sys.stderr)
                continue
            if rel not in restored_rels:
                candidate.unlink()

    return count


def backup_before_projection(agents_dir: Path, framework: str) -> None:
    """Snapshot a framework's agents dir, skills and instruction file before a pinned-sync projection overwrites.

    Projection writes with ``overwrite=True`` into directories that are commonly gitignored, so
    without this a bad projection is unrecoverable (incident 2026-09-29). Uses the standard
    ``.agentteams-backups/<timestamp>/`` store, so ``--restore-backup`` applies, and prunes it to
    :data:`DEFAULT_BACKUP_KEEP_LAST`. Moved here from ``multi_sync`` (2026-11-05).

    Args:
        agents_dir: The framework's agents directory (nothing is done when it is absent).
        framework: The framework id (selects the skills and instruction-file locations).

    Returns:
        None.

    Raises:
        OSError: A backup copy failed (before projection writes anything).
    """
    from agentteams.frameworks.registry import FRAMEWORKS  # lazy: the registry imports adapters that import this

    if not agents_dir.is_dir():
        return
    adapter = FRAMEWORKS[framework]()
    files = [p for p in agents_dir.rglob("*")
             if p.is_file() and BACKUP_DIR_NAME not in p.relative_to(agents_dir).parts]
    skills = adapter.skills_dir(agents_dir)
    if adapter.has_skill_concept() and skills.is_dir():
        files += [p for p in skills.rglob("*") if p.is_file()]  # projection rewrites skills too
    rels = [os.path.relpath(p, agents_dir) for p in files]
    rels.append(adapter.finalize_output_path("../copilot-instructions.md", "instructions"))
    backup_output_dir(agents_dir, files_to_backup=rels, reason="pre-sync-projection", framework=framework)
    prune_backups(agents_dir)  # keep the default most-recent N; every --sync would add one
