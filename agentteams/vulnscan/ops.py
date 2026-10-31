"""Shared names for the remediation operations: which ones target a repository, and the PR URLs they record.

One definition (CH-05/CH-08) for the executor, its guards, the agents' MCP server (which runs in the agent
container, so this module imports nothing but the standard library), the summary and ``close-superseded``.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Operations whose target is an absolute repository path (the executor guards and the MCP server check it).
REPO_OPS: tuple[str, ...] = ("stage-fix", "stage-major", "open-pr", "close-superseded")
#: The ops that stage a fix (``stage-major`` is ``stage-fix`` with semver-major upgrades allowed).
STAGE_OPS: tuple[str, ...] = ("stage-fix", "stage-major")

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


def repo_of(url: str) -> str:
    """Return ``host/owner/repo`` (lower-case) of a PR URL, or ""."""
    m = PR_URL.match(url)
    return f"{m[1]}/{m[2]}/{m[3]}".lower() if m else ""
