"""multi_sync.py — multi-framework pinned sync orchestrator.

Implements the operator-locked model (2026-08-12, revised by operator ruling 2026-11-04):

- **Pin authority.** Only the bootstrap pin's changes reach the canonical hub and
  project to the other frameworks. A peer's difference is proposed for review and
  never absorbed: a peer's render differs just by being written for that framework,
  and absorbing that overwrote the pin's own agent files. Every conflict and
  proposal is written to a durable log for after-the-fact review. A deliberate peer
  edit moves through the pin, or through ``--absorb-from``.
- **Reconciliation order:** the pin framework is processed first and
  authoritatively (its native-moved fields, including conflicts and
  capabilities, are absorbed into canonical). Peers are processed next, and only
  log: ``native-moved`` is proposed (a capability change is withheld), and
  ``both-moved-conflict`` keeps canonical. The pin's roster changes are carried too
  (:mod:`agentteams.sync_roster`); a peer's are not.
- **Projection:** after reconciliation, canonical is projected to every
  framework, and every baseline is rewritten from the on-disk result so the
  next run does not re-absorb this run's own output (thrash guard).
- **Change detection:** git diff between the last synced commit and the working
  tree; a framework is "changed" when any path under its agents directory moved.

Reuses the shipped primitives: :mod:`agentteams.interop`,
:mod:`agentteams.canonical`, :mod:`agentteams.sync_classifier`,
:mod:`agentteams.sync_baseline`, and the pin contract in
:mod:`agentteams.sync_pin`.
"""

from __future__ import annotations

import csv
import json
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.git_exec import run_git
from agentteams.backup import DEFAULT_BACKUP_KEEP_LAST, backup_before_projection
from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.frameworks._sandbox_emit import (
    SIBLING_DENY_DIRS_KEY,
    TEAM_MARKER_REL,
    VERIFY_KEY_STORE_SENTINEL_REL,
    VERIFY_KEY_STORE_SENTINEL_TEXT,
    present_sibling_deny_dirs,
)
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.frameworks.registry import FRAMEWORK_IDS, FRAMEWORKS, agents_dir_for
from agentteams.interop import export_to_cai, import_from_cai
from agentteams.interop_grant_check import ProjectionPolicy
from agentteams.sync_baseline import load_baseline, write_baseline
from agentteams.sync_classifier import Action, Classification, classify_sync
from agentteams.sync_hub_refresh import refresh_hub_for_projection, refuse_unsafe_pin_instructions
from agentteams.sync_roster import apply_pin_roster, prune_hub_agents, refuse_empty_pin, remove_dropped_peer_agents
from agentteams.sync_pin import (
    DEFAULT_CANONICAL_REL,
    read_pin,
    save_pin,
    update_last_synced_commit,
)

__all__ = [
    "CONFLICT_LOG_SUBPATH",
    "ConflictRecord",
    "SyncResult",
    "sync_init",
    "run_sync",
    "framework_agents_dir",
]

#: Durable, human-reviewable conflict log (append-only).
CONFLICT_LOG_SUBPATH = ".agentteams/sync-conflicts.log.csv"

_CONFLICT_LOG_HEADER = [
    "date", "framework", "agent", "field", "classification", "resolution", "note",
]


@dataclass
class ConflictRecord:
    """One documented conflict or withheld peer capability change."""

    framework: str
    agent: str
    field_name: str
    classification: str
    resolution: str
    note: str


@dataclass
class SyncResult:
    """Outcome of a sync run."""

    changed_frameworks: list[str] = field(default_factory=list)
    projected_frameworks: list[str] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    applied_fields: int = 0
    dry_run: bool = False
    note: str = ""
    notices: list[str] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        """True if any framework changed and reconciliation ran."""
        return bool(self.changed_frameworks)


def framework_agents_dir(root: Path, framework: str) -> Path:
    """Return a framework's agents directory under a project root.

    Args:
        root: The project root directory.
        framework: A registered framework id.

    Returns:
        The absolute agents directory for that framework.

    Raises:
        ValueError: If ``framework`` is not registered.
    """
    return agents_dir_for(framework, Path(root))


def _reject_directory_collisions(root: Path, frameworks: list[str]) -> None:
    """Refuse a sync set where two framework ids resolve to one physical directory.

    Harmless while every framework sharing a path renders byte-identical content
    (codex/agents-md were that precedent until codex moved to `.codex/agents`,
    2026-09-29). It stops being
    harmless once two frameworks can render DIFFERENT content for the same
    agent at the same path — copilot-vscode and copilot-cli, since the P1
    convergence (2026-08-15), share `.github/agents` but copilot-cli strips
    handoffs that copilot-vscode keeps. `_project_and_rebaseline` writes each
    framework in turn with no dedup guard, so whichever runs last would
    silently overwrite the other's legitimately different content — and the
    victim's own `write_baseline` call would then absorb the winner's output
    as if it were its own native state. There is no correct merge here (one
    file cannot be both shapes at once), so this fails fast instead of risking
    the silent loss. See references/agentteams-remediation-log.csv (P1 row).
    """
    by_dir: dict[Path, list[str]] = {}
    for fw in frameworks:
        d = framework_agents_dir(root, fw).resolve()
        by_dir.setdefault(d, []).append(fw)
    collisions = {d: fws for d, fws in by_dir.items() if len(fws) > 1}
    if not collisions:
        return
    detail = "\n".join(f"  {d}: {', '.join(fws)}" for d, fws in collisions.items())
    raise ValueError(
        "cannot sync frameworks that share one physical agents directory — "
        "their rendered content can genuinely differ, so whichever is "
        "processed last would silently overwrite the other:\n" + detail +
        "\nDrop one of each colliding pair from `frameworks`, or sync them "
        "as separate pinned sets."
    )


