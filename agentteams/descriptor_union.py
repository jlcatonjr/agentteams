"""descriptor_union.py — a re-render never silently loses the roster (IM item 12, G-17-1).

Two descriptors can describe one team. The brief (``brief.json`` or ``.agentteams/brief.json``) carries the content:
authority sources, style rules, components, output paths. The stub (``.github/agents/_build-description.json``) often
carries the roster: ``selected_archetypes`` and the keys below. A brief without a roster makes ``analyze`` re-run
keyword selection, which can drop agents, and the fleet used to pick the brief whole whenever it existed.

Two guards, each kept here so the crowded modules that call them grow by one line at most (CH-07):

* :func:`for_fleet` gives the fleet a union: the brief's content, plus each roster key the brief lacks, from the stub.
  The brief wins where both set a key. researchteam's ``_update_cmd._resolve_descriptor`` does the same union, except
  that its stub roster overrides a differing brief roster; here a thinner brief roster is caught by the second guard.
* :func:`refuse_roster_shrink` stops an ``--update --yes`` that would drop archetypes the stub records, unless
  ``--allow-roster-shrink`` is given. Without ``--yes`` it warns.
"""

from __future__ import annotations

import argparse
import atexit
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

#: The roster keys a stub can supply. ``selected_archetypes`` decides which agents exist; ``governance_agents``,
#: ``write_policy`` and ``retrieval_integration`` change which governance agents and capabilities render.
ROSTER_FIELDS: tuple[str, ...] = ("selected_archetypes", "governance_agents", "write_policy", "retrieval_integration")

STUB_REL = Path(".github") / "agents" / "_build-description.json"

_TEMP_DIRS: list[Path] = []


def _cleanup() -> None:
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TEMP_DIRS.clear()


atexit.register(_cleanup)


def _read(path: Path) -> dict[str, Any] | None:
    """Return the JSON object at ``path``, or None when it is missing, unreadable or not an object."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def union(brief: dict[str, Any], stub: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return ``brief`` with each :data:`ROSTER_FIELDS` key it lacks copied from ``stub``, and the keys copied.

    Args:
        brief: The brief's descriptor (content of record; it wins on every key it sets).
        stub: The stub's descriptor (roster of record).

    Returns:
        ``(merged, injected)``. ``merged`` is a new dict; ``brief`` is not modified.

    Raises:
        Nothing.
    """
    merged = dict(brief)
    injected = [k for k in ROSTER_FIELDS if k not in brief and stub.get(k) not in (None, [], {})]
    for key in injected:
        merged[key] = stub[key]
    return merged, injected


def for_fleet(ws: Path, descriptor: Path | None) -> Path | None:
    """Return the descriptor the fleet should render ``ws`` with: a brief/stub union where the stub adds a roster.

    The union is written as ``brief.json`` in a private temp directory, removed at exit, so nothing is written into the
    workspace (dry run included) and the fleet's ``descriptor.name == "brief.json"`` check still applies. ``ingest``
    resolves no path relative to the descriptor's own folder, so its location does not change the render.

    Args:
        ws: The workspace root.
        descriptor: What ``fleet._resolve_descriptor`` chose, or None.

    Returns:
        The union file, or ``descriptor`` unchanged when it is not a brief, there is no readable stub, or the stub
        adds nothing.

    Raises:
        Nothing.
    """
    if descriptor is None or descriptor.name != "brief.json":
        return descriptor
    brief, stub = _read(descriptor), _read(ws / STUB_REL)
    if brief is None or stub is None:
        return descriptor
    merged, injected = union(brief, stub)
    if not injected:
        return descriptor
    tmp = Path(tempfile.mkdtemp(prefix="agentteams-fleet-descriptor-"))
    _TEMP_DIRS.append(tmp)
    out = tmp / "brief.json"
    out.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    print(f"    descriptor: {descriptor.relative_to(ws)} + {', '.join(injected)} from {STUB_REL.as_posix()}")
    return out


def stub_for(project_root: Path, project: str | None = None) -> Path | None:
    """Return the project's stub, or None. ``project_root`` is the ``--output`` folder, often the agents dir itself.

    The candidates match ``build_team._check_dual_descriptor``: ``--project``'s stub first, then the stub under
    ``project_root``, then ``project_root`` as the agents dir.

    Args:
        project_root: The resolved write root (``resolve_output_dir``'s first value).
        project: The ``--project`` argument, when given.

    Returns:
        The first existing stub path, or None.

    Raises:
        Nothing.
    """
    candidates = [Path(project).resolve() / STUB_REL] if project else []
    candidates += [project_root / STUB_REL, project_root / STUB_REL.name]
    return next((c for c in candidates if c.is_file()), None)


def dropped_archetypes(manifest: dict[str, Any], project_root: Path, project: str | None = None) -> list[str]:
    """Return the stub's ``selected_archetypes`` that ``manifest`` no longer selects (sorted; empty if no stub).

    Args:
        manifest: The manifest this run is about to render.
        project_root: The run's write root (see :func:`stub_for`).
        project: The ``--project`` argument, when given.

    Returns:
        The dropped archetype slugs.

    Raises:
        Nothing.
    """
    path = stub_for(project_root, project)
    stub = _read(path) if path else None
    recorded = stub.get("selected_archetypes") if stub else None
    if not isinstance(recorded, list):
        return []
    return sorted(set(map(str, recorded)) - set(manifest.get("selected_archetypes") or []))


def refuse_roster_shrink(args: argparse.Namespace, manifest: dict[str, Any], project_root: Path) -> bool:
    """Return True (and print why) when an ``--update --yes`` would drop archetypes the stub records.

    Without ``--yes`` the shrink is reported and the run continues; ``--allow-roster-shrink`` accepts it.

    Args:
        args: The parsed CLI arguments (``update``, ``yes``, ``allow_roster_shrink``).
        manifest: The manifest this run is about to render.
        project_root: The project the run writes to.

    Returns:
        Whether the run must stop before writing.

    Raises:
        Nothing.
    """
    if not getattr(args, "update", False) or getattr(args, "allow_roster_shrink", False):
        return False
    dropped = dropped_archetypes(manifest, project_root, getattr(args, "project", None))
    if not dropped:
        return False
    what = (f"this update would drop {len(dropped)} archetype(s) that {STUB_REL.as_posix()} records: "
            f"{', '.join(dropped)}")
    if not getattr(args, "yes", False):
        print(f"[WARN] {what}. Check that the description carries the roster (selected_archetypes).",
              file=sys.stderr)
        return False
    print(f"Refused: {what}. Nothing was written. Put the roster in the description, or pass "
          f"--allow-roster-shrink if the shrink is intended.", file=sys.stderr)
    return True
