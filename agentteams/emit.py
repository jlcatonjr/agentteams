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
class DryRunEntry:
    """One per-file row in the dry-run preview (Plan 1).

    ``action`` is one of: WRITE, OVERWRITE, MERGE, MERGE-OVERWRITE-FENCED,
    UNCHANGED, SKIP. ``fence_actions`` is a list of {fence_id, action,
    delta_bytes} dicts populated for MERGE/MERGE-OVERWRITE-FENCED rows;
    Plan 3 (shrink-notice) consumes the same per-fence info.
    """
    path: str
    action: str
    fence_actions: list[dict[str, Any]] = field(default_factory=list)
    delta_bytes: int = 0


@dataclass
class DryRunReport:
    """Structured preview of what an ``--update`` / generate would write.

    Reporter is an explicit *extension point* — Plan 3 appends shrink notices
    into ``notices`` without forking the dry-run logic. Set by ``emit_all``
    when ``dry_run=True``; ``None`` on real runs.
    """
    entries: list[DryRunEntry] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


@dataclass
class EmitResult:
    written: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False
    dry_run_report: DryRunReport | None = None
    # Plan 3: notices surfaced from any run (real or dry); aggregated and
    # printed once by build_team at end of run.
    notices: list[str] = field(default_factory=list)
    # legacy-skip visibility: subset of `skipped` containing files that were
    # skipped because they had no fence markers (unfenced legacy files).
    # Template updates targeting these files were NOT applied. Parallel list
    # `skipped_legacy_drift` flags whether the rendered content actually differs
    # from on-disk (True = template change was lost; False = harmless skip).
    skipped_legacy: list[str] = field(default_factory=list)
    skipped_legacy_drift: list[bool] = field(default_factory=list)
    # Auto-fence-on-update: legacy files that were (or, in dry-run, would be)
    # retrofitted with a `content` fence so their template region became
    # mergeable this run instead of being skipped.
    fence_injected: list[str] = field(default_factory=list)
    # T2.D5: paths whose merge was skipped because shrink_policy="halt"
    # detected a destructive shrink. Distinct from `skipped` (overwrite
    # declined) and `errors` (true failures) — these are intentional
    # non-writes that the operator can review.
    shrink_blocked: list[str] = field(default_factory=list)

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
        sections_preserved: section_ids whose new render would have shrunk the
                            existing body, kept unchanged under shrink_policy
                            "preserve" (respectful update — no content lost).
        parse_errors:       Human-readable messages for parse failures.
        unchanged:          section_ids that were identical in both files (no write needed).
        merged_content:     The final merged file content (empty string on parse failure).
        shrink_notices:     Per-section human-readable Notices (Plan 3) when a
                            regenerated fence body is materially shorter / less
                            specific than the existing on-disk body.
    """
    sections_replaced: list[str] = field(default_factory=list)
    sections_added: list[str] = field(default_factory=list)
    sections_orphaned: list[str] = field(default_factory=list)
    sections_preserved: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    merged_content: str = ""
    shrink_notices: list[str] = field(default_factory=list)
    # W22 data-loss recovery: full pre-merge body of every fence that fired
    # a shrink notice, keyed by section_id. Persisted as a .lost.<sid>.md
    # sidecar inside the backup dir by emit_all when backup_path is provided.
    lost_fence_bodies: dict[str, str] = field(default_factory=dict)

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

# Fences whose body is refreshed each run from an upstream live feed
# (CISA KEV, NVD CVSS, OSV.dev, etc.). Content "loss" in these fences reflects
# normal feed rotation, not user-content deletion — suppress the shrink-warn
# heuristic so structural --update runs don't emit alarming false positives.
# The canonical history for these feeds is the cache JSON, not the embedded
# snapshot. Real user content sits in adjacent operator-managed fences.
_LIVE_DATA_FENCES: frozenset[str] = frozenset([
    "threat_intelligence",
    "threat_data",
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


_PROJECT_NOTES_HEADING = "## Project-Specific Notes"
_PROJECT_NOTES_SECTION = (
    "\n"
    "## Project-Specific Notes\n"
    "\n"
    "> ⚙️ **USER-EDITABLE** — project-specific rules, overrides, and extensions "
    "for this agent. This section lies outside every `AGENTTEAMS` fence and is "
    "preserved verbatim across `agentteams --update --merge`.\n"
)


def _is_agent_doc(rel_path: str, content: str) -> bool:
    """Return True when rel_path/content is a generated agent persona document.

    Agent personas carry YAML front matter; reference files, instruction files,
    and SETUP-REQUIRED.md do not and are excluded.
    """
    if not rel_path.endswith(".md"):
        return False
    base = rel_path.rsplit("/", 1)[-1]
    if "references/" in rel_path:
        return False
    # Skills (operational tool docs) carry front matter but are not agent
    # personas — they must not get the "Project-Specific Notes" persona section.
    if rel_path.startswith("../skills/") or "/skills/" in rel_path:
        return False
    if base in {"copilot-instructions.md", "CLAUDE.md", "SETUP-REQUIRED.md"}:
        return False
    return bool(_YAML_FM_RE.match(content))


def _ensure_project_notes_section(rel_path: str, content: str) -> str:
    """Append the USER-EDITABLE 'Project-Specific Notes' section if absent.

    Pure append: existing content — including project-authored orphan fences
    and hand edits outside the templated structure — is never rewritten, only
    extended. Idempotent: a file that already carries the section is returned
    unchanged. Applied to merged output as well as fresh renders, so existing
    fleet files gain the section on ``--update --merge`` (migration path b).
    """
    if not _is_agent_doc(rel_path, content):
        return content
    if _PROJECT_NOTES_HEADING in content:
        return content
    if not content.endswith("\n"):
        content += "\n"
    return content + _PROJECT_NOTES_SECTION


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s|\d+\.\s)", re.MULTILINE)
# Concrete file paths (foo/bar.py, foo.md) or backtick-quoted identifiers.
_PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:py|md|json|yaml|yml|toml|csv|tsv|sql|sh)\b")
_BACKTICK_IDENT_RE = re.compile(r"`([^`\n]+)`")


def _fence_body(block: str) -> str:
    """Strip the BEGIN/END marker lines from a fenced block — returns body only."""
    lines = block.splitlines(keepends=True)
    if not lines:
        return ""
    body = lines[1:-1] if len(lines) >= 2 else lines
    return "".join(body)


def _detect_fence_shrink(sid: str, existing_block: str, new_block: str) -> str | None:
    """Plan 3: return a Notice string when the new fence body is materially
    shorter or less specific than the existing body (rules a/b/c), else None.

    Rules (any one triggers):
      (a) new body length < 50% of existing body length;
      (b) new body has >= 3 fewer markdown list items than existing;
      (c) existing body contained concrete file paths or backtick-quoted
          identifiers that the new body does not.

    Live-feed fences (`_LIVE_DATA_FENCES`) are exempt: their bodies are
    refreshed each run from upstream feeds and rotation is expected. The
    sidecar mechanism in `lost_fence_bodies` still preserves the prior body
    on disk if real recovery is ever needed.
    """
    if sid in _LIVE_DATA_FENCES:
        return None
    existing = _fence_body(existing_block)
    new = _fence_body(new_block)
    if not existing.strip():
        return None  # nothing to shrink from
    ex_len, new_len = len(existing), len(new)
    if ex_len == 0:
        return None

    reasons: list[str] = []
    # (a) length shrink > 50%
    if ex_len > 0 and new_len < ex_len / 2:
        reasons.append(
            f"body shrank {ex_len}->{new_len} bytes (>{50}% reduction)"
        )
    # (b) list-item delta >= 3
    ex_items = len(_LIST_ITEM_RE.findall(existing))
    new_items = len(_LIST_ITEM_RE.findall(new))
    if ex_items - new_items >= 3:
        reasons.append(f"lost {ex_items - new_items} list item(s) ({ex_items}->{new_items})")
    # (c) lost concrete paths / backtick identifiers
    ex_paths = set(_PATH_RE.findall(existing)) | set(_BACKTICK_IDENT_RE.findall(existing))
    new_paths = set(_PATH_RE.findall(new)) | set(_BACKTICK_IDENT_RE.findall(new))
    lost = ex_paths - new_paths
    if lost:
        sample = sorted(lost)[:3]
        more = f" (+{len(lost) - 3} more)" if len(lost) > 3 else ""
        reasons.append(f"lost concrete refs: {', '.join(sample)}{more}")

    if not reasons:
        return None
    return f"fence '{sid}': " + "; ".join(reasons)


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


def _merge_fenced_content(
    new_rendered: str,
    existing_on_disk: str,
    preserve_on_shrink: bool = False,
) -> MergeResult:
    """Merge fenced sections from *new_rendered* into *existing_on_disk*.

    Template-owned (fenced) regions in the existing file are replaced with
    the corresponding regions from the new render.  All content outside any
    fence marker is preserved unchanged.

    Args:
        new_rendered:     Fully rendered file content from the render phase.
        existing_on_disk: Current content of the on-disk file.
        preserve_on_shrink: When True (shrink_policy="preserve"), a fence whose
            new render would materially shrink the existing body is left
            unchanged instead of being replaced — the richer enriched body is
            kept and recorded in ``sections_preserved``. Non-shrinking fences
            still receive their template updates. This is the respectful,
            non-destructive update path: no content is lost and no whole-file
            write is blocked.

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
                if new_regions[sid] == existing_regions.get(sid, ""):
                    output_lines.append(new_regions[sid])
                    result.unchanged.append(sid)
                else:
                    # Plan 3: detect material shrink and queue a Notice.
                    notice = _detect_fence_shrink(
                        sid, existing_regions.get(sid, ""), new_regions[sid]
                    )
                    if notice and preserve_on_shrink:
                        # Respectful update: the new render would drop enriched
                        # content. Keep the existing body verbatim; surface a
                        # notice so the suppression is visible. No data lost, so
                        # no .lost.<sid>.md sidecar is needed.
                        output_lines.append(existing_regions[sid])
                        result.sections_preserved.append(sid)
                        result.shrink_notices.append(notice)
                    else:
                        output_lines.append(new_regions[sid])
                        result.sections_replaced.append(sid)
                        if notice:
                            result.shrink_notices.append(notice)
                            # W22 data-loss recovery: capture the pre-merge body
                            # so emit_all can write a .lost.<sid>.md sidecar.
                            result.lost_fence_bodies[sid] = _fence_body(
                                existing_regions.get(sid, "")
                            )
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
    out.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
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