# ---------------------------------------------------------------------------
# privilege propagation (C2, audit 2026-W39): pinned-sync projection must carry the pin's
# brief privilege posture so a confined/exclusive team still emits its OS boundary. Privilege
# is a TEAM/brief-level property (never in agent files), so it is captured from the pin's
# .agentteams/brief.json into a team-level cai["privilege"] block (which round-trips through
# team.cai.json), and the projection re-emits only the sandbox/privilege artifacts per
# framework (NOT .goosehints etc. — no unrelated churn on the sync path). P00: the dedicated
# os_sandbox switch is captured raw beside the profile (absent: derived from privilege_profile as
# before; "off" suppresses the projected boundary; "on" projects one under cooperative); the
# projection recomputes the effective state (sandbox_state.effective_sandbox_state) — no derived
# "effective" key is ever stored.
# ---------------------------------------------------------------------------

#: Rel-path basenames (and suffixes) of the sandbox/privilege artifacts to (re)emit during
#: projection. Deliberately NARROW: only the OS-boundary files, never the generic extras.
_PRIVILEGE_ARTIFACT_BASENAMES: frozenset[str] = frozenset({
    "sandbox.sb", "config.yaml.agentteams.example",          # goose Seatbelt
    "settings.hooks.example.json", "constitutional-gate.py",  # claude sandbox block + gate
    "agentteams-python",                                      # the gate's (untracked) interpreter pin
    "confine-run.sh", "mac-escape-tests.sh",                  # neutral launcher + mac deny-test
})

def _read_brief_privilege(root: Path) -> dict[str, Any]:
    """Return the pin's brief privilege posture, or ``{}`` when non-confining/absent.

    Finds the team's brief as fleet discovery does (``fleet._resolve_descriptor``: ``.agentteams/brief.json``,
    else the repository-root ``brief.json``, else the generated ``_build-description.json``). Reading only the
    first left every team whose brief is at the root without its sandbox/ on sync. A brief other than
    ``.agentteams/brief.json`` counts only when it sets ``privilege_profile`` itself: a stub or a foreign
    ``brief.json`` that merely inherits the default never starts a sandbox rollout on sync. A brief that is a
    symlink, or resolves outside ``root``, is ignored. Returns only the privilege fields, with
    ``privilege_profile_explicit`` derived from whether the brief set the field (so the fail
    -closed gate flip's explicit-opt-in contract is preserved across projection — audit
    SEV-3#1). Returns ``{}`` when there is no brief, the profile is cooperative/absent, or the
    file is unreadable (projection then emits no boundary, exactly as before — fail safe).
    """
    from agentteams.fleet import _resolve_descriptor

    root = Path(root)
    path = _resolve_descriptor(root)
    if path is None or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        return {}
    try:
        brief = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(brief, dict):
        return {}
    if "privilege_profile" not in brief and path != root / ".agentteams" / "brief.json":
        return {}
    # Normalize through the SAME default as generation (a missing key → confined as of
    # 2026-W39), so a team that merely ACCEPTS the default still gets its boundary projected —
    # otherwise sync would under-protect exactly the default the flip made universal
    # (adversarial closeout #1). privilege_profile_explicit stays honest (False when defaulted),
    # so the fail-closed hook flip is NOT triggered for a defaulted team (stays fail-open).
    from agentteams.host_features import validate_privilege_profile

    from agentteams.sandbox_state import validate_os_sandbox

    profile = validate_privilege_profile(brief.get("privilege_profile"))
    try:
        os_sandbox = validate_os_sandbox(brief.get("os_sandbox"))
    except ValueError as exc:
        print(f"  note: {path.name}: {exc}; no privilege posture is projected", file=sys.stderr)
        return {}
    if profile == "cooperative" and os_sandbox != "on":
        return {}  # explicit opt-out → no boundary to propagate
    priv: dict[str, Any] = {
        "privilege_profile": profile,
        "privilege_profile_explicit": "privilege_profile" in brief,
    }
    if os_sandbox is not None:
        priv["os_sandbox"] = os_sandbox
    for key in ("workspace_write_roots", "protected_read_paths", "goose_egress_proxy",
                "resolve_deny_read_abspath"):
        if brief.get(key) is not None:
            priv[key] = brief[key]
    # R1 on sync (wave-4 @security P20 finding 1): a team with WASM on or orchestrator-only had its control plane
    # write-protected; sync re-emits the boundary, so it must re-emit that protection too.
    wblock = brief.get("wasm_sandbox")
    if brief.get("write_policy") == "orchestrator-only" or (isinstance(wblock, dict) and wblock.get("enabled") is True):
        priv["control_plane_protect"] = True
    # P20 D7: the projected model-substrate switch rides with the posture, so a sync keeps the file current.
    block = brief.get("wasm_sandbox")
    substrate = block.get("model_substrate") if isinstance(block, dict) else None
    if substrate is not None:
        from agentteams.wasm_config import validate_wasm_substrate_cli

        try:
            priv["model_substrate"] = validate_wasm_substrate_cli(substrate)
        except ValueError:
            print(f"  note: {path.name}: wasm_sandbox.model_substrate must be \"on\" or \"off\"; no privilege "
                  "posture is projected", file=sys.stderr)
            return {}
    return priv


