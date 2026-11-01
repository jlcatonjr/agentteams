"""WASM plan P13: what the native-exec policy does to rendered teams, per framework, and WASM-off byte identity.

* Claude: a narrowed subagent loses ``Bash``, keeps the scoped retrieval CLI, gains ``run_wasm``; the orchestrator
  and the ``native_exec_keep`` agents keep ``Bash``.
* Goose (grant): the recipe loses ``developer`` ``shell`` and carries ``agentteams_wasm``; its delegation row keeps
  ``developer`` (a delegate cannot get ``run_wasm``), also after the narrowed recipe is on disk (Rule-12 carve-out).
* Grok / Copilot: ``replace`` keeps ``execute`` (no ``run_wasm``); ``deny`` removes it. agents-md: unchanged.
* WASM off (absent or ``off``): every framework renders exactly what it renders with P13's hooks bypassed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import wasm_enforcement as we
from agentteams.cli.render_pipeline import _build_final_rendered
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
ALL = ("claude", "goose", "codex", "grok", "copilot-vscode", "copilot-cli", "agents-md")


def _manifest(framework: str, *, wasm: str | None = "on", **brief: object) -> dict:
    manifest = analyze.build_manifest({**BRIEF, **brief}, framework=framework)
    if wasm is not None:
        manifest[WASM_SANDBOX_CLI_KEY] = wasm
    return manifest


def _render(framework: str, manifest: dict) -> dict[str, str]:
    return dict(_build_final_rendered(manifest, FRAMEWORKS[framework](), "x"))


def _file(out: dict[str, str], name: str) -> str:
    [path] = [p for p in out if Path(p).name == name]
    return out[path]


def _claude_tools(text: str) -> list[str]:
    [line] = [ln for ln in text.splitlines() if ln.startswith("tools:")]
    return [t.strip() for t in line.split(":", 1)[1].split(",")]


# --- Claude ----------------------------------------------------------------------------------------------------------

def test_claude_narrowed_subagent_loses_bash_and_gains_run_wasm():
    out = _render("claude", _manifest("claude"))
    tools = _claude_tools(_file(out, "cleanup.md"))
    assert "Bash" not in tools and "mcp__agentteams_wasm__run_wasm" in tools
    assert "agentteams_wasm" in _file(out, "cleanup.md")


@pytest.mark.parametrize("name", ["orchestrator.md", "navigator.md", "git-operations.md"])
def test_claude_exempt_and_kept_agents_keep_bash(name):
    tools = _claude_tools(_file(_render("claude", _manifest("claude")), name))
    assert "Bash" in tools and "mcp__agentteams_wasm__run_wasm" in tools


def test_claude_retrieval_scope_survives_the_narrowing():
    # Rule 12 carve-out 2: the scoped research CLI is kept; nothing else of Bash is.
    m = _manifest("claude")
    content = "---\nname: R\ndescription: \"d\"\ntools: ['read', 'execute', 'retrieval']\n---\n\nbody\n"
    narrowed = we.apply_canonical(content, "work-summarizer", m, "claude")
    rendered = FRAMEWORKS["claude"]().render_agent_file(narrowed, "work-summarizer", m)
    tools = _claude_tools(rendered)
    assert "Bash(python -m agentteams.research:*)" in tools and "Bash" not in tools
    assert "mcp__agentteams_wasm__run_wasm" in tools


def test_claude_builder_is_never_narrowed():
    out = _render("claude", _manifest("claude"))
    builders = [t for p, t in out.items() if "team-builder" in p]
    assert builders and all("Bash" in t.split("\n---", 1)[0] for t in builders)


def test_keep_policy_changes_no_claude_tools():
    on = _render("claude", _manifest("claude", wasm=None, wasm_sandbox={"enabled": True, "native_exec": "keep"}))
    assert "Bash" in _claude_tools(_file(on, "cleanup.md"))


# --- Goose -----------------------------------------------------------------------------------------------------------

def test_goose_grant_recipe_loses_shell_and_carries_wasm():
    out = _render("goose", _manifest("goose", goose_tool_scoping="grant"))
    recipe = _file(out, "cleanup.yaml")
    assert "agentteams_wasm" in recipe and "[shell" not in recipe and "shell," not in recipe
    assert "available_tools: [shell, tree]" in _file(out, "git-operations.yaml")


def test_goose_delegation_row_keeps_developer_for_a_narrowed_agent(tmp_path):
    from agentteams.frameworks.goose_recipe_grants import member_declared_tools
    from agentteams.frameworks.goose_tool_scoping import declared_tools

    m = _manifest("goose", goose_tool_scoping="grant")
    out = _render("goose", m)
    templates = REPO / "agentteams" / "templates"
    assert "execute" in member_declared_tools(m, "cleanup", templates)
    # The narrowed recipe now on disk lacks execute in its marker; the delegation row still keeps it (carve-out 8).
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (recipes / "cleanup.yaml").write_text(_file(out, "cleanup.yaml"), encoding="utf-8")
    m2 = {**_manifest("goose", goose_tool_scoping="grant"), "existing_project_path": str(tmp_path)}
    assert "execute" in member_declared_tools(m2, "cleanup", templates)
    # What the carve-out newly permits is bounded to WASM-narrowed agents: a non-narrowed agent's hand-narrowed
    # marker is still honoured, and with WASM off nothing is restored.
    (recipes / "git-operations.yaml").write_text("# agentteams-declared-tools: ['read']\n", encoding="utf-8")
    assert "execute" not in member_declared_tools(m2, "git-operations", templates)
    off = {**_manifest("goose", wasm="off", goose_tool_scoping="grant"), "existing_project_path": str(tmp_path)}
    assert "execute" not in member_declared_tools(off, "cleanup", templates)
    assert declared_tools  # imported for the marker grammar


# --- Grok / Copilot / agents-md --------------------------------------------------------------------------------------

def _copilot_tools(text: str) -> str:
    return next(ln for ln in text.splitlines() if ln.startswith("tools:"))


@pytest.mark.parametrize("framework, name", [("copilot-vscode", "cleanup.agent.md"),
                                             ("copilot-cli", "cleanup.agent.md")])
def test_copilot_replace_keeps_execute_and_deny_removes_it(framework, name):
    keep = _file(_render(framework, _manifest(framework)), name)
    assert "execute" in _copilot_tools(keep)
    deny = _file(_render(framework, _manifest(framework, wasm=None,
                                              wasm_sandbox={"enabled": True, "native_exec": "deny"})), name)
    assert "execute" not in _copilot_tools(deny)


def test_grok_replace_keeps_bash_and_deny_removes_it():
    replace = _render("grok", _manifest("grok"))
    deny = _render("grok", _manifest("grok", wasm=None, wasm_sandbox={"enabled": True, "native_exec": "deny"}))
    name = next(Path(p).name for p in replace if "cleanup" in p and "references" not in p)
    head = lambda t: t.split("\n---", 1)[0]  # noqa: E731
    assert "Bash" in head(_file(replace, name)) and "Bash" not in head(_file(deny, name))


def test_agents_md_is_unchanged_by_deny():
    on = _render("agents-md", _manifest("agents-md", wasm=None, wasm_sandbox={"enabled": True, "native_exec": "deny"}))
    keep = _render("agents-md", _manifest("agents-md", wasm=None, wasm_sandbox={"enabled": True,
                                                                               "native_exec": "keep"}))
    # agents-md has no tool channel, so deny narrows nothing: the same files with the same tool declarations.
    # (P14's instruction text names the policy, so the prose legitimately differs between deny and keep.)
    assert set(on) == set(keep)
    for path in on:
        tools = [ln for ln in on[path].splitlines() if ln.lstrip().lower().startswith("tools:")]
        assert tools == [ln for ln in keep[path].splitlines() if ln.lstrip().lower().startswith("tools:")], path


# --- WASM off: byte identity ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("framework", ALL)
@pytest.mark.parametrize("wasm", [None, "off"])
def test_wasm_off_renders_are_byte_identical_to_the_unhooked_pipeline(framework, wasm, monkeypatch):
    extra = {"goose_tool_scoping": "grant"} if framework == "goose" else {}
    hooked = _render(framework, _manifest(framework, wasm=wasm, **extra))
    monkeypatch.setattr(we, "apply_canonical", lambda content, *a, **k: content)
    monkeypatch.setattr(we, "wiring_tools", lambda manifest, slug, tools: list(tools))
    monkeypatch.setattr(we, "agent_action", lambda *a, **k: None)
    plain = _render(framework, _manifest(framework, wasm=wasm, **extra))
    assert hooked == plain
    assert we.KEY not in _manifest(framework, wasm=wasm)
