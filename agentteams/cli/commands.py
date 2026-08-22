"""
commands.py — convert / interop / bridge CLI sub-command runners.

Extracted verbatim from build_team.py (CH-07 modular structure). build_team
re-exports these so main resolves them unchanged. Migrate/revert runners stay
in build_team (they self-invoke main). Gate calls route through the
security_gate module (Step A), so moving these does not affect gate patching.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentteams.backup import BACKUP_DIR_NAME as _BACKUP_DIR_NAME
from agentteams.cli import security_gate
from agentteams.frameworks.registry import FRAMEWORKS


def _run_verify_waivers(args: argparse.Namespace) -> int:
    """``--verify-waivers``: read-only report of every waiver's validity (never consumes).

    Resolves the project root from ``--output``/``--project`` (else CWD), reads
    ``references/security-waivers.log.csv`` via ``security_gate.verify_waivers``, and
    prints one line per waiver. Returns 0 when every waiver is valid (or none exist),
    1 when any waiver is invalid. Reuses ``_validate_security_waiver`` only — it never
    mints, consumes, or rewrites a waiver, so it adds no security surface. If the
    signing key is unset, each row reports ``invalid`` with that reason rather than
    crashing.
    """
    if getattr(args, "output", None):
        output_dir = Path(args.output).resolve()
    elif getattr(args, "project", None):
        output_dir = Path(args.project).resolve()
    else:
        output_dir = Path.cwd()

    log_path = output_dir / "references" / "security-waivers.log.csv"
    try:
        results = security_gate.verify_waivers(output_dir)
    except RuntimeError as exc:
        # CH-24: read-only CLI boundary — surface an unreadable/corrupt log as a
        # friendly error + nonzero exit rather than an uncaught traceback.
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not results:
        print(f"No security waivers found at {log_path}")
        return 0

    invalid = 0
    for entry in results:
        is_valid = entry["status"] == "valid"
        mark = "OK " if is_valid else "BAD"
        line = f"  [{mark}] {entry['waiver_id'] or '<no-id>'} (action={entry['action'] or '-'})"
        if not is_valid:
            invalid += 1
            line += f" — {entry['detail']}"
        print(line)
    print(f"\n{len(results)} waiver(s): {len(results) - invalid} valid, {invalid} invalid.")
    return 1 if invalid else 0


def _resolve_output_dir(args: argparse.Namespace) -> Path:
    """Resolve the agents output dir for a standalone read-only command, mirroring
    ``--verify-waivers``: ``--output`` → ``--project`` → CWD."""
    if getattr(args, "output", None):
        return Path(args.output).resolve()
    if getattr(args, "project", None):
        return Path(args.project).resolve()
    return Path.cwd()


def _run_verify_grants(args: argparse.Namespace) -> int:
    """``--verify-grants``: read-only report of every cross-workspace grant's validity.

    Resolves the workspace root from ``--output``/``--project`` (else CWD), validates
    every row in ``references/capability-grants.log.csv`` (signature, expiry, use-limit,
    approver roster) without consuming any, and prints one line per problem. Returns 0
    when all grants are valid (or none exist), 1 otherwise.

    Args:
        args: Parsed CLI namespace.

    Returns:
        Process exit code.
    """
    from agentteams.cli import grants

    output_dir = _resolve_output_dir(args)
    log_path = output_dir / grants.GRANT_LOG_REL
    try:
        problems = grants.verify_grants(output_dir)
    except grants.GrantError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not log_path.exists():
        print(f"No capability grants found at {log_path}")
        return 0
    if not problems:
        print(f"All capability grants valid at {log_path}")
        return 0
    for problem in problems:
        print(f"  [BAD] {problem}", file=sys.stderr)
    print(f"\n{len(problems)} invalid capability grant(s).", file=sys.stderr)
    return 1


def _run_issue_grant(args: argparse.Namespace) -> int:
    """``--issue-grant SPEC.json``: mint and sign a cross-workspace capability grant.

    Reads a JSON spec (issuer_team, holder_team, target_path, permitted_ops, expires_at,
    max_uses, approver, ticket_id, reason_code), generates a unique grant_id and issue
    timestamp, signs with ``AGENTTEAMS_GRANT_SIGNING_KEY``, and appends it to the HOLDER
    workspace's ledger — ``--output``/``--project`` must point at the holder, since the
    holder's own generation reads its ledger to widen its sandbox. Fails closed if the
    key is unset, the approver is off the holder's roster, or the spec is malformed.

    Args:
        args: Parsed CLI namespace (``issue_grant`` holds the spec path).

    Returns:
        Process exit code (0 on success).
    """
    import json
    import secrets
    from datetime import datetime, timezone

    from agentteams.cli import grants

    output_dir = _resolve_output_dir(args)
    try:
        spec = json.loads(Path(args.issue_grant).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Error: unable to read grant spec {args.issue_grant!r}: {exc}", file=sys.stderr)
        return 1
    required = ("issuer_team", "holder_team", "target_path", "permitted_ops",
                "expires_at", "max_uses", "approver", "ticket_id", "reason_code")
    missing = [k for k in required if k not in spec]
    if missing:
        print(f"Error: grant spec missing required field(s): {', '.join(missing)}", file=sys.stderr)
        return 1
    try:
        record = grants.issue_grant(
            output_dir,
            issuer_team=str(spec["issuer_team"]), holder_team=str(spec["holder_team"]),
            target_path=str(spec["target_path"]), permitted_ops=str(spec["permitted_ops"]),
            expires_at=str(spec["expires_at"]), max_uses=int(spec["max_uses"]),
            approver=str(spec["approver"]), ticket_id=str(spec["ticket_id"]),
            reason_code=str(spec["reason_code"]),
            grant_id=f"grant-{secrets.token_hex(8)}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except (grants.GrantError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Issued capability grant {record['grant_id']}: {record['issuer_team']} → "
        f"{record['holder_team']} may {record['permitted_ops']} {record['target_path']} "
        f"(expires {record['expires_at']}, max_uses {record['max_uses']})"
    )
    print(f"  appended to {output_dir / grants.GRANT_LOG_REL}")
    return 0


def _run_write_integrity_manifest(args: argparse.Namespace) -> int:
    """``--write-integrity-manifest``: re-record the enforcement-module manifest.

    Deliberately a separate, explicit act rather than part of any build. An
    auto-refreshed manifest verifies nothing: it would record whatever is on disk at
    the moment it runs, including an attacker's edit. Keeping regeneration manual is
    what makes the resulting ``git diff`` the actual control.

    This exists because the manifest's ``note`` has always instructed operators to run
    exactly this command, and the flag was never wired up. Probe E4 flagged a
    legitimate change to ``agentteams/redteam/registry.py``, the documented remedy was
    rejected by argparse, and the only working path was calling the library directly.
    A control whose recovery path is broken teaches operators to route around it.

    Returns:
        0 after writing the manifest, 1 if verification still reports a mismatch —
        which would mean the write did not take, not that the tree is dirty.
    """
    from agentteams import integrity

    repo_root = _resolve_output_dir(args)
    path = integrity.write_manifest(repo_root)
    findings = integrity.verify(repo_root)
    print(f"Wrote {path.relative_to(repo_root) if path.is_relative_to(repo_root) else path}")
    print(f"  modules recorded : {len(integrity.ENFORCEMENT_MODULES)}")
    if findings:
        print("  REFUSING to report success: verification still reports mismatches:")
        for finding in findings:
            print(f"    {finding}")
        return 1
    print("  Review the diff before committing — the diff IS the control.")
    return 0


def _run_verify_integrity(args: argparse.Namespace) -> int:
    """``--verify-integrity``: read-only classification of every generated output
    file against the build-log ``file_hashes`` baseline.

    OK / MODIFIED / TRUNCATED / MISSING / FENCE-BROKEN. Exit 1 on any
    TRUNCATED/MISSING/FENCE-BROKEN — unlike ``--update`` (where a non-zero exit
    can be a benign post-merge crash), **this exit code IS the integrity verdict
    and must be heeded.** MODIFIED is advisory (a legitimate USER-EDITABLE edit or
    drift; exit 0, listed for review).

    Also verifies the enforcement-integrity manifest
    (``references/enforcement-integrity.json``) when one exists at the resolved
    root — previously the ONLY check of that manifest lived inside the red-team
    battery, leaving no CLI path to heed the man page's "review the diff" guidance
    (remediation log, 2026-08-13). An enforcement-module mismatch exits 1.
    """
    from collections import Counter

    from agentteams import drift, integrity

    output_dir = _resolve_output_dir(args)

    enforcement_rc = 0
    manifest_path = output_dir / integrity.MANIFEST_REL_PATH
    if manifest_path.exists():
        enf_findings = integrity.verify(output_dir)
        if enf_findings:
            enforcement_rc = 1
            print(f"Enforcement manifest ({integrity.MANIFEST_REL_PATH}): MISMATCH", file=sys.stderr)
            for finding in enf_findings:
                print(f"  [ENFORCEMENT] {finding.describe()}", file=sys.stderr)
            print(
                "  An unreviewed enforcement-code change, or a reviewed one whose manifest "
                "regen was skipped. Review the module diff, then --write-integrity-manifest.",
                file=sys.stderr,
            )
        else:
            covered = len(integrity.compute_digests(output_dir))
            print(f"Enforcement manifest: OK ({covered} modules match)")

    results = drift.verify_output_integrity(output_dir)
    if not results:
        print(
            f"No build-log file_hashes under {output_dir}/references/ — cannot verify "
            "generated outputs (run --update to establish a baseline)."
        )
        return enforcement_rc

    counts = dict(Counter(e["status"] for e in results))
    suspect = [e for e in results if e["status"] in ("TRUNCATED", "MISSING", "FENCE-BROKEN")]
    print(f"Integrity of {len(results)} file(s) in {output_dir}: {counts}")
    for entry in (e for e in results if e["status"] == "MODIFIED"):
        print(f"  [MODIFIED] {entry['rel_path']} (edit or drift — review)")
    for entry in suspect:
        print(f"  [{entry['status']}] {entry['rel_path']} — {entry['note']}", file=sys.stderr)
    if suspect:
        print(
            f"\n{len(suspect)} file(s) need attention: re-run --update --merge to re-render a "
            "fenced region, or --restore-backup for a truncation/missing.",
            file=sys.stderr,
        )
        return 1
    return enforcement_rc


def _run_stale_check(args: argparse.Namespace) -> int:
    """``--stale-check``: read-only staleness scan of the resolved scan root.

    Resolves the scan root from ``--output``/``--project`` (else CWD), walks down it
    (auto-discovering generation provenance beneath), and reports stale agent docs and
    code/scripts across reliability tiers. Exit 1 on any Tier-1 (blocking) finding; 0
    otherwise. Never edits files. With ``--stale-remediate`` it also prints a guided
    (suggestion-only) remediation plan.
    """
    from agentteams import stale_detector

    root = _resolve_output_dir(args)
    if not root.exists():
        print(f"Error: stale-check target does not exist: {root}", file=sys.stderr)
        return 1
    try:
        report = stale_detector.scan_staleness(
            root, include_git=not bool(getattr(args, "stale_no_git", False))
        )
    except (OSError, ValueError) as exc:
        # CH-24: read-only CLI boundary — surface, don't traceback.
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    stale_detector.print_staleness_report(report)
    if bool(getattr(args, "stale_remediate", False)):
        # --yes promotes the preview into an applied, snapshot-protected revision pass.
        from agentteams import stale_remediate
        apply = bool(getattr(args, "yes", False))
        result = stale_remediate.apply_fixes(report, root, apply=apply)
        stale_remediate.print_fix_result(result)
        if apply:
            # exit 3 = remediation attempted but blocking items remain (manual/routed).
            return 3 if result.n_unresolved > 0 else 0
    return stale_detector.exit_code(report)


def _run_stale_restore(args: argparse.Namespace) -> int:
    """``--stale-restore [TS]``: recover files from a --stale-remediate safety snapshot
    under .agentteams-backups/stale-fix-<TS>/ (default: the latest). The recovery path
    for a revision that went wrong; verifies each backup's sha256 before writing."""
    from agentteams import stale_remediate

    root = _resolve_output_dir(args)
    ts = getattr(args, "stale_restore", None)
    if ts in (None, "latest"):
        snap = stale_remediate.latest_snapshot(root)
    else:
        cand = root / _BACKUP_DIR_NAME / f"stale-fix-{ts}"
        snap = cand if cand.is_dir() else None
    if snap is None:
        print(f"Error: no stale-fix snapshot found under {root}/.agentteams-backups/",
              file=sys.stderr)
        return 1
    try:
        restored = stale_remediate.restore_snapshot(root, snap)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Restored {len(restored)} file(s) from {snap}:")
    for rel in restored:
        print(f"  {rel}")
    return 0


