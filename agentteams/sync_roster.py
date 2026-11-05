"""sync_roster.py — pinned-sync roster changes: the pin adds and removes agents.

Operator ruling 2026-11-04 ("pin decides; peers propose"), applied to the roster. Before this, every roster
divergence was only logged: an agent the pin deleted came back on the next sync (the hub still held it, so
projection rewrote it), and an agent the pin added never reached the hub or the peers.

- **The pin adds an agent** the hub lacks: it joins the hub and projects to every framework.
- **The pin removes an agent** it had at its last sync (its baseline lists it): it leaves the hub, and each
  peer's copy is deleted after the pre-projection backup. Only a confirmed deletion counts: the file is gone
  from ``HEAD`` of a tracked agents directory, or no framework has the agent any more. Otherwise (uncommitted,
  or git can't tell) it is logged and projection restores the pin's file. A pin with no agents is refused.
  ``--sync-init`` over an existing hub removes the hub agents the new pin lacks, but no framework's copy.
- **A peer's own roster change** is never absorbed. A peer-only agent stays where it is and is logged as a
  proposal; an agent a peer deleted is restored by projection.

The hub's agent list is rebuilt from ``agents/*.md`` (``canonical.load_canonical``), so a removal must delete
that file too, not only drop the agent from the dict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentteams.git_exec import run_git

__all__ = ["RosterRecord", "apply_pin_roster", "prune_hub_agents", "refuse_empty_pin", "remove_dropped_peer_agents"]

#: (agent slug, resolution, note): the caller turns each into a sync-conflict log row.
RosterRecord = tuple[str, str, str]


def _slugs(cai: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {str(a["slug"]): a for a in ((cai or {}).get("agents") or []) if isinstance(a, dict) and a.get("slug")}


def _deletion_is_committed(root: Path, agents_dir: Path, source_path: str) -> bool:
    """Return whether the pin's deleted agent file is also gone from ``HEAD`` of a tracked agents directory.

    Fails closed (False) whenever git can't confirm it: no repository, no ``HEAD``, a git error, an untracked or
    gitignored agents directory, or a path outside the project. ``cat-file -e`` exits 128 for an absent path
    and for an error alike, so ``ls-tree`` of the directory is read instead.
    """
    project = Path(root).resolve()
    try:
        dir_rel = Path(agents_dir).resolve().relative_to(project).as_posix()
        file_rel = (Path(agents_dir).resolve() / source_path).resolve().relative_to(project).as_posix()
    except ValueError:
        return False
    if not source_path or not file_rel.startswith(f"{dir_rel}/"):
        return False
    proc = run_git(project, "ls-tree", "-r", "--name-only", "HEAD", "--", f"./{dir_rel}")
    tracked = set(proc.stdout.splitlines()) if proc.returncode == 0 else set()
    return bool(tracked) and file_rel not in tracked


def refuse_empty_pin(canonical_cai: dict[str, Any], pin_cai: dict[str, Any], pin_agents_dir: Path) -> None:
    """Refuse a sync or re-init from a pin with no agents while the hub has some.

    Raises:
        ValueError: The pin exports no agents (an empty, wrong or unreadable directory) and the hub has agents.
    """
    hub = _slugs(canonical_cai)
    if hub and not _slugs(pin_cai):
        raise ValueError(f"sync refused: the pin's agents directory {pin_agents_dir} has no agents, and the hub "
                         f"has {len(hub)}. Restore it from git or its .agentteams-backups, then sync again.")


def apply_pin_roster(
    canonical_cai: dict[str, Any],
    pin_cai: dict[str, Any],
    pin_baseline: dict[str, Any] | None,
    *,
    root: Path,
    pin_agents_dir: Path,
    peer_slugs: set[str],
) -> tuple[dict[str, Any], list[RosterRecord], list[str]]:
    """Carry the pin's added and removed agents into the hub's CAI dict (the caller writes it).

    A removal is an agent in the pin's baseline that the pin no longer has, with the deletion confirmed (see the
    module docstring). It is worked out from the pin's baseline, not from the hub, so a pass that fails after
    the hub changed still removes the peers' copies on the next pass (the baseline is rewritten only after a
    projection).

    Args:
        canonical_cai: The hub's CAI dict, updated in place and returned.
        pin_cai: The pin's export (``interop.export_to_cai``).
        pin_baseline: The pin's baseline from its last sync, or ``None`` (then nothing is removed).
        root: The project root (for the git check on a deletion).
        pin_agents_dir: The pin's agents directory.
        peer_slugs: Every agent slug some other framework still has.

    Returns:
        ``(canonical_cai, records, removed)``: a record per agent added, removed, or kept for review, and the
        slugs the pin removed (for :func:`remove_dropped_peer_agents`).

    Raises:
        ValueError: The pin has no agents and the hub has some (:func:`refuse_empty_pin`), before any change.
    """
    refuse_empty_pin(canonical_cai, pin_cai, pin_agents_dir)
    hub, pin, base = _slugs(canonical_cai), _slugs(pin_cai), _slugs(pin_baseline)
    records: list[RosterRecord] = []
    added = [slug for slug in pin if slug not in hub]
    removed: list[str] = []
    for slug in (s for s in base if s not in pin):
        if slug not in peer_slugs or _deletion_is_committed(root, pin_agents_dir,
                                                            str(base[slug].get("source_path") or "")):
            removed.append(slug)
        elif slug in hub:
            records.append((slug, "proposed (unconfirmed pin deletion; restored)",
                            "Commit the deletion (the pin's agents directory must be tracked by git), or delete "
                            "the agent from every framework, then sync again."))
    records += [(slug, "logged (not in the pin or its last sync; review)",
                 "The hub has an agent the pin never had. Projection writes it to every framework.")
                for slug in hub if slug not in pin and slug not in base]
    agents = [a for a in (canonical_cai.get("agents") or []) if not (isinstance(a, dict) and a.get("slug") in removed)]
    canonical_cai["agents"] = agents + [pin[slug] for slug in added]
    records += [(slug, "applied (pin added agent)", "Projected to every framework.") for slug in added]
    records += [(slug, "applied (pin removed agent)",
                 "Removed from the hub and every framework; each framework's .agentteams-backups keeps its copy "
                 "until pruned (newest 10), and git history keeps the hub's.") for slug in removed]
    return canonical_cai, records, removed


def prune_hub_agents(canonical_dir: Path, canonical_cai: dict[str, Any], *, dry_run: bool) -> list[str]:
    """Delete the hub's ``agents/<slug>.md`` files for agents no longer in ``canonical_cai``.

    Args:
        canonical_dir: The hub directory.
        canonical_cai: The hub's CAI dict as it will be written.
        dry_run: Report without deleting.

    Returns:
        The slugs whose hub file was (or would be) deleted.
    """
    keep = set(_slugs(canonical_cai))
    agents_dir = Path(canonical_dir) / "agents"
    if not agents_dir.is_dir():
        return []
    gone = [p for p in sorted(agents_dir.glob("*.md")) if p.is_file() and not p.is_symlink() and p.stem not in keep]
    if not dry_run:
        for path in gone:
            path.unlink()
    return [p.stem for p in gone]


def remove_dropped_peer_agents(
    agents_dir: Path, peer_cai: dict[str, Any], dropped: set[str] | list[str], *, dry_run: bool,
) -> list[str]:
    """Delete a framework's agent files for agents the pin removed (call after the pre-projection backup).

    Only the ``dropped`` slugs. A peer-only agent is never in ``dropped`` and is kept (it reaches the peer's
    baseline after one projection, so a baseline rule would not do).

    Args:
        agents_dir: The framework's agents directory.
        peer_cai: The framework's export.
        dropped: The slugs the pin removed (:func:`apply_pin_roster`).
        dry_run: Report without deleting.

    Returns:
        The slugs whose file was (or would be) deleted.
    """
    root = Path(agents_dir).resolve()
    gone: list[str] = []
    for slug, agent in _slugs(peer_cai).items():
        if slug not in dropped:
            continue
        path = Path(agents_dir) / str(agent.get("source_path") or "")
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            continue
        gone.append(slug)
        if not dry_run:
            path.unlink()
    return gone
