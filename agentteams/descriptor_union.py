"""descriptor_union.py — a re-render never silently loses the roster (IM item 12, G-17-1).

One team can have two descriptors. The brief (``brief.json`` or ``.agentteams/brief.json``) carries the content:
authority sources, style rules, components, output paths. The stub (``.github/agents/_build-description.json``) often
carries the roster (``selected_archetypes``). A description with no roster makes ``analyze`` re-run keyword selection,
which can drop agents, and the fleet takes the brief whole whenever one exists.

The fix runs where the description is loaded, so the fleet and a hand-run ``--update --description brief.json`` both
get it, with no temp file and the real descriptor path in every record:

* :func:`apply_stub_roster` copies the stub's ``selected_archetypes`` into a description that has none, and reports
  (never applies) the other roster keys the stub sets and the description lacks. Those keys change capability and
  governance (``write_policy``, ``governance_agents``, ``retrieval_integration``), and an operator may have deleted
  one from the brief on purpose; nothing rewrites the stub's roster, so restoring it would undo that silently.
* :func:`refuse_roster_shrink` runs before anything is written. Under ``--yes`` it refuses only when the roster cannot
  be known: the description has none and the stub exists but cannot be used (unparseable, or a roster with an
  unknown archetype). A description that sets its own roster
  is a deliberate choice, so archetypes it drops from the stub's are reported, not refused (no nightly FAIL from a
  stale stub). ``--allow-roster-shrink`` accepts the unknown-roster case.

researchteam's ``_update_cmd._resolve_descriptor`` makes a similar union, but lets the stub's roster override a brief
roster that differs; here the description wins on every key it sets.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

#: The roster keys a stub can carry. Only ``selected_archetypes`` is applied; the rest are reported.
ROSTER_FIELDS: tuple[str, ...] = ("selected_archetypes", "governance_agents", "write_policy", "retrieval_integration")

#: Archetypes ``analyze.build_manifest`` force-appends from a capability (retrieval, research verification, the
#: updater, vulnscan). Turning the capability off removes them, which is not a roster loss.
FORCE_APPENDED: frozenset[str] = frozenset({"retrieval-integrator", "research-analyst", "agentteams-updater",
                                            "vuln-remediation"})

STUB_NAME = "_build-description.json"
STUB_REL = Path(".github") / "agents" / STUB_NAME

#: ``args`` attribute recording what :func:`apply_stub_roster` found, for :func:`refuse_roster_shrink`.
_STATE_ATTR = "_stub_roster_state"


def stub_for(project: str | None, output: str | None, description: str | None = None) -> Path | None:
    """Return the project's stub, or None. A symlink or a non-regular file never counts (@security).

    With ``--project``, only that project's stub counts (@adversarial: a fallback could reach an unrelated repo's, such
    as an example brief's). Otherwise, from ``--output`` (often the agents dir itself), the places
    ``build_team._check_dual_descriptor`` probes; then the stub beside the description's project.

    Args:
        project: The ``--project`` argument.
        output: The ``--output`` argument.
        description: The ``--description`` path, which is never itself returned.

    Returns:
        The first candidate that is a regular, non-symlinked file other than the description, or None.

    Raises:
        Nothing.
    """
    desc = Path(description).resolve() if description else None
    candidates: list[Path] = []
    if project:
        candidates.append(Path(project).resolve() / STUB_REL)
    else:
        if output:
            root = Path(output).resolve()
            candidates += [root / STUB_NAME, root / STUB_REL]
            if root.name == "agents":
                candidates.append(root.parent / STUB_NAME)
        if desc is not None:
            candidates.append(desc.parent / STUB_REL)
    for cand in candidates:
        if desc is not None and cand.resolve() == desc:
            continue
        if cand.is_symlink() or not cand.is_file():
            continue
        return cand
    return None


_DOMAIN_TEMPLATES = Path(__file__).resolve().parent / "templates" / "domain"


def _known_archetype(slug: object) -> bool:
    """Return whether ``slug`` is a string naming an archetype with a domain template."""
    return isinstance(slug, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]*", slug)) and (
        _DOMAIN_TEMPLATES / f"{slug}.template.md").is_file()


def _read(path: Path) -> dict[str, Any] | None:
    """Return the JSON object at ``path``, or None when it cannot be read or is not an object."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def apply_stub_roster(description: dict[str, Any], args: argparse.Namespace) -> None:
    """Copy the stub's ``selected_archetypes`` into a description that has none; report other missing roster keys.

    Only ``--update`` and ``--check`` runs take the stub's roster (@adversarial): a fresh render into a repo with a
    leftover stub keeps keyword selection. A roster with an item that is not a known archetype slug (a string with a
    ``templates/domain/<slug>.template.md``) is treated as unreadable (@security).

    Records what it found on ``args`` for :func:`refuse_roster_shrink`: ``"own"`` (the description sets a roster),
    ``"injected"``, ``"none"`` (no stub, a stub with no roster, or not an update) or ``"unreadable"`` (a stub that
    cannot be parsed, or a roster with an unknown or non-string item).

    Args:
        description: The loaded description; modified in place when the roster is copied.
        args: The parsed CLI arguments (``update``, ``check``, ``project``, ``output``, ``description``).

    Returns:
        None.

    Raises:
        Nothing.
    """
    if not (getattr(args, "update", False) or getattr(args, "check", False)):
        setattr(args, _STATE_ATTR, ("none", None))
        return
    path = stub_for(getattr(args, "project", None), getattr(args, "output", None), getattr(args, "description", None))
    stub = _read(path) if path else None
    has_own = "selected_archetypes" in description
    state = "own" if has_own else ("none" if path is None else ("unreadable" if stub is None else "none"))
    if stub is not None:
        roster = stub.get("selected_archetypes")
        if not has_own and isinstance(roster, list) and roster and not all(map(_known_archetype, roster)):
            state = "unreadable"
            print(f"[WARN] {path} lists selected_archetypes that are not known archetypes; not applied.",
                  file=sys.stderr)
        elif not has_own and isinstance(roster, list) and roster:
            description["selected_archetypes"] = list(roster)
            state = "injected"
            print(f"  Roster: {len(roster)} selected_archetypes from {path} (the description has none)")
        unapplied = [k for k in ROSTER_FIELDS[1:] if k not in description and stub.get(k) not in (None, [], {})]
        if unapplied:
            print(f"[WARN] {path} sets {', '.join(unapplied)}, which the description lacks; not applied. Add them to "
                  f"the description if they are still wanted.", file=sys.stderr)
    setattr(args, _STATE_ATTR, (state, path))


