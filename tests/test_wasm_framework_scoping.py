"""Fail-closed tool translation for ``--framework wasm`` (WASM plan P08 §4.2, ``frameworks/_wasm_tool_scoping.py``)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams.frameworks import _wasm_tool_scoping as S
from agentteams.frameworks.wasm import WasmAdapter
from agentteams.wasm_agent.team import parse_agent_file
from agentteams.yaml_frontmatter import parse_yaml_front_matter

_TEMPLATES = Path(__file__).resolve().parents[1] / "agentteams" / "templates"
FULL = dict(has_read_mount=True, has_scratch=True, has_delegates=True)
NONE = dict(has_read_mount=False, has_scratch=False, has_delegates=False)


@pytest.mark.parametrize("scopes, grant, expected", [
    (["read"], FULL, ("read_file", "list_dir")),
    (["search"], FULL, ("list_dir", "search_files")),
    (["edit"], FULL, ("write_file",)),
    (["execute"], NONE, ("run_code",)),                          # never dropped: a fresh compute sandbox
    (["agent"], FULL, ("delegate",)),
    (["read", "search", "edit", "execute", "agent"], FULL, S.WASM_TOOLS),
    (["read", "edit"], dict(FULL, has_scratch=False), ("read_file", "list_dir")),  # edit needs /scratch
    (["read"], dict(NONE, has_scratch=True), ("read_file", "list_dir")),       # scratch alone is readable
    (["read", "search", "edit", "agent"], NONE, ()),
])
def test_translation_table(scopes: list[str], grant: dict, expected: tuple[str, ...]) -> None:
    assert S.translate_wasm_tools(scopes, **grant)[0] == expected


@pytest.mark.parametrize("scope", ["todo", "retrieval"])
def test_always_dropped_with_a_notice(scope: str) -> None:
    tools, notices = S.translate_wasm_tools(["execute", scope], agent="a", **FULL)
    assert tools == ("run_code",) and len(notices) == 1 and f"'{scope}'" in notices[0]


def test_edit_without_scratch_is_dropped_with_a_notice() -> None:
    tools, notices = S.translate_wasm_tools(["edit"], agent="a", **dict(FULL, has_scratch=False))
    assert tools == () and "write_file" in notices[0] and "/scratch" in notices[0]


def test_one_notice_line_per_agent_and_list_dir_only_once() -> None:
    _, notices = S.translate_wasm_tools(["read", "search", "edit", "todo"], agent="a", **NONE)
    assert len(notices) == 1 and notices[0].count("list_dir") == 1


def test_no_tools_block_is_an_empty_toolset_never_a_default() -> None:
    tools, notices = S.translate_wasm_tools(None, agent="a", **FULL)
    assert tools == () and "empty toolset" in notices[0]
    out = WasmAdapter().render_agent_file("---\nname: X\ndescription: \"d\"\n---\nbody\n", "x", {})
    assert parse_agent_file("x", out).tools == ()


@pytest.mark.parametrize("scopes", [["web"], ["read", "Bash"], ["mcp__x__y"], ["agentteams_runner/*"]])
def test_unknown_scope_refuses(scopes: list[str]) -> None:
    with pytest.raises(S.WasmToolScopeError):
        S.translate_wasm_tools(scopes, **FULL)


def test_a_malformed_tools_line_refuses() -> None:
    with pytest.raises(S.WasmToolScopeError):
        S.parse_tools_line("tools:\n  - read\n")
    with pytest.raises(S.WasmToolScopeError):
        S.parse_tools_line("tools: ['read', 'Bash(git:*)']")
    assert S.parse_tools_line("name: x") is None and S.parse_tools_line(None) is None


def test_grant_supports_matches_the_table() -> None:
    for tool in S.WASM_TOOLS:
        assert S.grant_supports(tool, **FULL)
    assert [t for t in S.WASM_TOOLS if S.grant_supports(t, **NONE)] == ["run_code"]
    assert not S.grant_supports("shell", **FULL)


@pytest.mark.parametrize("path", sorted((_TEMPLATES / "universal").glob("*.template.md"))
                         + sorted((_TEMPLATES / "domain").glob("*.template.md"))
                         + [_TEMPLATES / "workstream-expert.template.md"], ids=lambda p: p.name)
def test_every_template_renders_within_the_vocabulary(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    yaml_text, _ = parse_yaml_front_matter(text)
    if yaml_text is None or not re.search(r"^name:", yaml_text, re.MULTILINE):
        pytest.skip("not an agent template")
    slug = path.name.split(".")[0]
    out = WasmAdapter().render_agent_file(text, slug, {"project_name": "Demo", "agent_slug_list": [slug]})
    agent = parse_agent_file(slug, out)
    assert set(agent.tools) <= set(S.WASM_TOOLS) and set(agent.tools) <= {"run_code"}   # compute-only default
