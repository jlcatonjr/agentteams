"""``--interop-from`` projections never widen a grant (plan 2026-W42 projection-fixes, Revision 1).

The 2026-10-13 rehearsal projected a ``Read, Grep, Glob`` Claude agent (the injection-test-designer) to every
surface: goose got the whole ``developer`` builtin, Codex no read-only declaration, AGENTS.md nothing, and a
44k-character body reached Copilot with only a warning. These tests project a read-only and a full-tools Claude
source onto each surface, re-ingest the result through that surface's own reader, and diff the grant.
"""

from __future__ import annotations

import json
import tomllib
import warnings
from pathlib import Path

import pytest

from agentteams import interop_grant_check as igc
from agentteams.capability_map import goose_grants_to_canonical
from agentteams.frameworks.goose_recipe_read import recipe_extension_grants
from agentteams.frameworks.goose_tool_scoping import READFS_NAME, READFS_SCRIPT, READFS_TOOLS, grant_extensions
from agentteams.interop import export_to_cai, run_interop
from agentteams.signing_switch import GOVERNED_MARKER, SWITCH_REL, read_switch

READER = "reader"  # stands in for the injection-test-designer: Read, Grep, Glob
BUILDER = "builder"
_FULL_TOOLS = "Read, Write, Edit, Bash, Grep, Glob, Task, TodoWrite"
TARGETS = {
    "copilot-vscode": (".github/agents", ".agent.md"),
    "goose": (".goose/recipes", ".yaml"),
    "codex": (".codex/agents", ".toml"),
    "agents-md": (".agents", ".md"),
    "claude": (".claude/agents", ".md"),
}


def _claude_agent(name: str, tools: str, body: str = "") -> str:
    return (f"---\nname: {name.title()}\ndescription: \"The {name} agent\"\ntools: {tools}\n---\n\n"
            f"# {name.title()}\n\n{body or f'Does {name} work. KEEP_{name.upper()}'}\n")


def _source(root: Path, agents: dict[str, str] | None = None) -> Path:
    src = root / "src" / ".claude" / "agents"
    src.mkdir(parents=True)
    (src.parent / "CLAUDE.md").write_text("# Team\n\nRules.\n", encoding="utf-8")
    for slug, text in (agents or {READER: _claude_agent(READER, "Read, Grep, Glob"),
                                  BUILDER: _claude_agent(BUILDER, _FULL_TOOLS)}).items():
        (src / f"{slug}.md").write_text(text, encoding="utf-8")
    return src


def _target(root: Path, framework: str, *, readfs: bool = True) -> Path:
    project = root / "dst"
    if framework == "goose" and readfs:  # what a native goose generation installs
        (project / READFS_SCRIPT).parent.mkdir(parents=True, exist_ok=True)
        (project / READFS_SCRIPT).write_text("# agentteams_readfs\n", encoding="utf-8")
    return project / TARGETS[framework][0]


def _project(tmp_path: Path, framework: str, **kw):
    target = _target(tmp_path, framework)
    result = run_interop(_source(tmp_path), framework, target, source_framework="claude", **kw)
    assert result.success, result.errors
    return target, result


def _text(target: Path, framework: str, slug: str) -> str:
    return (target / f"{slug}{TARGETS[framework][1]}").read_text(encoding="utf-8")


# --- round trip: project -> re-ingest -> grant diff ----------------------------------------------

_COOP = {"codex": READER, "agents-md": READER}


@pytest.mark.parametrize("framework", ["copilot-vscode", "goose", "codex"])
def test_round_trip_never_widens_the_grant(tmp_path, framework):
    target, _ = _project(tmp_path, framework, allow_cooperative=_COOP.get(framework))
    source = {a["slug"]: set(a["capabilities"].get("tool_scopes") or ())
              for a in export_to_cai(_source(tmp_path / "again"), "claude")["agents"]}
    back = {a["slug"]: set(a["capabilities"].get("tool_scopes") or ()) for a in export_to_cai(target, framework)["agents"]}
    assert source[READER] == {"read", "search"}
    for slug, scopes in back.items():
        assert scopes <= source[slug] | ({"read", "search"} if source[slug] & {"read", "search"} else set()), slug
    assert back[READER] <= {"read", "search"} and back[READER]
    assert {"edit", "execute"} <= back[BUILDER]


