"""clearance_binding.py — "is there a verified @security PASS for scope X bound to this file's exact bytes?"

Operator scripts that must run only as reviewed used to hand-roll this gate (the manager-roster rollout's script took
twelve review rounds to bind its clearance to its own sha256, 2026-11-08). :func:`check_file_clearance` is the shared
answer, exposed as ``agentteams --check-clearance SCOPE --clearance-file PATH``. The carve-out registry
(:mod:`agentteams.carveouts`) uses it for an entry's declared ``clearance``.

**Which HALTs bind (#578).** A clearance may be checked in a *gates-only team dir*: a separate team dir that holds only
signed sign-off rows, so the main team's log need not become signing-active. A HALT must not be escapable by checking
in a fresh dir (C-2), so :func:`halt_scan_dirs` adds, for every team dir, the repository's canonical team dirs
(``.claude/agents``, ``.github/agents`` under the git toplevel) and an optional declared ``parent_team``. All of it is
fail-closed (@security ruling, 2026-11-20).

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

#: Team dirs whose HALTs bind every clearance checked anywhere in the same repository.
CANONICAL_TEAM_DIRS: tuple[str, ...] = (".claude/agents", ".github/agents")

#: The ``agent-privilege.json`` key naming one more team dir (relative to the git toplevel) whose HALTs bind.
PARENT_TEAM_KEY = "parent_team"


def _is_team_dir(path: Path) -> bool:
    refs = path / "references"
    return (refs / "agent-privilege.json").is_file() or (refs / "build-log.json").is_file()


def _git_toplevel(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _declared_parent(team_dir: Path) -> str | None:
    """The ``parent_team`` a team dir declares, or None.

    Raises:
        RuntimeError: ``agent-privilege.json`` exists but is not a JSON object, or ``parent_team`` is not a
            non-empty string.
    """
    priv = team_dir / "references" / "agent-privilege.json"
    if not priv.is_file():
        return None
    try:
        data = json.loads(priv.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{priv} cannot be read, so its {PARENT_TEAM_KEY} is unknown: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{priv} is not a JSON object, so its {PARENT_TEAM_KEY} is unknown")
    value = data.get(PARENT_TEAM_KEY)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{priv}: {PARENT_TEAM_KEY} must be a non-empty relative path")
    return value


def _no_symlink_below(top: Path, rel_parts: tuple[str, ...], what: str) -> Path:
    """``top / rel_parts``, refusing when any component below *top* is a symbolic link."""
    current = top
    for part in rel_parts:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"{what} '{current}' is a symbolic link; refusing (fail-closed)")
    return current


def halt_scan_dirs(team_dir: Path) -> list[Path]:
    """The team dirs whose decisions logs are scanned for HALTs when a clearance is checked in *team_dir*.

    Always *team_dir* itself. Inside a git repository, also each canonical team dir (:data:`CANONICAL_TEAM_DIRS`)
    that exists, and the team dir named by *team_dir*'s ``parent_team`` (in ``references/agent-privilege.json``),
    so a HALT cannot be escaped by checking a clearance in another dir. The parent path is relative to the git
    toplevel; it must stay inside it, have no symbolic-link component, be a team dir other than *team_dir*, and not
    declare a ``parent_team`` of its own (one level, no chains).

    Args:
        team_dir: The team dir the clearance is checked in.

    Returns:
        The dirs to scan, *team_dir* first, without duplicates.

    Raises:
        RuntimeError: Fail-closed refusals: a canonical team dir is or sits under a symbolic link; a declared parent
            is absolute, escapes the toplevel, has a symbolic-link component, is missing or not a team dir, is
            *team_dir* itself, or declares its own parent; a parent is declared outside any git repository; or an
            ``agent-privilege.json`` cannot be read.
    """
    team_dir = Path(team_dir)
    dirs = [team_dir]
    seen = {team_dir.resolve()}
    declared = _declared_parent(team_dir)
    top = _git_toplevel(team_dir.resolve())
    if top is None:
        if declared is not None:
            raise RuntimeError(f"{team_dir} declares {PARENT_TEAM_KEY} '{declared}' but is not inside a git "
                               "repository, so the parent cannot be checked for containment; refusing")
        return dirs
    for rel in CANONICAL_TEAM_DIRS:
        canonical = _no_symlink_below(top, Path(rel).parts, "canonical team dir")
        if canonical.is_dir() and canonical.resolve() not in seen:
            seen.add(canonical.resolve())
            dirs.append(canonical)
    if declared is not None:
        rel_path = Path(declared)
        if rel_path.is_absolute() or ".." in rel_path.parts or not rel_path.parts:
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' must be a path relative to the repository root, "
                               "without '..'; refusing")
        parent = _no_symlink_below(top, rel_path.parts, f"{PARENT_TEAM_KEY} component")
        if not _is_team_dir(parent):
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' is not a team dir (no references/agent-privilege.json "
                               "or references/build-log.json); refusing")
        if parent.resolve() == team_dir.resolve():
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' names the team dir itself; refusing")
        if _declared_parent(parent) is not None:
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' declares a {PARENT_TEAM_KEY} of its own; "
                               "only one level is allowed; refusing")
        if parent.resolve() not in seen:
            seen.add(parent.resolve())
            dirs.append(parent)
    return dirs


def clearance_action(scope: str, path: Path) -> str:
    """The gate action id binding *scope* to *path*'s exact bytes: ``<scope>:sha256-<hex>``.

    ``action_matches`` lets a row's id carry a suffix but never lets a shorter id match a longer action, so a row for
    the bare scope, or for another file's sha, can't satisfy this action.

    Args:
        scope: The clearance scope (an action id).
        path: The file the clearance is bound to.

    Returns:
        The action id.

    Raises:
        ValueError: *scope* is not a plain action id.
        OSError: *path* can't be read.
    """
    if not _SCOPE_RE.match(scope):
        raise ValueError(f"'{scope}' is not a plain action id")
    return f"{scope}:sha256-{hashlib.sha256(path.read_bytes()).hexdigest()}"


def check_file_clearance(team_dir: Path, scope: str, path: Path) -> tuple[bool, str]:
    """Whether a verified @security PASS exists for *scope* bound to *path*'s sha256, with no HALT on *scope* itself.

    Read-only: spends nothing. The caller must then use the bytes it checked (hash, then run the same file). The PASS
    is read from *team_dir*; HALTs on *scope* or the bound action are read from every dir :func:`halt_scan_dirs`
    names, each log under its own retraction and signing rules.

    Args:
        team_dir: The team directory holding ``references/security-decisions.log.csv``.
        scope: The clearance scope.
        path: The file the clearance must be bound to.

    Returns:
        ``(cleared, reason)``; *reason* says why not, and names the action id a clearance row must use.
    """
    from agentteams.cli.decision_log import _assert_no_unretracted_halt
    from agentteams.cli.security_gate import check_clearance

    try:
        action = clearance_action(scope, path)
    except (ValueError, OSError) as exc:
        return False, str(exc)
    try:  # a HALT on the bare scope stops every sha-bound clearance under it (C-2), in any binding log (#578)
        scan = halt_scan_dirs(team_dir)
        for halt_dir in scan:
            _assert_no_unretracted_halt(halt_dir, action=scope)
            if halt_dir != scan[0]:
                _assert_no_unretracted_halt(halt_dir, action=action)
    except RuntimeError as exc:
        return False, str(exc)
    ok, reason = check_clearance(team_dir, action=action)
    return ok, "" if ok else f"{reason} (a clearance for this file's exact bytes uses action_reviewed '{action}')"
