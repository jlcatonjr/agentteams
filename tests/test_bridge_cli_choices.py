"""A2.1 regression test: CLI/runtime bridge framework-choices mismatch.

Report section 3.3: the CLI's --bridge-source-framework argparse choices accepted
agents-md and codex, but bridge.py's run_bridge raised ValueError at runtime for
those frameworks. The fix narrows the argparse choices to match bridge.py's real
support.
"""

from __future__ import annotations

import pytest
from agentteams.cli.parser import _build_parser as build_parser


def test_bridge_source_framework_choices_match_runtime():
    """--bridge-source-framework choices must match bridge.py's accepted set."""
    parser = build_parser()
    # Find the bridge-source-framework argument
    action = next(
        a for a in parser._actions
        if "--bridge-source-framework" in (a.option_strings or [])
    )
    expected = {"copilot-vscode", "copilot-cli", "claude", "goose", "canonical"}
    assert set(action.choices) == expected, (
        f"--bridge-source-framework choices {set(action.choices)} "
        f"do not match bridge.py's accepted set {expected}"
    )
    # Specifically, agents-md and codex must NOT be in the choices
    assert "agents-md" not in action.choices
    assert "codex" not in action.choices


def test_bridge_source_framework_rejects_agents_md():
    """Passing --bridge-source-framework agents-md must fail at parse time."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--bridge-source-framework", "agents-md", "--bridge-from", "."])


def test_bridge_source_framework_rejects_codex():
    """Passing --bridge-source-framework codex must fail at parse time."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--bridge-source-framework", "codex", "--bridge-from", "."])
