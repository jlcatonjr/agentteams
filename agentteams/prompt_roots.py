"""Prompt-root change detection (follow-up #8 phase 1, 2026-10-01).

A *prompt root* is a file another harness reads as instructions: ``.github/copilot-instructions.md``,
``.github/instructions/**``, ``.github/prompts/**``, root ``AGENTS.md``, ``.goosehints``,
``CLAUDE.md`` / ``.claude/CLAUDE.md``, and each present team's agent files (``.github/agents/*.agent.md``,
``.codex/agents/*.toml``, ``.goose/recipes/*.yaml``, ``.claude/agents/*.md``). Most of them are
writable from a Claude sandbox, so a confined agent could rewrite what an unconfined Copilot, Codex
or goose session reads next.

At every generate / ``--update`` this team's ``references/build-log.json`` gets a
``prompt_root_hashes`` map (``{project-relative path: {section id: sha256}}``):

* files agentteams **emits** (the team dir's agent files, and the shared root files this team
  writes) are hashed per ``AGENTTEAMS`` fenced region, so an edit in a USER-EDITABLE (unfenced)
  region never trips the check; a file with no parseable fence (a hand-added agent file, say) is
  hashed whole under the id ``"*"``;
* non-emitted roots (``.github/instructions/**``, ``.github/prompts/**``) are hashed whole.

Every generate, ``--update`` and ``--check`` compares the live tree with the recorded maps of every
present team and prints a ``PROMPT-ROOT CHANGED since last build`` warning. The default is warn-only;
``--check --strict-prompt-roots`` exits 1 on any change.

**Detection only.** The build-log is agent-writable outside the launcher, so an agent that can edit
a prompt root can also rewrite the recorded hashes. And a fresh generation **re-baselines**
(remediation pattern "baseline-at-generation-launders-state"): whatever is on disk when the log is
written becomes the new baseline. When generate / ``--update`` re-records over a change it has just
detected, it prints a loud ``PROMPT-ROOT RE-BASELINE`` notice first. Printed paths are
``repr``-escaped. The converged ``--update`` heal path (nothing rewritten) keeps the old map.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from agentteams.fences import _extract_fenced_regions

#: agents dir (project-relative) -> agent-file glob of its framework.
TEAM_AGENT_GLOBS: dict[str, str] = {
    ".claude/agents": "*.md", ".goose/recipes": "*.yaml",
    ".github/agents": "*.agent.md", ".codex/agents": "*.toml",
}
#: Single-file prompt roots shared by the project (tracked when agentteams emitted them).
SHARED_ROOT_FILES: tuple[str, ...] = (
    ".github/copilot-instructions.md", "AGENTS.md", ".goosehints", "CLAUDE.md", ".claude/CLAUDE.md",
)
#: Prompt-root directories agentteams never emits: snapshotted whole-file at generation.
SNAPSHOT_DIRS: tuple[str, ...] = (".github/instructions", ".github/prompts")
#: Every prompt root, as glob-style patterns (documentation and :func:`is_prompt_root`).
PROMPT_ROOTS: tuple[str, ...] = (
    *SHARED_ROOT_FILES,
    *(f"{d}/**" for d in SNAPSHOT_DIRS),
    *(f"{d}/{g}" for d, g in TEAM_AGENT_GLOBS.items()),
)
BUILD_LOG_KEY = "prompt_root_hashes"
WHOLE_FILE = "*"
_MARKER = "references/build-log.json"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: Findings of the last detection, keyed by resolved project root (consumed by re-recording and
#: by ``--strict-prompt-roots``). One CLI run is one process, so this never outlives the run.
_LAST_FINDINGS: dict[str, tuple[str, ...]] = {}
#: Findings of the most recent detection in this process (every CLI run detects before --check).
_last_run: tuple[str, ...] = ()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_prompt_root(rel: str) -> bool:
    """Return whether a project-relative POSIX path is a prompt root.

    Args:
        rel: Project-relative path with ``/`` separators.

    Returns:
        ``True`` for a shared root file, a file under a snapshot dir, or a team agent file.
    """
    if rel in SHARED_ROOT_FILES or any(rel.startswith(d + "/") for d in SNAPSHOT_DIRS):
        return True
    head, _, name = rel.rpartition("/")
    glob = TEAM_AGENT_GLOBS.get(head)
    return glob is not None and Path(name).match(glob)


def region_hashes(text: str) -> dict[str, str]:
    """Hash each ``AGENTTEAMS`` fenced region of *text*.

    Args:
        text: File content.

    Returns:
        ``{section_id: sha256}``; ``{"*": sha256 of the whole text}`` when the text has no fence or
        its fences do not parse (a corrupted fence is itself a change worth seeing).
    """
    regions = _extract_fenced_regions(text)
    if isinstance(regions, dict) and regions:
        return {sid: _sha(block.encode("utf-8")) for sid, block in regions.items()}
    return {WHOLE_FILE: _sha(text.encode("utf-8"))}


#: Files larger than this are not read; their size and mtime are hashed instead.
MAX_HASH_BYTES = 4 * 1024 * 1024


def _symlinked_parent(path: Path, root: Path | None) -> str | None:
    """The first symlinked directory between ``root`` and ``path`` (exclusive), else None."""
    if root is None:
        return None
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return "outside the project"
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            return cur.relative_to(root).as_posix()
    return None


def _read_regular(path: Path) -> bytes | str:
    """Bytes of a regular file (O_NONBLOCK|O_NOFOLLOW, capped), or a marker string for anything else."""
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode):
        return "symlink:" + os.readlink(path)
    if not stat.S_ISREG(st.st_mode):
        return f"nonregular:{stat.S_IFMT(st.st_mode)}"  # a FIFO/device is never opened (no hang)
    if st.st_size > MAX_HASH_BYTES:
        return f"oversized:{st.st_size}:{st.st_mtime_ns}"
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as fh:
        return fh.read(MAX_HASH_BYTES + 1)


def _file_hashes(path: Path, *, fenced: bool, root: Path | None = None) -> dict[str, str] | None:
    """Hash one root without following links or blocking. ``None`` if unreadable.

    A symlink is hashed by its target string; a FIFO, device or oversized file by a marker (never
    read); a path under a symlinked directory by that directory (reported as a change, never
    walked); otherwise per fenced region (``fenced``) or whole.
    """
    linked = _symlinked_parent(path, root)
    if linked is not None:
        return {WHOLE_FILE: _sha(b"symlinked-parent:" + linked.encode("utf-8"))}
    try:
        data = _read_regular(path)
    except OSError:
        return None
    if isinstance(data, str):
        return {WHOLE_FILE: _sha(data.encode("utf-8", "replace"))}
    if fenced:
        return region_hashes(data.decode("utf-8", errors="replace"))
    return {WHOLE_FILE: _sha(data)}


def _team_agent_files(root: Path, team_dir: str) -> list[str]:
    """Project-relative agent files of one team dir (a symlinked dir is listed as itself)."""
    base = root / team_dir
    if base.is_symlink():
        return [team_dir]
    if not base.is_dir():
        return []
    return sorted(f"{team_dir}/{p.name}" for p in base.glob(TEAM_AGENT_GLOBS[team_dir])
                  if p.is_file() or p.is_symlink())


def _snapshot_files(root: Path) -> list[str]:
    """Project-relative files under the snapshot dirs, never following a symlink."""
    out: list[str] = []
    for rel_dir in SNAPSHOT_DIRS:
        base = root / rel_dir
        if base.is_symlink():
            out.append(rel_dir)
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            names = filenames + [d for d in dirnames if (Path(dirpath) / d).is_symlink()]
            out.extend(Path(dirpath, n).relative_to(root).as_posix() for n in names)
    return sorted(out)


def team_project_root(output_dir: Path) -> tuple[Path, str] | None:
    """Split a team's agents dir into ``(project root, team dir)``.

    Args:
        output_dir: The agents dir a run wrote (e.g. ``<project>/.github/agents``).

    Returns:
        The project root and the matched team dir, or ``None`` for a non-default layout (nothing
        is recorded there: there is no harness that reads it as a prompt root).
    """
    posix = output_dir.resolve().as_posix()
    for team_dir in TEAM_AGENT_GLOBS:
        if posix.endswith("/" + team_dir):
            return output_dir.resolve().parents[team_dir.count("/")], team_dir
    return None


def compute_prompt_root_hashes(
    project_root: Path, team_dir: str, emitted_abs_paths: list[str],
    previous: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Compute the ``prompt_root_hashes`` map for one team's build-log.

    Args:
        project_root: The resolved project root.
        team_dir: This team's agents dir, project-relative (a key of :data:`TEAM_AGENT_GLOBS`).
        emitted_abs_paths: Every file this run wrote, merged or left unchanged. An incremental
            ``--update`` lists only what it re-rendered, so a shared root file is also kept when
            *previous* recorded it.
        previous: This team's previous ``prompt_root_hashes`` map, if any.

    Returns:
        ``{rel_path: {section_id: sha256}}``: the team dir's agent files and this team's shared root
        files per fenced region, the snapshot dirs whole-file.
    """
    shared: set[str] = {k for k in (previous or {}) if k in SHARED_ROOT_FILES}
    for abs_path in emitted_abs_paths:
        resolved = Path(abs_path).resolve()
        if resolved.is_relative_to(project_root):
            rel = resolved.relative_to(project_root).as_posix()
            if rel in SHARED_ROOT_FILES:
                shared.add(rel)
    snapshot = set(_snapshot_files(project_root))
    out: dict[str, dict[str, str]] = {}
    for rel in sorted(shared | set(_team_agent_files(project_root, team_dir)) | snapshot):
        hashes = _file_hashes(project_root / rel, fenced=rel not in snapshot, root=project_root)
        if hashes is not None:
            out[rel] = hashes
    return out


