"""source_provenance.py — which agentteams code is running: commit, branch and whether it is dirty.

CA-033 (2026-10-07): a consumer's render silently ran a stale, unrecorded agentteams snapshot and lost every
adopted routing row. Its build log said only ``agentteams_version: 1.0.0rc8``. The version string can't tell
a merged commit from a work-in-progress branch, a pinned install or an old snapshot, so this module reports
the source itself:

* ``checkout``: the package runs from a git work tree (directly, or through an editable install). Records the
  commit, the branch, and ``dirty`` when ``git status --porcelain`` shows anything, untracked files included
  (they follow branch switches and are rendered by an editable install). Also ``on_remote``: whether any
  remote-tracking branch contains the commit (``git branch -r --contains``), so a render from a local-only branch,
  whose pin no CI can fetch, is visible (IM item 29). The check reads the local remote-tracking refs and never
  fetches, so a commit pushed since the last fetch reads as not on a remote until the next fetch.
* ``vcs-pin``: installed from a VCS URL. The commit comes from ``direct_url.json``.
* ``local-snapshot``: installed from a local directory without ``editable``, a frozen copy with no commit.
* ``package``: anything else (e.g. an index install).

No absolute path is recorded: consumers commit their build logs. Recording only; refusing is the consumer's
policy (operator decision, 2026-10-07). :func:`warn_if_unpushed` only warns. Stdlib only; never raises.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentteams.git_exec import run_git

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
_UNKNOWN = {"kind": "unknown", "commit": None, "branch": None, "dirty": None, "on_remote": None}


def _git(repo: Path, *args: str) -> str | None:
    try:
        proc = run_git(repo, *args, hardened=True, timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError):  # ValueError: undecodable output
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _from_checkout(package_dir: Path) -> dict[str, Any] | None:
    repo = package_dir.parent
    if not (repo / ".git").exists():
        return None
    commit = _git(repo, "rev-parse", "HEAD")
    if not commit or not _COMMIT_RE.fullmatch(commit):
        return None
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git(repo, "status", "--porcelain")
    return {"kind": "checkout", "commit": commit, "branch": branch if branch and branch != "HEAD" else None,
            "dirty": None if status is None else bool(status), "on_remote": _on_remote(repo, commit)}


def _on_remote(repo: Path, commit: str) -> bool | None:
    """Whether a remote-tracking branch of *repo* contains *commit*; None when git can't tell."""
    refs = _git(repo, "branch", "-r", "--contains", commit)
    return None if refs is None else bool(refs)


def _from_distribution() -> dict[str, Any]:
    try:
        from importlib.metadata import PackageNotFoundError, distribution

        raw = distribution("agentteams").read_text("direct_url.json")
    except (PackageNotFoundError, OSError, ValueError):
        raw = None
    try:
        info = json.loads(raw) if raw else {}
    except ValueError:
        info = {}
    if not isinstance(info, dict):
        info = {}
    vcs = info.get("vcs_info") if isinstance(info.get("vcs_info"), dict) else {}
    if isinstance(vcs.get("commit_id"), str) and _COMMIT_RE.fullmatch(vcs["commit_id"]):
        return {"kind": "vcs-pin", "commit": vcs["commit_id"], "branch": None, "dirty": False, "on_remote": None}
    dir_info = info.get("dir_info")
    if isinstance(dir_info, dict) and not dir_info.get("editable"):
        return {"kind": "local-snapshot", "commit": None, "branch": None, "dirty": None, "on_remote": None}
    return {"kind": "package", "commit": None, "branch": None, "dirty": None, "on_remote": None}


@lru_cache(maxsize=1)
def _cached() -> dict[str, Any]:
    import agentteams

    try:  # recording must never break a render: any surprise reports "unknown"
        package_dir = Path(agentteams.__file__).resolve().parent if agentteams.__file__ else None
        return (package_dir and _from_checkout(package_dir)) or _from_distribution()
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return dict(_UNKNOWN)  # an explicit "unknown" record, never a silent pass