def _write_store_sentinel(root: Path, framework: str, *, dry_run: bool) -> list[str]:
    """Write the verify-key store sentinel for an agentteams team that projects no boundary (write-if-absent).

    2026-09-30: a cooperative agentteams team (marker present) still needs its store sentinel, or a confined
    sibling's launcher refuses the project. Never follows a planted symlink: the store dir must resolve inside the
    team, and the file is created O_EXCL|O_NOFOLLOW (nothing, not even a dangling link, may already be there).
    """
    from agentteams.control_plane_io import _create_exclusive

    agents_dir = framework_agents_dir(root, framework)
    sentinel = agents_dir / VERIFY_KEY_STORE_SENTINEL_REL
    if not (agents_dir / TEAM_MARKER_REL).is_file() or sentinel.exists() or sentinel.is_symlink():
        return []
    team = agents_dir.resolve()
    if not sentinel.parent.resolve().is_relative_to(team):
        return []
    if not dry_run:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        if not sentinel.parent.resolve().is_relative_to(team):  # raced into a link
            return []
        if not _create_exclusive(sentinel, VERIFY_KEY_STORE_SENTINEL_TEXT):
            return []
    return [str(team / VERIFY_KEY_STORE_SENTINEL_REL)]


def _projected_privilege_manifest(root: Path, framework: str, privilege: dict[str, Any]) -> dict[str, Any]:
    """Build the privilege-bearing manifest a projection renders the boundary from (P00: carries ``os_sandbox``)."""
    from agentteams.host_features import merge_profile_features

    profile = privilege.get("privilege_profile")
    manifest: dict[str, Any] = {
        "project_name": "SyncedTeam",
        "privilege_profile": profile,
        "privilege_profile_explicit": bool(privilege.get("privilege_profile_explicit")),
        # os_sandbox None == expand_privilege_profile(profile, framework), as before.
        "host_features": merge_profile_features([], profile, framework, os_sandbox=privilege.get("os_sandbox")),
        # Transient, never persisted: the project root for the write-root hard bans (#2).
        PROJECT_ROOT_KEY: Path(root).resolve(),
        # Transient: the sibling teams present, as generate computes them (cli/generate_helpers.py). Without
        # it a projected boundary treated every sibling as absent, so goose's sandbox.sb lost the .github
        # F-4 ancestor rename-deny literals and claude's denyWrite the sibling team dirs (remediation-log
        # 2026-11-03, SECURITY).
        SIBLING_DENY_DIRS_KEY: present_sibling_deny_dirs(Path(root)),
    }
    for key in ("os_sandbox", "workspace_write_roots", "protected_read_paths", "goose_egress_proxy",
                "resolve_deny_read_abspath"):
        if privilege.get(key) is not None:
            manifest[key] = privilege[key]
    if privilege.get("control_plane_protect") is True:
        from agentteams.frameworks._control_plane_protect import SYNC_PROTECT_KEY

        manifest[SYNC_PROTECT_KEY] = True
    # P20 D7: the model-substrate switch (the WASM half stays off here, as before: enabled defaults to false), or the
    # floor a pipeline file holds when the brief no longer sets it (so the gate sees no change).
    from agentteams.wasm_config import WASM_SUBSTRATE_KEPT_KEY

    if privilege.get("model_substrate") in ("on", "off"):
        manifest["wasm_sandbox"] = {"model_substrate": privilege["model_substrate"]}
    else:
        from agentteams.sandbox_projection import kept_value

        kept = kept_value(Path(root))
        if kept is not None:
            manifest[WASM_SUBSTRATE_KEPT_KEY] = kept
    return manifest


def _sync_projection(root: Path, framework: str, manifest: dict[str, Any], *, dry_run: bool) -> list[str]:
    """Keep ``.agentteams/sandbox-state.json`` current on sync (P20 D7); the dry pass only preflights it."""
    from agentteams import sandbox_projection

    if dry_run:
        sandbox_projection.preflight(Path(root))  # ProjectionError is a ValueError: aborts before touching disk
        return []
    outcome = sandbox_projection.emit_projection(manifest, Path(root), framework_agents_dir(root, framework))
    return [str(outcome.path.resolve())] if outcome is not None and outcome.status == "written" else []


def _check_projected_relaxation(root: Path, framework: str, manifest: dict[str, Any], state: Any,
                                *, notice: bool = False) -> None:
    """Check (never consume) the P15 gate for a projection that changes the OS boundary (P00 D7, wave-2 S1).

    Engaged when a switch is in play or the previous state (record OR on-disk evidence) had the OS
    sandbox on and the projected state has it off, whatever the reason
    (``cli.os_sandbox_switch.check_projected_relaxation``). ``notice`` prints the ``SANDBOX RELAXATION``
    lines (the sync path records no ``sandbox_state``: the next generate does).

    Raises:
        ValueError: The relaxation is refused (governed workspace, no signed decision or active exception).
    """
    from agentteams.cli import sandbox_relaxation_gate as gate
    from agentteams.cli.os_sandbox_switch import check_projected_relaxation

    try:
        states = check_projected_relaxation(framework_agents_dir(root, framework), root, framework, manifest, state)
    except gate.SandboxRelaxationError as exc:
        raise ValueError(f"[{framework}] sync refused: {exc}") from exc
    if notice and states is not None:
        from agentteams.cli.write_root_policy import warn_sandbox_relaxation

        warn_sandbox_relaxation(states[0], states[1], framework_id=framework,
                                profile_would_emit=manifest.get("privilege_profile") in {"confined", "exclusive"})


