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
