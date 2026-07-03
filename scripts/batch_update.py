#!/usr/bin/env python3
"""
batch_update.py — Run --update --merge on all local agent team repos.

For each repo:
  1. Capture pre-update snapshot (git repos) or note backup path (non-git)
  2. Run build_team.py --description <path> --update --merge --yes
  3. Capture post-update snapshot
  4. Analyse delta: count added/removed lines, flag non-fence deletions
  5. Write per-repo diff to tmp/diffs/<repo-name>-update.diff
  6. Append row to results CSV at tmp/batch-update-all-repos-results.csv

Usage:
    python scripts/batch_update.py --root <path-to-repos-parent>

The --root argument is the directory that CONTAINS the repos to scan
(e.g. ~/githubrepositories). Defaults to the parent of the agentteams repo.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

AGENTTEAMS_DIR = Path(__file__).parent.parent
BUILD_TEAM = AGENTTEAMS_DIR / "build_team.py"
RESULTS_CSV = AGENTTEAMS_DIR / "tmp" / "batch-update-all-repos-results.csv"
DIFFS_DIR = AGENTTEAMS_DIR / "tmp" / "diffs"

# Regex: AGENTTEAMS fence begin/end lines — changes inside these are expected
_FENCE_LINE_RE = re.compile(r"<!--\s*AGENTTEAMS:(BEGIN|END)")
_OLD_DOC_TRIGGER = (
    '**Trigger:** "Update agent docs" / "Project structure changed" / "Repository updated"'
)
_VOLATILE_FILE_SUFFIXES = (
    "references/security-vulnerability-watch.json",
    "references/security-vulnerability-watch.reference.md",
)


def discover_repos(root: Path, exclude_repo_name: str = "agentteams") -> list[dict]:
    """Discover managed repos by _build-description.json presence."""
    repos: dict[str, dict] = {}
    for desc in root.rglob(".github/agents/_build-description.json"):
        repo = desc.parents[2]
        if repo.name == exclude_repo_name:
            continue
        git_path = repo / ".git"
        if git_path.is_file():
            # .git is a file, not a directory — this is a git worktree, not a
            # standalone repo. Worktrees share state with their parent checkout;
            # updating them independently produces duplicate/confusing output.
            continue
        repos[str(repo)] = {
            "path": str(repo),
            "git": git_path.is_dir(),
        }
    return [repos[p] for p in sorted(repos)]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], cwd: str | None = None, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True)


def git_diff_agents(repo_path: str) -> str:
    """Return `git diff` output for agent infra paths in a git repo."""
    r = run(["git", "diff", "--", ".github/agents/", ".github/copilot-instructions.md"], cwd=repo_path)
    return r.stdout


def snapshot_repo_state(repo_path: Path, agents_dir: Path) -> dict[str, str]:
    """Snapshot current text content for agent infra files."""
    files: dict[str, str] = {}

    if agents_dir.exists():
        for path in sorted(agents_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.relative_to(repo_path))
            try:
                files[rel] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                files[rel] = ""

    instructions = repo_path / ".github" / "copilot-instructions.md"
    if instructions.exists():
        rel = str(instructions.relative_to(repo_path))
        try:
            files[rel] = instructions.read_text(encoding="utf-8", errors="replace")
        except OSError:
            files[rel] = ""

    return files


def diff_snapshots(pre: dict[str, str], post: dict[str, str]) -> str:
    """Return unified diff between pre and post repository snapshots."""
    lines: list[str] = []
    for rel in sorted(set(pre) | set(post)):
        before = pre.get(rel, "")
        after = post.get(rel, "")
        if before == after:
            continue
        before_lines = before.splitlines()
        after_lines = after.splitlines()
        diff = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
        lines.extend(diff)
        lines.append("")
    return "\n".join(lines)


def analyse_diff(diff_text: str) -> dict:
    """
    Analyse a unified diff for safety concerns.

    Returns:
        added_lines:     count of + lines (excluding +++ header)
        removed_lines:   count of - lines (excluding --- header)
        files_changed:   list of changed file names
        outside_fence_deletions: list of (filename, line) for - lines that are
                         NOT inside an AGENTTEAMS-fenced block and are
                         substantive (not blank, not separator, not comment)
        volatile_outside_fence_deletions: subset of deletions identified as
                 expected volatile churn (security intel snapshots)
    """
    added = 0
    removed = 0
    files_changed: list[str] = []
    outside_fence_deletions: list[tuple[str, str]] = []
    volatile_outside_fence_deletions: list[tuple[str, str]] = []

    current_file = ""
    in_fence = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            current_file = line.split(" b/")[-1] if " b/" in line else line
            in_fence = False
            continue
        if line.startswith("+++"):
            current_file = line[4:].strip().lstrip("b/")
            continue
        if line.startswith("---"):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
            content = line[1:]  # strip leading '-'
            # Track fence state on removed lines too
            if _FENCE_LINE_RE.search(content):
                in_fence = "END" not in content
            if not in_fence:
                stripped = content.strip()
                # Flag substantial deletions outside fences
                if stripped and not stripped.startswith("#") and len(stripped) > 4:
                    is_volatile = any(
                        current_file.endswith(suffix)
                        for suffix in _VOLATILE_FILE_SUFFIXES
                    )
                    if current_file.endswith("security.agent.md"):
                        if (
                            stripped.startswith("Generated at:")
                            or stripped.startswith("- NVD (NIST):")
                            or stripped.startswith("- `CVE-")
                        ):
                            is_volatile = True
                    if current_file.endswith("orchestrator.agent.md") and _OLD_DOC_TRIGGER in stripped:
                        is_volatile = True

                    if is_volatile:
                        volatile_outside_fence_deletions.append((current_file, content.rstrip()))
                    else:
                        outside_fence_deletions.append((current_file, content.rstrip()))
        # Track fence opens/closes on context and added lines
        elif not line.startswith("-"):
            stripped = line.lstrip("+").strip()
            if _FENCE_LINE_RE.search(stripped):
                in_fence = "END" not in stripped

        if current_file and current_file not in files_changed and (
            line.startswith("+") or line.startswith("-")
        ):
            files_changed.append(current_file)

    return {
        "added_lines": added,
        "removed_lines": removed,
        "files_changed": files_changed,
        "outside_fence_deletions": outside_fence_deletions,
        "volatile_outside_fence_deletions": volatile_outside_fence_deletions,
    }


def find_latest_backup(agents_dir: Path) -> Path | None:
    backup_root = agents_dir / ".agentteams-backups"
    if not backup_root.exists():
        return None
    entries = sorted(
        (d for d in backup_root.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    return entries[0] if entries else None


def diff_backup_vs_current(backup_dir: Path, agents_dir: Path) -> str:
    """For non-git repos: produce a unified diff of backup vs current.

    The backup may include files that live one level above agents_dir
    (e.g. copilot-instructions.md stored as '../copilot-instructions.md'
    relative to agents/).  These are resolved against agents_dir's parent
    and are excluded from the 'DELETED' check since they always exist at
    their canonical path.
    """
    lines: list[str] = []
    for src in sorted(backup_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(backup_dir)
        rel_str = str(rel)
        # Files backed up from outside agents_dir (e.g. ../copilot-instructions.md)
        # are stored without the leading '../' — resolve against agents_dir parent.
        if rel_str.startswith(".."):
            current = agents_dir.parent / rel_str.lstrip("./")
        else:
            current = agents_dir / rel
        if not current.exists():
            lines.append(f"DELETED: {rel}\n")
            continue
        r = run(["diff", "-u", str(src), str(current)])
        if r.stdout:
            lines.append(r.stdout)
    return "".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run --update --merge on all local agent team repos.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=AGENTTEAMS_DIR.parent,
        help=(
            "Directory that contains the repos to scan. "
            "Defaults to the parent of the agentteams repo."
        ),
    )
    args = parser.parse_args()
    repo_root: Path = args.root.resolve()

    DIFFS_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []

    repos = discover_repos(repo_root)
    print(f"Discovered {len(repos)} managed repositories in {repo_root}")

    for repo_info in repos:
        repo_path = Path(repo_info["path"])
        is_git = repo_info["git"]
        name = repo_path.name
        agents_dir = repo_path / ".github" / "agents"
        description = agents_dir / "_build-description.json"

        # Descriptor-selection trap (see memory: collector-management-update):
        # a rich .agentteams/brief.json is the canonical source of truth and
        # MUST override the thin _build-description.json stub. Using the stub
        # silently regresses fenced regions (memory-index doc coverage,
        # selected_archetypes, etc.). When a brief is present, run the
        # canonical invocation: brief descriptor + --no-scan + --project.
        brief = repo_path / ".agentteams" / "brief.json"
        use_brief = brief.exists()
        if use_brief:
            description = brief

        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")

        if not description.exists():
            print(f"  SKIP: no _build-description.json")
            results.append({
                "repo": name, "status": "SKIPPED", "reason": "no build description",
                "files_changed": 0, "added_lines": 0, "removed_lines": 0,
                "outside_fence_deletions": 0, "migration_rows": 0,
                "backup_path": "", "diff_file": "",
            })
            continue

        # ---- Pre-update snapshot ----
        pre_state = snapshot_repo_state(repo_path, agents_dir)

        # ---- Run update ----
        cmd = [
            sys.executable, str(BUILD_TEAM),
            "--description", str(description),
            "--output", str(agents_dir),
            "--update", "--merge", "--yes",
        ]
        if use_brief:
            # Canonical brief invocation: pin the project and disable scan so the
            # brief's explicit fields (memory_index_extra_dirs, selected_archetypes)
            # are the sole source of truth — never --prune (would delete the 14
            # hand-authored specialists in collector-management).
            cmd += ["--project", str(repo_path), "--no-scan"]
        print(f"  Running: {' '.join(cmd[-8:])}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        stdout = result.stdout
        stderr = result.stderr
        print(stdout)
        if stderr:
            print(f"  STDERR: {stderr[:500]}")

        # ---- Post-update snapshot ----
        post_state = snapshot_repo_state(repo_path, agents_dir)
        update_diff = diff_snapshots(pre_state, post_state)

        # Keep backup-vs-current mode for non-git repos when snapshot diff is empty,
        # to preserve legacy diagnostic behavior.
        if not is_git and not update_diff:
            latest_backup = find_latest_backup(agents_dir)
            if latest_backup:
                update_diff = diff_backup_vs_current(latest_backup, agents_dir)

        # ---- Write diff file ----
        diff_file = DIFFS_DIR / f"{name}-update.diff"
        diff_file.write_text(update_diff, encoding="utf-8")

        # ---- Analyse ----
        analysis = analyse_diff(update_diff)
        outside_dels = analysis["outside_fence_deletions"]
        volatile_outside_dels = analysis["volatile_outside_fence_deletions"]

        # Extract migration row count from stdout
        migration_match = re.search(r"Migrated (\d+) changelog row", stdout)
        migration_rows = int(migration_match.group(1)) if migration_match else 0

        # Extract backup path from stdout
        backup_match = re.search(r"Backup created: (.+?) \(", stdout)
        backup_path = backup_match.group(1) if backup_match else ""

        # Determine status
        if result.returncode != 0:
            status = "ERROR"
        elif outside_dels:
            status = "WARN"
        else:
            status = "OK"

        print(f"\n  Result: {status}")
        print(f"  Files changed: {len(analysis['files_changed'])}")
        print(f"  +{analysis['added_lines']} / -{analysis['removed_lines']} lines")
        if outside_dels:
            print(f"  ⚠  {len(outside_dels)} deletion(s) outside AGENTTEAMS fences:")
            for fname, line_content in outside_dels[:5]:
                print(f"     [{fname}]  {line_content[:80]}")
        if volatile_outside_dels:
            print(
                f"  ℹ  {len(volatile_outside_dels)} volatile outside-fence deletion(s) "
                "(security intel churn)"
            )
        print(f"  Diff written to: {diff_file.name}")

        results.append({
            "repo": name,
            "status": status,
            "reason": stderr[:200] if result.returncode != 0 else "",
            "files_changed": len(analysis["files_changed"]),
            "added_lines": analysis["added_lines"],
            "removed_lines": analysis["removed_lines"],
            "outside_fence_deletions": len(outside_dels),
            "volatile_outside_fence_deletions": len(volatile_outside_dels),
            "migration_rows": migration_rows,
            "backup_path": backup_path,
            "diff_file": str(diff_file.name),
        })

    # ---- Write results CSV ----
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "repo", "status", "reason", "files_changed",
            "added_lines", "removed_lines", "outside_fence_deletions",
            "volatile_outside_fence_deletions",
            "migration_rows", "backup_path", "diff_file",
        ])
        writer.writeheader()
        writer.writerows(results)

    print(f"\n{'='*60}")
    print(f"BATCH UPDATE COMPLETE")
    print(f"Results: {RESULTS_CSV}")
    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    err = sum(1 for r in results if r["status"] == "ERROR")
    skip = sum(1 for r in results if r["status"] == "SKIPPED")
    print(f"  OK={ok}  WARN={warn}  ERROR={err}  SKIPPED={skip}")

    return 1 if err > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
