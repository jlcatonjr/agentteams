"""``--convert-from`` keeps another framework's owned fences out of the target's instructions (r11 P10).

1.1 backport: release/1.1 has no ``FRAMEWORK_OWNED_FENCE_IDS``; convert strips what 1.1's interop strips (Grok's block,
and Codex's write policy for non-Codex targets). Goose bridge fences are not stripped on 1.1, so not asserted here. ``convert_team`` read the source instructions file
verbatim, so a ``codex_write_policy`` block (or a goose bridge fence) in the source reached Goose's ``AGENTS.md``,
Claude's ``CLAUDE.md`` and the Goose adapter's ``_source_instructions_content``. Found by @security in the AT-11
backport review (convert.py:191); operator ruling 2026-11-08 (UTC): fix in 1.1.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from agentteams.convert import convert_team


def _fence(sid: str, body: str) -> str:
    return f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{body}\n<!-- AGENTTEAMS:END {sid} -->\n"


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src" / ".github" / "agents"
    src.mkdir(parents=True)
    (src / "orchestrator.agent.md").write_text(
        "---\nname: orchestrator\ndescription: \"orchestrator\"\ntools: ['read']\n---\n\n# orchestrator\n\nWork.\n")
    (src.parent / "copilot-instructions.md").write_text(
        "# Team\n\n" + _fence("codex_write_policy", "Planted Codex policy.") + "\n"
        + _fence("goose_bridge_entry", "Goose bridge entry.") + "\n" + _fence("project_notes", "Shared notes."))
    return src


def _convert(tmp_path: Path, framework: str, agents: tuple[str, ...]) -> Path:
    target = tmp_path.joinpath("proj", *agents)
    with contextlib.redirect_stdout(io.StringIO()):
        convert_team(_source(tmp_path), target, framework, overwrite=True)
    return tmp_path / "proj"


@pytest.mark.parametrize("framework, agents, instr, kept", [
    ("claude", (".claude", "agents"), ".claude/CLAUDE.md", None),
    ("goose", (".goose", "recipes"), "AGENTS.md", "Goose bridge entry."),
])
def test_converted_instructions_carry_no_foreign_fence(tmp_path: Path, framework: str, agents: tuple[str, ...],
                                                        instr: str, kept: str | None) -> None:
    text = (_convert(tmp_path, framework, agents) / instr).read_text(encoding="utf-8")
    assert "Shared notes." in text
    assert "codex_write_policy" not in text and "Planted Codex policy." not in text
    if kept:  # the target's own fence stays
        assert kept in text


def test_codex_conversion_keeps_its_own_write_policy_fence(tmp_path: Path) -> None:
    text = (_convert(tmp_path, "codex", (".codex", "agents")) / "AGENTS.md").read_text(encoding="utf-8")
    assert "Planted Codex policy." in text
