"""rc8: read-only sandbox notices on every run (fail-open; confined claude team with nothing merged)."""

from __future__ import annotations

import json
import sys

import pytest

from agentteams.cli import generate_helpers as gh

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="advisory-only on native Windows")


def _team(tmp_path, settings=None):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    if settings is not None:
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings))
    return agents


_CONFINED = {"framework": "claude", "privilege_profile": "confined"}


def test_unsandboxed_confined_team_is_named(tmp_path, capsys):
    assert gh._warn_claude_team_unsandboxed(_CONFINED, _team(tmp_path)) is True
    assert "runs UNCONFINED" in capsys.readouterr().err


def test_merged_sandbox_is_silent_and_never_echoed(tmp_path, capsys):
    agents = _team(tmp_path, {"sandbox": {"enabled": True, "secret": "do-not-echo"}})
    assert gh._warn_claude_team_unsandboxed(_CONFINED, agents) is False
    assert "do-not-echo" not in capsys.readouterr().err


def test_cooperative_team_is_silent(tmp_path):
    agents = _team(tmp_path)
    assert gh._warn_claude_team_unsandboxed({"framework": "claude", "privilege_profile": "cooperative"},
                                            agents) is False


def test_notices_run_from_the_every_run_hook(tmp_path, capsys):
    agents = _team(tmp_path)
    gh._apply_sibling_team_denies(dict(_CONFINED), tmp_path, agents)
    assert "runs UNCONFINED" in capsys.readouterr().err