def _emit_privilege_artifacts(
    root: Path, framework: str, privilege: dict[str, Any] | None, *, dry_run: bool
) -> list[str]:
    """Emit ONLY the sandbox/privilege artifacts for ``framework`` during projection.

    Builds a privilege-bearing manifest from the team-level ``privilege`` block and calls the
    framework adapter's ``extra_output_files``, writing back only the files whose basename is
    in :data:`_PRIVILEGE_ARTIFACT_BASENAMES` (the OS-boundary set). This keeps confinement
    projected across the pinned-sync frameworks without re-emitting unrelated extras. With no
    confining privilege, only the verify-key store sentinel is written, and only if absent in an
    agentteams team (build-log marker present). Returns the project-relative paths written.

    P00: the projected manifest carries the brief's ``os_sandbox``; its effective state decides
    whether a boundary is projected. ``off`` projects none (but keeps the sentinel and, for a
    confined/exclusive profile, the control-plane stubs a sibling launcher needs). A governed
    workspace refuses a sync that would drop a boundary (the switch, or any on -> off over the
    previous state, wave-2 S1) with no active exception: the P15 gate is checked with
    ``consume=False`` (sync never consumes a clearance); ungoverned, the non-dry pass prints the notice.

    Raises:
        ValueError: A refused sandbox relaxation (governed workspace), or an emitter refusal.
    """
    manifest = _projected_privilege_manifest(root, framework, privilege) if privilege else None
    from agentteams.sandbox_state import effective_sandbox_state

    state = effective_sandbox_state(manifest) if manifest is not None else None
    if manifest is not None:  # no privilege block projects nothing (and removes nothing): no relaxation to gate
        _check_projected_relaxation(root, framework, manifest, state, notice=not dry_run)
    projected = _sync_projection(root, framework, manifest, dry_run=dry_run) if manifest is not None else []
    if state is None or not state.os:
        written = projected + _write_store_sentinel(root, framework, dry_run=dry_run)
        if manifest is not None and manifest.get("privilege_profile") in {"confined", "exclusive"}:
            # Rule 12 (P00 row 18): the switch must never remove stubs a sibling confined launcher needs.
            from agentteams.control_plane_io import write_control_plane_stubs

            written += [str(p.resolve()) for p in write_control_plane_stubs(
                framework_agents_dir(root, framework), framework, manifest, dry_run=dry_run)]
        return written

    agents_dir = framework_agents_dir(root, framework)
    adapter = FRAMEWORKS[framework]()
    project = Path(root).resolve()
    written: list[str] = list(projected)
    from agentteams.frameworks.claude import guard_gate_pin_files

    # Never project a pin over/without its ignore rule (an operator-owned .gitignore is left alone).
    extras, pin_notices = guard_gate_pin_files(list(adapter.extra_output_files(manifest)), agents_dir)
    for notice in pin_notices:
        print(f"  note: {notice}", file=sys.stderr)
    for rel_path, content in extras:
        base = rel_path.rsplit("/", 1)[-1]
        # The verify-key store sentinel is matched by its full path (a bare ``README.md`` basename
        # is far too generic): the deny it backs is projected, so it must be too (bwrap cannot
        # start on a missing deny path).
        # The gate's pin travels with its ignore file (matched by full path: `.gitignore` is generic).
        if (base not in _PRIVILEGE_ARTIFACT_BASENAMES and rel_path != VERIFY_KEY_STORE_SENTINEL_REL
                and rel_path != "../hooks/.gitignore"):
            continue  # skip .goosehints / capability refs / etc. — privilege set only
        target = (agents_dir / rel_path).resolve()
        if not target.is_relative_to(project):  # a linked agents dir or a ../ path must not write outside
            print(f"  note: skipped {rel_path}: resolves outside {project}", file=sys.stderr)
            continue
        written.append(str(target))
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    # PR-D: the comment-only roster stubs the launcher/Seatbelt entries need, write-if-absent
    # (never through the overwrite loop above, which would clobber an operator roster).
    from agentteams.control_plane_io import write_control_plane_stubs

    stubs = write_control_plane_stubs(agents_dir, framework, manifest, dry_run=dry_run)
    written += [str(p.resolve()) for p in stubs]
    return written


# ---------------------------------------------------------------------------
# git change detection
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str) -> tuple[int, str]:
    """Run a git command under ``root``; return (exit_code, stdout)."""
    proc = run_git(root, *args)
    return proc.returncode, proc.stdout


def _current_head(root: Path) -> str:
    """Return the current HEAD sha, or empty string if not a git repo."""
    code, out = _git(root, "rev-parse", "HEAD")
    return out.strip() if code == 0 else ""


def _changed_paths_since(root: Path, since: str) -> list[str] | None:
    """Return repo-relative paths changed between ``since`` and HEAD.

    Commit-to-commit only (operator decision: "use diffs from commits"). A
    framework's *own* projected-but-uncommitted output therefore never
    re-triggers a sync — the loop settles once the workflow commits. Returns
    ``None`` when detection is unavailable (no git, or ``since`` does not
    resolve); the caller then treats *all* frameworks as changed.
    """
    if not since:
        return None
    code, _ = _git(root, "cat-file", "-e", f"{since}^{{commit}}")
    if code != 0:
        return None
    code, out = _git(root, "diff", "--name-only", since, "HEAD", "--")
    if code != 0:
        return None
    return [p for p in out.splitlines() if p.strip()]


