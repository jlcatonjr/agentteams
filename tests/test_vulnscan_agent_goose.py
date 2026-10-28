"""The agent run against goose's real behaviour (first live run, 2026-10-23).

goose 1.37 stopped before its first model call because it could not create its log directory, and
exited 0; the run reported ``completed``. Split out of ``test_vulnscan_agent.py`` (CH-07); shares its
fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_drivers, agent_run, gateway_dialects
from tests.test_vulnscan_agent import (  # noqa: F401  (dirs is a fixture)
    _a,
    _launchable,
    dirs,
)


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
    """XDG_STATE_HOME is inside the writable sessions mount (its logs survive the run), never $HOME/.local/state,
    whose parent the sessions bind mount makes root-owned."""
    argv = agent_run._base_argv(Path("/run"), Path("/sessions"), Path("/team"), Path("/spool"), name="x")
    envs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert f"XDG_STATE_HOME={agent_run.GOOSE_STATE_IN_CONTAINER}" in envs
    assert agent_run.GOOSE_STATE_IN_CONTAINER.startswith(agent_run.GOOSE_SESSIONS_IN_CONTAINER + "/")
    assert f"/sessions:{agent_run.GOOSE_SESSIONS_IN_CONTAINER}:rw" in argv
    assert 'r["goose_dirs_writable"]' in agent_run.SELFTEST                   # the self-test now proves it


def test_the_default_recipe_is_the_remediation_recipe_whose_prompt_is_the_task() -> None:
    """The orchestrator's generated prompt is a role check; a one-shot run of it answers that and stops."""
    goose = agent_drivers.GOOSE
    assert agent_run.DEFAULT_AGENT["recipe"] == "" and agent_run.DEFAULT_AGENT["command"] == []   # the driver's
    assert goose.default_recipe == ".goose/recipes/vuln-remediation.yaml"
    assert "{recipe}" in goose.command_template() and "task={task}" in goose.command_template()


def test_goose_output_cap_stays_under_the_chat_ceiling() -> None:
    """goose 1.54 sends the model's whole context as max_tokens unless GOOSE_MAX_TOKENS caps it (2026-10-25)."""
    env = agent_drivers.GOOSE.env({}, "http://gw")
    assert int(env["GOOSE_MAX_TOKENS"]) == 32_000   # agent.max_output_tokens default
    capped = agent_drivers.GOOSE.env({"max_output_tokens": 10 * gateway_dialects.DEFAULT_MAX_OUTPUT_TOKENS}, "x")
    assert int(capped["GOOSE_MAX_TOKENS"]) == gateway_dialects.DEFAULT_MAX_OUTPUT_TOKENS


def test_a_run_the_gateway_refused_throughout_is_gateway_refused(dirs: dict[str, Path],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-10-25: goose 1.54 sent session_id, every call was refused, and the run read as a transient
    agent-error. Zero calls with policy refusals is its own state, carrying the refusal reasons."""
    _launchable(dirs, monkeypatch)
    fake_popen = agent_run.subprocess.Popen
    reason = "chat: body key 'session_id' is not allowed on the chat route (deny by default)"

    def popen_refused(argv: list[str], stdout: object = None, **kw: object) -> object:
        gw = dirs["run"] / "gateway"
        gw.mkdir(parents=True, exist_ok=True)
        (gw / "spend.json").write_text(json.dumps({"calls": 0, "refused": 4, "refusal_reasons": [reason]}))
        if hasattr(stdout, "write"):
            stdout.write("Ran into this error: Request failed: Bad request (400).\nPlease retry if you think this "
                         "is a transient or recoverable error.\n")
            stdout.flush()
        return fake_popen(argv, stdout=stdout, **kw)

    monkeypatch.setattr(agent_run.subprocess, "Popen", popen_refused)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert st["run_state"] == "gateway-refused", st
    assert "session_id" in st["reason"] and "recurs on every run" in st["reason"] and "transient" not in st["reason"]
    assert "gateway-refused" in agent_run.RUN_STATES


def test_a_run_stopped_by_the_token_cap_is_cap_reached(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-10-27: the agent polled request_status until the 10M-token run cap refused the next reservation; the
    run read as a generic agent-error. A cap refusal in spend.json is its own state, naming the cap."""
    _launchable(dirs, monkeypatch, rc=1)
    fake_popen = agent_run.subprocess.Popen
    reason = "chat: reservation refused: this call could use 140000 tokens; 2000 of the 10000000-token run cap are left"

    def popen_capped(argv: list[str], stdout: object = None, **kw: object) -> object:
        gw = dirs["run"] / "gateway"
        gw.mkdir(parents=True, exist_ok=True)
        (gw / "spend.json").write_text(json.dumps({"calls": 92, "refused": 1, "refusal_reasons": [reason]}))
        return fake_popen(argv, stdout=stdout, **kw)

    monkeypatch.setattr(agent_run.subprocess, "Popen", popen_capped)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert st["run_state"] == "cap-reached", st
    assert "run cap" in st["reason"] and "(was agent-error)" in st["reason"]
    assert agent_run.cap_reason({"refusal_reasons": ["chat: body key 'x' is not allowed"]}) == ""
    assert agent_run.cap_reason({"refusal_reasons": ["chat: run budget exhausted ($2.00 spent)"]})
    assert agent_run.cap_reason(None) == ""