def source_provenance() -> dict[str, Any]:
    """Report which agentteams code is running.

    Returns:
        ``{"kind", "commit", "branch", "dirty", "on_remote"}``. ``kind`` is ``checkout``, ``vcs-pin``,
        ``local-snapshot`` or ``package``. ``commit`` and ``branch`` are ``None`` when unknown, and ``dirty`` is
        ``None`` when it can't be determined. ``on_remote`` is set for a ``checkout`` only (``None`` otherwise, or
        when git can't tell).

    Raises:
        Nothing.
    """
    return dict(_cached())  # a copy: callers must never mutate the cached record


def describe(prov: dict[str, Any] | None = None) -> str:
    """One-line summary for ``--version``: ``(checkout 729a0add38f1 main, dirty)``.

    Args:
        prov: A :func:`source_provenance` result; computed when omitted.

    Returns:
        The summary, in parentheses.

    Raises:
        Nothing.
    """
    p = prov if prov is not None else source_provenance()
    parts = [p.get("kind") or "unknown"]
    if p.get("commit"):
        parts.append(str(p["commit"])[:12])
    if p.get("branch"):
        parts.append(str(p["branch"]))
    text = " ".join(parts)
    if p.get("dirty"):
        text += ", dirty"
    if p.get("on_remote") is False:
        text += ", not on a remote"
    return f"({text})"


def unpushed_line(prov: dict[str, Any]) -> str | None:
    """The warning for a ``checkout`` render whose commit no remote-tracking branch contains, else None (IM item 29).

    Args:
        prov: A :func:`source_provenance` result, or a build log's recorded ``agentteams_source``.

    Returns:
        One line naming the commit and branch, or None when the source is fetchable or unknown.

    Raises:
        Nothing.
    """
    if prov.get("kind") != "checkout" or prov.get("on_remote") is not False:
        return None
    raw = prov.get("commit")
    # A recorded commit comes from a consumer file: only a hex id is printed (@security: no raw bytes to a terminal).
    commit = raw[:12] if isinstance(raw, str) and _COMMIT_RE.fullmatch(raw) else "<invalid commit>"
    branch = prov.get("branch")
    on = f" on local branch {branch!r}" if branch else ""
    return (f"agentteams commit {commit}{on} is in no local remote-tracking ref, so a CI that pins it may not be able "
            f"to fetch it. Push it or render from a pushed commit; if it was pushed since, fetch first. A squash-merged "
            f"branch that was deleted leaves its commits unfetchable too")


def warn_if_unpushed(prov: dict[str, Any]) -> dict[str, Any]:
    """Print :func:`unpushed_line` to stderr when it applies, and return *prov* unchanged (for the build log).

    Args:
        prov: A :func:`source_provenance` result.

    Returns:
        *prov*.

    Raises:
        Nothing.
    """
    if line := unpushed_line(prov):
        print(f"[WARN] {line}.", file=sys.stderr)
    return prov


def commit_on_remote(commit: str) -> bool | None:
    """Whether the running checkout's remote-tracking branches contain *commit* (for logs recorded before IM item 29).

    Args:
        commit: A recorded commit id.

    Returns:
        True or False when the running agentteams is a checkout that knows the commit; None otherwise (not a
        checkout, the commit is unknown here, or git can't tell).

    Raises:
        Nothing.
    """
    repo = _checkout_repo() if _COMMIT_RE.fullmatch(commit or "") else None
    if repo is None or _git(repo, "cat-file", "-e", f"{commit}^{{commit}}") is None:
        return None
    return _on_remote(repo, commit)


def _checkout_repo() -> Path | None:
    """The git work tree the running agentteams package lives in, or None (an installed package, or an OS error)."""
    import agentteams

    try:
        repo = Path(agentteams.__file__).resolve().parent.parent if agentteams.__file__ else None
        return repo if repo is not None and (repo / ".git").exists() else None
    except OSError:
        return None
