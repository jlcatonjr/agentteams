"""Phase 3 of the Goose read-only-agents work: recipe contract checks and the Goose version pin.

* AR_GOOSE_GRANT_EXCEEDED (error): a grant recipe, identified by its declared-tools marker, exposes
  more than those tools grant. It keys on declared tools, not prose.
* AR_GOOSE_READONLY_WRITE (warning): an unmarked recipe whose prose self-declares read-only exposes
  developer write/edit/shell.
* AR_GOOSE_EXTENSIONS_FAIL_OPEN: a missing, bare or null `extensions` makes Goose 1.37 load the user's
  configured extensions (verified live). An error in grant mode, a warning in legacy.

Every extensions shape the reader cannot interpret counts as an unscoped developer (worst case).
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agentteams import analyze, render
from agentteams.audit_agent_contract import _check_goose_recipe_grants, _check_readonly_tool_declarations
from agentteams.frameworks import goose_tool_scoping as scoping
from agentteams.frameworks.goose import GooseAdapter
from agentteams.frameworks.goose_recipe_read import developer_tools, recipe_extension_grants
from agentteams.frameworks.registry import FRAMEWORKS

_ADAPTER = GooseAdapter()
_HEAD = 'version: "1.0.0"\ntitle: "t"\ninstructions: |\n  You are **read-only**.\n'
_DEV = "  - type: builtin\n    name: developer\n"


def _manifest(mode, framework="goose"):
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}]}
    if mode:
        desc["goose_tool_scoping"] = mode
    return analyze.build_manifest(desc, framework=framework)


def _codes(findings):
    return sorted((f.code, f.severity) for f in findings)


def _check(recipe, *, grant=True):
    return _check_goose_recipe_grants({"x.yaml": recipe}, agent_ext=".yaml", grant_mode=grant)


# --- the reader fails safe --------------------------------------------------------------------


@pytest.mark.parametrize("tail, dev", [
    ("", None), ("extensions:\nprompt: x\n", None), ("extensions: null\n", None), ("extensions: ~\n", None),
    ("extensions: []  # none\n", set()),
    ("extensions: [{name: developer}]\n", {"write", "edit", "shell", "tree"}),               # flow style
    ("extensions: []\nextensions: []\n", {"write", "edit", "shell", "tree"}),                # duplicate key
    ("extensions:  # note\n" + _DEV + "    available_tools: [tree] # ro\n", {"tree"}),      # comments
    ("extensions:\n- type: builtin\n  name: developer # main\n  available_tools: [write]\n", {"write"}),  # zero indent
    ("extensions:\n" + _DEV + "    available_tools:\n      - tree\n", {"write", "edit", "shell", "tree"}),  # block list
    ("extensions:\n" + _DEV + "    available_tools: []\n", {"write", "edit", "shell", "tree"}),  # [] = all
])
def test_reader_shapes(tail, dev):
    grants = recipe_extension_grants(_HEAD + tail)
    assert (None if grants is None else developer_tools(grants)) == dev


# --- grant recipes: exposure vs the declared-tools marker -------------------------------------


def _emit(tools, slug="security", mode="grant"):
    content = f"---\nname: X\ndescription: d\ntools: {tools}\n---\n# X\nYou are **read-only**.\n"
    return _ADAPTER.render_agent_file(content, slug, _manifest(mode))


@pytest.mark.parametrize("tools", ["['read', 'search']", "['read', 'edit', 'search']",
                                   "['read', 'edit', 'execute', 'agent']", "['todo']"])
def test_emitted_grant_recipes_carry_a_marker_and_pass(tools):
    recipe = _emit(tools)
    assert scoping.declared_from_marker(recipe) is not None
    assert _check(recipe) == []


@pytest.mark.parametrize("widen, needle", [
    (lambda r: r.replace("available_tools: [write, edit, tree]", "available_tools: [write, edit, shell, tree]"),
     "developer shell"),
    (lambda r: r.replace("    available_tools: [write, edit, tree]\n", ""), "developer shell"),        # unscoped
    (lambda r: r.replace("    available_tools: [__none__]\n", ""), "analyze"),                       # analyze on
    (lambda r: r.replace("extensions:\n", "extensions:\n  - type: platform\n    name: summon\n", 1), "summon"),
])
def test_hand_widened_grant_recipe_is_an_error_whatever_its_prose(widen, needle):
    recipe = widen(_emit("['read', 'edit', 'search']", slug="primary-producer").replace("You are **read-only**.", "Produce."))
    findings = _check(recipe)
    assert _codes(findings) == [("AR_GOOSE_GRANT_EXCEEDED", "error")] and needle in findings[0].description


def test_marker_parsing():
    assert scoping.declared_from_marker("# agentteams-declared-tools: read, search\n") == {"read", "search"}
    assert scoping.declared_from_marker("# agentteams-declared-tools: none\n") == frozenset()
    assert scoping.declared_from_marker("no marker\n") is None
    assert scoping.declared_from_marker("# agentteams-declared-tools: read\n# agentteams-declared-tools: edit\n") is None


# --- unmarked recipes: prose fallback and fail-open -------------------------------------------


def test_legacy_read_only_recipe_warns_and_recommends_grant():
    findings = _check(_emit("['read', 'search']", mode=None), grant=False)
    assert _codes(findings) == [("AR_GOOSE_READONLY_WRITE", "warning")]
    assert "goose_tool_scoping" in findings[0].description


def test_unmarked_writer_recipe_is_not_flagged_in_legacy():
    assert _check('version: "1.0.0"\ntitle: "t"\ninstructions: |\n  Produce.\nextensions:\n' + _DEV, grant=False) == []


@pytest.mark.parametrize("edit", [
    lambda r: r.replace("# agentteams-declared-tools: edit, read, search\n", ""),                 # deleted
    lambda r: r.replace("# agentteams-declared-tools:", "# agentteams-declared-tools: read\n# agentteams-declared-tools:"),
])
def test_grant_recipe_without_exactly_one_marker_is_an_error(edit):
    recipe = edit(_emit("['read', 'edit', 'search']", slug="primary-producer"))
    assert _codes(_check(recipe)) == [("AR_GOOSE_MARKER_MISSING", "error")]


def test_bridge_recipe_is_exempt_from_the_marker_rule():
    bridge = 'version: "1.0.0"\ntitle: "b"\ninstructions: |\n  Bridge.\nextensions:\n' + _DEV
    assert _check_goose_recipe_grants({"bridge-orchestrator.yaml": bridge}, agent_ext=".yaml", grant_mode=True) == []


def test_ungranted_extension_is_exceeded_but_declared_operator_mcp_is_not():
    base = _emit("['read', 'search']")
    injected = base.replace("extensions:\n", "extensions:\n  - type: builtin\n    name: computercontroller\n", 1)
    findings = _check(injected)
    assert _codes(findings) == [("AR_GOOSE_GRANT_EXCEEDED", "error")] and "computercontroller" in findings[0].description
    operator = base.replace("extensions:\n", "extensions:\n  - type: stdio\n    name: \"fetch\"\n", 1)
    assert _check_goose_recipe_grants({"x.yaml": operator}, agent_ext=".yaml", grant_mode=True,
                                      operator_extensions={"x": frozenset({"fetch"})}) == []
    # Scoped to another agent only: not allowed here.
    findings = _check_goose_recipe_grants({"x.yaml": operator}, agent_ext=".yaml", grant_mode=True,
                                          operator_extensions={"other": frozenset({"fetch"})})
    assert _codes(findings) == [("AR_GOOSE_GRANT_EXCEEDED", "error")] and "fetch" in findings[0].description


def test_reader_takes_the_items_own_name_not_a_nested_one():
    recipe = _HEAD + "extensions:\n  - type: stdio\n    env:\n      name: developer\n    name: \"helper\"\n"
    assert [e["name"] for e in recipe_extension_grants(recipe)] == ["helper"]


@pytest.mark.parametrize("grant, severity", [(True, "error"), (False, "warning")])
@pytest.mark.parametrize("tail", ["", "extensions:\nprompt: x\n", "extensions: null\n"])
def test_fail_open_extensions(grant, severity, tail):
    recipe = 'version: "1.0.0"\ntitle: "t"\ninstructions: |\n  Produce.\n' + tail
    assert _codes(_check(recipe, grant=grant)) == [("AR_GOOSE_EXTENSIONS_FAIL_OPEN", severity)]


def test_only_goose_recipes_are_checked():
    assert _check_goose_recipe_grants({"a.agent.md": _HEAD}, agent_ext=".agent.md", grant_mode=True) == []
    assert _check({"x": 1} and "foo: bar\n") == []


def test_wired_into_post_audit(tmp_path):
    from agentteams.audit import run_post_audit

    bad = _HEAD + "extensions:\n" + _DEV
    grant = run_post_audit(tmp_path, _manifest("grant"), rendered_files=[("security.yaml", bad)], ai_audit=False)
    assert ("AR_GOOSE_MARKER_MISSING", "error") in _codes(grant.agent_refactor_findings)
    legacy = run_post_audit(tmp_path, _manifest(None), rendered_files=[("security.yaml", bad)], ai_audit=False)
    assert ("AR_GOOSE_READONLY_WRITE", "warning") in _codes(legacy.agent_refactor_findings)


def test_merge_notice_uses_the_shared_reader(tmp_path):
    (tmp_path / "legacy.yaml").write_text('version: "1.0.0"\nextensions:\n' + _DEV)
    (tmp_path / "flow.yaml").write_text('version: "1.0.0"\nextensions: [{name: developer}]\n')
    (tmp_path / "tree.yaml").write_text('version: "1.0.0"\nextensions:\n' + _DEV + "    available_tools: [tree]\n")
    (tmp_path / "grant.yaml").write_text(_emit("['read', 'edit', 'search']"))
    assert scoping.unscoped_recipes(tmp_path) == ["flow.yaml", "legacy.yaml"]


# --- full teams -------------------------------------------------------------------------------


def _team(framework, mode):
    m = _manifest(mode, framework)
    adapter = FRAMEWORKS[framework]()
    ext = adapter.get_file_extension("agent")
    files, declared = {}, {}
    for path, content in render.render_all(m, templates_dir=Path(analyze.__file__).parent / "templates"):
        if path.endswith(".agent.md"):
            slug = Path(path).name[: -len(".agent.md")]
            files[f"{slug}{ext}"] = adapter.render_agent_file(content, slug, m)
            declared[f"{slug}{ext}"] = (scoping.declared_tools(content), content)
    return files, declared, ext


@pytest.mark.parametrize("framework", ["goose", "copilot-vscode"])
def test_full_grant_team_passes_its_own_checks(framework):
    """Regression: cleanup's "(tags read-only, ...)" made every team fail AR_READONLY_TOOL_VIOLATION."""
    files, _, ext = _team(framework, "grant")
    findings = (_check_goose_recipe_grants(files, agent_ext=ext, grant_mode=True)
                + _check_readonly_tool_declarations(files, agent_ext=ext))
    assert [(f.file, f.code) for f in findings] == []


