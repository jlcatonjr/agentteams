"""The agent run against goose's real behaviour (first live run, 2026-10-23).

goose 1.37 stopped before its first model call because it could not create its log directory, and
exited 0; the run reported ``completed``. Split out of ``test_vulnscan_agent.py`` (CH-07); shares its
fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.vulnscan import agent_run
from tests.test_vulnscan_agent import _a, _launchable, dirs  # noqa: F401  (dirs is a fixture)


def test_a_run_without_a_single_model_call_is_an_agent_error_not_completed(dirs: dict[str, Path],
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """First live run: goose stopped on its own error and exited 0 with no model call; that is not a success."""
    _launchable(dirs, monkeypatch)
    fake_popen = agent_run.subprocess.Popen                      # the fake container client _launchable installed

    def popen_writing_goose_error(argv: list[str], stdout: object = None, **kw: object) -> object:
        if hasattr(stdout, "write"):
            stdout.write("goose is ready\nRan into this error: Execution error: failed to create directory "
                         "`/agenthome/.local/state/goose/logs`: Permission denied (os error 13).\n")
            stdout.flush()
        return fake_popen(argv, stdout=stdout, **kw)

    monkeypatch.setattr(agent_run.subprocess, "Popen", popen_writing_goose_error)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert st["run_state"] == "agent-error" and st["exit"] == 0, st
    assert "without a single model call" in st["reason"] and "Permission denied" in st["reason"], st


def test_the_agent_container_gives_goose_a_writable_state_dir() -> None:
    """XDG_STATE_HOME is set outside $HOME/.local, whose parents the sessions bind mount makes root-owned."""
    argv = agent_run._base_argv(Path("/run"), Path("/sessions"), Path("/team"), Path("/spool"), name="x")
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert f"XDG_STATE_HOME={agent_run.GOOSE_STATE_IN_CONTAINER}" in envs
    assert not agent_run.GOOSE_STATE_IN_CONTAINER.startswith(f"{agent_run.AGENT_HOME}/.local")
    assert 'r["goose_dirs_writable"]' in agent_run.SELFTEST                   # the self-test now proves it

