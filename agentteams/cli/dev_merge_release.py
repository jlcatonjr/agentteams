"""dev_merge_release.py — the release-mechanics refusals of ``agentteams --merge-pr`` (1.3.2).

``--merge-pr`` merges a pull request into the integration branch without the gate's prompt. The operator's rulings
D1, D3 and D5 (2026-11-26) keep three kinds of merge with the operator, and ``dev_merge.pr_problem`` checks paths
only, so it could not see them:

* **Release history.** A forward merge of ``main`` or a ``release/*`` line into the integration branch, or any pull
  request that carries their commits. It is caught by ancestry (a commit in ``base..head`` reachable from ``main``,
  a ``release/*`` branch or a ``v*`` tag) and by content (a commit whose ``git patch-id --stable`` equals a
  release-only commit's: a rebase or cherry-pick). A squash of several release commits has no single matching
  patch-id; the CHANGELOG check below names its usual footprint, a new release heading.
* **Release commits.** A change to the package version (``pyproject.toml``: ``project.version``,
  ``project.dynamic``, ``tool.setuptools.dynamic``, ``build-system``), to ``capability_hints.LAST_RELEASE_TAG``, or a
  new ``## [X.Y.Z…]`` heading in ``CHANGELOG.md``.
* **Pins and enforcement code.** A ``*.lock`` file, a lockfile the base's workflows name, a file that holds
  ``NAME=owner/repo@<40-hex>`` pin lines at the base or the head (so removing or deleting a pin counts), every module
  the base's integrity manifest pins, and ``agentteams/capability_hints.py`` (the merge gate imports it).

Every check reads the remote, not this checkout: the base, the head, ``main``, every ``release/*`` and every ``v*``
tag are fetched into a temporary bare repository, and the fetched base and head must be the SHAs ``gh`` reported.
Any fetch, read or parse error refuses (fail closed). These checks only ever refuse; nothing here clears a merge.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path

#: A git runner: argv (without ``git``) and a working directory, to a completed process.
GitRunner = Callable[[list[str], Path], "subprocess.CompletedProcess[str]"]

#: Most commits ``base..head`` may hold, and most release-only commits compared (beyond either, refuse).
MAX_PR_COMMITS = 2000
MAX_RELEASE_ONLY = 50000
#: Files larger than this are not scanned for pin lines and so refuse if they are changed (fail closed).
MAX_SCAN_BYTES = 2_000_000

MANIFEST_REL = "references/enforcement-integrity.json"
CAPABILITY_HINTS_REL = "agentteams/capability_hints.py"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+[^\]]*)\]", re.MULTILINE)
_LAST_RELEASE_TAG = re.compile(r"^LAST_RELEASE_TAG\s*(?::[^=]*)?=\s*(.+?)\s*$", re.MULTILINE)
_PIN_LINE = re.compile(r"^\s*[A-Za-z0-9_.-]+\s*=\s*[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}\s*$", re.MULTILINE)
_WORKFLOW_LOCK = re.compile(r"""(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.lock)\b""")


class ReleaseCheckError(RuntimeError):
    """A release-mechanics check could not be completed (the caller refuses)."""


def run_git(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``git`` hardened (no fsmonitor, no hooks), with ``gh`` as the credential helper for GitHub URLs.

    Args:
        argv: The ``git`` arguments.
        cwd: The working directory.

    Returns:
        The completed process (never raises on a non-zero exit).
    """
    base = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
    return subprocess.run([*base, *argv], cwd=cwd, capture_output=True, text=True, timeout=600, check=False)


def _ok(git: GitRunner, argv: list[str], cwd: Path) -> str:
    proc = git(argv, cwd)
    if proc.returncode != 0:
        raise ReleaseCheckError(f"`git {' '.join(argv[:2])}` failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


def _show(git: GitRunner, cwd: Path, ref: str, path: str) -> str | None:
    """The text of *path* at *ref*, or None when it is absent there."""
    proc = git(["cat-file", "-e", f"{ref}:{path}"], cwd)
    if proc.returncode != 0:
        return None
    return _ok(git, ["cat-file", "-p", f"{ref}:{path}"], cwd)


def _patch_ids(git: GitRunner, cwd: Path, shas: list[str]) -> set[str]:
    # The commit list goes on stdin (a GitRunner takes no input), so these two calls run git directly, hardened.
    del git
    if not shas:
        return set()
    proc = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "log", "--stdin", "--no-walk=unsorted",
         "-p", "--no-merges", "--no-color", "--no-ext-diff", "--no-textconv", "--format=commit %H"],
        cwd=cwd, input="\n".join(shas) + "\n", capture_output=True, text=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise ReleaseCheckError(f"`git log -p` failed: {proc.stderr.strip()[:300]}")
    pid = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "patch-id", "--stable"],
                         cwd=cwd, input=proc.stdout, capture_output=True, text=True,
                         timeout=600, check=False)
    if pid.returncode != 0:
        raise ReleaseCheckError(f"`git patch-id` failed: {pid.stderr.strip()[:300]}")
    return {line.split()[0] for line in pid.stdout.splitlines() if line.strip()}