def test_full_legacy_team_warns_exactly_the_read_only_agents():
    """The warning set must equal the agents that self-declare read-only and declare only read/search."""
    from agentteams.audit_agent_contract import _READONLY_BODY_RE

    files, declared, ext = _team("goose", None)
    warned = {f.file for f in _check_goose_recipe_grants(files, agent_ext=ext, grant_mode=False)}
    expected = {name for name, (tools, content) in declared.items()
                if _READONLY_BODY_RE.search(content) and tools <= {"read", "search"}}
    assert expected and warned == expected
    # Not circular: a hardcoded floor the regex must keep matching.
    assert {"security.yaml", "adversarial.yaml", "code-hygiene.yaml"} <= warned


# --- version pin ------------------------------------------------------------------------------


def _fake_goose(tmp_path, output, stream="1"):
    script = tmp_path / "goose"
    script.write_text(f"#!/bin/sh\necho '{output}' >&{stream}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_version_notice(tmp_path, monkeypatch):
    v = scoping.GOOSE_MAP_VERSION
    assert scoping.goose_version_notice(_fake_goose(tmp_path, f" {v}")) is None
    assert scoping.goose_version_notice(_fake_goose(tmp_path, f" {v}", stream="2")) is None    # stderr
    assert "1.40.2" in scoping.goose_version_notice(_fake_goose(tmp_path, " 1.40.2"))
    assert f"{v}-rc1" in scoping.goose_version_notice(_fake_goose(tmp_path, f" {v}-rc1"))      # suffix differs
    assert "cannot confirm" in scoping.goose_version_notice(str(tmp_path / "missing-goose"))
    monkeypatch.setattr(scoping.shutil, "which", lambda _name: None)
    assert "not on PATH" in scoping.goose_version_notice()


def test_windows_refuses_a_goose_from_the_current_directory(tmp_path, monkeypatch):
    planted = _fake_goose(tmp_path, f" {scoping.GOOSE_MAP_VERSION}")
    monkeypatch.setattr(scoping.os, "name", "nt")
    monkeypatch.setattr(scoping.shutil, "which", lambda _name: planted)
    monkeypatch.setattr(scoping.os, "getcwd", lambda: str(tmp_path).upper())  # case differs: normcase matters
    monkeypatch.setattr(scoping.os.path, "normcase", lambda p: p.lower())
    assert "current directory" in scoping.goose_version_notice()


@pytest.mark.parametrize("ext_type, flagged", [("builtin", True), ("platform", True), ("stdio", False),
                                               ("streamable_http", False)])
def test_operator_name_excuses_only_an_mcp_transport(ext_type, flagged):
    """An operator server named like a Goose builtin must not excuse that builtin (phase-3 follow-up)."""
    recipe = _emit("['read', 'search']").replace(
        "extensions:\n", f"extensions:\n  - type: {ext_type}\n    name: computercontroller\n", 1)
    findings = _check_goose_recipe_grants({"x.yaml": recipe}, agent_ext=".yaml", grant_mode=True,
                                          operator_extensions={"x": frozenset({"computercontroller"})})
    assert bool(findings) is flagged
    if flagged:
        assert "computercontroller" in findings[0].description


def test_reader_records_each_items_own_type():
    recipe = _HEAD + "extensions:\n  - type: stdio\n    name: a\n    env:\n      type: builtin\n  - type: builtin\n    name: b\n"
    assert [(e["name"], e["type"]) for e in recipe_extension_grants(recipe)] == [("a", "stdio"), ("b", "builtin")]


def test_nested_mcp_type_cannot_disguise_a_builtin():
    """Attack direction: an outer `type: builtin` with a nested `env: {type: stdio}` is still a builtin."""
    recipe = _emit("['read', 'search']").replace(
        "extensions:\n", "extensions:\n  - type: builtin\n    name: computercontroller\n    env:\n      type: stdio\n", 1)
    findings = _check_goose_recipe_grants({"x.yaml": recipe}, agent_ext=".yaml", grant_mode=True,
                                          operator_extensions={"x": frozenset({"computercontroller"})})
    assert findings and "computercontroller" in findings[0].description
