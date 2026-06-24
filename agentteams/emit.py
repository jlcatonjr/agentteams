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
from pathlib import Path
from typing import Any

# Atomic primitives extracted to agentteams/atomicio.py (CH-07); imported here
# for emit_all + re-exported so emit._atomic_write_text / emit._resolve_path stay stable.
from agentteams.atomicio import (  # noqa: F401 — re-exported for callers/tests
    _atomic_copy,
    _atomic_write_text,
    _resolve_path,
    _target_mode,
)

from agentteams.fences import (  # noqa: E402,F401  (carved for CH-07; re-exported)
    MergeResult,
    _BRIDGE_FENCE_BEGIN_RE,
    _FENCE_BEGIN_RE,
    _FENCE_END_RE,
    _LIVE_DATA_FENCES,
    _MACHINE_MANAGED_MERGE_OVERWRITE_PATHS,
    _SHRINK_NOTICE_SID_RE,
    _YAML_FM_RE,
    _detect_fence_shrink,
    _extract_fenced_regions,
    _fence_body,
    _is_machine_managed_merge_overwrite_path,
    _merge_fenced_content,
    _shrink_notice_sid,
    _write_lost_fence_sidecars,
)
# Backup subsystem extracted to agentteams/backup.py (CH-07); re-exported so
# cli/, build_team, drift, and tests resolve emit.<symbol> unchanged.
from agentteams.backup import (  # noqa: F401 — re-exported for callers/tests
    BACKUP_MANIFEST_NAME,
    BACKUP_MANIFEST_SCHEMA_VERSION,
    BackupResult,
    DEFAULT_BACKUP_KEEP_LAST,
    PruneResult,
    backup_output_dir,
    list_backups,
    prune_backups,
    restore_backup,
    verify_backup,
    _backup_rel,
    _mirror_backup,
    _parse_backup_timestamp,
    _restore_dest,
    _write_backup_manifest,
)


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
    if base in {"copilot-instructions.md", "CLAUDE.md", "AGENTS.md", "SETUP-REQUIRED.md"}:
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


# Concrete file paths (foo/bar.py, foo.md) or backtick-quoted identifiers.


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
                    _atomic_write_text(target, _auto_wrapped)
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
                            _atomic_write_text(target, normalized_content)
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
                        _atomic_write_text(target, migrated)
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
                _atomic_write_text(target, normalized_content)
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


def file_hash(path: Path) -> str:
    """Return the first 8 characters of the SHA-256 hash of a file."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:8]
