"""memory_index_surfaces.py — the memory index across a project's surfaces, and finding the brief (baseAgent P5–P7).

An instance runs on several providers, and each keeps its own copy of ``references/memory-index.json`` under its
agents directory. Each copy was refreshed only by its own provider's update, so baseAgent's three copies were
built from 77, 103 and 103 of 110 sources. This module:

* finds every surface of a project that holds an index (:func:`index_surfaces`), so ``--refresh-index`` rebuilds
  them together and a query can fall back to another surface's copy;
* finds the brief when a retrieval command names none, or names one that does not exist
  (:func:`resolve_brief`), so the recall skill's bare command and the agents' fenced
  ``--description .agentteams/brief.json`` both work;
* reports what the default index sources leave out (:func:`coverage_notices`).

Kept out of :mod:`agentteams.cli.artifacts`, which is over the CH-07 ceiling.

Stdlib only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: Each framework's agents directory (project-relative) and the framework an index there is stamped with.
SURFACE_DIRS: dict[str, str] = {
    ".github/agents": "copilot-vscode",
    ".claude/agents": "claude",
    ".goose/recipes": "goose",
    ".codex/agents": "codex",
    ".grok/agents": "grok",
    ".agents": "agents-md",
}
#: Where a brief is looked for, in order (the rich brief before the thin ``_build-description.json`` stub).
BRIEF_CANDIDATES: tuple[str, ...] = (".agentteams/brief.json", "brief.json", ".github/agents/_build-description.json")
_INDEX_REL = Path("references") / "memory-index.json"
#: Folders that commonly hold deliverables and operator docs outside the default index sources.
_DELIVERABLE_DIRS: tuple[str, ...] = ("Projects", "docs")


def resolve_brief(project_root: Path) -> Path | None:
    """The project's brief, from :data:`BRIEF_CANDIDATES`.

    Args:
        project_root: The project root.

    Returns:
        The first candidate that is a file, or ``None``.

    Raises:
        Nothing.
    """
    for rel in BRIEF_CANDIDATES:
        candidate = project_root / rel
        if candidate.is_file():
            return candidate
    return None


def index_surfaces(project_root: Path) -> list[Path]:
    """Every agents directory under *project_root* that holds a memory index, in :data:`SURFACE_DIRS` order.

    Args:
        project_root: The project root.

    Returns:
        The agents directories (absolute).

    Raises:
        Nothing.
    """
    return [project_root / rel for rel in SURFACE_DIRS if (project_root / rel / _INDEX_REL).is_file()]


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
    try:
        rel = agents_dir.resolve().relative_to(project_root.resolve()).as_posix()
    except (ValueError, OSError):
        return ""
    return SURFACE_DIRS.get(rel, "")


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
    try:
        root = project_root.resolve()
    except OSError:
        root = project_root
    out: list[Path] = []
    for raw in manifest.get("memory_index_extra_files") or []:
        if not isinstance(raw, str) or not raw.strip() or Path(raw).is_absolute() or any(ch in raw for ch in "*?["):
            continue
        candidate = project_root / raw
        if candidate.suffix == ".md" and candidate.is_file() and _inside(candidate, root):
            out.append(candidate)
    return out


def _inside(path: Path, root: Path) -> bool:
    """Whether *path*'s real path lies under *root* (a symlink may not lead out of the project)."""
    return Path(os.path.realpath(path)).is_relative_to(root)


__all__ = ["BRIEF_CANDIDATES", "SURFACE_DIRS", "coverage_notices", "extra_files", "index_surfaces",
           "resolve_brief", "surface_framework"]
