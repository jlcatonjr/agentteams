"""Instruction files carry a USER-EDITABLE 'Project Layout' region the generated Directory Structure defers to.

OrthodoxLLM (via researchteam-c7, 2026-10-10): the generated Directory Structure / Output Conventions render one
`primary_output_dir` and one `reference_db_path`, so a repo whose deliverables live under `Projects/*/` got shared
instruction files that misdirect every tool. The project now states its real layout once, and --update keeps it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import emit
from agentteams.project_notes import PROJECT_LAYOUT_SECTION, _ensure_project_layout_section

DOC = ("# Rules\n\n<!-- AGENTTEAMS:BEGIN directory_structure v=1 -->\n## Directory Structure\n\n| `reports/` | x |\n"
       "<!-- AGENTTEAMS:END directory_structure -->\n\n---\n\n<!-- AGENTTEAMS:BEGIN output_conventions v=1 -->\n"
       "## Output Conventions\n- in `reports/`\n<!-- AGENTTEAMS:END output_conventions -->\n")


@pytest.mark.parametrize("name", ["../copilot-instructions.md", "../CLAUDE.md", "../../AGENTS.md"])
def test_the_region_follows_the_directory_structure(name):
    out = _ensure_project_layout_section(name, DOC)
    assert out.count("## Project Layout") == 1
    assert out.index("## Project Layout") > out.index("AGENTTEAMS:END directory_structure")
    assert out.index("## Project Layout") < out.index("AGENTTEAMS:BEGIN output_conventions")
    assert _ensure_project_layout_section(name, out) == out                     # idempotent


def test_other_files_are_untouched():
    for name in ("orchestrator.agent.md", "references/x.md", "README.md"):
        assert _ensure_project_layout_section(name, DOC) == DOC
    assert _ensure_project_layout_section("../AGENTS.md", "# no generated structure\n") == "# no generated structure\n"


def test_update_merge_adds_the_region_and_keeps_the_projects_layout(tmp_path: Path):
    out = tmp_path / ".github" / "agents"
    out.mkdir(parents=True)
    target = tmp_path / ".github" / "copilot-instructions.md"
    target.write_text(DOC, encoding="utf-8")
    emit.emit_all([("../copilot-instructions.md", DOC)], output_dir=out, merge=True, yes=True)
    first = target.read_text(encoding="utf-8")
    assert first.count("## Project Layout") == 1
    edited = first.replace(PROJECT_LAYOUT_SECTION, PROJECT_LAYOUT_SECTION + "\n- Deliverables: `Projects/*/drafts/`\n")
    target.write_text(edited, encoding="utf-8")
    emit.emit_all([("../copilot-instructions.md", DOC.replace("| x |", "| y |"))], output_dir=out, merge=True, yes=True)
    again = target.read_text(encoding="utf-8")
    assert "- Deliverables: `Projects/*/drafts/`" in again and again.count("## Project Layout") == 1
    assert "| y |" in again                                                     # the fenced table still updates


def test_the_template_defers_to_the_region():
    text = (Path(__file__).resolve().parents[1] / "agentteams/templates/copilot-instructions.template.md").read_text()
    assert "the **Project Layout** section below is authoritative" in text
