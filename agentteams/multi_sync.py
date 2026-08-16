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
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentteams.canonical import load_canonical, materialize_canonical
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
    (codex/agents-md: proven precedent, always identical). It stops being
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
# git change detection
# ---------------------------------------------------------------------------

def _git(root: Path, *args: str) -> tuple[int, str]:
    """Run a git command under ``root``; return (exit_code, stdout)."""
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True,
    )
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
            if not prefix:  # root-level agents dir (agents-md/codex): match top-level files
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

def _project_and_rebaseline(
    root: Path,
    canonical_dir: Path,
    canonical_cai: dict[str, Any],
    frameworks: list[str],
    *,
    dry_run: bool,
) -> list[str]:
    """Project canonical to every framework and rewrite each baseline."""
    projected: list[str] = []
    for fw in frameworks:
        agents_dir = framework_agents_dir(root, fw)
        import_from_cai(canonical_cai, fw, agents_dir, overwrite=True, dry_run=dry_run)
        projected.append(fw)
        if not dry_run:
            native_cai = export_to_cai(agents_dir, fw)
            write_baseline(canonical_dir, fw, native_cai, native_source_dir=str(agents_dir))
    return projected


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
) -> SyncResult:
    """Bootstrap the pinned sync: seed canonical from the pin, project to all.

    Args:
        root: The project root directory.
        pin: The bootstrap-pin framework (seeds canonical, wins conflicts).
        frameworks: The sync set (defaults to every registered framework).
        canonical_rel: Canonical hub path relative to ``root``.
        dry_run: When set, computes without writing.

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
    materialize_canonical(seed, canonical_dir, dry_run=dry_run)

    projected = _project_and_rebaseline(
        root, canonical_dir, seed, fws, dry_run=dry_run,
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
    )


def run_sync(
    root: Path,
    *,
    since: str | None = None,
    dry_run: bool = False,
) -> SyncResult:
    """Run one sync pass: detect changes, reconcile to the pin, project all.

    Args:
        root: The project root directory.
        since: Git ref to diff from (defaults to the pin's ``last_synced_commit``).
        dry_run: When set, computes without writing.

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
    projected = _project_and_rebaseline(
        root, canonical_dir, final_canonical, fws, dry_run=dry_run,
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
    )
