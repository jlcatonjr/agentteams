"""Fleet update — safely run ``--update --merge`` across every agent-infrastructure
workspace under a parent directory (and its subfolders).

Design (see references/plans/fleet-update-integration-2026-06-08.plan.md):

- **Discovery**: walk the parent dir, find workspaces that contain ``.github/agents/``
  and/or ``.claude/`` infrastructure (pruning ``node_modules``, ``.git``,
  ``.worktrees``, ``archive``).
- **Snapshot via git commit**: before writing, commit the workspace's current
  agent-infra state (``chore(fleet): pre-update snapshot``) so the whole update
  is one recoverable point. The snapshot ref is the rollback target and the
  diff base.
- **In-process update**: re-enter ``build_team.main([...])`` per target with
  ``--update --merge`` (copilot direct / claude direct) or ``--bridge-merge``
  (claude bridge consumer). No subprocess ⇒ no exit-code/jsonschema ambiguity;
  per-target exceptions are isolated into a FAIL row.
- **Diff analysis**: after the update, ``git diff <snapshot>`` per workspace,
  classified by the authoritative content signals (shrink Notices,
  USER-EDITABLE-region deletions) — never the process exit code.
- **Safety**: merge-only; ``--overwrite``/``--prune``/``--migrate``/
  ``--bridge-refresh``/``--shrink-policy=allow`` are rejected upstream. ``.claude``
  is only ever bridge-**merged**, never bridge-refreshed.

The default is a **dry-run preview**; pass ``--yes`` to apply (snapshot + update
+ diff).
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

# Directories never descended into when discovering workspaces. ``.github`` and
# ``.claude`` are agent-infra internals — a workspace is detected from its PARENT,
# so we must never recurse into them (else a nested ``.github/agents/.github``
# artifact would be mis-discovered as its own workspace).
_PRUNE_DIRS = {
    "node_modules", ".git", ".agentteams-backups", "__pycache__", ".venv", "venv",
    ".github", ".claude", ".goose", "tmp",
}
_PRUNE_SUBSTR = (".worktrees", "/archive/")

# Fence markers (content fence in .agent.md files).
_FENCE_BEGIN = re.compile(r"AGENTTEAMS[A-Z_-]*:BEGIN")
_FENCE_END = re.compile(r"AGENTTEAMS[A-Z_-]*:END")
_USER_EDIT = re.compile(r"USER-EDITABLE|USER EDITABLE")
_USER_EDIT_END = re.compile(r"END USER-EDITABLE|/USER-EDITABLE|END USER EDITABLE")
_BRIDGE_FENCE = "AGENTTEAMS-BRIDGE:BEGIN"
_SUBAGENT_STUB_SIGNAL = "source_sha256"

# Volatile / fully-generated files whose churn is expected (not a content-loss signal).
_VOLATILE_SUFFIXES = (
    "references/security-vulnerability-watch.json",
    "references/security-vulnerability-watch.reference.md",
    "references/build-log.json",
    "references/framework-watch.json",
    "references/framework-watch.reference.md",
    "references/delivery-receipt.json",
    "references/memory-index.json",
    "references/pipeline-graph.md",
    "security.agent.md",
    "SETUP-REQUIRED.md",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TargetResult:
    workspace: str
    target: str            # "github" | "claude-direct" | "claude-bridge"
    status: str            # "OK" | "REVIEW" | "FAIL" | "SKIP" | "WOULD-UPDATE"
    detail: str = ""
    descriptor: str = ""
    files_changed: int = 0
    added_lines: int = 0
    removed_lines: int = 0
    shrink_notices: list[str] = field(default_factory=list)
    user_editable_deletions: list[str] = field(default_factory=list)
    rc: int | None = None


@dataclass
class WorkspaceResult:
    path: str
    is_git: bool
    snapshot_ref: str | None = None
    snapshot_committed: bool = False
    diff_file: str | None = None
    targets: list[TargetResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def _is_git_repo(path: Path) -> bool:
    return _git(path, "rev-parse", "--is-inside-work-tree").returncode == 0


def _agent_paths(ws: Path) -> list[str]:
    """Relative paths of agent infrastructure to snapshot/diff within a workspace."""
    rels = []
    for rel in (
        ".github/agents",
        ".github/copilot-instructions.md",
        ".claude",
        "CLAUDE.md",
        ".goose/recipes",
        ".goosehints",
        "AGENTS.md",
    ):
        if (ws / rel).exists():
            rels.append(rel)
    return rels


def _git_snapshot(ws: Path) -> tuple[str | None, bool]:
    """Commit the current agent-infra state as a recoverable snapshot.

    Returns (snapshot_ref, committed). If the agent paths are already clean, the
    current HEAD is the snapshot (committed=False). If dirty, a snapshot commit
    is created over just the agent-infra paths (committed=True).
    """
    paths = _agent_paths(ws)
    if not paths:
        head = _git(ws, "rev-parse", "HEAD")
        return (head.stdout.strip() or None, False)
    # Are any agent paths dirty (modified/untracked)?
    status = _git(ws, "status", "--porcelain", "--", *paths)
    if not status.stdout.strip():
        head = _git(ws, "rev-parse", "HEAD")
        return (head.stdout.strip() or None, False)
    # Stage only the agent-infra paths and commit a snapshot (does not touch
    # unrelated working-tree changes elsewhere in the repo).
    _git(ws, "add", "--", *paths)
    commit = _git(
        ws, "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "-m",
        "chore(fleet): pre-update snapshot",
    )
    if commit.returncode != 0:
        # Nothing to commit (e.g. only ignored files) → fall back to HEAD.
        head = _git(ws, "rev-parse", "HEAD")
        return (head.stdout.strip() or None, False)
    head = _git(ws, "rev-parse", "HEAD")
    return (head.stdout.strip() or None, True)


def _git_diff(ws: Path, ref: str, paths: list[str]) -> str:
    if not paths:
        return ""
    return _git(ws, "diff", ref, "--", *paths).stdout


# ---------------------------------------------------------------------------
# Discovery & detection
# ---------------------------------------------------------------------------

def _resolve_descriptor(ws: Path) -> Path | None:
    """Stub-trap fix: prefer the rich brief over the thin stub."""
    for cand in (
        ws / ".agentteams" / "brief.json",
        ws / "brief.json",
        ws / ".github" / "agents" / "_build-description.json",
    ):
        if cand.exists():
            return cand
    return None


def _claude_kind(ws: Path) -> str:
    """Classify a .claude target: 'bridge', 'direct', 'ambiguous', or 'none'."""
    claude = ws / ".claude"
    if not claude.exists():
        return "none"
    # Bridge signals win (HIGH-3): manifest, bridge fence, or subagent stub markers.
    bridges = ws / "references" / "bridges"
    if bridges.exists() and any(bridges.rglob("bridge-manifest.json")):
        return "bridge"
    for entry in (ws / "CLAUDE.md", *(claude.rglob("*.md"))):
        try:
            text = entry.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _BRIDGE_FENCE in text:
            return "bridge"
    agents_dir = claude / "agents"
    if agents_dir.is_dir():
        for f in agents_dir.glob("*.md"):
            try:
                if _SUBAGENT_STUB_SIGNAL in f.read_text(encoding="utf-8", errors="ignore"):
                    return "bridge"
            except OSError:
                continue
        # No bridge signal but real agent files present → direct claude team.
        if any(agents_dir.glob("*.md")):
            return "direct" if _resolve_descriptor(ws) else "ambiguous"
    return "ambiguous"


def _goose_kind(ws: Path) -> str:
    """Classify a .goose target: 'bridge', 'direct', or 'none'."""
    if not (ws / ".goose" / "recipes").is_dir():
        return "none"
    bridges = ws / "references" / "bridges"
    if bridges.exists():
        for mf in bridges.rglob("bridge-manifest.json"):
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(mf.read_text(encoding="utf-8"))
                if data.get("target_framework") == "goose":
                    return "bridge"
    if (ws / ".goose" / "recipes" / "orchestrator.yaml").exists():
        return "direct"
    return "none"


def discover_workspaces(parent: Path, frameworks: str = "both") -> list[Path]:
    """Find every workspace (dir with agent infrastructure) under parent."""
    found: set[Path] = set()
    parent = parent.resolve()
    for dirpath in [parent, *(_walk(parent))]:
        try:
            gh = (dirpath / ".github" / "agents").is_dir()
            cl = (dirpath / ".claude").is_dir()
            gs = (dirpath / ".goose" / "recipes").is_dir()
        except (PermissionError, OSError):
            continue  # unreadable dir (e.g. mode 000) — skip, never fatal
        if frameworks == "github":
            cl = gs = False
        elif frameworks == "claude":
            gh = gs = False
        elif frameworks == "goose":
            gh = cl = False
        elif frameworks == "both":   # legacy: copilot + claude only
            gs = False
        # "all" includes all three frameworks
        if gh or cl or gs:
            found.add(dirpath)
    return sorted(found)


def _walk(parent: Path):
    try:
        children = list(parent.iterdir())
    except (PermissionError, OSError):
        return  # unreadable dir (e.g. mode 000) — skip subtree, never fatal
    for child in children:
        try:
            if not child.is_dir() or child.is_symlink():
                continue
        except (PermissionError, OSError):
            continue
        name = child.name
        full = str(child)
        if name in _PRUNE_DIRS or any(s in full for s in _PRUNE_SUBSTR):
            continue
        yield child
        yield from _walk(child)


# ---------------------------------------------------------------------------
# Content-audit on the diff
# ---------------------------------------------------------------------------

def _fence_and_useredit_lines(text: str) -> tuple[set[int], set[int]]:
    fenced, useredit = set(), set()
    in_f = in_u = False
    for i, ln in enumerate(text.splitlines(), 1):
        if _FENCE_BEGIN.search(ln):
            in_f = True
        if _USER_EDIT.search(ln) and not _USER_EDIT_END.search(ln):
            in_u = True
        if in_f:
            fenced.add(i)
        if in_u:
            useredit.add(i)
        if _FENCE_END.search(ln):
            in_f = False
        if _USER_EDIT_END.search(ln):
            in_u = False
    return fenced, useredit


def _user_editable_deletions(ws: Path, ref: str, rel_file: str) -> list[str]:
    """Deleted lines (vs snapshot) that fall inside a USER-EDITABLE region — the
    only category that represents real hand-authored content loss."""
    head = _git(ws, "show", f"{ref}:{rel_file}").stdout
    if not head:
        return []
    _, useredit = _fence_and_useredit_lines(head)
    if not useredit:
        return []
    diff = _git(ws, "diff", "-U0", ref, "--", rel_file).stdout
    out: list[str] = []
    old_ln = None
    for ln in diff.splitlines():
        m = re.match(r"@@ -(\d+)(?:,\d+)? \+", ln)
        if m:
            old_ln = int(m.group(1))
            continue
        if old_ln is None:
            continue
        if ln.startswith("-") and not ln.startswith("---"):
            content = ln[1:]
            if old_ln in useredit and content.strip():
                out.append(f"{rel_file}:{old_ln}: {content[:100]}")
            old_ln += 1
        elif ln.startswith("+") and not ln.startswith("+++"):
            pass
        else:
            old_ln += 1
    return out


def _classify(ws: Path, ref: str, diff_text: str, update_output: str) -> dict:
    """Derive status from content signals, not exit code."""
    added = removed = 0
    files: list[str] = []
    cur = ""
    for ln in diff_text.splitlines():
        if ln.startswith("+++ b/"):
            cur = ln[6:].strip()
            if cur and cur != "/dev/null" and cur not in files:
                files.append(cur)
        elif ln.startswith("+") and not ln.startswith("+++"):
            added += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            removed += 1
    # Shrink Notices come from the update's own output (emit's shrink guard).
    shrink = [
        l.strip() for l in update_output.splitlines()
        if "Notice:" in l and "fence" in l
    ]
    # USER-EDITABLE deletions across non-volatile changed files.
    ue: list[str] = []
    for f in files:
        if any(f.endswith(suf) for suf in _VOLATILE_SUFFIXES):
            continue
        if f.endswith(".md"):
            ue.extend(_user_editable_deletions(ws, ref, f))
    return {
        "added": added,
        "removed": removed,
        "files": files,
        "shrink_notices": shrink,
        "user_editable_deletions": ue,
    }


# ---------------------------------------------------------------------------
# In-process per-target update
# ---------------------------------------------------------------------------

def _run_main(argv: list[str]) -> tuple[int, str]:
    """Call build_team.main(argv) in-process, capturing stdout+stderr."""
    import build_team  # local import to avoid circular import at module load

    buf = io.StringIO()
    rc = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = build_team.main(argv)
    except SystemExit as exc:  # argparse / validation errors
        rc = int(exc.code) if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception as exc:  # pragma: no cover - defensive per-target isolation
        rc = 1
        buf.write(f"\n[fleet] exception: {type(exc).__name__}: {exc}\n")
    return rc, buf.getvalue()


def _target_argv(target: str, ws: Path, descriptor: Path | None, dry_run: bool,
                 shrink_policy: str) -> list[str] | None:
    if target == "github":
        if descriptor is None:
            return None
        argv = [
            "--description", str(descriptor),
            "--project", str(ws),
            "--output", str(ws / ".github" / "agents"),
            "--framework", "copilot-vscode",
            "--update", "--merge", "--yes",
            "--shrink-policy", shrink_policy,
        ]
        if descriptor.name == "brief.json":
            argv.append("--no-scan")
    elif target == "claude-direct":
        if descriptor is None:
            return None
        argv = [
            "--description", str(descriptor),
            "--output", str(ws / ".claude" / "agents"),
            "--framework", "claude",
            "--update", "--merge", "--yes",
            "--shrink-policy", shrink_policy,
        ]
    elif target == "claude-bridge":
        argv = [
            "--bridge-from", str(ws / ".github" / "agents"),
            "--framework", "claude",
            "--bridge-merge",
            "--output", str(ws),
            "--yes",
        ]
    elif target == "goose-direct":
        if descriptor is None:
            return None
        argv = [
            "--description", str(descriptor),
            "--output", str(ws),       # normalize_output_path appends .goose/recipes
            "--framework", "goose",
            "--update", "--merge", "--yes",
            "--shrink-policy", shrink_policy,
        ]
        if descriptor.name == "brief.json":
            argv.append("--no-scan")
    elif target == "goose-bridge":
        argv = [
            "--bridge-from", str(ws / ".github" / "agents"),
            "--framework", "goose",
            "--bridge-merge",
            "--output", str(ws),
            "--yes",
        ]
    else:
        return None
    if dry_run:
        argv.append("--dry-run")
    return argv


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _plan_targets(ws: Path, frameworks: str) -> list[str]:
    targets: list[str] = []
    if frameworks in ("both", "all", "github") and (ws / ".github" / "agents").is_dir():
        targets.append("github")
    if frameworks in ("both", "all", "claude"):
        kind = _claude_kind(ws)
        if kind == "bridge":
            targets.append("claude-bridge")
        elif kind == "direct":
            targets.append("claude-direct")
        elif kind == "ambiguous":
            targets.append("claude-ambiguous")
    if frameworks in ("all", "goose"):
        kind = _goose_kind(ws)
        if kind == "bridge":
            targets.append("goose-bridge")
        elif kind == "direct":
            targets.append("goose-direct")
    return targets


def run_fleet(args, parser) -> int:
    """Entry point dispatched from build_team.main when --fleet is set."""
    parent = Path(args.fleet).resolve()
    if not parent.is_dir():
        print(f"Error: --fleet target is not a directory: {parent}", file=__import__("sys").stderr)
        return 1

    frameworks = getattr(args, "fleet_frameworks", "both")
    apply = bool(getattr(args, "yes", False)) and not getattr(args, "dry_run", False)
    shrink_policy = getattr(args, "shrink_policy", "preserve") or "preserve"
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_root = Path(getattr(args, "fleet_report", None) or (parent / ".agentteams-fleet")) / run_id

    workspaces = discover_workspaces(parent, frameworks)
    mode = "APPLY" if apply else "DRY-RUN (preview; pass --yes to apply)"
    print(f"\nFleet update — {mode}")
    print(f"  Parent: {parent}")
    print(f"  Workspaces discovered: {len(workspaces)}  |  frameworks: {frameworks}")
    print("=" * 70)

    results: list[WorkspaceResult] = []
    for ws in workspaces:
        is_git = _is_git_repo(ws)
        wr = WorkspaceResult(path=str(ws), is_git=is_git)
        rel = ws.relative_to(parent) if ws != parent else Path(".")
        print(f"\n▸ {rel}  ({'git' if is_git else 'NON-GIT'})")

        descriptor = _resolve_descriptor(ws)
        targets = _plan_targets(ws, frameworks)
        if not targets:
            print("    (no updatable target)")
            results.append(wr)
            continue

        # Snapshot once per workspace before any apply.
        snap_ref = None
        if apply and is_git:
            snap_ref, committed = _git_snapshot(ws)
            wr.snapshot_ref, wr.snapshot_committed = snap_ref, committed
            print(f"    snapshot: {snap_ref[:10] if snap_ref else '—'}"
                  f"{' (commit created)' if committed else ' (HEAD; clean)'}")

        for target in targets:
            if target == "claude-ambiguous":
                wr.targets.append(TargetResult(
                    workspace=str(ws), target="claude", status="SKIP",
                    detail="ambiguous .claude (no bridge signal, no resolvable descriptor) — manual review",
                ))
                print("    claude: SKIP (ambiguous — manual review)")
                continue

            argv = _target_argv(target, ws, descriptor, dry_run=not apply, shrink_policy=shrink_policy)
            if argv is None:
                wr.targets.append(TargetResult(
                    workspace=str(ws), target=target, status="FAIL",
                    detail="no resolvable descriptor", ))
                print(f"    {target}: FAIL (no descriptor)")
                continue

            rc, out = _run_main(argv)
            tr = TargetResult(
                workspace=str(ws), target=target, rc=rc,
                descriptor=str(descriptor) if descriptor else "",
                status="OK",
            )

            if not apply:
                # Dry-run: report intent; no diff (nothing written).
                would = "would update"
                if rc != 0 and "Error" in out:
                    tr.status, tr.detail = "FAIL", _first_error(out)
                else:
                    tr.status, tr.detail = "WOULD-UPDATE", would
                print(f"    {target}: {tr.status}")
                wr.targets.append(tr)
                continue

            # Apply path: classify against the snapshot via git diff.
            if is_git and snap_ref:
                diff_text = _git_diff(ws, snap_ref, _agent_paths(ws))
                cls = _classify(ws, snap_ref, diff_text, out)
                tr.files_changed = len(cls["files"])
                tr.added_lines, tr.removed_lines = cls["added"], cls["removed"]
                tr.shrink_notices = cls["shrink_notices"]
                tr.user_editable_deletions = cls["user_editable_deletions"]
                if rc != 0 and _is_hard_error(out):
                    tr.status, tr.detail = "FAIL", _first_error(out)
                elif cls["user_editable_deletions"] or cls["shrink_notices"]:
                    tr.status = "REVIEW"
                    tr.detail = "shrink notice / USER-EDITABLE deletion — review diff"
                else:
                    tr.status = "OK"
                # Persist the per-workspace diff for human review.
                if diff_text:
                    report_root.mkdir(parents=True, exist_ok=True)
                    df = report_root / f"{_slug(ws, parent)}.diff"
                    df.write_text(diff_text, encoding="utf-8")
                    wr.diff_file = str(df)
            else:
                # Non-git: recovery is the pre-write .agentteams-backups snapshot,
                # created by emit (direct targets) and by run_bridge (bridge
                # targets) before any overwrite/merge of an existing file.
                tr.status = "OK" if rc == 0 else "REVIEW"
                tr.detail = "non-git workspace — recovery via .agentteams-backups (pre-write snapshot)"
            print(f"    {target}: {tr.status}  (+{tr.added_lines}/-{tr.removed_lines}, "
                  f"{len(tr.shrink_notices)} shrink, {len(tr.user_editable_deletions)} UE-del)")
            wr.targets.append(tr)

        results.append(wr)

    return _write_report(results, report_root, parent, apply, frameworks)


def _first_error(out: str) -> str:
    for l in out.splitlines():
        if l.strip().startswith("Error") or "Traceback" in l:
            return l.strip()[:160]
    return "non-zero exit (see captured output)"


def _is_hard_error(out: str) -> bool:
    return any(
        l.strip().startswith("Error") or "Traceback (most recent call last)" in l
        for l in out.splitlines()
    )


def _slug(ws: Path, parent: Path) -> str:
    rel = ws.relative_to(parent) if ws != parent else Path("root")
    return str(rel).replace("/", "__") or "root"


def _write_report(results, report_root: Path, parent: Path, apply: bool, frameworks: str) -> int:
    rows = []
    counts: dict[str, int] = {}
    for wr in results:
        for tr in wr.targets:
            rows.append(asdict(tr))
            counts[tr.status] = counts.get(tr.status, 0) + 1

    report_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "parent": str(parent),
        "frameworks": frameworks,
        "applied": apply,
        "summary": counts,
        "workspaces": [asdict(w) for w in results],
    }
    (report_root / "report.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [f"# Fleet update report ({'apply' if apply else 'dry-run'})", "",
             f"- Parent: `{parent}`", f"- Frameworks: {frameworks}",
             f"- Status counts: {counts}", "",
             "| workspace | target | status | +/- | shrink | UE-del | detail |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {Path(r['workspace']).name} | {r['target']} | {r['status']} | "
            f"+{r['added_lines']}/-{r['removed_lines']} | {len(r['shrink_notices'])} | "
            f"{len(r['user_editable_deletions'])} | {r['detail']} |"
        )
    (report_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"Fleet {'apply' if apply else 'dry-run'} complete: {counts}")
    print(f"Report: {report_root}")
    review = [r for r in rows if r["status"] in ("REVIEW", "FAIL")]
    if review:
        print(f"  ⚠  {len(review)} target(s) need review:")
        for r in review[:10]:
            print(f"     [{r['status']}] {Path(r['workspace']).name}/{r['target']} — {r['detail']}")
    # Non-zero exit only on a real FAIL (so CI can gate); REVIEW is exit 0 with notice.
    return 1 if counts.get("FAIL") else 0