# W22 data-loss recovery -----------------------------------------------------

_SHRINK_NOTICE_SID_RE = re.compile(r"^fence '([^']+)':")


def _shrink_notice_sid(notice: str) -> str | None:
    """Extract the fence section_id from a shrink Notice string."""
    m = _SHRINK_NOTICE_SID_RE.match(notice)
    return m.group(1) if m else None


def _write_lost_fence_sidecars(
    backup_path: Path,
    rel_path: str,
    lost_bodies: dict[str, str],
) -> dict[str, str]:
    """Persist each lost fence body as ``<backup>/<rel_path>.lost.<sid>.md``.

    Returns a mapping of section_id → sidecar path (string, relative to repo
    root when possible, else absolute) so the caller can annotate Notices.
    Failures are non-fatal: the function returns whatever sidecars were
    written and skips the rest.
    """
    written: dict[str, str] = {}
    if not lost_bodies:
        return written
    try:
        backup_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return written
    for sid, body in lost_bodies.items():
        if not body.strip():
            continue
        # Flatten the rel_path into the filename so sibling files never collide:
        # references/foo.md + sid=content → references/foo.md.lost.content.md
        sidecar = backup_path / f"{rel_path}.lost.{sid}.md"
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(body, encoding="utf-8")
        except OSError:
            continue
        try:
            written[sid] = str(sidecar.relative_to(Path.cwd()))
        except ValueError:
            written[sid] = str(sidecar)
    return written


