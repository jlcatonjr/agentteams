#!/usr/bin/env python3
"""
build_team.py — CLI entry point for the Agent Teams Module.

Usage:
    python build_team.py --description brief.md --project /path/to/project
    python build_team.py --description brief.json --framework copilot-cli --dry-run
    python build_team.py --description brief.json --output /custom/output/dir
    python build_team.py --description brief.json --check
    python build_team.py --description brief.json --update
    python build_team.py --description brief.json --post-audit --auto-correct

Options:
    --description  PATH   Project description file (.json or .md) [required]
    --project      PATH   Existing project directory to scan (overrides existing_project_path in description)
    --framework    NAME   Target framework: copilot-vscode (default), copilot-cli, claude
    --output       DIR    Output directory for agent files (default: framework-specific agents
                          directory under <project>: .github/agents/ for copilot-vscode,
                          .github/copilot/ for copilot-cli, .claude/agents/ for claude)
    --dry-run             Show what would be generated without writing files
    --overwrite           Overwrite existing agent files without prompting
    --yes                 Non-interactive: answer yes to all prompts
    --no-scan             Disable project directory scanning
    --update              Re-render drifted files and emit new agents added to the taxonomy.
                          Preserves manually-filled {MANUAL:*} values. Reports removed agents.
    --prune               Used with --update: also delete agents removed from the taxonomy.
    --check               Check for template drift and structural changes (exit code 1 if found)
    --scan-security       Scan agent files for security issues
    --auto-correct        After post-audit findings, invoke standalone `copilot` CLI to repair files
    --migrate             One-step legacy fencing migration: tag the current state as
                          pre-fencing-snapshot, overwrite all agent files with fenced templates,
                          and print a quality-audit checklist. Use --revert-migration to undo.
    --revert-migration    Undo a --migrate run: git reset --hard pre-fencing-snapshot and delete
                          the tag. Requires the project directory to be a git repository.
    --convert-from DIR    Convert an existing agent team from DIR to the target --framework.
                          Reads existing agent files, preserves prose body, replaces front
                          matter with the target framework's conventions. Does not require
                          --description. Use --output to specify the destination directory.
    --version             Print version and exit
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# Ensure the agentteams/ package is importable in dev mode (direct script invocation)
_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from agentteams import ingest, analyze, render, emit
from agentteams import liaison_logs
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.claude import ClaudeAdapter

__version__ = "0.1.0"

FRAMEWORKS = {
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
    "claude": ClaudeAdapter,
}

TEMPLATES_DIR = _SCRIPT_DIR / "agentteams" / "templates"


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentteams",
        description="Generate a complete agent team for any project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--description", "-d",
        metavar="PATH",
        required=False,
        default=None,
        help="Project description file (.json or .md). Required unless --self is used.",
    )
    parser.add_argument(
        "--project", "-p",
        metavar="PATH",
        default=None,
        help="Project directory to scan and use as base (overrides existing_project_path in description)",
    )
    parser.add_argument(
        "--framework", "-f",
        choices=list(FRAMEWORKS.keys()),
        default="copilot-vscode",
        metavar="NAME",
        help=f"Target framework: {', '.join(FRAMEWORKS)} (default: copilot-vscode)",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="DIR",
        default=None,
        help=(
            "Output directory for agent files. "
            "Default: <project>/.github/agents/ (copilot-vscode), "
            "<project>/.github/copilot/ (copilot-cli), "
            "<project>/.claude/agents/ (claude)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be generated without writing files",
    )
    overwrite_group = parser.add_mutually_exclusive_group()
    overwrite_group.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing agent files unconditionally (full-file replacement). "
             "Use --merge instead to preserve user-authored content in fenced files.",
    )
    overwrite_group.add_argument(
        "--merge",
        action="store_true",
        help="Update only template-fenced regions in existing agent files, "
             "preserving all user-authored content outside fence markers. "
             "Skips legacy files (no fence markers) with a warning.",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Non-interactive: answer yes to all prompts",
    )
    parser.add_argument(
        "--no-scan",
        action="store_true",
        help="Disable project directory scanning",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Re-render drifted files and emit new agents added to the taxonomy. "
             "Preserves manually-filled {MANUAL:*} values from existing files. "
             "Removed agents are reported but not deleted (use --prune to remove them).",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Used with --update: also delete agent files that are no longer part of the team.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for template drift and structural changes without writing any files "
             "(exit code 1 if drift or structural changes are detected)",
    )
    parser.add_argument(
        "--scan-security",
        action="store_true",
        help="Scan generated agent files for security issues (PII, credentials, unresolved placeholders)",
    )
    parser.add_argument(
        "--self",
        action="store_true",
        dest="self_update",
        help="Operate on the module's own agent team using .github/agents/_build-description.json",
    )
    parser.add_argument(
        "--post-audit",
        action="store_true",
        dest="post_audit",
        help=(
            "Run a post-generation audit after emit. Performs static checks "
            "(unresolved placeholders, YAML integrity, required-agent coverage) "
            "and, if the `gh` CLI is authenticated, an AI-powered conflict and "
            "presupposition review via GitHub Models (Claude Sonnet 4.6)."
        ),
    )
    parser.add_argument(
        "--auto-correct",
        action="store_true",
        dest="auto_correct",
        help=(
            "After --post-audit finds issues, invoke the standalone `copilot` CLI "
            "in non-interactive mode to repair generated team files, then rerun the audit."
        ),
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help=(
            "After generating the team, scan for default template elements "
            "(unresolved {MANUAL:*} placeholders, underdeveloped sections, "
            "incomplete tool metadata) and attempt context-aware auto-enrichment. "
            "Exports a defaults-audit.csv to the references/ directory. "
            "Combine with --post-audit to also run AI-powered enrichment."
        ),
    )
    parser.add_argument(
        "--security-offline",
        action="store_true",
        help=(
            "Use cached security vulnerability snapshot only (no network fetch) "
            "when rendering security intelligence references."
        ),
    )
    parser.add_argument(
        "--security-max-items",
        type=int,
        default=15,
        metavar="N",
        help="Maximum number of current vulnerabilities to include in generated security references (default: 15)",
    )
    parser.add_argument(
        "--security-no-nvd",
        action="store_true",
        help=(
            "Skip NVD CVSS enrichment (avoids ~7 s per CVE rate-limit sleep). "
            "CISA KEV + EPSS data are still fetched."
        ),
    )
    parser.add_argument(
        "--migrate",
        action="store_true",
        help=(
            "One-step legacy fencing migration. Tags the current git state as "
            "'pre-fencing-snapshot', then runs --overwrite to regenerate all agent files "
            "with fenced templates. Prints a quality-audit checklist on completion. "
            "Use --revert-migration to undo."
        ),
    )
    parser.add_argument(
        "--convert-from",
        metavar="DIR",
        dest="convert_from",
        default=None,
        help=(
            "Convert an existing agent team from DIR to the target --framework. "
            "Reads each agent file, preserves its prose body, and re-wraps it with "
            "the target framework's front matter. Does not require --description. "
            "Use --output to specify the destination agents directory."
        ),
    )
    parser.add_argument(
        "--interop-from",
        metavar="DIR",
        dest="interop_from",
        default=None,
        help=(
            "Run cross-framework interop pipeline from an existing team directory. "
            "Uses Canonical Agent Interface (CAI) normalization before writing target output."
        ),
    )
    parser.add_argument(
        "--interop-source-framework",
        dest="interop_source_framework",
        choices=list(FRAMEWORKS.keys()),
        default=None,
        help="Optional source framework override for --interop-from (auto-detected when omitted).",
    )
    parser.add_argument(
        "--interop-mode",
        dest="interop_mode",
        choices=["direct", "bundle"],
        default="direct",
        help="Interop mode: direct conversion only, or bundle (conversion + interop artifacts).",
    )
    parser.add_argument(
        "--bridge-from",
        metavar="DIR",
        dest="bridge_from",
        default=None,
        help=(
            "Generate a lightweight interface bridge from an existing source team directory "
            "without regenerating source agent documentation."
        ),
    )
    parser.add_argument(
        "--bridge-source-framework",
        dest="bridge_source_framework",
        choices=list(FRAMEWORKS.keys()),
        default=None,
        help="Optional source framework override for --bridge-from (auto-detected when omitted).",
    )
    parser.add_argument(
        "--bridge-check",
        action="store_true",
        dest="bridge_check",
        help="Check bridge freshness against source files without regenerating bridge artifacts.",
    )
    parser.add_argument(
        "--bridge-refresh",
        action="store_true",
        dest="bridge_refresh",
        help="Refresh bridge artifacts even if they already exist.",
    )
    parser.add_argument(
        "--revert-migration",
        action="store_true",
        dest="revert_migration",
        help=(
            "Undo a previous --migrate run. Runs 'git reset --hard pre-fencing-snapshot' "
            "in the project directory and deletes the 'pre-fencing-snapshot' tag. "
            "Requires the project directory to be a git repository with that tag present."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        dest="no_backup",
        help=(
            "Skip automatic backup of existing agent files before any write operation. "
            "Not recommended for interactive use; intended for CI/scripted pipelines "
            "where an external snapshot mechanism is already in place."
        ),
    )
    parser.add_argument(
        "--list-backups",
        action="store_true",
        dest="list_backups",
        help="List available backups for the target output directory and exit.",
    )
    parser.add_argument(
        "--restore-backup",
        metavar="TIMESTAMP",
        dest="restore_backup",
        default=None,
        help=(
            "Restore a previously created backup into the output directory. "
            "Pass the timestamp label shown by --list-backups, or 'latest' "
            "to restore the most recent backup."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_option_combinations(parser, args)

    # -----------------------------------------------------------------------
    # --self: redirect to the module's own build description
    # -----------------------------------------------------------------------
    if args.self_update:
        self_desc = _SCRIPT_DIR / ".github" / "agents" / "_build-description.json"
        if not self_desc.exists():
            print(f"Error: Self-description not found at {self_desc}", file=sys.stderr)
            return 1
        args.description = str(self_desc)
        args.project = str(_SCRIPT_DIR)
        if not args.output:
            args.output = str(_SCRIPT_DIR / ".github" / "agents")
        print(f"Self-maintenance mode: using {self_desc.name}")

    # -----------------------------------------------------------------------
    # --revert-migration: undo a previous --migrate run
    # -----------------------------------------------------------------------
    if args.revert_migration:
        project_dir = Path(args.project).resolve() if args.project else Path.cwd()
        return _run_revert_migration(project_dir)

    # -----------------------------------------------------------------------
    # --migrate: tag + overwrite (delegates back into main with --overwrite)
    # -----------------------------------------------------------------------
    if args.migrate:
        if not args.description:
            parser.error("--description is required with --migrate")
        project_dir = Path(args.project).resolve() if args.project else Path.cwd()
        return _run_migrate(project_dir, argv or sys.argv[1:])

    # -----------------------------------------------------------------------
    # --convert-from: format migration (no --description needed)
    # -----------------------------------------------------------------------
    if args.convert_from:
        return _run_convert(
            source_dir=Path(args.convert_from).resolve(),
            target_framework=args.framework,
            output=Path(args.output).resolve() if args.output else None,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )

    # -----------------------------------------------------------------------
    # --interop-from: CAI-based cross-framework interop pipeline
    # -----------------------------------------------------------------------
    if args.interop_from:
        return _run_interop(
            source_dir=Path(args.interop_from).resolve(),
            source_framework=args.interop_source_framework,
            target_framework=args.framework,
            output=Path(args.output).resolve() if args.output else None,
            mode=args.interop_mode,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )

    # -----------------------------------------------------------------------
    # --bridge-from: lightweight bridge interface generation/check
    # -----------------------------------------------------------------------
    if args.bridge_from:
        return _run_bridge(
            source_dir=Path(args.bridge_from).resolve(),
            source_framework=args.bridge_source_framework,
            target_framework=args.framework,
            output=Path(args.output).resolve() if args.output else None,
            dry_run=args.dry_run,
            overwrite=(args.overwrite or args.bridge_refresh),
            check_only=args.bridge_check,
        )

    if not args.description:
        parser.error("--description is required (or use --self for self-maintenance)")

    framework_id: str = args.framework
    adapter = FRAMEWORKS[framework_id]()

    # --post-audit implies --enrich: auditing un-enriched files gives false positives.
    # Automatically enable enrichment so the audit sees the fully filled output.
    if args.post_audit and not args.enrich:
        args.enrich = True

    # -----------------------------------------------------------------------
    # Step 1: Ingest
    # -----------------------------------------------------------------------
    print(f"Loading description from {args.description!r}...")
    try:
        description = ingest.load(args.description, scan_project=not args.no_scan)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error loading description: {exc}", file=sys.stderr)
        return 1

    # Override project path from CLI if provided
    if args.project:
        description["existing_project_path"] = str(Path(args.project).resolve())
        if not args.no_scan:
            description = ingest._supplement_from_directory(
                description, Path(description["existing_project_path"])
            )

    # -----------------------------------------------------------------------
    # Step 2: Validate
    # -----------------------------------------------------------------------
    errors = ingest.validate(description)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    # -----------------------------------------------------------------------
    # Step 3: Analyze → manifest
    # -----------------------------------------------------------------------
    print(f"Analyzing project for {framework_id!r} framework...")
    manifest = analyze.build_manifest(description, framework=framework_id)

    project_name = manifest["project_name"]
    project_type = manifest["project_type"]
    print(f"  Project: {project_name!r}  |  Type: {project_type}  |  Framework: {framework_id}")
    print(f"  Archetypes: {', '.join(manifest['selected_archetypes'])}")
    print(f"  Components: {len(manifest['components'])}")
    print(f"  Total agents: {len(manifest['agent_slug_list'])}")

    # -----------------------------------------------------------------------
    # Step 4: Resolve output directory
    # -----------------------------------------------------------------------
    if args.output:
        output_dir = Path(args.output).resolve()
    elif description.get("existing_project_path"):
        project_path = Path(description["existing_project_path"])
        output_dir = adapter.get_agents_dir(project_path)
    else:
        output_dir = adapter.get_agents_dir(Path.cwd())

    print(f"  Output directory: {output_dir}")

    # -----------------------------------------------------------------------
    # Step 4a: Handle --list-backups and --restore-backup (no rendering needed)
    # -----------------------------------------------------------------------
    if args.list_backups:
        backups = emit.list_backups(output_dir)
        if not backups:
            print(f"No backups found for {output_dir}")
        else:
            print(f"Backups for {output_dir}:")
            for ts, bpath, count in backups:
                print(f"  {ts}  ({count} file(s))  {bpath}")
        return 0

    if args.restore_backup is not None:
        backups = emit.list_backups(output_dir)
        if not backups:
            print(f"No backups found for {output_dir}", file=sys.stderr)
            return 1
        label = args.restore_backup
        if label == "latest":
            _, backup_path, _ = backups[0]
        else:
            matched = [(ts, p, c) for ts, p, c in backups if ts == label]
            if not matched:
                print(f"Backup not found: {label!r}", file=sys.stderr)
                print(f"Available: {', '.join(ts for ts, _, _ in backups)}")
                return 1
            _, backup_path, _ = matched[0]

        try:
            _assert_destructive_action_allowed(output_dir, action="restore-backup")
        except RuntimeError as exc:
            print(f"Security gate blocked restore-backup: {exc}", file=sys.stderr)
            return 1

        count = emit.restore_backup(backup_path, output_dir, remove_extra=True)
        print(f"  ✓  Restored {count} file(s) from {backup_path}")
        return 0

    # -----------------------------------------------------------------------
    # Step 4b: Handle --scan-security (no rendering needed)
    # -----------------------------------------------------------------------
    if args.scan_security:
        from agentteams import scan
        report = scan.scan_directory(output_dir)
        scan.print_scan_report(report)
        return 1 if report.has_issues else 0

    # -----------------------------------------------------------------------
    # Step 4d: Build live security intelligence placeholders
    # -----------------------------------------------------------------------
    from agentteams import security_refs as _security_refs

    _project_tools: list[str] = manifest.get("tools", []) or []
    security_placeholders = _security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=args.security_offline,
        max_items=max(1, int(args.security_max_items)),
        tools=_project_tools if _project_tools else None,
        skip_nvd=args.security_no_nvd,
    )
    manifest["auto_resolved_placeholders"].update(security_placeholders)

    # -----------------------------------------------------------------------
    # Step 4c: Handle --check (drift + structural changes, no write)
    # -----------------------------------------------------------------------
    if args.check:
        from agentteams import drift
        # Content drift (template hash comparison)
        try:
            dreport = drift.detect_drift(output_dir, TEMPLATES_DIR)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        drift.print_drift_report(dreport)
        # Structural diff (team composition comparison)
        sdreport = None
        try:
            old_log = drift.load_build_log(output_dir)
            sdreport = drift.compute_structural_diff(old_log, manifest, TEMPLATES_DIR)
            if sdreport.added_files or sdreport.removed_files:
                print("\n  Structural changes:")
                drift.print_structural_diff_report(sdreport)
        except FileNotFoundError:
            pass  # no build-log — structural diff not available
        has_any = dreport.has_drift or (sdreport.has_changes if sdreport is not None else False)
        return 1 if has_any else 0

    # -----------------------------------------------------------------------
    # Step 5: Render
    # -----------------------------------------------------------------------
    print("Rendering templates...")
    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)

    # Compute template hashes for drift detection
    template_hashes = render.compute_template_hashes(manifest, templates_dir=TEMPLATES_DIR)

    # Apply framework-specific post-processing
    final_rendered: list[tuple[str, str]] = []
    for rel_path, content in rendered:
        file_type = _guess_file_type(rel_path)
        if file_type == "agent":
            slug = Path(rel_path).stem.replace(".agent", "")
            content = adapter.render_agent_file(content, slug, manifest)
        elif file_type == "instructions":
            content = adapter.render_instructions_file(content, manifest)
        final_path = adapter.finalize_output_path(rel_path, file_type)
        final_rendered.append((final_path, content))

    # -----------------------------------------------------------------------
    # Step 5c: Generate team topology graph
    # -----------------------------------------------------------------------
    from agentteams import graph as _graph
    graph_content = _graph.generate_graph_document(
        dict(final_rendered), project_name=project_name
    )
    final_rendered.append(("references/pipeline-graph.md", graph_content))

    # -----------------------------------------------------------------------
    # Step 5b: Handle --update (structural + content drift, manual preservation)
    # -----------------------------------------------------------------------
    if args.update:
        from agentteams import drift

        # Load old build-log (may not exist for first-generation teams)
        try:
            old_log = drift.load_build_log(output_dir)
        except FileNotFoundError:
            old_log = {}

        # Compute structural diff: additions, removals, drifted, unchanged
        sdreport = drift.compute_structural_diff(old_log, manifest, TEMPLATES_DIR)

        # Always refresh security intelligence references during --update,
        # even when template/content drift is otherwise empty.
        security_refresh_paths = {
            "references/security-vulnerability-watch.reference.md",
            "references/security-vulnerability-watch.json",
        }

        if not sdreport.has_changes and not sdreport.removed_files:
            print("No structural or content changes detected; refreshing security intelligence references.")
        else:
            print(f"\nStructural update for {project_name!r}:")
            drift.print_structural_diff_report(sdreport)

        # Build the update set from the structural diff + security refresh files
        update_paths: set[str] = {f["path"] for f in sdreport.update_files}
        update_paths.update(security_refresh_paths)

        update_rendered: list[tuple[str, str]] = []
        for rel_path, content in final_rendered:
            if rel_path not in update_paths:
                continue
            # Preserve {MANUAL:*} values from the existing file (if any)
            existing_path = emit._resolve_path(output_dir, rel_path)
            if existing_path.exists():
                content = _preserve_manual_values(
                    existing_path.read_text(encoding="utf-8"), content
                )
            update_rendered.append((rel_path, content))

        if not update_rendered:
            print("Changes detected but no matching rendered files — already up to date.")
            return 0

        # Always regenerate the team topology graph on every update
        graph_rel_path = "references/pipeline-graph.md"
        if not any(p == graph_rel_path for p, _ in update_rendered):
            graph_update_content = _graph.generate_graph_document(
                dict(final_rendered), project_name=project_name
            )
            update_rendered.append((graph_rel_path, graph_update_content))

        # --prune: delete removed files (with confirmation unless --yes)
        if args.prune and sdreport.removed_files:
            rc = _prune_removed_files(sdreport.removed_files, output_dir, args.yes, args.dry_run)
            if rc != 0:
                return rc

        print(f"\nWriting {len(update_rendered)} file(s)...")

        # Back up BEFORE migration so the backup captures pre-migration state
        if not args.dry_run and not args.no_backup:
            emit.backup_output_dir(
                output_dir,
                files_to_backup=[rel for rel, _ in update_rendered],
            )

        # Migrate any inline log tables to CSV before writing
        if not args.dry_run:
            adjacent_repos_md = emit._resolve_path(output_dir, "references/adjacent-repos.md")
            mresult = liaison_logs.migrate_inline_logs(adjacent_repos_md, output_dir / "references")
            if mresult.rows_moved > 0:
                print(
                    f"  ✓  Migrated {mresult.changelog_rows_moved} changelog row(s) and "
                    f"{mresult.coord_log_rows_moved} coordination row(s) to CSV files."
                )

        if not args.dry_run and not args.merge:
            try:
                _assert_destructive_action_allowed(output_dir, action="overwrite")
            except RuntimeError as exc:
                print(f"Security gate blocked overwrite update: {exc}", file=sys.stderr)
                return 1

        result = emit.emit_all(
            update_rendered,
            output_dir=output_dir,
            dry_run=args.dry_run,
            overwrite=not args.merge,
            merge=args.merge,
            yes=args.yes,
        )
        emit.print_summary(result, manifest)

        # ------------------------------------------------------------------
        # Post-generation audit (--update path)
        # ------------------------------------------------------------------
        if args.post_audit and result.success and not args.dry_run:
            from agentteams import audit as _audit
            audit_result = _audit.run_post_audit(
                output_dir, manifest,
                ai_audit=True,
            )
            _audit.print_audit_report(audit_result)
            if args.auto_correct and (audit_result.has_errors or audit_result.has_warnings):
                audit_result = _attempt_auto_correct(
                    output_dir=output_dir,
                    manifest=manifest,
                    audit_result=audit_result,
                )
            if audit_result.has_errors:
                return 1

        if not args.dry_run and result.success:
            created = liaison_logs.init_csv_stubs(output_dir / "references")
            if created:
                print(f"  ✓  Created CSV log stubs: {', '.join(created)}")
            _write_run_log(manifest, result, output_dir, template_hashes)
        return 0 if result.success else 1

    # -----------------------------------------------------------------------
    # Step 5d: Defaults audit + auto-enrichment (--enrich)
    # -----------------------------------------------------------------------
    if args.enrich:
        from agentteams import enrich as _enrich

        project_path_for_enrich: Path | None = None
        if description.get("existing_project_path"):
            project_path_for_enrich = Path(description["existing_project_path"])

        print("Scanning for default template elements...")
        # Attach manifest fields needed by enrich module
        manifest["project_goal"] = description.get("project_goal", "")
        manifest["output_format"] = description.get("output_format") or manifest.get("output_format", "")
        manifest["tools"] = description.get("tools", [])
        manifest["description"] = description

        # Build file_map from final_rendered for scanning
        enrich_file_map = dict(final_rendered)
        findings = _enrich.scan_defaults(enrich_file_map, manifest, project_path=project_path_for_enrich)
        print(f"  Found {len(findings)} default finding(s)")

        # Auto-enrich (rule-based + notebook scanning + tool catalog)
        enriched_file_map, findings = _enrich.auto_enrich(
            findings, enrich_file_map, manifest, project_path=project_path_for_enrich
        )

        # Optionally follow up with AI enrichment if copilot CLI is available
        if args.post_audit:
            copilot_exe = _enrich.shutil.which("copilot")
            if copilot_exe:
                print("  Running AI enrichment via copilot CLI...")
                enriched_file_map, findings = _enrich.ai_enrich(
                    findings, enriched_file_map, manifest,
                    project_path=project_path_for_enrich,
                    copilot_path=copilot_exe,
                )

        # Export CSV
        csv_rel_path = "references/defaults-audit.csv"
        _enrich.export_csv(findings, output_dir / csv_rel_path)
        print(f"  Defaults audit CSV written to {csv_rel_path}")

        # Replace final_rendered with enriched content + CSV entry
        final_rendered = list(enriched_file_map.items())
        # Add CSV to the rendered set so it gets emitted
        csv_content = (output_dir / csv_rel_path).read_text(encoding="utf-8")
        if not any(p == csv_rel_path for p, _ in final_rendered):
            final_rendered.append((csv_rel_path, csv_content))

        # Regenerate SETUP-REQUIRED.md based only on genuinely pending findings
        setup_req_content = _enrich.generate_setup_required(findings, manifest)
        final_rendered = [
            (p, setup_req_content if "SETUP-REQUIRED" in p else c)
            for p, c in final_rendered
        ]
        # Count pending for emit summary
        pending_count = sum(1 for f in findings if f.status == "pending")
        manifest["manual_required_placeholders"] = [
            {"placeholder": f.token, "agent_file": f.file,
             "context": f.context_snippet, "suggestion": f.auto_suggestion}
            for f in findings if f.status == "pending"
        ]

        _enrich.print_enrich_summary(findings)

    # -----------------------------------------------------------------------
    # Step 6: Validate cross-references
    # -----------------------------------------------------------------------
    warnings = render.validate_cross_refs(final_rendered)
    if warnings:
        manifest["_cross_ref_warnings"] = warnings
        print(f"  ⚠  {len(warnings)} cross-reference warning(s)")

    # -----------------------------------------------------------------------
    # Step 7: Emit
    # -----------------------------------------------------------------------
    # Surface user-customization warnings before a merge so users can review
    if args.merge and not args.dry_run:
        from agentteams import drift as _drift_mod
        customized = _drift_mod.detect_user_customizations(output_dir)
        if customized:
            print(f"\n  ℹ  {len(customized)} file(s) have been edited since last build (advisory):")
            for entry in customized[:10]:
                print(f"     ~ {entry['rel_path']}  ({entry['reason']})")
            if len(customized) > 10:
                print(f"     ... and {len(customized) - 10} more")
            print("     These files will have their fenced sections updated; "
                  "user-authored content outside fences is preserved.")

    if not args.dry_run and not args.no_backup and (args.overwrite or args.merge):
        emit.backup_output_dir(
            output_dir,
            files_to_backup=[rel for rel, _ in final_rendered],
        )

    # Migrate any inline log tables to CSV before a merge run
    if args.merge and not args.dry_run:
        adjacent_repos_md = emit._resolve_path(output_dir, "references/adjacent-repos.md")
        mresult = liaison_logs.migrate_inline_logs(adjacent_repos_md, output_dir / "references")
        if mresult.rows_moved > 0:
            print(
                f"  ✓  Migrated {mresult.changelog_rows_moved} changelog row(s) and "
                f"{mresult.coord_log_rows_moved} coordination row(s) to CSV files."
            )

    if not args.dry_run and args.overwrite:
        try:
            _assert_destructive_action_allowed(output_dir, action="overwrite")
        except RuntimeError as exc:
            print(f"Security gate blocked overwrite: {exc}", file=sys.stderr)
            return 1

    result = emit.emit_all(
        final_rendered,
        output_dir=output_dir,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        merge=args.merge,
        yes=args.yes,
    )
    emit.print_summary(result, manifest)

    # -----------------------------------------------------------------------
    # Step 8.5: Post-generation audit (if --post-audit)
    # -----------------------------------------------------------------------
    if args.post_audit and result.success and not args.dry_run:
        from agentteams import audit as _audit
        audit_result = _audit.run_post_audit(
            output_dir, manifest,
            rendered_files=final_rendered,
            ai_audit=True,
        )
        _audit.print_audit_report(audit_result)
        if args.auto_correct and (audit_result.has_errors or audit_result.has_warnings):
            audit_result = _attempt_auto_correct(
                output_dir=output_dir,
                manifest=manifest,
                audit_result=audit_result,
            )
        if audit_result.has_errors:
            return 1

    if not args.dry_run and result.success:
        created = liaison_logs.init_csv_stubs(output_dir / "references")
        if created:
            print(f"  ✓  Created CSV log stubs: {', '.join(created)}")

    # -----------------------------------------------------------------------
    # Step 9: Write run log (skip in dry-run)
    # -----------------------------------------------------------------------
    if not args.dry_run and result.success:
        _write_run_log(manifest, result, output_dir, template_hashes)

    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_convert(
    source_dir: Path,
    target_framework: str,
    output: Path | None,
    dry_run: bool,
    overwrite: bool,
) -> int:
    """Execute the --convert-from path: convert an existing team to a new framework format.

    Args:
        source_dir: Directory containing the source agent files.
        target_framework: Target framework identifier.
        output: Explicit output directory, or None to auto-derive from source.
        dry_run: When True, report actions without writing files.
        overwrite: When True, overwrite existing target files.

    Returns:
        0 on success, 1 on error.
    """
    from agentteams.convert import convert_team

    if output is not None:
        target_dir = output
    else:
        # Auto-derive: use source parent as project root, place agents under framework dir
        project_root = source_dir.parent.parent  # e.g. /repo from /repo/.github/agents
        adapter_cls = FRAMEWORKS[target_framework]
        target_dir = adapter_cls().get_agents_dir(project_root)

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Converting{dry_label} agent team:\n"
        f"  source:  {source_dir}\n"
        f"  target:  {target_dir}\n"
        f"  framework: {target_framework}"
    )

    # Read project_name from build-log.json if present (best-effort)
    build_log_path = source_dir / "references" / "build-log.json"
    project_manifest: dict = {}
    if build_log_path.exists():
        try:
            import json as _json
            with build_log_path.open("r", encoding="utf-8") as fh:
                log = _json.load(fh)
            if isinstance(log.get("project_name"), str):
                project_manifest["project_name"] = log["project_name"]
        except Exception:  # noqa: BLE001
            pass

    try:
        result = convert_team(
            source_dir=source_dir,
            target_dir=target_dir,
            target_framework=target_framework,
            project_manifest=project_manifest,
            dry_run=dry_run,
            overwrite=overwrite,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        print(f"\n  ✗  {len(result.errors)} error(s):")
        for err in result.errors:
            print(f"    {err}")

    verb = "Would convert" if dry_run else "Converted"
    print(
        f"\n  {verb} {len(result.converted)} file(s)"
        + (f", skipped {len(result.skipped)}" if result.skipped else "")
        + "."
    )
    if dry_run and result.converted:
        print("  Files that would be written:")
        for path in result.converted:
            print(f"    {path}")

    return 0 if result.success else 1


def _run_interop(
    source_dir: Path,
    source_framework: str | None,
    target_framework: str,
    output: Path | None,
    mode: str,
    dry_run: bool,
    overwrite: bool,
) -> int:
    """Execute the --interop-from path via CAI normalization pipeline."""
    from agentteams.interop import detect_framework, run_interop

    detected = source_framework or detect_framework(source_dir)
    if output is not None:
        target_dir = output
    else:
        project_root = source_dir.parent.parent
        target_dir = FRAMEWORKS[target_framework]().get_agents_dir(project_root)

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Running interop{dry_label}:\n"
        f"  source:  {source_dir}\n"
        f"  source framework: {detected}\n"
        f"  target:  {target_dir}\n"
        f"  target framework: {target_framework}\n"
        f"  mode: {mode}"
    )

    try:
        result = run_interop(
            source_dir=source_dir,
            source_framework=detected,
            target_framework=target_framework,
            target_dir=target_dir,
            mode=mode,
            dry_run=dry_run,
            overwrite=overwrite,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        print(f"\n  ✗  {len(result.errors)} error(s):")
        for err in result.errors:
            print(f"    {err}")

    verb = "Would interop-convert" if dry_run else "Interop-converted"
    print(
        f"\n  {verb} {len(result.converted)} file(s)"
        + (f", skipped {len(result.skipped)}" if result.skipped else "")
        + "."
    )
    if result.bundle_files:
        bundle_verb = "Would write" if dry_run else "Wrote"
        print(f"  {bundle_verb} {len(result.bundle_files)} interop bundle file(s).")

    return 0 if result.success else 1


def _run_bridge(
    source_dir: Path,
    source_framework: str | None,
    target_framework: str,
    output: Path | None,
    dry_run: bool,
    overwrite: bool,
    check_only: bool,
) -> int:
    """Execute the --bridge-from path via lightweight compatibility artifacts."""
    from agentteams.bridge import run_bridge
    from agentteams.interop import detect_framework

    detected = source_framework or detect_framework(source_dir)
    if output is not None:
        output_root = output
    else:
        project_root = source_dir.parent.parent
        output_root = project_root

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Running bridge{dry_label}:\n"
        f"  source:  {source_dir}\n"
        f"  source framework: {detected}\n"
        f"  target framework: {target_framework}\n"
        f"  output root: {output_root}\n"
        f"  mode: {'check' if check_only else 'generate'}"
    )

    try:
        result = run_bridge(
            source_dir=source_dir,
            source_framework=detected,
            target_framework=target_framework,
            output_root=output_root,
            dry_run=dry_run,
            overwrite=overwrite,
            check_only=check_only,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if result.errors:
        print(f"\n  ✗  {len(result.errors)} error(s):")
        for err in result.errors:
            print(f"    {err}")

    if check_only:
        print(f"\n  Bridge check: {'PASS' if result.check_ok else 'FAIL'}")
        if result.check_report_path:
            print(f"  Report: {result.check_report_path}")
    else:
        verb = "Would write" if dry_run else "Wrote"
        print(
            f"\n  {verb} {len(result.written)} bridge file(s)"
            + (f", skipped {len(result.skipped)}" if result.skipped else "")
            + "."
        )

    return 0 if result.success else 1


def _validate_option_combinations(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Validate explicit incompatible option pairs and mode-specific constraints."""
    if args.auto_correct and not args.post_audit:
        parser.error("--auto-correct requires --post-audit")

    if args.prune and not args.update:
        parser.error("--prune can only be used with --update")

    if args.convert_from and args.interop_from:
        parser.error("--convert-from and --interop-from are mutually exclusive")

    if args.bridge_from and args.convert_from:
        parser.error("--bridge-from and --convert-from are mutually exclusive")

    if args.bridge_from and args.interop_from:
        parser.error("--bridge-from and --interop-from are mutually exclusive")

    if args.bridge_check and args.bridge_refresh:
        parser.error("--bridge-check cannot be combined with --bridge-refresh")

    if args.bridge_check and not args.bridge_from:
        parser.error("--bridge-check requires --bridge-from")

    if args.bridge_refresh and not args.bridge_from:
        parser.error("--bridge-refresh requires --bridge-from")

    convert_incompatible = [
        ("description", "--description"),
        ("project", "--project"),
        ("self_update", "--self"),
        ("no_scan", "--no-scan"),
        ("update", "--update"),
        ("prune", "--prune"),
        ("check", "--check"),
        ("scan_security", "--scan-security"),
        ("post_audit", "--post-audit"),
        ("auto_correct", "--auto-correct"),
        ("enrich", "--enrich"),
        ("merge", "--merge"),
        ("migrate", "--migrate"),
        ("revert_migration", "--revert-migration"),
        ("list_backups", "--list-backups"),
        ("restore_backup", "--restore-backup"),
    ]

    interop_incompatible = [
        ("description", "--description"),
        ("project", "--project"),
        ("self_update", "--self"),
        ("no_scan", "--no-scan"),
        ("update", "--update"),
        ("prune", "--prune"),
        ("check", "--check"),
        ("scan_security", "--scan-security"),
        ("post_audit", "--post-audit"),
        ("auto_correct", "--auto-correct"),
        ("enrich", "--enrich"),
        ("merge", "--merge"),
        ("migrate", "--migrate"),
        ("revert_migration", "--revert-migration"),
        ("list_backups", "--list-backups"),
        ("restore_backup", "--restore-backup"),
    ]

    bridge_incompatible = [
        ("description", "--description"),
        ("project", "--project"),
        ("self_update", "--self"),
        ("no_scan", "--no-scan"),
        ("update", "--update"),
        ("prune", "--prune"),
        ("check", "--check"),
        ("scan_security", "--scan-security"),
        ("post_audit", "--post-audit"),
        ("auto_correct", "--auto-correct"),
        ("enrich", "--enrich"),
        ("merge", "--merge"),
        ("migrate", "--migrate"),
        ("revert_migration", "--revert-migration"),
        ("list_backups", "--list-backups"),
        ("restore_backup", "--restore-backup"),
    ]

    if args.convert_from:
        for attr, flag in convert_incompatible:
            val = getattr(args, attr)
            if attr == "description":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --convert-from")
            elif attr == "restore_backup":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --convert-from")
            elif val:
                parser.error(f"{flag} cannot be used with --convert-from")

    if args.interop_from:
        for attr, flag in interop_incompatible:
            val = getattr(args, attr)
            if attr == "description":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --interop-from")
            elif attr == "restore_backup":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --interop-from")
            elif val:
                parser.error(f"{flag} cannot be used with --interop-from")

    if args.bridge_from:
        for attr, flag in bridge_incompatible:
            val = getattr(args, attr)
            if attr == "description":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --bridge-from")
            elif attr == "restore_backup":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --bridge-from")
            elif val:
                parser.error(f"{flag} cannot be used with --bridge-from")


