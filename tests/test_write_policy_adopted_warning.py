"""Render-time warning for adopted agents left unnarrowed under write_policy (mathAgents P5 render, 2026-10-07)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_BESPOKE = ("---\nname: bespoke-writer\ndescription: Project-owned agent\ntools: Read, Write, Edit\nmodel: sonnet\n"
            "user-invocable: false\n---\n# Bespoke writer\n\n## 🛑 Invariant Core\nx\n")


def _render(project: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(project / "brief.json"),
                           "--project", str(project), "--framework", "claude", "--output",
                           str(project / ".claude" / "agents"), "--no-scan", "--yes", *extra],
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
