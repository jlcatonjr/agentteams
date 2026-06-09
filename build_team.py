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
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.claude import ClaudeAdapter

try:
    from importlib.metadata import version as _pkg_version, PackageNotFoundError
    try:
        __version__ = _pkg_version("agentteams")
    except PackageNotFoundError:
        # Running from a source checkout without an installed dist
        __version__ = "0.0.0+local"
except ImportError:  # pragma: no cover
    __version__ = "0.0.0+local"

FRAMEWORKS = {
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
    "claude": ClaudeAdapter,
}

TEMPLATES_DIR = _SCRIPT_DIR / "agentteams" / "templates"

_SECURITY_DECISION_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "legacy": frozenset(
        {
            "timestamp",
            "requesting_agent",
            "action_reviewed",
            "verdict",
            "conditions",
            "conditions_verified",
        }
    ),
    "current": frozenset(
        {
            "date",
            "plan_slug",
            "step",
            "decision",
            "status",
            "conditions",
            "conditions_verified",
            "evidence",
            "owner",
        }
    ),
}

_SECURITY_WAIVER_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "timestamp",
        "waiver_id",
        "action_reviewed",
        "expires_at",
        "max_uses",
        "uses",
        "approver",
        "ticket_id",
        "reason_code",
        "conditions_verified",
        "signature",
    }
)

