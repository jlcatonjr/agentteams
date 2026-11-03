"""Shared names for the remediation operations: which ones target a repository, and the PR URLs they record.

One definition (CH-05/CH-08) for the executor, its guards, the agents' MCP server (which runs in the agent
container, so this module imports nothing but the standard library), the summary and ``close-superseded``.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Operations whose target is an absolute repository path (the executor guards and the MCP server check it).
REPO_OPS: tuple[str, ...] = ("stage-fix", "stage-major", "stage-image", "stage-cargo", "open-pr", "close-superseded")
#: The ops that stage a fix (``stage-major`` is ``stage-fix`` with semver-major upgrades allowed; ``stage-image``
#: moves an end-of-life official runtime image's Dockerfile ``FROM`` tag, :mod:`.image_fix`; ``stage-cargo`` runs
#: in-major ``cargo update --precise`` fixes on a ``Cargo.lock``, :mod:`.cargo_fix`). One staged fix per repository
#: at a time, whichever op staged it.
STAGE_OPS: tuple[str, ...] = ("stage-fix", "stage-major", "stage-image", "stage-cargo")

#: A PR URL the executor records for ``open-pr``: ``https://HOST/OWNER/REPO/pull/N`` (groups: host, owner, repo, n).
PR_URL = re.compile(r"^https://([A-Za-z0-9.-]+)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)$")


def pr_of(row: dict[str, Any]) -> str:
    """Return the PR URL in a successful ``open-pr`` ledger row's JSON detail, or "" (any other row, or no URL)."""
    if row.get("op") != "open-pr" or row.get("kind") != "done":
        return ""
    try:
        url = json.loads(str(row.get("detail") or "{}")).get("pr", "")
    except (ValueError, AttributeError):   # a detail that is not a JSON object names no PR
        return ""
    return url if isinstance(url, str) and PR_URL.match(url) else ""


def lane_of(row: dict[str, Any]) -> str:
    """Return the PR lane of a successful ``open-pr`` ledger row: ``"image"``, ``"major"`` or ``"deps"``.

    A ``stage-major`` PR has its own lane (vfr-b.B2): it is staged beside the repository's fix PR, so its
    ``close-superseded`` must never close that fix PR, nor a later fix PR the MAJOR one.

    Args:
        row: A ledger row.

    Returns:
        ``"image"`` when the row's JSON detail records ``"image": true``, ``"major"`` when it records
        ``"major": true``, else ``"deps"`` (older rows included).
    """
    try:
        detail = json.loads(str(row.get("detail") or "{}"))
    except ValueError:
        return "deps"
    if not isinstance(detail, dict):
        return "deps"
    return "image" if detail.get("image") is True else "major" if detail.get("major") is True else "deps"


def repo_of(url: str) -> str:
    """Return ``host/owner/repo`` (lower-case) of a PR URL, or ""."""
    m = PR_URL.match(url)
    return f"{m[1]}/{m[2]}/{m[3]}".lower() if m else ""
