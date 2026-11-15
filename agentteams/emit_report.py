"""emit_report.py — the run's printed reports: the dry-run report and the generation summary.

Carved from ``emit.py`` (CH-07 line ceiling, agentteams #508); ``emit`` re-exports :func:`print_dry_run_report`,
:func:`print_summary` and ``_dry_run_counts`` so existing callers keep working.

Stdlib only.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # type names only: emit imports this module
    from agentteams.emit import DryRunReport, EmitResult


def print_dry_run_report(
    result: EmitResult,
    manifest: dict[str, Any],
    *,
    fmt: str = "text",
    stream: Any = None,
) -> None:
    """Print the structured dry-run plan (Plan 1).

    ``fmt='text'`` prints a per-file action table + aggregated counts +
    notices; ``fmt='json'`` prints a single JSON document to stdout suitable
    for ``jq`` piping. No-op (with a one-line note) if ``result.dry_run_report``
    is None.

    Args:
        result: The emit result carrying ``dry_run_report``.
        manifest: The team manifest (supplies project name and framework).
        fmt: ``"text"`` or ``"json"``.
        stream: Where to write. Defaults to ``sys.stdout``. JSON mode passes the *real* stdout
            explicitly, because the caller has redirected ``sys.stdout`` to stderr so that
            progress narration cannot corrupt the JSON document — see
            :func:`agentteams.cli.generate.run_generate`.
    """
    out = stream if stream is not None else sys.stdout
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
        if report.adopt_orphans_merge is not None:
            payload["adopt_orphans_merge"] = report.adopt_orphans_merge
        print(_json.dumps(payload, indent=2), file=out)
        return

    counts = _dry_run_counts(report)
    print(f"\n[DRY RUN PLAN] {project!r} ({framework}) — no files written", file=out)
    for entry in report.entries:
        delta = f" ({entry.delta_bytes:+d} bytes)" if entry.delta_bytes else ""
        print(f"  {entry.action:24s} {entry.path}{delta}", file=out)
        for fa in entry.fence_actions:
            print(f"      └─ fence:{fa['fence_id']:30s} {fa['action']}", file=out)
    print("\n  Plan counts:", file=out)
    for action, n in sorted(counts.items()):
        print(f"    {action:24s} {n}", file=out)
    if report.notices:
        print(f"\n  Notices ({len(report.notices)}):", file=out)
        for note in report.notices:
            print(f"    Notice: {note}", file=out)


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
        from agentteams.front_matter_merge import print_merge_notices

        print_merge_notices(result.notices)

    from agentteams.agent_runtime import print_report  # #508: name every agent_runtime value not written

    print_report(manifest, framework)

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
