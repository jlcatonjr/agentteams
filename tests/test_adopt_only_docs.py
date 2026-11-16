"""`--adopt-only`'s two requirements are documented (issue #392 item 13, docs half).

It needs ``--description`` or ``--self`` (``cli/app.py`` stops without one), and ``--output`` must be the agents
directory: a project-root ``--output`` refuses with "no regular orchestrator file" (``cli/adopt_only.py``).
"""

from __future__ import annotations

from pathlib import Path

from agentteams.cli.parser import _build_parser

REPO = Path(__file__).resolve().parents[1]


def _section() -> str:
    text = (REPO / "docs_src" / "cli-reference.md").read_text(encoding="utf-8")
    return text.split("### `--adopt-only`", 1)[1].split("\n### ", 1)[0]


def test_cli_reference_states_both_requirements() -> None:
    section = _section()
    assert "--description PATH" in section and "--self" in section
    assert "--description is required (or use --self" in section
    assert "`--output` is the agents directory" in section
    assert "no regular orchestrator file" in section


def test_help_text_states_both_requirements() -> None:
    action = next(a for a in _build_parser()._actions if "--adopt-only" in a.option_strings)
    assert "Needs --description, --self, or the project's own brief" in action.help
    assert "agents directory" in action.help


def test_the_documented_messages_match_the_code() -> None:
    assert "--description is required (or use --self" in (REPO / "agentteams" / "cli" / "app.py").read_text()
    assert "no regular orchestrator file" in (REPO / "agentteams" / "cli" / "adopt_only.py").read_text()
