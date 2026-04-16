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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class EmitResult:
    written: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
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
            merge_result = _merge_fenced_content(content, existing_text)
            if merge_result.has_errors:
                for err in merge_result.parse_errors:
                    print(f"  ⚠  Merge skipped ({target.name}): {err}", file=sys.stderr)
                result.skipped.append(str(target))
            elif not merge_result.content_changed and not merge_result.sections_added:
                result.skipped.append(str(target))
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
            target.write_text(content, encoding="utf-8")
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