def _prune_removed_files(
    removed_files: list[dict],
    output_dir: Path,
    yes: bool,
    dry_run: bool,
) -> int:
    """Delete agent files that the new manifest no longer includes.

    Args:
        removed_files: List of file-entry dicts from StructuralDiffReport.removed_files.
        output_dir:    Root agents directory.
        yes:           If True, skip interactive confirmation.
        dry_run:       If True, report but do not delete.

    Returns:
        0 on success, 1 if user declined or an error occurred.
    """
    paths = [emit._resolve_path(output_dir, f["path"]) for f in removed_files]
    existing = [p for p in paths if p.exists()]
    if not existing:
        return 0

    print(f"\n  --prune: {len(existing)} file(s) to remove:")
    for p in existing:
        print(f"    {p.name}")

    if dry_run:
        print("  (dry-run: no files deleted)")
        return 0

    try:
        _assert_destructive_action_allowed(output_dir, action="prune")
    except RuntimeError as exc:
        print(f"  Security gate blocked prune: {exc}", file=sys.stderr)
        return 1

    if not yes:
        try:
            answer = input("\n  Delete these files? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer != "y":
            print("  Prune cancelled.")
            return 1

    for p in existing:
        try:
            p.unlink()
            print(f"  Deleted: {p.name}")
        except OSError as exc:
            print(f"  Error deleting {p.name}: {exc}", file=sys.stderr)
            return 1

    return 0


def _assert_destructive_action_allowed(output_dir: Path, *, action: str) -> None:
    """Raise RuntimeError if security decisions do not allow destructive action.

    The check follows documented security protocol semantics:
    - HALT blocks execution
    - CONDITIONAL PASS requires conditions_verified=verified
    - PASS allows execution
    - No matching decision blocks execution
    """
    decision = _latest_security_decision(output_dir, action=action)
    if decision is None:
        raise RuntimeError(
            "no matching PASS decision found in references/security-decisions.log.csv"
        )

    verdict = decision.get("verdict", "").strip().upper()
    cond_verified = decision.get("conditions_verified", "").strip().lower()
    action_reviewed = decision.get("action_reviewed", "").strip()

    if verdict == "HALT":
        raise RuntimeError(
            f"latest decision for action '{action_reviewed or action}' is HALT"
        )

    if verdict == "CONDITIONAL PASS" and cond_verified != "verified":
        raise RuntimeError(
            "latest CONDITIONAL PASS has unverified conditions "
            f"(conditions_verified={cond_verified or 'pending'})"
        )

    if verdict not in {"PASS", "CONDITIONAL PASS"}:
        raise RuntimeError(
            f"latest decision has unsupported verdict '{verdict or 'UNKNOWN'}'"
        )


def _latest_security_decision(output_dir: Path, *, action: str) -> dict[str, str] | None:
    """Return the latest security decision row matching an action keyword."""
    log_path = output_dir / "references" / "security-decisions.log.csv"
    if not log_path.exists():
        return None

    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            expected_columns = [
                "timestamp",
                "requesting_agent",
                "action_reviewed",
                "verdict",
                "conditions",
                "conditions_verified",
            ]
            actual_columns = [c.strip() for c in (reader.fieldnames or [])]
            if actual_columns != expected_columns:
                raise RuntimeError(
                    "security decisions log is malformed: expected exact header "
                    "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified"
                )
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to read security decisions log: {exc}") from exc

    if not rows:
        return None

    for row in reversed(rows):
        reviewed = (row.get("action_reviewed") or "").lower()
        if _action_matches(reviewed, action):
            return {k: (v or "") for k, v in row.items()}
    return None


def _action_matches(action_reviewed: str, action: str) -> bool:
    """Return True for strict action-id style matches.

    Accepted patterns:
    - <action>
    - <action>-<suffix>
    - <action>_<suffix>
    - <action>.<suffix>
    - <action>:<suffix>
    """
    action_norm = action.strip().lower()
    reviewed_norm = action_reviewed.strip().lower()
    if not action_norm:
        return False
    if reviewed_norm == action_norm:
        return True
    return reviewed_norm.startswith(
        (f"{action_norm}-", f"{action_norm}_", f"{action_norm}.", f"{action_norm}:")
    )


def _attempt_auto_correct(
    *,
    output_dir: Path,
    manifest: dict,
    audit_result,
):
    """Invoke standalone Copilot CLI remediation and rerun the audit.

    Args:
        output_dir: Root agents directory for the generated team.
        manifest: Team manifest from analyze.build_manifest().
        audit_result: Initial audit result with findings to remediate.

    Returns:
        The rerun audit result if remediation succeeded, otherwise the original audit result.
    """
    from agentteams import audit as _audit
    from agentteams import remediate as _remediate

    remediation_result = _remediate.run_copilot_autocorrect(
        output_dir=output_dir,
        manifest=manifest,
        audit_result=audit_result,
    )
    _remediate.print_remediation_summary(remediation_result)

    if not remediation_result.succeeded:
        return audit_result

    rerun_result = _audit.run_post_audit(
        output_dir,
        manifest,
        ai_audit=True,
    )
    print("\n  --- Post-Remediation Audit ---")
    _audit.print_audit_report(rerun_result)
    return rerun_result


def _guess_file_type(rel_path: str) -> str:
    lower = rel_path.lower()
    if "copilot-instructions" in lower or rel_path.endswith("/CLAUDE.md") or rel_path == "../CLAUDE.md":
        return "instructions"
    if "SETUP-REQUIRED" in rel_path:
        return "setup-required"
    if "team-builder" in rel_path:
        return "builder"
    if rel_path.startswith("references/") or "/references/" in rel_path:
        return "reference"
    return "agent"


_MANUAL_RE = re.compile(r"\{MANUAL:([A-Z][A-Z0-9_]*)\}")


def _preserve_manual_values(existing_content: str, new_content: str) -> str:
    """Carry forward manually-filled {MANUAL:*} values from existing files.

    Scans the existing file for any {MANUAL:NAME} tokens that have been
    replaced with actual values, and applies those same replacements to
    the newly rendered content.

    Args:
        existing_content: Content of the currently-deployed agent file.
        new_content:      Freshly rendered content (may have {MANUAL:*} tokens).

    Returns:
        New content with manual values preserved from the existing file.
    """
    # Find all {MANUAL:*} tokens in the new content
    manual_tokens = set(_MANUAL_RE.findall(new_content))
    if not manual_tokens:
        return new_content

    # For each token, check if the existing file has a non-placeholder value
    # at the same location. We match by looking for the line context.
    result = new_content
    for token_name in manual_tokens:
        placeholder = f"{{MANUAL:{token_name}}}"
        # If the existing file still has the placeholder, nothing to preserve
        if placeholder in existing_content:
            continue
        # The existing file had this token resolved — find what it was replaced with.
        # Strategy: find lines in existing that would have contained this token,
        # by looking for the surrounding text pattern in the template.
        resolved_value = _extract_resolved_value(existing_content, new_content, placeholder)
        if resolved_value is not None:
            result = result.replace(placeholder, resolved_value)

    return result


def _extract_resolved_value(existing: str, new: str, placeholder: str) -> str | None:
    """Extract the value that replaced a placeholder in an existing file.

    Finds the line in new content containing the placeholder, builds a regex
    from the surrounding text, and matches it against the existing content.

    Args:
        existing:    Content of the existing file.
        new:         New template content with placeholder.
        placeholder: The {MANUAL:*} token to look up.

    Returns:
        The resolved value string, or None if it cannot be determined.
    """
    for new_line in new.splitlines():
        if placeholder not in new_line:
            continue
        # Build a pattern: escape everything except the placeholder
        parts = new_line.split(placeholder)
        if len(parts) != 2:
            continue  # Multiple occurrences on same line — skip for safety
        prefix = re.escape(parts[0].strip())
        suffix = re.escape(parts[1].strip())
        if not prefix and not suffix:
            continue
        pattern = prefix + r"(.+?)" + suffix if suffix else prefix + r"(.+)"
        try:
            match = re.search(pattern, existing)
        except re.error:
            continue
        if match:
            return match.group(1).strip()
    return None


def _compute_file_hashes(written_abs_paths: list[str], output_dir: Path) -> dict[str, str]:
    """Return a mapping of relative path → 16-char SHA-256 hex for written files.

    Paths are stored relative to output_dir so the build-log is portable.
    """
    import hashlib
    hashes: dict[str, str] = {}
    for abs_path_str in written_abs_paths:
        abs_path = Path(abs_path_str)
        if not abs_path.exists():
            continue
        try:
            rel = str(abs_path.relative_to(output_dir))
        except ValueError:
            # File is outside output_dir (e.g. ../copilot-instructions.md)
            try:
                rel = str(abs_path.relative_to(output_dir.parent))
                rel = "../" + rel
            except ValueError:
                rel = abs_path_str
        digest = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:16]
        hashes[rel] = digest
    return hashes


