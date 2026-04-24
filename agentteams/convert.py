"""
convert.py — Convert an existing agent team from one framework format to another.

This module implements the format-migration path for AgentTeamsModule. Unlike
fresh generation (which re-renders every file from templates), conversion reads
existing agent files, preserves their prose body content, and re-wraps each file
with the target framework's front matter conventions.

Use this when a team has been manually edited post-generation and you want to
produce an equivalent team in a different framework format without losing those
edits.

Typical usage
-------------
    from pathlib import Path
    from agentteams.convert import convert_team

    result = convert_team(
        source_dir=Path("/repo/.github/agents"),
        target_dir=Path("/repo/.claude/agents"),
        target_framework="claude",
        project_manifest={"project_name": "MyProject"},
    )
    print(f"Converted {len(result.converted)} files, skipped {len(result.skipped)}")
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams.frameworks.base import FrameworkAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, type[FrameworkAdapter]] = {
    "copilot-vscode": CopilotVSCodeAdapter,
    "copilot-cli": CopilotCLIAdapter,
    "claude": ClaudeAdapter,
}

# File name patterns that are treated as framework instructions files
_INSTRUCTIONS_NAMES = {"copilot-instructions.md", "CLAUDE.md"}

# Special-case file name for the team-builder agent
_BUILDER_NAME_FRAGMENT = "team-builder"

# Subdirectory and file names that are always copied verbatim (no transform)
_PASSTHROUGH_NAMES = {"SETUP-REQUIRED.md"}
_PASSTHROUGH_DIRS = {"references"}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ConvertResult:
    """Summary of a convert_team() run.

    Attributes:
        converted: Relative target paths that were written (or would be written
                   in a dry run).
        skipped:   Source paths that were not converted (already up-to-date or
                   excluded).
        errors:    Descriptions of any files that could not be processed.
        dry_run:   Whether this result reflects a dry run (no files written).
    """

    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def success(self) -> bool:
        """True when no errors occurred."""
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def convert_team(
    source_dir: Path,
    target_dir: Path,
    target_framework: str,
    *,
    project_manifest: dict[str, Any] | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> ConvertResult:
    """Convert an existing agent team to a target framework format.

    Reads every agent file from *source_dir*, applies the target framework
    adapter's transformation (stripping incompatible metadata and injecting
    the correct front matter), and writes converted files to *target_dir*.

    The prose body of each source file is preserved verbatim. Only the YAML
    front matter and optional handoff sections are transformed.

    Non-agent files (``SETUP-REQUIRED.md``, the ``references/`` subdirectory)
    are copied unchanged.

    The instructions file (``copilot-instructions.md`` or ``CLAUDE.md``) is
    handled separately: it is placed at the *parent* of *target_dir* (i.e.,
    the project root) with the name required by the target framework.

    Args:
        source_dir: Directory containing the source agent files.  For a
            copilot-vscode team this is typically ``.github/agents/``.
        target_dir: Directory where converted agent files will be written.
            Will be created if it does not exist.
        target_framework: Target framework identifier, one of
            ``"copilot-vscode"``, ``"copilot-cli"``, or ``"claude"``.
        project_manifest: Optional manifest dict supplying context (e.g.
            ``project_name``) for front-matter generation.  Defaults to
            ``{}``.
        dry_run: When ``True``, compute what would be done and return a
            :class:`ConvertResult` without writing any files.
        overwrite: When ``False`` (default), skip target files that already
            exist.  When ``True``, overwrite them unconditionally.

    Returns:
        :class:`ConvertResult` describing what was converted, skipped, or
        errored.

    Raises:
        ValueError: If *target_framework* is not a known framework identifier.
        FileNotFoundError: If *source_dir* does not exist.
    """
    if target_framework not in _ADAPTERS:
        raise ValueError(
            f"Unknown target framework {target_framework!r}. "
            f"Choose from: {', '.join(sorted(_ADAPTERS))}."
        )
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    manifest: dict[str, Any] = project_manifest or {}
    adapter: FrameworkAdapter = _ADAPTERS[target_framework]()
    result = ConvertResult(dry_run=dry_run)

    # Walk source directory shallowly first, then handle subdirs explicitly
    for entry in sorted(source_dir.iterdir()):
        if entry.is_dir():
            _convert_subdir(entry, target_dir, adapter, manifest, dry_run, overwrite, result)
        else:
            _convert_file(entry, target_dir, adapter, manifest, dry_run, overwrite, result)

    # Also look for an instructions file in source_dir.parent (the typical location in
    # real repos: e.g. .github/copilot-instructions.md sits above .github/agents/).
    parent_dir = source_dir.parent
    for candidate in sorted(parent_dir.iterdir()):
        if candidate.is_file() and candidate.name in _INSTRUCTIONS_NAMES:
            _convert_file(candidate, target_dir, adapter, manifest, dry_run, overwrite, result)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_source_file(path: Path) -> str:
    """Return a file-type label for a source agent file.

    Returns:
        One of: ``"agent"``, ``"builder"``, ``"instructions"``,
        ``"passthrough"``, or ``"skip"``.
    """
    name = path.name
    if name in _INSTRUCTIONS_NAMES:
        return "instructions"
    if name in _PASSTHROUGH_NAMES:
        return "passthrough"
    if name.endswith(".agent.md") or name.endswith(".md"):
        stem = name.replace(".agent.md", "").replace(".md", "")
        if _BUILDER_NAME_FRAGMENT in stem:
            return "builder"
        return "agent"
    return "skip"


def _target_filename(source_path: Path, file_type: str, adapter: FrameworkAdapter) -> str:
    """Return the target filename for a converted source file.

    For agent/builder types the file extension is updated; for all others
    the name is preserved unchanged.

    Args:
        source_path: Source file path.
        file_type: Logical type label from :func:`_classify_source_file`.
        adapter: Target framework adapter.

    Returns:
        Target filename string (name only, no directory component).
    """
    if file_type in {"agent", "builder"}:
        ext = adapter.get_file_extension(file_type)
        name = source_path.name
        if name.endswith(".agent.md"):
            return name[: -len(".agent.md")] + ext
        if name.endswith(".md"):
            return name[: -len(".md")] + ext
    return source_path.name


def _convert_file(
    source_path: Path,
    target_dir: Path,
    adapter: FrameworkAdapter,
    manifest: dict[str, Any],
    dry_run: bool,
    overwrite: bool,
    result: ConvertResult,
) -> None:
    """Convert a single source file and record the outcome in *result*.

    Args:
        source_path: Absolute path to the source file.
        target_dir: Directory where the converted file should be written.
        adapter: Target framework adapter.
        manifest: Project manifest dict for placeholder context.
        dry_run: Skip all filesystem writes when True.
        overwrite: Overwrite existing target files when True.
        result: Mutable ConvertResult that tracks converted/skipped/errors.
    """
    file_type = _classify_source_file(source_path)

    if file_type == "skip":
        result.skipped.append(str(source_path))
        return

    if file_type == "passthrough":
        dest = target_dir / source_path.name
        if dest.exists() and not overwrite:
            result.skipped.append(str(source_path))
            return
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest)
        result.converted.append(str(dest))
        return

    if file_type == "instructions":
        # Place the instructions file at the project root (parent of agents dir)
        # using the name required by the target framework.
        root_dir = target_dir.parent
        instructions_name = _target_instructions_name(adapter)
        dest = root_dir / instructions_name
        if dest.exists() and not overwrite:
            result.skipped.append(str(source_path))
            return
        content = source_path.read_text(encoding="utf-8")
        if not dry_run:
            root_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        result.converted.append(str(dest))
        return

    # agent or builder
    target_name = _target_filename(source_path, file_type, adapter)
    dest = target_dir / target_name

    if dest.exists() and not overwrite:
        result.skipped.append(str(source_path))
        return

    try:
        content = source_path.read_text(encoding="utf-8")
        slug = source_path.stem.replace(".agent", "")
        converted_content = adapter.render_agent_file(content, slug, manifest)
    except Exception as exc:  # noqa: BLE001
        result.errors.append(f"{source_path}: {exc}")
        return

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(converted_content, encoding="utf-8")
    result.converted.append(str(dest))


def _convert_subdir(
    source_subdir: Path,
    target_dir: Path,
    adapter: FrameworkAdapter,
    manifest: dict[str, Any],
    dry_run: bool,
    overwrite: bool,
    result: ConvertResult,
) -> None:
    """Copy a passthrough subdirectory or recurse into agent subdirectories.

    Args:
        source_subdir: Absolute path to the source subdirectory.
        target_dir: Parent target directory (the agents dir).
        adapter: Target framework adapter.
        manifest: Project manifest dict.
        dry_run: Skip all filesystem writes when True.
        overwrite: Overwrite existing files when True.
        result: Mutable ConvertResult.
    """
    if source_subdir.name in _PASSTHROUGH_DIRS:
        dest_subdir = target_dir / source_subdir.name
        if dest_subdir.exists() and not overwrite:
            result.skipped.append(str(source_subdir))
            return
        if not dry_run:
            if dest_subdir.exists():
                shutil.rmtree(dest_subdir)
            shutil.copytree(source_subdir, dest_subdir)
        result.converted.append(str(dest_subdir))
    else:
        # Recurse for non-passthrough subdirectories
        nested_target = target_dir / source_subdir.name
        for entry in sorted(source_subdir.iterdir()):
            if entry.is_dir():
                _convert_subdir(entry, nested_target, adapter, manifest, dry_run, overwrite, result)
            else:
                _convert_file(entry, nested_target, adapter, manifest, dry_run, overwrite, result)


def _target_instructions_name(adapter: FrameworkAdapter) -> str:
    """Return the instructions file name for the given adapter.

    Args:
        adapter: Target framework adapter.

    Returns:
        File name string (e.g. ``"CLAUDE.md"`` or ``"copilot-instructions.md"``).
    """
    if adapter.framework_id == "claude":
        return "CLAUDE.md"
    return "copilot-instructions.md"
