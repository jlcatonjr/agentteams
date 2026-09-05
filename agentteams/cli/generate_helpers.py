"""Standalone helpers for the generate/update/check pipeline (CH-07 carve out of ``generate.py``).

Extracted to keep ``cli/generate.py`` under the CH-07 module-size ceiling. These are the
self-contained helpers ``_run_generate_inner`` calls (capability-key sweep, agent-privilege
config emit, enforcement-integrity verification, and bridge-target detection) plus the
``--check`` handler (:func:`_handle_check`). ``generate.py`` re-imports every name so its call
sites — and tests/redteam references that resolve them via ``cli.generate`` — keep working.

No import cycle: this module imports only lower-level modules (``emit``, ``cli.artifacts``,
``cli.render_pipeline``, ``integrity``), none of which import ``generate``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from agentteams import emit
from agentteams.cli.artifacts import (
    _write_agent_privilege_config,
    _write_management_authority_config,
)
from agentteams.cli.render_pipeline import _build_final_rendered, _make_content_matches

_SCRIPT_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = _SCRIPT_DIR / "agentteams" / "templates"


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


def _emit_agent_privilege_config(manifest: dict, output_dir: Path) -> None:
    """Write ``references/agent-privilege.json`` and print the enforce-signing notice.

    Emitted on every real generate/update so the strict agent-privilege switch is explicit
    in the team. When the switch resolves ON (the default), a notice names the opt-out — the
    "default on at update, notify after, opportunity to switch off" contract.

    Args:
        manifest: The team manifest (carries ``enforce_decision_signing``).
        output_dir: The team root.
    """
    from agentteams.cli.artifacts import AGENT_PRIVILEGE_REL_PATH

    try:
        path = _write_agent_privilege_config(manifest, output_dir)
    except OSError as exc:
        print(f"  !  agent-privilege config write failed: {exc}", file=sys.stderr)
        return
    if path is None:
        return
    if manifest.get("enforce_decision_signing"):
        print(
            "  ⚖  Strict agent-privilege enforcement (enforce_decision_signing) is ON for "
            "this team — an unsigned authorizing security-decision row will be refused "
            "(fail-closed). NOTE: this also applies to any EXISTING unsigned PASS / "
            "HALT-RETRACTED rows already in this workspace's decisions log — they will stop "
            "clearing until signing is activated (add a `signature` column or set "
            "AGENTTEAMS_DECISION_SIGNING_KEY). To turn enforcement off, set "
            f"\"enforce_decision_signing\": false in the brief and re-run --update. "
            f"Switch: {AGENT_PRIVILEGE_REL_PATH}"
        )
    else:
        print(
            "  ℹ  Strict agent-privilege enforcement (enforce_decision_signing) is OFF for "
            f"this team (legacy behavior). Switch: {AGENT_PRIVILEGE_REL_PATH}"
        )


def _emit_management_authority_config(manifest: dict, output_dir: Path) -> None:
    """Write ``references/management-authority.json`` (+ roster + ledger stub) and print a notice.

    Mirrors :func:`_emit_agent_privilege_config`: a best-effort, manifest-gated emit that is
    byte-identical-when-off. :func:`_write_management_authority_config` writes NOTHING unless the
    manifest declares management authority (``authorized_managers`` non-empty OR
    ``is_management_repo`` true), so a team that predates the feature is untouched and no notice
    is printed. When it does emit, a notice names the roster and (when written) the ledger stub.

    Args:
        manifest: The team manifest (carries ``is_management_repo`` / ``authorized_managers``).
        output_dir: The team root.
    """
    from agentteams.cli import management_directives as _md
    from agentteams.cli.artifacts import MANAGEMENT_AUTHORITY_REL_PATH

    try:
        path = _write_management_authority_config(output_dir, manifest)
    except OSError as exc:
        print(f"  !  management-authority config write failed: {exc}", file=sys.stderr)
        return
    if path is None:
        return
    authorized = manifest.get("authorized_managers") or []
    if authorized:
        print(
            "  ⚖  Management-repository endowment is ON for this team: "
            f"{len(authorized)} authorized manager team(s). Roster: {_md.AUTHORIZED_MANAGERS_REL}; "
            f"signed-directive ledger: {_md.MGMT_DIRECTIVES_LOG_REL}. Switch: "
            f"{MANAGEMENT_AUTHORITY_REL_PATH}"
        )
    else:
        print(
            "  ℹ  This team is marked a management repository (is_management_repo) with no "
            f"authorized managers yet. Switch: {MANAGEMENT_AUTHORITY_REL_PATH}"
        )


# Structured AGENTTEAMS-BRIDGE fence (mirrors bridge._FENCE_BEGIN_RE, defined locally to
# avoid importing bridge.py → canonical.py → jsonschema on the --update path). The full
# HTML-comment structure (with region + v=N) cannot match backtick-quoted Rule-14 prose,
# which is why a substring check must NOT be used here (D3).
_BRIDGE_FENCE_BEGIN_RE = re.compile(
    r"<!--\s*AGENTTEAMS-BRIDGE:BEGIN\s+[A-Za-z0-9_-]+\s+v=\d+\s*-->"
)


def _verify_enforcement_integrity() -> list:
    """Return integrity findings for the agentteams SOURCE tree's enforcement modules (R5/D5).

    Runs against the running tool's own source root (where ``references/enforcement-integrity.json``
    and ``agentteams/`` live), NOT the ``--check`` target — an installed/consumer checkout has no
    manifest, so :func:`integrity.verify` returns ``[]`` there (a natural no-op; this exercises
    only in the agentteams dev checkout / ``--self``). Git-independent by design: it compares files
    to the manifest, never to ``git status``. A present-but-unreadable manifest is itself a
    finding-worthy state (an enforcement control that cannot be verified), surfaced as one.
    """
    import agentteams as _at
    from agentteams import integrity

    source_root = Path(_at.__file__).resolve().parent.parent
    try:
        return integrity.verify(source_root)
    except RuntimeError:
        return [
            integrity.IntegrityFinding(
                rel_path=integrity.MANIFEST_REL_PATH, expected="", actual="", reason="unreadable"
            )
        ]


def _bridge_entry_files(project_root: Path, framework_id: str) -> list[Path]:
    """Framework ENTRY files a bridge marks — never agent bodies (D3).

    Scanning agent bodies is exactly what produces the false positive: the string
    ``AGENTTEAMS-BRIDGE:BEGIN`` appears backtick-quoted in constitutional Rule 14 prose
    inside a NATIVE orchestrator. Only these entry files are legitimate bridge-marker homes.
    """
    if framework_id == "claude":
        cdir = project_root / ".claude"
        return [
            project_root / "CLAUDE.md",
            cdir / "README.md",
            cdir / "agent-team.md",
            cdir / "quickstart-snippet.md",
        ]
    if framework_id in ("copilot-vscode", "copilot-cli"):
        return [
            project_root / ".github" / "copilot-instructions.md",
            project_root / ".github" / "agents" / "bridge-orchestrator.agent.md",
        ]
    if framework_id == "goose":
        return [
            project_root / "AGENTS.md",
            project_root / ".goosehints",
            project_root / ".goose" / "README.md",
        ]
    return []


def _update_target_is_bridge(project_root: Path, framework_id: str) -> bool:
    """True when the ``--update`` target is a BRIDGE to a canonical framework (D3).

    Detected ONLY by positive, structured, per-target signals:

    * a bridge manifest for a pair whose TARGET is this framework —
      ``references/bridges/<source>-to-<framework_id>/bridge-manifest.json`` (a
      ``<framework_id>-to-*`` manifest means this framework is the canonical SOURCE, NOT a
      bridge, so the ``-to-{framework_id}`` suffix match is load-bearing);
    * the structured ``AGENTTEAMS-BRIDGE`` HTML-comment fence (:data:`bridge._FENCE_BEGIN_RE`,
      which cannot match backtick-quoted Rule-14 prose) in one of this framework's ENTRY files.

    NEVER a substring scan of agent bodies, and NEVER absent-build-log (a first-generation
    native team also has no build-log, and must not be misclassified as a bridge).
    """
    bridges = project_root / "references" / "bridges"
    if bridges.is_dir():
        for pair in bridges.iterdir():
            if (
                pair.is_dir()
                and pair.name.endswith(f"-to-{framework_id}")
                and (pair / "bridge-manifest.json").exists()
            ):
                return True
    for entry in _bridge_entry_files(project_root, framework_id):
        try:
            if entry.is_file() and _BRIDGE_FENCE_BEGIN_RE.search(
                entry.read_text(encoding="utf-8", errors="ignore")
            ):
                return True
        except OSError:
            continue
    return False


def _handle_check(
    args: argparse.Namespace,
    output_dir: Path,
    manifest: dict,
    adapter,
    project_name: str,
) -> int:
    """Step 4c: --check — content drift + structural diff + enforcement-integrity, no write.

    Returns the process exit code (1 when any drift/structural/integrity change is present,
    else 0). Carved from _run_generate_inner (CH-07); behavior byte-for-byte preserved.
    """
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
    # R5 (D5): fail --check when an enforcement module drifts from — or is absent from —
    # the integrity manifest. This is the CI/pre-commit fail-closed boundary for the
    # "edited an enforcement module without regenerating the manifest" trap; an
    # unmanifested enforcement module is a SILENTLY UNVERIFIED security control
    # (integrity.py). No-op outside the agentteams source tree (no manifest → []).
    _integrity_findings = _verify_enforcement_integrity()
    if _integrity_findings:
        print(
            "\nEnforcement-integrity FAILURES (an enforcement module drifted from or is "
            "absent from references/enforcement-integrity.json — regenerate deliberately "
            "with --write-integrity-manifest after an INTENDED control change):",
            file=sys.stderr,
        )
        for _f in _integrity_findings:
            print(f"  ✗ {_f.describe()}", file=sys.stderr)
        has_any = True
    return 1 if has_any else 0
