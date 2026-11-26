"""Where each framework's instruction file sits, as sync change detection and CAI import compute it.

``interop_helpers.instructions_path`` is derived from each adapter's own ``finalize_output_path``, so it must agree
with what a real render writes, including for a team rendered under a custom ``--output``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentteams.frameworks.registry import FRAMEWORK_IDS, agents_dir_for
from agentteams.interop_helpers import instructions_path
from tests._fresh_intel import run_build

REPO = Path(__file__).resolve().parent.parent
_NAMES = {"AGENTS.md", "CLAUDE.md", "copilot-instructions.md", "TEAM.md"}


def _rendered_instruction_files(project: Path) -> list[Path]:
    return sorted(p for p in project.rglob("*") if p.is_file() and p.name in _NAMES
                  and not {".agentteams-backups", "references"} & set(p.relative_to(project).parts))


def _render(project: Path, framework: str, output: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    brief = project / "brief.json"
    shutil.copy(REPO / "examples" / "software-project" / "brief.json", brief)
    proc = run_build(brief, project, framework, output)
    assert proc.returncode == 0, proc.stdout[-1000:] + proc.stderr[-1000:]


def test_the_default_layout_places_each_file_where_expected(tmp_path: Path) -> None:
    expected = {"copilot-vscode": ".github/copilot-instructions.md", "copilot-cli": ".github/copilot-instructions.md",
                "claude": ".claude/CLAUDE.md", "goose": "AGENTS.md", "agents-md": "AGENTS.md", "codex": "AGENTS.md",
                "grok": "AGENTS.md", "wasm": ".agentteams-wasm/TEAM.md"}
    assert set(expected) == set(FRAMEWORK_IDS)
    for fw in FRAMEWORK_IDS:
        assert instructions_path(agents_dir_for(fw, tmp_path), fw) == tmp_path / expected[fw], fw


@pytest.mark.parametrize("framework", FRAMEWORK_IDS)
def test_instructions_path_is_what_the_adapter_emits(tmp_path: Path, framework: str) -> None:
    project = tmp_path / "p"
    output = agents_dir_for(framework, project)
    _render(project, framework, output)
    assert _rendered_instruction_files(project) == [instructions_path(output, framework)]


def test_a_custom_output_keeps_the_offset_from_the_agents_dir(tmp_path: Path) -> None:
    project = tmp_path / "p"
    output = project / "team" / "agents"
    _render(project, "claude", output)
    assert _rendered_instruction_files(project) == [instructions_path(output, "claude")] == [project / "team" / "CLAUDE.md"]