def _detect_changed_frameworks(
    root: Path, frameworks: list[str], since: str
) -> list[str]:
    """Map changed paths to the frameworks whose agents directories moved."""
    changed_paths = _changed_paths_since(root, since)
    if changed_paths is None:
        return list(frameworks)  # conservative: can't tell → sync all
    root = Path(root)
    rels: dict[str, str] = {}
    for fw in frameworks:
        try:
            rel = framework_agents_dir(root, fw).resolve().relative_to(root.resolve())
            rels[fw] = str(rel)
        except (ValueError, OSError):
            rels[fw] = ""
    hit: list[str] = []
    for fw in frameworks:
        rel = rels[fw]
        prefix = "" if rel in ("", ".") else rel + "/"
        for p in changed_paths:
            if prefix and p.startswith(prefix):
                hit.append(fw)
                break
            if not prefix:  # root-level agents dir: match top-level files
                if "/" not in p:
                    hit.append(fw)
                    break
    return hit


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------

def _agents_by_slug(cai: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        a["slug"]: a for a in (cai.get("agents") or [])
        if isinstance(a, dict) and a.get("slug")
    }


def _reconcile(
    canonical_cai: dict[str, Any],
    report: Any,
    *,
    framework: str,
    is_pin: bool,
    committed: bool = True,
    dropped: list[str] | None = None,
) -> tuple[dict[str, Any], list[ConflictRecord], int]:
    """Fold one framework's classified changes into canonical.

    Args:
        canonical_cai: The current canonical CAI dict (mutated copy returned).
        report: The ``SyncReport`` from :func:`classify_sync`.
        framework: The framework being reconciled.
        is_pin: Whether ``framework`` is the bootstrap pin (authoritative).
        committed: Whether ``framework`` has a committed change in this pass. An uncommitted pin change
            still spreads (the pin wins), but each absorbed field is then logged for review.
        dropped: The agents the pin removed in this pass (``sync_roster.apply_pin_roster``).

    Returns:
        ``(updated_canonical_cai, conflicts, applied_field_count)``.
    """
    canon = _agents_by_slug(canonical_cai)
    conflicts: list[ConflictRecord] = []
    applied = 0

    for agent_report in report.agent_reports:
        slug = agent_report.agent_slug
        target = canon.get(slug)
        for fr in agent_report.field_results:
            fname = fr.field_name
            cls = fr.classification

            if fname == "__agent__":
                # The pin's roster is carried by sync_roster.apply_pin_roster before this runs. A peer's never is.
                if not is_pin:
                    gone = cls == Classification.CANONICAL_MOVED
                    if gone and fr.baseline_value is None:
                        continue  # never in this peer: an agent the pin added, which projection writes
                    resolution = ("restored (peer removed agent; the pin decides)" if gone
                                  else "removed (the pin removed this agent)" if slug in (dropped or ())
                                  else "proposed (peer-only agent; not added, add it in the pin)")
                    conflicts.append(ConflictRecord(framework, slug, fname, cls.value, resolution=resolution,
                                                    note=fr.notice))
                continue

            if target is None:
                continue

            if is_pin:
                # Pin is authoritative: absorb any field the pin moved,
                # including conflicts and capability fields.
                if cls in (Classification.NATIVE_MOVED, Classification.BOTH_MOVED_CONFLICT):
                    target[fname] = fr.native_value
                    applied += 1
                    if not committed:
                        conflicts.append(ConflictRecord(
                            framework, slug, fname, cls.value,
                            resolution="applied (uncommitted pin change; review)",
                            note="The pin's working tree differs from its last commit and wins; this spreads to "
                                 "every framework. Commit it, or restore the pin and sync again.",
                        ))
                    elif cls == Classification.BOTH_MOVED_CONFLICT:
                        conflicts.append(ConflictRecord(
                            framework, slug, fname, cls.value,
                            resolution="pin-wins (pin is bootstrap authority)",
                            note=fr.notice,
                        ))
                continue

            # Peer framework: the pin decides (operator ruling 2026-11-04). Nothing is absorbed.
            if fr.action == Action.APPLY:
                conflicts.append(ConflictRecord(
                    framework, slug, fname, cls.value,
                    resolution="proposed (peer change; not absorbed, make it in the pin)",
                    note=("Projection replaces this copy. Recover it from this framework's .agentteams-backups "
                          f"(newest {DEFAULT_BACKUP_KEEP_LAST} kept), then make the change in the pin."),
                ))
            elif cls == Classification.BOTH_MOVED_CONFLICT:
                conflicts.append(ConflictRecord(
                    framework, slug, fname, cls.value,
                    resolution="pin-wins (kept canonical)",
                    note=fr.notice,
                ))
            elif cls == Classification.NATIVE_MOVED:
                # Reaches here only for a capability field (Action.PROPOSAL):
                # never silently fanned out from a peer.
                conflicts.append(ConflictRecord(
                    framework, slug, fname, cls.value,
                    resolution="withheld (peer capability change; pin-authoritative)",
                    note=fr.notice,
                ))
            # canonical-moved / no-baseline / unchanged: nothing to absorb.

    return canonical_cai, conflicts, applied


