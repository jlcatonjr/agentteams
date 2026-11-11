"""Claude agent files default to Fable 5.2 for the orchestrator and Opus for every other agent."""

from __future__ import annotations

import pytest

from agentteams.frameworks.claude import (
    CLAUDE_AGENT_MODEL_DEFAULT,
    CLAUDE_ORCHESTRATOR_MODEL_DEFAULT,
    ClaudeAdapter,
)

_COPILOT_MODEL = 'model: ["Claude Opus 4.8 (copilot)"]'
_TEMPLATE = "---\nname: X\ndescription: \"d\"\ntools: ['read']\n{model}---\nbody\n"


def _model_lines(model_line: str, slug: str) -> list[str]:
    content = _TEMPLATE.format(model=f"{model_line}\n" if model_line else "")
    rendered = ClaudeAdapter().render_agent_file(content, slug, {"project_name": "P"})
    front = rendered.split("---", 2)[1]
    return [line for line in front.splitlines() if line.startswith("model:")]


def test_defaults_are_fable_and_opus():
    assert CLAUDE_ORCHESTRATOR_MODEL_DEFAULT == "claude-fable-5-2"
    assert CLAUDE_AGENT_MODEL_DEFAULT == "opus"


@pytest.mark.parametrize("slug", ["orchestrator", "bridge-orchestrator"])
def test_orchestrator_gets_fable(slug):
    assert _model_lines(_COPILOT_MODEL, slug) == ["model: claude-fable-5-2"]


@pytest.mark.parametrize("slug", ["security", "primary-producer", "navigator", "pipeline-core-expert"])
def test_other_agents_get_opus(slug):
    assert _model_lines(_COPILOT_MODEL, slug) == ["model: opus"]


def test_copilot_display_name_scalar_is_replaced_not_leaked():
    assert _model_lines("model: Claude Opus 4.8 (copilot)", "security") == ["model: opus"]


@pytest.mark.parametrize("value", ["claude-sonnet-5-5", "haiku", "opus[1m]", "inherit"])
def test_valid_claude_model_is_kept(value):
    assert _model_lines(f"model: {value}", "orchestrator") == [f"model: {value}"]


def test_no_model_key_keeps_inheriting():
    assert _model_lines("", "security") == []


# --- R1: Claude model ids never leak into Copilot headers; the builder is pinned too -----------------------------

from pathlib import Path  # noqa: E402

from agentteams.convert import convert_team  # noqa: E402
from agentteams.interop import import_from_cai  # noqa: E402
from agentteams.interop_helpers import drops_source_model, strip_front_matter_model  # noqa: E402


def _cai(source_framework: str, model, slug: str = "security") -> dict:
    return {
        "schema_version": "2.0",
        "created_at": "2026-11-11T00:00:00+00:00",
        "source_framework": source_framework,
        "source_dir": "seed",
        "instructions_binding": {"source_name": "CLAUDE.md", "content": "# I\n"},
        "agents": [{
            "slug": slug, "name": "S", "description": "d.", "body_markdown": "# S\n\nbody\n",
            "capabilities": {"tool_scopes": ["read"]}, "handoffs": [], "invariant_core_markdown": None,
            "raw_front_matter": {"model": model}, "source_path": f"{slug}.md",
        }],
        "skills": [], "mcp_servers": [], "references": [], "framework_extensions": {},
    }


def _front(path: Path) -> str:
    return path.read_text(encoding="utf-8").split("---", 2)[1]


def test_drops_source_model_is_by_source_framework_not_value_shape():
    assert drops_source_model("claude", "copilot-vscode")
    assert drops_source_model("claude", "copilot-cli")
    assert not drops_source_model("copilot-vscode", "copilot-vscode")
    assert not drops_source_model("claude", "claude")
    assert not drops_source_model(None, "copilot-vscode")


def test_strip_front_matter_model_leaves_the_body_alone():
    text = "---\nname: X\nmodel: opus\ntools: Read\n---\nmodel: stays in body\n"
    assert strip_front_matter_model(text) == "---\nname: X\ntools: Read\n---\nmodel: stays in body\n"
    block = "---\nname: X\nmodel:\n  - a\n  - b\ntools: Read\n---\nbody\n"
    assert strip_front_matter_model(block) == "---\nname: X\ntools: Read\n---\nbody\n"
    assert strip_front_matter_model("no front matter\nmodel: x\n") == "no front matter\nmodel: x\n"


@pytest.mark.parametrize("target", ["copilot-vscode", "copilot-cli"])
def test_interop_claude_model_is_dropped_for_copilot(tmp_path, target):
    result = import_from_cai(_cai("claude", "opus"), target, tmp_path, overwrite=True)
    assert result.errors == []
    out = next(tmp_path.rglob("security*.md"))
    model_lines = [ln for ln in _front(out).splitlines() if ln.startswith("model:")]
    assert model_lines and "opus" not in model_lines[0] and "(copilot)" in model_lines[0]
    assert any("is a Claude model" in n for n in result.notices)


def test_interop_copilot_bare_model_survives_copilot_import(tmp_path):
    result = import_from_cai(_cai("copilot-vscode", "gpt-5"), "copilot-vscode", tmp_path, overwrite=True)
    assert result.errors == []
    assert "model: gpt-5" in _front(next(tmp_path.rglob("security*.md")))


@pytest.mark.parametrize("slug,expected", [("security", "opus"), ("orchestrator", "claude-fable-5-2")])
def test_interop_copilot_list_model_becomes_claude_default(tmp_path, slug, expected):
    cai = _cai("copilot-vscode", ["Claude Sonnet 4.6 (copilot)"], slug)
    result = import_from_cai(cai, "claude", tmp_path, overwrite=True)
    assert result.errors == []
    assert f"model: {expected}\n" in _front(tmp_path / f"{slug}.md") + "\n"


def test_convert_claude_team_to_copilot_drops_claude_models(tmp_path):
    src = tmp_path / "proj" / ".claude" / "agents"
    src.mkdir(parents=True)
    (src / "security.md").write_text("---\nname: S\ndescription: \"d\"\ntools: Read, Grep\nmodel: opus\n---\n# S\n")
    (src / "orchestrator.md").write_text(
        "---\nname: O\ndescription: \"d\"\ntools: Read, Task\nmodel: claude-fable-5-2\n---\n# O\n")
    out = tmp_path / "out"
    result = convert_team(src, out, "copilot-vscode")
    assert result.errors == []
    headers = "".join(_front(p) for p in out.rglob("*.md") if p.read_text().startswith("---"))
    assert "opus" not in headers and "claude-fable" not in headers


def test_claude_builder_render_replaces_unreadable_model_and_keeps_valid_one():
    adapter = ClaudeAdapter()
    copilot_builder = "---\nname: B\ndescription: \"d\"\ntools: ['read']\nmodel: [\"Claude Opus 4.8 (copilot)\"]\n---\nbody\n"
    assert "model: opus\n" in adapter.render_builder_file(copilot_builder, {})
    kept = "---\nname: B\nallowed-tools: Read\nmodel: claude-sonnet-5-5\n---\nbody\n"
    assert adapter.render_builder_file(kept, {}) == kept
    absent = "---\nname: B\nallowed-tools: Read\n---\nbody\n"
    assert adapter.render_builder_file(absent, {}) == absent


def test_claude_builder_template_pins_opus():
    import agentteams

    template = Path(agentteams.__file__).parent / "templates" / "builder" / "team-builder-claude.template.md"
    assert "\nmodel: opus\n" in template.read_text(encoding="utf-8").split("---", 2)[1] + "\n"
