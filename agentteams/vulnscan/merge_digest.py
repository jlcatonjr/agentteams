"""The daily merge digest: a machine-local script that merges green remediation PRs, one confirmation each.

Nothing in vulnscan merges. Every daily run writes ``merge-digest.sh`` into its run directory (mode 0700,
never published): one block per open remediation PR whose CI checks all passed and that GitHub reports
mergeable. The operator runs it **with their own credentials**, outside every agent session; it is not a
grant use (the grant op ``merge`` stays reserved, refused in v1). Each block asks before merging, and merges
with ``gh pr merge --match-head-commit <sha>``, so a PR that moved after the digest was written is refused by
GitHub rather than merged unseen.

The script is built only from validated fields: ``HOST/OWNER/REPO`` (each part ``[A-Za-z0-9_.-]``), the PR
number (an int) and the 40-hex head SHA, each passed through :func:`shlex.quote`. No PR title, branch name or
other text from GitHub or an agent reaches it.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

FILENAME = "merge-digest.sh"
_REPO = re.compile(r"^[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")   # a run directory's name: its date
_PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}


@dataclass(frozen=True)
class GreenPr:
    """One mergeable remediation PR: validated identifiers only."""

    repo: str      # HOST/OWNER/REPO
    number: int
    head: str      # 40-hex head commit

    def __post_init__(self) -> None:
        if not _REPO.match(self.repo) or not isinstance(self.number, int) or self.number <= 0 \
                or not _SHA.match(self.head):
            raise ValueError("not a valid remediation PR identifier")


def green(repo: str, number: int, data: dict) -> GreenPr | None:
    """Return the PR as a :class:`GreenPr` when it is open, mergeable and every check passed, else None."""
    if str(data.get("state", "")).upper() != "OPEN" or str(data.get("mergeable", "")).upper() != "MERGEABLE":
        return None
    checks = data.get("statusCheckRollup") or []
    if not isinstance(checks, list) or not checks or any(
            not isinstance(c, dict) or str(c.get("conclusion") or c.get("state") or "").upper() not in _PASSING
            for c in checks):   # a check entry that is not an object is not a passing check
        return None
    try:
        return GreenPr(repo, number, str(data.get("headRefOid", "")))
    except ValueError:
        return None


def render_script(prs: list[GreenPr], run_name: str) -> str:
    """Return the digest script text."""
    lines = [
        "#!/usr/bin/env bash",
        f"# vulnscan merge digest for run {run_name if _RUN_NAME.match(run_name) else '(unknown)'}: green "
        "remediation PRs, one confirmation each.",
        "# Runs with YOUR gh credentials. Nothing merges unless you answer y. --match-head-commit makes GitHub",
        "# refuse a PR whose head moved after this file was written. Machine-local; never committed or published.",
        "set -uo pipefail",
        'ask() { local a; read -r -p "$1 [y/N] " a </dev/tty; [[ "$a" == [yY] ]]; }',
        "",
    ]
    if not prs:
        lines.append('echo "No green remediation PRs in this run."')
    for p in prs:
        repo, num, head = shlex.quote(p.repo), str(p.number), shlex.quote(p.head)
        lines += [
            f'if ask {shlex.quote(f"Merge {p.repo}#{p.number} at {p.head[:12]}?")}; then',
            f"  gh pr merge {num} --repo {repo} --merge --match-head-commit {head} || echo 'not merged: {num}'",
            "fi",
        ]
    return "\n".join(lines) + "\n"


def write(run_dir: Path, prs: list[GreenPr]) -> Path | None:
    """Write ``<run_dir>/merge-digest.sh`` (0700); return its path, or None when it could not be written.

    A failed write is reported on stderr and never stops the daily run (the summary still counts the PRs).
    """
    path = run_dir / FILENAME
    tmp = path.with_suffix(".tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o700)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(render_script(prs, run_dir.name))
        os.chmod(tmp, 0o700)
        tmp.replace(path)
    except OSError as exc:
        print(f"vulnscan daily: could not write {FILENAME}: {exc}", file=sys.stderr)
        return None
    return path
