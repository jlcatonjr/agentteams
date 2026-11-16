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
import re
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


def git_snapshot(ws: Path, *, allow_no_verify: bool = False,
                 notes: list[str] | None = None) -> tuple[str | None, bool]:
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
        notes: When given, each credential warning or refusal is appended here for the fleet report row.

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
    blocking, warnings = credential_findings(ws, head, tree, paths) if tree else ([], [])
    sink = notes if notes is not None else []
    if blocking:
        # A ref would keep these past gc and carry them in a --mirror clone or push.
        files = sorted({rel for rel, _, _ in blocking})
        where = "; ".join(f"{rel}:{n} ({msg})" for rel, n, msg in blocking)
        print(f"    [WARN] snapshot: not recorded in {ws}: credential-like content in {', '.join(files)} "
              f"({where}); falling back to HEAD. Remove it, then re-run.", file=sys.stderr)
        sink.append(f"snapshot not recorded: known credential format at {where}")
        return (head, False)
    for rel, n, msg in warnings:
        print(f"    [WARN] snapshot: possible secret ({msg}; not a known credential format) at {rel}:{n}; "
              "recorded anyway. Review it.", file=sys.stderr)
        sink.append(f"snapshot recorded with a possible secret to review: {rel}:{n} ({msg})")
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


#: Scanner findings that judge a possible secret by entropy or a nearby keyword, not by its format. They warn; they do
#: not stop a snapshot (operator ruling, 2026-11-16: the v1.2.0-rc.1 soak SKIPped repos over public fingerprints, slugs
#: and paths).
_UNTYPED_CREDENTIAL_MESSAGES = frozenset({"High-entropy token detected", "Possible secret-like token detected"})

#: Known credential formats that ``agentteams.scan``'s typed rules do not cover. A match stops the snapshot.
_EXTRA_CREDENTIAL_FORMATS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS temporary access key", re.compile(r"\bASIA[0-9A-Z]{16}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
)


Finding = tuple[str, int, str]   # (path, line, scanner message)


def credential_findings(ws: Path, head: str | None, tree: str,
                        paths: list[str]) -> tuple[list[Finding], list[Finding]]:
    """Scan the agent files the snapshot would add or change, split into blocking and warning findings.

    A pre-commit secret scanner used to see the snapshot commit; a snapshot ref runs no hook, so the fleet runs
    :func:`agentteams.scan.scan_content` itself (credential category, high severity) on what changed. A finding of a
    known credential format (a typed scanner rule, or :data:`_EXTRA_CREDENTIAL_FORMATS`) blocks the snapshot; an
    entropy- or keyword-only finding (:data:`_UNTYPED_CREDENTIAL_MESSAGES`) only warns.

    Args:
        ws: The workspace.
        head: HEAD's commit, or None in a repository with no commit.
        tree: The snapshot tree from :func:`worktree_tree`.
        paths: The agent paths.

    Returns:
        (blocking, warnings): ``(path, line, message)`` findings.
    """
    from agentteams.scan import scan_content   # deferred: scan pulls in more than the fleet needs here

    if head:
        changed = run_git(ws, "diff", "--name-only", "--no-renames", "--diff-filter=AM", head, tree, "--", *paths)
    else:
        changed = run_git(ws, "ls-tree", "-r", "--name-only", tree, "--", *paths)
    blocking: list[Finding] = []
    warnings: list[Finding] = []
    for rel in changed.stdout.splitlines():
        blob = run_git(ws, "cat-file", "blob", f"{tree}:{rel}", text=False)
        data = blob.stdout if blob.returncode == 0 else b""
        if not data or len(data) > 1_000_000 or b"\0" in data[:8000]:   # unreadable, huge or binary
            continue
        text = data.decode("utf-8", "replace")
        for f in scan_content(text, rel):
            if f.category == "credential" and f.severity == "high":
                (warnings if f.message in _UNTYPED_CREDENTIAL_MESSAGES else blocking).append((rel, f.line, f.message))
        for n, line in enumerate(text.splitlines(), 1):
            blocking += [(rel, n, f"Possible {name} detected") for name, rx in _EXTRA_CREDENTIAL_FORMATS
                         if rx.search(line)]
    return blocking, warnings


def credential_files(ws: Path, head: str | None, tree: str, paths: list[str]) -> list[str]:
    """Agent files the snapshot would add or change that hold a known-format credential (they block the snapshot).

    Args:
        ws: The workspace.
        head: HEAD's commit, or None in a repository with no commit.
        tree: The snapshot tree from :func:`worktree_tree`.
        paths: The agent paths.

    Returns:
        The paths with a blocking finding (see :func:`credential_findings`).
    """
    blocking, _ = credential_findings(ws, head, tree, paths)
    return sorted({rel for rel, _, _ in blocking})


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
