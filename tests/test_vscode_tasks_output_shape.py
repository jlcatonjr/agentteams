"""`.vscode/tasks.json` must never be written outside the tree `--output` names.

``vscode_tasks_rel_path`` returns a fixed ``../../.vscode/tasks.json``: every adapter that
overrides it places its agents dir exactly two segments below the project root, so the constant
is correct for a real project. It is wrong for any other ``--output``.

Measured during a golden-snapshot regeneration: with ``--output examples/<name>/expected`` — one
segment below its own conceptual root — the offset climbed a level too far and produced
``examples/.vscode/tasks.json``, a sibling of all the example projects rather than a child of any
of them. Nothing caught it because ``test_snapshot_comparison`` only reads ``*.md``/``*.svg``
inside ``expected/``.

The guard derives the expected depth from each adapter's own ``get_agents_dir`` rather than
restating "two levels" a second time, and **refuses** rather than guessing a corrected offset: an
arbitrary ``--output`` has no discoverable project root, so a corrected path would still be a
guess written somewhere the operator did not name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.cli.render_pipeline import _agents_dir_depth, _emit_vscode_tasks
from agentteams.frameworks.agents_md import AgentsMdAdapter
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
from agentteams.frameworks.goose import GooseAdapter


# --- the depth is derived, not restated ------------------------------------

@pytest.mark.parametrize(("adapter", "expected"), [
    (CopilotVSCodeAdapter(), (".github", "agents")),
    (ClaudeAdapter(), (".claude", "agents")),
])
def test_depth_comes_from_the_adapters_own_contract(adapter, expected):
    assert _agents_dir_depth(adapter) == expected


def test_every_adapter_that_emits_tasks_json_is_two_deep():
    """The premise behind the `../../` constant, asserted rather than assumed."""
    for adapter in (CopilotVSCodeAdapter(), ClaudeAdapter(), GooseAdapter()):
        assert adapter.vscode_tasks_rel_path() == "../../.vscode/tasks.json"
        assert len(_agents_dir_depth(adapter)) == 2, type(adapter).__name__


# --- the escape ------------------------------------------------------------

def test_a_shallow_output_dir_is_refused(tmp_path, capsys):
    """The observed defect: --output examples/<name>/expected wrote examples/.vscode/."""
    shallow = tmp_path / "examples" / "software-project" / "expected"
    shallow.mkdir(parents=True)

    assert _emit_vscode_tasks({}, CopilotVSCodeAdapter(), shallow) is None
    assert not (tmp_path / "examples" / ".vscode").exists()


def test_the_refusal_is_announced(tmp_path, capsys):
    """Silence is what let this ship — the stray directory was found by eye, not by output."""
    shallow = tmp_path / "expected"
    shallow.mkdir(parents=True)

    _emit_vscode_tasks({}, CopilotVSCodeAdapter(), shallow)

    err = capsys.readouterr().err
    assert ".vscode/tasks.json" in err
    assert ".github/agents" in err, "the message must name the shape it expected"


# --- what must still work --------------------------------------------------

def test_a_canonical_agents_dir_still_emits(tmp_path):
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)

    result = _emit_vscode_tasks({}, CopilotVSCodeAdapter(), agents_dir)

    assert result is not None
    rel_path, content = result
    assert rel_path == "../../.vscode/tasks.json"
    assert (agents_dir / rel_path).resolve() == (tmp_path / ".vscode" / "tasks.json")
    assert content.strip().startswith("{")


def test_the_claude_tree_still_emits(tmp_path):
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    assert _emit_vscode_tasks({}, ClaudeAdapter(), agents_dir) is not None


def test_no_output_dir_is_unaffected():
    """Rendering without a target still produces content; the guard needs a path to check."""
    result = _emit_vscode_tasks({}, CopilotVSCodeAdapter(), None)
    assert result is not None


def test_an_adapter_that_opts_out_never_reaches_the_guard():
    assert AgentsMdAdapter().vscode_tasks_rel_path() is None
    assert _emit_vscode_tasks({}, AgentsMdAdapter(), Path("/anywhere")) is None


def test_the_no_vscode_tasks_manifest_flag_still_wins(tmp_path):
    agents_dir = tmp_path / ".github" / "agents"
    agents_dir.mkdir(parents=True)
    assert _emit_vscode_tasks({"no_vscode_tasks": True}, CopilotVSCodeAdapter(), agents_dir) is None
