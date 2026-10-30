"""Checkout freshness for the daily summary: how far each scanned repository is behind ``origin/HEAD``.

Read-only and offline (the remote-tracking refs as of the last fetch). It also labels the package findings
already fixed upstream (``Finding.fixed_upstream``) and renders the summary's ``## Checkout freshness`` section.
Split from :mod:`.daily`, which collects the rows during a run (``daily._FRESH``) and re-exports these names;
git runs through ``daily._git`` and clones are classified by ``daily.foreign_clone``, both looked up at call time.
"""

from __future__ import annotations

import datetime as _dt
import tempfile
from pathlib import Path
from typing import Any

from agentteams.vulnscan.manifests import parse
from agentteams.vulnscan.report import Report

_FRESH_GIT_TIMEOUT = 60   # seconds for each read-only git query of the freshness check


def _freshness(root: Path, rep: Report) -> list[dict[str, Any]]:
    """Label the root's package findings already fixed upstream, and return each repository's checkout freshness.

    Read-only and offline: the local remote-tracking refs as of the last fetch (no fetch, no credentials: the
    hardened :func:`daily._git`, ``git show`` for file content). For each repository with package findings it returns
    ``{"repo", "branch", "behind", "fetch_age_h", "fixed_upstream"}``: ``behind`` is the commits ``HEAD`` lacks
    from ``origin/HEAD`` (None without ``origin/HEAD``), ``fetch_age_h`` the age of ``FETCH_HEAD`` (None: never
    fetched). A finding gets ``fixed_upstream`` when the checkout is behind and every file it was found in still
    exists at ``origin/HEAD`` but no longer holds that ``name==version`` (a file gone upstream proves nothing).
    Repositories nested in another inside the scan root, and third-party clones (:func:`daily.foreign_clone`), are
    skipped; a git query that fails
    leaves that repository unlabelled, never an error.

    Args:
        root: The resolved scan root.
        rep: The root's report (targets relative to ``root``).

    Returns:
        One row per repository checked, ``repo`` relative to ``root``.
    """
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    rows = []
    for rel in sorted({f.target for f in rep.findings if f.kind == "package"}):
        repo = root if rel == "." else root / rel
        try:
            row = _repo_freshness(repo, [f for f in rep.findings if f.kind == "package" and f.target == rel], root)
        except daily._ITEM_ERRORS:   # a hung or broken git: no label, no error
            row = None
        if row is not None:
            rows.append({"repo": rel, **row})
    return rows


def _repo_freshness(repo: Path, findings: list[Any], root: Path) -> dict[str, Any] | None:
    """One repository's freshness row, labelling its ``findings`` (see :func:`_freshness`); None when skipped."""
    from agentteams.vulnscan import daily   # lazy, and read at call time: tests patch daily._git / foreign_clone

    nested = any((p / ".git").exists() for p in repo.parents if p == root or root in p.parents)
    if not (repo / ".git").exists() or nested or daily.foreign_clone(repo):
        return None

    def git(*args: str) -> str:
        proc = daily._git(repo, *args, timeout=_FRESH_GIT_TIMEOUT)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    branch = git("symbolic-ref", "--short", "-q", "HEAD") or "(detached)"
    head = git("symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
    count = git("rev-list", "--count", f"HEAD..{head}") if head else ""
    behind = int(count) if count.isdigit() else None
    git_dir = git("rev-parse", "--git-dir")
    fetch_head = (repo / git_dir / "FETCH_HEAD") if git_dir else None
    age = None
    if fetch_head is not None and fetch_head.is_file():
        age = max((_dt.datetime.now().timestamp() - fetch_head.stat().st_mtime) / 3600, 0.0)
    labelled = 0
    if behind:
        upstream: dict[str, set[tuple[str, str, str]] | None] = {}
        for f in findings:
            if not f.locations:
                continue
            gone = True
            for loc in f.locations:
                if loc not in upstream:
                    upstream[loc] = _deps_at(repo, head, (root / loc).relative_to(repo).as_posix())
                deps = upstream[loc]
                if deps is None or (f.ecosystem, f.name.lower(), f.version) in deps:
                    gone = False
                    break
            if gone:
                f.fixed_upstream = True
                labelled += 1
    return {"branch": branch, "behind": behind, "fetch_age_h": age, "fixed_upstream": labelled}


def _deps_at(repo: Path, ref: str, rel: str) -> set[tuple[str, str, str]] | None:
    """Return the ``(ecosystem, name, version)`` the file ``rel`` pins at ``ref`` (``git show``), or None when absent."""
    from agentteams.vulnscan import daily   # lazy: daily imports this module

    proc = daily._git(repo, "show", f"{ref}:{rel}", timeout=_FRESH_GIT_TIMEOUT)
    if proc.returncode:
        return None
    with tempfile.TemporaryDirectory(prefix="vulnscan-upstream-") as tmp:
        path = Path(tmp) / Path(rel).name   # the same file name: parse picks the format by it
        path.write_text(proc.stdout, encoding="utf-8")
        m = parse(path)
    if m.unscannable:
        return None
    return {(d.ecosystem, d.name.lower(), d.version) for d in m.deps}


def _freshness_section(fresh: list[dict[str, Any]]) -> str:
    """Return the summary's ``## Checkout freshness`` section (after the per-repository sections), or ""."""
    if not fresh:
        return ""
    lines = ["## Checkout freshness (read-only: as of each repository's last fetch)", "",
             "| Repository | Branch | Behind origin/HEAD | Last fetch | Findings already fixed upstream |",
             "|---|---|---|---|---|"]
    for r in fresh:
        behind = "no origin/HEAD" if r["behind"] is None else str(r["behind"])
        age = "never" if r["fetch_age_h"] is None else f"{r['fetch_age_h']:.0f}h ago"
        lines.append(f"| `{r['repo']}` | `{r['branch']}` | {behind} | {age} | {r['fixed_upstream']} |")
    lines += ["", "A finding noted *fixed upstream (checkout behind)* is gone from that file at `origin/HEAD`: "
              "pull the checkout. It stays in the findings and is not counted as fixed.", ""]
    from agentteams.vulnscan import publish   # lazy, as in effectiveness: publish imports the executor

    return publish.rewrite_paths("\n".join(lines))
