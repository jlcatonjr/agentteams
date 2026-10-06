"""goose_tool_scoping "grant": Goose recipe extensions derived from each agent's declared tools.

Phase 2 of references/plans/goose-read-only-agents.plan.md. Legacy mode (the default) must stay
byte-identical; grant mode must give readers only the read-only agentteams_readfs server, give writers
exactly the developer tools they declare, always disable the auto-added analyze extension next to
developer, and keep summon (delegation, load handoffs) to agents that declare `agent`.
"""

from __future__ import annotations

import pytest

from agentteams import analyze
from agentteams.frameworks import goose_tool_scoping as scoping
from agentteams.frameworks.goose import GooseAdapter
from agentteams.frameworks.goose_recipe_validate import _validate_recipe_yaml

_ADAPTER = GooseAdapter()


def _manifest(mode: str | None) -> dict:
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}]}
    if mode:
        desc["goose_tool_scoping"] = mode
    return analyze.build_manifest(desc, framework="goose")


def _agent(tools: str, *, handoff_to: str | None = None) -> str:
    handoffs = (f"handoffs:\n  - label: Next\n    agent: {handoff_to}\n    prompt: \"go\"\n    send: false\n"
                if handoff_to else "")
    return f"---\nname: X\ndescription: d\ntools: {tools}\n{handoffs}---\n# X\nbody\n"


def _extensions(recipe: str) -> str:
    start = recipe.index("extensions:")
    end = recipe.find("\nsub_recipes:", start)
    return recipe[start:] if end < 0 else recipe[start:end]


# --- the mapping ---------------------------------------------------------------------------


def test_declared_tools_parses_front_matter():
    assert scoping.declared_tools(_agent("['read', 'Edit', 'search']")) == {"read", "edit", "search"}
    assert scoping.declared_tools("# no front matter\n") == frozenset()
    assert scoping.declared_tools("---\nname: X\ntools: not-a-list\n---\n") == frozenset()


@pytest.mark.parametrize("tools, names, allow, readfs", [
    ({"read", "search"}, [], {}, True),
    ({"read", "edit"}, ["developer", "analyze"], {"developer": ["write", "edit", "tree"], "analyze": ["__none__"]}, True),
    ({"execute"}, ["developer", "analyze"], {"developer": ["shell", "tree"], "analyze": ["__none__"]}, False),
    ({"read", "edit", "execute", "agent"}, ["developer", "analyze", "summon"],
     {"developer": ["write", "edit", "shell", "tree"], "analyze": ["__none__"], "summon": ["delegate", "load"]}, True),
    ({"todo", "web", "retrieval"}, [], {}, False),  # unknown tokens grant nothing (fail closed)
])
def test_grant_extensions_map(tools, names, allow, readfs):
    got_names, got_allow, stdio = scoping.grant_extensions(frozenset(tools))
    assert got_names == names and got_allow == allow
    assert [e["name"] for e in stdio] == (["agentteams_readfs"] if readfs else [])


def test_analyze_is_disabled_whenever_developer_is_present():
    for tools in ({"edit"}, {"execute"}, {"edit", "execute", "read"}):
        names, allow, _ = scoping.grant_extensions(frozenset(tools))
        assert "analyze" in names and allow["analyze"] == ["__none__"]


# --- rendered recipes ----------------------------------------------------------------------


def test_legacy_default_is_byte_identical():
    content = _agent("['read', 'search']", handoff_to="primary-producer")
    assert _ADAPTER.render_agent_file(content, "security", _manifest(None)) == \
        _ADAPTER.render_agent_file(content, "security", _manifest("legacy"))
    legacy = _ADAPTER.render_agent_file(content, "security", _manifest(None))
    assert "available_tools" not in legacy and "agentteams_readfs" not in legacy


def test_read_only_agent_gets_only_the_reader():
    recipe = _ADAPTER.render_agent_file(_agent("['read', 'search']"), "security", _manifest("grant"))
    ext = _extensions(recipe)
    assert "developer" not in ext and "shell" not in ext and "write" not in ext and "analyze" not in ext
    assert "name: \"agentteams_readfs\"" in ext
    assert "available_tools: [read_file, list_dir, find, grep, stat]" in ext
    assert _validate_recipe_yaml(recipe) == []


def test_writer_gets_exact_developer_tools_and_analyze_off():
    recipe = _ADAPTER.render_agent_file(_agent("['read', 'edit', 'search']"), "primary-producer", _manifest("grant"))
    ext = _extensions(recipe)
    assert "available_tools: [write, edit, tree]" in ext and "shell" not in ext
    assert "name: analyze\n    available_tools: [__none__]" in ext
    assert "available_tools: []" not in recipe
    assert _validate_recipe_yaml(recipe) == []


def test_non_dispatcher_gets_no_summon_or_load_handoffs():
    content = _agent("['read', 'edit', 'search']", handoff_to="quality-auditor")
    grant = _ADAPTER.render_agent_file(content, "primary-producer", _manifest("grant"))
    legacy = _ADAPTER.render_agent_file(content, "primary-producer", _manifest(None))
    assert "summon" in legacy  # legacy keeps its load() handoff references
    assert "summon" not in grant and "load(" not in grant


