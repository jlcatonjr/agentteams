"""multi_sync.py — multi-framework pinned sync orchestrator.

Implements the operator-locked model (2026-08-12):

- **Model A + pin authority.** Frameworks are peers; a clean one-sided change
  in any framework projects to all others through the canonical hub. On a
  genuine ``both-moved-conflict`` the **bootstrap pin always wins** — even the
  pin against itself — and every conflict is written to a durable log for
  after-the-fact review.
- **Reconciliation order:** the pin framework is processed first and
  authoritatively (its native-moved fields, including conflicts and
  capabilities, are absorbed into canonical). Peers are processed next: clean
  non-capability ``native-moved`` fields absorb; ``both-moved-conflict`` keeps
  canonical (the pin's authority) and is logged; a peer *capability* change is
  never silently fanned out — it is logged for review (the shipped
  capability-safety invariant, preserved deliberately).
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
from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.frameworks._sandbox_emit import (
    TEAM_MARKER_REL,
    VERIFY_KEY_STORE_SENTINEL_REL,
    VERIFY_KEY_STORE_SENTINEL_TEXT,
)
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.frameworks.registry import FRAMEWORK_IDS, FRAMEWORKS
from agentteams.interop import export_to_cai, import_from_cai
from agentteams.sync_baseline import load_baseline, write_baseline
from agentteams.sync_classifier import Action, Classification, classify_sync
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
    if framework not in FRAMEWORKS:
        raise ValueError(f"unknown framework {framework!r}")
    return FRAMEWORKS[framework]().get_agents_dir(Path(root))


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
# framework (NOT .goosehints etc. — no unrelated churn on the sync path).
# ---------------------------------------------------------------------------

#: Rel-path basenames (and suffixes) of the sandbox/privilege artifacts to (re)emit during
#: projection. Deliberately NARROW: only the OS-boundary files, never the generic extras.
_PRIVILEGE_ARTIFACT_BASENAMES: frozenset[str] = frozenset({
    "sandbox.sb", "config.yaml.agentteams.example",          # goose Seatbelt
    "settings.hooks.example.json", "constitutional-gate.py",  # claude sandbox block + gate
    "confine-run.sh", "mac-escape-tests.sh",                  # neutral launcher + mac deny-test
})

_BRIEF_SUBPATH = ".agentteams/brief.json"


def _read_brief_privilege(root: Path) -> dict[str, Any]:
    """Return the pin's brief privilege posture, or ``{}`` when non-confining/absent.

    Reads ``.agentteams/brief.json`` and returns only the privilege fields, with
    ``privilege_profile_explicit`` derived from whether the brief set the field (so the fail
    -closed gate flip's explicit-opt-in contract is preserved across projection — audit
    SEV-3#1). Returns ``{}`` when there is no brief, the profile is cooperative/absent, or the
    file is unreadable (projection then emits no boundary, exactly as before — fail safe).
    """
    path = root / _BRIEF_SUBPATH
    if not path.is_file():
        return {}
    try:
        brief = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(brief, dict):
        return {}
    # Normalize through the SAME default as generation (a missing key → confined as of
    # 2026-W39), so a team that merely ACCEPTS the default still gets its boundary projected —
    # otherwise sync would under-protect exactly the default the flip made universal
    # (adversarial closeout #1). privilege_profile_explicit stays honest (False when defaulted),
    # so the fail-closed hook flip is NOT triggered for a defaulted team (stays fail-open).
    from agentteams.host_features import validate_privilege_profile

    profile = validate_privilege_profile(brief.get("privilege_profile"))
    if profile == "cooperative":
        return {}  # explicit opt-out → no boundary to propagate
    priv: dict[str, Any] = {
        "privilege_profile": profile,
        "privilege_profile_explicit": "privilege_profile" in brief,
    }
    for key in ("workspace_write_roots", "protected_read_paths", "goose_egress_proxy",
                "resolve_deny_read_abspath"):
        if brief.get(key) is not None:
            priv[key] = brief[key]
    return priv


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
    """
    if not privilege or privilege.get("privilege_profile") not in {"confined", "exclusive"}:
        # 2026-09-30: a cooperative agentteams team (marker present) still needs its store
        # sentinel, or a confined sibling's launcher refuses the project. Write-if-absent.
        # Never follows a planted symlink: the store dir must resolve inside the team, and the file
        # is created O_EXCL|O_NOFOLLOW (nothing, not even a dangling link, may already be there).
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
    from agentteams.host_features import expand_privilege_profile

    profile = privilege["privilege_profile"]
    manifest: dict[str, Any] = {
        "project_name": "SyncedTeam",
        "privilege_profile": profile,
        "privilege_profile_explicit": bool(privilege.get("privilege_profile_explicit")),
        "host_features": expand_privilege_profile(profile, framework),
        # Transient, never persisted: the project root for the write-root hard bans (#2).
        PROJECT_ROOT_KEY: Path(root).resolve(),
    }
    for key in ("workspace_write_roots", "protected_read_paths", "goose_egress_proxy",
                "resolve_deny_read_abspath"):
        if privilege.get(key) is not None:
            manifest[key] = privilege[key]

    agents_dir = framework_agents_dir(root, framework)
    adapter = FRAMEWORKS[framework]()
    written: list[str] = []
    for rel_path, content in adapter.extra_output_files(manifest):
        base = rel_path.rsplit("/", 1)[-1]
        # The verify-key store sentinel is matched by its full path (a bare ``README.md`` basename
        # is far too generic): the deny it backs is projected, so it must be too (bwrap cannot
        # start on a missing deny path).
        if base not in _PRIVILEGE_ARTIFACT_BASENAMES and rel_path != VERIFY_KEY_STORE_SENTINEL_REL:
            continue  # skip .goosehints / capability refs / etc. — privilege set only
        target = (agents_dir / rel_path).resolve()
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
) -> tuple[dict[str, Any], list[ConflictRecord], int]:
    """Fold one framework's classified changes into canonical.

    Args:
        canonical_cai: The current canonical CAI dict (mutated copy returned).
        report: The ``SyncReport`` from :func:`classify_sync`.
        framework: The framework being reconciled.
        is_pin: Whether ``framework`` is the bootstrap pin (authoritative).

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
                # Roster divergence (agent added/removed on one side). v1 logs
                # it for review rather than auto-adding/removing agents — a
                # roster change is higher-blast-radius than a field edit.
                conflicts.append(ConflictRecord(
                    framework, slug, fname, cls.value,
                    resolution="logged (roster change — review; not auto-applied in v1)",
                    note=fr.notice,
                ))
                continue

            if target is None:
                continue

            if is_pin:
                # Pin is authoritative: absorb any field the pin moved,
                # including conflicts and capability fields.
                if cls in (Classification.NATIVE_MOVED, Classification.BOTH_MOVED_CONFLICT):
                    target[fname] = fr.native_value
                    applied += 1
                    if cls == Classification.BOTH_MOVED_CONFLICT:
                        conflicts.append(ConflictRecord(
                            framework, slug, fname, cls.value,
                            resolution="pin-wins (pin is bootstrap authority)",
                            note=fr.notice,
                        ))
                continue

            # Peer framework.
            if fr.action == Action.APPLY:
                # Clean, non-capability native-moved — safe to absorb + fan out.
                target[fname] = fr.native_value
                applied += 1
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
    if privilege.get("privilege_profile") not in {"confined", "exclusive"}:
        return
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
) -> list[str]:
    """Project canonical to every framework and rewrite each baseline.

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
    projected: list[str] = []
    for fw in frameworks:
        agents_dir = framework_agents_dir(root, fw)
        if not dry_run:
            _backup_before_projection(agents_dir, fw)
        # preserve_existing (item 3, 2026-09-29): unchanged agents keep their exact bytes
        # (bespoke front matter survives) and an existing instruction file is fence-merged,
        # never replaced by another framework's instructions.
        res = import_from_cai(canonical_cai, fw, agents_dir, overwrite=True, dry_run=dry_run,
                              preserve_existing=True)
        if notices is not None:
            notices.extend(f"[{fw}] {n}" for n in res.notices)
        # C2: re-emit this framework's OS-boundary artifacts from the team-level privilege
        # block so a confined/exclusive pinned-sync team stays confined after projection.
        _emit_privilege_artifacts(root, fw, privilege, dry_run=dry_run)
        if not dry_run:
            _mark_projected_team(agents_dir, fw, canonical_dir, canonical_cai, res.converted, notices)
        projected.append(fw)
        if not dry_run:
            native_cai = export_to_cai(agents_dir, fw)
            write_baseline(canonical_dir, fw, native_cai, native_source_dir=str(agents_dir))
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
    if outcome.error and notices is not None:
        notices.append(f"[{framework}] {outcome.error}; NO team marker was written")


