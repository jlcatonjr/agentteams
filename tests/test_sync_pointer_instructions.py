"""``--sync`` keeps a pointer instruction file a pointer, and says when it adds sections (#461, bl48-29a).

A deliberately short instruction file that holds only a framework-owned fence (a goose bridge entry in a root
``AGENTS.md``) shares no template section with the projection. Before the fix every ``--sync`` pass silently grew it
into a full instruction render.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.interop_helpers import merge_instruction_file
from tests.test_sync_low_severity import _PIN, _PIN_INSTR, _change_pin, _commit, _fence, _quiet, _team

from agentteams.multi_sync import run_sync

_FULL = "# Team\n\n" + _fence("project_overview", 1, "Overview.") + "\n" + _fence("agent_team", 1, "- `@a`")
_POINTER = "# Agents\n\nSee .github/copilot-instructions.md.\n\n" + _fence("goose_bridge_entry", 2, "Run goose.")


def test_pointer_agents_md_is_not_expanded_by_sync(tmp_path: Path) -> None:
    _team(tmp_path, instructions=_FULL, frameworks=("copilot-vscode", "claude", "agents-md"))
    agents_md = tmp_path / "AGENTS.md"
    assert "project_overview" in agents_md.read_text(encoding="utf-8")
    agents_md.write_text(_POINTER, encoding="utf-8")
    _change_pin(tmp_path)
    _commit(tmp_path)
    result = _quiet(run_sync, tmp_path)
    assert agents_md.read_text(encoding="utf-8") == _POINTER
    assert any("pointer instruction file (fences: goose_bridge_entry): 2 template section(s) not added: "
               "project_overview, agent_team" in n for n in result.notices), result.notices


def test_merge_instruction_file_reports_added_sections() -> None:
    missing_one = "# Team\n\n" + _fence("project_overview", 1, "Old overview.")
    merged, notice = merge_instruction_file(_FULL, missing_one)
    assert merged is not None and "agent_team" in merged and "Overview." in merged
    assert notice == "added section(s): agent_team"
    # Negative control: a file with every fence, already current, is left alone with no notice.
    assert merge_instruction_file(_FULL, _FULL) == (None, "")


def test_pointer_fence_body_still_refreshes() -> None:
    rendered = _FULL + "\n" + _fence("goose_bridge_entry", 2, "Run goose, new.")
    merged, notice = merge_instruction_file(rendered, _POINTER)
    assert merged == _POINTER.replace("Run goose.", "Run goose, new.")
    assert notice.startswith("pointer instruction file (fences: goose_bridge_entry): 2 template section(s) not added")
