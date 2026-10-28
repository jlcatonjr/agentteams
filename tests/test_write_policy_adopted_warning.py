"""Render-time warning for adopted agents left unnarrowed under write_policy (mathAgents P5 render, 2026-10-07)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests._fresh_intel import seed_offline_intel

REPO = Path(__file__).resolve().parent.parent
_BESPOKE = ("---\nname: bespoke-writer\ndescription: Project-owned agent\ntools: Read, Write, Edit\nmodel: sonnet\n"
            "user-invocable: false\n---\n# Bespoke writer\n\n## 🛑 Invariant Core\nx\n")


def _render(project: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(project / "brief.json"),
                           "--project", str(project), "--framework", "claude", "--output",
                           str(project / ".claude" / "agents"), "--no-scan", "--yes",
                           *seed_offline_intel(project / ".claude" / "agents", "claude"), *extra],
                          cwd=project, capture_output=True, text=True, timeout=300)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    brief.update(write_policy="orchestrator-only", privilege_profile="confined")
    (tmp_path / "brief.json").write_text(json.dumps(brief))
    first = _render(tmp_path)
    assert first.returncode == 0, first.stdout[-800:] + first.stderr[-800:]
    assert "[WRITE-POLICY]" not in first.stdout          # generated agents are narrowed: no warning
    return tmp_path


def test_an_unnarrowed_adopted_agent_is_named_and_left_alone(project):
    path = project / ".claude" / "agents" / "bespoke-writer.md"
    path.write_text(_BESPOKE)
    out = _render(project, "--update", "--merge")
    assert out.returncode == 0                            # a warning, not a failure (operator decision)
    assert "[WRITE-POLICY] 1 agent file(s) agentteams does not generate" in out.stdout and "bespoke-writer.md" in out.stdout
    assert path.read_text() == _BESPOKE                   # never rewritten


def test_a_generated_file_is_never_reported_even_if_unwritten(tmp_path):
    # A file agentteams generates but skipped this run (legacy, shrink-blocked) is not "adopted": the fix for it
    # is --merge/--overwrite, so the warning must not name it.
    from agentteams.cli.write_policy_warning import unnarrowed_adopted_agents
    (tmp_path / "x.md").write_text(_BESPOKE)
    manifest = {"framework": "claude", "write_policy": "orchestrator-only"}
    assert unnarrowed_adopted_agents(manifest, tmp_path, ["x.md"]) == []
    assert unnarrowed_adopted_agents(manifest, tmp_path, []) == ["x.md"]
    assert unnarrowed_adopted_agents({"framework": "claude"}, tmp_path, []) == []   # switch off: silent


# Bridge-generated agents are refused, not warned (operator decision, 2026-10-27).
_STUB = _BESPOKE.replace("name: bespoke-writer\n", "name: legacy-helper\nbridge: copilot-vscode-to-claude\n")


def _tree(root: Path) -> dict[str, bytes]:
    # The vulnerability watch is written by the test harness's seed_offline_intel on every _render call.
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and p.name != "security-vulnerability-watch.json"}


def test_a_wide_bridge_stub_refuses_the_render_and_nothing_is_written(project):
    agents = project / ".claude" / "agents"
    (agents / "legacy-helper.md").write_text(_STUB)
    before = _tree(agents)
    out = _render(project, "--update", "--merge")
    assert out.returncode == 1
    assert "bridge-generated agent file(s) are not narrowed" in out.stderr
    assert ("legacy-helper.md (bridge: copilot-vscode-to-claude): agentteams --retire-bridge copilot-vscode-to-claude"
            in out.stderr)
    assert _tree(agents) == before                        # refused before any write (heal, prune, backup, emit)


def test_a_wide_bridge_stub_also_fails_a_dry_run(project):
    (project / ".claude" / "agents" / "legacy-helper.md").write_text(_STUB)
    out = _render(project, "--update", "--merge", "--dry-run")
    assert out.returncode == 1 and "Error: [WRITE-POLICY]" in out.stderr


def test_bridge_refusal_names_only_bridge_files(tmp_path):
    from agentteams.cli.write_policy_warning import bridge_agent_refusal
    manifest = {"framework": "claude", "write_policy": "orchestrator-only"}
    (tmp_path / "bespoke.md").write_text(_BESPOKE)
    assert bridge_agent_refusal(manifest, tmp_path, []) is None          # bespoke: warning only
    (tmp_path / "odd.md").write_text(_BESPOKE.replace("name: bespoke-writer\n", "name: odd\nbridge:\n"))
    text = bridge_agent_refusal(manifest, tmp_path, [])
    assert text and "odd.md (bridge: ?): delete it, or narrow its tools" in text and "bespoke.md" not in text
    assert bridge_agent_refusal({"framework": "claude"}, tmp_path, []) is None   # switch off
