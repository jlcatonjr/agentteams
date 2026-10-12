"""Goose delegated children run with the delegator's extensions, so grant mode narrows every delegate call.

Biographies pilot 1 (Goose round, 2026-10-11): G1, a default-dispatched read-only quality-auditor inherited the
orchestrator's write/edit/shell and edited a deliverable; G2, ``delegate`` cannot add ``agentteams_runner``; G9,
``delegate``'s ``extensions`` argument can strip inherited tools. Re-measured with
``scripts/goose-probe/run_delegate_case.sh`` (references/goose-tool-scoping-spike.md, "Delegated children").
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.frameworks import goose_recipe_grants as grants
from agentteams.frameworks import goose_tool_scoping as scoping
from agentteams.frameworks.goose import GooseAdapter, _DELEGATION_REF_HEADING_RE
from agentteams.frameworks.goose_recipe_read import recipe_extension_grants
from agentteams.frameworks.goose_recipe_validate import _validate_recipe_yaml
from agentteams.frameworks.goose_recipe_emit import delegation_section

_ADAPTER = GooseAdapter()
_TEMPLATES = Path(analyze.__file__).parent / "templates"
_HEADING = "## Delegation narrowing (Goose)"
_ORCH_TOOLS = "['read', 'edit', 'search', 'execute', 'todo', 'agent']"


def _manifest(mode: str | None = "grant", **extra) -> dict:
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}], **extra}
    if mode:
        desc["goose_tool_scoping"] = mode
    return analyze.build_manifest(desc, framework="goose")


def _agent(tools: str) -> str:
    return f"---\nname: X\ndescription: d\ntools: {tools}\n---\n# X\nbody\n"


def _orchestrator(manifest: dict, tools: str = _ORCH_TOOLS) -> str:
    return _ADAPTER.render_agent_file(_agent(tools), "orchestrator", manifest)


def _rows(recipe: str) -> dict[str, tuple[str, list[str], str]]:
    """source -> (recipe file, extensions argument, withheld text), parsed from the narrowing table."""
    rows = {}
    for m in re.finditer(r"^\s*\| `([^`]+)` \| `([^`]+)` \| `(\[.*?\])` \| (.*) \|$", recipe, re.MULTILINE):
        rows[m.group(1)] = (m.group(2), json.loads(m.group(3)), m.group(4))
    return rows


# --- delegate_extensions ---------------------------------------------------------------------


@pytest.mark.parametrize("tools, passed, withheld", [
    ({"read", "search"}, ["agentteams_readfs"], []),
    (set(), [], []),
    ({"todo", "web"}, [], []),
    ({"read", "edit", "execute"}, ["developer", "agentteams_readfs"], []),
    ({"read", "edit", "execute", "agent"}, ["developer", "summon", "agentteams_readfs"], []),
    ({"read", "search", "agent"}, ["summon", "agentteams_readfs"], []),
    # A partial developer can't be carried: delegate would hand over the delegator's whole allowlist.
    ({"read", "edit"}, ["agentteams_readfs"], ["developer write, edit, tree"]),
    ({"read", "execute"}, ["agentteams_readfs"], ["developer shell, tree"]),
    ({"edit"}, [], ["developer write, edit, tree"]),
])
def test_delegate_extensions_map(tools, passed, withheld):
    got, held = scoping.delegate_extensions(frozenset(tools))
    assert got == passed
    assert [h.split(" (")[0] for h in held] == withheld


def test_delegate_extensions_fail_closed_when_tools_unknown():
    got, held = scoping.delegate_extensions(None)
    assert got == ["agentteams_readfs"] and held


def test_delegate_extensions_never_passes_an_extension_delegate_cannot_add():
    got, held = scoping.delegate_extensions(frozenset({"read", "edit", "execute"}), ("agentteams_runner", "fetch"))
    assert got == ["developer", "agentteams_readfs"]
    assert any(h.startswith("agentteams_runner") for h in held) and any(h.startswith("fetch") for h in held)


def test_delegate_extensions_never_exceeds_the_targets_grant():
    """Every name passed is one the target's own grant holds, with the target holding its whole allowlist."""
    tokens = ("read", "search", "edit", "execute", "agent", "todo")
    for mask in range(1 << len(tokens)):
        tools = frozenset(t for i, t in enumerate(tokens) if mask >> i & 1)
        names, allow, stdio = scoping.grant_extensions(tools)
        held = set(names) | {e["name"] for e in stdio}
        got, _ = scoping.delegate_extensions(tools)
        assert set(got) <= held, tools
        if "developer" in got:
            assert allow["developer"] == ["write", "edit", "shell", "tree"]


