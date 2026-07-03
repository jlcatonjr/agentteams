"""git_hooks.py — commit-triggered refresh of the agent-team topology map.

The team topology graph (``references/pipeline-graph.md``) is regenerated on
every ``agentteams --update``. Between updates it can go stale whenever an agent
file is edited by hand and committed. This module closes that gap with a
``pre-commit`` hook that regenerates the graph from the *staged* agent files and
stages the result into the same commit — so the committed graph is always in
step with the committed agents.

Two public capabilities, exposed on the CLI as ``--refresh-graph`` and
``--install-git-hooks`` (and auto-installed by ``--update`` unless
``--no-git-hooks``):

- :func:`refresh_pipeline_graph` — rebuild ``references/pipeline-graph.md`` from
  the agent files on disk, writing only when the content actually changes. This
  is what the installed hook invokes via ``python -m agentteams.git_hooks``.
- :func:`install_pre_commit_hook` — write (or sentinel-merge) the refresh block
  into the repo's ``pre-commit`` hook, preserving any pre-existing hook body.

Determinism note
----------------
The refresh reads agent files from disk (filesystem order) while the render
pipeline builds the same graph from ``dict(final)`` (render order). These orders
differ, so the graph serialisers in :mod:`agentteams.graph` sort nodes and edges
deterministically (:meth:`TeamGraph.ordered_edges`, sorted adjacency keys). That
guarantee is what lets a disk-built refresh reproduce the pipeline output
byte-for-byte — without it the hook would rewrite the file with meaningless
reorderings on every commit and never agree with ``--update``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agentteams import emit, graph as _graph

# Path within a project of the generated topology map.
GRAPH_REL_PATH = "references/pipeline-graph.md"

# Canonical agent-source directories, in preference order. The first that exists
# and contains ``*.agent.md`` files is mapped.
_AGENT_DIRS = (".github/agents", ".claude/agents")

# Sentinel markers delimiting the agentteams-owned block inside a pre-commit
# hook. A pre-existing hook body outside these markers is preserved verbatim.
_HOOK_BEGIN = "# >>> AGENTTEAMS:pipeline-graph-refresh >>>"
_HOOK_END = "# <<< AGENTTEAMS:pipeline-graph-refresh <<<"

# Recovers the project name the pipeline last wrote, so the refresh preserves it
# rather than re-inferring (which could differ and cause churn).
_H1_RE = re.compile(r"^# (?P<name>.+?) — Agent Team Topology\s*$", re.MULTILINE)


@dataclass
class RefreshResult:
    """Outcome of a :func:`refresh_pipeline_graph` call."""

    changed: bool          # graph content differs from what was on disk
    wrote: bool            # a write actually happened (False in dry-run / no-op)
    graph_path: Path
    agents_dir: Path | None
    agent_count: int
    reason: str = ""       # human-readable note (e.g. "no agent files")


@dataclass
class InstallResult:
    """Outcome of an :func:`install_pre_commit_hook` call."""

    action: str            # "created" | "updated" | "unchanged"
    hook_path: Path
    agentteams_path: str


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def _resolve_agents_dir(repo_root: Path) -> Path | None:
    """Return the first canonical agent-source dir holding ``*.agent.md`` files."""
    for rel in _AGENT_DIRS:
        candidate = repo_root / rel
        if candidate.is_dir() and any(candidate.glob("*.agent.md")):
            return candidate
    return None


def _project_name_for(repo_root: Path, file_map: dict[str, str]) -> str:
    """Recover the project name, preferring the existing graph's H1 for stability.

    Falls back to inference from agent ``name:`` fields when the graph file does
    not yet exist. Preferring the existing heading guarantees the refresh never
    changes the project name spuriously — it only ever updates topology.
    """
    existing = repo_root / GRAPH_REL_PATH
    if existing.is_file():
        m = _H1_RE.search(existing.read_text(encoding="utf-8"))
        if m:
            return m.group("name")
    return _graph.infer_project_name(file_map)


def refresh_pipeline_graph(
    repo_root: Path,
    *,
    agents_dir: Path | None = None,
    dry_run: bool = False,
) -> RefreshResult:
    """Regenerate ``references/pipeline-graph.md`` from agent files on disk.

    Writes only when the freshly-built graph differs from the file on disk, so a
    no-op commit leaves the file (and the git index) untouched. The output is
    fence-normalised through :func:`emit._normalize_generated_content`, i.e. it
    is byte-identical to what ``agentteams --update`` writes for the same agents.

    Args:
        repo_root:  Project root containing ``.github/agents/`` (or
                    ``.claude/agents/``) and ``references/``.
        agents_dir: Explicit agent-source directory; auto-detected when omitted.
        dry_run:    Compute and compare but never write.

    Returns:
        A :class:`RefreshResult` describing what happened.
    """
    repo_root = repo_root.resolve()
    graph_path = repo_root / GRAPH_REL_PATH

    src_dir = agents_dir if agents_dir is not None else _resolve_agents_dir(repo_root)
    if src_dir is None or not src_dir.is_dir():
        return RefreshResult(
            changed=False, wrote=False, graph_path=graph_path,
            agents_dir=src_dir, agent_count=0, reason="no agent files found",
        )

    file_map = _graph.load_from_disk(src_dir)
    if not file_map:
        return RefreshResult(
            changed=False, wrote=False, graph_path=graph_path,
            agents_dir=src_dir, agent_count=0, reason="no agent files found",
        )

    project_name = _project_name_for(repo_root, file_map)
    raw = _graph.generate_graph_document(file_map, project_name=project_name)
    new_content = emit._normalize_generated_content(GRAPH_REL_PATH, raw)

    old_content = graph_path.read_text(encoding="utf-8") if graph_path.is_file() else None
    changed = old_content != new_content

    wrote = False
    if changed and not dry_run:
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(new_content, encoding="utf-8")
        wrote = True

    return RefreshResult(
        changed=changed, wrote=wrote, graph_path=graph_path,
        agents_dir=src_dir, agent_count=len(file_map),
        reason="updated" if changed else "already current",
    )


# ---------------------------------------------------------------------------
# Hook installation
# ---------------------------------------------------------------------------

def _agentteams_import_path() -> str:
    """Return the directory that must be on sys.path for ``import agentteams``.

    Baked into the installed hook as PYTHONPATH so the hook works from any repo
    (including consumer repos that do not vendor agentteams). When agentteams is
    pip-installed this is site-packages and the entry is harmless.
    """
    import agentteams
    return str(Path(agentteams.__file__).resolve().parent.parent)


def _resolve_hooks_dir(repo_root: Path) -> Path:
    """Return the repo's git hooks directory, honouring core.hooksPath/worktrees.

    Uses ``git rev-parse --git-path hooks`` when git is available (the canonical
    resolution that respects ``core.hooksPath``, submodules and linked
    worktrees), falling back to ``<repo>/.git/hooks`` for environments without a
    git binary.
    """
    fallback = (repo_root / ".git" / "hooks").resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        # No usable git binary — fall back to the conventional hooks path
        # (explicit return, not a swallow-and-continue).
        return fallback
    if out.returncode == 0 and out.stdout.strip():
        hp = Path(out.stdout.strip())
        if not hp.is_absolute():
            hp = repo_root / hp
        return hp.resolve()
    return fallback


def _render_hook_block(agentteams_path: str) -> str:
    """Render the sentinel-delimited refresh block for a pre-commit hook.

    The block is non-blocking (``|| true``): a refresh failure never aborts a
    commit. It fires only when a staged file is an agent file, so unrelated
    commits pay no cost.
    """
    return (
        f"{_HOOK_BEGIN}\n"
        "# Auto-installed by `agentteams --install-git-hooks`. Regenerates\n"
        "# references/pipeline-graph.md when agent files are part of the commit\n"
        "# and stages the result. Non-blocking: never fails a commit. Remove this\n"
        "# block (or re-run agentteams --update with --no-git-hooks) to disable.\n"
        'if command -v git >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then\n'
        '    _at_root="$(git rev-parse --show-toplevel 2>/dev/null)"\n'
        '    if [ -n "$_at_root" ] && \\\n'
        '       git diff --cached --name-only --diff-filter=ACMR 2>/dev/null \\\n'
        "         | grep -qE '(^|/)agents/.*\\.agent\\.md$'; then\n"
        f'        PYTHONPATH="{agentteams_path}${{PYTHONPATH:+:$PYTHONPATH}}" \\\n'
        '            python3 -m agentteams.git_hooks --refresh --repo "$_at_root" >/dev/null 2>&1 \\\n'
        '            && git -C "$_at_root" add references/pipeline-graph.md >/dev/null 2>&1 \\\n'
        '            || true\n'
        "    fi\n"
        "fi\n"
        f"{_HOOK_END}\n"
    )


def _merge_hook_content(existing: str | None, block: str) -> str:
    """Insert or replace the agentteams block within an existing pre-commit hook.

    - No existing hook → a fresh ``#!/bin/sh`` script containing the block.
    - Existing hook without our block → the block is appended after the body.
    - Existing hook with our block → the block is replaced in place (idempotent
      re-install, e.g. to update the baked PYTHONPATH).
    """
    if not existing:
        return "#!/bin/sh\n" + block

    if _HOOK_BEGIN in existing and _HOOK_END in existing:
        pattern = re.compile(
            re.escape(_HOOK_BEGIN) + r".*?" + re.escape(_HOOK_END) + r"\n?",
            re.DOTALL,
        )
        return pattern.sub(block, existing, count=1)

    body = existing if existing.endswith("\n") else existing + "\n"
    return body + "\n" + block


def install_pre_commit_hook(
    repo_root: Path,
    *,
    agentteams_path: str | None = None,
    hooks_dir: Path | None = None,
) -> InstallResult:
    """Install (or refresh) the agentteams pipeline-graph pre-commit hook.

    Idempotent: re-running when the block is already present and identical
    returns ``action="unchanged"`` and writes nothing.

    Args:
        repo_root:       Project root (must be a git repository).
        agentteams_path: Directory to bake as the hook's PYTHONPATH; computed
                         from the running agentteams install when omitted.
        hooks_dir:       Explicit hooks directory (mainly for tests); resolved
                         via git when omitted.

    Returns:
        An :class:`InstallResult`.

    Raises:
        FileNotFoundError: if ``repo_root`` is not a git repository.
    """
    repo_root = repo_root.resolve()
    if not (repo_root / ".git").exists():
        raise FileNotFoundError(f"not a git repository: {repo_root}")

    at_path = agentteams_path or _agentteams_import_path()
    target_dir = hooks_dir if hooks_dir is not None else _resolve_hooks_dir(repo_root)
    hook_path = target_dir / "pre-commit"

    block = _render_hook_block(at_path)
    existing = hook_path.read_text(encoding="utf-8") if hook_path.is_file() else None
    merged = _merge_hook_content(existing, block)

    if existing == merged:
        return InstallResult(action="unchanged", hook_path=hook_path, agentteams_path=at_path)

    action = "updated" if existing else "created"
    target_dir.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(merged, encoding="utf-8")
    hook_path.chmod(0o755)
    return InstallResult(action=action, hook_path=hook_path, agentteams_path=at_path)


# ---------------------------------------------------------------------------
# Auto-install wiring (called from the generate/update success path)
# ---------------------------------------------------------------------------

def resolve_repo_root(args, fallback: Path) -> Path:
    """Resolve the git repository root for hook installation.

    Prefers an explicit ``--project`` (the canonical invocation passes
    ``--project . --output .github/agents``, so ``--output`` is NOT the repo
    root). Otherwise walks up from ``fallback`` (the resolved output/project
    path) to the nearest ancestor containing ``.git``.
    """
    if getattr(args, "project", None):
        return Path(args.project).resolve()
    start = fallback.resolve()
    for cand in (start, *start.parents):
        if (cand / ".git").exists():
            return cand
    return start


def maybe_install_git_hooks(args, project_root: Path) -> None:
    """Auto-install the pipeline-graph pre-commit hook after a successful build.

    Default-on (the commit-triggered map refresh is a standard agentteams
    feature); opt out with ``--no-git-hooks``. No-op when the target is not a
    git repository. Non-fatal: a hook-install failure never fails the build.
    Quiet unless the hook was actually created or updated.
    """
    if getattr(args, "no_git_hooks", False):
        return
    repo_root = resolve_repo_root(args, project_root)
    if not (repo_root / ".git").exists():
        return
    try:
        res = install_pre_commit_hook(repo_root)
    except (OSError, FileNotFoundError) as exc:  # never block a build on hooks
        print(f"  !  Git hook install skipped: {exc}", file=sys.stderr)
        return
    if res.action != "unchanged":
        print(f"  ✓  Pipeline-graph pre-commit hook {res.action}: {res.hook_path}")


# ---------------------------------------------------------------------------
# Module entry point (invoked by the installed hook)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m agentteams.git_hooks``.

    ``--refresh --repo <root>`` regenerates the topology map (what the installed
    pre-commit hook calls). Exit code 0 on success, 1 on error. The refresh is
    intentionally quiet unless ``--verbose`` is passed.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agentteams.git_hooks",
        description="Commit-triggered agent-team topology map refresh.",
    )
    parser.add_argument("--refresh", action="store_true",
                        help="Regenerate references/pipeline-graph.md from agent files on disk.")
    parser.add_argument("--repo", metavar="DIR", default=".",
                        help="Repository root (default: current directory).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compare only; never write.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print a one-line result summary.")
    args = parser.parse_args(argv)

    if not args.refresh:
        parser.error("nothing to do: pass --refresh")

    repo_root = Path(args.repo).resolve()
    try:
        result = refresh_pipeline_graph(repo_root, dry_run=args.dry_run)
    except OSError as exc:
        print(f"graph refresh failed: {exc}", file=sys.stderr)
        return 1

    if args.verbose:
        verb = "would update" if (result.changed and args.dry_run) else result.reason
        print(f"pipeline-graph: {verb} ({result.agent_count} agents) -> {result.graph_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
