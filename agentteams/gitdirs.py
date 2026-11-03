"""gitdirs.py — find a checkout's git directories from its files, without spawning git.

One place for "which ``.git`` does this path belong to", with custody checks: :func:`git_dirs` walks up to the
first ``.git`` and returns the per-checkout git directory and the common directory. A linked worktree's ``.git``
is a file whose ``gitdir:`` line names its per-checkout directory (``<main>/.git/worktrees/<name>``), which
holds a ``commondir`` file naming the shared one.

Nothing here follows a symlink: ``.git``, the directory a ``gitdir:`` line names and the ``commondir`` target
are ``lstat``-ed and refused when they are links, and every file is read ``O_NOFOLLOW`` with a size cap. A
``gitdir:`` target must look like a git directory (it holds ``HEAD``), and a linked worktree's directory must
point back at the ``.git`` file that named it (its ``gitdir`` file), so a ``.git`` file cannot borrow another
checkout's git directory.

Only :mod:`agentteams.update_lock` uses this so far; ``vulnscan/registry.py``, ``vulnscan/subproc_confine.py``
and ``git_hooks.py`` still carry their own lookups.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from agentteams.atomicio import read_regular_nofollow

#: The largest ``.git`` / ``gitdir`` / ``commondir`` file read (each holds one path).
MAX_POINTER_BYTES = 4096


class GitDirError(RuntimeError):
    """A ``.git`` pointer failed a custody or consistency check."""


def read_pointer(path: Path) -> str:
    """Read a small git pointer file (``.git``, ``gitdir``, ``commondir``, ``HEAD``, a ref) without following a link.

    Args:
        path: The file to read.

    Returns:
        Its text, stripped.

    Raises:
        GitDirError: It is not a regular file or is larger than :data:`MAX_POINTER_BYTES`.
        OSError: It can't be opened (missing, a symlink, unreadable).
    """
    try:
        data = read_regular_nofollow(path, MAX_POINTER_BYTES)
    except ValueError as exc:  # not regular, or FileTooLargeError
        raise GitDirError(f"{path}: {exc}") from exc
    return data.decode("utf-8", "replace").strip()


def _real_dir(path: Path, what: str) -> None:
    """Refuse *path* unless ``lstat`` shows a directory (a symlink is refused, not followed)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError as exc:
        raise GitDirError(f"{what} {path} does not exist") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise GitDirError(f"{what} {path} is a symlink or not a directory")


def git_dirs(start: Path) -> tuple[Path, Path] | None:
    """Return ``(git_dir, common_dir)`` for the checkout containing *start*.

    Args:
        start: A path inside the checkout; resolved first, and it need not exist yet.

    Returns:
        The per-checkout git directory and the common directory (the same path for a main checkout), both
        absolute with ``..`` resolved, or ``None`` outside a git checkout.

    Raises:
        GitDirError: ``.git``, a ``gitdir:`` target or a ``commondir`` target is a symlink or not what it should
            be; a ``.git`` file has no ``gitdir:`` line; the target holds no ``HEAD``; a linked worktree's
            ``gitdir`` back-pointer names another ``.git``; or a pointer file is oversized.
        OSError: A pointer file exists but can't be read (a sandbox denying it).
    """
    start = Path(start).resolve()
    for base in (start, *start.parents):
        dot_git = base / ".git"
        if not os.path.lexists(dot_git):
            continue
        st = os.lstat(dot_git)
        if stat.S_ISDIR(st.st_mode):
            return dot_git, _common_dir(dot_git).resolve()
        if not stat.S_ISREG(st.st_mode):
            raise GitDirError(f"{dot_git} is a symlink or not a regular file/directory")
        match = re.search(r"^gitdir:\s*(.+?)\s*$", read_pointer(dot_git), re.M)
        if not match:
            raise GitDirError(f"{dot_git} has no gitdir: line")
        target = Path(match.group(1))
        target = target if target.is_absolute() else base / target
        _real_dir(target, f"the git directory named by {dot_git},")
        if not os.path.lexists(target / "HEAD"):
            raise GitDirError(f"{target} (named by {dot_git}) holds no HEAD; not a git directory")
        if os.path.lexists(target / "gitdir"):  # a linked worktree: its back-pointer must name this .git
            back = Path(read_pointer(target / "gitdir"))
            back = back if back.is_absolute() else target / back
            if back.resolve() != dot_git.resolve():
                raise GitDirError(f"{target} belongs to {back}, not {dot_git} (moved worktree? run "
                                  "`git worktree repair`)")
        return target.resolve(), _common_dir(target).resolve()  # links refused above; resolve tidies `..`
    return None


def _common_dir(git_dir: Path) -> Path:
    """The shared git directory: ``git_dir``'s ``commondir`` target, else ``git_dir`` itself."""
    pointer = git_dir / "commondir"
    if not os.path.lexists(pointer):
        return git_dir
    rel = Path(read_pointer(pointer))
    common = rel if rel.is_absolute() else git_dir / rel
    _real_dir(common, f"the common directory named by {pointer},")
    return common