# --- summon outside the orchestrator --------------------------------------------------------


def test_spawn_false_keeps_only_load():
    _, allow, _ = scoping.grant_extensions(frozenset({"agent"}), spawn=False)
    assert allow["summon"] == ["load"]
    assert scoping.grant_extensions(frozenset({"agent"}))[1]["summon"] == ["delegate", "load"]


def test_non_orchestrator_recipe_cannot_delegate():
    recipe = _ADAPTER.render_agent_file(_agent("['read', 'edit', 'agent']"), "agent-updater", _manifest())
    summon = [e for e in recipe_extension_grants(recipe) if e["name"] == "summon"]
    assert summon and summon[0]["available_tools"] == ["load"]
    assert _HEADING not in recipe


def test_orchestrator_keeps_delegate():
    recipe = _orchestrator(_manifest())
    summon = [e for e in recipe_extension_grants(recipe) if e["name"] == "summon"]
    assert summon[0]["available_tools"] == ["delegate", "load"]


# --- the orchestrator's narrowing table -------------------------------------------------------


def test_orchestrator_lists_every_sub_recipe_with_its_argument():
    recipe = _orchestrator(_manifest())
    assert _HEADING in recipe and not _validate_recipe_yaml(recipe)
    sub_names = re.findall(r'^  - name: "([^"]+)"', recipe.split("\nsub_recipes:", 1)[1], re.MULTILINE)
    rows = _rows(recipe)
    assert sub_names and set(sub_names) == set(rows)
    # The universal templates' read-only auditor gets the reader only; nothing passes a partial developer.
    assert rows["quality_auditor"][1] == ["agentteams_readfs"] and rows["quality_auditor"][2] == "—"
    assert rows["security"][1] == ["agentteams_readfs"]
    for source, (_file, names, withheld) in rows.items():
        if "developer" not in names:
            continue
        assert withheld == "—", source


def test_orchestrator_rows_match_the_templates_declared_tools():
    manifest = _manifest()
    rows = _rows(_orchestrator(manifest))
    for source, (recipe_file, names, _withheld) in rows.items():
        slug = recipe_file[: -len(".yaml")]
        assert names == scoping.delegate_extensions(grants.member_declared_tools(manifest, slug, _TEMPLATES))[0], slug


def test_orchestrator_tells_it_never_to_omit_extensions_and_to_route_withheld_work():
    recipe = _orchestrator(_manifest())
    assert "Never omit it" in recipe
    assert "goose run --recipe .goose/recipes/<recipe>" in recipe
    assert "Don't start it from your own shell" in recipe
    assert 'extensions: ["agentteams_readfs"]' in recipe  # ad-hoc / unlisted targets


def test_partial_developer_writer_is_withheld_not_delegated():
    rows = _rows(_orchestrator(_manifest()))
    partial = {s: r for s, r in rows.items() if r[2].startswith("developer")}
    assert partial, "the universal team has writers declaring edit without execute"
    for source, (_file, names, _withheld) in partial.items():
        assert "developer" not in names, source


def test_unknown_member_gets_a_read_only_delegate():
    manifest = _manifest()
    manifest["output_files"] = [f for f in manifest["output_files"] if f["path"] != "cleanup.agent.md"]
    manifest["existing_agent_slugs"] = ["cleanup"]  # on disk only: no template this run
    rows = _rows(_orchestrator(manifest))
    assert rows["cleanup"][1] == ["agentteams_readfs"]
    assert "unknown" in rows["cleanup"][2]


