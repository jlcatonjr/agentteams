"""``--sync`` keeps a pointer instruction file a pointer, and says when it adds sections (#461).

A deliberately short instruction file that holds only a framework-owned fence (a goose bridge entry) shares no
template section with the projection. Before the fix every ``--sync`` pass silently grew it into a full instruction
render. A hand-written root ``AGENTS.md`` never reaches that merge: the agents-md ownership guard leaves it alone.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.interop_helpers import merge_instruction_file
from tests.test_sync_low_severity import _PIN, _PIN_INSTR, _change_pin, _commit, _fence, _quiet, _team

from agentteams.multi_sync import run_sync

# The agent_team fence lists the team's real roster (see _team), so the sync's roster check has nothing to report.
_FULL = ("# Team\n\n" + _fence("project_overview", 1, "Overview.") + "\n"
         + _fence("agent_team", 1, "- `@orchestrator`\n- `@helper`"))
_POINTER = "# Agents\n\nSee .github/copilot-instructions.md.\n\n" + _fence("goose_bridge_entry", 2, "Run goose.")
_POINTER_NOTICE = ("pointer instruction file (fences: goose_bridge_entry): 2 template section(s) not added: "
                   "project_overview, agent_team")


def _sync_after_replacing(root: Path, rel: str, text: str) -> list[str]:
    _team(root, instructions=_FULL, frameworks=("copilot-vscode", "claude", "agents-md"))
    target = root / rel
    assert "project_overview" in target.read_text(encoding="utf-8")
    target.write_text(text, encoding="utf-8")
    _change_pin(root)
    _commit(root)
    notices = _quiet(run_sync, root).notices
    assert not any("agent_team fence lists" in n for n in notices), notices
    return notices


def test_pointer_instruction_file_is_not_expanded_by_sync(tmp_path: Path) -> None:
    notices = _sync_after_replacing(tmp_path, ".claude/CLAUDE.md", _POINTER)
    assert (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8") == _POINTER
    assert any(_POINTER_NOTICE in n for n in notices), notices


def test_hand_written_pointer_agents_md_is_left_to_its_owner(tmp_path: Path) -> None:
    # A hand-written root AGENTS.md is not agents-md's to write: the ownership guard leaves it untouched and says
    # so before the fence-merge runs, so it stays a pointer with the guard's notice rather than the pointer notice.
    notices = _sync_after_replacing(tmp_path, "AGENTS.md", _POINTER)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == _POINTER
    assert any("AGENTS.md is hand-written or fenced by another render; --framework agents-md left it untouched"
               in n for n in notices), notices


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