def add_to_build_log(log: dict[str, Any], output_dir: Path, emitted_abs_paths: list[str]) -> None:
    """Record ``prompt_root_hashes`` on a build-log dict about to be written (re-baselines).

    Prints a loud ``PROMPT-ROOT RE-BASELINE`` notice first when this run's detection found changes.

    Args:
        log: The build-log dict (mutated: :data:`BUILD_LOG_KEY`).
        output_dir: This run's agents dir.
        emitted_abs_paths: Every file this run wrote, merged or left unchanged.
    """
    split = team_project_root(output_dir)
    if split is None:
        return
    root, team_dir = split
    pending = _LAST_FINDINGS.pop(str(root), ())
    if pending:
        print(f"\n  !! PROMPT-ROOT RE-BASELINE: {len(pending)} prompt-root change(s) were detected above, "
              f"and this run now re-records the hashes in {team_dir!r}'s build-log. Every change in "
              "the roots it records becomes the new baseline and will not be reported again. If you "
              "did not make them, review them (git diff) now.", file=sys.stderr)
    old = _read_log(output_dir / _MARKER)
    previous = old.get(BUILD_LOG_KEY) if isinstance(old, dict) else None
    log[BUILD_LOG_KEY] = compute_prompt_root_hashes(
        root, team_dir, emitted_abs_paths, previous if isinstance(previous, dict) else None)


