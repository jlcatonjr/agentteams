"""The executor's ``close-superseded`` operation: close older remediation PRs once their replacement is open.

Each ``open-pr`` opens a new ``vulnscan/<date>-<digest>`` PR, because the executor never force-pushes and so
never updates a PR in place. Without this op, an older PR for the same repository stays open beside its
replacement. ``close-superseded REPO`` closes those older PRs, deterministically, under a standing grant
that names the op (``open-pr`` never implies it):

- **The replacement must exist.** It is the PR of the repository's newest successful ``open-pr`` in the
  MAC-chained ledger, and GitHub must report it open, with a ``vulnscan/*`` head in the same repository.
- **Only the executor's own PRs are candidates:** URLs from earlier successful ``open-pr`` rows for the same
  repository, in the same GitHub repository as the replacement (at most :data:`MAX_CANDIDATES`, newest).
- **Untouched by people.** A candidate is closed only when it is open, its head is a ``vulnscan/*`` branch in
  the same repository (not a fork), and it has no reviews, no comments and no commit whose author is not
  the executor's git identity. Anything else is kept and reported with the reason.
- **Close, never merge, delete or push.** ``gh pr close`` with a fixed comment naming the replacement,
  scrubbed by :func:`publish.rewrite_paths`; the branch stays on the remote.

It runs inside the executor's grant use (``authorize`` and :mod:`.executor_guard` have already run) with the
hardened gh environment of ``open-pr``.

The inverse cases live here too: :func:`open_pr_with_same_change` lets ``open-pr`` recognise that the change it
staged is already open under an earlier date (same digest), so it claims that PR instead of opening a duplicate, and
:func:`already_in_base` that it was already merged, so it opens nothing.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentteams.vulnscan import ledger, ops

OP = "close-superseded"
MAX_CANDIDATES = 20
MAX_SAME_CHANGE = 3   # open PRs open-pr examines for the same change under another date (each costs a fetch)
_VIEW_FIELDS = "state,headRefName,isCrossRepository,reviews,comments,commits"


def ledger_prs(repo: str, rows: list[dict[str, Any]]) -> list[str]:
    """Return the distinct PR URLs of ``repo``'s successful ``open-pr`` rows, oldest first (by latest mention)."""
    out: dict[str, None] = {}
    for r in rows:
        url = ops.pr_of(r) if r.get("target") == repo else ""
        if url:
            out.pop(url, None)
            out[url] = None   # re-insert: a re-claimed PR counts at its newest position
    return list(out)


def head_problem(data: dict[str, Any]) -> str:
    """Return why a PR is not an open ``vulnscan/*`` PR from the same repository, or ""."""
    if str(data.get("state", "")).upper() != "OPEN":
        return "not open"
    if not str(data.get("headRefName", "")).startswith("vulnscan/") or data.get("isCrossRepository") is not False:
        return "its head is not a vulnscan/* branch in the same repository"
    return ""


def touched_problem(data: dict[str, Any], identity: tuple[str, str]) -> str:
    """Return why a person has touched a PR (reviews, comments, or a commit by anyone else), or ""."""
    if data.get("reviews"):
        return "it has reviews"
    if data.get("comments"):
        return "it has comments"
    return authored_problem(data, identity)


def authored_problem(data: dict[str, Any], identity: tuple[str, str]) -> str:
    """Return why a PR's commits are not all the executor's (``identity`` as every commit's only author), or ""."""
    commits = data.get("commits")
    if not isinstance(commits, list) or not commits:
        return "its commits could not be read"
    for c in commits:
        authors = c.get("authors") if isinstance(c, dict) else None
        if not isinstance(authors, list) or not authors:
            return "a commit has no readable author"
        if any(not isinstance(a, dict) or (a.get("name"), a.get("email")) != identity for a in authors):
            return "it has a commit not made by the executor"
    return ""


