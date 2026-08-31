"""The standalone modes: do one thing, report, and exit.

Carved out of ``cli/generate.py`` on 2026-08-03 under CH-07. That module's own docstring already
named these as a separate group — "the interleaved standalone modes (adopt-orphans,
restore-backup, scan-security, check-budget, refresh/query-index, ``--check``)" — sitting
*interleaved* with the render/emit pipeline they have nothing to do with.

The carve was overdue rather than chosen. `generate.py` sat at 979 lines and blocked two
consecutive features the same day, each needing nothing more than a five-line dispatch stub; in
both cases the feature's logic was moved into its own module and it *still* did not fit. Three
workarounds — moving logic out, trimming a comment, folding a call into another dispatch — is
where working around a ceiling becomes the defect it was meant to prevent.

What belongs here: a mode that answers a question or performs one action against an existing
output directory and returns an exit code, without rendering templates. What does not:
``--adopt-orphans``, which mutates the manifest *before* rendering and so is part of the
pipeline, not an alternative to it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from agentteams import emit, template_pins
from agentteams.cli import security_gate
from agentteams.cli.artifacts import _run_retrieval_utility_modes


def run_standalone_modes(
    args: Any,
    manifest: dict[str, Any],
    description: dict[str, Any],
    output_dir: Path,
    templates_dir: Path,
) -> int | None:
    """Dispatch the standalone modes.

    Returns:
        An exit code when one of them ran, or None so the caller continues into the
        render/emit pipeline. Order is preserved exactly from the original inline sequence:
        restore-backup, scan-security, check-budget, template pinning, retrieval utilities.
    """
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
            print(f"[SEC-GATE/DESTRUCTIVE:restore-backup] blocked: {exc}", file=sys.stderr)
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
        # Only HIGH findings block CI — medium/low are informational warnings.
        # Medium "high-entropy token" findings commonly appear in generated
        # markdown prose (script paths, workflow names) and are false positives.
        return 1 if report.high_count > 0 else 0

    # -----------------------------------------------------------------------
    # Step 4b.2: Handle --check-budget (3.1 + 3.4 efficiency lints)
    # -----------------------------------------------------------------------
    if args.check_budget:
        from agentteams import budget
        breport = budget.scan_directory(output_dir)
        budget.print_report(breport)
        return 1 if breport.has_failures else 0

    # -----------------------------------------------------------------------
    # Step 4b.3: Handle --check-rank (AP-2 rank-conformance; warn-only)
    # -----------------------------------------------------------------------
    if getattr(args, "check_rank", False) or getattr(args, "check_rank_strict", False):
        from agentteams import rank_conformance
        from agentteams.audit_types import _agent_file_ext

        strict = getattr(args, "check_rank_strict", False)
        agent_ext = _agent_file_ext(manifest)
        framework = str(manifest.get("framework") or "") or None
        file_map: dict[str, str] = {}
        if output_dir.is_dir():
            for agent_file in sorted(output_dir.glob(f"*{agent_ext}")):
                try:
                    file_map[agent_file.name] = agent_file.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
        findings = rank_conformance.check_rank_conformance(file_map, agent_ext, framework)
        mode = "strict" if strict else "warn-only"
        print(f"Rank-conformance scan ({mode}): {len(file_map)} agent file(s)")
        # C-3: if this framework has no tool-scope parser (codex/goose/agents-md), say so —
        # exit-neutral, so a strict run reports "not checkable" instead of a misleading clean pass.
        not_checkable = rank_conformance.rank_check_not_checkable_notice(framework, agent_ext)
        if not_checkable:
            print(f"  note: {not_checkable}")
        if not findings:
            print("  ✓ No findings.")
        else:
            label = "block" if strict else "warn"
            print(f"  {len(findings)} {label}")
            for f in findings:
                sigil = "✖" if strict else "⚠"
                print(f"  {sigil} {f.file} [{f.category}] {f.description}")
        # W19: also surface agents that declare NO capability key (an implicit maximal
        # grant the rank check skips). Advisory only — it does not affect the exit code
        # (choosing each agent's real tools: is an operator/@security decision).
        keyless = rank_conformance.agents_missing_capability_key(file_map, agent_ext, framework)
        if keyless:
            print(
                f"  note (W19): {len(keyless)} agent(s) declare no capability key, so they "
                f"inherit every tool: {', '.join(keyless)}"
            )
        # Disposition is opt-in: --check-rank is warn-only (always exit 0) while
        # the override policy beds in; --check-rank-strict is the blocking CI gate
        # (exit 1 on any finding). Finding severity is unchanged either way; real
        # over-grants route to @security (a C-3 widening).
        return rank_conformance.disposition_exit_code(findings, strict)

    # -----------------------------------------------------------------------
    # Step 4b.4: Handle --check-wiring (P1-3 — is the emitted sandbox merged live?)
    # -----------------------------------------------------------------------
    if getattr(args, "check_wiring", False):
        framework = str(manifest.get("framework") or "claude")
        if framework == "goose":
            # P1-1: goose has its own Seatbelt/GOOSE_SANDBOX wiring verifier (macOS-enforced;
            # honest exit-neutral "not enforceable here" notice on Linux/Windows).
            from agentteams.frameworks._goose_sandbox_emit import verify_goose_sandbox_wiring

            ok, messages = verify_goose_sandbox_wiring(output_dir, manifest)
        elif framework == "claude":
            from agentteams.frameworks.claude import verify_sandbox_wiring

            ok, messages = verify_sandbox_wiring(output_dir)
        else:
            # codex/copilot have no OS sandbox agentteams configures — report honestly
            # (exit-neutral) rather than running the Claude checker against a foreign tree.
            ok, messages = True, [
                f"no OS-enforced sandbox wiring is emitted for the {framework!r} framework — "
                "nothing to verify. Confine such targets from OUTSIDE the process (container "
                "+ seccomp-bpf + Landlock on Linux, plus egress filtering)."
            ]
        print(f"Sandbox wiring check ({framework}): {output_dir}")
        for msg in messages:
            print(f"  {msg}")
        return 0 if ok else 1

    # -----------------------------------------------------------------------
    # Step 4c: Handle retrieval utility modes — memory-index + code-index
    # (no template rendering; gitignored/local artifacts)
    # -----------------------------------------------------------------------
    # Template pinning (§4.6) rides the standalone-mode dispatch: --pin-templates writes and
    # exits, every other run only verifies. Policy in agentteams.template_pins.
    _pin_rc = template_pins.run_pinning(args, manifest, description, output_dir, templates_dir)
    if _pin_rc is not None:
        return _pin_rc

    _retrieval_rc = _run_retrieval_utility_modes(args, manifest, output_dir)
    if _retrieval_rc is not None:
        return _retrieval_rc
