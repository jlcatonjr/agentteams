"""A goose-owned repo-root AGENTS.md gets Codex's write-policy block (fix 4a/b, 2026-10-16).

Under ``write_policy: "orchestrator-only"`` the top-level Codex session reads its dispatch and proposal
instructions ("Write Policy: You Are the Orchestrator") from AGENTS.md. When goose had written that file (goose
renders without the switch), Codex left it untouched, so the session never saw them. Now:

* goose-owned + the switch on for Codex: only the ``codex_write_policy`` fence is added or refreshed (the way Grok
  upserts ``grok_runtime``), and goose's re-render carries it over;
* hand-written (or any other owner) + the switch on: never written; a loud notice names the missing section and
  the example block written to ``.codex/examples/AGENTS.codex-write-policy-block.md``;
* the switch off: as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import write_policy
from agentteams.emit import emit_all
from agentteams.frameworks import _agents_md_ownership as own
from agentteams.frameworks.codex import (
    WRITE_POLICY_INSTRUCTIONS_HEADING,
    CodexAdapter,
    codex_write_policy_block,
)
from agentteams.frameworks.goose import GOOSE_AGENTS_MD_NOTICE, GooseAdapter
from agentteams.frameworks.grok import GrokAdapter, grok_runtime_block

REL = "../../AGENTS.md"
EXAMPLE = Path(".codex") / "examples" / "AGENTS.codex-write-policy-block.md"
_BODY = "# Team brief\n\n<!-- AGENTTEAMS:BEGIN agent_team v=1 -->\n## Agent Team\n\n- @orchestrator\n" \
        "<!-- AGENTTEAMS:END agent_team -->\n"
GOOSE_TEXT = f"{GOOSE_AGENTS_MD_NOTICE}\n\n{_BODY}\n## Our notes\n\nGoose-side hand notes.\n"
HAND_TEXT = "# Our project\n\nHand-written rules.\n"
_ON = {"write_policy": "orchestrator-only", "framework": "codex"}


def _codex_render(switch: bool = True) -> str:
    manifest = _ON if switch else {"framework": "codex"}
    return CodexAdapter().render_instructions_file(write_policy.apply_instructions(_BODY, manifest), manifest)


def _codex_guard(root: Path, switch: bool = True):
    out = root / ".codex" / "agents"
    out.mkdir(parents=True, exist_ok=True)
    return CodexAdapter().guard_rendered_files([(REL, _codex_render(switch))], out)


def _goose_guard(root: Path):
    out = root / ".goose" / "recipes"
    out.mkdir(parents=True, exist_ok=True)
    return GooseAdapter().guard_rendered_files([(REL, GOOSE_TEXT)], out)


def _emit_codex(root: Path, switch: bool = True, **kw) -> list[str]:
    kept, notices = _codex_guard(root, switch)
    emit_all(kept, output_dir=root / ".codex" / "agents", yes=True, **kw)
    return notices


def test_the_heading_is_the_write_policy_modules_own():
    assert WRITE_POLICY_INSTRUCTIONS_HEADING in write_policy._INSTRUCTIONS_SECTION


def test_the_block_is_one_well_formed_fence_and_none_without_the_switch():
    block = codex_write_policy_block(_codex_render())
    assert own.fence_pattern(own.CODEX_WRITE_POLICY_FENCE_ID).fullmatch(block)
    assert WRITE_POLICY_INSTRUCTIONS_HEADING in block and "--issue-dispatch" in block
    assert codex_write_policy_block(_codex_render(switch=False)) is None
    unfenced = write_policy.apply_instructions("# Brief\n", _ON)        # a body without fences: section unfenced
    assert codex_write_policy_block(unfenced) is not None


def test_goose_then_codex_adds_only_the_write_policy_block(tmp_path: Path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(GOOSE_TEXT, encoding="utf-8")
    notices = _emit_codex(tmp_path, merge=True)
    after = agents_md.read_text(encoding="utf-8")
    assert own.classify_agents_md(agents_md, repo_root=tmp_path) == own.GOOSE      # still goose's file
    # Every goose byte is kept (the merge may seat the new fence after goose's last fence, not at the very end).
    assert own.strip_fence(after, own.CODEX_WRITE_POLICY_FENCE_ID).strip() == GOOSE_TEXT.strip()
    assert after.count(f"AGENTTEAMS:BEGIN {own.CODEX_WRITE_POLICY_FENCE_ID} ") == 1
    assert after.count(WRITE_POLICY_INSTRUCTIONS_HEADING) == 1
    assert "owned by the goose render" in notices[0] and own.CODEX_WRITE_POLICY_FENCE_ID in notices[0]
    _emit_codex(tmp_path, merge=True)                                                # idempotent
    assert agents_md.read_text(encoding="utf-8") == after


def test_a_goose_re_render_keeps_the_block(tmp_path: Path):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(GOOSE_TEXT, encoding="utf-8")
    _emit_codex(tmp_path, merge=True)
    with_block = agents_md.read_text(encoding="utf-8")
    kept, notices = _goose_guard(tmp_path)
    assert notices == [] and kept[0][1].count(f"AGENTTEAMS:BEGIN {own.CODEX_WRITE_POLICY_FENCE_ID} ") == 1
    for mode in ({"merge": True}, {"overwrite": True}):
        emit_all(kept, output_dir=tmp_path / ".goose" / "recipes", yes=True, **mode)
        text = agents_md.read_text(encoding="utf-8")
        assert text.count(WRITE_POLICY_INSTRUCTIONS_HEADING) == 1, mode
        assert codex_write_policy_block(_codex_render()) in text, mode
    assert agents_md.read_text(encoding="utf-8").count("Goose-side hand notes.") == 1
    assert with_block.count(own.CODEX_WRITE_POLICY_FENCE_ID) == 2


def test_goose_keeps_both_co_owner_blocks_in_their_on_disk_order(tmp_path: Path):
    agents_md = tmp_path / "AGENTS.md"
    block = codex_write_policy_block(_codex_render())
    agents_md.write_text(f"{GOOSE_TEXT}\n{block}\n\n{grok_runtime_block()}\n", encoding="utf-8")
    kept, _ = _goose_guard(tmp_path)
    text = kept[0][1]
    assert text.index(own.CODEX_WRITE_POLICY_FENCE_ID) < text.index(own.GROK_FENCE_ID)
    assert text.count(f"AGENTTEAMS:BEGIN {own.GROK_FENCE_ID} ") == 1


def test_a_hand_file_holding_only_the_pasted_block_is_still_not_goose_s(tmp_path: Path):
    block = codex_write_policy_block(_codex_render())
    (tmp_path / "AGENTS.md").write_text(f"{HAND_TEXT}\n{block}\n", encoding="utf-8")
    kept, notices = _goose_guard(tmp_path)
    assert kept == [] and "not generated by goose" in notices[0]


@pytest.mark.parametrize("mode", [{}, {"merge": True}, {"overwrite": True}])
def test_a_hand_written_file_is_untouched_with_a_loud_notice_and_an_example(tmp_path: Path, mode: dict):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(HAND_TEXT, encoding="utf-8")
    notices = _emit_codex(tmp_path, **mode)
    assert agents_md.read_text(encoding="utf-8") == HAND_TEXT
    example = tmp_path / EXAMPLE
    assert example.read_text(encoding="utf-8").strip() == codex_write_policy_block(_codex_render())
    assert len(notices) == 1 and "MISSING" in notices[0]
    assert "Write Policy: You Are the Orchestrator" in notices[0] and str(EXAMPLE) in notices[0]


def test_a_symlinked_examples_dir_gets_no_example(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text(HAND_TEXT, encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "examples").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    kept, notices = _codex_guard(tmp_path)
    assert kept == [] and "is a symlink" in notices[0]


def test_a_hand_file_with_the_pasted_block_is_not_called_missing(tmp_path: Path):
    block = codex_write_policy_block(_codex_render())
    (tmp_path / "AGENTS.md").write_text(f"{HAND_TEXT}\n{block}\n", encoding="utf-8")
    kept, notices = _codex_guard(tmp_path)
    assert "MISSING" not in notices[0] and "holds a codex_write_policy block" in notices[0]
    assert [rel for rel, _ in kept] == ["../examples/AGENTS.codex-write-policy-block.md"]


@pytest.mark.parametrize("existing, expected", [
    (GOOSE_TEXT, "generated by agentteams for Goose from the same team brief; Codex reads it as-is (left untouched)."),
    (HAND_TEXT, "existing AGENTS.md was not generated for Codex; left untouched"),
])
def test_the_switch_off_is_unchanged(tmp_path: Path, existing: str, expected: str):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(existing, encoding="utf-8")
    notices = _emit_codex(tmp_path, switch=False, merge=True)
    assert agents_md.read_text(encoding="utf-8") == existing
    assert not (tmp_path / EXAMPLE).exists()
    assert len(notices) == 1 and expected in notices[0]


def test_grok_still_reads_a_grok_file_as_is(tmp_path: Path):
    grok_out = tmp_path / ".grok" / "agents"
    grok_out.mkdir(parents=True)
    kept, _ = GrokAdapter().guard_rendered_files([(REL, GrokAdapter().render_instructions_file("# T\n", {}))],
                                                 grok_out)
    (tmp_path / "AGENTS.md").write_text(kept[0][1], encoding="utf-8")
    kept, notices = _codex_guard(tmp_path, switch=False)
    assert kept == [] and "for Grok Build" in notices[0]