def close_superseded(repo: Path, rows: list[dict[str, Any]] | None = None, *,
                     view: Callable[[str], dict[str, Any]] | None = None,
                     close: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """Close ``repo``'s older executor PRs that its newest ``open-pr`` supersedes.

    Args:
        repo: Canonical repository path (the grant's target).
        rows: Ledger rows (default: read now; the caller holds the ledger lock).
        view: ``url -> dict`` lookup (tests; default ``gh pr view``).
        close: ``(url, comment) -> None`` (tests; default ``gh pr close``).

    Returns:
        ``{"repo", "replacement", "closed": [...], "kept": [{"pr", "reason"}]}``.

    Raises:
        ExecError: No recorded PR, the replacement is not an open ``vulnscan/*`` PR, or a lookup or a close
            failed (PRs closed before the failure stay closed; the ledger row records the failure).
    """
    from agentteams.vulnscan import executor as ex
    from agentteams.vulnscan import publish   # lazy: publish imports the executor

    look, shut = view or _view, close or _close
    prs = ledger_prs(str(repo), ledger.read() if rows is None else rows)
    if not prs:
        raise ex.ExecError("no remediation PR of this repository is recorded; nothing supersedes anything")
    replacement = prs[-1]
    current = look(replacement)
    why = head_problem(current) or authored_problem(current, ex.GIT_IDENTITY)   # the executor's own PR
    if why:
        raise ex.ExecError(f"the newest remediation PR {replacement} is {why}; closing nothing")
    closed: list[str] = []
    kept: list[dict[str, str]] = []
    for url in prs[:-1][-MAX_CANDIDATES:]:
        if ops.repo_of(url) != ops.repo_of(replacement):
            kept.append({"pr": url, "reason": "it is in another repository than the replacement"})
            continue
        data = look(url)
        why = head_problem(data)
        if why == "not open":
            continue   # already merged or closed: nothing to do, nothing to report
        why = why or touched_problem(data, ex.GIT_IDENTITY)
        if not why:
            # Re-check right before closing, to keep the view -> close window as small as gh allows. A review,
            # comment or push landing between this view and the close can still be missed; that gap is
            # accepted because closing is reversible (the PR can be reopened and its branch is kept).
            fresh = look(url)
            why = head_problem(fresh) or touched_problem(fresh, ex.GIT_IDENTITY)
        if why:
            if why != "not open":
                kept.append({"pr": url, "reason": why})
            continue
        shut(url, publish.rewrite_paths(
            f"Superseded by {replacement}, a newer automated security fix for the same repository. Closed by the "
            "agentteams vulnscan executor (close-superseded); the branch is kept and nothing was merged."))
        closed.append(url)
    return {"repo": str(repo), "replacement": replacement, "closed": closed, "kept": kept}


def _gh(*args: str) -> subprocess.CompletedProcess:
    from agentteams.vulnscan import executor as ex

    return subprocess.run(["gh", *args], cwd=ex.gh_cwd(), capture_output=True, text=True, env=ex.gh_push_env(),
                          timeout=120, check=False)


def _view(url: str) -> dict[str, Any]:
    from agentteams.vulnscan import executor as ex

    proc = _gh("pr", "view", url, "--json", _VIEW_FIELDS)
    if proc.returncode:
        raise ex.ExecError(f"gh pr view failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise ex.ExecError(f"gh pr view did not return JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ex.ExecError("gh pr view did not return an object")
    return data


def _close(url: str, comment: str) -> None:
    from agentteams.vulnscan import executor as ex

    proc = _gh("pr", "close", url, "--comment", comment)
    if proc.returncode:
        raise ex.ExecError(f"gh pr close failed: {(proc.stderr or proc.stdout).strip()[:300]}")


def open_pr_with_same_change(wt: Path, target_repo: str, branch: str, base: str) -> tuple[str, str] | None:
    """Return ``(branch, url)`` of an open executor PR in ``target_repo`` carrying the change staged on ``branch``.

    Candidates are the repository's own open PRs (not a fork's) on ``vulnscan/<YYYYMMDD>-<digest>`` with ``branch``'s
    digest under another date, at most :data:`MAX_SAME_CHANGE`. The digest only selects them (it is public and 40 bits
    long); one is claimed only if every commit on it is the executor's (:func:`authored_problem`) and its whole change,
    ``git diff <merge-base with base> <its head>``, equals the staged change ``git diff <base> HEAD`` byte for byte
    (:func:`.executor.change_diff`). Reviews and comments do not stop a claim: they leave the content as it is, and
    the operator scripts comment the orchestrator's verdict on the PR. Best effort: when gh or git fails or times out
    there is no claim, and open-pr then opens a PR as it did before this check existed.

    Args:
        wt: The staged worktree.
        target_repo: ``origin``'s GitHub repository (``host/owner/name``).
        branch: The staged branch, ``vulnscan/<YYYYMMDD>-<digest>``.
        base: The base commit the fix was staged on.

    Returns:
        The matching PR's branch and URL, or None.
    """
    from agentteams.vulnscan import executor as ex

    digest = branch.rsplit("-", 1)[-1]
    same = re.compile(rf"vulnscan/\d{{8}}-{re.escape(digest)}")
    found = _gh_json("pr", "list", "--repo", target_repo, "--state", "open", "--limit", "100",
                     "--json", "url,headRefName,isCrossRepository")
    candidates = [pr for pr in found if isinstance(pr, dict) and isinstance(pr.get("headRefName"), str)
                  and pr["headRefName"] != branch and same.fullmatch(pr["headRefName"])
                  and pr.get("isCrossRepository") is False and str(pr.get("url", "")).startswith("https://")
                  ] if isinstance(found, list) else []
    staged = ex.change_diff(wt, base, "HEAD", check=False)
    for pr in candidates[:MAX_SAME_CHANGE]:
        head, url = pr["headRefName"], pr["url"]
        view = _gh_json("pr", "view", url, "--json", _VIEW_FIELDS)
        if not isinstance(view, dict) or authored_problem(view, ex.GIT_IDENTITY):
            continue
        if ex.git_proc(wt, "fetch", "--no-tags", "origin", f"refs/heads/{head}", push=True).returncode:
            continue
        tip = ex.git(wt, "rev-parse", "FETCH_HEAD", check=False).strip()
        mb = ex.git(wt, "merge-base", tip, base, check=False).strip() if tip else ""
        if mb and staged and ex.change_diff(wt, mb, tip, check=False) == staged:
            return head, url
    return None


def already_in_base(wt: Path, base_branch: str, changed: list[str]) -> bool:
    """Return whether every staged path already has its staged content on origin's ``base_branch`` now.

    True means the change reached the base by another route (an earlier day's PR with the same change was merged), so
    a new PR would carry nothing. Best effort: a failed fetch, or no recorded paths, is False (open-pr goes on).

    Args:
        wt: The staged worktree (``HEAD`` is the staged commit).
        base_branch: The PR base branch.
        changed: The staged paths (the staged record's ``changed``).

    Returns:
        True when the base already holds the staged content of every path.
    """
    from agentteams.vulnscan import executor as ex

    if not changed or ex.git_proc(wt, "fetch", "--no-tags", "origin", f"refs/heads/{base_branch}", push=True).returncode:
        return False
    paths = [f":(literal){p}" for p in changed]   # as stage-fix stages them
    return ex.git_proc(wt, "diff", "--quiet", "FETCH_HEAD", "HEAD", "--", *paths).returncode == 0


def _gh_json(*args: str) -> Any:
    """Return gh's JSON answer, or None when gh fails, times out or answers something else (best-effort lookups)."""
    try:
        proc = _gh(*args)
        data = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        data = None
    return data
