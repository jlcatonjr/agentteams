"""Fleet pre-update snapshots: recorded under a ref, never committed onto the checked-out branch.

Split out of ``agentteams.fleet`` to keep that module under the CH-07 line ceiling.

A snapshot committed onto the checked-out branch (the behaviour before 2026-10-31) left local-only
``chore(fleet): pre-update snapshot`` commits on live default branches, so they diverged from origin
and could no longer fast-forward. A snapshot is now a commit built in a temporary index and kept
under :data:`SNAPSHOT_REF_PREFIX`; the branch, ``HEAD``, the index and the working tree are untouched.
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agentteams.git_exec import run_git

#: Where snapshots live: local refs, never pushed (``clone --mirror`` / ``push --mirror`` would copy them).
SNAPSHOT_REF_PREFIX = "refs/agentteams/fleet-snapshots/"


def agent_paths(ws: Path) -> list[str]:
    """Relative paths of agent infrastructure to snapshot/diff within a workspace.

    Args:
        ws: The workspace.

    Returns:
        The agent-infra paths that exist in *ws*.
    """
    rels = []
    for rel in (
        ".github/agents",
        ".github/copilot-instructions.md",
        ".claude",
        "CLAUDE.md",
        ".goose/recipes",
        ".goosehints",
        ".codex/agents", ".grok/agents",
        "AGENTS.md",
    ):
        if (ws / rel).exists():
            rels.append(rel)
    return rels


def agent_paths_dirty(ws: Path) -> bool:
    """Whether the workspace's agent paths have uncommitted changes (modified or untracked).

    Args:
        ws: The workspace.

    Returns:
        True if they are dirty, or if git cannot tell (a failed status is never "clean").
    """
    paths = agent_paths(ws)
    if not paths:
        return False
    status = run_git(ws, "status", "--porcelain", "--", *paths)
    return status.returncode != 0 or bool(status.stdout.strip())


def worktree_tree(ws: Path, paths: list[str]) -> str | None:
    """The tree of HEAD with *paths* as they are on disk now (untracked files included, ignored ones not).

    Built in a temporary index, so the workspace's own index, HEAD and branch are untouched.

    Args:
        ws: The workspace.
        paths: The paths to take from disk.

    Returns:
        The tree's object id, or None if git cannot build it.
    """
    gitdir = run_git(ws, "rev-parse", "--absolute-git-dir").stdout.strip()
    if not gitdir:
        return None
    idx = Path(gitdir) / f"agentteams-fleet-{uuid.uuid4().hex}.index"
    env = {**os.environ, "GIT_INDEX_FILE": str(idx)}
    try:
        has_head = run_git(ws, "rev-parse", "-q", "--verify", "HEAD^{commit}").returncode == 0
        steps = ([("read-tree", "HEAD")] if has_head else []) + [("add", "-A", "--", *paths)]
        for step in steps:
            if run_git(ws, *step, env=env).returncode != 0:
                return None
        tree = run_git(ws, "write-tree", env=env)
        return tree.stdout.strip() if tree.returncode == 0 else None
    finally:
        idx.unlink(missing_ok=True)


def git_snapshot(ws: Path, *, allow_no_verify: bool = False) -> tuple[str | None, bool]:
    """Record the current agent-infra state as a recoverable snapshot.

    If the agent paths are already clean, the current HEAD is the snapshot. If dirty, a snapshot
    commit of HEAD plus the agent paths as they are on disk is written and kept under
    :data:`SNAPSHOT_REF_PREFIX`. The checked-out branch, HEAD, the index and the working tree are
    never changed, and no commit hook runs (``update-ref`` still fires a ``reference-transaction``
    hook, if one is installed). Content with a high-severity credential finding is not recorded.

    Args:
        ws: The workspace.
        allow_no_verify: No effect any more (the --fleet-allow-no-verify flag): no ``git commit``
            runs. Accepted so existing invocations keep working.

    Returns:
        (snapshot_ref, committed): the snapshot commit and True, or HEAD (None in a repository
        with no commit) and False when clean or when the ref could not be written.
    """
    paths = agent_paths(ws)
    head = run_git(ws, "rev-parse", "-q", "--verify", "HEAD^{commit}").stdout.strip() or None
    if not paths:
        return (head, False)
    # Are any agent paths dirty (modified/untracked)?
    status = run_git(ws, "status", "--porcelain", "--", *paths)
    if status.returncode != 0:
        print(f"    [WARN] snapshot: git status failed in {ws}; falling back to HEAD.", file=sys.stderr)
        return (head, False)
    if not status.stdout.strip():
        return (head, False)
    tree = worktree_tree(ws, paths)
    if tree and (secrets := credential_files(ws, head, tree, paths)):
        # A ref would keep these past gc and carry them in a --mirror clone or push.
        print(f"    [WARN] snapshot: not recorded in {ws}: credential-like content in "
              f"{', '.join(secrets)}; falling back to HEAD. Remove it, then re-run.", file=sys.stderr)
        return (head, False)
    parent = ("-p", head) if head else ()
    commit = run_git(ws, "commit-tree", tree, *parent, "-m", "chore(fleet): pre-update snapshot") if tree else None
    sha = commit.stdout.strip() if commit is not None and commit.returncode == 0 else ""
    name = (f"{SNAPSHOT_REF_PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-"
            f"{hashlib.sha256(str(ws.resolve()).encode()).hexdigest()[:8]}")
    # The empty old value makes update-ref refuse an existing ref: a snapshot never overwrites another.
    if not sha or run_git(ws, "update-ref", name, sha, "").returncode != 0:
        print(f"    [WARN] snapshot: could not record a snapshot ref in {ws}; "
              "falling back to HEAD (dirty state not captured).", file=sys.stderr)
        return (head, False)
    return (sha, True)


def credential_files(ws: Path, head: str | None, tree: str, paths: list[str]) -> list[str]:
    """Agent files the snapshot would add or change that hold a high-severity credential finding.

    A pre-commit secret scanner used to see the snapshot commit; a snapshot ref runs no hook, so the
    fleet runs :func:`agentteams.scan.scan_content` itself (credential category only) on what changed.

    Args:
        ws: The workspace.
        head: HEAD's commit, or None in a repository with no commit.
        tree: The snapshot tree from :func:`worktree_tree`.
        paths: The agent paths.

    Returns:
        The paths whose new content has a high-severity credential finding.
    """
    from agentteams.scan import scan_content   # deferred: scan pulls in more than the fleet needs here

    if head:
        changed = run_git(ws, "diff", "--name-only", "--no-renames", "--diff-filter=AM", head, tree, "--", *paths)
    else:
        changed = run_git(ws, "ls-tree", "-r", "--name-only", tree, "--", *paths)
    out = []
    for rel in changed.stdout.splitlines():
        blob = run_git(ws, "cat-file", "blob", f"{tree}:{rel}", text=False)
        data = blob.stdout if blob.returncode == 0 else b""
        if not data or len(data) > 1_000_000 or b"\0" in data[:8000]:   # unreadable, huge or binary
            continue
        text = data.decode("utf-8", "replace")
        if any(f.category == "credential" and f.severity == "high" for f in scan_content(text, rel)):
            out.append(rel)
    return out


def git_diff(ws: Path, ref: str, paths: list[str], new: str | None = None) -> str:
    """Diff of *paths* from *ref* to *new*.

    Args:
        ws: The workspace.
        ref: The snapshot (diff base).
        paths: The paths to diff.
        new: A tree from :func:`worktree_tree`; None diffs against the working tree.

    Returns:
        The unified diff text ("" when *paths* is empty).
    """
    if not paths:
        return ""
    return run_git(ws, "diff", ref, *((new,) if new else ()), "--", *paths).stdout
