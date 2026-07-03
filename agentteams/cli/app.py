"""
app.py — CLI entry point: argument dispatch + the generate/update/check pipeline.

Moved verbatim from build_team.py (CH-07) — no control-flow change; the inline
security gates stay in their exact positions in the linear pipeline. The 30
helper names main uses are imported directly from their cli/agentteams modules;
the 9 build_team-RESIDENT helpers (events, migrate, auto-correct, run-log,
prune, dual-descriptor) stay in build_team (they are __file__-anchored or
self-invoke main) and are reached via a lazy `import build_team` inside main.
build_team.py re-exports main/_finalize_exit_code/_deprecated_build_team_entry.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agentteams.cli.commands import (
    _run_bridge,
    _run_convert,
    _run_interop,
    _run_prune_backups,
    _run_stale_check,
    _run_stale_restore,
    _run_verify_backup,
    _run_verify_integrity,
    _run_verify_waivers,
)
from agentteams.cli.parser import _build_parser, _validate_option_combinations
from agentteams.cli.render_pipeline import _resolve_strict_manual_mode
# run_generate holds the generate/update/check pipeline; _finalize_exit_code is
# re-exported here so build_team's shim (from agentteams.cli.app import ...) and
# tests resolve build_team._finalize_exit_code unchanged.
from agentteams.cli.generate import _finalize_exit_code, run_generate

# Repo root = three levels up from agentteams/cli/app.py (identical to
# build_team's _SCRIPT_DIR = Path(build_team.py).parent at repo root).
_SCRIPT_DIR = Path(__file__).resolve().parents[2]

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
    import build_team  # lazy: resident helpers (events/migrate/etc.) stay in build_team
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_option_combinations(parser, args)

    # --backup-mirror overrides AGENTTEAMS_BACKUP_MIRROR for this run so the
    # off-machine mirror (emit._mirror_backup) fires during any --update backup.
    # Set before fleet dispatch so re-entrant per-workspace runs inherit it.
    if getattr(args, "backup_mirror", None):
        os.environ["AGENTTEAMS_BACKUP_MIRROR"] = args.backup_mirror

    # -----------------------------------------------------------------------
    # --target-host-features: parse early so emitters can read the list.
    # Validation errors abort before any expensive work.
    # -----------------------------------------------------------------------
    try:
        from agentteams.host_features import parse_tokens as _parse_host_features
        args.host_features = _parse_host_features(getattr(args, "target_host_features", None))
    except Exception as exc:  # noqa: BLE001 — CH-24: surface any --target-host-features parse error to the user
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
    # --verify-waivers: standalone read-only audit of the security waiver log.
    # Never mints/consumes a waiver; skips the generation pipeline.
    # -----------------------------------------------------------------------
    if getattr(args, "verify_waivers", False):
        return _run_verify_waivers(args)

    # -----------------------------------------------------------------------
    # --verify-integrity / --verify-backup: standalone read-only integrity
    # checks (no description/generation needed). The exit code IS the verdict.
    # -----------------------------------------------------------------------
    if getattr(args, "verify_integrity", False):
        return _run_verify_integrity(args)
    if getattr(args, "verify_backup", None) is not None:
        return _run_verify_backup(args)

    # -----------------------------------------------------------------------
    # --prune-backups: standalone retention sweep — bound backup growth by
    # deleting old timestamped backups (never the newest). Maintenance op, not
    # a verdict; honours --dry-run and --keep-within-days.
    # -----------------------------------------------------------------------
    if getattr(args, "prune_backups", None) is not None:
        return _run_prune_backups(args)

    # -----------------------------------------------------------------------
    # --stale-check: standalone read-only staleness scan (docs + code/scripts).
    # The exit code IS the verdict (0 clean / 1 blocking). Never edits files.
    # -----------------------------------------------------------------------
    if getattr(args, "stale_check", False):
        return _run_stale_check(args)

    # -----------------------------------------------------------------------
    # --stale-restore: recover files from a --stale-remediate --yes snapshot.
    # -----------------------------------------------------------------------
    if getattr(args, "stale_restore", None) is not None:
        return _run_stale_restore(args)

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
    # --refresh-graph / --install-git-hooks: standalone commit-map ops.
    # No --description or generation pipeline needed; operate on an existing
    # tree. Resolve the target repo from --output, else --project, else CWD
    # (same precedence as --capture-baseline).
    # -----------------------------------------------------------------------
    if getattr(args, "refresh_graph", False):
        from agentteams import git_hooks as _git_hooks
        repo_root = Path(args.output or args.project or ".").resolve()
        result = _git_hooks.refresh_pipeline_graph(repo_root, dry_run=args.dry_run)
        if result.agents_dir is None:
            print(f"  ℹ  No agent files under {repo_root}; nothing to map.")
            return 0
        verb = "Would update" if (result.changed and args.dry_run) else (
            "Updated" if result.changed else "Already current"
        )
        print(f"  ✓  pipeline-graph: {verb} ({result.agent_count} agents) → {result.graph_path}")
        return 0

    if getattr(args, "install_git_hooks", False):
        from agentteams import git_hooks as _git_hooks
        repo_root = Path(args.output or args.project or ".").resolve()
        try:
            result = _git_hooks.install_pre_commit_hook(repo_root)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"  ✓  pre-commit hook {result.action}: {result.hook_path}")
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
        return build_team._run_revert_migration(project_dir)

    # -----------------------------------------------------------------------
    # --migrate: tag + overwrite (delegates back into main with --overwrite)
    # -----------------------------------------------------------------------
    if args.migrate:
        if not args.description:
            parser.error("--description is required with --migrate")
        project_dir = Path(args.project).resolve() if args.project else Path.cwd()
        return build_team._run_migrate(project_dir, argv or sys.argv[1:])

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
    # --goose-source/--goose-model/--goose-show: standalone Goose config switch
    # -----------------------------------------------------------------------
    if (
        getattr(args, "goose_source", None)
        or getattr(args, "goose_model", None)
        or getattr(args, "goose_show", False)
    ):
        from agentteams.cli.goose_switch import run_goose_switch
        return run_goose_switch(args)

    # -----------------------------------------------------------------------
    # --recipe-check: standalone Goose recipe YAML structural validation
    # -----------------------------------------------------------------------
    if getattr(args, "recipe_check", False):
        from agentteams.cli.recipe_check import run_recipe_check
        from agentteams.frameworks.goose import GooseAdapter
        if args.output:
            recipes_dir = GooseAdapter().normalize_output_path(Path(args.output).resolve())
        else:
            recipes_dir = Path(".goose/recipes").resolve()
        if not recipes_dir.is_dir():
            print(f"Error: recipes directory not found: {recipes_dir}", file=sys.stderr)
            return 1
        return run_recipe_check(recipes_dir)

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

    return run_generate(args, strict_manual_placeholders)
