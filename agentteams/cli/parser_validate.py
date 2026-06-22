"""
parser_validate.py — CLI option-combination validation, carved from parser.py
(CH-07 line ceiling). Holds _BRIDGE_USAGE_HINT and _validate_option_combinations;
parser.py re-exports them so importers resolve them from agentteams.cli.parser
(agentteams.cli.app, build_team, tests) unchanged.
"""

from __future__ import annotations

import argparse


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
    # The Goose source/model switch is a standalone action (dispatched in app.py before
    # the generate pipeline); it cannot be combined with generation/bridge/convert/interop.
    if (
        getattr(args, "goose_source", None)
        or getattr(args, "goose_model", None)
        or getattr(args, "goose_show", False)
    ):
        _goose_conflicts = [
            flag for flag, on in (
                ("--description", getattr(args, "description", None) is not None),
                ("--bridge-from", bool(getattr(args, "bridge_from", None))),
                ("--convert-from", bool(getattr(args, "convert_from", None))),
                ("--interop-from", bool(getattr(args, "interop_from", None))),
                ("--self", bool(getattr(args, "self_update", False))),
                ("--fleet", getattr(args, "fleet", None) is not None),
            ) if on
        ]
        if _goose_conflicts:
            parser.error(
                "--goose-source/--goose-model/--goose-show cannot be combined with "
                f"{', '.join(_goose_conflicts)} (the goose switch is a standalone action)."
            )

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
        ("--stale-check", bool(getattr(args, "stale_check", False))),
        ("--stale-restore", getattr(args, "stale_restore", None) is not None),
    ]
    _active_ops = [flag for flag, on in _standalone_ops if on]
    if len(_active_ops) > 1:
        parser.error(
            f"{' and '.join(_active_ops)} are mutually exclusive "
            "(each is a standalone, dispatch-shadowing integrity/retention operation)"
        )

    # --stale-remediate / --stale-no-git are modifiers for --stale-check.
    if getattr(args, "stale_remediate", False) and not getattr(args, "stale_check", False):
        parser.error("--stale-remediate requires --stale-check")
    if getattr(args, "stale_no_git", False) and not getattr(args, "stale_check", False):
        parser.error("--stale-no-git requires --stale-check")

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
            ("stale_check", "--stale-check"),
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

    # agents-md is a generate-only AGENTS.md emitter. The convert/interop/bridge
    # paths hardcode the instructions filename (copilot-instructions.md / CLAUDE.md)
    # and would emit a mislabeled file for this target, so reject those combinations
    # with a clear message rather than producing wrong output.
    if getattr(args, "framework", None) == "agents-md":
        for attr, flag in (
            ("convert_from", "--convert-from"),
            ("interop_from", "--interop-from"),
            ("bridge_from", "--bridge-from"),
        ):
            if getattr(args, attr, None):
                parser.error(
                    f"--framework agents-md is a generate-only AGENTS.md emitter and "
                    f"cannot be a {flag} target. Generate a team with "
                    f"`--framework agents-md --description …`, or pick a convertible "
                    f"target framework (copilot-vscode, copilot-cli, claude, goose)."
                )

    # interop-to-goose is refused: the canonical interop representation (CAI) drops
    # the handoff graph (export_to_cai strips handoff blocks + sets handoffs=[]), so
    # the Goose orchestrator would emit zero sub_recipes — a disconnected pile of
    # recipes that is not a working team. --convert-from preserves handoffs (from the
    # source agent content) and IS supported. (Bridge-to-goose has its own path.)
    if getattr(args, "framework", None) == "goose" and getattr(args, "interop_from", None):
        parser.error(
            "--interop-from with --framework goose is not supported: the interop "
            "representation drops the handoff graph Goose needs for sub_recipe "
            "delegation, so the result would be unwired. Use "
            "`--convert-from <team> --framework goose` instead (it preserves delegation)."
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

    if getattr(args, "recipe_check", False) and getattr(args, "framework", None) != "goose":
        parser.error("--recipe-check requires --framework goose")

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