def _run_verify_backup(args: argparse.Namespace) -> int:
    """``--verify-backup [TS]``: read-only check that a backup is restorable — its
    bytes match the recorded ``source_sha256`` in ``_manifest.json``. Exit 1 on any
    FAIL/MISSING. Defaults to the latest backup."""
    from agentteams import emit

    output_dir = _resolve_output_dir(args)
    backups = emit.list_backups(output_dir)
    if not backups:
        print(f"No backups found for {output_dir}", file=sys.stderr)
        return 1
    label = getattr(args, "verify_backup", None)
    if label in (None, "latest"):
        _, backup_path, _ = backups[0]
    else:
        matched = [(ts, p, c) for ts, p, c in backups if ts == label]
        if not matched:
            print(f"Backup not found: {label!r}", file=sys.stderr)
            print(f"Available: {', '.join(ts for ts, _, _ in backups)}")
            return 1
        _, backup_path, _ = matched[0]

    results = emit.verify_backup(backup_path)
    if not results:
        print(f"Backup {backup_path} has no _manifest.json — cannot verify integrity.")
        return 0
    failed = [e for e in results if e["status"] != "PASS"]
    print(f"Backup {backup_path.name}: {len(results) - len(failed)}/{len(results)} file(s) verified.")
    for entry in failed:
        print(f"  [{entry['status']}] {entry['source_path']} — {entry['note']}", file=sys.stderr)
    return 1 if failed else 0