# ---------------------------------------------------------------------------
# projection + baselines
# ---------------------------------------------------------------------------

def _check_projected_write_roots(
    root: Path, privilege: dict[str, Any], frameworks: list[str], accepted: list[str] | None,
) -> None:
    """Apply the generation-time write-root policy to a projection (follow-up #2).

    The projected roots come from the agent-writable brief. Hard bans are enforced by the pinned
    emitters; here a NEW external root needs ``--accept-write-root``. The baseline is the live
    ``.claude/settings.json`` for claude only (a protected file); every other framework has an
    empty baseline, because a previously emitted file the agent can write is not a baseline.

    Raises:
        ValueError: A hard-banned or unaccepted external root (raised before any write).
    """
    from agentteams.sandbox_state import effective_sandbox_state

    if not any(effective_sandbox_state(_projected_privilege_manifest(root, fw, privilege)).os for fw in frameworks):
        return  # P00: no projected boundary consumes the roots
    from agentteams.cli.write_root_policy import live_claude_allow_write
    from agentteams.frameworks._write_roots import (
        is_external_root,
        unaccepted_external_roots,
        validate_write_roots,
    )

    roots = list(privilege.get("workspace_write_roots") or ["."])
    project_root = str(Path(root).resolve())
    validate_write_roots(roots, project_root=project_root)
    for fw in frameworks:
        baseline = live_claude_allow_write(Path(root)) if fw == "claude" else []
        need = unaccepted_external_roots(roots, project_root=project_root, baseline=baseline,
                                         accepted=accepted or [])
        for r in roots:
            if r != "." and is_external_root(r, project_root):
                print(f"  !  SANDBOX WIDENING [{fw}]: projected allowWrite includes external root "
                      f"{r!r} (source: brief workspace_write_roots).", file=sys.stderr)
        if need:
            raise ValueError(
                f"[{fw}] new external sandbox write root(s) from the brief need operator "
                f"acceptance: {need!r}. If you intend this, re-run with "
                + " ".join(f"--accept-write-root {shlex.quote(r)}" for r in need)
                + " (operator argv only)."
            )


def _project_and_rebaseline(
    root: Path,
    canonical_dir: Path,
    canonical_cai: dict[str, Any],
    frameworks: list[str],
    *,
    dry_run: bool,
    notices: list[str] | None = None,
    accepted_write_roots: list[str] | None = None,
    dropped: list[str] | None = None,
) -> list[str]:
    """Project canonical to every framework (deleting each one's copy of a ``dropped`` agent) and rebaseline.

    Interop notices (e.g. an instruction file with no mergeable fences, left untouched) are
    appended to *notices* so the operator sees every place the projection did not land.

    Raises:
        ValueError / FileNotFoundError: when the team-level ``privilege`` block cannot be
            emitted for some framework (an unrepresentable ``workspace_write_roots`` entry, or
            a missing launcher asset). To avoid a partial projection (some agent files written,
            others not), this is surfaced UP FRONT via a dry validation pass BEFORE any write
            (adversarial closeout #3), so a bad privilege config aborts before touching disk.
    """
    privilege = canonical_cai.get("privilege") if isinstance(canonical_cai, dict) else None
    # Up-front validation: dry-emit the privilege artifacts for every framework so an
    # unrepresentable write-root / missing asset fails BEFORE the write loop mutates disk.
    if privilege:
        _check_projected_write_roots(root, privilege, frameworks, accepted_write_roots)
        for fw in frameworks:
            _emit_privilege_artifacts(root, fw, privilege, dry_run=True)
    # A projection never widens a grant (interop_grant_check): goose renders grant-scoped and each agent is re-read
    # and compared with the canonical one. Not strict: the enforcement-class / Copilot-size refusals are
    # --interop-from's. A dry pass over every framework first, so a refusal aborts before any framework is written.
    policy = ProjectionPolicy(strict=False)
    for fw in frameworks if not dry_run else ():
        import_from_cai(canonical_cai, fw, framework_agents_dir(root, fw), overwrite=True, dry_run=True,
                        preserve_existing=True, projection=policy)
    projected: list[str] = []
    for fw in frameworks:
        agents_dir = framework_agents_dir(root, fw)
        if not dry_run:
            backup_before_projection(agents_dir, fw)
        if dropped and agents_dir.is_dir():  # an agent the pin removed leaves every framework (after the backup)
            gone = remove_dropped_peer_agents(agents_dir, export_to_cai(agents_dir, fw), dropped, dry_run=dry_run)
            if gone and notices is not None:
                notices.append(f"[{fw}] removed agent(s) the pin removed: {', '.join(gone)}")
        # preserve_existing (item 3, 2026-09-29): unchanged agents keep their exact bytes
        # (bespoke front matter survives) and an existing instruction file is fence-merged,
        # never replaced by another framework's instructions.
        res = import_from_cai(canonical_cai, fw, agents_dir, overwrite=True, dry_run=dry_run,
                              preserve_existing=True, projection=policy)
        if notices is not None:
            notices.extend(f"[{fw}] {n}" for n in res.notices)
        if not dry_run:
            _mark_projected_team(agents_dir, fw, canonical_dir, canonical_cai, res.converted, notices)
        projected.append(fw)
    # C2: re-emit each framework's OS-boundary artifacts from the team-level privilege block, so a confined
    # pinned-sync team stays confined. Only once every team is marked: the sibling denies are computed from the
    # markers present, and a team marked later in the same pass was missed (remediation-log 2026-11-04).
    for fw in frameworks:
        agents_dir = framework_agents_dir(root, fw)
        _emit_privilege_artifacts(root, fw, privilege, dry_run=dry_run)
        if not dry_run:
            write_baseline(canonical_dir, fw, export_to_cai(agents_dir, fw), native_source_dir=str(agents_dir))
    return projected


