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
    --refresh-index       Rebuild references/memory-index.json only
    --query-index TEXT    Query references/memory-index.json and print ranked hits
    --query-k N           Number of ranked results returned by --query-index (default: 5)
    --query-strategy STR  Strategy for --query-index: 'lexical' (BM25, default) or 'vector'
                          (cosine similarity, better for thematic/semantic queries)
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
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

# Ensure the agentteams/ package is importable in dev mode (direct script invocation)
_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from agentteams import ingest, analyze, render, emit
from agentteams import liaison_logs
# Concrete adapter classes are imported for type annotations (e.g. the
# _build_final_rendered signature); the framework-id -> adapter map itself is
# the single source of truth in agentteams.frameworks.registry (CH-05).
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.registry import FRAMEWORKS
# Artifact emission extracted to agentteams/errors.py + cli/artifacts.py (CH-07);
# re-exported so main and tests resolve these in build_team's namespace.
from agentteams.errors import (
    DeliveryReceiptError, EvalSuiteError, MemoryIndexError, ModelRoutingError,
)
from agentteams.cli.artifacts import (
    DELIVERY_RECEIPT_REL_PATH, EVAL_SUITE_REL_PATH, MODEL_ROUTING_REL_PATH,
    MEMORY_INDEX_REL_PATH, MEMORY_INDEX_EXTRA_DOC_NAMES,
    _require_jsonschema, _compute_file_hashes,
    _write_delivery_receipt, _write_eval_suite, _write_model_routing,
    _memory_index_sources, _read_memory_index, _validate_memory_index_schema,
    _run_refresh_index, _run_query_index, _write_memory_index,
)
# Convert/interop/bridge runners extracted to agentteams/cli/commands.py
# (CH-07); re-exported so main resolves them in build_team's namespace.
from agentteams.cli.commands import (
    _BRIDGE_AGENTS_DIR_SUFFIXES,
    _normalize_bridge_output_root,
    _run_convert,
    _run_interop,
    _run_bridge,
)
# Render/merge helpers extracted to agentteams/cli/render_pipeline.py (CH-07);
# re-exported so main and tests resolve them in build_team's namespace.
from agentteams.cli.render_pipeline import (
    _MANUAL_RE,
    _build_final_rendered,
    _make_content_matches,
    _guess_file_type,
    _preserve_manual_values,
    _extract_resolved_value,
    _apply_placeholder_policy,
    _resolve_strict_manual_mode,
    _stale_tool_agent_paths,
    _remove_stale_tool_agents,
)
# CLI parser extracted to agentteams/cli/parser.py (CH-07); re-exported here
# so main, agentteams.man, and tests resolve these in build_team's namespace.
from agentteams.cli.parser import (
    _BRIDGE_USAGE_HINT,
    _build_parser,
    _validate_option_combinations,
)

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    try:
        __version__ = _pkg_version("agentteams")
    except PackageNotFoundError:
        # Running from a source checkout without an installed dist
        __version__ = "0.0.0+local"
except ImportError:  # pragma: no cover
    __version__ = "0.0.0+local"

TEMPLATES_DIR = _SCRIPT_DIR / "agentteams" / "templates"

# Security gate extracted to agentteams/cli/security_gate.py (CH-07). Re-exported
# here so main's bare-name calls and tests (incl. monkeypatch targets) resolve
# these names in build_team's namespace unchanged.
from agentteams.cli import security_gate
from agentteams.cli.security_gate import (
    _SECURITY_DECISION_REQUIRED_COLUMNS,
    _SECURITY_WAIVER_REQUIRED_COLUMNS,
    _SECURITY_INTEL_TTL_HOURS,
    _assert_destructive_action_allowed,
    _consume_security_decision_use,
    _assert_security_intelligence_fresh,
    _security_intelligence_freshness,
    _consume_security_waiver_use,
    _latest_security_waiver,
    _security_waiver_schema_kind,
    _validate_security_waiver,
    _latest_security_decision,
    _security_decision_schema_kind,
    _action_matches,
)


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

_DUAL_DESCRIPTOR_FIELDS = ("project_name", "primary_output_dir", "reference_db_path", "deliverables")