def test_agents_md_carries_no_grant_and_needs_the_opt_in(tmp_path):
    target, result = _project(tmp_path, "agents-md", allow_cooperative=READER)
    assert "grant is cooperative on this surface" in _text(target, "agents-md", READER)
    assert "grant is cooperative" not in _text(target, "agents-md", BUILDER)  # the full grant isn't narrower
    assert any(READER in n and "NOT ENFORCED" in n for n in result.notices)


# --- goose: grant-scoped, never developer, refused without the readfs server ---------------------

def test_goose_reader_gets_agentteams_readfs_not_developer(tmp_path):
    target, _ = _project(tmp_path, "goose")
    grants = recipe_extension_grants(_text(target, "goose", READER))
    assert [g["name"] for g in grants] == [READFS_NAME]
    assert grants[0]["available_tools"] == list(READFS_TOOLS)
    # exactly what native grant mode gives a read/search agent: no builtin, the readfs server only
    names, allow, stdio = grant_extensions(frozenset({"read", "search"}), spawn=False)
    assert names == [] and allow == {} and [s["name"] for s in stdio] == [READFS_NAME]
    assert "goose run --recipe" in _text(target, "goose", READER)  # the #206 delegate caveat


def test_goose_full_tools_agent_gets_a_scoped_developer(tmp_path):
    target, _ = _project(tmp_path, "goose")
    grants = {g["name"]: g["available_tools"] for g in recipe_extension_grants(_text(target, "goose", BUILDER))}
    assert grants["developer"] == ["write", "edit", "shell", "tree"]
    assert grants["analyze"] == ["__none__"] and READFS_NAME in grants


def test_goose_refuses_when_the_readfs_server_is_missing(tmp_path):
    target = _target(tmp_path, "goose", readfs=False)
    with pytest.raises(ValueError, match=f"{READER}.*goose-readfs-mcp.py"):
        run_interop(_source(tmp_path), "goose", target, source_framework="claude")
    assert not target.exists() or not list(target.glob("*.yaml"))


def test_goose_reverse_map_reads_missing_extensions_as_developer():
    assert goose_grants_to_canonical(None) == ["read", "edit", "search", "execute"]
    assert goose_grants_to_canonical([]) == []
    assert goose_grants_to_canonical([{"name": READFS_NAME, "available_tools": None}]) == ["read", "search"]
    assert goose_grants_to_canonical([{"name": "developer", "available_tools": ["write", "edit", "tree"]},
                                      {"name": "analyze", "available_tools": ["__none__"]}]) == ["edit"]
    assert goose_grants_to_canonical([{"name": "<unparsed>", "available_tools": None}]) == [
        "read", "edit", "search", "execute"]
    assert igc.projected_scopes("goose", 'version: "1.0.0"\ntitle: "x"\ninstructions: |\n  hi\n') == {
        "read", "edit", "search", "execute"}


# --- Copilot: the no-front-matter default is a widening ------------------------------------------

def test_copilot_default_tools_are_caught():
    assert igc.projected_scopes("copilot-vscode", "# Body only\n") == {"read", "edit", "search"}
    problems = igc.check_agent("x", ["read"], "copilot-vscode", "# Body only\n", {}, igc.ProjectionPolicy())
    assert len(problems) == 1 and "widen" in problems[0] and "copilot-vscode" in problems[0]
    header_without_tools = "---\nname: X\ndescription: \"d\"\n---\n\n# X\n"
    assert igc.check_agent("x", ["read", "search"], "copilot-vscode", header_without_tools, {},
                           igc.ProjectionPolicy())


