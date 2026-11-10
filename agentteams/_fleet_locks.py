"""Fleet guards: release locks (``toolchain.lock``) and ``--fleet-exclude`` globs.

Split out of ``agentteams.fleet`` to keep that module under the CH-07 line ceiling.

A repository *release-locks* agentteams when its ``toolchain.lock`` names an agentteams pin::

    agentteams=jlcatonjr/agentteams@<commit>
    agentteams.tag=vX.Y.Z

Such a repository moves only through its own verified pin process (validate, gates, signed lock bump). A fleet
render with whatever build is running would undo the pin, which is what happened in the 2026-11-09 weekly run. The
fleet therefore skips it with a visible SKIP row, unless a ``--fleet-ignore-locks`` glob matches its repository.
"""
from __future__ import annotations

import os
from fnmatch import fnmatchcase
from pathlib import Path

#: The lock file name; the same file the researchteam toolchain scripts read and write.
LOCK_FILE = "toolchain.lock"


class _Unparsable(ValueError):
    """A lock file that is not ``key=value`` lines: in doubt, the workspace is skipped."""


def _parse_lock(path: Path) -> dict[str, str]:
    """``key=value`` lines of a lock file; comments and blank lines are ignored.

    Raises ``_Unparsable`` for a non-comment line without ``=`` or bytes that are not UTF-8, and ``OSError``
    when the file cannot be read.
    """
    pairs: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise _Unparsable("not UTF-8") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise _Unparsable(f"line without '=': {line[:40]!r}")
        key, _, value = line.partition("=")
        pairs.setdefault(key.strip(), value.strip())
    return pairs


def _is_git_marker(git: Path) -> bool:
    """Whether *git* marks a repository toplevel: a ``.git`` directory, or a ``.git`` file whose first line is
    ``gitdir: <path>`` (a linked worktree or submodule). An empty or other planted ``.git`` file does not, so
    it cannot cut the lock search short below the repository's real toplevel."""
    try:
        if git.is_dir():
            return True
        if git.is_file():
            with git.open("rb") as fh:
                return fh.read(64).startswith(b"gitdir:")
    except OSError:
        return False
    return False


def _git_toplevel(ws: Path) -> Path | None:
    """The nearest directory at or above *ws* holding a real ``.git`` marker (``_is_git_marker``), or ``None``."""
    for d in (ws, *ws.parents):
        if _is_git_marker(d / ".git"):
            return d
    return None


def read_release_lock(ws: Path, parent: Path) -> dict[str, str] | None:
    """The agentteams release lock that governs *ws*, if any.

    ``toolchain.lock`` files are searched for from *ws* upward to a bound. The bound is the workspace's git
    toplevel (the nearest directory holding a ``.git`` directory, or a ``.git`` file starting ``gitdir:``),
    wherever the fleet *parent* is;
    so ``--fleet <repo>/Projects`` still sees ``<repo>/toolchain.lock``. Only for a workspace in no git repository
    is the bound the fleet *parent*, so a lock outside the tree being updated is never applied. The nearest lock
    that names agentteams decides; a lock that does not (a sub-tool's) is passed over, so it cannot hide the
    repository's own pin. A git submodule or nested repository is its own toplevel and does not inherit its
    superproject's lock.

    Args:
        ws: The workspace directory.
        parent: The fleet parent directory (the search bound for a workspace outside git).

    Returns:
        ``{"tag": ..., "commit": ..., "lock": <path>}`` for the governing lock (an ``agentteams.tag`` key, an
        ``agentteams=<repo>@<commit>`` key, or both; a missing part is ``""``), else ``None``. A lock that
        fails closed counts as a lock with an empty tag and commit, so in doubt the workspace is skipped: an
        unreadable or unparsable lock file, or a ``toolchain.lock`` path that exists but is not a regular file
        (a directory, a dangling or looping symlink).

    Raises:
        Nothing.
    """
    ws, parent = Path(ws).resolve(), Path(parent).resolve()
    try:
        bound = _git_toplevel(ws) or parent
    except OSError:
        bound = parent
    for d in (ws, *ws.parents):
        lock = d / LOCK_FILE
        try:
            if os.path.lexists(lock) and not lock.is_file():   # a directory, dangling or looping symlink
                return {"tag": "", "commit": "", "lock": str(lock)}
            if lock.is_file():
                pairs = _parse_lock(lock)
                if "agentteams.tag" in pairs or "agentteams" in pairs:
                    pin = pairs.get("agentteams", "")
                    commit = pin.partition("@")[2] if "@" in pin else ""
                    return {"tag": pairs.get("agentteams.tag", ""), "commit": commit, "lock": str(lock)}
        except (OSError, _Unparsable):
            return {"tag": "", "commit": "", "lock": str(lock)}
        if d == bound:
            return None
    return None


def lock_detail(lock: dict[str, str]) -> str:
    """The SKIP-row detail for a release-locked workspace.

    Args:
        lock: A ``read_release_lock`` result.

    Returns:
        A one-line explanation naming the pin and the opt-out flag.

    Raises:
        Nothing.
    """
    pin = " ".join(p for p in (lock["tag"], f"({lock['commit'][:12]})" if lock["commit"] else "") if p)
    pin = pin or "(unreadable, unparsable or not a file: skipped)"
    return (f"release-locked: {lock['lock']} pins agentteams {pin}; it moves only through "
            "its own pin process (pass --fleet-ignore-locks GLOB to render it anyway)")


def lock_ignored(lock: dict[str, str], parent: Path, patterns: list[str] | tuple[str, ...]) -> str | None:
    """The first ``--fleet-ignore-locks`` glob that lifts *lock*, or ``None``.

    A glob is matched against the directory holding the lock, i.e. the locked repository, not the workspace.
    So ``--fleet-ignore-locks researchteam`` lifts researchteam's lock for every workspace it governs, and a
    workspace-name glob such as ``docs`` cannot lift the locks of every repository that happens to have one.
    The directory is taken relative to the fleet *parent* when it lies inside it, else by its own name.

    Args:
        lock: A ``read_release_lock`` result.
        parent: The fleet parent directory.
        patterns: The ``--fleet-ignore-locks`` globs.

    Returns:
        The matching pattern, or ``None``.

    Raises:
        TypeError: *patterns* is a bare string.
    """
    if not patterns:
        return None
    lock_dir, parent = Path(lock["lock"]).parent, Path(parent).resolve()
    try:
        rel = lock_dir.relative_to(parent)
    except ValueError:
        rel = Path(lock_dir.name)
    if rel == Path("."):
        rel = Path(lock_dir.name)
    return excluded_by(rel, patterns)


def excluded_by(rel: Path, patterns: list[str] | tuple[str, ...]) -> str | None:
    """The first ``--fleet-exclude`` glob that matches a folder, or ``None``.

    A pattern matches the folder's path relative to the fleet parent (POSIX form, e.g. ``visualknowledge/old*``)
    or the folder's own name (e.g. ``agentteams*``). Matching is case-sensitive.

    Args:
        rel: The folder's path relative to the fleet parent.
        patterns: The ``--fleet-exclude`` globs.

    Returns:
        The matching pattern, or ``None``.

    Raises:
        TypeError: *patterns* is a bare string (it would be matched one character at a time).
    """
    if isinstance(patterns, str):
        raise TypeError("patterns must be a list of globs, not a string")
    posix = Path(rel).as_posix()
    for pat in patterns:
        if fnmatchcase(posix, pat) or fnmatchcase(Path(rel).name, pat):
            return pat
    return None