def _read_log(log_path: Path) -> Any:
    """Parse a regular (non-symlink) build-log; ``None`` when absent, symlinked or malformed."""
    if log_path.is_symlink() or not log_path.is_file():
        return None
    try:
        return json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_records(root: Path) -> list[tuple[str, str, dict[str, dict[str, str]]]]:
    """``(generated_at, team_dir, map)`` for every present team whose build-log records hashes."""
    records = []
    for team_dir in TEAM_AGENT_GLOBS:
        log_path = root / team_dir / _MARKER
        log = _read_log(log_path)
        recorded = log.get(BUILD_LOG_KEY) if isinstance(log, dict) else None
        if not isinstance(recorded, dict):
            continue  # legacy log (before #8): nothing to compare
        clean = {str(k): {str(s): str(h) for s, h in v.items()} if isinstance(v, dict) else {}
                 for k, v in recorded.items()}
        records.append((str(log.get("generated_at") or ""), team_dir, clean))
    return sorted(records)


def _diff_file(rel: str, recorded: dict[str, str], current: dict[str, str] | None) -> list[str]:
    if current is None:
        return [f"{rel!r}: removed or unreadable"]
    if WHOLE_FILE in recorded or WHOLE_FILE in current:
        same = recorded == current and all(_HEX64.match(h) for h in recorded.values())
        return [] if same else [f"{rel!r}: content changed"]
    out = []
    for sid in sorted(set(recorded) | set(current)):
        if sid not in current:
            out.append(f"{rel!r}: fenced region {sid!r} removed")
        elif sid not in recorded:
            out.append(f"{rel!r}: fenced region {sid!r} added")
        elif not _HEX64.match(recorded[sid]) or recorded[sid] != current[sid]:
            out.append(f"{rel!r}: fenced region {sid!r} changed")
    return out