def test_a_grant_copilot_cannot_express_is_refused_end_to_end(tmp_path):
    src = _source(tmp_path, {"webby": _claude_agent("webby", "WebFetch")})  # declared, but no canonical token
    target = _target(tmp_path, "copilot-vscode")
    with pytest.raises(ValueError, match="webby: projecting onto copilot-vscode would widen"):
        run_interop(src, "copilot-vscode", target, source_framework="claude")
    assert not target.exists() or not list(target.glob("*.agent.md"))


def test_claude_without_tools_inherits_everything():
    assert igc.projected_scopes("claude", "---\nname: X\n---\n\nB\n") == frozenset(
        ("read", "edit", "search", "execute", "todo", "agent", "retrieval"))
    assert igc.projected_scopes("claude", "---\nname: X\ntools: Read, Grep, Glob\n---\n\nB\n") == {"read", "search"}


# --- enforcement class and the cooperative opt-in -------------------------------------------------

@pytest.mark.parametrize("framework", ["codex", "agents-md"])
def test_a_narrow_grant_on_an_unenforcing_surface_is_refused_by_default(tmp_path, framework):
    target = _target(tmp_path, framework)
    with pytest.raises(ValueError) as exc:
        run_interop(_source(tmp_path), framework, target, source_framework="claude")
    assert f"{READER}:" in str(exc.value) and framework in str(exc.value)
    assert "--interop-allow-cooperative" in str(exc.value) and f"{BUILDER}:" not in str(exc.value)
    assert not target.exists() or not [p for p in target.iterdir() if p.is_file()]


@pytest.mark.parametrize("framework", ["codex", "agents-md"])
def test_the_opt_in_projects_with_the_banner_and_notice(tmp_path, framework):
    target, result = _project(tmp_path, framework, allow_cooperative=[READER])
    assert "grant is cooperative on this surface" in _text(target, framework, READER)
    assert any(f"{READER}: projected onto {framework}" in n and "NOT ENFORCED" in n for n in result.notices)


def test_enforcement_classes():
    assert igc.enforcement_class("claude", {}) == igc.ENFORCED
    assert igc.enforcement_class("goose", {}) == igc.ENFORCED
    assert igc.enforcement_class("copilot-vscode", {}) == igc.ENFORCED
    assert igc.enforcement_class("codex", {}) == igc.DECLARED
    assert igc.enforcement_class("codex", {"write_policy": "orchestrator-only",
                                           "host_features": ["codex:sandbox"]}) == igc.ENFORCED
    assert igc.enforcement_class("agents-md", {}) == igc.NONE


def test_the_opt_in_takes_slugs_only():
    assert igc.parse_slug_list("a,b , c") == {"a", "b", "c"}
    assert igc.parse_slug_list(["a", "b,c"]) == {"a", "b", "c"}
    with pytest.raises(ValueError, match="not an agent slug"):
        igc.parse_slug_list("*")


def test_an_unknown_opt_in_is_reported(tmp_path):
    _, result = _project(tmp_path, "codex", allow_cooperative=f"{READER},ghost")
    assert any("ghost" in n and "not in the source team" in n for n in result.notices)


def test_banners_do_not_accumulate_across_projections(tmp_path):
    first, _ = _project(tmp_path, "codex", allow_cooperative=READER)
    goose = tmp_path / "g"
    (goose / READFS_SCRIPT).parent.mkdir(parents=True)
    (goose / READFS_SCRIPT).write_text("#\n", encoding="utf-8")
    assert run_interop(first, "goose", goose / ".goose" / "recipes", source_framework="codex").success
    recipe = (goose / ".goose" / "recipes" / f"{READER}.yaml").read_text(encoding="utf-8")
    assert "grant is cooperative" not in recipe and recipe.count("Projection grant note") == 1


# --- codex: canonical tokens so sandbox_mode and "Reading on Codex" render -----------------------

