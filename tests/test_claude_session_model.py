"""The Claude settings example names the session model and the subagent fallback model.

``.claude/settings.hooks.example.json`` carries ``"model": CLAUDE_ORCHESTRATOR_MODEL_DEFAULT`` (the main thread) and
``env.CLAUDE_CODE_SUBAGENT_MODEL = CLAUDE_AGENT_MODEL_DEFAULT`` (built-in subagents and agents with no ``model:``),
with and without the sandbox block. Like the hooks block, it is inert until an operator merges it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.frameworks import _claude_model as cm
from agentteams.frameworks.registry import FRAMEWORKS

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
EXAMPLE = "../settings.hooks.example.json"


def _example(**brief_extra: object) -> dict:
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), **brief_extra}
    manifest = analyze.build_manifest(brief, framework="claude")
    return json.loads(dict(FRAMEWORKS["claude"]().extra_output_files(manifest))[EXAMPLE])


@pytest.mark.parametrize("brief_extra", [{}, {"privilege_profile": "confined"}], ids=["no-sandbox", "sandbox"])
def test_example_sets_session_and_subagent_models(brief_extra: dict) -> None:
    data = _example(**brief_extra)
    assert data["model"] == cm.CLAUDE_ORCHESTRATOR_MODEL_DEFAULT == "claude-fable-5-2"
    assert data["env"] == {cm.CLAUDE_SUBAGENT_MODEL_ENV: cm.CLAUDE_AGENT_MODEL_DEFAULT}
    assert data["env"][cm.CLAUDE_SUBAGENT_MODEL_ENV] == "opus"
    assert "hooks" in data
    assert any(cm.CLAUDE_SUBAGENT_MODEL_ENV in line for line in data["_comment"])
    if brief_extra:
        assert "sandbox" in data


def test_force_is_never_set() -> None:
    """``CLAUDE_CODE_SUBAGENT_MODEL_FORCE`` would pull the orchestrator agent off its own ``model:``."""
    assert not any(key.endswith("_FORCE") for key in _example()["env"])


def test_existing_values_are_kept() -> None:
    text = json.dumps({"_comment": ["x"], "model": "sonnet", "env": {"A": "1", cm.CLAUDE_SUBAGENT_MODEL_ENV: "haiku"}})
    data = json.loads(cm.inject_session_model(text))
    assert data["model"] == "sonnet"
    assert data["env"] == {"A": "1", cm.CLAUDE_SUBAGENT_MODEL_ENV: "haiku"}


@pytest.mark.parametrize("text", ["not json", "[]", "", '{"env": []}'])
def test_malformed_example_fails_loud(text: str) -> None:
    with pytest.raises(ValueError):
        cm.inject_session_model(text)


def test_non_str_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        cm.inject_session_model(b"{}")  # type: ignore[arg-type]


def test_output_keys_are_sorted() -> None:
    assert list(json.loads(cm.inject_session_model('{"z": 1, "_comment": []}'))) == ["_comment", "env", "model", "z"]


def test_other_keys_survive_unchanged() -> None:
    template = (REPO / "agentteams" / "templates" / "universal" / "hooks" / "settings.hooks.example.json").read_text(
        encoding="utf-8")
    before = json.loads(template)
    after = json.loads(cm.inject_session_model(template))
    assert after["_comment"][: len(before["_comment"])] == before["_comment"]
    assert {k: v for k, v in after.items() if k not in ("model", "env", "_comment")} == {
        k: v for k, v in before.items() if k != "_comment"}
