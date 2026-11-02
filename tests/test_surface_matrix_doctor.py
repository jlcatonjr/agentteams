"""baseAgent P3–P11: the agent-surface matrix and ``--doctor``."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import surface_matrix as M
from agentteams.doctor import run_doctor
from agentteams.memory_index import build_memory_index

REPO = Path(__file__).resolve().parent.parent
_NOTES = "\n## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — notes\n"


def _md(tools: str, notes: str = "") -> str:
    return f"---\nname: a\ntools: {tools}\n---\n# A\n{_NOTES}{notes}"


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    claude, copilot = tmp_path / ".claude" / "agents", tmp_path / ".github" / "agents"
    goose, codex = tmp_path / ".goose" / "recipes", tmp_path / ".codex" / "agents"
    for d in (claude, copilot, goose, codex):
        d.mkdir(parents=True)
    (claude / "security.md").write_text(_md("Read, Grep, Glob", "\n- claude-only rule\n"), encoding="utf-8")
    (copilot / "security.agent.md").write_text(_md("['read', 'search']"), encoding="utf-8")
    (claude / "builder.md").write_text(_md("Read, Edit, Write, Bash"), encoding="utf-8")
    (goose / "security.yaml").write_text('version: "1.0.0"\ntitle: s\ninstructions: |\n  # S\nextensions:\n'
                                         '  - type: builtin\n    name: developer\nprompt: go\n', encoding="utf-8")
    (codex / "security.toml").write_text('name = "security"\nsandbox_mode = "read-only"\n', encoding="utf-8")
    (tmp_path / ".claude" / "skills" / "recall").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "recall" / "SKILL.md").write_text("x", encoding="utf-8")
    (tmp_path / ".agents" / "skills" / "tool-x").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "tool-x" / "SKILL.md").write_text("x", encoding="utf-8")
    return tmp_path


def test_matrix_finds_gaps_tools_notes_and_skills(root: Path):
    matrix = M.build_matrix(root)
    assert matrix.surfaces == ["claude", "copilot", "goose", "codex"]
    assert matrix.gaps() == {"builder": ["copilot", "goose", "codex"]}
    assert matrix.notes_differ() == ["security"]
    assert M.capability(matrix.agents["builder"]["claude"].tools) == "may write and run commands (assume so)"
    assert M.capability(matrix.agents["security"]["claude"].tools) == "read-only"
    assert "may still run commands" in M.capability(matrix.agents["security"]["codex"].tools)
    assert M.capability(matrix.agents["security"]["goose"].tools) == "may write and run commands (assume so)"
    text = M.render_matrix(matrix)
    assert M.NEVER_A_GRANT in text and "`builder` — missing on copilot, goose, codex" in text
    assert "claude: Read, Edit, Write, Bash — may write and run commands (assume so)" in text
    assert "On some skill surfaces only: recall, tool-x." in text


def test_write_matrix_only_when_changed(root: Path):
    path, written = M.write_matrix(root)
    assert written and path == root / M.MATRIX_REL
    assert M.write_matrix(root) == (path, False)


def test_doctor_reports_and_fails_on_stale_rules(root: Path, capsys):
    (root / ".claude" / "CLAUDE.md").write_text("# T\n\n## Project-Specific Rules\n\n- R-1 no runs at night\n",
                                                encoding="utf-8")
    (root / ".github" / "copilot-instructions.md").write_text("# T\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("<!-- AGENTTEAMS:BEGIN constitutional_core v=2 -->\nc\n"
                                    "<!-- AGENTTEAMS:END constitutional_core -->\n", encoding="utf-8")
    (root / ".github" / "copilot-instructions.md").write_text(
        "<!-- AGENTTEAMS:BEGIN constitutional_core v=3 -->\nc\n<!-- AGENTTEAMS:END constitutional_core -->\n",
        encoding="utf-8")
    for rel, fp in ((".claude/agents", "a"), (".github/agents", "b")):
        idx = build_memory_index([], framework="x", root=root)
        idx["source_fingerprint"] = fp
        (root / rel / "references").mkdir(parents=True, exist_ok=True)
        (root / rel / "references" / "memory-index.json").write_text(json.dumps(idx), encoding="utf-8")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert run_doctor(str(root)) == 1
    out = capsys.readouterr().out
    assert "FAIL .github/copilot-instructions.md: missing" in out
    assert "built from 2 different source sets" in out
    assert "builder: missing on copilot, goose, codex" in out
    assert "constitutional_core v=2, the other entry files v=3" in out
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before  # doctor writes nothing


def test_doctor_cli_is_clean_on_an_agreeing_project(tmp_path: Path):
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "a.md").write_text(_md("Read"), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--doctor", "--project", str(tmp_path)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 failure(s)" in proc.stdout



# --- adversarial review, 2026-11-02: a label must never understate what an agent can do ---------------------


@pytest.mark.parametrize("front_matter, expected", [
    ("name: a\n", None),                                          # nothing declared: inherits everything
    ("tools:\n  - Read\n  - Edit\n", ["Read", "Edit"]),           # block list
    ("tools: >\n  Read\n", None),                                 # folded scalar: not trusted
    ("tools: Read, You may also use Bash\n", ["Read", "(invalid entry)"]),  # prose is not a name (C-4)
])
def test_front_matter_tools_parsing(front_matter, expected):
    assert M._front_matter_tools(f"---\n{front_matter}---\n# A\n") == expected


@pytest.mark.parametrize("tools", [None, [], ["apply_patch"], ["mcp__filesystem__move_file"], ["Read", "new"],
                                   ["(invalid entry)"], ["sandbox_mode=workspace-write"]])
def test_anything_not_proven_read_only_is_assumed_to_write_and_run(tools):
    assert M.capability(tools) != "read-only"


def test_goose_extension_forms_and_inheritance():
    assert M._recipe_tools("extensions:\n- type: builtin\n  name: developer\nprompt: x\n") == ["developer"]
    assert M._recipe_tools("extensions:\n  - name: computercontroller\n    type: builtin\n") == ["computercontroller"]
    assert M._recipe_tools("title: t\nprompt: x\n") is None  # no block: inherits the user's extensions


def test_write_matrix_never_clobbers_a_hand_made_file_or_follows_a_symlink(root: Path, tmp_path_factory):
    path = root / M.MATRIX_REL
    path.parent.mkdir(parents=True)
    path.write_text("# my bibliography note\n", encoding="utf-8")
    with pytest.raises(OSError, match="wasn't generated"):
        M.write_matrix(root)
    path.unlink()
    path.parent.rmdir()
    outside = tmp_path_factory.mktemp("outside")
    (root / "references").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError, match="symlink"):
        M.write_matrix(root)
    assert not any(outside.iterdir())
