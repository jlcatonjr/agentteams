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
    "               --bridge-source-framework <claude|copilot-cli|copilot-vscode|goose|canonical> \\\n"
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
                ("--absorb-from", bool(getattr(args, "absorb_from", None))),
                ("--self", bool(getattr(args, "self_update", False))),
                ("--fleet", getattr(args, "fleet", None) is not None),
                ("--package-team", bool(getattr(args, "package_team", None))),
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
            ("stale_check", "--stale-check"), ("package_team", "--package-team"),
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

    # agents-md / codex are generate-only AGENTS.md emitters for the
    # convert/bridge paths (their instructions-file emission still hardcodes
    # copilot/claude names and would mislabel the file). The interop path IS
    # supported as of F.2: import_from_cai writes the framework-owned
    # AGENTS.md next to the target agents dir, and both are valid CAI
    # source/target values (agent-cai.schema.json v2 enum). agents-md import
    # is best-effort by nature — its rendered output carries no front matter,
    # so capabilities/handoffs land inferred-or-empty (surfaced via
    # compatibility-report.md). codex (F.4) is thin and delegates to the same
    # agents-md rendering, so it shares this guard.
    if getattr(args, "framework", None) in ("agents-md", "codex"):
        for attr, flag in (
            ("convert_from", "--convert-from"),
            ("bridge_from", "--bridge-from"),
        ):
            if getattr(args, attr, None):
                fw = getattr(args, "framework")
                parser.error(
                    f"--framework {fw} is a generate-only AGENTS.md emitter and "
                    f"cannot be a {flag} target. Generate a team with "
                    f"`--framework {fw} --description …`, or use the interop "
                    f"path (`--interop-from`), which supports {fw} targets."
                )

    # F.2: interop-to-goose is now supported — the old refusal predates C.3
    # (CAI captures handoffs and the goose adapter renders sub_recipes from
    # them natively), so the "disconnected pile of recipes" failure mode no
    # longer applies. goose is a valid CAI source/target value
    # (agent-cai.schema.json v2 enum).

    # G.1 (plan §5.7): canonical is an INTEROP-ONLY pseudo-framework — the
    # durable on-disk CAI directory (team.cai.json + agents/ + skills/). It
    # has no generation template and no convert/bridge path; it dispatches to
    # canonical.py (materialize/load). Bundle mode is refused here too (the
    # run-time refusal in interop.run_interop stays as the backstop).
    if getattr(args, "framework", None) == "canonical":
        if not getattr(args, "interop_from", None):
            parser.error(
                "--framework canonical is interop-only: pair it with "
                "--interop-from <source team dir> to export the durable "
                "canonical format (there is no generate/convert/bridge path "
                "for canonical)."
            )
        if getattr(args, "interop_mode", None) == "bundle":
            parser.error(
                "--framework canonical does not support --interop-mode bundle: "
                "bundle artifacts would land inside the canonical directory and "
                "corrupt its references/ tree on load. Use --interop-mode direct."
            )
    if (
        getattr(args, "interop_source_framework", None) == "canonical"
        and not getattr(args, "interop_from", None)
    ):
        parser.error(
            "--interop-source-framework canonical requires --interop-from "
            "<canonical dir> (a directory holding team.cai.json)."
        )

    # Open-items remediation OPEN-3: generic is a BRIDGE-ONLY target — it has no
    # registry.py adapter entry (FRAMEWORKS[...] lookups in cli/commands.py's
    # _run_convert/_run_interop would KeyError on it, the same reachability gap
    # canonical already needed the block above to avoid), so it must be rejected
    # here rather than left to crash further down.
    if getattr(args, "framework", None) == "generic" and not getattr(args, "bridge_from", None):
        parser.error(
            "--framework generic is bridge-only: pair it with --bridge-from "
            "<source team dir> (there is no generate/convert/interop path for "
            "generic)."
        )

    if args.convert_from and args.interop_from:
        parser.error("--convert-from and --interop-from are mutually exclusive")

    if args.bridge_from and args.convert_from:
        parser.error("--bridge-from and --convert-from are mutually exclusive")

    if args.bridge_from and args.interop_from:
        parser.error("--bridge-from and --interop-from are mutually exclusive")

    if args.bridge_from and args.interop_from:
        parser.error("--bridge-from and --interop-from are mutually exclusive")

    # C.1: --absorb-from is mutually exclusive with the other source-dir flags
    if args.absorb_from and args.convert_from:
        parser.error("--absorb-from and --convert-from are mutually exclusive")
    if args.absorb_from and args.interop_from:
        parser.error("--absorb-from and --interop-from are mutually exclusive")
    if args.absorb_from and args.bridge_from:
        parser.error("--absorb-from and --bridge-from are mutually exclusive")

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
            ("absorb_from", "--absorb-from"),
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
            ("absorb_from", "--absorb-from"),
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

    # Open-items remediation OPEN-5: --package-team is its own standalone mode
    # (like --bridge-from/--convert-from/--interop-from), mutually exclusive
    # with every other standalone op that dispatches earlier in app.py's
    # if-chain (D.7 finding: unlike the other three modes, --package-team is
    # new rather than inherited, so this closes the full dispatch-shadowing
    # set here rather than only the two combinations D.6 originally covered
    # — a silent no-op, not an error, is what happens without this: whichever
    # branch app.py reaches first wins and --package-team is dropped).
    _package_team_incompatible = [
        ("backup_mirror", "--backup-mirror"), ("fleet", "--fleet"),
        ("capture_baseline", "--capture-baseline"), ("check_baseline", "--check-baseline"),
        ("verify_waivers", "--verify-waivers"), ("redteam", "--redteam"),
        ("accept_probe_baseline", "--accept-probe-baseline"),
        ("write_integrity_manifest", "--write-integrity-manifest"),
        ("verify_integrity", "--verify-integrity"), ("verify_backup", "--verify-backup"),
        ("prune_backups", "--prune-backups"), ("stale_check", "--stale-check"),
        ("stale_restore", "--stale-restore"), ("add_fence_markers", "--add-fence-markers"),
        ("refresh_graph", "--refresh-graph"), ("refresh_architecture", "--refresh-architecture"),
        ("install_git_hooks", "--install-git-hooks"), ("self_update", "--self"),
        ("revert_migration", "--revert-migration"), ("migrate", "--migrate"),
        ("convert_from", "--convert-from"), ("interop_from", "--interop-from"),
        ("goose_source", "--goose-source"), ("goose_model", "--goose-model"),
        ("goose_show", "--goose-show"), ("recipe_check", "--recipe-check"),
        ("bridge_from", "--bridge-from"), ("absorb_from", "--absorb-from"),
    ]
    if getattr(args, "package_team", None):
        for attr, flag in _package_team_incompatible:
            if getattr(args, attr, None):
                parser.error(f"--package-team and {flag} are mutually exclusive")
    elif getattr(args, "package_source_framework", None):
        parser.error("--package-source-framework requires --package-team")
