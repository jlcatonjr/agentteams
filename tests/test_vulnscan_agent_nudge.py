"""The one nudge a goose run gets after a reply cut off at the output-token limit (rc.12).

rc.9 / rc.10 live runs (z-ai/glm-5.3): one reply spent all its output on reasoning, goose logged its output-limit
warning, exited 0 with no tool call, and the run ended ``agent-error`` without a report. Now the launcher resumes the
same goose session once with a fixed operator message (:data:`agent_outcome.NUDGE_MESSAGE`). Shares
``test_vulnscan_agent``'s fixtures; the container client is faked, nothing runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import agent_drivers, agent_outcome, agent_run, report_store
from tests.test_vulnscan_agent import (  # noqa: F401  (dirs is a fixture)
    _a,
    _FakeProc,
    _launchable,
    dirs,
)

CUT_OFF = f"thinking...\n{agent_drivers.GOOSE_OUTPUT_LIMIT} (max_tokens 16384)\n"
SPEND = {"budget_usd": 2.0, "max_tokens": 10**7, "spent_usd": 0.12, "calls": 2, "unmetered": 0,
         "prompt_tokens": 90000, "completion_tokens": 32000, "refusal_reasons": []}


def _agent(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, calls: list[list[str]],
           outputs: list[str], *, report_on: int | None = None, spend: dict[str, Any] | None = None) -> None:
    """Fake agent containers: the n-th writes ``outputs[n]`` to the transcript (and the report if ``report_on``)."""
    seen = {"n": 0}

    def popen(argv: list[str], stdout: Any = None, **kw: Any) -> _FakeProc:
        n = seen["n"]
        seen["n"] += 1
        (dirs["run"] / "gateway" / "spend.json").write_text(json.dumps(spend or SPEND), encoding="utf-8")
        if hasattr(stdout, "write"):
            stdout.write(outputs[n] if n < len(outputs) else "")
            stdout.flush()
        if report_on == n:
            report_store.save(dirs["run"], "# report", request_id="e" * 32)
        return _FakeProc(calls, argv, 0)
    monkeypatch.setattr(agent_run.subprocess, "Popen", popen)


def _containers(calls: list[list[str]]) -> list[list[str]]:
    return [c[1:] for c in calls if c[0] == "popen"]


def test_a_cut_off_goose_run_is_resumed_once_and_completes(dirs: dict[str, Path],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, [CUT_OFF, "submitted\n"], report_on=1)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    first, second = _containers(calls)
    session = f"vulnscan-{dirs['run'].name}"
    i = second.index("goose")
    assert second[i:] == ["goose", "run", "--resume", "--name", session, "--with-extension",
                          agent_drivers.MCP_COMMAND, "--text", agent_outcome.NUDGE_MESSAGE]
    assert first[:i] == second[:i]                                       # the same isolation, mounts and env
    assert st["run_state"] == "completed" and st["nudged"] is True and st["report"], st
    log = (dirs["run"] / "agent" / "transcript.log").read_text(encoding="utf-8")
    assert CUT_OFF in log and "submitted" in log                         # one transcript, both attempts
    assert json.loads((dirs["run"] / "agent" / "status.json").read_text())["nudged"] is True


def test_a_second_failure_keeps_agent_error_and_says_why(dirs: dict[str, Path],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, [CUT_OFF, CUT_OFF])
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert len(_containers(calls)) == 2                                  # one retry, never more
    assert st["run_state"] == "agent-error" and st["nudged"] is True, st
    assert agent_drivers.OUTPUT_LIMIT_ERROR in st["reason"] and "resumed once" in st["reason"], st


def test_a_run_that_reported_is_not_nudged(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, [CUT_OFF], report_on=0)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert len(_containers(calls)) == 1 and "nudged" not in st and "nudge_skipped" not in st, st


def test_another_error_is_not_nudged(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, ["Ran into this error: boom\n"])
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert len(_containers(calls)) == 1 and st["run_state"] == "agent-error" and "nudged" not in st, st


@pytest.mark.parametrize(("spend", "why"), [
    ({**SPEND, "spent_usd": 2.0}, "budget is used up"),
    ({**SPEND, "max_tokens": 122000}, "token cap is used up"),
    ({**SPEND, "refusal_reasons": ["chat: run budget exhausted ($2.00 spent)"]}, "run cap refused"),
])
def test_no_nudge_when_the_gateway_has_no_room(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
                                               spend: dict[str, Any], why: str) -> None:
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, [CUT_OFF], spend=spend)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert len(_containers(calls)) == 1 and why in st["nudge_skipped"], st
    assert "nudged" not in st


def test_no_nudge_without_enough_time_left(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, [CUT_OFF])
    a = {**_a(dirs), "max_minutes": 1}                                   # under MIN_NUDGE_SECONDS left at most
    st = agent_run.run_agent({"agent": a}, dirs["run"])
    assert len(_containers(calls)) == 1 and "s of the run's time" in st["nudge_skipped"], st


def test_no_nudge_without_the_gateway_totals(dirs: dict[str, Path]) -> None:
    (dirs["run"] / "agent").mkdir(parents=True, exist_ok=True)
    (dirs["run"] / "agent" / "transcript.log").write_text(CUT_OFF, encoding="utf-8")
    assert "spend.json is missing" in agent_outcome.nudge_skip_reason(_a(dirs), dirs["run"], 3600)
    (dirs["run"] / "gateway").mkdir(parents=True, exist_ok=True)
    (dirs["run"] / "gateway" / "spend.json").write_text(json.dumps({**SPEND, "spent_usd": "x"}), encoding="utf-8")
    assert "no usable totals" in agent_outcome.nudge_skip_reason(_a(dirs), dirs["run"], 3600)


def test_no_nudge_with_a_command_override(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    a = {**_a(dirs), "command": ["goose", "run", "--recipe", "{recipe}", "--name", "{session}",
                                 "--params", "task={task}"]}
    (dirs["run"] / "agent").mkdir(parents=True, exist_ok=True)
    (dirs["run"] / "agent" / "transcript.log").write_text(CUT_OFF, encoding="utf-8")
    assert "agent.command" in agent_outcome.nudge_skip_reason(a, dirs["run"], 3600)


def test_other_drivers_are_never_nudged(dirs: dict[str, Path]) -> None:
    (dirs["run"] / "agent").mkdir(parents=True, exist_ok=True)
    (dirs["run"] / "agent" / "transcript.log").write_text(CUT_OFF, encoding="utf-8")
    for fw in ("claude", "codex", "copilot"):
        assert agent_outcome.nudge_skip_reason({**_a(dirs), "framework": fw}, dirs["run"], 3600) is None


def test_the_nudge_message_is_fixed_and_short() -> None:
    assert "submit_report" in agent_outcome.NUDGE_MESSAGE and len(agent_outcome.NUDGE_MESSAGE) < 400
    cmd = agent_drivers.GOOSE.resume_command("vulnscan-x", agent_outcome.NUDGE_MESSAGE)
    assert cmd[:5] == ["goose", "run", "--resume", "--name", "vulnscan-x"] and "--recipe" not in cmd