def test_codex_gets_canonical_tokens_from_claude_names(tmp_path):
    target, _ = _project(tmp_path, "codex", allow_cooperative=READER)
    doc = tomllib.loads(_text(target, "codex", READER))
    assert doc["sandbox_mode"] == "read-only"
    instructions = doc["developer_instructions"]
    assert "Declared tools: `read`, `search`." in instructions
    assert "### Reading on Codex" in instructions and "### Tool limit (self-imposed)" in instructions
    assert tomllib.loads(_text(target, "codex", BUILDER))["sandbox_mode"] == "workspace-write"


def test_maps_to_canonical():
    assert igc.maps_to_canonical("Read, Grep, Glob")
    assert not igc.maps_to_canonical("['read', 'search']")  # already canonical: kept verbatim
    assert not igc.maps_to_canonical("['read', 'runCommands']")  # bespoke: kept verbatim


# --- Copilot size: refused on interop, a warning natively ----------------------------------------

def test_an_oversized_copilot_body_is_refused_on_interop_only(tmp_path):
    from agentteams.frameworks.copilot_vscode import _MAX_BODY_CHARS, CopilotVSCodeAdapter

    body = "\n\n".join("Rule text that must not be truncated. " * 20 for _ in range(_MAX_BODY_CHARS // 700 + 2))
    assert len(body) > _MAX_BODY_CHARS
    src = _source(tmp_path, {"big": _claude_agent("big", _FULL_TOOLS, body)})
    target = _target(tmp_path, "copilot-vscode")
    with pytest.raises(ValueError, match="big: its copilot-vscode body is .* over the platform's 30,000"):
        run_interop(src, "copilot-vscode", target, source_framework="claude")
    assert not target.exists() or not list(target.glob("*.agent.md"))
    native = f"---\nname: Big\ndescription: \"d\"\ntools: ['read']\n---\n\n{body}\n"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rendered = CopilotVSCodeAdapter().render_agent_file(native, "big", {})
    assert body in rendered
    assert any("30,000-character limit" in str(w.message) for w in caught)


# --- a governed destination is never weakened ----------------------------------------------------

def _governed(agents: Path, *, governed: bool = True) -> dict[str, str]:
    refs = agents / "references"
    refs.mkdir(parents=True)
    (refs / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": True}), encoding="utf-8")
    rosters = {"security-approvers.txt": "alice\n", "authorized-managers.txt": "team-a\n"}
    for name, text in rosters.items():
        (refs / name).write_text(text, encoding="utf-8")
    if governed:
        (agents / GOVERNED_MARKER).write_text("governed\n", encoding="utf-8")
    return rosters


@pytest.mark.parametrize("framework", ["copilot-vscode", "goose"])
@pytest.mark.parametrize("source_switch", [None, False])
def test_projecting_into_a_governed_team_keeps_its_switch_and_rosters(tmp_path, framework, source_switch):
    src = _source(tmp_path)
    if source_switch is not None:
        (src / "references").mkdir()
        (src / SWITCH_REL).write_text(json.dumps({"enforce_decision_signing": source_switch}), encoding="utf-8")
    target = _target(tmp_path, framework)
    rosters = _governed(target)
    result = run_interop(src, framework, target, source_framework="claude", overwrite=True)
    assert result.success, result.errors
    assert read_switch(target / SWITCH_REL) is True
    for name, text in rosters.items():
        assert (target / "references" / name).read_text(encoding="utf-8") == text


def test_an_ungoverned_true_switch_is_not_relaxed_by_a_source_without_one(tmp_path):
    target = _target(tmp_path, "copilot-vscode")
    rosters = _governed(target, governed=False)
    assert run_interop(_source(tmp_path), "copilot-vscode", target, source_framework="claude").success
    assert read_switch(target / SWITCH_REL) is True
    for name, text in rosters.items():
        assert (target / "references" / name).read_text(encoding="utf-8") == text
