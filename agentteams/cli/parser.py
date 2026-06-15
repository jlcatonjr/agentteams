"""
parser.py — CLI argument parser and option-combination validation.

Extracted verbatim from build_team.py (CH-07 modular structure). build_team
re-exports _build_parser / _validate_option_combinations / _BRIDGE_USAGE_HINT,
so callers (main, agentteams.man) and tests resolve them in build_team's
namespace unchanged.
"""

from __future__ import annotations

import argparse

from agentteams import __version__
from agentteams.emit import DEFAULT_BACKUP_KEEP_LAST
from agentteams.frameworks.registry import FRAMEWORKS

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
    parser.add_argument(
        "--verify-waivers",
        action="store_true",
        dest="verify_waivers",
        default=False,
        help=(
            "Read-only: report the validity (signature, expiry, use-limit, "
            "conditions) of every security waiver in "
            "references/security-waivers.log.csv under --output/--project (else "
            "CWD). Never mints or consumes a waiver. Exits non-zero if any waiver "
            "is invalid. Requires AGENTTEAMS_WAIVER_SIGNING_KEY to verify "
            "signatures; without it, rows report as unverifiable."
        ),
    )
    parser.add_argument(
        "--verify-integrity",
        action="store_true",
        dest="verify_integrity",
        default=False,
        help=(
            "Read-only: classify every generated output file under --output/"
            "--project (else CWD) against the build-log file_hashes baseline — "
            "OK / MODIFIED / TRUNCATED / MISSING / FENCE-BROKEN. Detects silent "
            "corruption. Exits non-zero on any TRUNCATED/MISSING/FENCE-BROKEN; "
            "MODIFIED (a legitimate edit or drift) is advisory. Unlike --update, "
            "this exit code IS the integrity gate."
        ),
    )
    parser.add_argument(
        "--verify-backup",
        nargs="?",
        const="latest",
        default=None,
        metavar="TIMESTAMP",
        dest="verify_backup",
        help=(
            "Read-only: verify a backup is restorable — its bytes match the "
            "recorded source_sha256 in _manifest.json. Defaults to the latest "
            "backup; pass a TIMESTAMP for a specific one. Exits non-zero on any "
            "bit-rot/tamper mismatch."
        ),
    )
    parser.add_argument(
        "--prune-backups",
        nargs="?",
        type=int,
        const=DEFAULT_BACKUP_KEEP_LAST,
        default=None,
        metavar="KEEP",
        dest="prune_backups",
        help=(
            "Standalone: delete old timestamped backups under --output/--project "
            f"(else CWD) .agentteams-backups/, keeping the newest KEEP (default "
            f"{DEFAULT_BACKUP_KEEP_LAST}). The single newest backup is NEVER "
            "deleted, even with KEEP 0. Combine with --keep-within-days to also "
            "retain anything younger than N days, and --dry-run to preview. This "
            "bounds backup growth; distinct from --prune (which removes stale "
            "*agents*, not backups)."
        ),
    )
    parser.add_argument(
        "--keep-within-days",
        type=int,
        default=None,
        metavar="DAYS",
        dest="keep_within_days",
        help=(
            "Modifier for --prune-backups: in addition to the newest KEEP, retain "
            "any backup younger than DAYS (a backup with an unparseable timestamp "
            "is always kept, fail-safe)."
        ),
    )
    parser.add_argument(
        "--backup-mirror",
        default=None,
        metavar="DIR",
        dest="backup_mirror",
        help=(
            "Modifier for --update: after a backup is written, also copy it to "
            "DIR/<output-slug>/<timestamp>/ (e.g. a NAS or synced folder) so the "
            "recovery net survives local disk loss. Best-effort and non-fatal — a "
            "mirror failure warns but never breaks the update. Overrides the "
            "AGENTTEAMS_BACKUP_MIRROR environment variable."
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

    # CP-1: the standalone integrity/retention ops are mutually exclusive. Each
    # is a terminal read-or-prune action with its own exit-code contract;
    # combining them would silently run only the first in app.py dispatch order.
    _standalone_ops = [
        ("--verify-integrity", bool(getattr(args, "verify_integrity", False))),
        ("--verify-backup", getattr(args, "verify_backup", None) is not None),
        ("--prune-backups", getattr(args, "prune_backups", None) is not None),
    ]
    _active_ops = [flag for flag, on in _standalone_ops if on]
    if len(_active_ops) > 1:
        parser.error(
            f"{' and '.join(_active_ops)} are mutually exclusive "
            "(each is a standalone integrity/retention operation)"
        )

    # --keep-within-days is a modifier for --prune-backups; alone it does nothing.
    if (
        getattr(args, "keep_within_days", None) is not None
        and getattr(args, "prune_backups", None) is None
    ):
        parser.error("--keep-within-days only applies with --prune-backups")

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
