"""
generate.py — the generate / update / check pipeline (extracted from cli/app.py).

run_generate is everything cli.app.main does AFTER the Layer-1 standalone-command
dispatch: dual-descriptor check, manifest build, the interleaved standalone modes
(adopt-orphans, restore-backup, scan-security, check-budget, refresh/query-index,
--check), and the render/emit pipeline with its inline security gates (order
preserved exactly). Verbatim move (CH-07). build_team-resident helpers
(events/migrate/prune/run-log/auto-correct) are reached via a lazy
`import build_team` so there is no module-level import cycle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentteams import analyze, emit, ingest, liaison_logs, render, template_pins
from agentteams.cli import security_gate
from agentteams.cli.artifacts import (
    _emit_codex_mcp_if_enabled,  # noqa: F401  (re-exported: tests reach it via generate.)
    _emit_host_mcp_artifacts_if_enabled,
    _emit_mcp_servers_if_enabled,  # noqa: F401  (re-exported: tests reach it via generate.)
    _refresh_existing_code_index,
    _run_retrieval_utility_modes,
    _write_delivery_receipt,
    _write_eval_suite,
    _write_memory_index,
    _write_model_routing,
)
from agentteams.cli import standalone_modes
from agentteams.cli.exit_codes import _finalize_exit_code
from agentteams.cli.json_mode import json_stdout, run_with_json_stdout
from agentteams.cli.output_target import refuse_foreign_target, resolve_output_dir
from agentteams.cli.post_emit_checks import _post_emit_security_scan
from agentteams.cli.render_pipeline import (
    _apply_placeholder_policy,
    _build_final_rendered,
    _make_content_matches,
    _preserve_manual_values,
    _remove_stale_tool_agents,
)
from agentteams.errors import (
    DeliveryReceiptError,
    EvalSuiteError,
    MemoryIndexError,
    ModelRoutingError,
)
from agentteams.frameworks.registry import FRAMEWORKS

# Re-exported for cli.app and existing tests that resolve it here (carved to cli/exit_codes.py).
__all__ = ["_finalize_exit_code", "run_generate"]

_SCRIPT_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = _SCRIPT_DIR / "agentteams" / "templates"


def run_generate(
    args: argparse.Namespace,
    strict_manual_placeholders: bool,
    *,
    migrate_exemption: bool = False,
) -> int:
    """Run the generate/update/check pipeline; see `cli.json_mode` for `--json` stdout handling."""
    return run_with_json_stdout(
        _run_generate_inner, args, strict_manual_placeholders,
        migrate_exemption=migrate_exemption,
    )



def _sweep_capability_key(
    output_dir: Path, result: emit.EmitResult, *, dry_run: bool
) -> None:
    """Migrate a superseded capability key across the WHOLE agents directory.

    ``_merge_front_matter`` migrates the key for files the render produced. That misses every
    agent file written by another path — the ``--bridge-refresh`` subagent stubs above all.

    Measured 2026-08-06 by rehearsing a fleet update against an isolated copy of a real
    downstream repository: 27 of 31 agents migrated and 4 did not. All four were bridge-written,
    and ``team-builder`` — holding Bash, Write and Edit — was among them. **A partial migration
    is worse than a visible failure**, because the run reports success and the operator believes
    the grant is now enforced.

    Called from BOTH emit call sites. The first implementation was wired to the fresh-generation
    path only, so ``--update --merge`` — the command an actual fleet sweep uses — did not run it.
    That is the same one-of-two-paths shape as the defect it fixes.

    Args:
        output_dir: The generated team's agents directory.
        result: The emit result; the sweep runs only on success.
        dry_run: Report without writing.
    """
    if not result.success:
        return
    from agentteams.front_matter_reconcile import migrate_capability_key

    migrated = migrate_capability_key(output_dir, dry_run=dry_run)
    if not migrated:
        return
    verb = "would migrate" if dry_run else "migrated"
    print(f"\n  Capability key: {verb} {len(migrated)} file(s) from a key this framework's "
          f"runtime ignores:")
    for m in migrated[:10]:
        print(f"     {m.rel_path}: {m.old_key} -> {m.new_key}")
    if len(migrated) > 10:
        print(f"     ... and {len(migrated) - 10} more")


def _run_generate_inner(
    args: argparse.Namespace,
    strict_manual_placeholders: bool,
    *,
    migrate_exemption: bool = False,
) -> int:
    import build_team  # lazy: resident helpers (events/migrate/prune/run-log) stay in build_team
    build_team._check_dual_descriptor(args)

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
    manifest["no_vscode_tasks"] = bool(getattr(args, "no_vscode_tasks", False))

    project_name = manifest["project_name"]
    project_type = manifest["project_type"]
    print(f"  Project: {project_name!r}  |  Type: {project_type}  |  Framework: {framework_id}")
    print(f"  Archetypes: {', '.join(manifest['selected_archetypes'])}")
    print(f"  Components: {len(manifest['components'])}")
    print(f"  Total agents: {len(manifest['agent_slug_list'])}")

    # -----------------------------------------------------------------------
    # Step 4: Resolve output directory
    # -----------------------------------------------------------------------
    project_root, output_dir = resolve_output_dir(args, adapter, description)

    from agentteams.cli.artifacts import finalize_privilege_wiring as _finalize_privilege

    _finalize_privilege(manifest, list(getattr(args, "host_features", []) or []), framework_id, project_root)

    print(f"  Output directory: {output_dir}")

    _refusal = refuse_foreign_target(args, output_dir)   # fails OPEN; see cli/output_target
    if _refusal:
        print(f"[OUTPUT-TARGET] refused: {_refusal}", file=sys.stderr)
        return 1

    # -----------------------------------------------------------------------
    # Step 4·adopt: --adopt-orphans — register pre-existing custom agent files
    # into the roster before rendering, so the orchestrator declares them. Must
    # run before _build_final_rendered. Never adds to output_files (their files
    # are preserved, not regenerated). Effective only when the orchestrator is
    # (re)rendered — i.e. with --overwrite/--migrate (see flag help).
    # -----------------------------------------------------------------------
    if getattr(args, "adopt_orphans", False) and output_dir.exists():
        # Source manifest always uses .agent.md paths; slugs are extension-independent.
        _src_suffix = ".agent.md"
        # "Planned" = agent files this build will EMIT (from output_files), not
        # the roster — so legitimately-generated-but-non-roster files
        # (team-builder, content-enricher) are not mistaken for orphans.
        _emitted_slugs = {
            Path(f["path"]).name[: -len(_src_suffix)]
            for f in manifest.get("output_files", [])
            if isinstance(f, dict) and str(f.get("path", "")).endswith(_src_suffix)
        }
        # Legacy tool-<slug>.agent.md files are migrated to docs/skills, not
        # adopted as agents — never pull them back into the roster.
        _tool_doc_slugs = {ta["slug"] for ta in manifest.get("tool_agents", [])}
        # Use the framework's actual agent file extension for on-disk discovery.
        _agent_ext = adapter.get_file_extension("agent")
        _orphan_slugs = sorted(
            p.name[: -len(_agent_ext)]
            for p in output_dir.glob(f"*{_agent_ext}")
            if p.name[: -len(_agent_ext)] not in _emitted_slugs
            and p.name[: -len(_agent_ext)] not in _tool_doc_slugs
            # For Goose: skip non-recipe YAML files (require version: "1.0.0").
            and (
                _agent_ext != ".yaml"
                or 'version: "1.0.0"' in p.read_text(encoding="utf-8", errors="ignore")
            )
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

    _standalone_rc = standalone_modes.run_standalone_modes(
        args, manifest, description, output_dir, TEMPLATES_DIR
    )
    if _standalone_rc is not None:
        return _standalone_rc

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
            print(f"[SEC-GATE/INTEL-FRESHNESS] blocked: {exc}", file=sys.stderr)
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
    final_rendered = _build_final_rendered(manifest, adapter, project_name, output_dir=output_dir)

    # --reconcile-front-matter: needs the render, and nothing after it. Policy and rationale
    # live in `front_matter_reconcile`, not here.
    if getattr(args, "reconcile_front_matter", False):
        from agentteams import front_matter_reconcile as _fmr

        return _fmr.run_reconcile(args, final_rendered, output_dir)

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
                    f"[SEC-GATE/DESTRUCTIVE:overwrite-update] blocked: {exc}\n"
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
            # Names the two halves separately: no TEMPLATE/STRUCTURE change, and a live-data
            # refresh that does write. The old wording read as "this run did nothing".
            print(
                "No structural or template-content changes detected. "
                "Live-data references (security intelligence, framework research) will still be "
                "refreshed, so files carrying those fences may change."
            )
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

        # Sidecar files (e.g. .vscode/tasks.json) are not tracked by the
        # structural drift detector and are therefore absent from update_paths.
        # Include them whenever their rendered content differs from disk.
        _already_included = {p for p, _ in update_rendered}
        for _sc_path, _sc_content in final_rendered:
            if _sc_path in _already_included or _sc_path in update_paths:
                continue
            _sc_disk = emit._resolve_path(output_dir, _sc_path)
            if not _sc_disk.exists() or _sc_disk.read_text(encoding="utf-8") != _sc_content:
                update_rendered.append((_sc_path, _sc_content))

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
                build_team._heal_build_log_baseline(output_dir, manifest)
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
                _emit_host_mcp_artifacts_if_enabled(manifest, project_root)
                print(
                    "  ✓  Healed build-log baseline (no material drift; "
                    "fingerprint refreshed)."
                )
                return 0
            print("Changes detected but no matching rendered files — already up to date.")
            return 0

        # Always regenerate the team topology graph (md + companion svg) on every update
        graph_rel_path = "references/pipeline-graph.md"
        graph_svg_rel_path = "references/pipeline-graph.svg"
        if not any(p == graph_rel_path for p, _ in update_rendered):
            from pathlib import Path as _Path

            from agentteams import graph as _graph
            # Build from the UNION of on-disk agents and the freshly rendered team so
            # hand-authored specialists the (often thin) build description omits are
            # still drawn — matching render_pipeline and the git-hook refresh. Without
            # this the graph under-draws the team (e.g. 18 of 37 agents).
            graph_file_map = _graph.load_from_disk(output_dir)
            for _p, _c in final_rendered:
                if _p.endswith(".agent.md"):
                    graph_file_map[_Path(_p).name] = _c
            graph_update_content = _graph.generate_graph_document(
                graph_file_map, project_name=project_name
            )
            update_rendered.append((graph_rel_path, graph_update_content))
            update_rendered.append((
                graph_svg_rel_path,
                _graph.generate_graph_svg(graph_file_map, project_name=project_name),
            ))
            update_rendered.append((
                "references/pipeline-handoffs.svg",
                _graph.generate_graph_handoff_svg(graph_file_map, project_name=project_name),
            ))

        # --prune: delete removed files (with confirmation unless --yes)
        if args.prune and sdreport.removed_files:
            rc = build_team._prune_removed_files(sdreport.removed_files, output_dir, args.yes, args.dry_run)
            if rc != 0:
                return rc

        # Orphan advisories (CH-07: both extracted to build_team.py to keep this
        # module under the line ceiling). Agent files and reference docs present on
        # disk that the current team no longer emits — `--prune` above handles only
        # removals the build log recorded, so without these they accumulate invisibly.
        build_team._report_orphan_agent_files(
            final_rendered, output_dir, manifest,
            agent_ext=adapter.get_file_extension("agent"),
        )
        build_team._report_orphan_reference_docs(final_rendered, output_dir)

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
        build_team._persist_shrink_events(args, result, manifest, output_dir)
        _sweep_capability_key(output_dir, result, dry_run=args.dry_run)
        if args.dry_run and result.dry_run_report is not None:
            emit.print_dry_run_report(
                result, manifest,
                fmt="json" if args.json else "text",
                stream=json_stdout() if args.json else None,
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
                audit_result = build_team._attempt_auto_correct(
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
            build_team._write_run_log(manifest, result, output_dir, template_hashes)
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
            # Code index (F-CODEIDX) — lazy: keep an *existing* gitignored cache
            # fresh on --update (query-time is the primary trigger). §8.2.
            _refresh_existing_code_index(manifest, output_dir)
            if args.cost_routing:
                try:
                    _write_model_routing(manifest, output_dir)
                except (OSError, ModelRoutingError) as exc:
                    print(
                        f"  !  Model-routing write failed: {exc}",
                        file=sys.stderr,
                    )
            _emit_host_mcp_artifacts_if_enabled(manifest, project_root)
            from agentteams import git_hooks as _git_hooks
            _git_hooks.maybe_install_git_hooks(args, project_root)
            _git_hooks.maybe_refresh_architecture_map(args, project_root)
            if heal_converged:
                print(
                    "  ✓  Healed build-log baseline (no material drift; "
                    "fingerprint refreshed)."
                )
        _scan_blocked = _post_emit_security_scan(args, output_dir, manifest, result)
        _rc = _finalize_exit_code(result, args)
        return _rc or (1 if _scan_blocked else 0)

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
    # pre-fencing-snapshot git tag + --revert-migration). The exemption arrives as an
    # explicit PARAMETER from _run_migrate, not as module state — see the removal note in
    # security_gate.py. A caller that does not pass it cannot enable it.
    if not args.dry_run and args.overwrite and not migrate_exemption:
        try:
            security_gate._assert_destructive_action_allowed(output_dir, action="overwrite")
        except RuntimeError as exc:
            print(f"[SEC-GATE/DESTRUCTIVE:overwrite] blocked: {exc}", file=sys.stderr)
            return 1
    elif not args.dry_run and args.overwrite and migrate_exemption:
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
    build_team._persist_shrink_events(args, result, manifest, output_dir)

    _sweep_capability_key(output_dir, result, dry_run=args.dry_run)

    # Durable record of this run's decisions; silent when nothing to attribute.
    if not args.dry_run:
        from agentteams import update_report as _ur
        _ur.report_run(result, output_dir, backup_path)

    if args.dry_run and result.dry_run_report is not None:
        emit.print_dry_run_report(
            result, manifest,
            fmt="json" if args.json else "text",
            stream=json_stdout() if args.json else None,
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
            audit_result = build_team._attempt_auto_correct(
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
        build_team._write_run_log(manifest, result, output_dir, template_hashes)
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
        _emit_host_mcp_artifacts_if_enabled(manifest, project_root)
        from agentteams import git_hooks as _git_hooks
        _git_hooks.maybe_install_git_hooks(args, project_root)
        _git_hooks.maybe_refresh_architecture_map(args, project_root)

    _scan_blocked = _post_emit_security_scan(args, output_dir, manifest, result)
    _rc = _finalize_exit_code(result, args)
    return _rc or (1 if _scan_blocked else 0)