def _mark_projected_team(
    agents_dir: Path, framework: str, canonical_dir: Path, canonical_cai: dict[str, Any],
    converted: list[str], notices: list[str] | None,
) -> None:
    """Write the projection's ``origin: "interop"`` team marker, control plane first.

    ``projection_marker.write_projection_marker`` never overwrites a native build-log and writes
    no marker when a control-plane write fails (the error is printed and added to *notices*).
    """
    from agentteams.projection_marker import print_marker_outcome, write_projection_marker

    outcome = write_projection_marker(
        agents_dir, framework, source_dir=canonical_dir, source_framework="canonical",
        files_written=list(converted),
        agent_slugs=[str(a.get("slug")) for a in canonical_cai.get("agents", []) if a.get("slug")],
    )
    print_marker_outcome(outcome, label=f"[{framework}] ")
    if notices is not None:
        notices.extend(outcome.notices)  # switch relaxed / kept (signing_switch)
    if outcome.error and notices is not None:
        notices.append(f"[{framework}] {outcome.error}; NO team marker was written")


def _append_conflict_log(root: Path, records: list[ConflictRecord]) -> None:
    """Append conflict records to the durable, human-reviewable log."""
    if not records:
        return
    path = Path(root) / CONFLICT_LOG_SUBPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    stamp = datetime.now(timezone.utc).date().isoformat()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(_CONFLICT_LOG_HEADER)
        for r in records:
            w.writerow([
                stamp, r.framework, r.agent, r.field_name,
                r.classification, r.resolution, r.note,
            ])


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------

def sync_init(
    root: Path,
    *,
    pin: str,
    frameworks: list[str] | None = None,
    canonical_rel: str = DEFAULT_CANONICAL_REL,
    dry_run: bool = False,
    accepted_write_roots: list[str] | None = None,
) -> SyncResult:
    """Bootstrap the pinned sync: seed canonical from the pin, project to all.

    Args:
        root: The project root directory.
        pin: The bootstrap-pin framework (seeds canonical, wins conflicts).
        frameworks: The sync set (defaults to every registered framework).
        canonical_rel: Canonical hub path relative to ``root``.
        dry_run: When set, computes without writing.
        accepted_write_roots: ``--accept-write-root`` values (operator argv only).

    Returns:
        A :class:`SyncResult`.

    Raises:
        ValueError: If ``pin`` is unregistered or absent from ``frameworks``,
            if two frameworks in ``frameworks`` resolve to the same
            physical agents directory (see :func:`_reject_directory_collisions`),
            or if the pin's instruction file is unsafe to sync from
            (see ``sync_hub_refresh.refuse_unsafe_pin_instructions``).
    """
    root = Path(root)
    fws = list(frameworks) if frameworks else list(FRAMEWORK_IDS)
    if pin not in FRAMEWORK_IDS:
        raise ValueError(f"pin {pin!r} is not a registered framework")
    if pin not in fws:
        raise ValueError(f"pin {pin!r} must be in the sync set {fws!r}")
    _reject_directory_collisions(root, fws)

    canonical_dir = root / canonical_rel
    pin_dir = framework_agents_dir(root, pin)
    seed = export_to_cai(pin_dir, pin)
    refuse_unsafe_pin_instructions(root, {"pinned_framework": pin, "frameworks": fws}, seed)
    # C2: capture the pin's brief privilege posture into the team-level canonical block so it
    # persists in team.cai.json and projection re-emits each framework's OS boundary.
    priv = _read_brief_privilege(root)
    if priv:
        _check_projected_write_roots(root, priv, fws, accepted_write_roots)  # before any write
        seed["privilege"] = priv
    if (canonical_dir / "agents").is_dir():
        refuse_empty_pin(load_canonical(canonical_dir), seed, pin_dir)  # a re-init from an empty or wrong pin
    materialize_canonical(seed, canonical_dir, dry_run=dry_run)
    notices: list[str] = []
    projected = _project_and_rebaseline(
        root, canonical_dir, seed, fws, dry_run=dry_run, notices=notices,
        accepted_write_roots=accepted_write_roots,
    )
    # A re-init keeps no hub agent the pin lacks, but deletes no framework's copy (the earlier pin may differ).
    # After the projection, so a refusal there leaves the hub's agents alone.
    pruned = prune_hub_agents(canonical_dir, seed, dry_run=dry_run)
    if pruned:
        notices.append(f"hub: removed agent(s) the pin lacks: {', '.join(pruned)} (other frameworks keep theirs)")
    if not dry_run:
        save_pin(
            root,
            pinned_framework=pin,
            frameworks=fws,
            canonical_dir=canonical_rel,
            last_synced_commit=_current_head(root),
        )
    return SyncResult(
        changed_frameworks=list(fws),
        projected_frameworks=projected,
        dry_run=dry_run,
        note=f"initialized pinned sync (pin={pin}, {len(fws)} frameworks)",
        notices=notices,
    )


