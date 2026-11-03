"""frozen_render.py — a render never writes a path that a frozen surface owns.

The brief's ``frozen_surfaces`` names provider surfaces whose files must not change (a Codex freeze, for one). The
governance classifier and the rules projection already honour it; this module makes the render honour it too:

* :func:`frozen_refusal` refuses a render, before it writes anything, when its framework is frozen or its output
  directory lies inside a directory a frozen surface owns (``copilot-cli`` and ``copilot-vscode`` share
  ``.github/agents/``), so the build log, backups and post-build files never land there either;
* :func:`drop_frozen` drops each rendered (or pruned) file whose project-relative path a frozen surface owns
  (:data:`agentteams.cli.frozen_surfaces.FROZEN_SURFACE_PATHS`), with a notice. That covers a shared file one
  surface renders while another surface's freeze owns it: a goose render of the repo-root ``AGENTS.md`` while Codex
  is frozen (baseAgent D63).

Paths are compared after resolving symlinks, as the emit step resolves them before writing. The working brief is
the source here, unlike the classifier's target axis, which needs the recorded freeze state: reading the brief can
only narrow what a render writes, and dropping a surface from the brief is itself an unfreeze the classifier reports.

Stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentteams.cli.frozen_surfaces import frozen_owned_paths, touches_frozen


def _project_root(manifest: dict, output_dir: Path) -> str:
    """The project root frozen paths are relative to: ``existing_project_path``, else the standard layout, else emit's
    write boundary (the output directory's grandparent, ``atomicio``), so the filter never silently switches off.
    (release/1.0 has no ``cli/post_build.project_root_of``; this is its logic.)"""
    from agentteams.prompt_roots import team_project_root

    found = team_project_root(output_dir)
    root = (Path(manifest["existing_project_path"]).resolve() if manifest.get("existing_project_path")
            else found[0] if found else output_dir.resolve().parent.parent)
    return os.path.realpath(root)


def _relative(target: str, root: str) -> str | None:
    """*target* relative to *root* in POSIX form, or ``None`` when it lies outside the project."""
    rel = os.path.relpath(os.path.realpath(target), root)
    return None if rel == ".." or rel.startswith(".." + os.sep) else Path(rel).as_posix()


def frozen_refusal(manifest: dict, framework_id: str, output_dir: Path) -> str | None:
    """The refusal for a render that would write into a frozen surface, or for an invalid ``frozen_surfaces``.

    Args:
        manifest: The team manifest (``frozen_surfaces`` from the brief).
        framework_id: The framework being rendered.
        output_dir: The agents output directory.

    Returns:
        The message to print before exiting 1, or ``None`` when the render may go ahead.

    Raises:
        Nothing.
    """
    frozen = manifest.get("frozen_surfaces") or []
    try:
        paths = frozen_owned_paths(frozen)
    except ValueError as exc:
        return f"Error: {exc}"
    if framework_id in frozen:
        owner = framework_id
    else:
        rel = _relative(str(output_dir), _project_root(manifest, output_dir))
        owner = next((s for s in frozen for p in frozen_owned_paths([s]) if rel is not None and p.endswith("/")
                      and (rel.lower() + "/").startswith(p.lower())), None) if paths else None
    if owner is None:
        return None
    return (f"Error: the '{owner}' surface is frozen (frozen_surfaces in the brief) and owns {output_dir}, so nothing "
            f"is rendered or written for '{framework_id}' there. Unfreezing is an operator decision.")


def drop_frozen(manifest: dict, rendered: list[tuple[str, str]], output_dir: Path, *,
                quiet: bool = False) -> list[tuple[str, str]]:
    """Drop the rendered files a frozen surface owns, printing one notice per file unless *quiet*.

    Args:
        manifest: The team manifest (``frozen_surfaces`` from the brief).
        rendered: ``(rel_path, content)`` pairs relative to *output_dir* (``../../AGENTS.md`` included).
        output_dir: The agents output directory.
        quiet: Drop without notices (a second pass over the same files, or a prune list).

    Returns:
        The rendered files outside every frozen surface. A path outside the project is kept: it is not a frozen
        surface's, and the emit step's own guards decide it.

    Raises:
        ValueError: ``frozen_surfaces`` names an unknown surface (:func:`frozen_refusal` reports it first).
    """
    frozen = manifest.get("frozen_surfaces") or []
    paths = frozen_owned_paths(frozen)
    if not paths:
        return rendered
    root = _project_root(manifest, output_dir)
    base = str(output_dir.resolve())
    kept: list[tuple[str, str]] = []
    for rel_path, content in rendered:
        rel = _relative(os.path.join(base, rel_path), root)
        if rel is not None and touches_frozen(rel, paths):
            if not quiet:
                print(f"  ↻  {rel}: skipped (a frozen surface owns it: {', '.join(frozen)})")
            continue
        kept.append((rel_path, content))
    return kept


def exempt_frozen(manifest: dict, output_dir: Path, dreport: object, sdreport: object | None) -> None:
    """Leave frozen-owned files out of ``--check``'s drift verdict, in place, with one notice per file.

    A frozen file is never written, so it can't be brought up to date; failing ``--check`` on it would fail every run.

    Args:
        manifest: The team manifest (``frozen_surfaces`` from the brief).
        output_dir: The agents output directory.
        dreport: The template drift report (``changed_templates`` entries carry ``output_file``).
        sdreport: The structural diff report (``drifted_files`` / ``added_files`` entries carry ``path``), or None.

    Returns:
        None.

    Raises:
        ValueError: ``frozen_surfaces`` names an unknown surface.
    """
    if not frozen_owned_paths(manifest.get("frozen_surfaces") or []):
        return
    lists = [(dreport, "changed_templates", "output_file")]
    lists += [(sdreport, name, "path") for name in ("drifted_files", "added_files")] if sdreport is not None else []
    for report, name, key in lists:
        entries = getattr(report, name)
        kept = {rel for rel, _ in drop_frozen(manifest, [(e[key], "") for e in entries], output_dir, quiet=True)}
        for e in entries:
            if e[key] not in kept:
                print(f"  ↻  {e[key]}: not checked (a frozen surface owns it)")
        setattr(report, name, [e for e in entries if e[key] in kept])


def prunable(manifest: dict, removed: list[dict], output_dir: Path) -> list[dict]:
    """The ``--prune`` candidates (build-log entries with a ``path``) outside every frozen surface.

    Args:
        manifest: The team manifest (``frozen_surfaces`` from the brief).
        removed: The structural diff's ``removed_files`` entries.
        output_dir: The agents output directory.

    Returns:
        The entries ``--prune`` may delete; a frozen file is never deleted (it is skipped with a notice).

    Raises:
        ValueError: ``frozen_surfaces`` names an unknown surface.
    """
    kept = {rel for rel, _ in drop_frozen(manifest, [(f["path"], "") for f in removed], output_dir)}
    return [f for f in removed if f["path"] in kept]


__all__ = ["drop_frozen", "exempt_frozen", "frozen_refusal", "prunable"]
