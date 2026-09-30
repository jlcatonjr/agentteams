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
import json
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


def _framework_conformance_enabled(manifest: dict) -> bool:
    """Decide whether to emit the framework-watch ≤24h standard-conformance-check section.

    An explicit brief field ``framework_conformance_check`` (carried into the manifest as a bool)
    overrides in either direction. When absent, default **on** for teams that maintain framework
    adapters — a component with slug ``framework-adapters`` (the ``@framework-adapters-expert`` role)
    — and off otherwise, since only adapter-bearing teams have anything to keep conformant.
    """
    explicit = manifest.get("framework_conformance_check")
    if isinstance(explicit, bool):
        return explicit
    return any(c.get("slug") == "framework-adapters" for c in manifest.get("components", []))


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


def _warn_sandbox_deny_path_mismatch(manifest: dict, output_dir: Path) -> None:
    """Warn when an emitted sandbox write-denies a switch path this team does not use.

    The sandbox ``denyWrite`` / Seatbelt control plane names the switch at the framework's
    DEFAULT agents dir (``_sandbox_emit.protected_write_paths``), because the adapter emitting it
    never sees ``--output``. A team written anywhere else keeps its switch elsewhere: it is then
    unprotected, and on Linux the dangling deny path stops bwrap initializing the sandbox
    (@security condition, 2026-09-30). Only reported, never adjusted: the operator either uses
    the default dir or edits the deny path when merging the example into their settings.

    Args:
        manifest: The team manifest (``framework``, ``host_features``).
        output_dir: The team's agents dir.
    """
    from agentteams.frameworks._goose_sandbox_emit import _goose_sandbox_feature_enabled
    from agentteams.frameworks._sandbox_emit import _AGENT_PRIVILEGE_SWITCH, _sandbox_feature_enabled

    framework = manifest.get("framework") or ""
    enabled = {"claude": _sandbox_feature_enabled, "goose": _goose_sandbox_feature_enabled}.get(framework)
    if enabled is None or not enabled(manifest):
        return
    sub = tuple(Path(_AGENT_PRIVILEGE_SWITCH[framework]).parts[:2])  # e.g. (".claude", "agents")
    if tuple(output_dir.parts[-2:]) == sub:
        return
    print(
        f"  !  {framework}:sandbox write-denies the enforce_decision_signing switch at "
        f"{'/'.join(sub)}/references/agent-privilege.json (relative to the project root), but "
        f"this team writes it to {output_dir / 'references' / 'agent-privilege.json'}. The switch "
        f"is not protected there, and on Linux the missing deny path stops the sandbox starting. "
        f"Use the default agents dir, or fix the denyWrite path when merging the sandbox block.",
        file=sys.stderr,
    )