def dropped_archetypes(manifest: dict[str, Any], stub_path: Path | None) -> list[str]:
    """Return the stub's ``selected_archetypes`` that ``manifest`` no longer selects, less :data:`FORCE_APPENDED`.

    Args:
        manifest: The manifest this run is about to render.
        stub_path: The stub (from :func:`stub_for`), or None.

    Returns:
        The dropped archetype slugs, sorted; empty when there is no readable stub roster.

    Raises:
        Nothing.
    """
    stub = _read(stub_path) if stub_path else None
    recorded = stub.get("selected_archetypes") if stub else None
    if not isinstance(recorded, list):
        return []
    return sorted(set(map(str, recorded)) - set(manifest.get("selected_archetypes") or []) - FORCE_APPENDED)


def refuse_roster_shrink(args: argparse.Namespace, manifest: dict[str, Any], project_root: Path) -> bool:
    """Return True (and print why) when an ``--update --yes`` cannot know the roster it would apply.

    Args:
        args: The parsed CLI arguments, after :func:`apply_stub_roster` ran.
        manifest: The manifest this run is about to render.
        project_root: The run's write root (unused; kept so the call site reads like ``refuse_if_frozen``).

    Returns:
        Whether the run must stop before writing.

    Raises:
        Nothing.
    """
    if not getattr(args, "update", False):
        return False
    state, path = getattr(args, _STATE_ATTR, ("none", None))
    if state == "own":
        if dropped := dropped_archetypes(manifest, path):
            print(f"[WARN] the description's selected_archetypes leaves out {', '.join(dropped)}, which {path} "
                  f"records. Applied as written (the description is authoritative).", file=sys.stderr)
        return False
    if state != "unreadable" or getattr(args, "allow_roster_shrink", False):
        return False
    what = (f"the description has no selected_archetypes and {path} cannot be used, so this update would re-run "
            f"keyword selection and may drop agents")
    if not getattr(args, "yes", False):
        print(f"[WARN] {what}.", file=sys.stderr)
        return False
    print(f"Refused: {what}. Nothing was written. Fix the stub, put the roster in the description, or pass "
          f"--allow-roster-shrink.", file=sys.stderr)
    return True
