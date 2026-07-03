"""Tests for agentteams.git_hooks — commit-triggered pipeline-graph refresh.

Covers the two public capabilities (refresh + install), the determinism
guarantee that lets a disk-built refresh reproduce the render-pipeline output,
and the CLI/auto-install wiring.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import git_hooks as gh
from agentteams import graph as _graph

EXAMPLES = Path(__file__).parent.parent / "examples"
_EXAMPLE_AGENTS = EXAMPLES / "software-project" / "expected"
_EXAMPLE_GRAPH = _EXAMPLE_AGENTS / "references" / "pipeline-graph.md"


def _has_git() -> bool:
    return shutil.which("git") is not None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _make_repo(tmp_path: Path, *, agents_subdir: str = ".github/agents") -> Path:
    """Create a git repo with the example agent files under an agents dir."""
    repo = tmp_path / "repo"
    agents = repo / agents_subdir
    agents.mkdir(parents=True)
    for p in _EXAMPLE_AGENTS.glob("*.agent.md"):
        shutil.copy(p, agents / p.name)
    if _has_git():
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t.co")
        _git(repo, "config", "user.name", "t")
    return repo


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def test_refresh_creates_graph_when_missing(tmp_path):
    repo = _make_repo(tmp_path)
    result = gh.refresh_pipeline_graph(repo)
    assert result.wrote and result.changed
    assert result.agent_count == 24
    assert (repo / "references" / "pipeline-graph.md").is_file()


def test_refresh_is_idempotent(tmp_path):
    repo = _make_repo(tmp_path)
    gh.refresh_pipeline_graph(repo)
    second = gh.refresh_pipeline_graph(repo)
    assert not second.changed and not second.wrote
    assert second.reason == "already current"


def test_refresh_output_is_fence_wrapped(tmp_path):
    repo = _make_repo(tmp_path)
    gh.refresh_pipeline_graph(repo)
    text = (repo / "references" / "pipeline-graph.md").read_text()
    assert text.startswith("<!-- AGENTTEAMS:BEGIN content v=1 -->")
    assert text.rstrip().endswith("<!-- AGENTTEAMS:END content -->")


def test_refresh_reproduces_pipeline_golden_byte_for_byte(tmp_path):
    """A disk-built refresh must equal what the render pipeline committed."""
    repo = _make_repo(tmp_path)
    (repo / "references").mkdir(exist_ok=True)
    shutil.copy(_EXAMPLE_GRAPH, repo / "references" / "pipeline-graph.md")
    result = gh.refresh_pipeline_graph(repo)
    # Golden is the pipeline output; refresh must not want to change it.
    assert not result.changed, "hook refresh diverges from --update output"


def test_refresh_no_agent_files_is_graceful(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    result = gh.refresh_pipeline_graph(repo)
    assert not result.changed and not result.wrote
    assert result.agents_dir is None
    assert "no agent files" in result.reason


def test_refresh_dry_run_does_not_write(tmp_path):
    repo = _make_repo(tmp_path)
    result = gh.refresh_pipeline_graph(repo, dry_run=True)
    assert result.changed and not result.wrote
    assert not (repo / "references" / "pipeline-graph.md").exists()


def test_refresh_excludes_backup_ghost_agents(tmp_path):
    """Agents under .agentteams-backups/ must not become graph nodes."""
    repo = _make_repo(tmp_path)
    backup = repo / ".github" / "agents" / ".agentteams-backups" / "20260101-000000"
    backup.mkdir(parents=True)
    shutil.copy(_EXAMPLE_AGENTS / "cleanup.agent.md", backup / "stale-ghost.agent.md")
    gh.refresh_pipeline_graph(repo)
    text = (repo / "references" / "pipeline-graph.md").read_text()
    assert "stale-ghost" not in text
    assert "stale_ghost" not in text


def test_refresh_preserves_project_name_from_existing_h1(tmp_path):
    repo = _make_repo(tmp_path)
    graph_path = repo / "references" / "pipeline-graph.md"
    graph_path.parent.mkdir(exist_ok=True)
    graph_path.write_text(
        "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
        "# CustomName — Agent Team Topology\n\n"
        "<!-- AGENTTEAMS:END content -->\n"
    )
    gh.refresh_pipeline_graph(repo)
    assert "# CustomName — Agent Team Topology" in graph_path.read_text()


def test_refresh_prefers_github_over_claude_agents(tmp_path):
    repo = _make_repo(tmp_path)
    # Add a .claude/agents dir too; .github/agents should win.
    claude = repo / ".claude" / "agents"
    claude.mkdir(parents=True)
    shutil.copy(_EXAMPLE_AGENTS / "orchestrator.agent.md", claude / "orchestrator.agent.md")
    result = gh.refresh_pipeline_graph(repo)
    assert result.agents_dir == (repo / ".github" / "agents").resolve()


# ---------------------------------------------------------------------------
# Determinism (the guarantee the hook relies on)
# ---------------------------------------------------------------------------

def test_graph_output_is_order_independent():
    files = sorted(_EXAMPLE_AGENTS.glob("*.agent.md"))
    from agentteams import emit
    fm_fwd = {p.name: p.read_text() for p in files}
    fm_rev = {p.name: p.read_text() for p in reversed(files)}
    name = _graph.infer_project_name(fm_fwd)
    out_fwd = emit._normalize_generated_content(
        "references/pipeline-graph.md",
        _graph.generate_graph_document(fm_fwd, project_name=name),
    )
    out_rev = emit._normalize_generated_content(
        "references/pipeline-graph.md",
        _graph.generate_graph_document(fm_rev, project_name=name),
    )
    assert out_fwd == out_rev


def test_ordered_edges_are_sorted_by_source_then_kind_then_target():
    fm = {p.name: p.read_text() for p in _EXAMPLE_AGENTS.glob("*.agent.md")}
    graph = _graph.build_graph(fm, project_name="X")
    edges = graph.ordered_edges()
    keys = [
        (e.source, 0 if e.edge_type == "handoff" else 1, e.target, e.label or "")
        for e in edges
    ]
    assert keys == sorted(keys)


def test_load_from_disk_skips_dot_dirs(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    shutil.copy(_EXAMPLE_AGENTS / "orchestrator.agent.md", agents / "orchestrator.agent.md")
    hidden = agents / ".agentteams-backups"
    hidden.mkdir()
    shutil.copy(_EXAMPLE_AGENTS / "cleanup.agent.md", hidden / "ghost.agent.md")
    fm = _graph.load_from_disk(agents)
    assert "orchestrator.agent.md" in fm
    assert not any(".agentteams-backups" in k for k in fm)


# ---------------------------------------------------------------------------
# Hook block rendering
# ---------------------------------------------------------------------------

def test_hook_block_has_sentinels_and_baked_path():
    block = gh._render_hook_block("/opt/agentteams")
    assert gh._HOOK_BEGIN in block and gh._HOOK_END in block
    assert 'PYTHONPATH="/opt/agentteams' in block
    assert "python3 -m agentteams.git_hooks --refresh" in block
    assert "|| true" in block  # non-blocking


def test_hook_block_fires_only_on_staged_agent_files():
    block = gh._render_hook_block("/x")
    # The grep guard restricts the refresh to commits touching agent files.
    assert r"grep -qE '(^|/)agents/.*\.agent\.md$'" in block
    assert "--cached --name-only" in block


def test_merge_hook_creates_fresh_when_absent():
    merged = gh._merge_hook_content(None, gh._render_hook_block("/x"))
    assert merged.startswith("#!/bin/sh\n")
    assert gh._HOOK_BEGIN in merged


def test_merge_hook_appends_and_preserves_existing_body():
    existing = "#!/bin/sh\necho pre-existing-guard\n"
    merged = gh._merge_hook_content(existing, gh._render_hook_block("/x"))
    assert "pre-existing-guard" in merged
    assert gh._HOOK_BEGIN in merged
    assert merged.index("pre-existing-guard") < merged.index(gh._HOOK_BEGIN)


def test_merge_hook_replaces_existing_block_in_place():
    first = gh._merge_hook_content("#!/bin/sh\necho x\n", gh._render_hook_block("/old"))
    second = gh._merge_hook_content(first, gh._render_hook_block("/new"))
    assert second.count(gh._HOOK_BEGIN) == 1  # not duplicated
    assert "/new" in second and "/old" not in second
    assert "echo x" in second  # body preserved


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_install_creates_executable_hook(tmp_path):
    repo = _make_repo(tmp_path)
    result = gh.install_pre_commit_hook(repo, agentteams_path="/opt/agentteams")
    assert result.action == "created"
    assert result.hook_path.is_file()
    assert os.access(result.hook_path, os.X_OK)


@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_install_is_idempotent(tmp_path):
    repo = _make_repo(tmp_path)
    gh.install_pre_commit_hook(repo, agentteams_path="/opt/agentteams")
    second = gh.install_pre_commit_hook(repo, agentteams_path="/opt/agentteams")
    assert second.action == "unchanged"


@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_install_merges_into_existing_hook(tmp_path):
    repo = _make_repo(tmp_path)
    hook = gh._resolve_hooks_dir(repo) / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho other-tooling\n")
    result = gh.install_pre_commit_hook(repo, agentteams_path="/opt/agentteams")
    assert result.action == "updated"
    body = hook.read_text()
    assert "other-tooling" in body and gh._HOOK_BEGIN in body


def test_install_raises_for_non_git_dir(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    with pytest.raises(FileNotFoundError):
        gh.install_pre_commit_hook(plain, agentteams_path="/x")


# ---------------------------------------------------------------------------
# End-to-end: the installed hook fires on a real commit
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_installed_hook_refreshes_and_stages_on_commit(tmp_path):
    repo = _make_repo(tmp_path)
    gh.install_pre_commit_hook(
        repo, agentteams_path=str(Path(_graph.__file__).resolve().parent.parent)
    )
    _git(repo, "add", ".github/agents")
    commit = _git(repo, "commit", "-m", "add agents")
    assert commit.returncode == 0, commit.stderr
    # The hook should have generated and staged pipeline-graph.md into the commit.
    tracked = _git(repo, "ls-files", "references/pipeline-graph.md").stdout.strip()
    assert tracked == "references/pipeline-graph.md"
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


# ---------------------------------------------------------------------------
# CLI dispatch + auto-install
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_cli_refresh_graph_and_install(tmp_path):
    import build_team
    repo = _make_repo(tmp_path)
    rc = build_team.main(["--install-git-hooks", "--output", str(repo)])
    assert rc == 0
    assert (gh._resolve_hooks_dir(repo) / "pre-commit").is_file()
    rc = build_team.main(["--refresh-graph", "--output", str(repo)])
    assert rc == 0
    assert (repo / "references" / "pipeline-graph.md").is_file()