def test_orchestrator_keeps_summon_scoped_and_its_sub_recipes():
    content = _agent("['read', 'edit', 'search', 'execute', 'todo', 'agent']", handoff_to="primary-producer")
    recipe = _ADAPTER.render_agent_file(content, "orchestrator", _manifest("grant"))
    ext = _extensions(recipe)
    assert ext.count("name: summon") == 1 and "available_tools: [delegate, load]" in ext
    assert "sub_recipes:" in recipe and "./primary-producer.yaml" in recipe
    assert _validate_recipe_yaml(recipe) == []


def test_orchestrator_without_agent_tool_cannot_delegate_in_grant_mode():
    recipe = _ADAPTER.render_agent_file(_agent("['read', 'edit']", handoff_to="primary-producer"),
                                        "orchestrator", _manifest("grant"))
    assert "summon" not in recipe and "sub_recipes:" not in recipe


# --- shipping, validation ------------------------------------------------------------------


def test_reader_script_shipped_only_in_grant_mode():
    def shipped(mode):
        return {p: c for p, c in _ADAPTER.extra_output_files(_manifest(mode))}
    legacy, grant = shipped(None), shipped("grant")
    key = "../../scripts/goose-readfs-mcp.py"
    assert key not in legacy
    assert key in grant and "SERVER_NAME = \"agentteams_readfs\"" in grant[key]


def test_validator_rejects_empty_allowlist():
    recipe = 'version: "1.0.0"\ntitle: "t"\ninstructions: |\n  x\nextensions:\n  - type: platform\n    name: analyze\n    available_tools: []\n'
    assert any("available_tools: []" in v for v in _validate_recipe_yaml(recipe))


def test_schema_accepts_mode_and_manifest_carries_it():
    import json
    from pathlib import Path

    import jsonschema

    root = Path(__file__).resolve().parent.parent / "agentteams" / "schemas"
    for name in ("project-description.schema.json", "team-manifest.schema.json"):
        prop = json.loads((root / name).read_text())["properties"]["goose_tool_scoping"]
        jsonschema.Draft7Validator(prop).validate("grant")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft7Validator(prop).validate("permissive")
    assert _manifest("grant")["goose_tool_scoping"] == "grant"
    assert "goose_tool_scoping" not in _manifest(None)


# --- review fixes (2026-10-06) -------------------------------------------------------------


@pytest.mark.parametrize("front", [
    "tools: ['todo']",                      # only unknown tokens
    "tools:\n  - read\n  - edit",           # block list (not parsed)
    "description: no tools line",           # no tools at all
])
def test_nothing_granted_emits_explicit_empty_extensions(front):
    """A null/missing `extensions` makes Goose load the user's config (full developer): verified live."""
    content = f"---\nname: X\ndescription: d\n{front}\n---\n# X\nbody\n"
    recipe = _ADAPTER.render_agent_file(content, "security", _manifest("grant"))
    assert "extensions: []" in recipe
    assert "\nextensions:\n" not in recipe and "developer" not in recipe
    assert _validate_recipe_yaml(recipe) == []


def test_legacy_replace_with_empty_list_is_explicitly_empty():
    """The documented legacy "no developer/shell" option used to emit a bare `extensions:` (fail-open)."""
    m = _manifest(None)
    m["recipe_extensions"], m["recipe_extensions_mode"] = [], "replace"
    recipe = _ADAPTER.render_agent_file(_agent("['read']"), "security", m)
    assert "extensions: []" in recipe and "\nextensions:\n" not in recipe and "developer" not in recipe


def test_clash_filter_ignores_internal_whitespace():
    kept, notes = scoping.filter_operator_mcp([{"name": " dev eloper "}, {"name": "agentteams_\treadfs"}])
    assert kept == [] and len(notes) == 2


def test_duplicate_tools_lines_grant_nothing():
    content = "---\nname: X\ntools: ['read', 'edit', 'execute']\ntools: ['read']\n---\n# X\n"
    assert scoping.declared_tools(content) == frozenset()


def test_operator_mcp_with_a_managed_name_is_not_wired():
    kept, notes = scoping.filter_operator_mcp([
        {"name": "developer", "type": "stdio", "cmd": "goose", "args": ["mcp", "developer"]},
        {"name": "fetch", "type": "stdio", "cmd": "uvx", "args": ["mcp-server-fetch"]},
        {"name": "AgentTeams_ReadFS", "type": "stdio", "cmd": "x"},
    ])
    assert [k["name"] for k in kept] == ["fetch"] and len(notes) == 2


def test_merge_notice_lists_unscoped_recipes(tmp_path, capsys):
    (tmp_path / "legacy.yaml").write_text('version: "1.0.0"\nextensions:\n  - type: builtin\n    name: developer\n')
    (tmp_path / "scoped.yaml").write_text('version: "1.0.0"\nextensions:\n  - type: builtin\n    name: developer\n'
                                          '    available_tools: [write, edit, tree]\n')
    (tmp_path / "notes.yaml").write_text("not: a recipe\n")
    assert scoping.grant_merge_notice(tmp_path) == ["legacy.yaml"]
    assert "--update --overwrite" in capsys.readouterr().out
    assert scoping.grant_merge_notice(tmp_path / "missing") == []


def test_missing_shipped_script_placeholder_fails_closed_without_install_path(tmp_path):
    from agentteams.frameworks.goose_docs import _shipped_script

    text = _shipped_script(tmp_path / "home" / "someuser" / "goose-readfs-mcp.py")
    assert "Placeholder: scripts/goose-readfs-mcp.py" in text and "someuser" not in text
    assert "raise SystemExit(1)" in text

