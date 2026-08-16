"""CLI integration tests for the multi-framework pinned-sync switch (--sync-init / --sync).

Hermetic: every invocation passes --project <tmp_path> pointing at a throwaway
git repo, so nothing touches the real working tree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from agentteams.cli.app import main


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")


def _agent(name="Orchestrator", desc="Coordinates the team") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {desc}\n"
        "user-invocable: true\n"
        "tools: ['read', 'edit']\n"
        'model: ["Claude Sonnet 4.6 (copilot)"]\n'
        "---\n\n"
        "Body prose here.\n"
    )


def test_sync_init_default_frameworks_collides_and_reports_cleanly(tmp_path, capsys):
    """The unqualified default set now always includes both copilot-vscode and
    copilot-cli (P1 convergence: same physical directory) — --sync-init must
    refuse with exit code 2 and a readable message, not a traceback."""
    agents = tmp_path / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "orchestrator.agent.md").write_text(_agent(), encoding="utf-8")
    _init_repo(tmp_path)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")

    code = main(["--sync-init", "--pin", "copilot-vscode", "--project", str(tmp_path)])
    err = capsys.readouterr().err
    assert code == 2
    assert "share one physical agents directory" in err
    assert "copilot-vscode" in err and "copilot-cli" in err


def test_sync_init_with_frameworks_flag_avoids_collision(tmp_path, capsys):
    """--frameworks lets an operator explicitly narrow the sync set to avoid
    the colliding pair; the CLI must plumb it through to sync_init untouched."""
    agents = tmp_path / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "orchestrator.agent.md").write_text(_agent(), encoding="utf-8")
    _init_repo(tmp_path)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")

    code = main([
        "--sync-init", "--pin", "copilot-vscode",
        "--frameworks", "copilot-vscode,claude",
        "--project", str(tmp_path),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "projected to: copilot-vscode, claude" in out
    assert (tmp_path / ".claude" / "agents" / "orchestrator.md").exists()


def test_frameworks_flag_ignores_blank_entries_and_whitespace(tmp_path, capsys):
    """A trailing comma or stray space in --frameworks must not produce a
    bogus empty-string framework id."""
    agents = tmp_path / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "orchestrator.agent.md").write_text(_agent(), encoding="utf-8")
    _init_repo(tmp_path)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")

    code = main([
        "--sync-init", "--pin", "copilot-vscode",
        "--frameworks", " copilot-vscode , claude ,",
        "--project", str(tmp_path),
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "projected to: copilot-vscode, claude" in out
