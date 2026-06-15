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
    --framework    NAME   Target framework: copilot-vscode (default), copilot-cli, claude, goose
    --output       DIR    Output directory for agent files (default: framework-specific agents
                          directory under <project>: .github/agents/ for copilot-vscode,
                          .github/copilot/ for copilot-cli, .claude/agents/ for claude,
                          .goose/recipes/ for goose)
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
# main + entry points moved to agentteams/cli/app.py (CH-07); re-exported so
# the `agentteams`/`build-team` console scripts, _run_migrate's main()
# re-invocation, agentteams.man, and tests resolve them in build_team.
from agentteams.cli.app import (  # noqa: E402
    _deprecated_build_team_entry,
    _finalize_exit_code,
    main,
)
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






# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------







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
        # v1.3 addition — per-file hashes for user-customization detection.
        # Include `unchanged` so the baseline covers the FULL output set, not just
        # this run's write-set — otherwise an incremental --update (which writes
        # few files) would leave `--verify-integrity` checking almost nothing.
        "file_hashes": _compute_file_hashes(
            result.written + result.merged + result.unchanged, output_dir
        ),
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
