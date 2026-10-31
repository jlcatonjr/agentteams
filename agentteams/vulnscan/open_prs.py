"""Never duplicate someone else's open dependency PR (D1).

A repository may already have open PRs that bump the packages a staged fix would bump: Dependabot's, Renovate's,
or a person's. Before ``stage-fix`` / ``stage-major`` stage a change, and again at ``open-pr``, the executor lists
the repository's open PRs (read-only: ``gh pr list --state open --json number,title,headRefName,author``) and
leaves every package such a PR already updates to that PR ("left to #227"). A PR counts when its head branch does
not start with ``vulnscan/`` (the executor's own PRs are handled by ``close-superseded``). It names a package by:

- its title: ``Bump <name> from <a> to <b>``, ``chore(deps): bump <name> ...``, ``Update dependency <name> to
  v<b>`` (Renovate), case-insensitively;
- or its head branch: ``dependabot/<ecosystem>/<path>/<name>-<version>`` or ``renovate/<name>-<version>``.

Names compare in :func:`normalize`'s form (lower case, runs of ``_``, ``-``, ``.`` as one ``-``: PEP 503, which also
leaves npm names comparable). Staging does not wait on gh: a failed listing stages as before (the change is
checked again at ``open-pr``). ``open-pr`` fails closed: no listing, no PR.

Imports only the standard library and :mod:`.executor_env`.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

from agentteams.vulnscan.executor_env import ExecError, gh_cwd, gh_push_env

LIMIT = 100
_NAME = r"(@?[A-Za-z0-9][\w.\-/@]*?)"
_TITLES = (
    re.compile(rf"(?:^|:\s*)bump(?:s|ed)?\s+(?:the\s+)?{_NAME}\s+(?:from|to|requirement|in|and)\b", re.I),
    re.compile(rf"\bupdate\s+(?:dependency|module|package)\s+{_NAME}\s+to\s+v?\d", re.I),
)
_BRANCHES = (
    re.compile(r"^dependabot/[^/]+/(?:.+/)?(@?[A-Za-z0-9][\w.\-]*?)-v?\d[\w.\-]*$"),
    re.compile(r"^renovate/(@?[A-Za-z0-9][\w.\-/]*?)-v?\d[\w.\-]*$"),
)


def normalize(name: str) -> str:
    """Return ``name`` in a comparable form: lower case, each run of ``_``, ``-`` and ``.`` as one ``-``.

    Args:
        name: A package name.

    Returns:
        The normalized name.

    Raises:
        TypeError: ``name`` is not a string.
    """
    if not isinstance(name, str):
        raise TypeError("normalize takes a string")
    return re.sub(r"[-_.]+", "-", name).lower()


def list_open(target_repo: str) -> list[dict[str, Any]]:
    """List ``target_repo``'s open PRs with gh (read-only; the executor's hardened gh environment, empty cwd).

    Args:
        target_repo: ``HOST/OWNER/REPO``.

    Returns:
        The PRs gh returned (dicts; anything else is dropped).

    Raises:
        ExecError: gh failed, timed out or did not answer with a JSON list.
    """
    try:
        proc = subprocess.run(["gh", "pr", "list", "--repo", target_repo, "--state", "open", "--json",
                               "number,title,headRefName,author", "--limit", str(LIMIT)], cwd=gh_cwd(),
                              capture_output=True, text=True, env=gh_push_env(), timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExecError(f"gh pr list (open PRs) failed: {exc}") from exc
    if proc.returncode:
        raise ExecError(f"gh pr list (open PRs) failed: {(proc.stderr or proc.stdout).strip()[:300]}")
    try:
        prs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ExecError(f"gh pr list (open PRs) did not return JSON: {exc}") from exc
    if not isinstance(prs, list):
        raise ExecError("gh pr list (open PRs) did not return a list")
    return [p for p in prs if isinstance(p, dict)]


def packages_of(pr: dict[str, Any]) -> set[str]:
    """Return the normalized package names an open PR updates, read from its title and head branch.

    Args:
        pr: One entry of :func:`list_open`.

    Returns:
        The names (empty for the executor's own ``vulnscan/*`` PRs and for PRs that name no package).
    """
    branch, title = pr.get("headRefName"), pr.get("title")
    branch = branch if isinstance(branch, str) else ""
    if branch.startswith("vulnscan/"):
        return set()
    names = {m.group(1) for rx in _TITLES for m in rx.finditer(title)} if isinstance(title, str) else set()
    names |= {m.group(1).rsplit("/", 1)[-1] if not m.group(1).startswith("@") else m.group(1)
              for rx in _BRANCHES if (m := rx.match(branch))}
    return {normalize(n) for n in names}


def claimed(prs: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Return ``{normalized package: [PR numbers]}`` for every package another open PR already updates.

    Args:
        prs: :func:`list_open`'s result.

    Returns:
        The map (PR numbers ascending).
    """
    out: dict[str, list[int]] = {}
    for pr in prs:
        num = pr.get("number")
        if not isinstance(num, int) or isinstance(num, bool):
            continue
        for name in packages_of(pr):
            out.setdefault(name, [])
            if num not in out[name]:
                out[name].append(num)
    return {k: sorted(v) for k, v in out.items()}


def duplicate_detail(left: dict[str, list[int]]) -> str:
    """Return the refusal for a change every package of which another open PR already updates.

    Args:
        left: ``{package: [PR numbers]}``.

    Returns:
        ``an open PR already updates <names> (#227, #228); not duplicated``.
    """
    nums = sorted({n for v in left.values() for n in v})
    return (f"an open PR already updates {', '.join(sorted(left))} ({', '.join(f'#{n}' for n in nums)}); "
            "not duplicated")


def staging_claims(target_repo: Callable[[], str]) -> dict[str, list[int]]:
    """Return :func:`claimed` for staging: on any failure (gh, or an origin gh cannot target) ``{}``, never a refusal.

    Staging does not wait on gh; ``open-pr`` lists the PRs again and fails closed (:func:`refuse_duplicate`).

    Args:
        target_repo: Returns ``HOST/OWNER/REPO`` (``executor._origin_repo``); it may raise :class:`ExecError`.

    Returns:
        ``{normalized package: [PR numbers]}``.
    """
    try:
        return claimed(list_open(target_repo()))
    except ExecError:   # not blocking: the same check runs again, fail-closed, at open-pr
        return {}


def left_to_prs(changes: list[Any], claims: dict[str, list[int]]) -> dict[str, list[int]]:
    """Return ``{package: [PR numbers]}`` for the rows a fix left to other open PRs (``pr-held``).

    Args:
        changes: :attr:`.fix.FixResult.changes`.
        claims: :func:`claimed`'s map the fix was given.

    Returns:
        The map, by each row's own package name.
    """
    return {c.name: claims.get(normalize(c.name), []) for c in changes if c.action == "pr-held"}


def refuse_duplicate(target_repo: str, packages: list[str]) -> str:
    """Return the refusal when another open PR now updates a package the staged change updates, else "" (open-pr).

    Args:
        target_repo: ``HOST/OWNER/REPO``.
        packages: The package names the staged change updates (staged state ``packages``).

    Returns:
        :func:`duplicate_detail` for the overlap, or "".

    Raises:
        ExecError: gh failed (fail closed: no PR is opened without the check).
    """
    claims = claimed(list_open(target_repo))
    dup = {p: claims[normalize(p)] for p in packages if isinstance(p, str) and normalize(p) in claims}
    return duplicate_detail(dup) if dup else ""
