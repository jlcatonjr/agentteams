"""source_provenance.py — which agentteams code is running: commit, branch and whether it is dirty.

CA-033 (2026-10-07): a consumer's render silently ran a stale, unrecorded agentteams snapshot and lost every
adopted routing row. Its build log said only ``agentteams_version: 1.0.0rc8``. The version string can't tell
a merged commit from a work-in-progress branch, a pinned install or an old snapshot, so this module reports
the source itself:

* ``checkout``: the package runs from a git work tree (directly, or through an editable install). Records the
  commit, the branch, and ``dirty`` when ``git status --porcelain`` shows anything, untracked files included
  (they follow branch switches and are rendered by an editable install).
* ``vcs-pin``: installed from a VCS URL. The commit comes from ``direct_url.json``.
* ``local-snapshot``: installed from a local directory without ``editable``, a frozen copy with no commit.
* ``package``: anything else (e.g. an index install).

No absolute path is recorded: consumers commit their build logs. Recording only; refusing is the consumer's
policy (operator decision, 2026-10-07). Stdlib only; never raises.
"""

from __future__ import annotations

import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
_UNKNOWN = {"kind": "unknown", "commit": None, "branch": None, "dirty": None}


def _git(repo: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError, ValueError):  # ValueError: undecodable output
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _from_checkout(package_dir: Path) -> dict[str, Any] | None:
    repo = package_dir.parent
    if not (repo / ".git").exists():
        return None
    commit = _git(repo, "rev-parse", "HEAD")
    if not commit or not _COMMIT_RE.match(commit):
        return None
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git(repo, "status", "--porcelain")
    return {"kind": "checkout", "commit": commit, "branch": branch if branch and branch != "HEAD" else None,
            "dirty": None if status is None else bool(status)}


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
    if isinstance(vcs.get("commit_id"), str) and _COMMIT_RE.match(vcs["commit_id"]):
        return {"kind": "vcs-pin", "commit": vcs["commit_id"], "branch": None, "dirty": False}
    dir_info = info.get("dir_info")
    if isinstance(dir_info, dict) and not dir_info.get("editable"):
        return {"kind": "local-snapshot", "commit": None, "branch": None, "dirty": None}
    return {"kind": "package", "commit": None, "branch": None, "dirty": None}


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
        ``{"kind", "commit", "branch", "dirty"}``. ``kind`` is ``checkout``, ``vcs-pin``, ``local-snapshot`` or
        ``package``. ``commit`` and ``branch`` are ``None`` when unknown, and ``dirty`` is ``None`` when it can't
        be determined.

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
    return f"({text})"
