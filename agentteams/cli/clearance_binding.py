"""clearance_binding.py — "is there a verified @security PASS for scope X bound to this file's exact bytes?"

Operator scripts that must run only as reviewed used to hand-roll this gate (the manager-roster rollout's script took
twelve review rounds to bind its clearance to its own sha256, 2026-11-08). :func:`check_file_clearance` is the shared
answer, exposed as ``agentteams --check-clearance SCOPE --clearance-file PATH``. The carve-out registry
(:mod:`agentteams.carveouts`) uses it for an entry's declared ``clearance``.

**Which HALTs bind (#578).** A clearance may be checked in a *gates-only team dir*: a separate team dir that holds only
signed sign-off rows, so the main team's log need not become signing-active. A HALT must not be escapable by checking
in a fresh dir (C-2), so :func:`halt_scan_dirs` adds, for every team dir, the repository's canonical team dirs
(every generated team location under each enclosing git root) and an optional declared ``parent_team``. All of it is
fail-closed (@security ruling, 2026-11-20).

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")

#: The ``agent-privilege.json`` key naming one more team dir (relative to the git toplevel) whose HALTs bind.
PARENT_TEAM_KEY = "parent_team"


def canonical_team_dirs() -> tuple[str, ...]:
    """Every generated team location, relative to a repository root; their HALTs bind clearances checked anywhere
    in that repository. The same list :data:`agentteams.carveouts.TEAM_DIRS` names, so a team rendered for any
    framework is covered (scanning more dirs only adds refusals)."""
    from agentteams.carveouts import TEAM_DIRS

    return TEAM_DIRS


def is_team_dir(path: Path) -> bool:
    """Whether *path* is an agentteams team dir: it holds ``references/agent-privilege.json`` or ``build-log.json``.

    Args:
        path: The directory to test.

    Returns:
        True when either file is present.
    """
    refs = path / "references"
    return (refs / "agent-privilege.json").is_file() or (refs / "build-log.json").is_file()


def _git_roots(team_dir: Path) -> list[Path]:
    """Every ancestor of *team_dir* holding a ``.git`` entry of any kind, innermost first.

    Both the path as given (made absolute) and its resolved form are walked, and every match is kept rather than
    the nearest: a ``.git`` planted between a gates dir and the repository root, or a gates dir reached through a
    symbolic link, must add roots to scan, never hide the real one (@security review of #578). For the same reason
    an empty or fake ``.git`` counts here, unlike ``_fleet_locks._is_git_marker``.
    """
    roots: list[Path] = []
    for start in (Path(os.path.abspath(team_dir)), team_dir.resolve()):
        for candidate in (start, *start.parents):
            if lstat_or_none(candidate / ".git", "git marker") is not None and candidate not in roots:
                roots.append(candidate)
    return roots


def lstat_or_none(path: Path, what: str) -> os.stat_result | None:
    """``os.lstat(path)``, or None when *path* is absent; any other error refuses.

    ``Path.is_dir()``, ``is_symlink()`` and ``exists()`` return False on *any* OSError from Python 3.14 (3.13 raised
    ``PermissionError``), so a team dir whose parent cannot be read would read as absent and its HALTs would vanish
    (IM-da review of #588). Only "not there" (``FileNotFoundError``, ``NotADirectoryError``) counts as absent.

    Args:
        path: The path to stat (a symbolic link is not followed).
        what: What *path* is, for the refusal message.

    Returns:
        The stat result, or None when *path* does not exist.

    Raises:
        RuntimeError: *path* exists but cannot be stat'd (permission denied, I/O error, ...).
    """
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise RuntimeError(f"{what} '{path}' cannot be read ({exc.strerror or exc}); refusing (fail-closed)") from exc


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
        st = lstat_or_none(current, what)
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise RuntimeError(f"{what} '{current}' is a symbolic link; refusing (fail-closed)")
    return current


def _log_not_linked(team: Path) -> None:
    for part in (team / "references", team / "references" / "security-decisions.log.csv"):
        st = lstat_or_none(part, "HALT log path")
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise RuntimeError(f"'{part}' is a symbolic link; a HALT log is never read through one (fail-closed)")


def halt_scan_dirs(team_dir: Path) -> list[Path]:
    """The team dirs whose decisions logs are scanned for HALTs when a clearance is checked in *team_dir*.

    Always *team_dir* itself. Inside a git repository, also each generated team dir (:func:`canonical_team_dirs`)
    that exists under every enclosing ``.git`` root, and the team dir named by *team_dir*'s ``parent_team`` (in ``references/agent-privilege.json``),
    so a HALT cannot be escaped by checking a clearance in another dir. The parent path is relative to the git
    toplevel (the innermost root); it must stay inside it, have no symbolic-link component, be a team dir other than *team_dir*, and not
    declare a ``parent_team`` of its own (one level, no chains).

    Args:
        team_dir: The team dir the clearance is checked in.

    Returns:
        The dirs to scan, *team_dir* first, without duplicates.

    Raises:
        RuntimeError: Fail-closed refusals: a canonical team dir is or sits under a symbolic link, or a scanned dir's ``references/`` or log is one; a declared parent
            is absolute, escapes the toplevel, has a symbolic-link component, is missing or not a team dir, is
            *team_dir* itself, or declares its own parent; a parent is declared outside any git repository; or an
            ``agent-privilege.json`` cannot be read.
    """
    team_dir = Path(team_dir)
    dirs = [team_dir]
    seen = {team_dir.resolve()}
    declared = _declared_parent(team_dir)
    roots = _git_roots(team_dir)
    if not roots:
        if declared is not None:
            raise RuntimeError(f"{team_dir} declares {PARENT_TEAM_KEY} '{declared}' but is not inside a git "
                               "repository, so the parent cannot be checked for containment; refusing")
        return dirs
    for root in roots:
        for rel in canonical_team_dirs():
            canonical = _no_symlink_below(root, Path(rel).parts, "canonical team dir")
            st = lstat_or_none(canonical, "canonical team dir")
            if st is not None and stat.S_ISDIR(st.st_mode) and canonical.resolve() not in seen:
                _log_not_linked(canonical)
                seen.add(canonical.resolve())
                dirs.append(canonical)
    if declared is not None:
        top = roots[0]
        rel_path = Path(declared)
        if rel_path.is_absolute() or ".." in rel_path.parts or not rel_path.parts:
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' must be a path relative to the repository root, "
                               "without '..'; refusing")
        parent = _no_symlink_below(top, rel_path.parts, f"{PARENT_TEAM_KEY} component")
        if not is_team_dir(parent):
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' is not a team dir (no references/agent-privilege.json "
                               "or references/build-log.json); refusing")
        if parent.resolve() == team_dir.resolve():
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' names the team dir itself; refusing")
        if _declared_parent(parent) is not None:
            raise RuntimeError(f"{PARENT_TEAM_KEY} '{declared}' declares a {PARENT_TEAM_KEY} of its own; "
                               "only one level is allowed; refusing")
        if parent.resolve() not in seen:
            _log_not_linked(parent)
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