def detect_prompt_root_changes(project_root: Path) -> list[str]:
    """Compare the live prompt roots with every present team's recorded hashes.

    Each team's own agent files are compared with that team's log; a shared root with the newest
    log that records it; the snapshot dirs with the newest log overall (so a sibling team's older
    baseline does not re-report a change a newer generation already recorded).

    Args:
        project_root: The project root.

    Returns:
        Sorted, de-duplicated findings (paths ``repr``-escaped); empty when nothing changed or no
        present team's build-log records ``prompt_root_hashes``.
    """
    root = project_root.resolve()
    records = _load_records(root)
    if not records:
        return []
    found: set[str] = set()
    shared: dict[str, dict[str, str]] = {}
    for _at, team_dir, recorded in records:
        own = {k for k in recorded if k == team_dir or k.startswith(team_dir + "/")}
        for rel in own | set(_team_agent_files(root, team_dir)):
            if rel not in recorded:
                found.add(f"{rel!r}: added")
                continue
            found.update(_diff_file(rel, recorded[rel], _file_hashes(root / rel, fenced=True, root=root)))
        shared.update({k: v for k, v in recorded.items() if k in SHARED_ROOT_FILES})
    for rel, hashes in shared.items():
        found.update(_diff_file(rel, hashes, _file_hashes(root / rel, fenced=True, root=root)))
    newest = records[-1][2]
    snap_recorded = {k for k in newest if any(k == d or k.startswith(d + "/") for d in SNAPSHOT_DIRS)}
    for rel in snap_recorded | set(_snapshot_files(root)):
        if rel not in newest:
            found.add(f"{rel!r}: added")
        else:
            found.update(_diff_file(rel, newest[rel], _file_hashes(root / rel, fenced=False, root=root)))
    return sorted(found)


def print_prompt_root_warnings(project_root: Path) -> list[str]:
    """Detect prompt-root changes, print the warning block to stderr and remember the findings.

    Args:
        project_root: The project root.

    Returns:
        The findings (see :func:`detect_prompt_root_changes`).
    """
    global _last_run
    findings = detect_prompt_root_changes(project_root)
    _last_run = tuple(findings)
    _LAST_FINDINGS[str(project_root.resolve())] = tuple(findings)
    if findings:
        print(f"\n  WARNING: PROMPT-ROOT CHANGED since last build ({len(findings)} change(s)). "
              "Copilot, Codex, goose and Claude read these as instructions:", file=sys.stderr)
        for line in findings:
            print(f"    ✗ {line}", file=sys.stderr)
        print("  If you did not make these edits, review them (git diff) before running an agent. "
              "Detection only (the build-log is agent-writable); `--check --strict-prompt-roots` "
              "exits 1 on this.", file=sys.stderr)
    return findings


def strict_failure() -> bool:
    """Return whether this run's prompt-root detection found any change (``--strict-prompt-roots``).

    Every generate / ``--update`` / ``--check`` run calls :func:`print_prompt_root_warnings` (from
    ``generate_helpers._apply_sibling_team_denies``) before the ``--check`` handler reads this.

    Returns:
        ``True`` when the most recent :func:`print_prompt_root_warnings` reported a change.
    """
    return bool(_last_run)
