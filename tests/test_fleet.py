"""Tests for the fleet-update feature (agentteams/fleet.py).

The per-target update is exercised via a monkeypatched in-process call so the
tests stay fast and deterministic (no network / no full template render).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentteams import fleet


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _mk_agent(dirpath: Path, name: str, body: str = "agent\n") -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / name).write_text(body, encoding="utf-8")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "init", "--no-verify")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discover_finds_github_and_claude_and_prunes_noise(tmp_path):
    _mk_agent(tmp_path / "a" / ".github" / "agents", "x.agent.md")
    _mk_agent(tmp_path / "b" / ".claude" / "agents", "x.md")
    _mk_agent(tmp_path / "c" / "nested" / ".github" / "agents", "x.agent.md")
    # noise that must be pruned
    _mk_agent(tmp_path / "node_modules" / "pkg" / ".github" / "agents", "x.agent.md")
    _mk_agent(tmp_path / "d" / ".worktrees" / "wt" / ".github" / "agents", "x.agent.md")
    _mk_agent(tmp_path / "archive" / "old" / ".github" / "agents", "x.agent.md")

    ws = fleet.discover_workspaces(tmp_path, "both")
    names = {str(w.relative_to(tmp_path)) for w in ws}
    assert names == {"a", "b", "c/nested"}


def test_discover_framework_filter(tmp_path):
    _mk_agent(tmp_path / "a" / ".github" / "agents", "x.agent.md")
    _mk_agent(tmp_path / "b" / ".claude" / "agents", "x.md")
    gh = {w.name for w in fleet.discover_workspaces(tmp_path, "github")}
    cl = {w.name for w in fleet.discover_workspaces(tmp_path, "claude")}
    assert gh == {"a"} and cl == {"b"}


# ---------------------------------------------------------------------------
# Descriptor resolution (stub-trap fix)
# ---------------------------------------------------------------------------

def test_descriptor_prefers_brief_over_stub(tmp_path):
    ws = tmp_path / "ws"
    _mk_agent(ws / ".github" / "agents", "_build-description.json", "{}")
    (ws / ".agentteams").mkdir(parents=True)
    (ws / ".agentteams" / "brief.json").write_text("{}", encoding="utf-8")
    assert fleet._resolve_descriptor(ws) == ws / ".agentteams" / "brief.json"


def test_descriptor_falls_back_to_stub(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".github" / "agents").mkdir(parents=True)
    (ws / ".github" / "agents" / "_build-description.json").write_text("{}", encoding="utf-8")
    assert fleet._resolve_descriptor(ws).name == "_build-description.json"


# ---------------------------------------------------------------------------
# Claude target detection
# ---------------------------------------------------------------------------

def test_claude_kind_bridge_via_manifest(tmp_path):
    ws = tmp_path / "ws"
    _mk_agent(ws / ".claude" / "agents", "x.md")
    man = ws / "references" / "bridges" / "copilot-vscode-to-claude"
    man.mkdir(parents=True)
    (man / "bridge-manifest.json").write_text("{}", encoding="utf-8")
    assert fleet._claude_kind(ws) == "bridge"


def test_claude_kind_bridge_via_subagent_stub(tmp_path):
    ws = tmp_path / "ws"
    _mk_agent(ws / ".claude" / "agents", "x.md", "---\nsource_sha256: abc\n---\nstub\n")
    assert fleet._claude_kind(ws) == "bridge"


def test_claude_kind_direct_when_descriptor_and_no_bridge(tmp_path):
    ws = tmp_path / "ws"
    _mk_agent(ws / ".claude" / "agents", "x.md", "native claude agent\n")
    (ws / ".github" / "agents").mkdir(parents=True)
    (ws / ".github" / "agents" / "_build-description.json").write_text("{}", encoding="utf-8")
    assert fleet._claude_kind(ws) == "direct"


def test_claude_kind_ambiguous_without_descriptor(tmp_path):
    ws = tmp_path / "ws"
    _mk_agent(ws / ".claude" / "agents", "x.md", "native\n")
    assert fleet._claude_kind(ws) == "ambiguous"


# ---------------------------------------------------------------------------
# Git snapshot
# ---------------------------------------------------------------------------

def test_snapshot_clean_repo_uses_head_no_commit(tmp_path):
    repo = tmp_path / "r"
    _mk_agent(repo / ".github" / "agents", "x.agent.md")
    _init_repo(repo)
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    ref, committed = fleet._git_snapshot(repo)
    assert ref == head_before and committed is False


def test_snapshot_dirty_agent_paths_creates_commit(tmp_path):
    repo = tmp_path / "r"
    _mk_agent(repo / ".github" / "agents", "x.agent.md")
    _init_repo(repo)
    # dirty an agent file
    (repo / ".github" / "agents" / "x.agent.md").write_text("changed\n", encoding="utf-8")
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    ref, committed = fleet._git_snapshot(repo)
    assert committed is True and ref != head_before
    # working tree is now clean for agent paths
    assert _git(repo, "status", "--porcelain", "--", ".github/agents").stdout.strip() == ""


# ---------------------------------------------------------------------------
# USER-EDITABLE deletion detection
# ---------------------------------------------------------------------------

def test_user_editable_deletion_flagged(tmp_path):
    repo = tmp_path / "r"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    f = agents / "a.agent.md"
    f.write_text(
        "<!-- AGENTTEAMS:content:BEGIN -->\ntemplate line\n<!-- AGENTTEAMS:content:END -->\n"
        "## USER-EDITABLE\nkeep me\ndelete me\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    ref = _git(repo, "rev-parse", "HEAD").stdout.strip()
    # delete a USER-EDITABLE line
    f.write_text(
        "<!-- AGENTTEAMS:content:BEGIN -->\ntemplate line\n<!-- AGENTTEAMS:content:END -->\n"
        "## USER-EDITABLE\nkeep me\n",
        encoding="utf-8",
    )
    dels = fleet._user_editable_deletions(repo, ref, ".github/agents/a.agent.md")
    assert any("delete me" in d for d in dels)


def test_user_editable_deletion_not_flagged_for_fenced_change(tmp_path):
    repo = tmp_path / "r"
    agents = repo / ".github" / "agents"
    agents.mkdir(parents=True)
    f = agents / "a.agent.md"
    f.write_text(
        "<!-- AGENTTEAMS:content:BEGIN -->\nold template\n<!-- AGENTTEAMS:content:END -->\n"
        "## USER-EDITABLE\nkeep me\n",
        encoding="utf-8",
    )
    _init_repo(repo)
    ref = _git(repo, "rev-parse", "HEAD").stdout.strip()
    f.write_text(
        "<!-- AGENTTEAMS:content:BEGIN -->\nnew template\n<!-- AGENTTEAMS:content:END -->\n"
        "## USER-EDITABLE\nkeep me\n",
        encoding="utf-8",
    )
    dels = fleet._user_editable_deletions(repo, ref, ".github/agents/a.agent.md")
    assert dels == []


# ---------------------------------------------------------------------------
# Orchestration (dry-run + apply) with a faked in-process update
# ---------------------------------------------------------------------------

class _Args:
    def __init__(self, **kw):
        self.fleet = kw.get("fleet")
        self.fleet_frameworks = kw.get("fleet_frameworks", "both")
        self.fleet_report = kw.get("fleet_report")
        self.yes = kw.get("yes", False)
        self.dry_run = kw.get("dry_run", False)
        self.shrink_policy = kw.get("shrink_policy", "preserve")


def test_dry_run_writes_report_and_no_changes(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    _mk_agent(ws / ".github" / "agents", "_build-description.json", "{}")
    _mk_agent(ws / ".github" / "agents", "a.agent.md", "x\n")
    _init_repo(ws)

    calls = []
    monkeypatch.setattr(fleet, "_run_main", lambda argv: (calls.append(argv) or (0, "[DRY RUN] Would generate 1 file(s)")))
    rc = fleet.run_fleet(_Args(fleet=str(tmp_path), yes=False), parser=None)

    assert rc == 0
    # every faked call was a dry-run
    assert all("--dry-run" in a for a in calls)
    report = tmp_path / ".agentteams-fleet"
    assert report.exists()
    runs = list(report.iterdir())
    assert runs and (runs[0] / "report.json").exists()


def test_apply_classifies_ok_when_only_fenced_change(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    agents = ws / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "_build-description.json").write_text("{}", encoding="utf-8")
    (agents / "a.agent.md").write_text(
        "<!-- AGENTTEAMS:content:BEGIN -->\nold\n<!-- AGENTTEAMS:content:END -->\n## USER-EDITABLE\nkeep\n",
        encoding="utf-8",
    )
    _init_repo(ws)

    def fake_update(argv):
        # simulate a fenced-region change only (no USER-EDITABLE loss)
        (agents / "a.agent.md").write_text(
            "<!-- AGENTTEAMS:content:BEGIN -->\nnew\n<!-- AGENTTEAMS:content:END -->\n## USER-EDITABLE\nkeep\n",
            encoding="utf-8",
        )
        return 0, "Merged: 1 file(s)"

    monkeypatch.setattr(fleet, "_run_main", fake_update)
    rc = fleet.run_fleet(_Args(fleet=str(tmp_path), yes=True), parser=None)
    assert rc == 0
    report = next((tmp_path / ".agentteams-fleet").iterdir())
    import json
    data = json.loads((report / "report.json").read_text())
    assert data["applied"] is True
    statuses = [t["status"] for w in data["workspaces"] for t in w["targets"]]
    assert statuses == ["OK"]


def test_apply_flags_review_on_user_editable_deletion(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    agents = ws / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "_build-description.json").write_text("{}", encoding="utf-8")
    (agents / "a.agent.md").write_text(
        "<!-- AGENTTEAMS:content:BEGIN -->\nt\n<!-- AGENTTEAMS:content:END -->\n## USER-EDITABLE\nkeep\ndrop\n",
        encoding="utf-8",
    )
    _init_repo(ws)

    def fake_update(argv):
        (agents / "a.agent.md").write_text(
            "<!-- AGENTTEAMS:content:BEGIN -->\nt\n<!-- AGENTTEAMS:content:END -->\n## USER-EDITABLE\nkeep\n",
            encoding="utf-8",
        )
        return 0, "Merged: 1 file(s)"

    monkeypatch.setattr(fleet, "_run_main", fake_update)
    rc = fleet.run_fleet(_Args(fleet=str(tmp_path), yes=True), parser=None)
    report = next((tmp_path / ".agentteams-fleet").iterdir())
    import json
    data = json.loads((report / "report.json").read_text())
    statuses = [t["status"] for w in data["workspaces"] for t in w["targets"]]
    assert "REVIEW" in statuses


# ---------------------------------------------------------------------------
# CLI validation contract (locks what the docs promise)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["--fleet", "/tmp"],                                       # missing --update
    ["--fleet", "/tmp", "--update"],                          # missing --merge (merge-only)
    ["--fleet", "/tmp", "--update", "--overwrite"],          # --overwrite rejected
    ["--fleet", "/tmp", "--update", "--merge", "--description", "x.json"],  # single-target rejected
    ["--fleet", "/tmp", "--update", "--merge", "--prune"],    # destructive rejected
])
def test_fleet_cli_validation_rejects(argv):
    import build_team
    with pytest.raises(SystemExit) as exc:
        build_team.main(argv)
    assert exc.value.code == 2  # argparse parser.error() exit code

