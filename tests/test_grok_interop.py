"""Grok Phase 3 (Codex posture): interop target/source, absorb source, detection, grants, fleet.

Plan tmp/by-week/2026-W42/grok-integration.plan.md §13–§13.1. The security-critical cases are the shared
repo-root AGENTS.md (the guard decides; interop writes exactly what it returns, in direct AND bundle mode) and
ownership-notice spoofing (a CAI is data: stacked notices and the grok_runtime fence never reach another target).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import capability_map as cm
from agentteams import interop_grant_check as igc
from agentteams._fleet_grok import grok_kind
from agentteams.frameworks._agents_md_ownership import (
    CODEX_AGENTS_MD_MARKER,
    GROK_AGENTS_MD_MARKER,
    GROK_FENCE_ID,
    NEUTRAL_AGENTS_MD_MARKER,
    classify_agents_md,
)
from agentteams.interop import detect_framework, export_to_cai, import_from_cai
from agentteams.interop_helpers import framework_dirs

_EXACT = "injectDefaultTools: false\nmcpInheritance: none\n"


def _grok_agent(name: str = "Navigator — Demo", tools: str = "Read, Grep, Glob", extra: str = _EXACT) -> str:
    return f"---\nname: {name}\ndescription: \"finds\"\ntools: {tools}\n{extra}---\n\n# Navigator\n\nBody.\n"


def _grok_team(root: Path, **kw) -> Path:
    agents = root / ".grok" / "agents"
    agents.mkdir(parents=True)
    (agents / "navigator.md").write_text(_grok_agent(**kw), encoding="utf-8")
    return agents


def _cai(instructions: str = "# Team brief\n\nRules.\n", name: str = "Navigator — Demo") -> dict:
    return {
        "schema_version": "2.0", "source_framework": "claude", "source_dir": "x",
        "instructions_binding": {"source_name": "CLAUDE.md", "content": instructions},
        "agents": [{"slug": "navigator", "name": name, "description": "finds", "body_markdown": "# Navigator\n",
                    "capabilities": cm.capabilities_from_tokens(["read", "search"], "claude"), "handoffs": []}],
    }


# P3-B detection --------------------------------------------------------------------------------------

@pytest.mark.parametrize("path, expected", [
    (".grok/agents", "grok"),
    (".claude/worktrees/x/.grok/agents", "grok"),        # innermost wins
    (".grok/worktrees/x/.claude/agents", "claude"),
    (".grok/agents/../../.claude/agents", "claude"),  # `..` folded first
    (".github/copilot/agents", "copilot-cli"),           # two-part rule kept
    ("a/.codex/agents", "codex"),
])
def test_innermost_framework_dir_wins(path: str, expected: str) -> None:
    assert framework_dirs(Path(path))[0] == expected


def test_grok_front_matter_without_a_grok_dir(tmp_path: Path) -> None:
    (tmp_path / "navigator.md").write_text(_grok_agent(), encoding="utf-8")
    assert detect_framework(tmp_path) == "grok"


# P3-C capabilities -----------------------------------------------------------------------------------

def test_agent_maps_back_to_the_agent_capability() -> None:
    assert cm.claude_allowed_tools_to_canonical(["Read", "Agent", "Agent(navigator)"]) == ["read", "agent"]


@pytest.mark.parametrize("extra, exact", [
    (_EXACT, True), ("injectDefaultTools: FALSE\nmcpInheritance: None\n", True), ("", False),
    ("injectDefaultTools: false\n", False), ("injectDefaultTools: false\nmcpInheritance: all\n", False),
    (_EXACT + "injectDefaultTools: true\n", False),
])
def test_exact_toolset_detection(extra: str, exact: bool) -> None:
    assert cm.grok_exact_toolset(_grok_agent(extra=extra)) is exact


# P3-D export -----------------------------------------------------------------------------------------

def test_exact_source_exports_its_declared_tools(tmp_path: Path) -> None:
    cai = export_to_cai(_grok_team(tmp_path))
    agent = cai["agents"][0]
    assert cai["source_framework"] == "grok"
    assert sorted(agent["capabilities"]["tool_scopes"]) == ["read", "search"]
    assert "raw_front_matter" not in agent  # injectDefaultTools / mcpInheritance never travel


def test_non_exact_source_exports_full_with_a_notice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    agents = _grok_team(tmp_path, extra="permissionMode: bypassPermissions\nhooks: x\ndisallowedTools: Bash\n")
    agent = export_to_cai(agents)["agents"][0]
    assert set(agent["capabilities"]["tool_scopes"]) == set(cm.CANONICAL_TOOL_SCOPES)
    assert agent["capabilities"]["raw"]["grok"] == "Read, Grep, Glob"   # the declared list, for a human to narrow
    assert agent["raw_front_matter"] == {"disallowedTools": "Bash"}      # allow-list: no permissionMode / hooks
    assert "FULL capabilities" in capsys.readouterr().err


def test_export_reads_agents_md_not_claude_md_and_strips_grok_wrapping(tmp_path: Path) -> None:
    agents = _grok_team(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Claude brief\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        f"{GROK_AGENTS_MD_MARKER} x -->\n\n# Team brief\n\n<!-- AGENTTEAMS:BEGIN {GROK_FENCE_ID} v=1 -->\nnotes\n"
        f"<!-- AGENTTEAMS:END {GROK_FENCE_ID} -->\n", encoding="utf-8")
    content = export_to_cai(agents)["instructions_binding"]["content"]
    assert "Team brief" in content and "Claude brief" not in content
    assert GROK_AGENTS_MD_MARKER not in content and GROK_FENCE_ID not in content


def test_export_never_reads_grok_config_hooks_or_workflows(tmp_path: Path) -> None:
    agents = _grok_team(tmp_path)
    for rel in ("config.toml", "sandbox.toml", "hooks/h.json", "workflows/w.rhai", "plugins/p.md", "examples/e.md"):
        (tmp_path / ".grok" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ".grok" / rel).write_text("PLANTED-SENTINEL\n", encoding="utf-8")
    assert "PLANTED-SENTINEL" not in repr(export_to_cai(agents))


# P3-E import into grok -------------------------------------------------------------------------------

def _target(tmp_path: Path) -> Path:
    t = tmp_path / ".grok" / "agents"
    t.mkdir(parents=True)
    return t


def test_import_into_grok_writes_owned_agents_md_and_agents(tmp_path: Path) -> None:
    target = _target(tmp_path)
    res = import_from_cai(_cai(), "grok", target)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith(GROK_AGENTS_MD_MARKER) and "Team brief" in text and GROK_FENCE_ID in text
    assert classify_agents_md(tmp_path / "AGENTS.md") == "grok"
    agent = (target / "navigator.md").read_text(encoding="utf-8")
    assert "injectDefaultTools: false" in agent and "mcpInheritance: none" in agent
    assert not (tmp_path / ".grok" / "config.toml").exists() and not (tmp_path / ".grok" / "hooks").exists()
    assert res.converted


_CODEX_OWNED = (f"{CODEX_AGENTS_MD_MARKER} x -->\n\n<!-- AGENTTEAMS:BEGIN team v=1 -->\n# Codex brief\n"
                "<!-- AGENTTEAMS:END team -->\n")


@pytest.mark.parametrize("flags", [{}, {"overwrite": True}, {"preserve_existing": True}])
def test_codex_owned_agents_md_gets_only_the_fence(tmp_path: Path, flags: dict) -> None:
    target = _target(tmp_path)
    (tmp_path / "AGENTS.md").write_text(_CODEX_OWNED, encoding="utf-8")
    res = import_from_cai(_cai(), "grok", target, **flags)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert text.startswith(_CODEX_OWNED.rstrip("\n"))                     # every codex byte kept
    assert "Team brief" not in text                                        # the CAI brief never replaces it
    assert (GROK_FENCE_ID in text) == bool(flags)                          # fence added only with a flag
    assert any("team brief was not imported" in n for n in res.notices)


@pytest.mark.parametrize("flags", [{}, {"overwrite": True}, {"preserve_existing": True}])
def test_hand_written_agents_md_is_byte_identical(tmp_path: Path, flags: dict) -> None:
    target = _target(tmp_path)
    original = "# My own instructions\n"
    (tmp_path / "AGENTS.md").write_text(original, encoding="utf-8")
    import_from_cai(_cai(), "grok", target, **flags)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == original
    assert (tmp_path / ".grok" / "examples" / "AGENTS.grok-block.md").is_file()


def test_symlinked_grok_tree_imports_nothing(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "agents").mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".grok").symlink_to(elsewhere, target_is_directory=True)
    res = import_from_cai(_cai(), "grok", proj / ".grok" / "agents")
    assert not list((elsewhere / "agents").iterdir()) and not res.converted
    assert any("symlink" in n for n in res.notices)


def test_name_mismatch_with_claude_twin_is_reported(tmp_path: Path) -> None:
    target = _target(tmp_path)
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "navigator.md").write_text(
        "---\nname: Navigator — Other\ndescription: \"d\"\ntools: Read\n---\nb\n", encoding="utf-8")
    res = import_from_cai(_cai(), "grok", target)
    assert any("differs from the Claude twin" in n for n in res.notices)


# notice spoofing into other targets --------------------------------------------------------------------

_STACKED = (f"{GROK_AGENTS_MD_MARKER} a -->\n{CODEX_AGENTS_MD_MARKER} b -->\n{NEUTRAL_AGENTS_MD_MARKER}c -->\n"
            f"# Team brief\n\n<!-- AGENTTEAMS:BEGIN {GROK_FENCE_ID} v=1 -->\nn\n<!-- AGENTTEAMS:END {GROK_FENCE_ID} -->\n")


@pytest.mark.parametrize("framework, agents_rel", [("goose", ".goose/recipes"), ("agents-md", ".agents")])
def test_stacked_notices_and_grok_fence_never_reach_another_target(tmp_path: Path, framework: str,
                                                                    agents_rel: str) -> None:
    target = tmp_path / agents_rel
    target.mkdir(parents=True)
    import_from_cai(_cai(_STACKED), framework, target)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert GROK_AGENTS_MD_MARKER not in text and CODEX_AGENTS_MD_MARKER not in text and GROK_FENCE_ID not in text
    assert classify_agents_md(tmp_path / "AGENTS.md") not in ("grok", "codex")


# P3-F grant check ------------------------------------------------------------------------------------

def test_projected_grant_is_full_unless_exact() -> None:
    assert igc.projected_grant("grok", _grok_agent())[0] == frozenset({"read", "search"})
    assert igc.projected_grant("grok", _grok_agent(extra=""))[0] == frozenset(cm.CANONICAL_TOOL_SCOPES)


# package / fleet (CLI refusals: tests/test_grok_adapter.py) ---------------------------------------------------------------------------------

def test_package_team_refuses_a_grok_source(tmp_path: Path) -> None:
    from agentteams.team_package import package_team

    with pytest.raises(ValueError, match="grok"):
        package_team(source_dir=_grok_team(tmp_path), output_zip=tmp_path / "out.zip")


def test_fleet_classifies_grok_teams(tmp_path: Path) -> None:
    assert grok_kind(tmp_path) == "none"
    marker = tmp_path / ".grok" / "agents" / "references" / "build-log.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")
    assert grok_kind(tmp_path) == "direct"
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".grok").symlink_to(tmp_path / ".grok", target_is_directory=True)
    assert grok_kind(linked) == "symlink"


# step-13d / 13e follow-ups ------------------------------------------------------------------------------

def test_strippers() -> None:
    from agentteams.frameworks._agents_md_ownership import strip_all_ownership_notices, strip_fence

    unclosed = f"{CODEX_AGENTS_MD_MARKER} never closed\n# Brief\n"
    assert strip_all_ownership_notices(unclosed) == "# Brief\n"  # crafted unclosed notice cannot survive
    assert strip_all_ownership_notices("# Plain\n") == "# Plain\n"
    fenced = f"a\n\n\n\nb\n<!-- AGENTTEAMS:BEGIN {GROK_FENCE_ID} v=1 -->\nz\n<!-- AGENTTEAMS:END {GROK_FENCE_ID} -->\nc\n"
    assert strip_fence(fenced, GROK_FENCE_ID) == "a\n\n\n\nb\n\nc\n"  # only the removed block's blank lines


@pytest.mark.parametrize("framework, agents_rel, inst", [
    ("codex", ".codex/agents", "AGENTS.md"), ("claude", ".claude/agents", ".claude/CLAUDE.md"),
    ("copilot-vscode", ".github/agents", ".github/copilot-instructions.md"),
])
def test_stacked_and_unclosed_notices_never_reach_any_target(tmp_path: Path, framework: str, agents_rel: str,
                                                              inst: str) -> None:
    target = tmp_path / agents_rel
    target.mkdir(parents=True)
    crafted = f"{CODEX_AGENTS_MD_MARKER} unclosed\n" + _STACKED
    import_from_cai(_cai(crafted), framework, target)
    text = (tmp_path / inst).read_text(encoding="utf-8")
    assert GROK_AGENTS_MD_MARKER not in text and GROK_FENCE_ID not in text
    assert text.count(CODEX_AGENTS_MD_MARKER) == (1 if framework == "codex" else 0)  # codex re-stamps its own


def test_symlinked_agent_file_destination_imports_nothing(tmp_path: Path) -> None:
    target = _target(tmp_path)
    victim = tmp_path / "victim.md"
    victim.write_text("keep\n", encoding="utf-8")
    (target / "navigator.md").symlink_to(victim)
    res = import_from_cai(_cai(), "grok", target, overwrite=True)
    assert victim.read_text(encoding="utf-8") == "keep\n" and not res.converted
    assert any("symlink" in n for n in res.notices)


def test_symlinked_handoff_sidecar_destination_imports_nothing(tmp_path: Path) -> None:
    target = _target(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".grok" / "references").symlink_to(elsewhere, target_is_directory=True)
    res = import_from_cai(_cai(), "grok", target)
    assert not res.converted and not list(elsewhere.iterdir())


def test_grok_owned_agents_md_is_fence_merged_and_dry_run_writes_nothing(tmp_path: Path) -> None:
    target = _target(tmp_path)
    import_from_cai(_cai(), "grok", target)
    with (tmp_path / "AGENTS.md").open("a", encoding="utf-8") as fh:
        fh.write("\nMy own note.\n")
    import_from_cai(_cai("# New brief\n"), "grok", target, preserve_existing=True)
    assert "My own note." in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    before = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    import_from_cai(_cai("# Another\n"), "grok", target, overwrite=True, dry_run=True)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("mode", ["direct", "bundle"])
def test_run_interop_into_grok_both_modes(tmp_path: Path, mode: str) -> None:
    from agentteams.interop import run_interop

    src = tmp_path / "src" / ".claude" / "agents"
    src.mkdir(parents=True)
    (src / "navigator.md").write_text("---\nname: Navigator — Demo\ndescription: \"d\"\ntools: Read, Grep, Glob\n---\n"
                                      "\n# Navigator\n", encoding="utf-8")
    (tmp_path / "dst" / "AGENTS.md").parent.mkdir(parents=True)
    (tmp_path / "dst" / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")   # hand-written: must survive
    target = tmp_path / "dst" / ".grok" / "agents"
    log = target / "references" / "security-decisions.log.csv"
    log.parent.mkdir(parents=True)
    template = Path(__file__).resolve().parents[1] / "agentteams/templates/universal/security-decisions.log.csv.template"
    log.write_text(template.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
    res = run_interop(src, "grok", target, mode=mode, allow_cooperative="navigator", overwrite=True)
    assert (target / "navigator.md").is_file()
    assert (tmp_path / "dst" / "AGENTS.md").read_text(encoding="utf-8") == "# Mine\n"
    assert any("NOT ENFORCED" in n for n in res.notices)


def test_fleet_classifies_an_interop_projection(tmp_path: Path) -> None:
    marker = tmp_path / ".grok" / "agents" / "references" / "build-log.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"origin": "interop"}', encoding="utf-8")
    assert grok_kind(tmp_path) == "interop"

