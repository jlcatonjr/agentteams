"""``fix_mode = "apply-worktree"``: stage a repository's fixes in a fresh git worktree for review.

The daily run calls :func:`stage_worktree_fixes` per repository with fixable findings. Git and npm run in the
tools container (:mod:`.subproc_confine`); the result must pass :func:`executor.validate_diff`. Nothing is
committed, pushed or merged. Split from :mod:`.daily`, which re-exports these names; git runs through
``daily._git``, looked up at call time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agentteams.vulnscan import executor, fix, subproc_confine
from agentteams.vulnscan.manifests import discover

_FETCH_TIMEOUT = 300          # seconds for ``git fetch`` (an unreachable remote must not hang the daily run)


def _reuse_problem(repo: Path, wt: Path, branch: str, head: str) -> tuple[str, str]:
    """Check an existing same-day worktree before staging into it again.

    It must be a registered worktree of ``repo`` on ``branch``, with no commits that are not on
    ``head`` and no change :func:`executor.validate_diff` refuses.

    Returns:
        (problem or "", the worktree's HEAD commit to validate against).
    """
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    if wt.is_symlink():
        return "it is a symbolic link", ""
    entry: dict[str, str] = {}
    for block in daily._git(repo, "worktree", "list", "--porcelain").stdout.split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        if fields.get("worktree") and Path(fields["worktree"]).resolve() == wt.resolve():
            entry = fields
    if not entry:
        return "it is not a worktree of this repository", ""
    if entry.get("branch") != f"refs/heads/{branch}":
        return f"it is not on {branch}", ""
    base = entry.get("HEAD", "")
    if not base or daily._git(repo, "merge-base", "--is-ancestor", base, head).returncode:
        return f"it has commits that are not on {head}", ""
    try:
        executor.validate_diff(wt, base)
    except executor.ExecError as exc:
        return f"it has changes the daily run did not make ({exc})", ""
    return "", base


def _discard_worktree(repo: Path, wt: Path, branch: str) -> None:
    """Remove a staged worktree and its branch (a refused change is never left for review)."""
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    daily._git(repo, "worktree", "remove", "--force", str(wt))
    daily._git(repo, "branch", "-D", branch)


def stage_worktree_fixes(repo: Path, cfg: dict, date: str) -> tuple[str, str]:
    """Stage fixes for ``repo`` (:func:`_stage_worktree_fixes`) with git and npm in the tools container.

    Fail closed: when confinement is on and the tools container is unavailable, nothing is staged and the
    reason is returned (``confine.subprocesses: false`` runs them on the host, as before).

    Args:
        repo: Repository root.
        cfg: Daily config.
        date: Run date (YYYY-MM-DD).

    Returns:
        (worktree path or "", markdown fix result or the reason nothing was staged).

    Raises:
        subprocess.TimeoutExpired: A git step other than ``git fetch`` ran longer than its timeout.
    """
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    try:
        with subproc_confine.scope(cfg, scratch=[repo.parent / f"{repo.name}.worktrees"]):
            return _stage_worktree_fixes(repo, cfg, date)
    except subproc_confine.Unavailable as exc:
        daily._note_infra(exc)
        return "", f"{repo}: nothing staged: {exc}"


def _stage_worktree_fixes(repo: Path, cfg: dict, date: str) -> tuple[str, str]:
    """Create ``<repo>.worktrees/vulnscan-<date>`` off ``origin/HEAD`` and apply fixes there.

    Fix releases are not age-gated (:data:`executor.REMEDIATION_COOLDOWN_DAYS`: Rule S-10's remediation carve-out)
    and the result must pass
    :func:`executor.validate_diff` (only dependency pins and lockfiles change); a refused result is
    removed. A same-day rerun reuses the worktree only after :func:`_reuse_problem` finds nothing.
    Without ``origin/HEAD`` the default branch is unknown and nothing is staged.

    Args:
        repo: Repository root.
        cfg: Daily config.
        date: Run date (YYYY-MM-DD).

    Returns:
        (worktree path or "", markdown fix result or the reason nothing was staged).
    """
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    if not (repo / ".git").exists():
        return "", f"{repo}: not a git repository"
    notes: list[str] = []
    try:
        fetched = daily._git(repo, "fetch", "-q", "origin", timeout=_FETCH_TIMEOUT, push=True)
    except subprocess.TimeoutExpired:
        notes.append(f"`git fetch` timed out after {_FETCH_TIMEOUT}s; staged on the last fetched origin/HEAD")
    else:
        if fetched.returncode:
            notes.append(f"`git fetch` failed ({fetched.stderr.strip()[:200]}); staged on the last fetched origin/HEAD")
    head = daily._git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    if not head:
        return "", (f"{repo}: origin/HEAD is not set, so the default branch is unknown; nothing staged "
                    "(run `git remote set-head origin --auto`)")
    base = daily._git(repo, "rev-parse", "--verify", "--quiet", f"{head}^{{commit}}").stdout.strip()
    if not base:
        return "", f"{repo}: {head} does not resolve to a commit; nothing staged"
    wt = repo.parent / f"{repo.name}.worktrees" / f"vulnscan-{date}"
    branch = f"vulnscan/{date}"
    if wt.exists() or wt.is_symlink():
        problem, base = _reuse_problem(repo, wt, branch, head)
        if problem:
            return "", f"{repo}: not reusing {wt}: {problem}"
    else:
        made = daily._git(repo, "worktree", "add", "-q", "-b", branch, str(wt), base)
        if made.returncode:
            return "", f"{repo}: worktree add failed: {made.stderr.strip()}"
    cooldown = executor.REMEDIATION_COOLDOWN_DAYS   # the config's cooldown_days is ignored (S-10 remediation carve-out)
    found = [m for m in discover(wt) if not daily._operator_excluded(cfg, repo / m.relative_to(wt))]
    res = fix.fix_path(wt, apply=True, allow_major=False,   # unattended: never a major jump
                       cooldown_days=cooldown, manifests=found)
    try:
        executor.validate_diff(wt, base)
    except executor.ExecError as exc:
        _discard_worktree(repo, wt, branch)
        return "", f"{repo}: staged change refused, worktree removed: {exc}\n\n{res.markdown()}"
    return str(wt), res.markdown() + "".join(f"\n- {n}" for n in notes) + ("\n" if notes else "")