def _run_prune_backups(args: argparse.Namespace) -> int:
    """``--prune-backups [KEEP]``: bound backup growth by deleting old timestamped
    backups under ``.agentteams-backups/``, keeping the newest KEEP. The single
    newest backup is never deleted (fail-safe), and ``--keep-within-days`` retains
    anything younger than N days. With ``--dry-run`` nothing is deleted — the plan
    is printed. Exit 0 (this is a maintenance op, not a verdict)."""
    from agentteams import emit

    output_dir = _resolve_output_dir(args)
    keep_last = getattr(args, "prune_backups", None)
    if keep_last is None:  # defensive: dispatch only reaches here when set
        keep_last = emit.DEFAULT_BACKUP_KEEP_LAST
    keep_within_days = getattr(args, "keep_within_days", None)
    dry_run = bool(getattr(args, "dry_run", False))

    result = emit.prune_backups(
        output_dir,
        keep_last=keep_last,
        keep_within_days=keep_within_days,
        dry_run=dry_run,
    )
    if not result.deleted and not result.kept:
        print(f"No backups found under {output_dir}/.agentteams-backups/")
        return 0

    verb = "Would delete" if result.dry_run else "Deleted"
    print(
        f"Backups in {output_dir}: kept {len(result.kept)}, "
        f"{verb.lower()} {len(result.deleted)} (keep_last={keep_last}"
        + (f", keep_within_days={keep_within_days}" if keep_within_days is not None else "")
        + ")."
    )
    for ts in result.deleted:
        print(f"  {verb}: {ts}")
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

    adapter = FRAMEWORKS[target_framework]()
    if output is not None:
        # Apply framework-specific path normalization (e.g. Goose appends .goose/recipes).
        target_dir = adapter.normalize_output_path(output)
    else:
        # Auto-derive: use source parent as project root, place agents under framework dir
        project_root = source_dir.parent.parent  # e.g. /repo from /repo/.github/agents
        target_dir = adapter.get_agents_dir(project_root)

    if not dry_run:
        from agentteams import security_refs as _security_refs

        convert_security = _security_refs.build_security_placeholders(
            output_dir=target_dir,
            # cross-framework external write: live security intel enforced;
            # air-gapped uses a 'security-intel-freshness' waiver, not --security-offline.
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        security_gate._assert_security_intelligence_fresh(convert_security, output_dir=target_dir)

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
        except (OSError, _json.JSONDecodeError):
            # CH-24: optional build-log read for a fallback project_name; a
            # missing/corrupt file is the known-recoverable case (use default).
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
    elif target_framework == "canonical":
        # G.1 (plan §5.5): canonical is not a registry adapter, so
        # get_agents_dir doesn't apply — default to the established
        # .agentteams/canonical/ control-directory convention.
        from agentteams.canonical import DEFAULT_CANONICAL_SUBDIR

        target_dir = source_dir.parent.parent / DEFAULT_CANONICAL_SUBDIR
    else:
        project_root = source_dir.parent.parent
        target_dir = FRAMEWORKS[target_framework]().get_agents_dir(project_root)

    if not dry_run:
        from agentteams import security_refs as _security_refs

        interop_security = _security_refs.build_security_placeholders(
            output_dir=target_dir,
            # cross-framework external write: live security intel enforced;
            # air-gapped uses a 'security-intel-freshness' waiver, not --security-offline.
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        security_gate._assert_security_intelligence_fresh(interop_security, output_dir=target_dir)

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
def _run_absorb(
    native_dir: Path,
    canonical_dir: Path,
    source_framework: str | None,
    apply: bool,
    dry_run: bool,
) -> int:
    """Execute the --absorb-from path: native→canonical three-way sync.

    Phase C of the canonical bidirectional sync plan. Reads current native
    state, current canonical state, and the last sync baseline, classifies
    every agent field using the three-way classifier (B.1), and either prints
    a report (default) or writes clean native-moved changes to canonical
    (with --absorb-apply).

    Capability-bearing fields are NEVER auto-applied (§6.1); they always
    appear in the report as proposals for human review.
    """
    from agentteams.interop import detect_framework, export_to_cai
    from agentteams.canonical import load_canonical, DEFAULT_CANONICAL_SUBDIR
    from agentteams.sync_classifier import classify_sync, Action
    from agentteams.sync_baseline import (
        load_baseline, write_baseline,
    )

    # Auto-detect framework if not specified
    framework = source_framework or detect_framework(native_dir)
    from agentteams.frameworks.registry import FRAMEWORK_IDS
    if framework not in FRAMEWORK_IDS:
        print(
            f"Error: --absorb-from framework {framework!r} is not registered. "
            f"Registered frameworks: {', '.join(FRAMEWORK_IDS)}.",
            file=sys.stderr,
        )
        return 1

    # Resolve canonical directory
    if not canonical_dir:
        canonical_dir = native_dir.parent.parent / DEFAULT_CANONICAL_SUBDIR
    canonical_dir = Path(canonical_dir)

    if not canonical_dir.is_dir():
        print(
            f"Error: canonical directory not found: {canonical_dir}\n"
            f"Run --interop-from <native_dir> --framework canonical first "
            f"to create the canonical baseline.",
            file=sys.stderr,
        )
        return 1

    dry_label = " (dry-run)" if dry_run else ""
    apply_label = " + --absorb-apply" if apply else ""
    print(
        f"Running absorb{dry_label}{apply_label}:\n"
        f"  native:       {native_dir}\n"
        f"  framework:    {framework}\n"
        f"  canonical:    {canonical_dir}"
    )

    # 1. Read current native state
    try:
        native_cai = export_to_cai(native_dir, framework)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error exporting native: {exc}", file=sys.stderr)
        return 1

    # 2. Read current canonical state
    try:
        canonical_cai = load_canonical(canonical_dir)
    except FileNotFoundError as exc:
        print(f"Error loading canonical: {exc}", file=sys.stderr)
        return 1

    # 3. Read baseline (may be None — no-baseline → report only)
    baseline = load_baseline(canonical_dir, framework)
    if baseline is None:
        print(f"\n  ⚠  No sync baseline for {framework!r} — "
              f"reporting only, nothing will be applied.")

    # 4. Classify
    report = classify_sync(
        canonical_cai, native_cai, baseline,
        canonical_dir=str(canonical_dir),
        native_dir=str(native_dir),
        framework=framework,
    )

    # 5. Print report
    print()
    print(report.to_text())

    # 6. Apply (if --absorb-apply and not dry-run)
    if apply and not dry_run and report.has_changes:
        applied_count = 0
        proposal_count = report.total_proposals

        for ar in report.agent_reports:
            for fr in ar.field_results:
                if fr.action == Action.APPLY:
                    # Write the native value into the canonical agent dict
                    for agent in canonical_cai.get("agents", []):
                        if agent.get("slug") == ar.agent_slug:
                            agent[fr.field_name] = fr.native_value
                            applied_count += 1
                            break

        if applied_count > 0:
            # Write the updated canonical back to disk
            from agentteams.canonical import materialize_canonical
            materialize_canonical(canonical_cai, canonical_dir)
            print(
                f"\n  Applied {applied_count} field(s) to canonical.\n"
                f"  {proposal_count} proposal(s) require human review "
                f"(not applied — capability keys or conflicts)."
            )
        else:
            print("\n  No auto-applicable fields found.")

        # Update the baseline to reflect the post-sync state
        write_baseline(canonical_dir, framework, native_cai,
                       native_source_dir=str(native_dir))
        print(f"  Sync baseline updated for {framework!r}.")

    elif apply and dry_run:
        print(f"\n  (dry-run) Would apply {report.total_applied} field(s), "
              f"would report {report.total_proposals} proposal(s) for review.")

    if not apply and report.has_changes:
        print(
            f"\n  Report-only mode. Pass --absorb-apply to write "
            f"{report.total_applied} clean field(s) to canonical."
        )

    return 0


_BRIDGE_AGENTS_DIR_SUFFIXES: dict[str, tuple[tuple[str, ...], ...]] = {
    "copilot-vscode": ((".github", "agents"),),
    "copilot-cli": ((".github", "copilot"),),
    "claude": ((".claude", "agents"),),
    # Goose bridge --output is the repo root (AGENTS.md lives there); normalize a
    # mistakenly-passed .goose/recipes or .goose path back up to the root.
    "goose": ((".goose", "recipes"), (".goose",)),
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


def _bridge_mode_label(check_only: bool, overwrite: bool, merge_only: bool) -> str:
    """Name the bridge mode that is actually about to run.

    The mode determines whether target entry files are read, fenced-merged, or
    overwritten unconditionally, so it is the single most consequential fact in
    the run banner. This line previously printed only ``check`` or ``generate``,
    which meant a ``--bridge-refresh`` and a bare invocation were
    indistinguishable in the output and ``--bridge-merge`` displayed as
    ``generate`` — so an operator could not confirm from the banner that the mode
    they intended was the mode that ran. See
    ``references/bridge-refresh-safety.md``.

    Args:
        check_only: ``--bridge-check`` — read-only freshness comparison.
        overwrite: ``--bridge-refresh`` or ``--overwrite`` — destructive at the target.
        merge_only: ``--bridge-merge`` — fenced regions only.

    Returns:
        One of ``"check"``, ``"refresh (overwrites target entry files)"``,
        ``"merge (fenced regions only)"`` or ``"generate (creates missing files only)"``.
    """
    if check_only:
        return "check"
    if overwrite:
        return "refresh (overwrites target entry files)"
    if merge_only:
        return "merge (fenced regions only)"
    return "generate (creates missing files only)"


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
            # cross-framework external write: live security intel enforced;
            # air-gapped uses a 'security-intel-freshness' waiver, not --security-offline.
            offline=False,
            max_items=1,
            tools=None,
            skip_nvd=True,
        )
        security_gate._assert_security_intelligence_fresh(bridge_security, output_dir=output_root)

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Running bridge{dry_label}:\n"
        f"  source:  {source_dir}\n"
        f"  source framework: {detected}\n"
        f"  target framework: {target_framework}\n"
        f"  output root: {output_root}\n"
        f"  mode: {_bridge_mode_label(check_only, overwrite, merge_only)}"
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


def _run_redteam(args: argparse.Namespace) -> int:
    """``--redteam`` / ``--accept-probe-baseline``: the standing red-team audit.

    Runs phases 1, 2, 3-skeleton and 6 against the repository, writes the four artifacts, and
    returns the exit code that distinguishes *clean* from *finding* from *harness broken*.
    Phases 4, 5 and 7 are not run: an unattended job that writes remediation code is a larger
    risk than the one it closes, and the emitted skeleton is what a human or agent takes
    through them.

    Args:
        args: Parsed CLI namespace. Reads ``redteam_probes``, ``redteam_report``,
            ``accept_probe_baseline``, ``project``/``output`` and ``dry_run``.

    Returns:
        ``0`` clean, ``1`` on a finding, ``2`` when the harness itself is broken.
    """
    from datetime import UTC, datetime

    from agentteams.redteam import cycle

    root = Path(getattr(args, "project", None) or Path.cwd()).resolve()
    now = datetime.now(UTC)
    generated_at = now.isoformat().replace("+00:00", "Z")
    dry_run = bool(getattr(args, "dry_run", False))

    if getattr(args, "redteam_freshness_check", False):
        from agentteams.redteam.freshness import run_freshness_check

        try:
            import agentteams.research  # noqa: F401
        except ImportError:
            print(
                "Error: --redteam-freshness-check needs the optional research extra. "
                "Install with: pip install agentteams[research]",
                file=sys.stderr,
            )
            return 2
        path = run_freshness_check(root, dry_run=dry_run)
        if dry_run:
            print(f"  --dry-run: would write candidates to {path}")
        else:
            print(f"  ✓  Candidates written to {path}")
            print("     Human triage only — nothing here was added to the probe corpus.")
        return 0

    if getattr(args, "accept_probe_baseline", False):
        module = getattr(args, "redteam_probes", None)
        if not module:
            print(
                "Error: --accept-probe-baseline needs --redteam-probes MODULE; there is "
                "nothing to baseline without probes.",
                file=sys.stderr,
            )
            return 2
        count, path = cycle.accept_probe_baseline(
            root, probe_module_path=module, generated_at=generated_at, dry_run=dry_run
        )
        print(f"  ✓  Recorded {count} probe outcome(s) to {path}")
        print("     Review the diff before committing: this is what accepting a changed "
              "probe outcome costs.")
        return 0

    report_dir = (
        Path(args.redteam_report).resolve()
        if getattr(args, "redteam_report", None)
        else cycle.report_dir_for(root, now.strftime("%Y-%m-%d"))
    )
    result = cycle.run_cycle(
        root,
        probe_module_path=getattr(args, "redteam_probes", None),
        report_dir=report_dir,
        generated_at=generated_at,
        dry_run=dry_run,
    )

    print("Standing red-team audit")
    for line in result.summary_lines():
        print(f"  {line}")
    if result.selfaudit.advisory:
        print(f"  advisory          : {len(result.selfaudit.advisory)} finding(s) in "
              f"hand-written historical documents (not gating)")
    if dry_run:
        print("  --dry-run: nothing written.")
    else:
        print(f"  artifacts         : {report_dir}")

    if result.exit_code == cycle.EXIT_HARNESS_BROKEN:
        print(
            "  HARNESS BROKEN — these results measure the instrument, not the target, and "
            "must not be read as a clean run.",
            file=sys.stderr,
        )
        for item in (
            result.findings.control_failures
            + result.findings.registration_problems
            + result.findings.corpus_mismatches
            + [f"live agent tree modified during the run: {p}"
               for p in result.live_tree_modifications]
        ):
            print(f"    - {item}", file=sys.stderr)
    elif result.exit_code == cycle.EXIT_FINDINGS:
        for finding in result.selfaudit.findings:
            print(f"    - {finding.render()}", file=sys.stderr)
        for pid in result.findings.exploited:
            print(f"    - probe {pid} returned EXPLOITED", file=sys.stderr)

    return result.exit_code