def emit_all(
    rendered_files: list[tuple[str, str]],
    *,
    output_dir: Path,
    dry_run: bool = False,
    overwrite: bool = False,
    merge: bool = False,
    yes: bool = False,
    shrink_policy: str = "preserve",
    backup_path: Path | None = None,
    auto_fence_legacy: bool = False,
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
        shrink_policy:  Policy for fenced-region shrinks during merge.
                        "preserve" (default): keep the existing enriched body
                                for any fence the new render would shrink; still
                                apply template updates to non-shrinking fences.
                                Respectful, non-destructive — no content lost,
                                no whole-file block.
                        "warn": log notice, write smaller content (recoverable
                                via .lost.<sid>.md sidecar).
                        "halt": log notice, SKIP write, append to
                                result.shrink_blocked.
                        "allow": suppress notice, write smaller content.
                        Plan: references/plans/T2-D5-shrink-policy-2026-05-25.plan.md
        backup_path:    If provided, write per-fence ``.lost.<sid>.md`` sidecars
                        inside this directory whenever a merge fires a shrink
                        notice — capturing the full pre-merge fence body so the
                        operator can recover dropped hand-edits even under the
                        default ``warn`` policy. (W22 data-loss recovery.)

    Returns:
        EmitResult with results of all write operations.

    Raises:
        ValueError: If both *overwrite* and *merge* are True.
    """
    if overwrite and merge:
        raise ValueError("overwrite and merge are mutually exclusive")

    result = EmitResult(
        dry_run=dry_run,
        dry_run_report=DryRunReport() if dry_run else None,
    )

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
        normalized_content = _ensure_project_notes_section(rel_path, normalized_content)

        # Auto-fence-on-update: retrofit a `content` fence onto an eligible
        # legacy (unfenced) file so its template region becomes mergeable this
        # run instead of being skipped. Default-on at the CLI (build_team passes
        # auto_fence_legacy=True unless --no-add-fence-markers); --yes-gated;
        # never applied to machine-managed overwrite-fenced paths. Content-safe:
        # the pre-injection backup retains the original legacy body (recoverable),
        # and the shrink-guard below still suppresses material template shrinks.
        auto_fenced_now = False
        _auto_wrapped: str | None = None
        if (
            merge
            and auto_fence_legacy
            and yes
            and target.exists()
            and not _is_machine_managed_merge_overwrite_path(rel_path)
        ):
            try:
                _pre_text = target.read_text(encoding="utf-8")
            except OSError:
                _pre_text = None
            if _pre_text is not None and not _FENCE_BEGIN_RE.search(_pre_text):
                from agentteams.fence_inject import _unique_fence_id, _wrap_body
                _auto_wrapped = _wrap_body(_pre_text, _unique_fence_id(_pre_text))
                auto_fenced_now = True
                if dry_run:
                    result.fence_injected.append(f"{target} (dry-run)")
                else:
                    if backup_path is not None:
                        backup_path.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(target, backup_path / target.name)
                    target.write_text(_auto_wrapped, encoding="utf-8")
                    result.fence_injected.append(str(target))

        if dry_run:
            # Plan 1: compute the action accurately (mirror the real write
            # path's classification) and record a structured DryRunEntry.
            # Per-file action precedence matches the write branches below.
            entry = DryRunEntry(path=str(target), action="WRITE")
            existing_text: str | None = None
            try:
                if target.exists():
                    existing_text = target.read_text(encoding="utf-8")
            except OSError:
                existing_text = None
            # Dry-run fidelity: if the real run would auto-fence this legacy file,
            # merge against the (in-memory) wrapped body so the reported action
            # matches the live run instead of showing a legacy SKIP.
            if auto_fenced_now and _auto_wrapped is not None:
                existing_text = _auto_wrapped

            if merge and existing_text is not None:
                mr = _merge_fenced_content(
                    normalized_content,
                    existing_text,
                    preserve_on_shrink=(shrink_policy == "preserve"),
                )
                # Plan 3: dry-run preview also surfaces the notices that the
                # real run would emit (D-4 from update-dry-run plan).
                # Annotate so operators understand what the real run will do
                # with each shrink — preserve in place, or sidecar+write.
                for notice in mr.shrink_notices:
                    if shrink_policy == "preserve":
                        suffix = " (existing enriched body will be retained; template update suppressed for this fence — use --shrink-policy=allow to force)"
                    else:
                        suffix = " (prior body will be preserved in a .lost.<sid>.md sidecar in the backup dir on the real run)"
                    annotated = f"{rel_path}: {notice}{suffix}"
                    result.notices.append(annotated)
                    if result.dry_run_report is not None:
                        result.dry_run_report.notices.append(annotated)
                # T5.1 / IV.1: in dry-run, also pre-flight the halt decision
                # so operators see what a real --shrink-policy=halt run would
                # block, without actually modifying any file.
                if (
                    mr.shrink_notices
                    and shrink_policy == "halt"
                    and result.dry_run_report is not None
                ):
                    result.shrink_blocked.append(str(target))
                if mr.has_errors:
                    legacy_no_fence = all(
                        "No fence markers detected" in e for e in mr.parse_errors
                    )
                    if legacy_no_fence and _is_machine_managed_merge_overwrite_path(rel_path):
                        if existing_text == normalized_content:
                            entry.action = "UNCHANGED"
                        else:
                            entry.action = "MERGE-OVERWRITE-FENCED"
                            entry.delta_bytes = len(normalized_content) - len(existing_text)
                    else:
                        entry.action = "SKIP"
                        if legacy_no_fence:
                            result.skipped_legacy.append(str(target))
                            result.skipped_legacy_drift.append(
                                existing_text != normalized_content
                            )
                else:
                    migrated = _ensure_project_notes_section(rel_path, mr.merged_content)
                    if (
                        not mr.content_changed
                        and not mr.sections_added
                        and migrated == existing_text
                    ):
                        entry.action = "UNCHANGED"
                    else:
                        entry.action = "MERGE"
                        entry.delta_bytes = len(migrated) - len(existing_text)
                        for sid in mr.sections_replaced:
                            entry.fence_actions.append({"fence_id": sid, "action": "replaced"})
                        for sid in mr.sections_added:
                            entry.fence_actions.append({"fence_id": sid, "action": "added"})
                        for sid in mr.sections_orphaned:
                            entry.fence_actions.append({"fence_id": sid, "action": "orphaned"})
                        for sid in mr.sections_preserved:
                            entry.fence_actions.append({"fence_id": sid, "action": "preserved"})
            elif existing_text is not None and not overwrite:
                entry.action = "SKIP"
            elif existing_text is not None and overwrite:
                if existing_text == normalized_content:
                    entry.action = "UNCHANGED"
                else:
                    entry.action = "OVERWRITE"
                    entry.delta_bytes = len(normalized_content) - len(existing_text)
            else:
                entry.action = "WRITE"
                entry.delta_bytes = len(normalized_content)

            assert result.dry_run_report is not None  # set above
            result.dry_run_report.entries.append(entry)
            # Back-compat: keep the human-readable per-file line + the
            # `written` count current callers expect.
            print(f"[DRY RUN] {entry.action} {target}")
            result.written.append(str(target))
            continue

        # Merge path
        if merge and target.exists():
            existing_text = target.read_text(encoding="utf-8")
            merge_result = _merge_fenced_content(
                normalized_content,
                existing_text,
                preserve_on_shrink=(shrink_policy == "preserve"),
            )
            # Plan 3: surface shrink Notices from this merge (real-run path).
            # T2.D5: shrink_policy controls whether to surface and whether
            # to write the smaller content.
            if merge_result.shrink_notices and shrink_policy == "preserve":
                # Respectful update: the enriched body was kept in place; no
                # content was lost, so no sidecar is needed. Surface a notice so
                # the suppressed template update is visible to the operator.
                for notice in merge_result.shrink_notices:
                    result.notices.append(
                        f"{rel_path}: {notice} — retained existing enriched "
                        f"body (template update suppressed; use "
                        f"--shrink-policy=allow to force)"
                    )
            elif merge_result.shrink_notices and shrink_policy != "allow":
                # W22 data-loss recovery: persist each lost fence body to a
                # sidecar in the backup dir so the operator can recover from
                # silent shrinks even under default "warn".
                sidecar_paths: dict[str, str] = {}
                if backup_path is not None and merge_result.lost_fence_bodies:
                    sidecar_paths = _write_lost_fence_sidecars(
                        backup_path, rel_path, merge_result.lost_fence_bodies,
                    )
                for notice in merge_result.shrink_notices:
                    sid = _shrink_notice_sid(notice)
                    sidecar = sidecar_paths.get(sid) if sid else None
                    line = f"{rel_path}: {notice}"
                    if sidecar:
                        line += f" — recovery: {sidecar}"
                    result.notices.append(line)
            if merge_result.shrink_notices and shrink_policy == "halt":
                # Skip the write entirely; record the path for operator review.
                result.shrink_blocked.append(str(target))
                print(
                    f"  ⛔  shrink-halt: refused to write {rel_path} "
                    f"({len(merge_result.shrink_notices)} shrink notice(s)). "
                    f"Re-run with --shrink-policy=warn or allow to override.",
                    file=sys.stderr,
                )
                continue
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
                    if legacy_no_fence:
                        result.skipped_legacy.append(str(target))
                        result.skipped_legacy_drift.append(
                            existing_text != normalized_content
                        )
            else:
                migrated = _ensure_project_notes_section(
                    rel_path, merge_result.merged_content
                )
                if (
                    not merge_result.content_changed
                    and not merge_result.sections_added
                    and migrated == existing_text
                ):
                    result.unchanged.append(str(target))
                else:
                    try:
                        target.write_text(migrated, encoding="utf-8")
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
            if target.exists() and target.read_text(encoding="utf-8") == normalized_content:
                result.unchanged.append(str(target))
            else:
                target.write_text(normalized_content, encoding="utf-8")
                result.written.append(str(target))
        except OSError as exc:
            result.errors.append(f"Failed to write {target}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_dry_run_report(
    result: EmitResult,
    manifest: dict[str, Any],
    *,
    fmt: str = "text",
) -> None:
    """Print the structured dry-run plan (Plan 1).

    ``fmt='text'`` prints a per-file action table + aggregated counts +
    notices; ``fmt='json'`` prints a single JSON document to stdout suitable
    for ``jq`` piping. No-op (with a one-line note) if ``result.dry_run_report``
    is None.
    """
    import json as _json
    report = result.dry_run_report
    if report is None:
        return
    project = manifest.get("project_name", "")
    framework = manifest.get("framework", "")

    if fmt == "json":
        payload = {
            "project_name": project,
            "framework": framework,
            "entries": [
                {
                    "path": e.path,
                    "action": e.action,
                    "delta_bytes": e.delta_bytes,
                    "fence_actions": e.fence_actions,
                }
                for e in report.entries
            ],
            "notices": list(report.notices),
            "counts": _dry_run_counts(report),
        }
        print(_json.dumps(payload, indent=2))
        return

    counts = _dry_run_counts(report)
    print(f"\n[DRY RUN PLAN] {project!r} ({framework}) — no files written")
    for entry in report.entries:
        delta = f" ({entry.delta_bytes:+d} bytes)" if entry.delta_bytes else ""
        print(f"  {entry.action:24s} {entry.path}{delta}")
        for fa in entry.fence_actions:
            print(f"      └─ fence:{fa['fence_id']:30s} {fa['action']}")
    print("\n  Plan counts:")
    for action, n in sorted(counts.items()):
        print(f"    {action:24s} {n}")
    if report.notices:
        print(f"\n  Notices ({len(report.notices)}):")
        for note in report.notices:
            print(f"    Notice: {note}")


def _dry_run_counts(report: DryRunReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in report.entries:
        counts[e.action] = counts.get(e.action, 0) + 1
    return counts


def print_summary(result: EmitResult, manifest: dict[str, Any]) -> None:
    """Print a human-readable summary of the emit operation."""
    project = manifest.get("project_name", "")
    framework = manifest.get("framework", "")

    if result.dry_run:
        print(f"\n[DRY RUN] Would generate {len(result.written)} file(s) for {project!r} ({framework})")
    else:
        print(f"\nAgent team generated for {project!r} ({framework})")
        print(f"  Written:  {len(result.written)} file(s)")
        if result.fence_injected:
            print(
                f"  Fence-retrofitted: {len(result.fence_injected)} legacy file(s) "
                f"(AGENTTEAMS content fence added so template regions merge; "
                f"originals backed up)"
            )
        if result.merged:
            print(f"  Merged:   {len(result.merged)} file(s) (template regions updated, user content preserved)")
        if result.unchanged:
            print(f"  Unchanged:{len(result.unchanged):>4} file(s) already matched rendered fenced content")
        if result.skipped:
            print(f"  Skipped:  {len(result.skipped)} (use --overwrite to replace, or --merge for fenced files)")
        if result.errors:
            print(f"  Errors:   {len(result.errors)}", file=sys.stderr)

    # Legacy-skip visibility: when --merge skipped one or more files because
    # they lacked fence markers, the template updates targeting those files
    # were NOT applied. Surface this explicitly with the retrofit options,
    # because the per-file inline warnings get lost in long outputs.
    if result.skipped_legacy:
        n = len(result.skipped_legacy)
        pending = sum(1 for d in result.skipped_legacy_drift if d)
        suffix = f" ({pending} with template change pending)" if pending else ""
        print(
            f"\n  ⚠  Legacy files skipped — template updates NOT applied: {n}{suffix}",
            file=sys.stderr,
        )
        for path, drift in zip(result.skipped_legacy, result.skipped_legacy_drift):
            marker = "  (template change pending)" if drift else ""
            print(f"       {path}{marker}", file=sys.stderr)
        print(
            "     Retrofit options (one-step --migrate is recommended):\n"
            "       agentteams ... --migrate             # tag 'pre-fencing-snapshot' then fence all files; reversible via --revert-migration\n"
            "       agentteams --add-fence-markers <path> [--in-place]   # non-destructive, one file at a time\n"
            "       agentteams ... --overwrite           # replace unconditionally (loses local edits)",
            file=sys.stderr,
        )

    # Plan 3: aggregated shrink Notices, printed once to stderr after the
    # summary. Real-run channel; dry-run notices are folded into the dry-run
    # report instead (so the JSON payload carries them).
    if result.notices and not result.dry_run:
        print(f"\n  Notice: {len(result.notices)} fenced region(s) shrank during merge:", file=sys.stderr)
        for note in result.notices:
            print(f"    Notice: {note}", file=sys.stderr)
        print(
            "     Review whether the source description needs to be expanded "
            "before re-running, or use --overwrite if the shrink is intended.",
            file=sys.stderr,
        )

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
