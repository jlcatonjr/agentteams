"""
emit.py — Write rendered agent files to disk.

Takes the list of (output_path, content) pairs from render.py
and writes them to the target output directory.

Safety features:
  - Dry-run mode: show what would be written without writing
  - No silent overwrite: existing files require --overwrite or --merge
  - Merge mode: section-fenced files preserve user-authored content
  - Summary report on completion
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class EmitResult:
    written: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


@dataclass
class MergeResult:
    """Result of a single fenced-content merge operation.

    Attributes:
        sections_replaced:  section_ids whose content was updated from the new render.
        sections_added:     section_ids present in new render but absent in existing file.
        sections_orphaned:  section_ids present in existing file but absent in new render.
        parse_errors:       Human-readable messages for parse failures.
        unchanged:          section_ids that were identical in both files (no write needed).
        merged_content:     The final merged file content (empty string on parse failure).
    """
    sections_replaced: list[str] = field(default_factory=list)
    sections_added: list[str] = field(default_factory=list)
    sections_orphaned: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    merged_content: str = ""

    @property
    def has_errors(self) -> bool:
        return bool(self.parse_errors)

    @property
    def content_changed(self) -> bool:
        return bool(self.sections_replaced or self.sections_added)


# ---------------------------------------------------------------------------
# Section-fencing internals
# ---------------------------------------------------------------------------

_FENCE_BEGIN_RE = re.compile(
    r"<!-- AGENTTEAMS:BEGIN (?P<sid>[a-z][a-z0-9_]*) v=\d+ -->",
)
_FENCE_END_RE = re.compile(
    r"<!-- AGENTTEAMS:END (?P<sid>[a-z][a-z0-9_]*) -->",
)
_YAML_FM_RE = re.compile(r"^(---\n.+?\n---\n)", re.DOTALL)

_MACHINE_MANAGED_MERGE_OVERWRITE_PATHS: frozenset[str] = frozenset([
    "references/security-vulnerability-watch.json",
])


def _normalize_generated_content(rel_path: str, content: str) -> str:
    """Return emitted content normalized for merge-safe markdown generation.

    Markdown outputs participate in section-fencing merge mode. When a rendered
    markdown file has no AGENTTEAMS fences at all, wrap the full file in a
    default fence block so future ``--merge`` runs can update it safely.

    Args:
        rel_path: Relative output path for the generated file.
        content: Rendered file body.

    Returns:
        Content ready for write/merge.
    """
    if not rel_path.endswith(".md"):
        return content

    existing_regions = _extract_fenced_regions(content)
    if isinstance(existing_regions, dict) and existing_regions:
        return content

    # If content has YAML front matter, wrap only the body so the front matter
    # stays at the top of the file (as required by all framework parsers).
    fm_match = _YAML_FM_RE.match(content)
    if fm_match:
        front_matter = fm_match.group(1)
        body = content[len(front_matter):]
        normalized = front_matter + "<!-- AGENTTEAMS:BEGIN content v=1 -->\n" + body
        if not normalized.endswith("\n"):
            normalized += "\n"
        normalized += "<!-- AGENTTEAMS:END content -->\n"
        return normalized

    normalized = "<!-- AGENTTEAMS:BEGIN content v=1 -->\n" + content
    if not normalized.endswith("\n"):
        normalized += "\n"
    normalized += "<!-- AGENTTEAMS:END content -->\n"
    return normalized


def _extract_fenced_regions(content: str) -> dict[str, str] | str:
    """Extract all fenced regions from *content*.

    Returns a dict mapping ``section_id`` to the full fenced block (including
    the BEGIN and END markers) on success, or an error message string on failure.

    Failure conditions: unclosed BEGIN, duplicate section_id, mismatched END.
    """
    regions: dict[str, str] = {}
    lines = content.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        begin_match = _FENCE_BEGIN_RE.search(lines[i])
        if begin_match:
            sid = begin_match.group("sid")
            if sid in regions:
                return f"Duplicate section_id '{sid}'"
            block_lines = [lines[i]]
            i += 1
            closed = False
            while i < len(lines):
                end_match = _FENCE_END_RE.search(lines[i])
                if end_match:
                    end_sid = end_match.group("sid")
                    if end_sid != sid:
                        return f"Mismatched END: expected '{sid}', got '{end_sid}'"
                    block_lines.append(lines[i])
                    closed = True
                    i += 1
                    break
                # Check for nested BEGIN (not allowed)
                if _FENCE_BEGIN_RE.search(lines[i]):
                    nested_sid = _FENCE_BEGIN_RE.search(lines[i]).group("sid")
                    return f"Nested fence not allowed: '{nested_sid}' inside '{sid}'"
                block_lines.append(lines[i])
                i += 1
            if not closed:
                return f"Unclosed fence: '{sid}' has no END marker"
            regions[sid] = "".join(block_lines)
        else:
            i += 1
    return regions


def _is_machine_managed_merge_overwrite_path(rel_path: str) -> bool:
    """Return True when merge mode may safely full-replace a machine-managed file."""
    return rel_path in _MACHINE_MANAGED_MERGE_OVERWRITE_PATHS


def _merge_fenced_content(new_rendered: str, existing_on_disk: str) -> MergeResult:
    """Merge fenced sections from *new_rendered* into *existing_on_disk*.

    Template-owned (fenced) regions in the existing file are replaced with
    the corresponding regions from the new render.  All content outside any
    fence marker is preserved unchanged.

    Args:
        new_rendered:     Fully rendered file content from the render phase.
        existing_on_disk: Current content of the on-disk file.

    Returns:
        MergeResult describing what changed.  ``merged_content`` is empty on
        parse failure.
    """
    result = MergeResult()

    # Parse existing file
    existing_regions = _extract_fenced_regions(existing_on_disk)
    if isinstance(existing_regions, str):
        # String return means error
        if "has no" in existing_regions and "END marker" in existing_regions:
            result.parse_errors.append(
                f"Existing file parse error: {existing_regions}"
            )
        elif not existing_regions:
            # _extract_fenced_regions returns empty dict for no fences
            pass
        else:
            result.parse_errors.append(
                f"Existing file parse error: {existing_regions}"
            )
        return result

    if not existing_regions:
        result.parse_errors.append(
            "No fence markers detected — legacy file. "
            "Use --overwrite to replace unconditionally, or add "
            "AGENTTEAMS fence markers manually."
        )
        return result

    # Parse new render
    new_regions = _extract_fenced_regions(new_rendered)
    if isinstance(new_regions, str):
        result.parse_errors.append(
            f"New render parse error: {new_regions}"
        )
        return result

    # Rebuild the existing file by replacing each fenced block in-place
    lines = existing_on_disk.splitlines(keepends=True)
    output_lines: list[str] = []
    i = 0
    replaced_sids: set[str] = set()

    while i < len(lines):
        begin_match = _FENCE_BEGIN_RE.search(lines[i])
        if begin_match:
            sid = begin_match.group("sid")
            # Skip the entire old fenced block
            i += 1
            while i < len(lines):
                if _FENCE_END_RE.search(lines[i]):
                    i += 1
                    break
                i += 1
            # Inject replacement or preserve orphan
            if sid in new_regions:
                output_lines.append(new_regions[sid])
                if new_regions[sid] == existing_regions.get(sid, ""):
                    result.unchanged.append(sid)
                else:
                    result.sections_replaced.append(sid)
                replaced_sids.add(sid)
            else:
                # Orphaned: in existing but not in new render — leave in place
                output_lines.append(existing_regions[sid])
                result.sections_orphaned.append(sid)
        else:
            output_lines.append(lines[i])
            i += 1

    merged = "".join(output_lines)

    # Append sections that are new (in new render but not in existing file)
    for sid, block in new_regions.items():
        if sid not in replaced_sids and sid not in result.sections_orphaned:
            merged = merged.rstrip("\n") + "\n\n" + block
            if not merged.endswith("\n"):
                merged += "\n"
            result.sections_added.append(sid)

    result.merged_content = merged
    return result


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

_BACKUP_DIR_NAME = ".agentteams-backups"


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


def backup_output_dir(
    output_dir: Path,
    *,
    files_to_backup: list[str] | None = None,
    dry_run: bool = False,
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

    for src in paths:
        try:
            rel = src.relative_to(output_dir)
        except ValueError:
            # File is outside output_dir (e.g. ../copilot-instructions.md) —
            # store with a safe flattened name
            rel = Path(src.name)
        dest = backup_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        result.files_backed_up += 1

    result.backup_path = backup_path
    print(f"  ✓  Backup created: {backup_path} ({result.files_backed_up} file(s))")
    return result


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

    # Collect the set of relative paths present in the backup
    backup_rels: set[Path] = set()
    for src in backup_path.rglob("*"):
        if not src.is_file():
            continue
        backup_rels.add(src.relative_to(backup_path))

    # Restore all backed-up files
    count = 0
    for rel in backup_rels:
        src = backup_path / rel
        dest = output_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
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


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def emit_all(
    rendered_files: list[tuple[str, str]],
    *,
    output_dir: Path,
    dry_run: bool = False,
    overwrite: bool = False,
    merge: bool = False,
    yes: bool = False,
) -> EmitResult:
    """Write rendered files to output_dir.

    Args:
        rendered_files: List of (relative_output_path, content) from render_all().
        output_dir:     Absolute path to the agents output directory.
        dry_run:        If True, print actions without writing any files.
        overwrite:      If True, overwrite existing files unconditionally.
                        Mutually exclusive with *merge*.
        merge:          If True, merge template-fenced regions into existing files
                        while preserving all user-authored content outside fences.
                        Mutually exclusive with *overwrite*.
        yes:            If True, answer 'yes' to all interactive prompts.

    Returns:
        EmitResult with results of all write operations.

    Raises:
        ValueError: If both *overwrite* and *merge* are True.
    """
    if overwrite and merge:
        raise ValueError("overwrite and merge are mutually exclusive")

    result = EmitResult(dry_run=dry_run)

    # Check for existing files before writing anything (only relevant for
    # overwrite path — merge handles its own existence check per-file)
    if not merge:
        existing: list[Path] = []
        for rel_path, _ in rendered_files:
            target = _resolve_path(output_dir, rel_path)
            if target.exists():
                existing.append(target)

        if existing and not overwrite and not dry_run:
            if not yes:
                print(f"\n{len(existing)} file(s) already exist:")
                for p in existing[:10]:
                    print(f"  {p}")
                if len(existing) > 10:
                    print(f"  ... and {len(existing) - 10} more")
                if sys.stdin.isatty():
                    try:
                        answer = input("\nOverwrite existing files? [y/N] ").strip().lower()
                    except EOFError:
                        answer = "n"
                else:
                    answer = "n"
                if answer != "y":
                    result.errors.append("Aborted: user declined to overwrite existing files")
                    return result
            overwrite = True

    # Write files
    for rel_path, content in rendered_files:
        target = _resolve_path(output_dir, rel_path)
        normalized_content = _normalize_generated_content(rel_path, content)

        if dry_run:
            if merge and target.exists():
                action = "MERGE"
            else:
                action = "OVERWRITE" if target.exists() else "WRITE"
            print(f"[DRY RUN] {action} {target}")
            result.written.append(str(target))
            continue

        # Merge path
        if merge and target.exists():
            existing_text = target.read_text(encoding="utf-8")
            merge_result = _merge_fenced_content(normalized_content, existing_text)
            if merge_result.has_errors:
                legacy_no_fence = all(
                    "No fence markers detected" in err for err in merge_result.parse_errors
                )
                if legacy_no_fence and _is_machine_managed_merge_overwrite_path(rel_path):
                    if existing_text == normalized_content:
                        result.unchanged.append(str(target))
                    else:
                        try:
                            target.write_text(normalized_content, encoding="utf-8")
                            result.merged.append(str(target))
                        except OSError as exc:
                            result.errors.append(f"Failed to write {target}: {exc}")
                else:
                    for err in merge_result.parse_errors:
                        print(f"  ⚠  Merge skipped ({target.name}): {err}", file=sys.stderr)
                    result.skipped.append(str(target))
            elif not merge_result.content_changed and not merge_result.sections_added:
                result.unchanged.append(str(target))
            else:
                try:
                    target.write_text(merge_result.merged_content, encoding="utf-8")
                    result.merged.append(str(target))
                    if merge_result.sections_orphaned:
                        print(
                            f"  ⚠  {target.name}: {len(merge_result.sections_orphaned)} "
                            f"orphaned section(s) left in place: "
                            f"{', '.join(merge_result.sections_orphaned)}",
                            file=sys.stderr,
                        )
                except OSError as exc:
                    result.errors.append(f"Failed to write {target}: {exc}")
            continue

        if target.exists() and not overwrite:
            result.skipped.append(str(target))
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(normalized_content, encoding="utf-8")
            result.written.append(str(target))
        except OSError as exc:
            result.errors.append(f"Failed to write {target}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary(result: EmitResult, manifest: dict[str, Any]) -> None:
    """Print a human-readable summary of the emit operation."""
    project = manifest.get("project_name", "")
    framework = manifest.get("framework", "")

    if result.dry_run:
        print(f"\n[DRY RUN] Would generate {len(result.written)} file(s) for {project!r} ({framework})")
    else:
        print(f"\nAgent team generated for {project!r} ({framework})")
        print(f"  Written:  {len(result.written)} file(s)")
        if result.merged:
            print(f"  Merged:   {len(result.merged)} file(s) (template regions updated, user content preserved)")
        if result.unchanged:
            print(f"  Unchanged:{len(result.unchanged):>4} file(s) already matched rendered fenced content")
        if result.skipped:
            print(f"  Skipped:  {len(result.skipped)} (use --overwrite to replace, or --merge for fenced files)")
        if result.errors:
            print(f"  Errors:   {len(result.errors)}", file=sys.stderr)

    manual_count = len(manifest.get("manual_required_placeholders", []))
    if manual_count > 0:
        print(f"\n  ⚠  {manual_count} placeholder(s) require manual completion.")
        print("     Review SETUP-REQUIRED.md in the output directory.")

    warnings = manifest.get("_cross_ref_warnings", [])
    if warnings:
        print(f"\n  ⚠  {len(warnings)} cross-reference warning(s):")
        for w in warnings[:5]:
            print(f"     {w}")
        if len(warnings) > 5:
            print(f"     ... and {len(warnings) - 5} more (see above)")

    if result.errors:
        print("\nErrors:", file=sys.stderr)
        for err in result.errors:
            print(f"  {err}", file=sys.stderr)

    if not result.dry_run and result.success:
        print("\nNext steps:")
        step = 1
        if manual_count > 0:
            print("  1. Review SETUP-REQUIRED.md and fill in manual placeholders")
            step += 1
        print(f"  {step}. Open VS Code in the project directory")
        print(f"  {step + 1}. Invoke @orchestrator to begin production")
        if framework == "copilot-vscode":
            print(f"  {step + 2}. Or invoke @team-builder to regenerate or expand the team")
            final_step = step + 3
        else:
            final_step = step + 2
        print(f"  {final_step}. Re-run with --post-audit to verify team consistency,")
        print("     add --auto-correct to let standalone `copilot` repair findings,")
        print("     or invoke @conflict-auditor and @adversarial in VS Code")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_path(output_dir: Path, rel_path: str) -> Path:
    """Resolve a relative path that may start with '../'."""
    # Paths like '../copilot-instructions.md' should go one level above agents dir
    clean = Path(rel_path)
    return (output_dir / clean).resolve()


def file_hash(path: Path) -> str:
    """Return the first 8 characters of the SHA-256 hash of a file."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:8]
