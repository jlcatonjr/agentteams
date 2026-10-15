"""Goose and the shared repo-root AGENTS.md (GROK-RETRO-R4, expert reference G13).

Goose used to write AGENTS.md with no ownership marker and no guard, so it overwrote (or under
``--merge`` re-fenced) a file owned by Codex, Grok or a person. The rule now lives in the pinned
``_agents_md_ownership.goose_agents_md_decision``; goose, convert and interop all go through it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import build_team
from agentteams import capability_map as cm
from agentteams.cli import security_gate
from agentteams.convert import convert_team
from agentteams.frameworks import _agents_md_ownership as own
from agentteams.frameworks.codex import CodexAdapter
from agentteams.frameworks.goose import GOOSE_AGENTS_MD_NOTICE, GooseAdapter
from agentteams.frameworks.grok import GROK_AGENTS_MD_NOTICE, GrokAdapter, grok_runtime_block
from agentteams.interop import import_from_cai

REL = "../../AGENTS.md"
GOOSE_TEXT = f"{GOOSE_AGENTS_MD_NOTICE}\n\n# Goose brief\n"
CODEX_TEXT = f"{own.CODEX_AGENTS_MD_MARKER} x -->\n\n# Codex brief\n"
GROK_TEXT = f"{GROK_AGENTS_MD_NOTICE}\n\n# Grok brief\n\n{grok_runtime_block()}\n"
NEUTRAL_TEXT = f"{own.NEUTRAL_AGENTS_MD_MARKER} x -->\n\n# Neutral brief\n"
HAND_TEXT = "# Our project\n\nHand-written rules.\n"
LEGACY_FENCED = ("# Brief\n\n<!-- AGENTTEAMS:BEGIN constitutional_rules v=1 -->\nrules\n"
                 "<!-- AGENTTEAMS:END constitutional_rules -->\n")


def _recipes(root: Path) -> Path:
    out = root / ".goose" / "recipes"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _guard(root: Path):
    return GooseAdapter().guard_rendered_files([(REL, GOOSE_TEXT)], _recipes(root))


def _render(text: str = "# Goose brief\n") -> str:
    return GooseAdapter().render_instructions_file(text, {})


# Classification -----------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    (GOOSE_TEXT, own.GOOSE), (NEUTRAL_TEXT, own.AGENTS_MD), (CODEX_TEXT, own.CODEX), (GROK_TEXT, own.GROK),
    (LEGACY_FENCED, own.FENCED), (HAND_TEXT, own.FOREIGN),
    (f"# Title\n\n{own.GOOSE_AGENTS_MD_MARKER} quoted later -->\n", own.FOREIGN),  # only a leading marker counts
])
def test_classification(tmp_path: Path, text: str, expected: str) -> None:
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    assert own.classify_agents_md(tmp_path / "AGENTS.md", repo_root=tmp_path) == expected


def test_goose_and_neutral_markers_do_not_share_a_prefix() -> None:
    markers = (own.GOOSE_AGENTS_MD_MARKER, own.NEUTRAL_AGENTS_MD_MARKER, own.CODEX_AGENTS_MD_MARKER,
               own.GROK_AGENTS_MD_MARKER)
    for a in markers:
        for b in markers:
            assert a == b or not a.startswith(b)


def test_goose_render_carries_its_notice_once() -> None:
    rendered = _render()
    assert rendered.startswith(own.GOOSE_AGENTS_MD_MARKER)
    assert _render(rendered).count(own.GOOSE_AGENTS_MD_MARKER) == 1   # goose -> goose re-render


def test_stacked_or_spoofed_goose_notices_are_stripped() -> None:
    stacked = f"{GOOSE_AGENTS_MD_NOTICE}\n{GOOSE_AGENTS_MD_NOTICE}\n{own.GOOSE_AGENTS_MD_MARKER} unclosed\n# Body\n"
    assert own.strip_all_ownership_notices(stacked).strip() == "# Body"


# The decision table, through goose's guard ----------------------------------------------------------

def test_absent_is_written(tmp_path: Path) -> None:
    kept, notices = _guard(tmp_path)
    assert kept == [(REL, GOOSE_TEXT)] and notices == []


def test_own_file_is_rewritten(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(GOOSE_TEXT + "old\n", encoding="utf-8")
    kept, notices = _guard(tmp_path)
    assert kept == [(REL, GOOSE_TEXT)] and notices == []


@pytest.mark.parametrize("text, owner", [(CODEX_TEXT, "OpenAI Codex"), (GROK_TEXT, "Grok Build"),
                                         (NEUTRAL_TEXT, "--framework agents-md")])
def test_another_frameworks_file_is_left_with_a_warning(tmp_path: Path, text: str, owner: str) -> None:
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    kept, notices = _guard(tmp_path)
    assert kept == [] and owner in notices[0] and "remove AGENTS.md and re-render" in notices[0]


@pytest.mark.parametrize("text", [HAND_TEXT, f"{HAND_TEXT}\n{grok_runtime_block()}\n"])
def test_hand_written_file_is_left_with_a_warning(tmp_path: Path, text: str) -> None:
    # The second case: a hand file the operator pasted Grok's block into (grok.py's FOREIGN advice). Only
    # grok_runtime fences it, so adopting it would hand a hand-written file to goose.
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    kept, notices = _guard(tmp_path)
    assert kept == [] and "legacy unfenced goose render" in notices[0] and "re-render" in notices[0]


def test_legacy_fenced_goose_render_is_adopted_with_a_notice(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(LEGACY_FENCED, encoding="utf-8")
    kept, notices = _guard(tmp_path)
    assert kept == [(REL, GOOSE_TEXT)] and "adopted" in notices[0]


def test_unsafe_target_is_never_written(tmp_path: Path) -> None:
    (tmp_path / "elsewhere.md").write_text(HAND_TEXT, encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(tmp_path / "elsewhere.md")
    kept, notices = _guard(tmp_path)
    assert kept == [] and "symlink" in notices[0]


def test_a_symlinked_recipes_dir_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / ".goose").symlink_to(real)
    (real / "recipes").mkdir()
    kept, notices = GooseAdapter().guard_rendered_files([(REL, GOOSE_TEXT)], tmp_path / ".goose" / "recipes")
    assert kept == [] and "symlink" in notices[0]


def test_other_files_pass_through_unchanged(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(HAND_TEXT, encoding="utf-8")
    files = [("../../.goosehints", "hints\n"), ("orchestrator.yaml", "title: x\n")]
    kept, _ = GooseAdapter().guard_rendered_files([*files, (REL, GOOSE_TEXT)], _recipes(tmp_path))
    assert kept == files


# Render orders --------------------------------------------------------------------------------------

def test_goose_then_grok_then_goose_keeps_the_grok_fence(tmp_path: Path) -> None:
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(GOOSE_TEXT, encoding="utf-8")
    grok_out = tmp_path / ".grok" / "agents"
    grok_out.mkdir(parents=True)
    kept, notices = GrokAdapter().guard_rendered_files([(REL, GROK_TEXT)], grok_out)
    assert "owned by the goose render" in notices[0]
    agents_md.write_text(kept[0][1], encoding="utf-8")
    assert agents_md.read_text(encoding="utf-8").startswith(GOOSE_TEXT.rstrip("\n"))  # grok kept goose's bytes
    kept, notices = _guard(tmp_path)
    assert notices == []
    assert kept[0][1].startswith(GOOSE_TEXT.rstrip("\n")) and kept[0][1].count(own.GROK_FENCE_ID) == 2
    agents_md.write_text(kept[0][1], encoding="utf-8")
    kept, _ = _guard(tmp_path)                                 # a second cycle neither drops nor duplicates it
    assert kept[0][1].count(f"AGENTTEAMS:BEGIN {own.GROK_FENCE_ID}") == 1


def test_a_duplicated_grok_fence_is_skipped_not_dropped(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(f"{GOOSE_TEXT}\n{grok_runtime_block()}\n{grok_runtime_block()}\n",
                                        encoding="utf-8")
    kept, notices = _guard(tmp_path)
    assert kept == [] and "duplicate or unbalanced" in notices[0]


def test_codex_skips_a_goose_file_and_names_it(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(GOOSE_TEXT, encoding="utf-8")
    codex_out = tmp_path / ".codex" / "agents"
    codex_out.mkdir(parents=True)
    kept, notices = CodexAdapter().guard_rendered_files([(REL, CODEX_TEXT)], codex_out)
    assert kept == [] and "generated by agentteams for Goose" in notices[0]


# Convert and interop reach the same rule (@security step-10 conditions 1 and 2) -----------------------

def _copilot_source(root: Path) -> Path:
    agents = root / "src" / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "navigator.agent.md").write_text(
        "---\nname: navigator\ndescription: \"finds\"\ntools: ['read']\n---\n\n# Navigator\n", encoding="utf-8")
    (agents.parent / "copilot-instructions.md").write_text("# Source brief\n", encoding="utf-8")
    return agents


@pytest.mark.parametrize("text", [CODEX_TEXT, HAND_TEXT])
def test_convert_overwrite_never_replaces_a_file_goose_does_not_own(tmp_path: Path, text: str) -> None:
    project = tmp_path / "proj"
    (project / "AGENTS.md").parent.mkdir(parents=True)
    (project / "AGENTS.md").write_text(text, encoding="utf-8")
    res = convert_team(_copilot_source(tmp_path), _recipes(project), "goose",
                       project_manifest={"project_name": "P"}, overwrite=True)
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == text
    assert any("untouched" in s for s in res.skipped)


@pytest.mark.parametrize("text", [GOOSE_TEXT, HAND_TEXT])
def test_convert_overwrite_to_codex_never_replaces_a_file_codex_does_not_own(tmp_path: Path, text: str) -> None:
    # Convert now runs every adapter's instructions guard, codex's included (CHANGELOG behaviour change).
    project = tmp_path / "proj"
    codex_out = project / ".codex" / "agents"
    codex_out.mkdir(parents=True)
    (project / "AGENTS.md").write_text(text, encoding="utf-8")
    convert_team(_copilot_source(tmp_path), codex_out, "codex", project_manifest={"project_name": "P"},
                 overwrite=True)
    assert (project / "AGENTS.md").read_text(encoding="utf-8") == text


def test_convert_writes_a_goose_owned_file(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    convert_team(_copilot_source(tmp_path), _recipes(project), "goose", project_manifest={"project_name": "P"})
    text = (project / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith(own.GOOSE_AGENTS_MD_MARKER) and "Source brief" in text


def _cai(instructions: str = "# Team brief\n") -> dict:
    return {
        "schema_version": "2.0", "source_framework": "claude", "source_dir": "x",
        "instructions_binding": {"source_name": "CLAUDE.md", "content": instructions},
        "agents": [{"slug": "navigator", "name": "Navigator", "description": "finds", "body_markdown": "# N\n",
                    "capabilities": cm.capabilities_from_tokens(["read", "search"], "claude"), "handoffs": []}],
    }


@pytest.mark.parametrize("flags", [{}, {"overwrite": True}, {"preserve_existing": True}])
@pytest.mark.parametrize("text", [CODEX_TEXT, HAND_TEXT])
def test_interop_never_replaces_a_file_goose_does_not_own(tmp_path: Path, text: str, flags: dict) -> None:
    (tmp_path / "AGENTS.md").write_text(text, encoding="utf-8")
    res = import_from_cai(_cai(), "goose", _recipes(tmp_path), **flags)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == text
    assert any("untouched" in n for n in res.notices)


def test_interop_writes_goose_notice_and_keeps_the_grok_fence(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(f"{GOOSE_TEXT}\n{grok_runtime_block()}\n", encoding="utf-8")
    import_from_cai(_cai(), "goose", _recipes(tmp_path), overwrite=True)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith(own.GOOSE_AGENTS_MD_MARKER) and "Team brief" in text
    assert text.count(f"AGENTTEAMS:BEGIN {own.GROK_FENCE_ID}") == 1


def test_a_goose_owned_file_keeps_hand_notes_under_merge(tmp_path: Path, monkeypatch) -> None:
    # @security step-10 condition 3: "write in full" must still go through --merge.
    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    brief = Path(__file__).resolve().parent.parent / "examples" / "software-project" / "brief.json"
    args = ["--description", str(brief), "--framework", "goose", "--output", str(tmp_path), "--yes", "--no-scan",
            "--security-offline"]
    assert build_team.main(args) == 0
    agents_md = tmp_path / "AGENTS.md"
    assert own.classify_agents_md(agents_md, repo_root=tmp_path) == own.GOOSE
    agents_md.write_text(agents_md.read_text(encoding="utf-8") + "\nHAND-NOTE-OUTSIDE-FENCES\n", encoding="utf-8")
    assert build_team.main([*args, "--update", "--merge"]) == 0
    text = agents_md.read_text(encoding="utf-8")
    assert "HAND-NOTE-OUTSIDE-FENCES" in text and text.count(own.GOOSE_AGENTS_MD_MARKER) == 1