def _pyproject_keys(text: str | None) -> dict:
    if text is None:
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseCheckError(f"pyproject.toml does not parse ({exc})") from exc
    project = data.get("project") or {}
    setuptools = ((data.get("tool") or {}).get("setuptools") or {})
    return {"project.version": project.get("version"), "project.dynamic": project.get("dynamic"),
            "tool.setuptools.dynamic": setuptools.get("dynamic"), "build-system": data.get("build-system")}


def version_problem(base_pyproject: str | None, head_pyproject: str | None, base_hints: str | None,
                    head_hints: str | None, base_changelog: str | None, head_changelog: str | None) -> str | None:
    """Why the change is a release commit, or None.

    Args:
        base_pyproject: ``pyproject.toml`` at the base (None when absent).
        head_pyproject: ``pyproject.toml`` at the head (None when absent).
        base_hints: ``agentteams/capability_hints.py`` at the base (None when absent).
        head_hints: The same at the head.
        base_changelog: ``CHANGELOG.md`` at the base (None when absent).
        head_changelog: The same at the head.

    Returns:
        A one-line reason, or None.

    Raises:
        ReleaseCheckError: ``pyproject.toml`` does not parse.
    """
    before, after = _pyproject_keys(base_pyproject), _pyproject_keys(head_pyproject)
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        return f"it changes the package version or how it is built ({', '.join(changed)} in pyproject.toml)"
    tag_before = _LAST_RELEASE_TAG.findall(base_hints or "")
    tag_after = _LAST_RELEASE_TAG.findall(head_hints or "")
    if tag_before != tag_after:
        return "it changes capability_hints.LAST_RELEASE_TAG (a release commit)"
    new = sorted(set(_RELEASE_HEADING.findall(head_changelog or "")) - set(_RELEASE_HEADING.findall(base_changelog or "")))
    if new:
        return f"it adds a CHANGELOG release section ({', '.join(new[:3])}): a release commit or a forward merge"
    return None


def workflow_lockfiles(workflows: list[str]) -> set[str]:
    """Paths ending in ``.lock`` that the base's workflow files name.

    Args:
        workflows: The text of each ``.github/workflows/*`` file at the base.

    Returns:
        The named lockfile paths (leading ``./`` removed).
    """
    found: set[str] = set()
    for text in workflows:
        for match in _WORKFLOW_LOCK.findall(text):
            found.add(match.removeprefix("./"))
    return found


def pinned_modules(manifest_text: str | None) -> set[str]:
    """The ``modules`` the base's integrity manifest pins (empty when the repository has no manifest).

    Args:
        manifest_text: ``references/enforcement-integrity.json`` at the base, or None when absent.

    Returns:
        The pinned paths.

    Raises:
        ReleaseCheckError: The manifest is present but unreadable, or has no ``modules`` object.
    """
    if manifest_text is None:
        return set()
    try:
        modules = json.loads(manifest_text).get("modules")
    except (json.JSONDecodeError, AttributeError) as exc:
        raise ReleaseCheckError(f"{MANIFEST_REL} at the base does not parse") from exc
    if not isinstance(modules, dict) or not modules:
        raise ReleaseCheckError(f"{MANIFEST_REL} at the base has no modules")
    return set(modules)


