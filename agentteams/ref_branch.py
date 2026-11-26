"""ref_branch.py — read a repository's default branch, and say how stale the answer may be.

A verdict such as "no tool covers this" is only as good as the tree it was read from. A local
checkout can lag the default branch, and the remote-tracking ref lags too, by however long ago
the last fetch was. This module gives every audit one way to:

* name the reference ref (:func:`default_ref`): ``origin/HEAD``, else ``origin/main`` or
  ``origin/master``, else a local ``main`` or ``master``;
* read a file from it without touching the working tree (:func:`show`);
* see whether a path changed there recently (:func:`changed_since`);
* report how far the checkout is behind it and how old the last fetch is (:func:`freshness`),
  so the ref and its age can be printed beside the verdict.

It never fetches, never guesses a default branch, and never writes. Git runs in the given
repository only, with the read-only hardening of :mod:`agentteams.git_exec`, with inherited
``GIT_*`` variables removed, and file content is read with ``cat-file blob`` so no filter
driver or textconv runs.

Usage::

    python -m agentteams.ref_branch [REPO] [--json] [--max-fetch-age HOURS]
    python -m agentteams.ref_branch [REPO] --show PATH

Standard library only.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agentteams.git_exec import run_git

#: Seconds before a git query is abandoned.
GIT_TIMEOUT = 20.0
_REMOTE_CANDIDATES = ("origin/main", "origin/master")
_LOCAL_CANDIDATES = ("main", "master")


class RefBranchError(ValueError):
    """The repository has no resolvable default branch, or is not a git repository."""


@dataclasses.dataclass(frozen=True)
class Freshness:
    """How current a repository's view of its default branch is.

    Attributes:
        ref: The reference ref, e.g. ``origin/main``.
        remote: True when *ref* is a remote-tracking ref; False when only a local branch was found.
        branch: The checked-out branch, or ``(detached)``.
        behind: Commits on *ref* that ``HEAD`` lacks; ``None`` when it could not be counted.
        fetch_age_hours: Hours since the last fetch; ``None`` when the repository was never fetched.
    """

    ref: str
    remote: bool
    branch: str
    behind: int | None
    fetch_age_hours: float | None

    def describe(self) -> str:
        """One line to print beside a verdict read from :attr:`ref`."""
        if not self.remote:
            age = "local branch only: no remote-tracking ref"
        elif self.fetch_age_hours is None:
            age = "never fetched"
        else:
            age = f"fetched {self.fetch_age_hours:.1f} h ago"
        behind = "position unknown" if self.behind is None else (
            "checkout up to date" if self.behind == 0 else f"checkout {self.behind} commit(s) behind")
        return f"{self.ref} ({age}; {behind}; on {self.branch})"


def _env() -> dict[str, str]:
    """The process environment without inherited ``GIT_*`` variables (``GIT_DIR`` would redirect every query)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        proc = run_git(repo, *args, hardened=True, env=_env(), timeout=GIT_TIMEOUT, devnull_stdin=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RefBranchError(f"git could not run in {repo}: {exc}") from exc
    return proc.returncode, (proc.stdout or "")


def default_ref(repo: Path | str) -> str:
    """The ref that stands for the repository's default branch.

    Args:
        repo: A directory inside a git repository.

    Returns:
        ``origin/<name>`` when a remote-tracking ref exists, else a local ``main`` or ``master``.

    Raises:
        RefBranchError: *repo* is not a git repository, or it has none of those refs.
    """
    root = Path(repo)
    code, _ = _git(root, "rev-parse", "--git-dir")
    if code != 0:
        raise RefBranchError(f"{root} is not a git repository")
    code, out = _git(root, "rev-parse", "--abbrev-ref", "-q", "origin/HEAD")
    name = out.strip()
    if code == 0 and name and name != "origin/HEAD":
        return name
    for candidate in _REMOTE_CANDIDATES:
        if _git(root, "rev-parse", "--verify", "-q", f"refs/remotes/{candidate}")[0] == 0:
            return candidate
    for candidate in _LOCAL_CANDIDATES:
        if _git(root, "rev-parse", "--verify", "-q", f"refs/heads/{candidate}")[0] == 0:
            return candidate
    raise RefBranchError(f"{root} has no origin/HEAD, origin/main, origin/master, main or master")


def show(repo: Path | str, path: str, *, ref: str | None = None) -> str | None:
    """The content of *path* at the reference ref, read without touching the working tree.

    Args:
        repo: A directory inside a git repository.
        path: Path relative to the repository root, with forward slashes.
        ref: The ref to read; defaults to :func:`default_ref`.

    Returns:
        The file's text (undecodable bytes replaced), or ``None`` when *path* is not a file at *ref*.

    Raises:
        RefBranchError: The default ref cannot be resolved, or *path* is not a plain relative path.
    """
    if not path or path.startswith(("/", "-")) or ":" in path or ".." in Path(path).parts:
        raise RefBranchError(f"not a plain repository-relative path: {path!r}")
    root = Path(repo)
    target = ref or default_ref(root)
    try:
        proc = run_git(root, "cat-file", "blob", f"{target}:{path}", hardened=True, env=_env(),
                       timeout=GIT_TIMEOUT, text=False, devnull_stdin=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RefBranchError(f"git could not run in {root}: {exc}") from exc
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace")


def changed_since(repo: Path | str, path: str, days: int, *, ref: str | None = None) -> list[str]:
    """Commits on the reference ref that touched *path* in the last *days* days.

    Args:
        repo: A directory inside a git repository.
        path: A file or directory relative to the repository root.
        days: How far back to look.
        ref: The ref to read; defaults to :func:`default_ref`.

    Returns:
        ``"<short sha> <date> <subject>"`` lines, newest first; empty when nothing changed.

    Raises:
        RefBranchError: The default ref cannot be resolved, or *days* is negative.
    """
    if days < 0:
        raise RefBranchError("days must not be negative")
    root = Path(repo)
    target = ref or default_ref(root)
    code, out = _git(root, "log", f"--since={days}.days", "--format=%h %as %s", "--no-ext-diff", "--no-textconv",
                     target, "--", path)
    return [line for line in out.splitlines() if line.strip()] if code == 0 else []


def freshness(repo: Path | str, *, ref: str | None = None) -> Freshness:
    """How far the checkout is behind the reference ref, and how old the last fetch is.

    Args:
        repo: A directory inside a git repository.
        ref: The ref to compare with; defaults to :func:`default_ref`.

    Returns:
        The measurement. Nothing is fetched, so :attr:`Freshness.fetch_age_hours` bounds how
        current the ref itself is.

    Raises:
        RefBranchError: The default ref cannot be resolved.
    """
    root = Path(repo)
    target = ref or default_ref(root)
    remote = _git(root, "rev-parse", "--verify", "-q", f"refs/remotes/{target}")[0] == 0
    code, out = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    branch = out.strip() if code == 0 and out.strip() else "(detached)"
    code, out = _git(root, "rev-list", "--count", f"HEAD..{target}")
    behind = int(out.strip()) if code == 0 and out.strip().isdigit() else None
    age: float | None = None
    if remote:
        stamps: list[float] = []
        for query in ("--git-dir", "--git-common-dir"):
            code, out = _git(root, "rev-parse", query)
            if code == 0 and out.strip():
                marker = (root / out.strip() / "FETCH_HEAD")
                if marker.is_file():
                    stamps.append(marker.stat().st_mtime)
        if stamps:
            age = max((time.time() - max(stamps)) / 3600.0, 0.0)
    return Freshness(target, remote, branch, behind, age)


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments, or ``None`` for ``sys.argv[1:]``.

    Returns:
        0 normally; 1 when ``--max-fetch-age`` is exceeded (or the repository was never fetched),
        or ``--show`` names a path absent from the ref; 2 when the default ref cannot be resolved.

    Raises:
        Nothing. Refusals are printed and returned as exit status 2.
    """
    parser = argparse.ArgumentParser(
        prog="python -m agentteams.ref_branch",
        description="Name a repository's default-branch ref, how stale it may be, and read files from it.")
    parser.add_argument("repo", nargs="?", default=".", help="repository directory (default: .)")
    parser.add_argument("--show", metavar="PATH", help="print PATH as it is on the default-branch ref")
    parser.add_argument("--json", action="store_true", help="print the freshness report as JSON")
    parser.add_argument("--max-fetch-age", type=float, metavar="HOURS",
                        help="exit 1 when the last fetch is older than this, or there never was one")
    args = parser.parse_args(argv)
    try:
        fresh = freshness(args.repo)
        if args.show:
            text = show(args.repo, args.show, ref=fresh.ref)
            print(f"# {args.show} at {fresh.describe()}", file=sys.stderr)
            if text is None:
                print(f"Error: {args.show} is not a file at {fresh.ref}", file=sys.stderr)
                return 1
            sys.stdout.write(text)
            return 0
    except RefBranchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(dataclasses.asdict(fresh), indent=2) if args.json else fresh.describe())
    if args.max_fetch_age is not None and fresh.remote and (
            fresh.fetch_age_hours is None or fresh.fetch_age_hours > args.max_fetch_age):
        print(f"STALE: the last fetch is older than {args.max_fetch_age:g} h; run 'git fetch' and re-check",
              file=sys.stderr)
        return 1
    return 0


__all__ = ["Freshness", "GIT_TIMEOUT", "RefBranchError", "changed_since", "default_ref", "freshness", "main", "show"]

if __name__ == "__main__":
    sys.exit(main())