def _write_run_log(manifest: dict, result: emit.EmitResult, output_dir: Path, template_hashes: dict[str, str] | None = None) -> None:
    """Write a minimal JSON run log to the output directory."""
    from agentteams import drift as _drift

    # Convert absolute paths to project-relative paths for portability
    project_root = output_dir.parent.parent  # output_dir is .github/agents/
    files_written = []
    for f in result.written:
        try:
            files_written.append(str(Path(f).relative_to(project_root)))
        except ValueError:
            files_written.append(f)

    log = {
        "schema_version": "1.2",
        "project_name": manifest["project_name"],
        "framework": manifest["framework"],
        "project_type": manifest["project_type"],
        "archetypes": manifest["selected_archetypes"],
        "components": [c["slug"] for c in manifest["components"]],
        "files_written": files_written,
        "manual_required": len(manifest.get("manual_required_placeholders", [])),
        "template_hashes": template_hashes or {},
        # v1.2 additions — structural fingerprint for compute_structural_diff()
        "output_files_map": manifest.get("output_files", []),
        "agent_slug_list": manifest.get("agent_slug_list", []),
        "governance_agents": manifest.get("governance_agents", []),
        "manifest_fingerprint": _drift.compute_manifest_fingerprint(manifest),
        # v1.3 addition — per-file hashes for user-customization detection
        "file_hashes": _compute_file_hashes(result.written + result.merged, output_dir),
    }
    log_path = output_dir / "references" / "build-log.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