def release_problem(*, repo_url: str, base_branch: str, head_ref: str, head_sha: str, files: list[str],
                    base_sha: str | None = None, git: GitRunner | None = None) -> str | None:
    """Why the pull request is release mechanics, a pin move or an enforcement-code change, or None.

    Args:
        repo_url: The repository to fetch (``https://github.com/<owner>/<name>.git``).
        base_branch: The base branch (the signed integration branch). Its fetched tip is the ``base..head`` anchor.
        head_ref: The ref that holds the head (``refs/pull/<N>/head``).
        head_sha: The head commit ``gh`` reported; the fetched head must equal it.
        files: Every path the pull request changes.
        base_sha: When given, the fetched base tip must equal it (``pull.base.sha`` can lag the live tip, so the
            verb passes None and relies on ``mergeStateStatus`` and ``--match-head-commit``).
        git: The git runner (default :func:`run_git`; tests pass a local one).

    Returns:
        A one-line reason, or None when every check passes.

    Raises:
        ReleaseCheckError: A check could not be completed (the caller refuses).
    """
    git = git or run_git
    if not _SHA_RE.match(head_sha) or (base_sha is not None and not _SHA_RE.match(base_sha)):
        raise ReleaseCheckError("the base or head commit is unknown")
    with tempfile.TemporaryDirectory(prefix="agentteams-merge-pr-") as tmp:
        d = Path(tmp)
        _ok(git, ["init", "-q", "--bare", str(d)], d)
        _ok(git, ["fetch", "-q", "--no-tags", repo_url, "+refs/heads/main:refs/r/main",
                  "+refs/heads/release/*:refs/r/release/*", "+refs/tags/v*:refs/r/tags/v*",
                  f"+refs/heads/{base_branch}:refs/r/base", f"+{head_ref}:refs/r/head"], d)
        if base_sha is not None and _ok(git, ["rev-parse", "refs/r/base"], d).strip() != base_sha:
            return "its base branch moved while it was being checked"
        if _ok(git, ["rev-parse", "refs/r/head"], d).strip() != head_sha:
            return "its head moved while it was being checked"
        release_refs = [line.strip() for line in _ok(git, ["for-each-ref", "--format=%(refname)", "refs/r/main",
                                                           "refs/r/release", "refs/r/tags"], d).splitlines()
                        if line.strip()]
        if "refs/r/main" not in release_refs:
            raise ReleaseCheckError("main could not be fetched")

        pr_commits = _ok(git, ["rev-list", "refs/r/head", "^refs/r/base"], d).split()
        if len(pr_commits) > MAX_PR_COMMITS:
            return f"it has {len(pr_commits)} commits; at most {MAX_PR_COMMITS} can be checked"
        release_only = _ok(git, ["rev-list", *release_refs, "^refs/r/base"], d).split()
        if len(release_only) > MAX_RELEASE_ONLY:
            raise ReleaseCheckError(f"{len(release_only)} release-only commits; at most {MAX_RELEASE_ONLY}")
        carried = [c for c in pr_commits if c in set(release_only)]
        if carried:
            return (f"it carries {len(carried)} commit(s) from main, a release line or a release tag "
                    f"(e.g. {carried[0][:12]}): a forward merge or a backport, which the operator merges")
        if set(_patch_ids(git, d, pr_commits)) & set(_patch_ids(git, d, release_only)):
            return "a commit in it has the same patch as a release-only commit (a rebased or cherry-picked release)"

        why = version_problem(_show(git, d, "refs/r/base", "pyproject.toml"), _show(git, d, "refs/r/head", "pyproject.toml"),
                              _show(git, d, "refs/r/base", CAPABILITY_HINTS_REL), _show(git, d, "refs/r/head", CAPABILITY_HINTS_REL),
                              _show(git, d, "refs/r/base", "CHANGELOG.md"), _show(git, d, "refs/r/head", "CHANGELOG.md"))
        if why:
            return why

        pinned = pinned_modules(_show(git, d, "refs/r/base", MANIFEST_REL))
        wf_names = _ok(git, ["ls-tree", "--name-only", "refs/r/base", ".github/workflows/"], d).split()
        locks = workflow_lockfiles([_show(git, d, "refs/r/base", n) or "" for n in wf_names])
        touched: list[str] = []
        for path in files:
            p = path.strip()
            while p.startswith("./"):
                p = p[2:]
            if p in pinned or p == CAPABILITY_HINTS_REL or p.lower().endswith(".lock") or p in locks:
                touched.append(p)
                continue
            # Pin lines on EITHER side: adding, editing, removing or deleting a pin is a pin move (D3).
            for ref in ("refs/r/head", "refs/r/base"):
                size = git(["cat-file", "-s", f"{ref}:{p}"], d)
                if size.returncode != 0:
                    continue   # absent on this side (added or deleted)
                if int(size.stdout.strip() or "0") > MAX_SCAN_BYTES or _PIN_LINE.search(_show(git, d, ref, p) or ""):
                    touched.append(p)
                    break
        if touched:
            return (f"it changes a pinned enforcement module, a lockfile or a pin file ({', '.join(sorted(touched)[:5])}),"
                    " which the operator merges")
    return None


__all__ = [
    "MAX_PR_COMMITS",
    "MAX_RELEASE_ONLY",
    "GitRunner",
    "ReleaseCheckError",
    "pinned_modules",
    "release_problem",
    "run_git",
    "version_problem",
    "workflow_lockfiles",
]
