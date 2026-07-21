"""session_scan.py — deterministic repo at-large issue scan (orchestrator Workflow 11 Part B).

Consolidates the three still-manual issue sources Part B step 1 describes in prose — CHANGELOG.md
"Known Issues", tmp/**/*.steps.csv pending/blocked rows, git status --short anomalies — into one
function returning structured findings, instead of three independently hand-run greps. The fourth
source, {CONFLICT_LOG_PATH}, is intentionally NOT covered here: orchestrator.template.md step 2
already routes it through @conflict-resolution's ACCEPT/REJECT/REVISE decision, a judgment call
this module doesn't attempt to replace.

Also exposes ``python -m agentteams.session_scan [repo_root]`` for a shell-only runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from agentteams.plan_steps import read_steps

_KNOWN_ISSUES_HEADING_RE = re.compile(r"^#{1,6}\s*.*Known Issues.*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^#{1,6}\s")
_STRUCK_BULLET_RE = re.compile(r"^-\s*~~")

_PENDING_BLOCKED_STATUSES = frozenset({"pending", "blocked"})


@dataclass
class RepoIssue:
    """One repo at-large issue surfaced by :func:`scan_repo_issues`."""

    source: str  # "changelog" | "steps_csv" | "git_status"
    path: str
    detail: str


def _scan_changelog_known_issues(repo_root: Path) -> list[RepoIssue]:
    """Return live (non-struck-through) bullets under a CHANGELOG.md "Known Issues" heading."""
    changelog = repo_root / "CHANGELOG.md"
    if not changelog.exists():
        return []
    lines = changelog.read_text(encoding="utf-8").splitlines()

    issues: list[RepoIssue] = []
    in_section = False
    for line in lines:
        if _KNOWN_ISSUES_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section and _HEADING_RE.match(line):
            break
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        if _STRUCK_BULLET_RE.match(stripped):
            continue
        issues.append(RepoIssue(source="changelog", path="CHANGELOG.md", detail=stripped[2:].strip()))
    return issues


def _scan_pending_blocked_steps(
    repo_root: Path, *, exclude_paths: set[Path] | None = None
) -> list[RepoIssue]:
    """Return pending/blocked rows from tmp/by-week/**/*.steps.csv and legacy tmp/*.steps.csv."""
    exclude = {p.resolve() for p in (exclude_paths or set())}
    csv_paths = sorted(
        {*repo_root.glob("tmp/by-week/**/*.steps.csv"), *repo_root.glob("tmp/*.steps.csv")}
    )

    issues: list[RepoIssue] = []
    for csv_path in csv_paths:
        if csv_path.resolve() in exclude:
            continue
        try:
            rows = read_steps(csv_path)
        except (OSError, UnicodeDecodeError) as exc:
            warnings.warn(f"session_scan: skipping unreadable {csv_path}: {exc}", stacklevel=2)
            continue
        rel = str(csv_path.relative_to(repo_root))
        for row in rows:
            status = row.get("status", "").strip().lower()
            if status not in _PENDING_BLOCKED_STATUSES:
                continue
            agent = row.get("agent", "")
            action = row.get("action", "")
            issues.append(RepoIssue(
                source="steps_csv", path=rel,
                detail=f"{agent}: {action} (status={status})",
            ))
    return issues


GitRunner = Callable[[list[str]], subprocess.CompletedProcess]


def _run_git(argv: list[str]) -> subprocess.CompletedProcess:
    """Default runner: shell out to `git`. Tests replace this."""
    return subprocess.run(["git", *argv], capture_output=True, text=True, check=False)


def _scan_git_status(
    repo_root: Path, *, known_output_paths: set[str] | None = None, runner: GitRunner = _run_git
) -> list[RepoIssue]:
    """Return git-status anomalies: untracked tmp/ files, or modified files outside known outputs."""
    known = known_output_paths or set()
    result = runner(["-C", str(repo_root), "status", "--short"])
    if result.returncode != 0:
        return []

    issues: list[RepoIssue] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        path = line[3:].strip().strip('"')
        untracked = code.strip() == "??"
        if untracked and path.startswith("tmp/"):
            issues.append(RepoIssue(source="git_status", path=path, detail=f"untracked ({code.strip()})"))
        elif not untracked and path not in known:
            issues.append(RepoIssue(source="git_status", path=path, detail=f"modified outside known outputs ({code.strip()})"))
    return issues


def scan_repo_issues(
    repo_root: Path,
    *,
    exclude_steps_paths: set[Path] | None = None,
    known_output_paths: set[str] | None = None,
    runner: GitRunner = _run_git,
) -> list[RepoIssue]:
    """Return the repo at-large issues from the three still-manual sources, in source order."""
    return [
        *_scan_changelog_known_issues(repo_root),
        *_scan_pending_blocked_steps(repo_root, exclude_paths=exclude_steps_paths),
        *_scan_git_status(repo_root, known_output_paths=known_output_paths, runner=runner),
    ]


def main(argv: list[str] | None = None) -> int:
    """``python -m agentteams.session_scan [repo_root]`` — print RepoIssue list as JSON."""
    parser = argparse.ArgumentParser(prog="python -m agentteams.session_scan")
    parser.add_argument("repo_root", nargs="?", default=".", help="Repository root (default: .)")
    args = parser.parse_args(argv)

    issues = scan_repo_issues(Path(args.repo_root))
    json.dump([asdict(i) for i in issues], sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RepoIssue", "scan_repo_issues", "GitRunner", "main"]
