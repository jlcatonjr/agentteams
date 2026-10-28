"""Plan A3: goose's self-test runs it headless against a local chat stub and passes every request it sent through
the gateway's own policy (:func:`agentteams.vulnscan.selftest_canary.goose_checks`).

goose 1.54 (the 2026-10-24 image) sent ``session_id`` and a whole-context ``max_tokens``; the self-test passed and
every live call was refused. These tests pin the probe that turns that into a failed self-test. The request shape is
the one goose 1.54 sent on 2026-10-26 (its ``llm_request`` log), with the phase-A fixes in place.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agentteams.vulnscan import agent_drivers, selftest_canary
from agentteams.vulnscan.selftest_canary import StubRun

MODEL = agent_drivers.GOOSE_SELFTEST_MODEL
FN_TOOL = {"type": "function", "function": {"name": "analyze", "parameters": {"type": "object"}}}


def _goose_body(**over: Any) -> dict[str, Any]:
    body = {"model": MODEL, "messages": [{"role": "system", "content": "x"}, {"role": "user", "content": "ok?"}],
            "tools": [FN_TOOL], "max_tokens": 32000, "stream": True, "stream_options": {"include_usage": True},
            "user": "20261026_2", "session_id": "20261026_2", "transforms": ["middle-out"],
            "usage": {"include": True}}
    body.update(over)
    return {k: v for k, v in body.items() if v is not None}


def _run(*bodies: dict[str, Any], rc: int | None = 0) -> StubRun:
    reqs = [{"path": "/api/v1/chat/completions", "body": b, "text": json.dumps(b)} for b in bodies]
    return StubRun(rc, requests=reqs)


def test_goose_154_requests_pass_the_gateway_policy() -> None:
    checks, detail = selftest_canary.evaluate_goose(_run(_goose_body(), _goose_body()), frozenset({MODEL}))
    assert checks == {"goose_headless_ran": True, "goose_wire_compatible": True}
    assert detail["requests"] == 2 and detail["wire_problems"] == []
    assert "'session_id'" in detail["body_keys"]


def test_a_request_the_gateway_would_refuse_fails_the_probe() -> None:
    # the 2026-10-24 failure: goose asked for the model's whole context as max_tokens
    checks, detail = selftest_canary.evaluate_goose(_run(_goose_body(), _goose_body(max_tokens=943718)),
                                                    frozenset({MODEL}))
    assert checks == {"goose_headless_ran": True, "goose_wire_compatible": False}
    assert len(detail["wire_problems"]) == 1 and detail["wire_problems"][0].startswith("request 1: ")
    assert "between 1 and" in detail["wire_problems"][0]
    # a body key the chat route does not allow (deny by default)
    checks, detail = selftest_canary.evaluate_goose(_run(_goose_body(prediction={"type": "content"})),
                                                    frozenset({MODEL}))
    assert not checks["goose_wire_compatible"] and "not allowed on the chat route" in detail["wire_problems"][0]
    # another model than the one goose was told to use
    assert not selftest_canary.evaluate_goose(_run(_goose_body(model="x/other")), frozenset({MODEL}))[0][
        "goose_wire_compatible"]


def test_no_request_or_a_failed_run_fails() -> None:
    assert selftest_canary.evaluate_goose(_run(), frozenset({MODEL}))[0] == {
        "goose_headless_ran": False, "goose_wire_compatible": False}
    assert selftest_canary.evaluate_goose(_run(_goose_body(), rc=1), frozenset({MODEL}))[0] == {
        "goose_headless_ran": False, "goose_wire_compatible": True}
    bad = StubRun(0, requests=[{"path": "/api/v1/chat/completions", "body": None, "text": "not json"}])
    assert selftest_canary.evaluate_goose(bad, frozenset({MODEL}))[1]["wire_problems"] == [
        "request 0: not a JSON object"]


FAKE_GOOSE = r'''
import json, os, sys, urllib.request
body = json.loads(sys.argv[1])
req = urllib.request.Request(os.environ["OPENROUTER_HOST"] + "/api/v1/chat/completions",
                             data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
reply = urllib.request.urlopen(req, timeout=10).read().decode()
sys.exit(0 if "ok" in reply and os.environ.get("GOOSE_MODEL") == body["model"] else 3)
'''


def test_goose_checks_runs_the_cli_against_the_stub(tmp_path: Path) -> None:
    fake = tmp_path / "goose.py"
    fake.write_text(FAKE_GOOSE, encoding="utf-8")
    good = json.dumps(_goose_body())
    checks, detail = selftest_canary.goose_checks(["goose", good], {"GOOSE_MODEL": MODEL}, model=MODEL,
                                                  binary=[sys.executable, str(fake)], timeout=30)
    assert checks == {"goose_headless_ran": True, "goose_wire_compatible": True}, detail
    bad = json.dumps(_goose_body(max_tokens=943718))
    checks, detail = selftest_canary.goose_checks(["goose", bad], {"GOOSE_MODEL": MODEL}, model=MODEL,
                                                  binary=[sys.executable, str(fake)], timeout=30)
    assert checks == {"goose_headless_ran": True, "goose_wire_compatible": False}, detail


def test_the_goose_driver_probes_the_command_a_run_uses() -> None:
    entries = agent_drivers.GOOSE.selftest_entries()
    assert len(entries) == 3 and "goose_checks" in entries[2]
    assert '"--recipe", "/work/run/agentdef/recipe.yaml"' in entries[2]
    assert json.dumps({"GOOSE_MODEL": MODEL}) in entries[2]
    override = ["goose", "run", "--recipe", "{recipe}", "--name", "{session}", "--params", "task={task}", "--x"]
    probe = agent_drivers.GOOSE.selftest_entries(override)[2]
    assert '"--x"' in probe and "{recipe}" not in probe and "/work/run/agentdef/recipe.yaml" in probe
