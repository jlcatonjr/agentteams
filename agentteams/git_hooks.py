"""git_hooks.py — commit-triggered refresh of the agent-team topology map.

The team topology graph (``references/pipeline-graph.md``) is regenerated on
every ``agentteams --update``. Between updates it can go stale whenever an agent
file is edited by hand and committed. This module closes that gap with a
``pre-commit`` hook that, when a commit touches agent files, regenerates the
graph from the **working tree** and stages the result into the same commit — so
the committed graph tracks the committed agents. (Like most format-on-commit
hooks it reflects the working tree, not a partial index; with fully-staged
changes — the common case — the two coincide.)

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

# Paths within a project of the generated maps.
GRAPH_REL_PATH = "references/pipeline-graph.md"          # agent topology
ARCH_REL_PATH = "references/architecture-graph.md"       # module architecture

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
    """Outcome of a map-refresh call (agent topology or module architecture)."""

    changed: bool          # map content differs from what was on disk
    wrote: bool            # a write actually happened (False in dry-run / no-op)
    out_path: Path         # the map file
    source: Path | None    # agents dir / package dir the map was built from
    count: int             # items mapped (agents, or modules)
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
            changed=False, wrote=False, out_path=graph_path,
            source=src_dir, count=0, reason="no agent files found",
        )

    file_map = _graph.load_from_disk(src_dir)
    if not file_map:
        return RefreshResult(
            changed=False, wrote=False, out_path=graph_path,
            source=src_dir, count=0, reason="no agent files found",
        )

    project_name = _project_name_for(repo_root, file_map)
    raw = _graph.generate_graph_document(file_map, project_name=project_name)
    new_content = emit._normalize_generated_content(GRAPH_REL_PATH, raw)

    changed, wrote = _write_if_changed(graph_path, new_content, dry_run=dry_run)
    return RefreshResult(
        changed=changed, wrote=wrote, out_path=graph_path,
        source=src_dir, count=len(file_map),
        reason="updated" if changed else "already current",
    )


def refresh_architecture_graph(
    repo_root: Path,
    *,
    package_dir: Path | None = None,
    dry_run: bool = False,
) -> RefreshResult:
    """Regenerate ``references/architecture-graph.md`` from the repo's package.

    Maps the module import-dependency graph of the repo's primary Python package
    (auto-detected unless ``package_dir`` is given). Like the topology refresh,
    the output is deterministic and written only when it changed. No-op (with a
    ``reason``) when the repo has no importable package.
    """
    from agentteams import architecture as _arch

    repo_root = repo_root.resolve()
    out_path = repo_root / ARCH_REL_PATH
    pkg = package_dir if package_dir is not None else _arch.discover_package_root(repo_root)
    if pkg is None:
        return RefreshResult(
            changed=False, wrote=False, out_path=out_path,
            source=None, count=0, reason="no importable package found",
        )

    graph = _arch.build_architecture(repo_root, pkg)
    if not graph.nodes:
        return RefreshResult(
            changed=False, wrote=False, out_path=out_path,
            source=pkg, count=0, reason="no modules found",
        )

    new_content = emit._normalize_generated_content(ARCH_REL_PATH, graph.to_markdown_document())
    changed, wrote = _write_if_changed(out_path, new_content, dry_run=dry_run)
    return RefreshResult(
        changed=changed, wrote=wrote, out_path=out_path,
        source=pkg, count=len(graph.nodes),
        reason="updated" if changed else "already current",
    )


def _write_if_changed(path: Path, content: str, *, dry_run: bool) -> tuple[bool, bool]:
    """Write ``content`` to ``path`` only when it differs. Returns (changed, wrote)."""
    old = path.read_text(encoding="utf-8") if path.is_file() else None
    changed = old != content
    if changed and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True, True
    return changed, False


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
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--git-path", "hooks"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        # No usable git binary — resolve the hooks dir ourselves (explicit
        # return, not a swallow-and-continue).
        return _hooks_dir_fallback(repo_root)
    if out.returncode == 0 and out.stdout.strip():
        hp = Path(out.stdout.strip())
        if not hp.is_absolute():
            hp = repo_root / hp
        return hp.resolve()
    return _hooks_dir_fallback(repo_root)


def _hooks_dir_fallback(repo_root: Path) -> Path:
    """Resolve ``<gitdir>/hooks`` without git, honouring a ``.git`` *file*.

    In a linked worktree or submodule ``.git`` is a ``gitdir: <path>`` pointer
    file, not a directory, so the naive ``.git/hooks`` is invalid; parse the
    pointer to find the real git dir.
    """
    dot_git = repo_root / ".git"
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text.startswith("gitdir:"):
            gd = Path(text[len("gitdir:"):].strip())
            if not gd.is_absolute():
                gd = (repo_root / gd).resolve()
            return gd / "hooks"
    return (dot_git / "hooks").resolve()


def _render_hook_block(agentteams_path: str) -> str:
    """Render the sentinel-delimited refresh block for a pre-commit hook.

    Two independent, guarded refreshes, each staging its own map:

    - agent files staged  → refresh references/pipeline-graph.md (agent topology)
    - ``*.py`` files staged → refresh references/architecture-graph.md (module map)

    The block is non-blocking (``|| true``): a refresh failure never aborts a
    commit, and each guard fires only when relevant files are staged, so
    unrelated commits pay no cost.
    """
    pp = f'PYTHONPATH="{agentteams_path}${{PYTHONPATH:+:$PYTHONPATH}}"'
    # `_at_rc=$?` captures the exit status of whatever ran before this block (a
    # pre-existing hook whose body was preserved by the sentinel merge), and the
    # closing `exit $_at_rc` restores it — so appending this block can never mask
    # a prior hook's failing status (e.g. a `npm test` guard). The merge always
    # keeps this block LAST, so the exit is safe.
    return (
        f"{_HOOK_BEGIN}\n"
        "_at_rc=$?\n"
        "# Auto-installed by `agentteams --install-git-hooks`. Regenerates the\n"
        "# repository maps (references/pipeline-graph.md from agent files;\n"
        "# references/architecture-graph.md from package .py files) that are part\n"
        "# of the commit, from the working tree, and stages the result. Non-blocking:\n"
        "# never changes the commit's success. Remove this block (or\n"
        "# agentteams --update --no-git-hooks) to disable.\n"
        'if command -v git >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then\n'
        '    _at_root="$(git rev-parse --show-toplevel 2>/dev/null)"\n'
        '    _staged="$(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null)"\n'
        '    if [ -n "$_at_root" ]; then\n'
        "        if printf '%s\\n' \"$_staged\" | grep -qE '(^|/)\\.(github|claude)/agents/[^/]*\\.agent\\.md$'; then\n"
        f'            {pp} \\\n'
        '                python3 -m agentteams.git_hooks --refresh --repo "$_at_root" >/dev/null 2>&1 \\\n'
        '                && git -C "$_at_root" add references/pipeline-graph.md >/dev/null 2>&1 || true\n'
        "        fi\n"
        "        if printf '%s\\n' \"$_staged\" | grep -qE '\\.py$'; then\n"
        f'            {pp} \\\n'
        '                python3 -m agentteams.git_hooks --refresh-architecture --repo "$_at_root" >/dev/null 2>&1 \\\n'
        '                && git -C "$_at_root" add references/architecture-graph.md >/dev/null 2>&1 || true\n'
        "        fi\n"
        "    fi\n"
        "fi\n"
        "exit $_at_rc\n"
        f"{_HOOK_END}\n"
    )


def _strip_all_blocks(text: str) -> str:
    """Remove every agentteams sentinel block from ``text``, self-repairing.

    Complete BEGIN…END pairs are excised. A trailing orphan BEGIN with no END
    (a truncated/corrupted install) is dropped from the BEGIN to end-of-file, so
    a re-install never leaves an unbalanced ``if`` that would break every commit.
    Duplicate blocks are all removed, so only one is ever re-appended.
    """
    out = text
    while True:
        begin = out.find(_HOOK_BEGIN)
        if begin == -1:
            return out
        end = out.find(_HOOK_END, begin + len(_HOOK_BEGIN))
        if end == -1:
            return out[:begin]
        after = end + len(_HOOK_END)
        if after < len(out) and out[after] == "\n":
            after += 1
        out = out[:begin] + out[after:]


def _merge_hook_content(existing: str | None, block: str) -> str:
    """Insert/replace the agentteams block, keeping it LAST and self-repairing.

    - No existing hook → a fresh ``#!/bin/sh`` script containing the block.
    - Existing hook → all prior agentteams blocks (complete, duplicated, or a
      truncated orphan) are stripped and one block is appended after the
      preserved body. Idempotent: re-running with the same baked path is a no-op.
      Uses pure string ops (never ``re.sub``) so backslash sequences in ``block``
      are preserved verbatim.
    """
    if not existing:
        return "#!/bin/sh\n" + block
    cleaned = _strip_all_blocks(existing)
    if not cleaned.strip():
        return "#!/bin/sh\n" + block
    body = cleaned if cleaned.endswith("\n") else cleaned + "\n"
    return body + block


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
    # Guard against a global core.hooksPath: auto-installing into a hooks dir
    # OUTSIDE this repo would silently run the hook for every repository. Skip
    # (with a note) on auto-install; an explicit --install-git-hooks still honours
    # the operator's configured path.
    hooks_dir = _resolve_hooks_dir(repo_root)
    try:
        _inside_repo = hooks_dir.is_relative_to(repo_root)
    except AttributeError:  # pragma: no cover - Python < 3.9
        _inside_repo = str(hooks_dir).startswith(str(repo_root))
    if not _inside_repo:
        print(
            f"  ℹ  Skipping auto-install of the pre-commit hook: git hooks resolve "
            f"outside this repo ({hooks_dir}) — likely a global core.hooksPath. "
            f"Run `agentteams --install-git-hooks` explicitly to install there.",
            file=sys.stderr,
        )
        return
    try:
        res = install_pre_commit_hook(repo_root, hooks_dir=hooks_dir)
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
    parser.add_argument("--refresh-architecture", action="store_true", dest="refresh_architecture",
                        help="Regenerate references/architecture-graph.md from the package's .py files.")
    parser.add_argument("--repo", metavar="DIR", default=".",
                        help="Repository root (default: current directory).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compare only; never write.")
    parser.add_argument("--verbose", action="store_true",
                        help="Print a one-line result summary.")
    args = parser.parse_args(argv)

    if not args.refresh and not args.refresh_architecture:
        parser.error("nothing to do: pass --refresh and/or --refresh-architecture")

    repo_root = Path(args.repo).resolve()
    jobs = []
    if args.refresh:
        jobs.append(("pipeline-graph", "agents", refresh_pipeline_graph))
    if args.refresh_architecture:
        jobs.append(("architecture-graph", "modules", refresh_architecture_graph))

    for label, unit, fn in jobs:
        try:
            result = fn(repo_root, dry_run=args.dry_run)
        except (OSError, ImportError, ValueError) as exc:
            # CLI boundary: report cleanly rather than dumping a traceback. Narrow
            # set — file I/O (OSError) and the architecture build's parse/resolve
            # failures (ValueError/ImportError) — not a blanket catch.
            print(f"{label} refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        if args.verbose:
            verb = "would update" if (result.changed and args.dry_run) else result.reason
            print(f"{label}: {verb} ({result.count} {unit}) -> {result.out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