import subprocess as _subprocess


def _git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a git command in cwd and return (returncode, stdout, stderr)."""
    result = _subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


_MIGRATION_TAG = "pre-fencing-snapshot"


def _run_migrate(project_dir: Path, original_argv: list[str]) -> int:
    """Create the pre-fencing snapshot tag, then run --overwrite.

    Args:
        project_dir:   The project directory (must be a git repository).
        original_argv: The original sys.argv[1:] so we can delegate to --overwrite.

    Returns:
        0 on success, 1 on failure.
    """
    # Confirm git repo
    rc, _, err = _git(["rev-parse", "--git-dir"], project_dir)
    if rc != 0:
        print(
            f"Error: {project_dir} is not a git repository. "
            "--migrate requires a git repository to create the safety snapshot.",
            file=sys.stderr,
        )
        return 1

    # Check for uncommitted changes (warn only; don't block)
    rc2, status_out, _ = _git(["status", "--porcelain"], project_dir)
    if rc2 == 0 and status_out:
        print(
            "  ⚠  Uncommitted changes detected. The snapshot tag will capture the "
            "current HEAD commit, not the working-tree state. Consider committing first."
        )

    # Create snapshot tag (fail if it already exists)
    rc3, _, tag_err = _git(["tag", _MIGRATION_TAG], project_dir)
    if rc3 != 0:
        if "already exists" in tag_err or "already a tag" in tag_err.lower():
            print(
                f"Error: tag '{_MIGRATION_TAG}' already exists in {project_dir}. "
                "Delete it first with: git tag -d pre-fencing-snapshot",
                file=sys.stderr,
            )
        else:
            print(f"Error creating snapshot tag: {tag_err}", file=sys.stderr)
        return 1

    print(f"  ✓  Snapshot tag '{_MIGRATION_TAG}' created at HEAD.")

    # Re-invoke main() with --overwrite replacing --migrate; force --yes
    new_argv = [a for a in original_argv if a not in ("--migrate", "--revert-migration")]
    if "--overwrite" not in new_argv:
        new_argv.append("--overwrite")
    if "--yes" not in new_argv and "-y" not in new_argv:
        new_argv.append("--yes")

    print("  Running --overwrite migration...\n")
    rc_emit = main(new_argv)

    if rc_emit != 0:
        print(
            f"\n  ⚠  Overwrite failed. Snapshot tag '{_MIGRATION_TAG}' preserved for rollback.",
            file=sys.stderr,
        )
        return rc_emit

    # Post-migration guidance
    print(
        f"\n{'='*70}\n"
        "MIGRATION COMPLETE — Quality Audit Checklist\n"
        f"{'='*70}\n"
        "1. Review lost project-specific content:\n"
        f"   git diff {_MIGRATION_TAG} HEAD -- .github/agents/orchestrator.agent.md\n"
        "\n"
        "2. Restore any project-specific rules inside the USER-EDITABLE zone:\n"
        "   Add a '### <Project> Project Rules' subsection under '### Rules'\n"
        "   in orchestrator.agent.md — this section survives all future --merge runs.\n"
        "\n"
        "3. Once satisfied, commit the migrated files:\n"
        "   git add .github/agents/ && git commit -m 'chore: fence-migrate agent team'\n"
        "\n"
        "4. Future updates use --merge (non-destructive):\n"
        "   agentteams --description .github/agents/_build-description.json \\\n"
        "              --framework copilot-vscode --project . --merge --yes\n"
        "\n"
        "To roll back at any time (before committing):\n"
        f"   agentteams --revert-migration --project {project_dir}\n"
        f"{'='*70}"
    )
    return 0


def _run_revert_migration(project_dir: Path) -> int:
    """Undo a --migrate run: git reset --hard pre-fencing-snapshot and delete the tag.

    Args:
        project_dir: The project directory (must be a git repository with the tag).

    Returns:
        0 on success, 1 on failure.
    """
    rc, _, _ = _git(["rev-parse", "--git-dir"], project_dir)
    if rc != 0:
        print(
            f"Error: {project_dir} is not a git repository.",
            file=sys.stderr,
        )
        return 1

    # Verify the tag exists
    rc2, tag_sha, _ = _git(["rev-parse", "--verify", _MIGRATION_TAG], project_dir)
    if rc2 != 0:
        print(
            f"Error: tag '{_MIGRATION_TAG}' not found in {project_dir}. "
            "Nothing to revert.",
            file=sys.stderr,
        )
        return 1

    try:
        _assert_destructive_action_allowed(
            project_dir / ".github" / "agents",
            action="revert-migration",
        )
    except RuntimeError as exc:
        print(f"Security gate blocked revert-migration: {exc}", file=sys.stderr)
        return 1

    print(f"  Reverting to snapshot tag '{_MIGRATION_TAG}' ({tag_sha[:12]})...")

    rc3, _, reset_err = _git(["reset", "--hard", _MIGRATION_TAG], project_dir)
    if rc3 != 0:
        print(f"Error: git reset --hard failed: {reset_err}", file=sys.stderr)
        return 1

    print(f"  ✓  Working tree restored to {_MIGRATION_TAG}.")

    rc4, _, del_err = _git(["tag", "-d", _MIGRATION_TAG], project_dir)
    if rc4 != 0:
        print(f"  ⚠  Could not delete tag: {del_err}", file=sys.stderr)
    else:
        print(f"  ✓  Tag '{_MIGRATION_TAG}' deleted.")

    print("\n  Revert complete. Agent files are back to their pre-migration state.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
