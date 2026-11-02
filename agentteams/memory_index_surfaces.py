"""memory_index_surfaces.py — the memory index across a project's surfaces, and finding the brief (baseAgent P5–P7).

An instance runs on several providers, and each keeps its own copy of ``references/memory-index.json`` under its
agents directory. Each copy was refreshed only by its own provider's update, so baseAgent's three copies were
built from 77, 103 and 103 of 110 sources. This module:

* finds every surface of a project that holds an index (:func:`index_surfaces`), so ``--refresh-index`` rebuilds
  them together and a query can fall back to another surface's copy;
* finds the brief when a retrieval command names none (:func:`resolve_brief`), or, for a read-only query, names
  one that does not exist, so the recall skill's bare command and the agents' fenced
  ``--description .agentteams/brief.json`` both work;
* reports what the default index sources leave out (:func:`coverage_notices`).

Kept out of :mod:`agentteams.cli.artifacts`, which is over the CH-07 ceiling.

Stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_INDEX_REL = Path("references") / "memory-index.json"
#: Folders that commonly hold deliverables and operator docs outside the default index sources.
_DELIVERABLE_DIRS: tuple[str, ...] = ("Projects", "docs")


def resolve_brief(project_root: Path) -> Path | None:
    """The project's own brief, found as fleet discovery finds it (:func:`agentteams.fleet._resolve_descriptor`).

    A brief that is a symlink, resolves outside *project_root*, or names another project in
    ``existing_project_path`` is not this project's, and is not used (adversarial review, 2026-11-02).

    Args:
        project_root: The project root.

    Returns:
        The brief, or ``None``.

    Raises:
        Nothing.
    """
    from agentteams.fleet import _resolve_descriptor  # the canonical lookup (redteam F-3)

    root = project_root.resolve()
    path = _resolve_descriptor(project_root)
    if path is None or path.is_symlink() or not path.resolve().is_relative_to(root):
        return None
    try:
        brief = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    other = brief.get("existing_project_path") if isinstance(brief, dict) else None
    if isinstance(other, str) and other and (project_root / other).resolve() != root:
        return None
    return path


def surface_dirs(project_root: Path) -> dict[Path, str]:
    """Each registered framework's agents directory under *project_root*, mapped to its framework.

    From :func:`agentteams.frameworks.registry.agents_dir_for`, so there is no second framework→directory table
    (CH-05). Frameworks that share a directory (copilot-vscode and copilot-cli) keep the first.

    Args:
        project_root: The project root.

    Returns:
        ``{agents_dir: framework}`` in registry order.

    Raises:
        Nothing.
    """
    from agentteams.frameworks.registry import FRAMEWORK_IDS, agents_dir_for

    out: dict[Path, str] = {}
    for framework in FRAMEWORK_IDS:
        out.setdefault(agents_dir_for(framework, project_root), framework)
    return out


def index_surfaces(project_root: Path, frozen: object = None) -> list[Path]:
    """Every agents directory of the project holding a memory index, inside the project and not frozen.

    Args:
        project_root: The project root.
        frozen: The brief's ``frozen_surfaces``. A surface whose directory a frozen surface owns is left out:
            a cross-surface rebuild must not write it.

    Returns:
        The agents directories, in registry order.

    Raises:
        ValueError: *frozen* is not a list of known surface ids.
    """
    from agentteams.cli.frozen_surfaces import frozen_owned_paths, touches_frozen
    from agentteams.confinement import _inside

    frozen_paths = frozen_owned_paths(frozen)
    out: list[Path] = []
    for agents_dir in surface_dirs(project_root):
        rel = agents_dir.relative_to(project_root).as_posix()
        if ((agents_dir / _INDEX_REL).is_file() and _inside(agents_dir, project_root)
                and not touches_frozen(rel + "/", frozen_paths)):
            out.append(agents_dir)
    return out


def surface_framework(agents_dir: Path, project_root: Path) -> str:
    """The framework an index in *agents_dir* is stamped with: the index's own value, else the directory's.

    Args:
        agents_dir: A surface's agents directory.
        project_root: The project root.

    Returns:
        The framework id, or ``""`` when neither is known.

    Raises:
        Nothing.
    """
    try:
        head = json.loads((agents_dir / _INDEX_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        head = None  # unreadable: fall back to the directory's framework
    framework = head.get("framework") if isinstance(head, dict) else None
    if isinstance(framework, str) and framework:
        return framework
    return surface_dirs(project_root).get(agents_dir, "")


def _md_files(folder: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
        out.extend(Path(dirpath, n) for n in filenames if n.endswith(".md"))
    return out


def coverage_notices(manifest: dict, project_root: Path, sources: list[Path]) -> list[str]:
    """What the index sources leave out or may sweep in, for the build-time report (baseAgent P7).

    Args:
        manifest: The team manifest (``memory_index_extra_dirs`` / ``memory_index_extra_files``).
        project_root: The project root.
        sources: The sources the index is being built from.

    Returns:
        One notice per finding:
        - an ``memory_index_extra_dirs`` entry that is a file (it is skipped; list it under
          ``memory_index_extra_files``) or matches nothing;
        - an ``memory_index_extra_dirs`` directory or open glob, which indexes, and commits snippets of, every
          Markdown file added there later;
        - Markdown under ``Projects/`` or ``docs/`` that the index leaves out.

    Raises:
        Nothing.
    """
    notices: list[str] = []
    for raw in manifest.get("memory_index_extra_dirs") or []:
        if not isinstance(raw, str) or not raw.strip() or Path(raw).is_absolute():
            continue
        is_glob = any(ch in raw for ch in "*?[")
        target = project_root / raw
        if not is_glob and target.is_file():
            notices.append(f"memory_index_extra_dirs entry '{raw}' is a file, so it is skipped; list it under "
                           "memory_index_extra_files instead")
        elif not is_glob and not target.is_dir():
            notices.append(f"memory_index_extra_dirs entry '{raw}' matches nothing")
        else:
            notices.append(f"memory_index_extra_dirs entry '{raw}' indexes every Markdown file added there later, "
                           "and the committed index carries snippets of each; if a later note may hold restricted "
                           "content, list checked documents under memory_index_extra_files instead")
    indexed = {p.resolve() for p in sources}
    for name in _DELIVERABLE_DIRS:
        folder = project_root / name
        if not folder.is_dir():
            continue
        left_out = [p for p in _md_files(folder) if p.resolve() not in indexed]
        if left_out:
            notices.append(f"{len(left_out)} Markdown file(s) under {name}/ are not in the memory index; add checked "
                           "documents with memory_index_extra_files (or a folder with memory_index_extra_dirs), and "
                           "keep results and data folders out with memory_index_exclude")
    return notices


def extra_files(manifest: dict, project_root: Path) -> list[Path]:
    """The ``memory_index_extra_files`` sources: named Markdown files inside the project.

    Each entry is a project-relative path to one ``.md`` file. Globs, absolute paths, directories, missing files and
    any file whose real path leaves the project are skipped.

    Args:
        manifest: The team manifest.
        project_root: The project root.

    Returns:
        The files, in the order listed.

    Raises:
        Nothing.
    """
    from agentteams.confinement import _inside

    out: list[Path] = []
    for raw in manifest.get("memory_index_extra_files") or []:
        if not isinstance(raw, str) or not raw.strip() or Path(raw).is_absolute() or any(ch in raw for ch in "*?["):
            continue
        candidate = project_root / raw
        if candidate.suffix == ".md" and candidate.is_file() and _inside(candidate, project_root):
            out.append(candidate)
    return out


__all__ = ["coverage_notices", "extra_files", "index_surfaces", "resolve_brief", "surface_dirs", "surface_framework"]