def test_runner_granted_member_is_withheld_under_the_switch(monkeypatch):
    monkeypatch.setenv("AGENTTEAMS_ALLOW_UNENFORCED_GOOSE_SWITCH", "1")
    manifest = _manifest(None, write_policy="orchestrator-only", privilege_profile="confined",
                         mcp_grants={"primary-producer": {"tools": ["write_file"]}})
    rows = _rows(_orchestrator(manifest))
    names, withheld = rows["primary_producer"][1], rows["primary_producer"][2]
    assert "agentteams_runner" not in names and "developer" not in names
    assert "agentteams_runner (delegate cannot add it)" in withheld


def test_orchestrator_without_summon_has_no_table():
    recipe = _orchestrator(_manifest(), tools="['read', 'edit', 'search', 'execute']")
    assert _HEADING not in recipe and "sub_recipes:" not in recipe


def test_legacy_orchestrator_is_unchanged():
    recipe = _orchestrator(_manifest(None))
    assert _HEADING not in recipe and "available_tools" not in recipe


def test_section_is_stripped_on_re_render():
    recipe = _orchestrator(_manifest())
    body = recipe.split("instructions: |\n", 1)[1]
    assert _DELEGATION_REF_HEADING_RE.search(body.replace("\n  ", "\n"))
    content = _agent(_ORCH_TOOLS).replace("body\n", "body\n\n" + delegation_section([("a", "a", [], [])]) + "\n")
    again = _ADAPTER.render_agent_file(content, "orchestrator", _manifest())
    assert again.count(_HEADING) == 1


def test_delegation_section_rows():
    text = delegation_section([("primary_producer", "primary-producer", ["agentteams_readfs"], ["developer x"])])
    assert '| `primary_producer` | `primary-producer.yaml` | `["agentteams_readfs"]` | developer x |' in text
    assert text.startswith(_HEADING) and not text.endswith("\n")


# --- one source for a recipe's extras; template and on-disk tools ------------------------------


def test_member_tools_never_widen_a_hand_narrowed_recipe_on_disk(tmp_path):
    manifest = _manifest()
    manifest["existing_project_path"] = str(tmp_path)
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "cleanup.yaml").write_text('version: "1.0.0"\n# agentteams-declared-tools: read, search\nextensions: []\n')
    # The cleanup template declares edit, search, execute: the on-disk marker drops edit and execute.
    assert grants.member_declared_tools(manifest, "cleanup", _TEMPLATES) == {"search"}
    assert _rows(_orchestrator(manifest))["cleanup"][1] == ["agentteams_readfs"]
    # A hand-WIDENED marker does not widen the template's tools.
    (recipes / "security.yaml").write_text('version: "1.0.0"\n# agentteams-declared-tools: read, edit, execute\n')
    assert "edit" not in (grants.member_declared_tools(manifest, "security", _TEMPLATES) or ())


def test_member_tools_ignore_a_template_outside_the_library():
    manifest = _manifest()
    manifest["output_files"] = [{"path": "cleanup.agent.md", "template": "../../../../etc/hosts"}]
    assert grants.member_declared_tools(manifest, "cleanup", _TEMPLATES) is None


@pytest.mark.parametrize("slug", ["orchestrator", "repo-liaison", "security"])
def test_recipe_extras_are_what_the_recipe_emits(slug):
    manifest = _manifest()
    manifest["coordination_write_roots"] = ["../sibling"]
    extras, _notes = grants.recipe_extras(manifest, slug)
    assert bool(extras) == (slug != "security")
    recipe = _ADAPTER.render_agent_file(_agent("['read', 'search', 'agent']"), slug, manifest)
    emitted = {e["name"] for e in recipe_extension_grants(recipe)} - {"agentteams_readfs", "summon"}
    assert emitted == {e["name"] for e in extras}