def run_sync(
    root: Path,
    *,
    since: str | None = None,
    dry_run: bool = False,
    accepted_write_roots: list[str] | None = None,
) -> SyncResult:
    """Run one sync pass: detect changes, reconcile to the pin, project all.

    Args:
        root: The project root directory.
        since: Git ref to diff from (defaults to the pin's ``last_synced_commit``).
        dry_run: When set, computes without writing.
        accepted_write_roots: ``--accept-write-root`` values (operator argv only).

    Returns:
        A :class:`SyncResult`. When no framework changed, returns early with
        ``did_work == False`` and performs no writes.

    Raises:
        ValueError: If the project is not pinned (run :func:`sync_init` first),
            if two frameworks in the pinned sync set resolve to the same
            physical agents directory (see :func:`_reject_directory_collisions`),
            or if the pin's instruction file is unsafe to sync from
            (see ``sync_hub_refresh.refuse_unsafe_pin_instructions``).
    """
    root = Path(root)
    pin_doc = read_pin(root)
    if pin_doc is None:
        raise ValueError("project is not pinned — run sync_init first")

    pin = pin_doc["pinned_framework"]
    fws: list[str] = list(pin_doc["frameworks"])
    _reject_directory_collisions(root, fws)
    canonical_rel = pin_doc.get("canonical_dir", DEFAULT_CANONICAL_REL)
    canonical_dir = root / canonical_rel
    anchor = since if since is not None else pin_doc.get("last_synced_commit", "")

    changed = _detect_changed_frameworks(root, fws, anchor)
    if not changed:
        return SyncResult(note="no framework changed since last sync")

    refuse_unsafe_pin_instructions(root, pin_doc, load_canonical(canonical_dir))

    # Pin first (authoritative), then peers. Every framework, not only the changed ones: projection overwrites them
    # all, so a peer edit that isn't committed would otherwise vanish with no proposal in the log.
    ordered = [pin] + [f for f in fws if f != pin]

    all_conflicts: list[ConflictRecord] = []
    applied_total = 0
    dropped: list[str] = []  # the agents the pin removed (sync_roster), set while the pin is reconciled
    # Exported once up front: the loop writes only the hub, and the pin's roster needs the peers' agents.
    exports = {fw: export_to_cai(framework_agents_dir(root, fw), fw) for fw in ordered
               if fw == pin or framework_agents_dir(root, fw).is_dir()}
    for fw in (f for f in ordered if f in exports):
        agents_dir, native_cai = framework_agents_dir(root, fw), exports[fw]
        canonical_cai = load_canonical(canonical_dir)
        baseline = load_baseline(canonical_dir, fw)
        if fw == pin:  # the pin's added and removed agents (sync_roster), before its fields are classified
            peers = [f for f in ordered if f != pin]  # "deleted everywhere" needs every peer present (review)
            peer_slugs = ({a.get("slug") for f in peers for a in exports[f].get("agents") or []}
                          if peers and all(exports.get(f, {}).get("agents") for f in peers) else None)
            canonical_cai, roster, dropped = apply_pin_roster(canonical_cai, native_cai, baseline, root=root,
                                                              pin_agents_dir=agents_dir, peer_slugs=peer_slugs)
            all_conflicts += [ConflictRecord(fw, s, "__agent__", "roster", resolution=r, note=n) for s, r, n in roster]
            prune_hub_agents(canonical_dir, canonical_cai, dry_run=dry_run)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            canonical_dir=str(canonical_dir), native_dir=str(agents_dir), framework=fw,
        )
        canonical_cai, conflicts, applied = _reconcile(
            canonical_cai, report, framework=fw, is_pin=(fw == pin), committed=(fw in changed), dropped=dropped,
        )
        all_conflicts.extend(conflicts)
        applied_total += applied
        materialize_canonical(canonical_cai, canonical_dir, dry_run=dry_run)

    final_canonical = load_canonical(canonical_dir)
    if dry_run:  # the hub was not pruned: project what a real pass would, so the report agrees with itself
        final_canonical["agents"] = [a for a in final_canonical["agents"] if a.get("slug") not in dropped]
    # C2: refresh the team-level privilege posture from the (current) brief so a changed
    # privilege_profile propagates, and re-persist it so team.cai.json stays authoritative.
    priv = _read_brief_privilege(root)
    if priv:
        _check_projected_write_roots(root, priv, fws, accepted_write_roots)  # before persisting
        final_canonical["privilege"] = priv
        materialize_canonical(final_canonical, canonical_dir, dry_run=dry_run)
    notices: list[str] = []
    refresh_hub_for_projection(root, pin_doc, final_canonical, canonical_dir, dry_run=dry_run, notices=notices)
    projected = _project_and_rebaseline(
        root, canonical_dir, final_canonical, fws, dry_run=dry_run, notices=notices,
        accepted_write_roots=accepted_write_roots, dropped=dropped,
    )
    if not dry_run:
        _append_conflict_log(root, all_conflicts)
        update_last_synced_commit(root, _current_head(root))

    return SyncResult(
        changed_frameworks=changed,
        projected_frameworks=projected,
        conflicts=all_conflicts,
        applied_fields=applied_total,
        dry_run=dry_run,
        note=f"synced {len(changed)} changed framework(s); "
             f"{applied_total} field(s) absorbed; {len(all_conflicts)} conflict(s)",
        notices=notices,
    )