def _backup_before_projection(agents_dir: Path, framework: str) -> None:
    """Snapshot a framework's agents dir and its instruction file before projection overwrites.

    Projection writes with ``overwrite=True`` into directories that are commonly gitignored, so
    without this a bad projection is unrecoverable (incident 2026-09-29). Uses the standard
    ``.agentteams-backups/<timestamp>/`` store, so ``--restore-backup`` applies.
    """
    import os

    from agentteams.backup import BACKUP_DIR_NAME, backup_output_dir, prune_backups

    if not agents_dir.is_dir():
        return
    adapter = FRAMEWORKS[framework]()
    files = [p for p in agents_dir.rglob("*")
             if p.is_file() and BACKUP_DIR_NAME not in p.relative_to(agents_dir).parts]
    skills = adapter.skills_dir(agents_dir)
    if adapter.has_skill_concept() and skills.is_dir():
        files += [p for p in skills.rglob("*") if p.is_file()]  # projection rewrites skills too
    rels = [os.path.relpath(p, agents_dir) for p in files]
    rels.append(adapter.finalize_output_path("../copilot-instructions.md", "instructions"))
    backup_output_dir(agents_dir, files_to_backup=rels, reason="pre-sync-projection", framework=framework)
    prune_backups(agents_dir)  # keep the default most-recent N; every --sync would add one


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
            or if two frameworks in ``frameworks`` resolve to the same
            physical agents directory (see :func:`_reject_directory_collisions`).
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
    # C2: capture the pin's brief privilege posture into the team-level canonical block so it
    # persists in team.cai.json and projection re-emits each framework's OS boundary.
    priv = _read_brief_privilege(root)
    if priv:
        _check_projected_write_roots(root, priv, fws, accepted_write_roots)  # before any write
        seed["privilege"] = priv
    materialize_canonical(seed, canonical_dir, dry_run=dry_run)

    notices: list[str] = []
    projected = _project_and_rebaseline(
        root, canonical_dir, seed, fws, dry_run=dry_run, notices=notices,
        accepted_write_roots=accepted_write_roots,
    )
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
            or if two frameworks in the pinned sync set resolve to the same
            physical agents directory (see :func:`_reject_directory_collisions`).
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

    # Process the pin first (authoritative), then peers.
    ordered = ([pin] if pin in changed else []) + [f for f in changed if f != pin]

    all_conflicts: list[ConflictRecord] = []
    applied_total = 0
    for fw in ordered:
        agents_dir = framework_agents_dir(root, fw)
        native_cai = export_to_cai(agents_dir, fw)
        canonical_cai = load_canonical(canonical_dir)
        baseline = load_baseline(canonical_dir, fw)
        report = classify_sync(
            canonical_cai, native_cai, baseline,
            canonical_dir=str(canonical_dir), native_dir=str(agents_dir), framework=fw,
        )
        canonical_cai, conflicts, applied = _reconcile(
            canonical_cai, report, framework=fw, is_pin=(fw == pin),
        )
        all_conflicts.extend(conflicts)
        applied_total += applied
        materialize_canonical(canonical_cai, canonical_dir, dry_run=dry_run)

    final_canonical = load_canonical(canonical_dir)
    # C2: refresh the team-level privilege posture from the (current) brief so a changed
    # privilege_profile propagates, and re-persist it so team.cai.json stays authoritative.
    priv = _read_brief_privilege(root)
    if priv:
        _check_projected_write_roots(root, priv, fws, accepted_write_roots)  # before persisting
        final_canonical["privilege"] = priv
        materialize_canonical(final_canonical, canonical_dir, dry_run=dry_run)
    notices: list[str] = []
    projected = _project_and_rebaseline(
        root, canonical_dir, final_canonical, fws, dry_run=dry_run, notices=notices,
        accepted_write_roots=accepted_write_roots,
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
