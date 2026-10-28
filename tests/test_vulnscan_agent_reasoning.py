"""The rc.9 live runs: a reply that is all reasoning stops at the output limit before any tool call.

Both runs on 2026-10-27 (z-ai/glm-5.3) spent 53k-115k characters on reasoning, hit the output-token limit and
submitted no report; the run state named a tool-data line (``"feed_errors": [],``) as the error. Covered here:
``agent.reasoning`` / ``--chat-reasoning`` (the gateway writes the operator's object into every chat request), the
goose driver naming the output-limit warning, and ``list_requests`` returning a short, newest-first list.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import agent_drivers, agent_upstreams, gateway, mcp
from agentteams.vulnscan import gateway_dialects as d
from agentteams.vulnscan.mcp import Server
from tests.test_vulnscan_gateway_chat_policy import FN_TOOL, MODEL, _chat, _check

# ---------------------------------------------------------------- gateway: the operator's reasoning object


@pytest.mark.parametrize("value", [{"effort": "low"}, {"max_tokens": 4000}, {"enabled": False},
                                   {"effort": "minimal", "exclude": True}])
def test_valid_reasoning_settings(value: dict[str, Any]) -> None:
    assert d.reasoning_problem(value, 32_000) == ""


@pytest.mark.parametrize(("value", "why"), [
    (None, "must be an object"),
    ({"effort": "low", "max_tokens": 10}, "exactly one"),
    ({}, "exactly one"),
    ({"effort": "extreme"}, "effort must be one of"),
    ({"max_tokens": 32_000}, "below the output ceiling"),
    ({"max_tokens": True}, "max_tokens must be"),
    ({"enabled": True}, "enabled may only be false"),
    ({"effort": "low", "exclude": "yes"}, "exclude must be"),
    ({"effort": "low", "provider": "x"}, "must be an object"),
])
def test_invalid_reasoning_settings(value: object, why: str) -> None:
    assert why in d.reasoning_problem(value, 32_000)


def test_the_gateway_writes_the_operators_reasoning_into_every_chat_request() -> None:
    raw = _chat(tools=[FN_TOOL], stream=True, max_tokens=32_000)
    assert "reasoning" not in json.loads(_check(raw).body)               # unset: unchanged
    body = json.loads(_check(raw, chat_reasoning={"effort": "low"}).body)
    assert body["reasoning"] == {"effort": "low"}
    client = _chat(reasoning={"effort": "high"}, reasoning_effort="high", include_reasoning=True)
    body = json.loads(_check(client, chat_reasoning={"max_tokens": 2000}).body)
    assert body["reasoning"] == {"max_tokens": 2000}                     # operator policy wins
    assert not {"reasoning_effort", "include_reasoning"} & set(body)
    assert json.loads(_check(client).body)["reasoning"] == {"effort": "high"}           # unset: forwarded as sent


def test_main_checks_the_chat_reasoning_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    key = tmp_path / "k.env"
    key.write_text("OPENROUTER_API_KEY=sk-test-only\n", encoding="utf-8")
    for value, why in (("{nope", "must be JSON"), ('{"effort": "extreme"}', "effort must be one of")):
        with pytest.raises(SystemExit) as exc:
            gateway.main(["--model", MODEL, "--key-file", str(key), "--chat-reasoning", value])
        assert exc.value.code == 2 and f"--chat-reasoning {why}" in capsys.readouterr().err


# ---------------------------------------------------------------- agent.reasoning


def _agent(**extra: Any) -> dict[str, Any]:
    return {"model": "z-ai/glm-5.3", "key_file": "/key", "upstream": "https://openrouter.ai", **extra}


def test_agent_reasoning_adds_the_gateway_flag_only_when_set() -> None:
    _, flags = agent_upstreams.gateway_routes(_agent(), "chat")
    assert "--chat-reasoning" not in flags                               # null: the argv is unchanged
    _, flags = agent_upstreams.gateway_routes(_agent(reasoning={"effort": "low"}), "chat")
    assert flags[-2:] == ["--chat-reasoning", '{"effort": "low"}']


def test_agent_reasoning_is_validated() -> None:
    agent_upstreams.validate(_agent(reasoning={"effort": "low"}), "chat")
    agent_upstreams.validate(_agent(reasoning=None), "chat")
    with pytest.raises(ValueError, match="agent.reasoning effort must be one of"):
        agent_upstreams.validate(_agent(reasoning={"effort": "max"}), "chat")
    with pytest.raises(ValueError, match="below the output ceiling"):
        agent_upstreams.validate(_agent(reasoning={"max_tokens": 9_000}, max_output_tokens=8_000), "chat")
    with pytest.raises(ValueError, match="chat route only"):
        agent_upstreams.validate(_agent(model="claude-x", reasoning={"effort": "low"}, prices="/p"), "messages")


# ---------------------------------------------------------------- goose: name the output limit


def test_goose_names_the_output_limit_not_tool_data() -> None:
    g = agent_drivers.GOOSE
    cut = ('  ▸ list_requests python\n[\n {\n  "feed_errors": [],\n  "status": "refused"\n }\n]\n'
           "Warning: Response reached the model's output-token limit and may be incomplete.\n")
    assert g.parse_outcome(cut).error == agent_drivers.OUTPUT_LIMIT_ERROR
    tool_data = ('  ▸ get_summary python\n"feed_errors": [],\n| `a` | b | error-prone | fix |\n{"error": 1}\n')
    assert g.parse_outcome(tool_data).error == ""                        # the second rc.9 run's shape
    assert g.parse_outcome("ok\nRan into this error: boom\n").error == "Ran into this error: boom"
    assert g.parse_outcome("ok\n[ERROR] provider failed\n").error == "[ERROR] provider failed"
    indented = "  Warning: Response reached the model's output-token limit and may be incomplete.\n"
    assert g.parse_outcome(indented).error == agent_drivers.OUTPUT_LIMIT_ERROR


# ---------------------------------------------------------------- list_requests


def test_list_requests_returns_the_newest_results_first(tmp_path: Path) -> None:
    run, requests, results = (tmp_path / n for n in ("run", "requests", "results"))
    for p in (run, requests, results):
        p.mkdir()
    n = mcp.MAX_LISTED_REQUESTS + 5
    for i in range(n):
        f = results / f"{i:032x}.json"
        f.write_text(json.dumps({"id": f"{i:032x}", "op": "stage-fix", "status": "done"}))
        os.utime(f, (1_000_000 + i, 1_000_000 + i))
    out = Server(run, requests, results).call("list_requests", {})
    assert [r["id"] for r in out] == [f"{i:032x}" for i in range(n - 1, n - 1 - mcp.MAX_LISTED_REQUESTS, -1)]
