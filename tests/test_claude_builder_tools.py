"""The Claude team-builder never ships canonical tool tokens (mathAgents P5 render, 2026-10-07)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks.claude import ClaudeAdapter

REPO = Path(__file__).resolve().parent.parent


def _render(tmp_path: Path, **brief_extra) -> str:
    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    brief.update(brief_extra)
    (tmp_path / "brief.json").write_text(json.dumps(brief))
    out = tmp_path / ".claude" / "agents"
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(tmp_path / "brief.json"),
                           "--project", str(tmp_path), "--framework", "claude", "--output", str(out),
                           "--no-scan", "--yes"], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    return (out / "team-builder.md").read_text()


def _front_matter(text: str) -> str:
    return text.split("\n---", 1)[0]


def test_under_write_policy_the_builder_gets_claude_tool_names(tmp_path):
    fm = _front_matter(_render(tmp_path, write_policy="orchestrator-only", privilege_profile="confined"))
    tools = re.search(r"^tools:\s*(.*)$", fm, re.M).group(1)
    assert "[" not in tools and "'" not in tools                 # no canonical flow list
    assert set(t.strip() for t in tools.split(",")) <= {"Read", "Grep", "Glob"}
    assert "Write" not in tools and "Edit" not in tools and "Bash" not in tools  # still narrowed


@pytest.mark.parametrize("content", [
    "---\nname: b\ndescription: x\nallowed-tools: Read, Edit, Write\n---\n# B\n",
    "---\nname: b\ndescription: x\ntools: Read, Edit\n---\n# B\n\ntools: ['read'] (body)\n",
])
def test_a_claude_shaped_builder_passes_through_unchanged(content):
    assert ClaudeAdapter().render_builder_file(content, {}) == content


def test_a_block_scalar_with_dashes_does_not_hide_the_tools_line():
    content = ("---\nname: b\ndescription: |\n  line one\n  ---\n  line two\ntools: ['read', 'search']\n---\n"
               "# B\n\ntools: ['read'] (body)\n")
    out = ClaudeAdapter().render_builder_file(content, {})
    assert "tools: Read, Grep, Glob" in out and out.endswith("tools: ['read'] (body)\n")
