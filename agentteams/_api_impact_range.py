"""Ranges, batched git reads and the pre-push check for ``agentteams.api_impact status`` (#447 b).

``status`` used to range the default branch from ``@{upstream}``, so a commit pushed before its review left the range
for good, and it looked for each review record at ``HEAD`` rather than at the tip of the range it checked. This module
supplies:

- **A push-independent range.** ``--full`` checks the whole history, or everything after the commit named in the
  ledger's committed ``BASELINE`` file. On the default branch, a committed ``BASELINE`` also replaces ``@{upstream}``
  as the default base.
- **The ledger at the tip.** A commit counts as reviewed when ``<tip>:<ledger>/<patch-id>.json`` exists, listed once
  with ``git ls-tree``.
- **Batched reads.** One ``git log`` for SHAs and subjects, one ``diff-tree --stdin | patch-id --stable`` pipeline for
  every patch-id, and one ``diff-tree --stdin`` for every commit's changed paths. The ids equal
  :func:`agentteams.api_impact.patch_id`'s.
- **The pre-push check**, opt-in through ``git config agentteams.apiReviewPrePush warn|block``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

#: The ledger file that names the commit a full check starts after (first token of its first non-comment line).
BASELINE_FILE = "BASELINE"
#: Git config key for the pre-push check: ``warn`` reports, ``block`` also fails the push (exit 3); unset is off.
PRE_PUSH_KEY = "agentteams.apiReviewPrePush"


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> tuple[int, bytes]:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=False)
    return proc.returncode, proc.stdout


def baseline(repo: Path, ledger_rel: str, tip: str = "HEAD") -> str | None:
    """The commit named in ``<tip>:<ledger_rel>/BASELINE``, when it is an ancestor of ``tip``.

    Args:
        repo: Repository root.
        ledger_rel: The review ledger's repo-relative directory.
        tip: The commit whose tree holds the file.

    Returns:
        The full SHA, or ``None`` when there is no committed baseline, or it does not name an ancestor of ``tip``.

    Raises:
        Nothing.
    """
    rc, raw = _git(repo, "cat-file", "-p", f"{tip}:{ledger_rel}/{BASELINE_FILE}")
    if rc != 0:
        return None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            if line.startswith("-"):  # never an option to git
                return None
            rc, sha = _git(repo, "rev-parse", "-q", "--verify", f"{line.split()[0]}^{{commit}}")
            sha_s = sha.decode().strip()
            if rc == 0 and sha_s and _git(repo, "merge-base", "--is-ancestor", sha_s, tip)[0] == 0:
                return sha_s
            return None
    return None


def range_tip(rev_range: str) -> str:
    """The commit a range ends at, where the review ledger is read.

    ``A..B`` and ``A...B`` end at ``B`` (``HEAD`` when empty; for ``A...B`` that is also where the commits on ``A``'s
    side are judged). A space-separated form (``B ^A``, ``B --not A``) ends at its last plain revision. A single
    revision ends at itself.

    Args:
        rev_range: A revision range as ``status`` accepts it.

    Returns:
        The tip revision.

    Raises:
        Nothing.
    """
    for sep in ("...", ".."):
        if sep in rev_range and " " not in rev_range.strip():
            return rev_range.rsplit(sep, 1)[1] or "HEAD"
    plain: list[str] = []
    for token in rev_range.split():
        if token == "--not":
            break
        if not token.startswith(("^", "-")):
            plain.append(token)
    return plain[-1] if plain else "HEAD"


def commits(repo: Path, rev_args: list[str]) -> list[tuple[str, str]]:
    """``(sha, subject)`` of every non-merge commit selected by ``rev_args``, oldest first, in one ``git log``.

    Args:
        repo: Repository root.
        rev_args: Revision arguments (``["A..B"]``, ``["B", "--not", "--remotes=origin"]``).

    Returns:
        The commits.

    Raises:
        RuntimeError: When git rejects the revisions.
    """
    proc = subprocess.run(["git", "-C", str(repo), "log", "--no-merges", "--reverse", "--format=%H%x1f%s",
                           *rev_args, "--"], capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git log {' '.join(rev_args)}: {proc.stderr.decode(errors='replace').strip()}")
    out: list[tuple[str, str]] = []
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        sha, _, subject = line.partition("\x1f")
        if sha:
            out.append((sha, subject))
    return out


def patch_ids(repo: Path, shas: list[str], ledger_rel: str) -> dict[str, str]:
    """Stable patch-ids of ``shas`` in one pipeline, the review ledger left out of every diff.

    Args:
        repo: Repository root.
        shas: Non-merge commit SHAs.
        ledger_rel: The review ledger's repo-relative directory.

    Returns:
        SHA → patch-id. A commit whose diff is empty once the ledger is excluded is absent (its id is ``""``).

    Raises:
        Nothing.
    """
    if not shas:
        return {}
    _, diff = _git(repo, "diff-tree", "--stdin", "-p", "--root", "--no-ext-diff", "--no-textconv", "--no-color",
                   "--", ".", f":(exclude){ledger_rel}", stdin=("\n".join(shas) + "\n").encode())
    _, out = _git(repo, "patch-id", "--stable", stdin=diff)
    ids: dict[str, str] = {}
    for line in out.decode().splitlines():
        parts = line.split()
        if len(parts) == 2:
            ids[parts[1]] = parts[0]
    return ids


def changed_paths(repo: Path, shas: list[str]) -> dict[str, list[str]]:
    """Paths each commit changed (deletions and both sides of renames), in one ``diff-tree --stdin``.

    Args:
        repo: Repository root.
        shas: Commit SHAs.

    Returns:
        SHA → paths.

    Raises:
        Nothing.
    """
    out: dict[str, list[str]] = {s: [] for s in shas}
    if not shas:
        return out
    _, raw = _git(repo, "diff-tree", "--stdin", "-r", "--root", "--name-only", "-z",
                  stdin=("\n".join(shas) + "\n").encode())
    current: str | None = None
    for field in raw.decode("utf-8", errors="replace").split("\0"):
        if not field:
            continue
        if field in out:
            current = field
        elif current is not None:
            out[current].append(field)
    return out


def ledger_names(repo: Path, tip: str, ledger_rel: str) -> set[str]:
    """File names in ``<tip>:<ledger_rel>``, listed once.

    Args:
        repo: Repository root.
        tip: The commit whose tree is read.
        ledger_rel: The review ledger's repo-relative directory.

    Returns:
        The names (``<patch-id>.json`` and any others); empty when the directory is absent at ``tip``.

    Raises:
        Nothing.
    """
    rc, raw = _git(repo, "ls-tree", "--name-only", "-z", f"{tip}:{ledger_rel}")
    return {n for n in raw.decode("utf-8", errors="replace").split("\0") if n} if rc == 0 else set()


def pre_push_mode(repo: Path) -> str:
    """The pre-push check's mode from git config: ``"warn"``, ``"block"``, or ``"off"`` (unset or anything else).

    Args:
        repo: Repository root.

    Returns:
        The mode.

    Raises:
        Nothing.
    """
    _, raw = _git(repo, "config", "--get", PRE_PUSH_KEY)
    mode = raw.decode().strip().lower()
    return mode if mode in ("warn", "block") else "off"


def pre_push_ranges(repo: Path, stdin_text: str, remote: str) -> list[list[str]]:
    """Revision arguments for each ref update git passes a pre-push hook.

    Args:
        repo: Repository root.
        stdin_text: The hook's standard input, ``<local ref> <local sha> <remote ref> <remote sha>`` per line.
        remote: The remote's name (the hook's first argument).

    Returns:
        One argument list per pushed ref, its first element the pushed tip: ``remote_sha..local_sha`` for an
        update whose old remote tip is in this clone, and ``local_sha --not --remotes=<remote>`` for a new ref or
        an old tip this clone lacks. Deletions, and lines that are not four fields of hex SHAs, are skipped.

    Raises:
        Nothing.
    """
    out: list[list[str]] = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4 or not all(_is_sha(p) for p in (parts[1], parts[3])) or set(parts[1]) == {"0"}:
            continue
        local_sha, remote_sha = parts[1], parts[3]
        known = set(remote_sha) != {"0"} and _git(repo, "cat-file", "-e", f"{remote_sha}^{{commit}}")[0] == 0
        out.append([f"{remote_sha}..{local_sha}"] if known else [local_sha, "--not", f"--remotes={remote}"])
    return out


def _is_sha(text: str) -> bool:
    return len(text) in (40, 64) and all(c in "0123456789abcdef" for c in text)


def pre_push_check(repo: Path, remote: str, stdin_text: str,
                   status_fn: Callable[..., tuple[str | None, list[Any]]]) -> tuple[str, bool]:
    """Run the pre-push step: report impactful commits being pushed without a review record at the pushed tip.

    Off unless ``git config agentteams.apiReviewPrePush`` is ``warn`` or ``block`` (:func:`pre_push_mode`).

    Args:
        repo: Repository root.
        remote: The remote's name (the hook's first argument).
        stdin_text: The hook's standard input (one ``<local ref> <local sha> <remote ref> <remote sha>`` per line).
        status_fn: :func:`agentteams.api_impact.status`, passed in so this module does not import it back.

    Returns:
        ``(message, blocked)``; ``blocked`` only in ``block`` mode with an unreviewed commit.

    Raises:
        RuntimeError: When git fails.
    """
    mode = pre_push_mode(repo)
    if mode == "off":
        return "", False
    pending: list[Any] = []
    for rev_args in pre_push_ranges(repo, stdin_text, remote):
        _, found = status_fn(repo, rev_args=rev_args)
        seen = {p.commit for p in pending}
        pending += [c for c in found if not c.reviewed and c.commit not in seen]
    if not pending:
        return "", False
    lines = [f"agentteams: {len(pending)} impactful commit(s) pushed without an API impact review record:"]
    lines += [f"  {c.commit[:12]} {c.subject} ({c.impact.summary()})" for c in pending[:10]]
    if mode == "block":
        lines.append(f"agentteams: push blocked ({PRE_PUSH_KEY}=block): record the review (python -m "
                     "agentteams.api_impact record --note ...), commit it, and push again; or git push --no-verify")
    return "\n".join(lines), mode == "block"