_SECURITY_INTEL_TTL_HOURS = 24


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
    parser.add_argument(
        "--json",
        action="store_true",
        help="With --dry-run: emit the per-file action plan as a single JSON "
             "document on stdout (no-op without --dry-run).",
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
        "--cost-routing",
        action="store_true",
        help="OFF by default. When set, emit references/model-routing.json — a "
             "framework-neutral per-agent model-tier contract (governance agents "
             "-> 'cheap', producers/experts -> 'primary'). Generated agent files "
             "are unchanged either way; this only adds the opt-in contract artifact.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Re-render drifted files and emit new agents added to the taxonomy. "
             "Default behavior merges only template-fenced regions, preserving all "
             "user-authored content outside fence markers (same as --update --merge). "
             "Use --update --overwrite to fully re-render existing files, replacing "
             "user-authored content (requires security clearance). "
             "Preserves manually-filled {MANUAL:*} values from existing files. "
             "Removed agents are reported but not deleted (use --prune to remove them).",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Used with --update: also delete agent files that are no longer part of the team.",
    )
    parser.add_argument(
        "--adopt-orphans",
        action="store_true",
        dest="adopt_orphans",
        help="Register pre-existing agent files that the generated taxonomy does "
             "not produce (e.g. bespoke custom agents) into the team roster — the "
             "orchestrator's handoff list and domain routing — WITHOUT generating "
             "or overwriting their files. The opposite of --prune: integrate "
             "orphans instead of removing them. Requires the orchestrator to be "
             "(re)rendered, so use with --overwrite or --migrate (under --merge the "
             "orchestrator front matter is preserved and adoption would not surface).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for template drift and structural changes without writing any files "
             "(exit code 1 if drift or structural changes are detected)",
    )
    parser.add_argument(
        "--refresh-index",
        action="store_true",
        dest="refresh_index",
        help=(
            "Rebuild references/memory-index.json only (no template emit/update). "
            "Useful after editing work summaries or docs."
        ),
    )
    parser.add_argument(
        "--query-index",
        metavar="TEXT",
        dest="query_index",
        default=None,
        help=(
            "Query references/memory-index.json and print ranked hits. "
            "Requires a pre-existing index in the output directory."
        ),
    )
    parser.add_argument(
        "--query-k",
        metavar="N",
        dest="query_k",
        type=int,
        default=5,
        help="Number of ranked results to return with --query-index (default: 5).",
    )
    parser.add_argument(
        "--query-strategy",
        choices=["lexical", "vector"],
        default="lexical",
        dest="query_strategy",
        help=(
            "Query strategy for --query-index: 'lexical' (BM25, default) or 'vector' "
            "(cosine similarity, better for semantic/thematic matching)."
        ),
    )
    parser.add_argument(
        "--fail-on-legacy-skip",
        action="store_true",
        dest="fail_on_legacy_skip",
        help=(
            "Exit with non-zero status if --merge skipped any files due to "
            "missing fence markers (legacy files). Use in CI to enforce that "
            "template updates always propagate to downstream repositories."
        ),
    )
    parser.add_argument(
        "--no-add-fence-markers",
        action="store_true",
        dest="no_add_fence_markers",
        help=(
            "Opt OUT of the default behaviour where --update --merge (with "
            "--yes) auto-retrofits AGENTTEAMS `content` fence markers onto "
            "legacy (unfenced) files so their template region becomes mergeable "
            "instead of being skipped. Each retrofit is backed up first and the "
            "shrink-guard still suppresses material template shrinks, so the "
            "legacy body is recoverable. Pass this flag to keep the conservative "
            "skip-legacy behaviour (distinct from the standalone per-file "
            "`--add-fence-markers PATH` retrofit)."
        ),
    )
    parser.add_argument(
        "--scan-security",
        action="store_true",
        help="Scan generated agent files for security issues (PII, credentials, unresolved placeholders)",
    )
    parser.add_argument(
        "--check-budget",
        action="store_true",
        dest="check_budget",
        help=(
            "Audit live .agent.md files for token-budget overrun and "
            "prompt-cache prefix volatility. Read-only. Exits 1 on fail-class "
            "findings; 0 on warn-class only. Routes remediation to @agent-refactor."
        ),
    )
    parser.add_argument(
        "--self",
        action="store_true",
        dest="self_update",
        help="Operate on the module's own agent team using .github/agents/_build-description.json",
    )
    parser.add_argument(
        "--allow-external-self-output",
        action="store_true",
        dest="allow_external_self_output",
        help=(
            "Permit --self to write self-maintenance artifacts to an --output "
            "path outside the AgentTeamsModule source tree. Required to prevent "
            "accidental writes into consumer repositories."
        ),
    )
    parser.add_argument(
        "--post-audit",
        action="store_true",
        dest="post_audit",
        help=(
            "Run a post-generation audit after emit. Performs static checks "
            "(unresolved placeholders, YAML integrity, required-agent coverage) "
            "and, if the `gh` CLI is authenticated, an AI-powered conflict and "
            "presupposition review via GitHub Models (Auto model selection)."
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
    strict_group = parser.add_mutually_exclusive_group()
    strict_group.add_argument(
        "--strict-manual-placeholders",
        dest="strict_manual_placeholders",
        action="store_true",
        help=(
            "Preserve unresolved {MANUAL:*} placeholder tokens for core governance "
            "fields instead of auto-replacing them with explicit N/A defaults. "
            "Enabled by default in --self mode."
        ),
    )
    strict_group.add_argument(
        "--no-strict-manual-placeholders",
        dest="strict_manual_placeholders",
        action="store_false",
        help=(
            "Disable strict MANUAL placeholder preservation and apply usability "
            "defaults for optional governance placeholders."
        ),
    )
    parser.set_defaults(strict_manual_placeholders=None)
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
        help=(
            "Refresh bridge artifacts AND overwrite target-framework entry "
            "files (CLAUDE.md, .claude/agent-team.md, etc.). Destructive at "
            "the target — use --bridge-merge for non-destructive updates."
        ),
    )
    parser.add_argument(
        "--bridge-merge",
        action="store_true",
        dest="bridge_merge",
        help=(
            "Non-destructive update: regenerate bridge-internal artifacts, "
            "and for target-framework entry files only re-render content "
            "inside <!-- AGENTTEAMS-BRIDGE:BEGIN ... --> fences. Files "
            "lacking any bridge fence are skipped with notices in "
            "bridge-merge.report.md. First-time consumers should use "
            "--bridge-refresh."
        ),
    )
    parser.add_argument(
        "--bridge-no-skills",
        action="store_true",
        dest="bridge_no_skills",
        help=(
            "Suppress emission of .claude/skills/recall.md (Claude target "
            "only). The recall skill wraps `agentteams --query-index` for "
            "in-session memory-index retrieval; disable if your team manages "
            "skills separately."
        ),
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
        "--shrink-policy",
        choices=("preserve", "warn", "halt", "allow"),
        default="preserve",
        dest="shrink_policy",
        help=(
            "Behaviour when a fenced-region merge would lose concrete refs. "
            "'preserve' (default) keeps the existing enriched body for that "
            "fence and still updates non-shrinking fences (respectful, "
            "non-destructive); 'warn' writes the smaller body and saves a "
            ".lost.<sid>.md recovery sidecar; 'halt' refuses the whole-file "
            "write and lists the blocked file; 'allow' writes silently. Plan: "
            "references/plans/T2-D5-shrink-policy-2026-05-25.plan.md"
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
    parser.add_argument(
        "--add-fence-markers",
        metavar="PATH",
        dest="add_fence_markers",
        default=None,
        help=(
            "Retrofit canonical AGENTTEAMS:BEGIN/END fence markers around "
            "PATH's existing body so it becomes eligible for future merge-mode "
            "--update runs. Default mode writes a non-destructive sidecar "
            "<name>.fenced.md; pair with --in-place to rewrite PATH directly "
            "(requires --yes; creates a timestamped backup first). Idempotent "
            "on already-fenced files."
        ),
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        dest="add_fence_markers_in_place",
        help=(
            "With --add-fence-markers: rewrite the target file directly instead "
            "of producing a sidecar. Requires --yes; a timestamped backup is "
            "written under .agentteams-backups/ before the rewrite."
        ),
    )
    parser.add_argument(
        "--target-host-features",
        metavar="TOKENS",
        dest="target_host_features",
        default=None,
        help=(
            "Comma-separated host-feature subselectors gating opt-in emission. "
            "Tokens are <ns>:<feature>; ns is one of claude, copilot-vscode, "
            "copilot-cli, bridge:copilot-vscode-to-claude, etc. Examples: "
            "'bridge:copilot-vscode-to-claude:subagents,bridge:copilot-vscode-to-claude:hooks'. "
            "Default emission is unchanged when omitted."
        ),
    )
    parser.add_argument(
        "--capture-baseline",
        metavar="PATH",
        dest="capture_baseline",
        default=None,
        help=(
            "Capture a deterministic SHA-256 manifest of the output tree and "
            "write it to PATH (e.g., tests/baselines/<target>.json). Used by "
            "regression tests to detect emission drift across phases. Skips "
            "the normal generation pipeline."
        ),
    )
    parser.add_argument(
        "--baseline-label",
        metavar="LABEL",
        dest="baseline_label",
        default=None,
        help=(
            "Label embedded in the captured baseline manifest "
            "(default: --framework value)."
        ),
    )
    parser.add_argument(
        "--check-baseline",
        metavar="PATH",
        dest="check_baseline",
        default=None,
        help=(
            "Compare the current output tree against the baseline at PATH and "
            "exit non-zero on any diff. Lists added/removed/changed files."
        ),
    )

    # -- Fleet update: run --update --merge across every workspace under a dir --
    fleet_group = parser.add_argument_group("fleet update (multi-workspace)")
    fleet_group.add_argument(
        "--fleet",
        metavar="DIR",
        default=None,
        help=(
            "Update every agent-infrastructure workspace under DIR and its "
            "subfolders with --update --merge. Discovers .github/agents/ and "
            ".claude/ targets, snapshots each git workspace via a commit, applies "
            "the merge, then analyses the diff. Default is a DRY-RUN preview; pass "
            "--yes to apply. Non-destructive: merge-only; .claude is bridge-merged."
        ),
    )
    fleet_group.add_argument(
        "--fleet-frameworks",
        choices=["github", "claude", "both"],
        default="both",
        help="Which infrastructures to update per workspace (default: both).",
    )
    fleet_group.add_argument(
        "--fleet-report",
        metavar="DIR",
        default=None,
        help="Directory for the fleet report (default: <DIR>/.agentteams-fleet/<run-id>/).",
    )
    return parser


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
        _orphan_slugs = sorted(
            p.name[: -len(_suffix)]
            for p in output_dir.glob("*.agent.md")
            if p.name[: -len(_suffix)] not in _emitted
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
            _assert_security_intelligence_fresh(security_placeholders, output_dir=output_dir)
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
                _assert_destructive_action_allowed(output_dir, action="overwrite")
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
        _orphan_agents = sorted(
            f.name for f in output_dir.glob("*.agent.md")
            if f.name not in _emitted_agent_names and f.name not in _adopted_names
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
    # gated on _MIGRATE_GATE_EXEMPTION_ACTIVE — a module-level flag set ONLY
    # by _run_migrate around its main() re-invocation, so the bypass is not
    # reachable from the CLI.
    if not args.dry_run and args.overwrite and not _MIGRATE_GATE_EXEMPTION_ACTIVE:
        try:
            _assert_destructive_action_allowed(output_dir, action="overwrite")
        except RuntimeError as exc:
            print(f"Security gate blocked overwrite: {exc}", file=sys.stderr)
            return 1
    elif not args.dry_run and args.overwrite and _MIGRATE_GATE_EXEMPTION_ACTIVE:
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

    if not dry_run:
        from agentteams import security_refs as _security_refs

        convert_security = _security_refs.build_security_placeholders(
            output_dir=target_dir,
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        _assert_security_intelligence_fresh(convert_security, output_dir=target_dir)

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

    if not dry_run:
        from agentteams import security_refs as _security_refs

        interop_security = _security_refs.build_security_placeholders(
            output_dir=target_dir,
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        _assert_security_intelligence_fresh(interop_security, output_dir=target_dir)

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


# Bridge --output is interpreted as the *repo root*, not the agents directory.
# When users pass a known agents-dir suffix (intuiting that --output means
# "where agent files go"), normalize by stripping the suffix and warn so the
# bridge does not produce nested .github/.github/... layouts.
_BRIDGE_AGENTS_DIR_SUFFIXES: dict[str, tuple[tuple[str, ...], ...]] = {
    "copilot-vscode": ((".github", "agents"),),
    "copilot-cli": ((".github", "copilot"),),
    "claude": ((".claude", "agents"),),
}


def _normalize_bridge_output_root(output: Path, target_framework: str) -> Path:
    """Strip a known agents-dir suffix from a bridge --output path.

    Bridge mode treats --output as the *repo root*; if a user passes the
    target framework's conventional agents directory (e.g. ``.github/agents``
    for copilot-vscode), strip the suffix and emit a warning so bridge
    artifacts do not land at nested ``.github/.github/...`` paths.
    """
    suffixes = _BRIDGE_AGENTS_DIR_SUFFIXES.get(target_framework, ())
    parts = output.parts
    for suffix in suffixes:
        if len(parts) >= len(suffix) and parts[-len(suffix):] == suffix:
            normalized = Path(*parts[:-len(suffix)]) if parts[:-len(suffix)] else Path(output.anchor or ".")
            print(
                f"Warning: bridge --output {output} ends in '{'/'.join(suffix)}'.\n"
                f"  Bridge mode treats --output as the repository root, not the\n"
                f"  agents directory. Normalizing to {normalized} so bridge\n"
                f"  artifacts are written under the expected layout.",
                file=sys.stderr,
            )
            return normalized
    return output


def _run_bridge(
    source_dir: Path,
    source_framework: str | None,
    target_framework: str,
    output: Path | None,
    dry_run: bool,
    overwrite: bool,
    check_only: bool,
    merge_only: bool = False,
    emit_skills: bool = True,
    host_features: list[str] | None = None,
) -> int:
    """Execute the --bridge-from path via lightweight compatibility artifacts."""
    from agentteams.bridge import run_bridge
    from agentteams.interop import detect_framework

    detected = source_framework or detect_framework(source_dir)
    if output is not None:
        output_root = _normalize_bridge_output_root(output, target_framework)
    else:
        project_root = source_dir.parent.parent
        output_root = project_root

    if not dry_run and not check_only:
        from agentteams import security_refs as _security_refs

        bridge_security = _security_refs.build_security_placeholders(
            output_dir=output_root,
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        _assert_security_intelligence_fresh(bridge_security, output_dir=output_root)

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
            merge_only=merge_only,
            emit_skills=emit_skills,
            host_features=host_features or [],
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
        if result.manifest_missing:
            print(
                "\n  Hint: no bridge manifest exists yet. Run the same command "
                "with --bridge-refresh (omit --bridge-check) to generate the "
                "initial bridge artifacts, then re-run --bridge-check.",
                file=sys.stderr,
            )
    else:
        verb = "Would write" if dry_run else "Wrote"
        print(
            f"\n  {verb} {len(result.written)} bridge file(s)"
            + (f", skipped {len(result.skipped)}" if result.skipped else "")
            + "."
        )
        for notice in result.notices:
            print(f"  Notice: {notice}", file=sys.stderr)

    return 0 if result.success else 1


_BRIDGE_USAGE_HINT = (
    " Bridge mode is independent of description/project-driven generation.\n"
    "  Example:\n"
    "    agentteams --bridge-from <source-agents-dir> \\\n"
    "               --bridge-source-framework <claude|copilot-cli|copilot-vscode> \\\n"
    "               --framework <target-framework> \\\n"
    "               [--bridge-check | --bridge-refresh]"
)


def _validate_option_combinations(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Validate explicit incompatible option pairs and mode-specific constraints."""
    if args.query_k < 1:
        parser.error("--query-k must be >= 1")

    if args.auto_correct and not args.post_audit:
        parser.error("--auto-correct requires --post-audit")

    if args.prune and not args.update:
        parser.error("--prune can only be used with --update")

    if getattr(args, "fleet", None) is not None:
        # Fleet mode is non-destructive by construction: merge-only, and every
        # destructive or single-target mode is rejected.
        if not args.update:
            parser.error("--fleet requires --update")
        if args.overwrite:
            parser.error("--fleet requires --merge (not --overwrite)")
        if not args.merge:
            parser.error("--fleet requires --merge (fleet mode is merge-only)")
        if getattr(args, "shrink_policy", "preserve") == "allow":
            parser.error("--fleet forbids --shrink-policy=allow (it can drop retrofitted user content)")
        _fleet_incompatible = [
            ("self", "--self"), ("prune", "--prune"), ("migrate", "--migrate"),
            ("revert_migration", "--revert-migration"), ("overwrite", "--overwrite"),
            ("adopt_orphans", "--adopt-orphans"), ("bridge_from", "--bridge-from"),
            ("bridge_refresh", "--bridge-refresh"), ("convert_from", "--convert-from"),
            ("interop_from", "--interop-from"), ("refresh_index", "--refresh-index"),
            ("query_index", "--query-index"), ("list_backups", "--list-backups"),
            ("restore_backup", "--restore-backup"), ("description", "--description"),
            ("project", "--project"), ("output", "--output"),
            ("add_fence_markers", "--add-fence-markers"),
            ("capture_baseline", "--capture-baseline"), ("check_baseline", "--check-baseline"),
        ]
        for attr, flag in _fleet_incompatible:
            val = getattr(args, attr, None)
            if val:
                parser.error(f"--fleet cannot be combined with {flag} (it operates on many workspaces)")

    if getattr(args, "adopt_orphans", False):
        # Adoption rewrites the orchestrator front matter (agents: roster), which
        # only happens on a full re-render. Under --merge front matter is
        # preserved, so adoption would be a silent no-op — require overwrite/migrate.
        if not (args.overwrite or args.migrate):
            parser.error(
                "--adopt-orphans requires --overwrite or --migrate "
                "(under --merge the orchestrator front matter is preserved, so "
                "adoption would not take effect)"
            )
        if args.prune:
            parser.error(
                "--adopt-orphans and --prune are mutually exclusive "
                "(adopt integrates orphan agents; prune deletes them)"
            )

    if args.convert_from and args.interop_from:
        parser.error("--convert-from and --interop-from are mutually exclusive")

    if args.bridge_from and args.convert_from:
        parser.error("--bridge-from and --convert-from are mutually exclusive")

    if args.bridge_from and args.interop_from:
        parser.error("--bridge-from and --interop-from are mutually exclusive")

    if args.bridge_check and args.bridge_refresh:
        parser.error("--bridge-check cannot be combined with --bridge-refresh")

    if args.bridge_check and not args.bridge_from:
        parser.error("--bridge-check requires --bridge-from." + _BRIDGE_USAGE_HINT)

    if args.bridge_refresh and not args.bridge_from:
        parser.error("--bridge-refresh requires --bridge-from." + _BRIDGE_USAGE_HINT)

    if args.refresh_index and args.query_index:
        parser.error("--refresh-index and --query-index are mutually exclusive")

    if args.refresh_index:
        refresh_incompatible = [
            ("update", "--update"),
            ("prune", "--prune"),
            ("check", "--check"),
            ("scan_security", "--scan-security"),
            ("post_audit", "--post-audit"),
            ("auto_correct", "--auto-correct"),
            ("enrich", "--enrich"),
            ("migrate", "--migrate"),
            ("revert_migration", "--revert-migration"),
            ("list_backups", "--list-backups"),
            ("restore_backup", "--restore-backup"),
            ("convert_from", "--convert-from"),
            ("interop_from", "--interop-from"),
            ("bridge_from", "--bridge-from"),
            ("bridge_check", "--bridge-check"),
            ("bridge_refresh", "--bridge-refresh"),
        ]
        for attr, flag in refresh_incompatible:
            val = getattr(args, attr)
            if attr == "restore_backup":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --refresh-index")
            elif val:
                parser.error(f"{flag} cannot be used with --refresh-index")

    if args.query_index:
        query_incompatible = [
            ("update", "--update"),
            ("prune", "--prune"),
            ("check", "--check"),
            ("scan_security", "--scan-security"),
            ("post_audit", "--post-audit"),
            ("auto_correct", "--auto-correct"),
            ("enrich", "--enrich"),
            ("migrate", "--migrate"),
            ("revert_migration", "--revert-migration"),
            ("list_backups", "--list-backups"),
            ("restore_backup", "--restore-backup"),
            ("convert_from", "--convert-from"),
            ("interop_from", "--interop-from"),
            ("bridge_from", "--bridge-from"),
            ("bridge_check", "--bridge-check"),
            ("bridge_refresh", "--bridge-refresh"),
        ]
        for attr, flag in query_incompatible:
            val = getattr(args, attr)
            if attr == "restore_backup":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --query-index")
            elif val:
                parser.error(f"{flag} cannot be used with --query-index")

    convert_incompatible = [
        ("description", "--description"),
        ("project", "--project"),
        ("self_update", "--self"),
        ("no_scan", "--no-scan"),
        ("update", "--update"),
        ("prune", "--prune"),
        ("check", "--check"),
        ("refresh_index", "--refresh-index"),
        ("query_index", "--query-index"),
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
        ("refresh_index", "--refresh-index"),
        ("query_index", "--query-index"),
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
        ("refresh_index", "--refresh-index"),
        ("query_index", "--query-index"),
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
                    parser.error(f"{flag} cannot be used with --bridge-from." + _BRIDGE_USAGE_HINT)
            elif attr == "restore_backup":
                if val is not None:
                    parser.error(f"{flag} cannot be used with --bridge-from." + _BRIDGE_USAGE_HINT)
            elif val:
                parser.error(f"{flag} cannot be used with --bridge-from." + _BRIDGE_USAGE_HINT)


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
        waiver = _latest_security_waiver(output_dir, action=action)
        if waiver is not None:
            _consume_security_waiver_use(output_dir, waiver, action=action)
            return
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

    waiver = _latest_security_waiver(output_dir, action=action)
    if waiver is not None:
        _consume_security_waiver_use(output_dir, waiver, action=action)
        return

    if verdict == "PASS":
        _consume_security_decision_use(output_dir, decision, action=action)
        return

    if verdict == "CONDITIONAL PASS" and cond_verified == "verified":
        _consume_security_decision_use(output_dir, decision, action=action)
        return

    if verdict == "USED":
        raise RuntimeError(
            "no matching PASS decision found in references/security-decisions.log.csv"
        )

    if verdict == "CONDITIONAL PASS":
        raise RuntimeError(
            "latest CONDITIONAL PASS has unverified conditions "
            f"(conditions_verified={cond_verified or 'pending'})"
        )

    if verdict not in {"PASS", "CONDITIONAL PASS"}:
        raise RuntimeError(
            f"latest decision has unsupported verdict '{verdict or 'UNKNOWN'}'"
        )

    raise RuntimeError(
        "no matching PASS decision found in references/security-decisions.log.csv"
    )


def _consume_security_decision_use(output_dir: Path, decision: dict[str, str], *, action: str) -> None:
    """Mark a validated security decision as consumed so it cannot be replayed."""
    log_path = output_dir / "references" / "security-decisions.log.csv"
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to update security decision log: {exc}") from exc

    if not rows:
        raise RuntimeError("security decision log is empty")

    target_action = decision.get("action_reviewed", "").strip()
    target_timestamp = decision.get("timestamp", "").strip()
    target_verdict = decision.get("verdict", "").strip().upper()

    updated = False
    for row in reversed(rows):
        row_action = row.get("action_reviewed", row.get("decision", "")).strip()
        if row_action != target_action:
            continue
        row_timestamp = row.get("timestamp", row.get("date", "")).strip()
        if target_timestamp and row_timestamp != target_timestamp:
            continue
        row_verdict = row.get("verdict", row.get("status", row.get("decision", ""))).strip().upper()
        if row_verdict != target_verdict:
            continue

        if "consumed" not in fieldnames:
            fieldnames.append("consumed")
        row["consumed"] = "yes"
        updated = True
        break

    if not updated:
        raise RuntimeError(f"validated decision for action '{action}' could not be updated")

    try:
        with log_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        raise RuntimeError(f"unable to persist security decision log: {exc}") from exc


def _assert_security_intelligence_fresh(
    security_placeholders: dict[str, str],
    *,
    output_dir: Path,
) -> None:
    """Raise RuntimeError when generated security intelligence is stale."""
    freshness = _security_intelligence_freshness(security_placeholders)
    if freshness["status"] == "fresh":
        return

    waiver = _latest_security_waiver(output_dir, action="security-intel-freshness")
    if waiver is not None:
        _consume_security_waiver_use(output_dir, waiver, action="security-intel-freshness")
        return

    raise RuntimeError(
        "security intelligence is stale "
        f"(status={freshness['status']}, age_hours={freshness['age_hours']}, "
        f"ttl_hours={freshness['ttl_hours']})"
    )


def _security_intelligence_freshness(security_placeholders: dict[str, str]) -> dict[str, str]:
    """Return machine-readable freshness state for generated security intelligence."""
    explicit_status = security_placeholders.get("SECURITY_DATA_FRESHNESS_STATUS", "").strip().lower()
    generated_at = security_placeholders.get("SECURITY_DATA_GENERATED_AT", "")
    summary = security_placeholders.get("SECURITY_CURRENT_THREATS_SUMMARY", "")
    playbook = security_placeholders.get("SECURITY_PREVENTION_PLAYBOOK", "")

    age_hours = ""
    status = "unknown"
    if explicit_status in {"fresh", "stale", "unknown"}:
        status = explicit_status
    try:
        generated_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        age_delta = datetime.now(UTC) - generated_dt
        age_hours_raw = age_delta.total_seconds() / 3600.0
        if age_hours_raw < -(5.0 / 60.0):
            age_hours = f"{age_hours_raw:.2f}"
            status = "stale"
        else:
            age_hours_value = max(age_hours_raw, 0.0)
            age_hours = f"{age_hours_value:.2f}"
            if age_hours_value <= _SECURITY_INTEL_TTL_HOURS and "STALE DATA" not in summary and "STALE DATA" not in playbook and explicit_status != "stale":
                status = "fresh"
            else:
                status = "stale"
    except ValueError:
        status = "stale"

    return {
        "status": status,
        "age_hours": age_hours,
        "ttl_hours": str(_SECURITY_INTEL_TTL_HOURS),
    }


def _consume_security_waiver_use(output_dir: Path, waiver: dict[str, str], *, action: str) -> None:
    """Increment the use counter for an already validated security waiver."""
    log_path = output_dir / "references" / "security-waivers.log.csv"
    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to update security waiver log: {exc}") from exc

    if not rows:
        raise RuntimeError("security waiver log is empty")

    target_id = waiver.get("waiver_id", "").strip()
    if not target_id:
        raise RuntimeError("validated waiver is missing waiver_id")

    signing_key = os.getenv("AGENTTEAMS_WAIVER_SIGNING_KEY", "")
    if not signing_key:
        raise RuntimeError("waiver signing key is not configured")

    updated = False
    for row in reversed(rows):
        if (row.get("waiver_id", "").strip() != target_id) or not _action_matches(row.get("action_reviewed", ""), action):
            continue

        try:
            uses_value = int((row.get("uses", "") or "0").strip() or 0)
        except ValueError as exc:
            raise RuntimeError("waiver use counters are not numeric") from exc
        row["uses"] = str(uses_value + 1)
        payload = "|".join(
            [
                row.get("waiver_id", "").strip(),
                row.get("action_reviewed", "").strip(),
                row.get("expires_at", "").strip(),
                row.get("max_uses", "").strip(),
                row.get("uses", "").strip(),
                row.get("approver", "").strip(),
                row.get("ticket_id", "").strip(),
                row.get("reason_code", "").strip(),
                row.get("conditions_verified", "").strip(),
            ]
        )
        row["signature"] = hmac.new(
            signing_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        updated = True
        break

    if not updated:
        raise RuntimeError(f"validated waiver '{target_id}' could not be updated")

    try:
        with log_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        raise RuntimeError(f"unable to persist security waiver log: {exc}") from exc


def _latest_security_waiver(output_dir: Path, *, action: str) -> dict[str, str] | None:
    """Return the latest valid security waiver for an action keyword, if present."""
    log_path = output_dir / "references" / "security-waivers.log.csv"
    if not log_path.exists():
        return None

    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            actual_columns = [c.strip() for c in (reader.fieldnames or [])]
            _security_waiver_schema_kind(actual_columns)
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to read security waiver log: {exc}") from exc

    if not rows:
        return None

    for row in reversed(rows):
        reviewed_candidates = [
            row.get("action_reviewed") or "",
        ]
        if not any(_action_matches(candidate, action) for candidate in reviewed_candidates):
            continue

        normalized_row = {k: (v or "") for k, v in row.items()}
        _validate_security_waiver(normalized_row, action=action)
        return normalized_row
    return None


def _security_waiver_schema_kind(actual_columns: list[str]) -> str:
    """Return the supported schema kind for a security-waiver log header."""
    normalized = [c.strip() for c in actual_columns]
    if _SECURITY_WAIVER_REQUIRED_COLUMNS.issubset(normalized):
        return "waiver"
    raise RuntimeError(
        "security waiver log is malformed: expected header "
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,ticket_id,reason_code,conditions_verified,signature"
    )


def _validate_security_waiver(waiver: dict[str, str], *, action: str) -> None:
    """Raise RuntimeError if a waiver row is missing required properties."""
    if not _action_matches(waiver.get("action_reviewed", ""), action):
        raise RuntimeError(f"waiver scope mismatch for action '{action}'")

    if waiver.get("conditions_verified", "").strip().lower() != "verified":
        raise RuntimeError("waiver conditions are not verified")

    approver = waiver.get("approver", "").strip()
    ticket_id = waiver.get("ticket_id", "").strip()
    reason_code = waiver.get("reason_code", "").strip()
    if not approver or not ticket_id or not reason_code:
        raise RuntimeError("waiver is missing approver, ticket_id, or reason_code")

    try:
        expires_at = datetime.fromisoformat(waiver.get("expires_at", "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("waiver expires_at is not a valid ISO-8601 timestamp") from exc
    if expires_at <= datetime.now(UTC):
        raise RuntimeError("waiver has expired")

    try:
        max_uses = int(waiver.get("max_uses", "0") or 0)
        uses = int(waiver.get("uses", "0") or 0)
    except ValueError as exc:
        raise RuntimeError("waiver use counters are not numeric") from exc
    if max_uses <= 0 or uses >= max_uses:
        raise RuntimeError("waiver use limit has been reached")

    signing_key = os.getenv("AGENTTEAMS_WAIVER_SIGNING_KEY", "")
    if not signing_key:
        raise RuntimeError("waiver signing key is not configured")

    payload = "|".join(
        [
            waiver.get("waiver_id", "").strip(),
            waiver.get("action_reviewed", "").strip(),
            waiver.get("expires_at", "").strip(),
            waiver.get("max_uses", "").strip(),
            waiver.get("uses", "").strip(),
            approver,
            ticket_id,
            reason_code,
            waiver.get("conditions_verified", "").strip(),
        ]
    )
    expected_signature = hmac.new(
        signing_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, waiver.get("signature", "").strip().lower()):
        raise RuntimeError("waiver signature verification failed")


def _latest_security_decision(output_dir: Path, *, action: str) -> dict[str, str] | None:
    """Return the latest security decision row matching an action keyword."""
    log_path = output_dir / "references" / "security-decisions.log.csv"
    if not log_path.exists():
        return None

    try:
        with log_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            actual_columns = [c.strip() for c in (reader.fieldnames or [])]
            schema_kind = _security_decision_schema_kind(actual_columns)
            action_field = "action_reviewed" if schema_kind == "legacy" else "decision"
            verdict_field = "verdict" if schema_kind == "legacy" else "status"
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"unable to read security decisions log: {exc}") from exc

    if not rows:
        return None

    for row in reversed(rows):
        if row.get("consumed", "").strip().lower() in {"yes", "true", "1"}:
            continue
        reviewed_candidates = [
            row.get(action_field) or "",
            row.get("action_reviewed") or "",
            row.get("decision") or "",
        ]
        if any(_action_matches(candidate, action) for candidate in reviewed_candidates):
            normalized_row = {k: (v or "") for k, v in row.items()}
            normalized_row["action_reviewed"] = normalized_row.get(action_field, normalized_row.get("action_reviewed", ""))
            normalized_row["verdict"] = normalized_row.get(verdict_field, normalized_row.get("verdict", ""))
            normalized_row["timestamp"] = normalized_row.get("timestamp", normalized_row.get("date", ""))
            return normalized_row
    return None


def _security_decision_schema_kind(actual_columns: list[str]) -> str:
    """Return the supported schema kind for a security-decision log header.

    Accepts either the legacy six-column schema or the current repository schema
    with additional provenance fields. The required subset must be present.
    """
    normalized = [c.strip() for c in actual_columns]
    for schema_kind, required in _SECURITY_DECISION_REQUIRED_COLUMNS.items():
        if required.issubset(normalized):
            return schema_kind
    raise RuntimeError(
        "security decisions log is malformed: expected either the legacy header "
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified "
        "or the current repository header date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner"
    )


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


def _apply_placeholder_policy(
    manifest: dict,
    *,
    strict_manual_placeholders: bool,
) -> None:
    """Apply dual-mode policy for optional governance placeholders.

    In strict mode, unresolved {MANUAL:*} tokens are preserved as-is.
    In usability mode, selected optional placeholders are replaced with
    explicit defaults and removed from SETUP-REQUIRED tracking.
    """
    if strict_manual_placeholders:
        return

    auto = manifest.get("auto_resolved_placeholders", {})
    ref_key = "REFERENCE_DB_PATH"
    style_key = "STYLE_REFERENCE_PATH"
    ref_manual = "{MANUAL:REFERENCE_DB_PATH}"
    style_manual = "{MANUAL:STYLE_REFERENCE_PATH}"

    if str(auto.get(ref_key, "")).strip() == ref_manual:
        auto[ref_key] = "N/A - no citation database configured for this project"

    if str(auto.get(style_key, "")).strip() == style_manual:
        desc = manifest.get("description", {}) or {}
        style_value = desc.get("style_reference") or desc.get("style_reference_path")
        auto[style_key] = (
            str(style_value)
            if style_value
            else "N/A - no formal style guide defined for this project"
        )

    manual_items = manifest.get("manual_required_placeholders", [])
    if manual_items:
        filtered = [
            item for item in manual_items
            if item.get("placeholder") not in {ref_key, style_key}
        ]
        manifest["manual_required_placeholders"] = filtered


def _resolve_strict_manual_mode(*, strict_arg: bool | None, self_update: bool) -> bool:
    """Resolve strict/manual policy from CLI args.

    Explicit CLI flags win. Otherwise strict mode defaults to True in
    self-maintenance mode and False for normal generation.
    """
    if strict_arg is not None:
        return bool(strict_arg)
    return bool(self_update)


def _build_final_rendered(
    manifest: dict[str, Any],
    adapter: CopilotVSCodeAdapter | CopilotCLIAdapter | ClaudeAdapter,
    project_name: str,
) -> list[tuple[str, str]]:
    """Render templates and apply framework post-processing.

    Returns a list of (relative_path, content) pairs including
    runtime-handoffs (when the adapter uses manifest delivery) and the
    pipeline graph. This is the shared rendering step used by the generate
    path, ``--update``, and ``--check``; ``--check`` uses the result for
    content comparison only and does not write to disk.
    """
    from agentteams import graph as _graph

    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)
    final: list[tuple[str, str]] = []
    runtime_handoff_agents: list[dict[str, object]] = []
    for rel_path, content in rendered:
        file_type = _guess_file_type(rel_path)
        if file_type == "agent":
            slug = Path(rel_path).stem.replace(".agent", "")
            if adapter.handoff_delivery_mode() == "manifest":
                handoffs = adapter.extract_handoffs(content)
                if handoffs:
                    runtime_handoff_agents.append({"agent": slug, "handoffs": handoffs})
            content = adapter.render_agent_file(content, slug, manifest)
        elif file_type == "instructions":
            content = adapter.render_instructions_file(content, manifest)
        final_path = adapter.finalize_output_path(rel_path, file_type)
        final.append((final_path, content))

    if runtime_handoff_agents:
        final.append((
            "references/runtime-handoffs.json",
            json.dumps({
                "schema_version": "1.0",
                "framework": adapter.framework_id,
                "project_name": project_name,
                "agents": runtime_handoff_agents,
            }, indent=2) + "\n",
        ))

    final.append((
        "references/pipeline-graph.md",
        _graph.generate_graph_document(dict(final), project_name=project_name),
    ))
    return final


def _make_content_matches(
    output_dir: Path,
    rendered_by_path: dict[str, str],
    security_refresh_paths: set[str],
) -> Callable[[str], bool]:
    """Return a predicate: does a file's disk content match its rendered content?

    Files in ``security_refresh_paths`` always return False (they are
    force-written on every ``--update``). Missing files return False.
    The comparison mirrors what ``emit`` writes: manual-value preservation
    followed by merge-fence normalization.
    """
    def _matches(path: str) -> bool:
        if path in security_refresh_paths:
            return False
        rendered = rendered_by_path.get(path)
        if rendered is None:
            return False
        disk_path = emit._resolve_path(output_dir, path)
        if not disk_path.exists():
            return False
        disk_text = disk_path.read_text(encoding="utf-8")
        preserved = _preserve_manual_values(disk_text, rendered)
        effective = emit._normalize_generated_content(path, preserved)
        effective = emit._ensure_project_notes_section(path, effective)
        return effective == disk_text
    return _matches


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


DELIVERY_RECEIPT_REL_PATH = "references/delivery-receipt.json"
"""Path (relative to ``output_dir``) where ``--update`` writes the delivery
receipt on success.

The receipt is an ATTESTATION, not a baseline: it is intentionally not part of
``output_files_map``, ``template_hashes``, or ``file_hashes`` in the build-log,
and the drift detector never reads it. See
``schemas/delivery-receipt.schema.json`` and
``docs_src/delivery-procedure.md``.
"""


def _require_jsonschema(error_cls: type[Exception], artifact: str) -> Any:
    """Import and return ``jsonschema``, or raise *error_cls* if it is absent.

    A *missing* jsonschema module is an environment gap (e.g. the run was driven
    by an interpreter without the dep), not a malformed artifact. Every artifact
    writer below is wrapped by ``main()`` in a non-fatal
    ``except (OSError, <error_cls>)`` handler, so converting the ``ImportError``
    into the writer's own error type lets a fully successful, non-destructive
    merge finish cleanly (exit 0) instead of aborting with a traceback *after*
    the merge already wrote every file. The artifact is re-emitted on the next
    ``--update`` once jsonschema is installed.
    """
    try:
        import jsonschema
        return jsonschema
    except ImportError as exc:
        raise error_cls(
            f"jsonschema is not installed; cannot validate the {artifact}. "
            "The merge itself is complete — install jsonschema (or run via the "
            f"`agentteams` console entry point) to re-emit the {artifact} on the "
            "next --update."
        ) from exc


class DeliveryReceiptError(RuntimeError):
    """Raised when the delivery receipt cannot be produced or fails schema
    validation (RA2). Callers treat this as non-fatal: the build-log heal
    stands and the next ``--update`` re-emits the receipt."""


def _write_delivery_receipt(manifest: dict, output_dir: Path) -> Path:
    """Write a P3 delivery receipt attesting that ``--update`` succeeded.

    The receipt is written AFTER the build-log (``_write_run_log``) inside the
    same ``not args.dry_run and result.success`` block, so its
    ``manifest_fingerprint`` always matches the build-log just written. This is
    the "heal first, attest second" ordering (see R3 rationale in
    ``docs_src/delivery-procedure.md``). If the receipt write
    fails after the log is written, the next ``--update`` converges to zero
    drift and re-emits the receipt — the safe failure direction.

    The receipt is excluded from drift detection by construction: it is never
    added to the rendered set, ``output_files``, ``template_hashes``, or
    ``file_hashes``. See ``schemas/delivery-receipt.schema.json`` for the
    contract; see ``docs_src/delivery-procedure.md`` for the procedure and the
    "heal first, attest second" (R3) ordering rationale.

    The payload is validated against ``schemas/delivery-receipt.schema.json``
    at write time (RA2); a non-conforming receipt raises
    ``DeliveryReceiptError`` and is *not* written. Callers treat that as
    non-fatal — the build-log heal stands and the next ``--update`` re-emits.
    """
    from datetime import datetime, timezone
    from agentteams import drift as _drift
    try:
        from agentteams import __version__ as _agentteams_version
    except (ImportError, AttributeError):  # version attr legitimately absent
        _agentteams_version = None

    receipt: dict[str, object] = {
        "artifact_type": "delivery-receipt",
        "receipt_schema_version": "1.0",
        "delivered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project_name": manifest.get("project_name", ""),
        "framework": manifest.get("framework", ""),
        "manifest_fingerprint": _drift.compute_manifest_fingerprint(manifest),
        "fingerprint_algo_version": _drift.FINGERPRINT_ALGO_VERSION,
        "output_dir": str(output_dir),
    }
    if _agentteams_version:
        receipt["agentteams_version"] = str(_agentteams_version)

    # RA2: validate against the shipped schema before writing. A non-conforming
    # receipt is a real defect we want surfaced — not silently written. A
    # missing jsonschema module degrades to a non-fatal DeliveryReceiptError
    # (see _require_jsonschema) rather than crashing a completed merge.
    jsonschema = _require_jsonschema(DeliveryReceiptError, "delivery receipt")
    schema_path = Path(__file__).resolve().parent / "schemas" / "delivery-receipt.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryReceiptError(
            f"delivery-receipt schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(receipt, schema)
    except jsonschema.ValidationError as exc:
        raise DeliveryReceiptError(
            f"delivery receipt failed schema validation: {exc.message}"
        ) from exc

    receipt_path = output_dir / DELIVERY_RECEIPT_REL_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt_path


EVAL_SUITE_REL_PATH = "references/eval-suite.json"


class EvalSuiteError(RuntimeError):
    """Raised when the eval suite cannot be produced or fails schema
    validation (Cluster A Phase 2). Non-fatal to the caller, like
    DeliveryReceiptError — the build-log heal stands and the next ``--update``
    re-emits."""


def _write_eval_suite(manifest: dict, output_dir: Path) -> Path:
    """Emit the framework-neutral eval suite (Cluster A Phase 2, increment 1).

    Mirrors ``_write_delivery_receipt``: build from the manifest, validate
    against ``schemas/eval-suite.schema.json`` before writing, raise
    ``EvalSuiteError`` (a RuntimeError, never OSError) on non-conformance and
    write nothing. Generator-owned artifact at
    ``<output_dir>/references/eval-suite.json``; excluded from drift by
    construction (never added to the rendered set, output_files_map,
    template_hashes, or file_hashes; never read by --check or --update). See
    ``schemas/eval-suite.schema.json`` and ``docs_src`` for the contract.
    """
    from agentteams.eval_suite import build_eval_suite

    suite = build_eval_suite(manifest)

    jsonschema = _require_jsonschema(EvalSuiteError, "eval suite")
    schema_path = Path(__file__).resolve().parent / "schemas" / "eval-suite.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalSuiteError(
            f"eval-suite schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(suite, schema)
    except jsonschema.ValidationError as exc:
        raise EvalSuiteError(
            f"eval suite failed schema validation: {exc.message}"
        ) from exc

    suite_path = output_dir / EVAL_SUITE_REL_PATH
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(json.dumps(suite, indent=2) + "\n", encoding="utf-8")
    return suite_path


MODEL_ROUTING_REL_PATH = "references/model-routing.json"


class ModelRoutingError(RuntimeError):
    """Raised when the model-routing contract fails schema validation (F6).
    Non-fatal to the caller, like EvalSuiteError — emitted only under
    --cost-routing; a malformed contract is not written and the next run
    re-emits."""


def _write_model_routing(manifest: dict, output_dir: Path) -> Path:
    """Emit the framework-neutral model-routing contract (F6, opt-in).

    Called ONLY when ``--cost-routing`` is set. Same RA2 contract as
    ``_write_eval_suite``: pure build → schema-validate against
    ``schemas/model-routing.schema.json`` → raise ``ModelRoutingError``
    (RuntimeError, never OSError) and write nothing on non-conformance.
    Generator-owned, drift-excluded by construction (``.json``; never in
    output_files_map/template_hashes/file_hashes; never read by --check or
    --update). Does NOT modify any rendered agent file.
    """
    from agentteams.model_routing import build_routing_contract

    contract = build_routing_contract(manifest)

    jsonschema = _require_jsonschema(ModelRoutingError, "model routing contract")
    schema_path = Path(__file__).resolve().parent / "schemas" / "model-routing.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelRoutingError(
            f"model-routing schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(contract, schema)
    except jsonschema.ValidationError as exc:
        raise ModelRoutingError(
            f"model-routing contract failed schema validation: {exc.message}"
        ) from exc

    contract_path = output_dir / MODEL_ROUTING_REL_PATH
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return contract_path


MEMORY_INDEX_REL_PATH = "references/memory-index.json"
MEMORY_INDEX_EXTRA_DOC_NAMES = ("CHANGELOG.md", "README.md", "build-team-plan.md")


class MemoryIndexError(RuntimeError):
    """Raised when the memory index fails schema validation (F8). Non-fatal
    at the call site: the existing work-summary documents are the source of
    truth — the navigator falls back to opening them and to filesystem search
    when the index is absent / stale / malformed."""


def _memory_index_sources(manifest: dict, output_dir: Path) -> list[Path]:
    """Collect durable text sources for the memory index (F8).

    RSR1-aware: durable, project-local sources only — never gitignored
    scratch areas. Prefers the manifest's ``existing_project_path`` (the
    operator's explicit signal of the project root, e.g. when ``--output``
    is non-standard); falls back to inferring from ``output_dir`` when
    absent (standard layout: ``<project>/.github/agents`` or
    ``<project>/.claude/agents``).
    """
    epp = manifest.get("existing_project_path")
    project_root = Path(epp) if epp else output_dir.parent.parent
    sources: list[Path] = []
    # Work summaries (the canonical durable history substrate).
    ws = project_root / "workSummaries"
    if ws.exists() and ws.is_dir():
        sources.extend(sorted(ws.rglob("*.md")))
    # Top-level durable docs.
    for name in MEMORY_INDEX_EXTRA_DOC_NAMES:
        p = project_root / name
        if p.exists() and p.is_file():
            sources.append(p)
    # Additional durable authored docs.
    docs_src = project_root / "docs_src"
    if docs_src.exists() and docs_src.is_dir():
        sources.extend(sorted(docs_src.glob("*.md")))
    refs = project_root / "references"
    if refs.exists() and refs.is_dir():
        sources.extend(sorted(refs.rglob("*.md")))
    # Consumer-declared extra index dirs / globs (W22 recall-first follow-up).
    # Each entry is a project-relative string treated as:
    #   - a glob pattern if it contains '*' or '?' (expanded literally), or
    #   - a directory otherwise (recursively scanned for *.md).
    # Safety: reject absolute paths, traversal that escapes project_root, and
    # symlinked escapes (post-glob realpath check).
    extra = manifest.get("memory_index_extra_dirs")
    if isinstance(extra, list):
        try:
            project_root_resolved = project_root.resolve()
        except OSError:
            project_root_resolved = project_root
        for raw in extra:
            if not isinstance(raw, str) or not raw.strip():
                continue
            if Path(raw).is_absolute():
                continue
            is_glob = any(ch in raw for ch in "*?[")
            try:
                if is_glob:
                    candidates = sorted(project_root.glob(raw))
                else:
                    target = (project_root / raw)
                    if not (target.exists() and target.is_dir()):
                        continue
                    try:
                        target.resolve().relative_to(project_root_resolved)
                    except (ValueError, OSError):
                        continue
                    candidates = sorted(target.rglob("*.md"))
            except (OSError, ValueError):
                continue
            for c in candidates:
                if not c.is_file() or c.suffix != ".md":
                    continue
                try:
                    real = Path(os.path.realpath(c))
                    real.relative_to(project_root_resolved)
                except (ValueError, OSError):
                    continue
                sources.append(c)
    return sources


def _read_memory_index(output_dir: Path) -> dict[str, object]:
    """Load and parse references/memory-index.json from output_dir.

    Raises RuntimeError when the file is missing or invalid.
    """
    index_path = output_dir / MEMORY_INDEX_REL_PATH
    if not index_path.exists():
        raise MemoryIndexError(
            f"memory index not found at {index_path}; run --refresh-index or --update first"
        )
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(f"failed reading memory index at {index_path}: {exc}") from exc
    _validate_memory_index_schema(index)
    return index


def _validate_memory_index_schema(index: dict[str, object]) -> None:
    """Validate a parsed memory-index payload against its schema.

    Query-mode reads must validate shape so malformed payloads fail with a
    controlled ``MemoryIndexError`` instead of surfacing raw ``KeyError`` from
    downstream ranking logic.
    """
    jsonschema = _require_jsonschema(MemoryIndexError, "memory index")

    schema_path = Path(__file__).resolve().parent / "schemas" / "memory-index.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(
            f"memory-index schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(index, schema)
    except jsonschema.ValidationError as exc:
        raise MemoryIndexError(
            f"memory index failed schema validation: {exc.message}"
        ) from exc


def _run_refresh_index(manifest: dict, output_dir: Path) -> int:
    """Rebuild memory-index.json only (no template emit/update path)."""
    path = _write_memory_index(manifest, output_dir)
    index = _read_memory_index(output_dir)
    docs = int(index.get("N", 0))
    source_count = int(index.get("source_count", 0))
    print(f"  ✓  Refreshed memory index: {path}")
    print(f"     Indexed {docs} document(s) from {source_count} source file(s).")
    return 0


def _run_query_index(
    manifest: dict, output_dir: Path, query: str, k: int, strategy: str = "lexical"
) -> int:
    """Query memory-index.json and print ranked hits."""
    from agentteams.memory_index import is_index_stale, query_index

    index = _read_memory_index(output_dir)
    sources = _memory_index_sources(manifest, output_dir)
    if is_index_stale(index, sources):
        refreshed_path = _write_memory_index(manifest, output_dir)
        index = _read_memory_index(output_dir)
        print(
            "  !  Index was stale relative to source files. "
            f"Auto-refreshed: {refreshed_path}"
        )

    hits = query_index(index, query, k=k, strategy=strategy)

    print(f"Query: {query!r}")
    if not hits:
        print("  No matching documents found.")
        return 1
    for idx, hit in enumerate(hits, start=1):
        print(
            f"  {idx}. score={hit['score']:.6f}  {hit['title']}\n"
            f"     path: {hit['path']}\n"
            f"     snippet: {hit['snippet']}"
        )
    return 0


def _write_memory_index(manifest: dict, output_dir: Path) -> Path:
    """Emit the additive lexical memory index (F8).

    Always emitted (no opt-in flag): the index is *additive* to the existing
    work-summary documents, never a replacement. Empty source list ⇒ an
    empty-but-schema-valid index (a freshly generated team has no history
    yet; later --update runs accumulate it). Same RA2 contract as the other
    generator-owned artifacts: pure build → schema-validate at write time →
    raise ``MemoryIndexError`` (RuntimeError, never OSError) on
    non-conformance, write nothing → non-fatal at the call site →
    drift-excluded by construction.
    """
    from agentteams.memory_index import build_memory_index
    from agentteams.memory_index_incremental import try_incremental_sed_update

    index_path = output_dir / MEMORY_INDEX_REL_PATH
    incremental_enabled = os.getenv("AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED", "").strip() == "1"

    if incremental_enabled and index_path.exists():
        try:
            current = _read_memory_index(output_dir)
            result = try_incremental_sed_update(
                index_path=index_path,
                index=current,
                sources=_memory_index_sources(manifest, output_dir),
                project_name=manifest.get("project_name", ""),
                framework=manifest.get("framework", ""),
                validate_index=_validate_memory_index_schema,
            )
            if result.applied:
                return index_path
            print(
                "  !  Incremental memory-index update skipped "
                f"({result.reason}); falling back to full rebuild."
            )
        except (OSError, MemoryIndexError, RuntimeError) as exc:
            print(
                "  !  Incremental memory-index update failed "
                f"({exc}); falling back to full rebuild."
            )

    index = build_memory_index(
        _memory_index_sources(manifest, output_dir),
        project_name=manifest.get("project_name", ""),
        framework=manifest.get("framework", ""),
    )

    jsonschema = _require_jsonschema(MemoryIndexError, "memory index")
    schema_path = Path(__file__).resolve().parent / "schemas" / "memory-index.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemoryIndexError(
            f"memory-index schema unavailable ({schema_path}): {exc}"
        ) from exc
    try:
        jsonschema.validate(index, schema)
    except jsonschema.ValidationError as exc:
        raise MemoryIndexError(
            f"memory index failed schema validation: {exc.message}"
        ) from exc

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return index_path


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

# In-process flag exempting `--migrate`-driven --overwrite from the
# destructive-action security gate. Set ONLY by _run_migrate around its
# main() re-invocation (try/finally-scoped). Never exposed to the CLI — a
# direct user invocation cannot reach the exemption path.
_MIGRATE_GATE_EXEMPTION_ACTIVE = False


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
    global _MIGRATE_GATE_EXEMPTION_ACTIVE
    _MIGRATE_GATE_EXEMPTION_ACTIVE = True
    try:
        rc_emit = main(new_argv)
    finally:
        _MIGRATE_GATE_EXEMPTION_ACTIVE = False

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