def _warn_live_sandbox_fails_open(manifest: dict, output_dir: Path) -> bool:
    """Warn when the merged Claude ``settings.json`` sandbox lacks ``failIfUnavailable``.

    A ``sandbox`` block with ``enabled: true`` but no ``failIfUnavailable: true`` fails OPEN:
    when Claude Code cannot start its sandbox (measured on Linux with ``socat`` missing) it runs
    every command unsandboxed. agentteams never edits the operator's ``settings.json``, and blocks
    merged before 2026-09-30 lack the key, so ``--update`` names the one-line fix instead. Read-only
    and informational; silent on native Windows, where the emitted block is advisory and the key
    would stop Claude Code starting.

    Args:
        manifest: The team manifest (``framework``).
        output_dir: The team's agents dir; the merged settings sit beside the emitted example,
            at ``<output_dir>/../settings.json`` (``.claude/settings.json`` by default).

    Returns:
        True iff the notice was printed.
    """
    from agentteams.frameworks._sandbox_emit import _sandbox_fails_closed_on

    if manifest.get("framework") != "claude" or not _sandbox_fails_closed_on():
        return False
    live = output_dir.parent / "settings.json"
    try:
        data = json.loads(live.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    sandbox = data.get("sandbox") if isinstance(data, dict) else None
    if not (isinstance(sandbox, dict) and sandbox.get("enabled") is True):
        return False
    if sandbox.get("failIfUnavailable") is True:
        return False
    print(
        f"  !  {live}: the merged sandbox block has no \"failIfUnavailable\": true, so it FAILS "
        "OPEN — if Claude Code cannot start its sandbox (e.g. bwrap or socat missing on Linux) "
        "it runs every command UNSANDBOXED. Fix: add \"failIfUnavailable\": true to the "
        "\"sandbox\" object (or re-merge the sandbox block from settings.hooks.example.json).",
        file=sys.stderr,
    )
    return True


def _warn_legacy_signing_keys(manifest: dict) -> bool:
    """Advise when a sandbox is emitted and legacy private keys sit on THIS host (F-1).

    Host-local and informational only: the build host may not be the host that runs the team.
    The emitted sandboxes deny the legacy files by exact path transitionally, but only the ones
    present at generation; the durable fix is moving them into the read-denied key directory.

    Args:
        manifest: The team manifest (sandbox request gates the notice).

    Returns:
        True iff the notice was printed.
    """
    from agentteams.frameworks._goose_sandbox_emit import _goose_sandbox_feature_enabled
    from agentteams.frameworks._sandbox_emit import (
        SIGNING_KEY_DIR,
        _sandbox_feature_enabled,
        legacy_signing_key_files,
    )

    if not (_sandbox_feature_enabled(manifest) or _goose_sandbox_feature_enabled(manifest)):
        return False
    legacy = legacy_signing_key_files()
    if not legacy:
        return False
    print(
        f"  !  {len(legacy)} private key file(s) in the pre-F-1 location on this host "
        f"({', '.join(legacy)}). The emitted sandbox denies them by exact path for now; move them "
        f"into the read-denied {SIGNING_KEY_DIR} with "
        "references/authorized-verify-keys/provision-operator-signing-key.sh --migrate.",
        file=sys.stderr,
    )
    return True


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
    _warn_live_sandbox_fails_open(manifest, output_dir)
    _warn_legacy_signing_keys(manifest)
    if path is None:
        return
    _warn_sandbox_deny_path_mismatch(manifest, output_dir)
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


#: The native agents directory of each framework, relative to its project root.
_NATIVE_AGENTS_SUBPATHS: dict[str, tuple[str, str]] = {
    "claude": (".claude", "agents"),
    "goose": (".goose", "recipes"),
    "copilot-vscode": (".github", "agents"),
    "copilot-cli": (".github", "agents"),
}


def _bridge_gate_roots(project_root: Path, output_dir: Path, framework_id: str) -> list[Path]:
    """Project roots the D3 bridge gate must inspect for this run.

    ``project_root`` is whatever ``--output`` named. Passing the agents directory itself
    (``--output <project>/.claude/agents``) made it that directory, so the gate looked for
    ``.claude/agents/references/bridges/`` and never fired: the same bridge target was gated or
    not depending only on how ``--output`` was spelled. Found 2026-09-30 while reproducing the
    researchteam mixed-target report. When the write target is the framework's native agents
    directory, its grandparent is the real project root and is checked too — for the per-target
    bridge MANIFEST only (see :func:`_bridge_gate_refusal`). The entry-file fence signal is not
    framework-exclusive there: a copilot-cli bridge fences `.github/copilot-instructions.md`, which
    is also a copilot-vscode entry file, so applying it at the real root would refuse a canonical
    copilot-vscode update (fleet's `--output <ws>/.github/agents`).
    """
    roots = [project_root]
    sub = _NATIVE_AGENTS_SUBPATHS.get(framework_id)
    if sub and tuple(output_dir.parts[-2:]) == sub:
        real_root = output_dir.parent.parent
        if real_root != project_root:
            roots.append(real_root)
    return roots


def _update_target_is_bridge(
    project_root: Path, framework_id: str, *, manifest_only: bool = False
) -> bool:
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

    ``manifest_only`` restricts detection to the per-target bridge manifest (the one signal that
    names its target framework unambiguously).
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
    if manifest_only:
        return False
    for entry in _bridge_entry_files(project_root, framework_id):
        try:
            if entry.is_file() and _BRIDGE_FENCE_BEGIN_RE.search(
                entry.read_text(encoding="utf-8", errors="ignore")
            ):
                return True
        except OSError:
            continue
    return False


def _bridge_gate_refusal(project_root: Path, output_dir: Path, framework_id: str) -> str | None:
    """The D3 ``--update`` bridge-gate refusal text, or ``None`` when the target is not a bridge.

    An ``--update`` against a BRIDGE target would silently materialize a full native team (the
    missing build-log makes the structural diff treat every file as an addition), so the caller
    fails closed on a positively detected bridge unless the operator passes
    ``--materialize-native``.

    A MIXED target, where a native team with its own build-log already lives inside the bridge,
    gets different advice. ``--bridge-merge`` never touches those native files, so pointing at it
    sent operators down a route that cannot refresh what they asked to refresh (researchteam,
    2026-09-30). For them ``--materialize-native`` with ``--merge`` is a drift-aware merge against
    the existing build-log.

    Args:
        project_root: The root ``--output`` named (see :func:`_bridge_gate_roots`).
        output_dir: The resolved agents directory this run writes.
        framework_id: The target framework.

    Returns:
        The refusal message, or ``None`` when no bridge signal is present.
    """
    if not any(
        _update_target_is_bridge(root, framework_id, manifest_only=root != project_root)
        for root in _bridge_gate_roots(project_root, output_dir, framework_id)
    ):
        return None
    native_log = output_dir / "references" / "build-log.json"
    if native_log.is_file():
        return (
            f"Error: the --update target is a BRIDGE to a canonical framework that ALSO holds a "
            f"native {framework_id!r} team ({native_log} present). --bridge-merge refreshes only "
            f"the bridge and never touches these native files. Re-run with --materialize-native to "
            f"update the native team in place: with --merge this is a drift-aware merge against its "
            f"existing build-log, not a from-scratch materialization."
        )
    return (
        f"Error: the --update target is a BRIDGE to a canonical framework "
        f"(structured AGENTTEAMS-BRIDGE marker / references/bridges/*-to-{framework_id}/"
        f"bridge-manifest.json present). Proceeding would materialize a full NATIVE "
        f"{framework_id!r} team over the bridge (every file read as an addition). "
        f"Re-run with --bridge-merge to refresh the BRIDGE (safe, content-preserving), "
        f"or with --materialize-native to intentionally generate a native team here."
    )


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
    # Live-refresh artifacts whose VALUE drift is expected on every render (security intel from
    # CISA KEV / NVD / OSV rendered via fences._LIVE_DATA_FENCES; the manifest-mode
    # runtime-handoffs). Single source of truth for BOTH the manifest-promotion reconciliation
    # below and the --check drifted-verdict exemption further down (was duplicated).
    live_refresh_files = {
        "references/security-vulnerability-watch.reference.md",
        "references/security-vulnerability-watch.json",
    }
    if adapter.handoff_delivery_mode() == "manifest":
        live_refresh_files.add("references/runtime-handoffs.json")

    if sdreport is not None and sdreport.manifest_changed and any(
        e.get("_reason") in drift._MANIFEST_PROMOTION_REASONS
        for e in sdreport.drifted_files
    ):
        check_final = _build_final_rendered(manifest, adapter, project_name)
        drift.refine_manifest_promotion(
            sdreport,
            _make_content_matches(output_dir, dict(check_final), live_refresh_files),
        )

    # Print structural diff under the same condition `--update` uses
    # (R1c — print on has_changes, not just on added/removed).
    if sdreport is not None and sdreport.has_changes:
        print(f"\nStructural changes for {project_name!r}:")
        drift.print_structural_diff_report(sdreport)
    # The live-refresh artifacts (live_refresh_files, defined above) change VALUES every render
    # because their upstream feed moved, so a manifest-values drift in them is expected — not a
    # reason to fail --check (a consumer's daily security-maintenance re-renders current intel and
    # would otherwise fail every run purely because the feed moved). Exempt them from the drifted
    # verdict, but ONLY when the drift reason is a manifest-promotion (values/fingerprint) reason:
    # a genuine TEMPLATE-CONTENT drift on these same paths is NOT a feed refresh and still fails
    # (belt-and-suspenders on top of dreport's independent template-hash drift). Added/removed
    # files and team-membership changes are composition changes and always fail.
    structural_fail = False
    if sdreport is not None:
        non_exempt_drift = [
            e for e in sdreport.drifted_files
            if not (
                e.get("path") in live_refresh_files
                and e.get("_reason") in drift._MANIFEST_PROMOTION_REASONS
            )
        ]
        structural_fail = bool(
            sdreport.added_files
            or non_exempt_drift
            or sdreport.team_membership_changed
        )
    has_any = dreport.has_drift or structural_fail
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
