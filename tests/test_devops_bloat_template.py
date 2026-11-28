"""The devops-bloat agent is templated for the teams that get the PR agents, and only for them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams import analyze, mcp_catalog

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "agentteams" / "templates"
AGENT = TEMPLATES / "domain" / "devops-bloat.template.md"
REFERENCE = TEMPLATES / "universal" / "devops-bloat.reference.template.md"
REFERENCE_PATH = "references/devops-bloat.reference.md"


def _manifest(framework: str, **brief: object) -> dict:
    return analyze.build_manifest({"project_goal": "g", "project_name": "p", **brief}, framework=framework)


def _paths(manifest: dict) -> set[str]:
    return {entry["path"] for entry in manifest["output_files"]}


@pytest.mark.parametrize("framework", ["copilot-vscode", "claude"])
def test_a_pr_managing_team_gets_the_agent_and_its_reference(framework: str) -> None:
    manifest = _manifest(framework, pr_management=True)
    assert {"pr-manager", "pr-notifier", "devops-bloat"} <= set(manifest["agent_slug_list"])
    paths = _paths(manifest)
    assert REFERENCE_PATH in paths
    assert any(Path(path).name.startswith("devops-bloat.") and "reference" not in path for path in paths)


def test_a_github_catalogue_opt_in_brings_it_too() -> None:
    assert mcp_catalog.wants_pr_agents({"mcp_catalog": ["github-read"]})
    assert "devops-bloat" in _manifest("claude", mcp_catalog=["github-read"])["agent_slug_list"]


@pytest.mark.parametrize("framework", ["copilot-vscode", "claude"])
def test_a_team_without_the_pr_trigger_gets_neither(framework: str) -> None:
    manifest = _manifest(framework)
    assert "devops-bloat" not in manifest["agent_slug_list"]
    assert not any("devops-bloat" in path for path in _paths(manifest))


def test_a_selected_archetypes_override_cannot_drop_it() -> None:
    manifest = _manifest("claude", pr_management=True, selected_archetypes=["primary-producer"])
    assert "devops-bloat" in manifest["agent_slug_list"]


def test_the_reference_follows_the_archetype_not_the_trigger() -> None:
    manifest = _manifest("claude", selected_archetypes=["devops-bloat"])
    assert "devops-bloat" in manifest["agent_slug_list"] and REFERENCE_PATH in _paths(manifest)


@pytest.mark.parametrize("framework", ["claude", "copilot-vscode"])
def test_the_rendered_agent_resolves_its_placeholders_and_holds_no_writing_tool(framework: str) -> None:
    from agentteams import render
    from agentteams.frameworks import registry

    manifest = _manifest(framework, pr_management=True)
    rendered = dict(render.render_all(manifest, templates_dir=TEMPLATES))
    name = next(path for path in rendered if Path(path).name.startswith("devops-bloat.") and "/" not in path)
    emitted = registry.FRAMEWORKS[framework]().render_agent_file(rendered[name], "devops-bloat", manifest)
    for text in (emitted, rendered[REFERENCE_PATH]):
        assert not re.search(r"\{(?:MANUAL:)?[A-Z][A-Z_]+\}", text), "an unresolved placeholder was emitted"
    tools = re.search(r"^tools:\s*(.+)$", emitted, re.MULTILINE).group(1)
    assert not re.search(r"\b(Write|Edit|MultiEdit|NotebookEdit|Bash|edit|execute|write)\b", tools), tools


def test_no_mcp_server_is_scoped_to_the_agent() -> None:
    for entry in mcp_catalog.load_catalog().values():
        assert "devops-bloat" not in entry.get("role_scope", [])


def _floor(path: Path) -> list[str]:
    """The floor's bullet list, normalised, up to the tier item (worded per file)."""
    text = " ".join(path.read_text(encoding="utf-8").split())
    start = text.index("- the Constitutional Core")
    body = text[start: text.index("- the tier classification", start)]
    return [item.strip(" ;.") for item in body.split("- ") if item.strip()]


def test_the_agent_and_reference_state_the_same_floor() -> None:
    assert _floor(AGENT) == _floor(REFERENCE)
    for path in (AGENT, REFERENCE):
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "re-verifying" not in text, "name only rules a generated team has"
        assert "tiered too low" in text and re.search(r"(never|does not) recommend lowering a tier", text)


@pytest.mark.parametrize("template", [AGENT, REFERENCE], ids=lambda path: path.name)
def test_the_method_never_reads_as_an_exemption(template: Path) -> None:
    text = " ".join(template.read_text(encoding="utf-8").split())
    assert "does not apply" not in text
    assert "stays in force until the operator edits it" in text


def test_the_agent_holds_only_read_and_search() -> None:
    text = AGENT.read_text(encoding="utf-8")
    assert re.search(r"^tools: \['read', 'search'\]$", text, re.MULTILINE)
    assert re.search(r"^user-invocable: true$", text, re.MULTILINE)


@pytest.mark.parametrize("template", [AGENT, REFERENCE], ids=lambda path: path.name)
def test_no_rule_is_cited_by_number(template: Path) -> None:
    """Rule numbers differ between teams and between the instructions file and the orchestrator."""
    text = template.read_text(encoding="utf-8")
    assert not re.search(r"\bRules? \d", text), "name a rule by what it does, never by its number"
    assert "C-1 to C-5" in text


@pytest.mark.parametrize("template", [AGENT, REFERENCE], ids=lambda path: path.name)
def test_the_templates_point_at_the_packaged_ledger_not_a_repo_script(template: Path) -> None:
    text = template.read_text(encoding="utf-8")
    assert "python -m agentteams.gate_catch_ledger" in text
    assert "scripts/" not in text


def test_the_agent_and_reference_state_the_same_output_contract() -> None:
    def contract(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        start = text.index("PROCESS-BLOAT REVIEW")
        return text[start: text.index("```", start)]

    assert contract(AGENT) == contract(REFERENCE)