def _check_dual_descriptor(args: argparse.Namespace) -> None:
    """Advisory check: warn when a consumer repo has both the user-supplied
    descriptor and a sibling `_build-description.json` that diverges on a
    small set of stable fields. Read-only; never modifies either file.
    Records hits to tmp/daily-pipeline/dual-descriptor-events/<date>.md (gitignored).
    Rationale and field list: references/plans/dual-descriptor-divergence-2026-05-25.plan.md
    """
    if getattr(args, "self_update", False):
        return
    if not getattr(args, "description", None):
        return
    desc_path = Path(args.description).resolve()
    if not desc_path.exists():
        return
    # Probe the conventional sibling location used by --self.
    out = getattr(args, "output", None)
    project_root = Path(out).resolve() if out else desc_path.parent
    # The sibling lives at <project>/.github/agents/_build-description.json.
    candidates = [
        project_root / "_build-description.json",
        project_root / ".github" / "agents" / "_build-description.json",
        project_root.parent / "_build-description.json" if project_root.name == "agents" else None,
    ]
    sibling = next((c for c in candidates if c and c.exists() and c.resolve() != desc_path), None)
    if sibling is None:
        return

    try:
        import json as _json

        primary = _json.loads(desc_path.read_text(encoding="utf-8"))
        sibling_doc = _json.loads(sibling.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return

    diverging: list[str] = []
    for field in _DUAL_DESCRIPTOR_FIELDS:
        if primary.get(field) != sibling_doc.get(field):
            diverging.append(field)
    if not diverging:
        return

    print(
        f"[WARN] Dual descriptor detected; {len(diverging)} field(s) diverge "
        f"between\n  primary: {desc_path}\n  sibling: {sibling}\n"
        f"  divergent fields: {', '.join(diverging)}\n"
        f"  Resolution: align or remove the sibling. Primary is authoritative for this run.",
        file=sys.stderr,
    )
    try:
        log_dir = Path(__file__).resolve().parent / "tmp" / "daily-pipeline" / "dual-descriptor-events"
        log_dir.mkdir(parents=True, exist_ok=True)
        now_utc = datetime.now(UTC)
        project_label = primary.get("project_name") or project_root.name
        signature = f"{project_label}|{','.join(diverging)}"
        log_path = log_dir / f"{now_utc.strftime('%Y-%m-%d')}.md"
        if log_path.exists() and signature in log_path.read_text(encoding="utf-8"):
            return  # delta-only: same divergence already logged today
        header = (
            f"# Dual-Descriptor Events — {now_utc.strftime('%Y-%m-%d')}\n\n"
            "Append-only daily log of dual-descriptor advisories from "
            "`build_team.py --update --merge`. Each section records one run.\n"
        )
        section = [
            "",
            f"## {project_label} @ {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
            f"- primary: `{desc_path}`",
            f"- sibling: `{sibling}`",
            f"- divergent_fields: {', '.join(diverging)}",
            f"- signature: `{signature}`",
            "",
        ]
        if log_path.exists():
            log_path.write_text(log_path.read_text(encoding="utf-8") + "\n".join(section), encoding="utf-8")
        else:
            log_path.write_text(header + "\n".join(section), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - never block emit
        print(f"[WARN] could not persist dual-descriptor event: {exc}", file=sys.stderr)


def _persist_orphan_events(
    orphans: list[str],
    manifest: dict[str, Any],
    output_dir: Path,
) -> None:
    """F5: append the current orphan set to a daily-pipeline artefact.

    Delta-only on a (project_label, sorted_orphans) signature so the same
    orphan inventory does not produce repeat sections within one day.
    Best-effort; failure never blocks the build.
    Plan: references/plans/F5-orphan-files-lifecycle-2026-05-25.md
    """
    if not orphans:
        return
    try:
        log_dir = Path(__file__).resolve().parent / "tmp" / "daily-pipeline" / "orphan-events"
        log_dir.mkdir(parents=True, exist_ok=True)
        now_utc = datetime.now(UTC)
        project_label = manifest.get("project_name") or output_dir.name or "unknown"
        signature = f"{project_label}|{','.join(orphans)}"
        log_path = log_dir / f"{now_utc.strftime('%Y-%m-%d')}.md"
        if log_path.exists() and signature in log_path.read_text(encoding="utf-8"):
            return
        section = [
            "",
            f"## {project_label} @ {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
            f"- output_dir: `{output_dir}`",
            f"- orphan_count: {len(orphans)}",
            f"- signature: `{signature}`",
            "",
            "Orphan agent files (present on disk, not in current team config):",
            "",
        ]
        for name in orphans:
            section.append(f"- `{name}`")
        section.append("")
        section.append("Routing: `@cleanup` (delete if obsolete) or `@code-hygiene` (review). "
                       "Daily pipeline never auto-deletes — destructive action requires "
                       "orchestrator approval.")
        section.append("")
        if log_path.exists():
            log_path.write_text(log_path.read_text(encoding="utf-8") + "\n".join(section), encoding="utf-8")
        else:
            header = (
                f"# Orphan Agent Events — {now_utc.strftime('%Y-%m-%d')}\n\n"
                "Append-only daily log of orphaned agent files detected by "
                "`build_team.py --update`. Each section records one run.\n"
            )
            log_path.write_text(header + "\n".join(section), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - never block emit
        print(f"[WARN] could not persist orphan events: {exc}", file=sys.stderr)


def _persist_shrink_events(
    args: argparse.Namespace,
    result: emit.EmitResult,
    manifest: dict[str, Any],
    output_dir: Path,
) -> None:
    """D5: append shrink notices from this run to a daily log under the
    agentteams source tree's gitignored tmp/. Delta-only — no notices means no write.
    Never raises; logging is a best-effort side effect.
    """
    if not (args.update and args.merge and not args.dry_run and result.notices):
        return
    try:
        shrink_dir = Path(__file__).resolve().parent / "tmp" / "daily-pipeline" / "shrink-events"
        shrink_dir.mkdir(parents=True, exist_ok=True)
        now_utc = datetime.now(UTC)
        today = now_utc.strftime("%Y-%m-%d")
        project_label = manifest.get("project_name") or output_dir.name or "unknown"
        # F2: link this run's shrink notices to the timestamped backup
        # directory that emit just created (most-recent mtime under
        # <output>/.agentteams-backups/). Best-effort; missing backups
        # produce a "—" entry rather than blocking the log.
        backup_dir_str = "—"
        try:
            backups_root = output_dir / ".agentteams-backups"
            if backups_root.is_dir():
                latest_backup = max(
                    (p for p in backups_root.iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime,
                    default=None,
                )
                if latest_backup is not None:
                    backup_dir_str = str(latest_backup)
        except OSError:
            pass

        blocked_lines = []
        if getattr(result, "shrink_blocked", None):
            blocked_lines = [
                "",
                f"Blocked (shrink-policy=halt): {len(result.shrink_blocked)} file(s)",
                "",
            ]
            for path in result.shrink_blocked:
                blocked_lines.append(f"- BLOCKED: `{path}`")

        section = [
            "",
            f"## {project_label} @ {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
            f"- output_dir: `{output_dir}`",
            f"- backup_dir: `{backup_dir_str}`",
            f"- notices: {len(result.notices)}",
            f"- shrink_policy: `{getattr(args, 'shrink_policy', 'preserve')}`",
            "",
        ]
        for notice in result.notices:
            section.append(f"- {notice}")
        section.extend(blocked_lines)
        section.append("")

        log_path = shrink_dir / f"{today}.md"
        if log_path.exists():
            log_path.write_text(
                log_path.read_text(encoding="utf-8") + "\n".join(section),
                encoding="utf-8",
            )
        else:
            header = [
                f"# Fenced-Region Shrink Events — {today}",
                "",
                "Append-only daily log of fenced-region shrink notices emitted by "
                "`build_team.py --update --merge`. Each section records one run.",
                "",
            ]
            log_path.write_text("\n".join(header + section), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - never block emit
        print(f"[WARN] could not persist shrink events: {exc}", file=sys.stderr)


def _deprecated_build_team_entry(argv: list[str] | None = None) -> int:
    """Entry point for the legacy ``build-team`` console script.

    Emits a one-line deprecation notice to stderr then delegates to :func:`main`.
    The alias is retained through the 1.x series and will be removed at 2.0.
    """
    print(
        "warning: the 'build-team' command is a deprecated alias for "
        "'agentteams' and will be removed in agentteams 2.0. "
        "Switch your scripts to 'agentteams'.",
        file=sys.stderr,
    )
    return main(argv)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_option_combinations(parser, args)

    # -----------------------------------------------------------------------
    # --target-host-features: parse early so emitters can read the list.
    # Validation errors abort before any expensive work.
    # -----------------------------------------------------------------------
    try:
        from agentteams.host_features import parse_tokens as _parse_host_features
        args.host_features = _parse_host_features(getattr(args, "target_host_features", None))
    except Exception as exc:
        print(f"Error: --target-host-features: {exc}", file=sys.stderr)
        return 1

    # -----------------------------------------------------------------------
    # --fleet: run --update --merge across every workspace under a parent dir.
    # Re-enters this main() in-process per (workspace, target); the constructed
    # argv never includes --fleet, so there is no recursion.
    # -----------------------------------------------------------------------
    if getattr(args, "fleet", None) is not None:
        from agentteams import fleet as _fleet
        return _fleet.run_fleet(args, parser)

    # -----------------------------------------------------------------------
    # --capture-baseline / --check-baseline: standalone baseline ops.
    # Skip the generation pipeline; operate on an existing output tree.
    # -----------------------------------------------------------------------
    if args.capture_baseline or args.check_baseline:
        from agentteams import baseline as _baseline
        # Resolve the target tree: prefer --output, else --project, else CWD.
        target_dir = args.output or args.project or "."
        target_path = Path(target_dir).resolve()
        if not target_path.exists():
            print(f"Error: baseline target does not exist: {target_path}", file=sys.stderr)
            return 1
        label = args.baseline_label or (args.framework or "default")
        current = _baseline.capture(target_path, label=label)
        if args.capture_baseline:
            out_path = Path(args.capture_baseline)
            _baseline.write(current, out_path)
            print(f"  ✓  Baseline captured: {out_path} ({current['file_count']} files)")
            return 0
        if args.check_baseline:
            prior_path = Path(args.check_baseline)
            if not prior_path.exists():
                print(f"Error: baseline not found: {prior_path}", file=sys.stderr)
                return 1
            prior = _baseline.load(prior_path)
            d = _baseline.diff(prior, current)
            total = sum(len(d[k]) for k in ("added", "removed", "changed"))
            if total == 0:
                print(f"  ✓  Baseline match: {prior_path} ({current['file_count']} files)")
                return 0
            print(f"  ✗  Baseline drift: {prior_path}")
            for k in ("added", "removed", "changed"):
                for p in d[k]:
                    print(f"     {k:8s} {p}")
            return 2

    # -----------------------------------------------------------------------
    # --add-fence-markers: standalone file-retrofit (no description needed).
    # Plan 4 of the W21 --update improvements.
    # -----------------------------------------------------------------------
    if args.add_fence_markers is not None:
        from agentteams.fence_inject import inject_fence_markers
        target_path = Path(args.add_fence_markers)
        if not target_path.exists():
            print(f"Error: file not found: {target_path}", file=sys.stderr)
            return 1
        if args.add_fence_markers_in_place and not args.yes:
            print(
                "Error: --in-place requires --yes (this rewrites the file; a "
                "backup is created first).",
                file=sys.stderr,
            )
            return 1
        try:
            mode = "in-place" if args.add_fence_markers_in_place else "sidecar"
            result = inject_fence_markers(
                target_path, mode=mode,
                confirm_in_place=args.add_fence_markers_in_place,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not result.injected:
            print(f"  ✓  Already fenced; no changes: {target_path}")
        elif mode == "sidecar":
            print(
                f"  ✓  Wrote sidecar with fence id {result.fence_id!r}: "
                f"{result.output_path}"
            )
        else:
            print(
                f"  ✓  Rewrote in place with fence id {result.fence_id!r}; "
                f"backup at {result.backup_path}"
            )
        return 0

    # -----------------------------------------------------------------------
    # --self: redirect to the module's own build description
    # -----------------------------------------------------------------------
    if args.self_update:
        # External-output guard runs FIRST — it does not depend on the
        # self-description existing, and refusing to write into a foreign
        # repository is the more meaningful error in that scenario. (Also
        # makes the test hermetic on CI runners where the self-description
        # is gitignored and therefore absent.)
        if args.output and not args.dry_run:
            try:
                resolved_output = Path(args.output).resolve()
                module_root = _SCRIPT_DIR.resolve()
                output_inside_module = (
                    resolved_output == module_root
                    or resolved_output.is_relative_to(module_root)
                )
            except (OSError, ValueError):
                output_inside_module = False
            if not output_inside_module and not args.allow_external_self_output:
                print(
                    "Error: --self with an --output path outside the AgentTeamsModule "
                    "source tree is refused to prevent self-maintenance artifacts from "
                    "being written into consumer repositories.\n"
                    f"  module root:    {module_root}\n"
                    f"  requested out:  {resolved_output}\n"
                    "If this is intentional, pass --allow-external-self-output.",
                    file=sys.stderr,
                )
                return 1
        self_desc = _SCRIPT_DIR / ".github" / "agents" / "_build-description.json"
        if not self_desc.exists():
            print(f"Error: Self-description not found at {self_desc}", file=sys.stderr)
            return 1
        args.description = str(self_desc)
        args.project = str(_SCRIPT_DIR)
        if not args.output:
            args.output = str(_SCRIPT_DIR / ".github" / "agents")
        print(f"Self-maintenance mode: using {self_desc.name}")

    strict_manual_placeholders = _resolve_strict_manual_mode(
        strict_arg=args.strict_manual_placeholders,
        self_update=args.self_update,
    )

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
        # Mode mutual exclusion: at most one of {check, refresh, merge}.
        bridge_mode_flags = sum(
            (bool(args.bridge_check), bool(args.bridge_refresh), bool(args.bridge_merge))
        )
        if bridge_mode_flags > 1:
            parser.error(
                "--bridge-check, --bridge-refresh, and --bridge-merge are "
                "mutually exclusive; pass at most one."
            )
        return _run_bridge(
            source_dir=Path(args.bridge_from).resolve(),
            source_framework=args.bridge_source_framework,
            target_framework=args.framework,
            output=Path(args.output).resolve() if args.output else None,
            dry_run=args.dry_run,
            overwrite=(args.overwrite or args.bridge_refresh),
            check_only=args.bridge_check,
            merge_only=args.bridge_merge,
            emit_skills=not args.bridge_no_skills,
            host_features=getattr(args, "host_features", []) or [],
        )

    if not args.description:
        parser.error("--description is required (or use --self for self-maintenance)")

    _check_dual_descriptor(args)

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
    _apply_placeholder_policy(manifest, strict_manual_placeholders=strict_manual_placeholders)
    # Host-feature subselectors (Phase 0): default [] preserves existing emission.
    manifest["host_features"] = list(getattr(args, "host_features", []) or [])
    if manifest["host_features"]:
        print(f"  Host features: {', '.join(manifest['host_features'])}")

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
    # Step 4·adopt: --adopt-orphans — register pre-existing custom agent files
    # into the roster before rendering, so the orchestrator declares them. Must
    # run before _build_final_rendered. Never adds to output_files (their files
    # are preserved, not regenerated). Effective only when the orchestrator is
    # (re)rendered — i.e. with --overwrite/--migrate (see flag help).
    # -----------------------------------------------------------------------
    if getattr(args, "adopt_orphans", False) and output_dir.exists():
        _suffix = ".agent.md"
        # "Planned" = agent files this build will EMIT (from output_files), not
        # the roster — so legitimately-generated-but-non-roster files
        # (team-builder, content-enricher) are not mistaken for orphans.
        _emitted = {
            Path(f["path"]).name[: -len(_suffix)]
            for f in manifest.get("output_files", [])
            if isinstance(f, dict) and str(f.get("path", "")).endswith(_suffix)
        }
        # Legacy tool-<slug>.agent.md files are migrated to docs/skills, not
        # adopted as agents — never pull them back into the roster.
        _tool_doc_slugs = {ta["slug"] for ta in manifest.get("tool_agents", [])}
        _orphan_slugs = sorted(
            p.name[: -len(_suffix)]
            for p in output_dir.glob("*.agent.md")
            if p.name[: -len(_suffix)] not in _emitted
            and p.name[: -len(_suffix)] not in _tool_doc_slugs
        )
        _adopted = analyze.adopt_orphan_agents(manifest, _orphan_slugs)
        if _adopted:
            print(f"  Adopted {len(_adopted)} orphan agent(s) into roster: {', '.join(_adopted)}")
        else:
            print("  --adopt-orphans: no orphan agent files to adopt.")

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
            security_gate._assert_destructive_action_allowed(output_dir, action="restore-backup")
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
        # T3a.2 v4: pass the current manifest's expected agent-file set so the
        # scanner can skip orphans that the build_team orphan advisory already
        # surfaces separately.
        expected = {
            Path(f["path"]).name
            for f in manifest.get("output_files", [])
            if isinstance(f, dict) and str(f.get("path", "")).endswith(".agent.md")
        }
        report = scan.scan_directory(output_dir, expected_agent_names=expected or None)
        scan.print_scan_report(report)
        return 1 if report.has_issues else 0

    # -----------------------------------------------------------------------
    # Step 4b.2: Handle --check-budget (3.1 + 3.4 efficiency lints)
    # -----------------------------------------------------------------------
    if args.check_budget:
        from agentteams import budget
        breport = budget.scan_directory(output_dir)
        budget.print_report(breport)
        return 1 if breport.has_failures else 0

    # -----------------------------------------------------------------------
    # Step 4c: Handle memory-index utility modes (no template rendering)
    # -----------------------------------------------------------------------
    if args.refresh_index:
        try:
            return _run_refresh_index(manifest, output_dir)
        except (OSError, MemoryIndexError) as exc:
            print(f"Memory index refresh failed: {exc}", file=sys.stderr)
            return 1

    if args.query_index:
        try:
            return _run_query_index(
                manifest, output_dir, args.query_index, args.query_k,
                strategy=args.query_strategy,
            )
        except (OSError, MemoryIndexError) as exc:
            print(f"Memory index query failed: {exc}", file=sys.stderr)
            return 1

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

    # AI bad-habits catalog placeholder (single wiring site — the main build
    # path only; convert/interop/bridge copy references/ verbatim and never
    # render this template). Static catalog, offline, network-free.
    from agentteams import ai_bad_habits as _ai_bad_habits

    manifest["auto_resolved_placeholders"].update(
        _ai_bad_habits.build_catalog_placeholders()
    )

    # Step 4d.2: Framework-watch placeholders (Claude Code etc.).
    # Offline by default so consumer repos do not need network; the
    # daily-pipeline refreshes the snapshot via the research stage.
    try:
        from agentteams import framework_research as _framework_research

        framework_placeholders = _framework_research.build_framework_placeholders(
            output_dir=output_dir,
            offline=True,
        )
        manifest["auto_resolved_placeholders"].update(framework_placeholders)
    except Exception as exc:  # pragma: no cover - never block build on research stage
        print(f"[WARN] framework-watch placeholders unavailable: {exc}", file=sys.stderr)

    if not args.check and not args.dry_run:
        try:
            security_gate._assert_security_intelligence_fresh(security_placeholders, output_dir=output_dir)
        except RuntimeError as exc:
            print(f"Security gate blocked write path: {exc}", file=sys.stderr)
            return 1

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
        except FileNotFoundError:
            sdreport = None  # no build-log — structural diff not available

        # --------------------------------------------------------------
        # P0 — Option C render-faithful reconciliation (D1, R1, R1b, R1c).
        #
        # `compute_structural_diff` promotes every unchanged file to drifted
        # whenever the build-log fingerprint is stale (mismatch or algo-version
        # bump). Without rendering, `--check` cannot tell the difference
        # between a real manifest delta and a baseline-only delta. To stay
        # consistent with what `--update` actually writes, we render the team
        # the same way `--update` does and run `refine_manifest_promotion`
        # against `_content_matches` — but only when the fast-path predicate
        # below fires. Outside the predicate, rendering would be wasted work
        # because `refine_manifest_promotion` would be a no-op.
        # --------------------------------------------------------------
        if sdreport is not None and sdreport.manifest_changed and any(
            e.get("_reason") in drift._MANIFEST_PROMOTION_REASONS
            for e in sdreport.drifted_files
        ):
            check_final = _build_final_rendered(manifest, adapter, project_name)
            check_security_refresh = {
                "references/security-vulnerability-watch.reference.md",
                "references/security-vulnerability-watch.json",
            }
            if adapter.handoff_delivery_mode() == "manifest":
                check_security_refresh.add("references/runtime-handoffs.json")

            drift.refine_manifest_promotion(
                sdreport,
                _make_content_matches(output_dir, dict(check_final), check_security_refresh),
            )

        # Print structural diff under the same condition `--update` uses
        # (R1c — print on has_changes, not just on added/removed).
        if sdreport is not None and sdreport.has_changes:
            print(f"\nStructural changes for {project_name!r}:")
            drift.print_structural_diff_report(sdreport)
        has_any = dreport.has_drift or (sdreport.has_changes if sdreport is not None else False)
        return 1 if has_any else 0

    # -----------------------------------------------------------------------
    # Step 5: Render
    # -----------------------------------------------------------------------
    print("Rendering templates...")

    # Compute template hashes for drift detection
    template_hashes = render.compute_template_hashes(manifest, templates_dir=TEMPLATES_DIR)

    # Apply framework-specific post-processing; runtime-handoffs and pipeline
    # graph (Step 5c) are appended by _build_final_rendered.
    final_rendered = _build_final_rendered(manifest, adapter, project_name)

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

        # Destructive-action gate. A real (non-dry-run, non-merge) --update
        # overwrites files, so clear the gate BEFORE printing the structural
        # report or performing any side effect (backup, migration, "Writing
        # ..."). A team that cannot be updated must not show a misleading
        # drift report or create a spurious backup. compute_structural_diff
        # above is read-only, so evaluating the gate here is safe.
        if not args.dry_run and args.overwrite:
            try:
                security_gate._assert_destructive_action_allowed(output_dir, action="overwrite")
            except RuntimeError as exc:
                print(
                    f"Security gate blocked overwrite update: {exc}\n"
                    "  Tip: omit --overwrite (or use --merge explicitly) to preserve "
                    "your customizations without requiring a security clearance.",
                    file=sys.stderr,
                )
                return 1

        # Always refresh security intelligence references during --update,
        # even when template/content drift is otherwise empty.
        security_refresh_paths = {
            "references/security-vulnerability-watch.reference.md",
            "references/security-vulnerability-watch.json",
        }
        if adapter.handoff_delivery_mode() == "manifest":
            security_refresh_paths.add("references/runtime-handoffs.json")

        # Content-aware manifest-drift refinement (Defect 2 Option A).
        # compute_structural_diff promotes EVERY unchanged file to drifted when
        # the build-log manifest_fingerprint is merely stale (e.g. after a
        # generator/description-shape change). Demote a fingerprint-only
        # promotion when its freshly-rendered, manual-preserved content is
        # byte-identical to disk, so --update stops re-rendering the whole team
        # for a no-op. security_refresh_paths (the only files with per-render
        # volatile content, and force-written every --update anyway) and
        # missing files are never demoted.
        drift.refine_manifest_promotion(
            sdreport,
            _make_content_matches(output_dir, dict(final_rendered), security_refresh_paths),
        )

        # ------------------------------------------------------------------
        # Observable baseline self-heal (P0 — drift trust).
        # When `compute_structural_diff` flagged manifest_changed (fingerprint
        # delta or algo-version bump) but `refine_manifest_promotion` demoted
        # every fingerprint-only promotion AND no real structural / template /
        # team-membership drift survives, the prior build-log was merely stale
        # — the team is byte-identical to what the current manifest would
        # render. The subsequent `_write_run_log` call (gated by the
        # destructive-action gate and `result.success`) rewrites the build-log
        # with the fresh fingerprint + algo_version, healing the baseline for
        # future runs. We surface the heal explicitly so it is observable in
        # logs and testable. Heal is *not* asserted (and the print is
        # suppressed) when:
        #   - `removed_files` is non-empty (the file-set genuinely shrank;
        #     resolution belongs to `--update --prune`, not heal)
        #   - any surviving drifted entry has a non-manifest-promotion reason
        #     (template / structural / team-membership drift is real work)
        #   - any added_files / team_membership_changed signal is present
        # The heal *write* is implicit (via `_write_run_log` at the end of the
        # update path). It is gated by the same destructive-action gate as
        # every other --update write; a blocked or failed update never heals.
        # ------------------------------------------------------------------
        heal_converged = False
        if sdreport.manifest_changed:
            surviving_manifest_promotions = [
                e for e in sdreport.drifted_files
                if e.get("_reason") in drift._MANIFEST_PROMOTION_REASONS
            ]
            real_surviving = [
                e for e in surviving_manifest_promotions
                if e["path"] not in security_refresh_paths
            ]
            non_manifest_drift = [
                e for e in sdreport.drifted_files
                if e.get("_reason") not in drift._MANIFEST_PROMOTION_REASONS
            ]
            heal_converged = (
                not real_surviving
                and not non_manifest_drift
                and not sdreport.added_files
                and not sdreport.removed_files
                and not sdreport.team_membership_changed
            )

        if not sdreport.has_changes and not sdreport.removed_files:
            print("No structural or content changes detected; refreshing security intelligence references.")
        else:
            print(f"\nStructural update for {project_name!r}:")
            drift.print_structural_diff_report(sdreport)

        # Build the update set from the structural diff + security refresh files
        update_paths: set[str] = {f["path"] for f in sdreport.update_files}
        update_paths.update(security_refresh_paths)

        # Recover missing expected files even when build-log/template drift does not
        # surface them (for example, files deleted manually after the last build).
        expected_paths = {rel_path for rel_path, _ in final_rendered}
        missing_expected_paths = {
            rel_path
            for rel_path in expected_paths
            if not emit._resolve_path(output_dir, rel_path).exists()
        }
        if missing_expected_paths:
            print(
                "Detected missing expected output file(s); restoring during update: "
                f"{len(missing_expected_paths)}"
            )
            update_paths.update(missing_expected_paths)

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

        # Migrate away legacy tool-*.agent.md files: tools are now emitted as
        # reference/skill documents, never agents. Runs before the converged
        # early-exit (and before the orphan advisory) so migration happens even
        # when there is no content drift. Overwrite deletes (after backup);
        # merge leaves a notice.
        _removed_tool_agents, _stale_notices = _remove_stale_tool_agents(
            manifest, output_dir, framework_id,
            overwrite=args.overwrite, dry_run=args.dry_run,
        )
        for _n in _stale_notices:
            print(f"  ⚠  {_n}", file=sys.stderr)
        if _removed_tool_agents and not args.dry_run:
            print(
                f"  ✓  Removed {len(_removed_tool_agents)} legacy tool-agent file(s) "
                "(tools are now reference/skill documents)."
            )

        if not update_rendered:
            # RA1: a converged team (manifest_changed but every fingerprint-only
            # promotion demoted, no real drift) must still heal its stale
            # baseline here — otherwise convergence depends on the incidental
            # fact that security_refresh_paths kept update_rendered non-empty.
            # The destructive-action gate already cleared above; a blocked
            # update returned before this point. Dry-run never heals.
            if heal_converged and not args.dry_run:
                _heal_build_log_baseline(output_dir, manifest)
                try:
                    _write_delivery_receipt(manifest, output_dir)
                except (OSError, DeliveryReceiptError) as exc:
                    print(
                        f"  !  Delivery receipt write failed (build-log healed): {exc}",
                        file=sys.stderr,
                    )
                try:
                    _write_eval_suite(manifest, output_dir)
                except (OSError, EvalSuiteError) as exc:
                    print(
                        f"  !  Eval suite write failed (build-log healed): {exc}",
                        file=sys.stderr,
                    )
                try:
                    _write_memory_index(manifest, output_dir)
                except (OSError, MemoryIndexError) as exc:
                    print(
                        f"  !  Memory index write failed: {exc}",
                        file=sys.stderr,
                    )
                if args.cost_routing:
                    try:
                        _write_model_routing(manifest, output_dir)
                    except (OSError, ModelRoutingError) as exc:
                        print(
                            f"  !  Model-routing write failed: {exc}",
                            file=sys.stderr,
                        )
                print(
                    "  ✓  Healed build-log baseline (no material drift; "
                    "fingerprint refreshed)."
                )
                return 0
            print("Changes detected but no matching rendered files — already up to date.")
            return 0

        # Always regenerate the team topology graph on every update
        graph_rel_path = "references/pipeline-graph.md"
        if not any(p == graph_rel_path for p, _ in update_rendered):
            from agentteams import graph as _graph
            graph_update_content = _graph.generate_graph_document(
                dict(final_rendered), project_name=project_name
            )
            update_rendered.append((graph_rel_path, graph_update_content))

        # --prune: delete removed files (with confirmation unless --yes)
        if args.prune and sdreport.removed_files:
            rc = _prune_removed_files(sdreport.removed_files, output_dir, args.yes, args.dry_run)
            if rc != 0:
                return rc

        # Orphan-agent advisory: agent files present on disk that the current
        # team no longer emits. `--prune` above handles removals tracked since
        # the last build; these are older orphans the build log no longer
        # records, so without this advisory they accumulate invisibly.
        _emitted_agent_names = {
            Path(p).name for p, _ in final_rendered if p.endswith(".agent.md")
        }
        # Adopted orphans (--adopt-orphans) are deliberately not emitted but are
        # now roster members — don't re-report them as orphaned.
        _adopted_names = {f"{s}.agent.md" for s in manifest.get("adopted_agents", [])}
        # Tool docs are never agents — exclude any tool-<slug>.agent.md whose tool
        # is in the current team from the orphan scan (handled by migration above).
        _tool_doc_agent_names = {
            f"{ta['slug']}.agent.md" for ta in manifest.get("tool_agents", [])
        }
        _orphan_agents = sorted(
            f.name for f in output_dir.glob("*.agent.md")
            if f.name not in _emitted_agent_names
            and f.name not in _adopted_names
            and f.name not in _tool_doc_agent_names
        )
        if _orphan_agents:
            print(
                f"\n  ⚠  {len(_orphan_agents)} agent file(s) on disk are not part "
                "of the current team (orphaned by past team-config changes):",
                file=sys.stderr,
            )
            for _name in _orphan_agents:
                print(f"       {_name}", file=sys.stderr)
            print(
                "     These are not updated by --update. Review and delete if obsolete.",
                file=sys.stderr,
            )
            _persist_orphan_events(_orphan_agents, manifest, output_dir)

        print(f"\nWriting {len(update_rendered)} file(s)...")

        # Back up BEFORE migration so the backup captures pre-migration state
        backup_path = None
        if not args.dry_run and not args.no_backup:
            backup_result = emit.backup_output_dir(
                output_dir,
                files_to_backup=[rel for rel, _ in update_rendered],
                reason="overwrite-mode" if args.overwrite else "pre-update",
                framework=framework_id,
                description_path=str(args.description) if args.description else None,
            )
            backup_path = backup_result.backup_path

        # Migrate any inline log tables to CSV before writing
        if not args.dry_run:
            adjacent_repos_md = emit._resolve_path(output_dir, "references/adjacent-repos.md")
            mresult = liaison_logs.migrate_inline_logs(adjacent_repos_md, output_dir / "references")
            if mresult.rows_moved > 0:
                print(
                    f"  ✓  Migrated {mresult.changelog_rows_moved} changelog row(s) and "
                    f"{mresult.coord_log_rows_moved} coordination row(s) to CSV files."
                )

        result = emit.emit_all(
            update_rendered,
            output_dir=output_dir,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            merge=not args.overwrite,
            yes=args.yes,
            shrink_policy=getattr(args, "shrink_policy", "preserve"),
            backup_path=backup_path,
            auto_fence_legacy=not getattr(args, "no_add_fence_markers", False),
        )
        emit.print_summary(result, manifest)
        _persist_shrink_events(args, result, manifest, output_dir)
        if args.dry_run and result.dry_run_report is not None:
            emit.print_dry_run_report(
                result, manifest,
                fmt="json" if args.json else "text",
            )

        # ------------------------------------------------------------------
        # Post-generation audit (--update path)
        # ------------------------------------------------------------------
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
            _write_run_log(manifest, result, output_dir, template_hashes)
            # Receipt write AFTER build-log (R3 — "heal first, attest
            # second"). Same gate as the log: only on real, successful runs.
            try:
                _write_delivery_receipt(manifest, output_dir)
            except (OSError, DeliveryReceiptError) as exc:
                # Heal still happened. Surface the failure but do not abort
                # the update; the next --update will re-emit the receipt.
                print(
                    f"  !  Delivery receipt write failed (build-log healed): {exc}",
                    file=sys.stderr,
                )
            try:
                _write_eval_suite(manifest, output_dir)
            except (OSError, EvalSuiteError) as exc:
                # Non-fatal, same contract as the receipt: next --update
                # re-emits the suite.
                print(
                    f"  !  Eval suite write failed (build-log healed): {exc}",
                    file=sys.stderr,
                )
            try:
                _write_memory_index(manifest, output_dir)
            except (OSError, MemoryIndexError) as exc:
                print(
                    f"  !  Memory index write failed: {exc}",
                    file=sys.stderr,
                )
            if args.cost_routing:
                try:
                    _write_model_routing(manifest, output_dir)
                except (OSError, ModelRoutingError) as exc:
                    print(
                        f"  !  Model-routing write failed: {exc}",
                        file=sys.stderr,
                    )
            if heal_converged:
                print(
                    "  ✓  Healed build-log baseline (no material drift; "
                    "fingerprint refreshed)."
                )
        return _finalize_exit_code(result, args)

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

    # Destructive-action gate must clear BEFORE backup/migration so a blocked
    # overwrite produces no spurious backup or log migration. A --migrate-driven
    # overwrite is exempt: --migrate supplies its own safety (the
    # pre-fencing-snapshot git tag + --revert-migration). The exemption is
    # gated on security_gate.migrate_exemption_active() — module state set ONLY
    # by _run_migrate around its main() re-invocation, so the bypass is not
    # reachable from the CLI.
    if not args.dry_run and args.overwrite and not security_gate.migrate_exemption_active():
        try:
            security_gate._assert_destructive_action_allowed(output_dir, action="overwrite")
        except RuntimeError as exc:
            print(f"Security gate blocked overwrite: {exc}", file=sys.stderr)
            return 1
    elif not args.dry_run and args.overwrite and security_gate.migrate_exemption_active():
        print(
            "  ℹ  Security-decision gate exempted for --migrate "
            "(rollback point is the 'pre-fencing-snapshot' tag).",
            file=sys.stderr,
        )

    backup_path = None
    if not args.dry_run and not args.no_backup and (args.overwrite or args.merge):
        backup_result = emit.backup_output_dir(
            output_dir,
            files_to_backup=[rel for rel, _ in final_rendered],
            reason="pre-overwrite" if args.overwrite else "merge-overwrite-fenced",
            framework=framework_id,
            description_path=str(args.description) if args.description else None,
        )
        backup_path = backup_result.backup_path

    # Migrate any inline log tables to CSV before a merge run
    if args.merge and not args.dry_run:
        adjacent_repos_md = emit._resolve_path(output_dir, "references/adjacent-repos.md")
        mresult = liaison_logs.migrate_inline_logs(adjacent_repos_md, output_dir / "references")
        if mresult.rows_moved > 0:
            print(
                f"  ✓  Migrated {mresult.changelog_rows_moved} changelog row(s) and "
                f"{mresult.coord_log_rows_moved} coordination row(s) to CSV files."
            )

    result = emit.emit_all(
        final_rendered,
        output_dir=output_dir,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        merge=args.merge,
        yes=args.yes,
        shrink_policy=getattr(args, "shrink_policy", "preserve"),
        backup_path=backup_path,
        auto_fence_legacy=not getattr(args, "no_add_fence_markers", False),
    )
    emit.print_summary(result, manifest)
    _persist_shrink_events(args, result, manifest, output_dir)

    if args.dry_run and result.dry_run_report is not None:
        emit.print_dry_run_report(
            result, manifest,
            fmt="json" if args.json else "text",
        )

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
        # F2 increment 1b: emit the framework-neutral eval suite on the
        # generate path too (increment 1 was --update-only). Safe now that
        # RCC2 unified the render pipeline. Non-fatal, same contract as on
        # --update; eval-suite.json is drift-excluded by construction so it
        # does not perturb snapshot comparisons (.md-only).
        try:
            _write_eval_suite(manifest, output_dir)
        except (OSError, EvalSuiteError) as exc:
            print(
                f"  !  Eval suite write failed (team generated): {exc}",
                file=sys.stderr,
            )
        try:
            _write_memory_index(manifest, output_dir)
        except (OSError, MemoryIndexError) as exc:
            print(
                f"  !  Memory index write failed: {exc}",
                file=sys.stderr,
            )
        if args.cost_routing:
            try:
                _write_model_routing(manifest, output_dir)
            except (OSError, ModelRoutingError) as exc:
                print(
                    f"  !  Model-routing write failed: {exc}",
                    file=sys.stderr,
                )

    return _finalize_exit_code(result, args)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finalize_exit_code(result: emit.EmitResult, args: argparse.Namespace) -> int:
    """Return the final exit code, applying optional fail-on-legacy-skip gate.

    A successful emit returns 0 by default. When ``--fail-on-legacy-skip`` is
    set and any files were skipped due to missing fence markers, the run is
    promoted to non-zero so CI can enforce template propagation.
    """
    if not result.success:
        return 1
    if getattr(args, "fail_on_legacy_skip", False) and result.skipped_legacy:
        print(
            f"\nExit 1: --fail-on-legacy-skip set and "
            f"{len(result.skipped_legacy)} legacy file(s) skipped.",
            file=sys.stderr,
        )
        return 1
    return 0






# Bridge --output is interpreted as the *repo root*, not the agents directory.
# When users pass a known agents-dir suffix (intuiting that --output means
# "where agent files go"), normalize by stripping the suffix and warn so the
# bridge does not produce nested .github/.github/... layouts.










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
        security_gate._assert_destructive_action_allowed(output_dir, action="prune")
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


def _attempt_auto_correct(
    *,
    output_dir: Path,
    manifest: dict[str, Any],
    audit_result: Any,
) -> Any:
    """Invoke standalone Copilot CLI remediation and rerun the audit.

    Args:
        output_dir: Root agents directory for the generated team.
        manifest: Team manifest from analyze.build_manifest().
        audit_result: Initial audit result with findings to remediate.

    Returns:
        The rerun audit result if remediation succeeded, otherwise the original audit result.
    """
    # When a concrete audit type cannot be imported at module-load time,
    # validate minimal shape at runtime before handing off.
    assert isinstance(manifest, dict), "manifest must be a dict"
    assert hasattr(audit_result, "has_errors") and hasattr(audit_result, "has_warnings"), (
        "audit_result must expose has_errors/has_warnings"
    )

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
























"""Path (relative to ``output_dir``) where ``--update`` writes the delivery
receipt on success.

The receipt is an ATTESTATION, not a baseline: it is intentionally not part of
``output_files_map``, ``template_hashes``, or ``file_hashes`` in the build-log,
and the drift detector never reads it. See
``schemas/delivery-receipt.schema.json`` and
``docs_src/delivery-procedure.md``.
"""




































def _heal_build_log_baseline(output_dir: Path, manifest: dict) -> None:
    """Refresh only the fingerprint fields of an existing build-log (RA1).

    Used on the converged ``--update`` path where there is nothing to (re)write
    but the stored ``manifest_fingerprint`` / ``fingerprint_algo_version`` are
    stale. Patches just those two fields in place so the next ``--update``
    sees a matching baseline, while preserving ``file_hashes``,
    ``output_files_map`` and every other field (a full ``_write_run_log`` with
    an empty result would wipe ``file_hashes`` and break user-customization
    detection). No-op if the build-log is absent — there is no baseline to heal.
    """
    from agentteams import drift as _drift

    log_path = output_dir / "references" / "build-log.json"
    if not log_path.exists():
        return
    try:
        log = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    log["manifest_fingerprint"] = _drift.compute_manifest_fingerprint(manifest)
    log["fingerprint_algo_version"] = _drift.FINGERPRINT_ALGO_VERSION
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")


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
        "fingerprint_algo_version": _drift.FINGERPRINT_ALGO_VERSION,
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

    # Create the snapshot tag at HEAD. A stale tag from a prior migration is
    # moved to current HEAD when --yes is set — the rollback point for THIS
    # migration is the current state — instead of hard-failing on the collision.
    migrate_yes = "--yes" in original_argv or "-y" in original_argv
    rc3, _, tag_err = _git(["tag", _MIGRATION_TAG], project_dir)
    if rc3 != 0:
        is_collision = "already exists" in tag_err or "already a tag" in tag_err.lower()
        if is_collision and migrate_yes:
            rc_mv, _, mv_err = _git(["tag", "-f", _MIGRATION_TAG], project_dir)
            if rc_mv != 0:
                print(f"Error moving snapshot tag: {mv_err}", file=sys.stderr)
                return 1
            print(
                f"  ⚠  Existing '{_MIGRATION_TAG}' tag from a prior migration "
                "moved to current HEAD."
            )
        elif is_collision:
            print(
                f"Error: tag '{_MIGRATION_TAG}' already exists in {project_dir}. "
                "Re-run with --yes to move it to the current HEAD, or delete it "
                "first with: git tag -d pre-fencing-snapshot",
                file=sys.stderr,
            )
            return 1
        else:
            print(f"Error creating snapshot tag: {tag_err}", file=sys.stderr)
            return 1

    print(f"  ✓  Snapshot tag '{_MIGRATION_TAG}' set at HEAD.")

    # Re-invoke main() with --overwrite replacing --migrate; force --yes.
    # Set the in-process gate-exemption flag so the security gate skips this
    # overwrite (the snapshot tag is the safety net). The flag is NEVER set
    # via the CLI — set only here, scoped by try/finally, so a direct user
    # invocation cannot reach the exemption path.
    new_argv = [a for a in original_argv if a not in ("--migrate", "--revert-migration")]
    if "--overwrite" not in new_argv:
        new_argv.append("--overwrite")
    if "--yes" not in new_argv and "-y" not in new_argv:
        new_argv.append("--yes")

    print("  Running --overwrite migration...\n")
    security_gate.set_migrate_exemption(True)
    try:
        rc_emit = main(new_argv)
    finally:
        security_gate.set_migrate_exemption(False)

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

    # --revert-migration is a recovery operation: it restores a deliberate
    # safety checkpoint (the pre-fencing-snapshot tag). It is intentionally NOT
    # gated by the destructive-action security check — gating the rollback path
    # would leave a failed --migrate effectively unrecoverable via the CLI.
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
