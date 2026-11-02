"""Tests for the WASM agent host loop (agentteams/wasm_agent/host.py + mediation.py, WASM P05).

Pure Python: guests are ``FnBroker`` functions, the model is P06's StubProvider, the transcript a real FileJournal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentteams import model_providers as MP
from agentteams.wasm_agent import host as H
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.mediation import MediationError, ReservedKeyVault
from agentteams.wasm_agent.transcript import JournalError, revoke_run, verify_transcript
from tests._wasm_agent_helpers import (
    KEY, RUN, FnBroker, no_network, options, project, records, reference_guest, result, spec, stub, text_reply,
    tool_reply,
)

__all__ = ["no_network"]


def _run(tmp_path: Path, script: Any, fn: Any = None, **kw: Any) -> tuple[H.RunResult, MP.Provider, FnBroker, Path]:
    root = project(tmp_path)
    provider = stub(script)
    broker = FnBroker(fn or reference_guest({"/project/docs/a.md": "doc text\n"}), kw.pop("code_fn", None))
    s = kw.pop("agent", None) or spec(root)
    res = H.run_agent(s, kw.pop("task", "Summarise the docs."), options(root, broker, provider, **kw))
    return res, provider, broker, root


def test_text_only_end_turn_is_ok(tmp_path: Path) -> None:
    res, provider, _, root = _run(tmp_path, [text_reply("the answer")])
    assert (res.status, res.reason, res.output) == ("ok", "done", "the answer")
    assert provider._stub.requests[0]["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "Summarise the docs."}]}]
    rep = verify_transcript(root / ".agentteams" / "wasm-runs" / RUN / "transcript.jsonl")
    assert rep.ok and rep.complete and rep.head == res.transcript_head


def test_in_guest_tool_result_reaches_the_next_request(tmp_path: Path) -> None:
    res, provider, broker, _ = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/project/docs/a.md"})),
                                               text_reply("done")])
    assert res.status == "ok"
    second = provider._stub.requests[1]["messages"]
    assert second[-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                                        "content": "doc text\n", "is_error": False}]}
    assert broker.frames[0]["mounts"] == [{"guest": "/project/docs", "mode": "ro"}]


def test_host_tool_result_is_host_held(tmp_path: Path) -> None:
    res, provider, broker, root = _run(tmp_path, [tool_reply(("t1", "run_code", {"language": "python",
                                                                                 "source": "print(42)"})),
                                                  text_reply("ok")])
    assert res.status == "ok" and broker.code_runs == [("python", "print(42)", b"")]
    sent = provider._stub.requests[1]["messages"][-1]["content"][0]
    assert sent["tool_use_id"] == "t1" and "42" in sent["content"] and "guest's view" not in sent["content"]
    assert [r for r in records(root) if r["kind"] == "host-tool"][0]["ok"] is True


def test_host_fills_missing_ids(tmp_path: Path) -> None:
    def lazy(frame: dict) -> dict:
        if frame["event"]["kind"] == "model_reply" and frame["event"]["stop"] == "tool_use":
            return {"v": 1, "op": "model", "tool_results": [], "continue": False, "carry": None}
        return reference_guest()(frame)
    res, provider, _, _ = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/x"})), text_reply("k")], lazy)
    assert res.status == "ok"
    block = provider._stub.requests[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "no result" in block["content"]


def test_three_failed_steps_fail_the_run(tmp_path: Path) -> None:
    res, provider, _, root = _run(tmp_path, [text_reply("x")], lambda f: result(b"", exit_code=1, trap="fuel"))
    assert (res.status, res.reason) == ("failed", "guest-unhealthy")
    assert provider._stub.requests == []
    steps = [r for r in records(root) if r["kind"] == "guest-step"]
    assert len(steps) == 3 and steps[0]["error"] == "trap:fuel" and steps[0]["os_confinement"] == "none"


def test_failed_step_with_pending_uses_answers_them(tmp_path: Path) -> None:
    def flaky(frame: dict) -> Any:
        if frame["event"]["kind"] == "model_reply" and frame["event"]["stop"] == "tool_use":
            return result(b"not json\n")
        return reference_guest()(frame)
    res, provider, _, _ = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/x"})), text_reply("k")], flaky)
    assert res.status == "ok"
    block = provider._stub.requests[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and block["content"].startswith("tool execution failed: json")


def test_protocol_version_fails_at_once(tmp_path: Path) -> None:
    res, _, _, _ = _run(tmp_path, [text_reply("x")], lambda f: {"v": 2, "op": "done", "status": "ok"})
    assert (res.status, res.reason) == ("failed", "protocol-version")


def test_done_ok_with_pending_tool_use_refused(tmp_path: Path) -> None:
    seen: list[str] = []

    def eager(frame: dict) -> dict:
        ev = frame["event"]
        seen.append(ev["kind"])
        if ev["kind"] == "start":
            return {"v": 1, "op": "model", "tool_results": [], "continue": False, "carry": None}
        if ev["kind"] == "model_reply":
            return {"v": 1, "op": "done", "status": "ok"}
        return {"v": 1, "op": "done", "status": "failed", "reason": "gave-up"}
    res, _, _, _ = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/x"}))], eager)
    assert seen == ["start", "model_reply", "denied"] and (res.status, res.reason) == ("failed", "guest:gave-up")
    assert res.output == ""


def test_ungranted_tool_op_is_a_failed_step(tmp_path: Path) -> None:
    def rogue(frame: dict) -> dict:
        if frame["event"]["kind"] == "model_reply":
            return {"v": 1, "op": "tool", "id": "t1", "name": "write_file", "input": {}, "carry": None}
        return reference_guest()(frame)
    res, _, _, root = _run(tmp_path, [tool_reply(("t1", "write_file", {})), text_reply("k")], rogue)
    errors = {r["error"] for r in records(root) if r["kind"] == "guest-step" and r["error"]}
    assert "not-granted" in errors and res.status in ("ok", "failed")


def test_model_asking_for_an_ungranted_tool_gets_a_host_error(tmp_path: Path) -> None:
    res, provider, _, _ = _run(tmp_path, [tool_reply(("t1", "write_file", {"path": "/scratch/x", "content": ""})),
                                          text_reply("k")])
    assert res.status == "ok"
    block = provider._stub.requests[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "not granted" in block["content"]


def test_tool_op_must_match_the_models_input(tmp_path: Path) -> None:
    def swap(frame: dict) -> dict:
        ev = frame["event"]
        if ev["kind"] == "model_reply" and ev["stop"] == "tool_use":
            return {"v": 1, "op": "tool", "id": "t1", "name": "run_code",
                    "input": {"language": "python", "source": "import os"}, "carry": None}
        return reference_guest()(frame)
    res, _, broker, _ = _run(tmp_path, [tool_reply(("t1", "run_code", {"language": "python", "source": "1"}))], swap)
    assert broker.code_runs == [] and res.reason == "guest:denied-refused"


def test_delegate_is_refused_until_p09(tmp_path: Path) -> None:
    root = project(tmp_path / "s")
    s = spec(root)
    s = H.AgentSpec(**{**s.__dict__, "delegates": frozenset({"helper"})})

    def deleg(frame: dict) -> dict:
        if frame["event"]["kind"] == "model_reply":
            return {"v": 1, "op": "delegate", "id": "d1", "agent": "helper", "task": "x", "budget": None,
                    "carry": None}
        return reference_guest()(frame)
    res, _, broker, _ = _run(tmp_path, [text_reply("x")], deleg, agent=s)
    assert broker.frames[-1]["event"]["code"] == "refused" and res.reason == "guest:denied-refused"


def test_continue_after_max_tokens(tmp_path: Path) -> None:
    res, provider, _, _ = _run(tmp_path, [text_reply("part one", "max_tokens"), text_reply("part two")])
    assert res.status == "ok" and res.output == "part two"
    assert provider._stub.requests[1]["messages"][-1]["content"][0]["text"].startswith("Continue")


def test_continue_refused_after_end_turn(tmp_path: Path) -> None:
    def cont(frame: dict) -> dict:
        if frame["event"]["kind"] == "model_reply":
            return {"v": 1, "op": "model", "tool_results": [], "continue": True, "carry": None}
        return reference_guest()(frame)
    res, provider, _, _ = _run(tmp_path, [text_reply("x")], cont)
    assert len(provider._stub.requests) == 1 and res.reason == "guest:denied-refused"


def test_budget_denied_once_then_exhausted(tmp_path: Path) -> None:
    res, provider, broker, _ = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/x"})), text_reply("y")],
                                    budget=Budget(max_model_calls=1))
    assert broker.frames[-1]["event"] == {"kind": "denied", "code": "budget", "message":
                                         "budget exhausted: max_model_calls"}
    assert res.status == "failed" and res.reason == "guest:denied-budget" and len(provider._stub.requests) == 1


def test_step_budget_ends_the_run(tmp_path: Path) -> None:
    res, _, _, _ = _run(tmp_path, [text_reply("x")], budget=Budget(max_steps=1))
    assert (res.status, res.reason) == ("budget-exhausted", "max_steps")


def test_usd_cap_without_price_refuses_at_start(tmp_path: Path) -> None:
    res, provider, broker, _ = _run(tmp_path, [text_reply("x")], budget=Budget(max_usd=1.0))
    assert (res.status, res.reason) == ("refused", "usd-unpriced") and broker.frames == []
    res, _, _, _ = _run(tmp_path / "b", [{"content": [{"type": "text", "text": "x"}], "usage": {
        "input_tokens": 10, "output_tokens": 10}}], budget=Budget(max_usd=1.0),
        prices={"test-model": {"input_per_mtok": 1.0, "output_per_mtok": 2.0, "cache_read_per_mtok": 1.0,
                               "cache_write_per_mtok": 2.0}})
    assert res.status == "ok" and res.usage["usd"] == pytest.approx(3e-5)


def test_journal_failure_blocks_the_model_call(tmp_path: Path) -> None:
    class Flaky:
        def append(self, kind: str, body: dict) -> str:
            if kind == "model-request":
                raise JournalError("disk full")
            return "h"
    res, provider, _, _ = _run(tmp_path, [text_reply("x")], journal=Flaky())
    assert (res.status, res.reason) == ("failed", "journal-unwritable") and provider._stub.requests == []


def test_output_is_last_model_text_not_guest_authored(tmp_path: Path) -> None:
    res, _, _, _ = _run(tmp_path, [text_reply("model says hi")],
                        lambda f: {"v": 1, "op": "model", "tool_results": [], "continue": False, "carry": "x"}
                        if f["event"]["kind"] == "start" else {"v": 1, "op": "done", "status": "ok",
                                                               "reason": "i-wrote-this"})
    assert res.output == "model says hi"


# --- DLP (S-2) ---------------------------------------------------------------------------------------------------

def test_secret_in_task_refused_before_the_model(tmp_path: Path) -> None:
    res, provider, broker, root = _run(tmp_path, [text_reply("x")], task=f"use {KEY} please")
    assert res.status == "refused" and res.reason == "dlp:known-secret"
    assert provider._stub.requests == [] and broker.frames == []
    assert KEY not in (root / ".agentteams" / "wasm-runs" / RUN / "transcript.jsonl").read_text()


def test_secret_in_a_file_read_is_withheld_and_the_call_proceeds(tmp_path: Path) -> None:
    aws = "AKIA" + "Q3XJ7PLM2RTZV9WE"
    res, provider, _, root = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/f"})), text_reply("k")],
                                  reference_guest({"/f": f"key={aws}"}))
    assert res.status == "ok"
    block = provider._stub.requests[1]["messages"][-1]["content"][0]
    assert block["content"] == "[withheld by DLP: aws-access-key]" and block["is_error"] is True
    dlp = [r for r in records(root) if r["kind"] == "dlp"]
    assert dlp and dlp[0]["blocked"] == ["aws-access-key"] and aws not in json.dumps(dlp)


def test_home_path_redacted(tmp_path: Path) -> None:
    home_file = "/" + "Users" + "/somebody/notes.txt"     # built at runtime: no literal home path in the source
    res, provider, _, _ = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/f"})), text_reply("k")],
                               reference_guest({"/f": "see " + home_file}))
    assert provider._stub.requests[1]["messages"][-1]["content"][0]["content"] == "see ~/notes.txt"


def test_provider_echoing_the_key_fails_the_run(tmp_path: Path) -> None:
    res, _, broker, root = _run(tmp_path, [text_reply(f"here is {KEY}")])
    assert (res.status, res.reason) == ("failed", "provider-echoed-secret")
    assert all(f["event"]["kind"] == "start" for f in broker.frames)
    assert KEY not in (root / ".agentteams" / "wasm-runs" / RUN / "transcript.jsonl").read_text()


def test_key_never_reaches_a_guest_frame(tmp_path: Path) -> None:
    res, _, broker, root = _run(tmp_path, [tool_reply(("t1", "read_file", {"path": "/project/docs/a.md"})),
                                           text_reply("fine")])
    assert res.status == "ok"
    assert all(KEY.encode() not in raw for raw in broker.raw)
    blob = b"".join(p.read_bytes() for p in (root / ".agentteams").rglob("*") if p.is_file())
    assert KEY.encode() not in blob


# --- reserved provider keys (P06 _provider_id / _provider_sig) --------------------------------------------------

SIG = '[{"type":"thinking","thinking":"hm","signature":"c2ln"}]'


def _sig_script(messages: list, system: Any, tools: Any) -> dict:
    if len(messages) == 1:
        return {"content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "/f"}}],
                "stop": "tool_use"}
    return text_reply("done")


def test_reserved_keys_stay_host_side_and_are_restored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = MP.StubProvider.reply

    def with_reserved(self: Any, model: str, messages: Any, system: Any, tools: Any, max_tokens: int) -> Any:
        reply = real(self, model, messages, system, tools, max_tokens)
        if reply.stop == "tool_use":
            block = dict(reply.content[0], _provider_id="call_raw/id", _provider_sig=SIG)
            reply = MP.ModelReply((block,), reply.stop, reply.raw_stop, reply.usage, "stub", model)
        return reply
    monkeypatch.setattr(MP.StubProvider, "reply", with_reserved)
    res, provider, broker, root = _run(tmp_path, _sig_script, reference_guest({"/f": "x"}))
    assert res.status == "ok"
    assert all(b"_provider" not in raw for raw in broker.raw)                    # never shown to a guest
    sent = provider._stub.requests[1]["messages"][1]["content"][0]
    assert sent["_provider_id"] == "call_raw/id" and sent["_provider_sig"] == SIG   # restored for the provider
    reply = [r for r in records(root) if r["kind"] == "model-reply"][0]
    assert "_provider_sig" not in json.dumps(reply["content"]) and reply["reserved"][0]["mac"]


def test_vault_refuses_injected_or_tampered_reserved_keys() -> None:
    vault = ReservedKeyVault(b"m" * 32)
    blocks, _ = vault.strip(1, [{"type": "tool_use", "id": "t1", "name": "x", "input": {},
                                 "_provider_id": "raw", "_provider_sig": SIG}])
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}, {"role": "assistant", "content": blocks}]
    assert vault.restore(msgs)[1]["content"][0]["_provider_id"] == "raw"
    forged = json.loads(json.dumps(msgs))
    forged[1]["content"][0]["_provider_sig"] = "[]"
    with pytest.raises(MediationError, match="reserved"):
        vault.restore(forged)
    moved = json.loads(json.dumps(msgs))
    moved[1]["content"][0]["id"] = "t2"                                      # a different block in that slot
    with pytest.raises(MediationError, match="MAC"):
        vault.restore(moved)
    with pytest.raises(ValueError):
        ReservedKeyVault(b"short")


# --- revocation (§4.7) -------------------------------------------------------------------------------------------

def test_revoked_sentinel_stops_at_the_next_step(tmp_path: Path) -> None:
    root = project(tmp_path)
    provider = stub([tool_reply(("t1", "read_file", {"path": "/x"})), text_reply("never")])
    steps: list[int] = []

    def guest(frame: dict) -> dict:
        steps.append(frame["step"])
        if frame["step"] == 1:
            revoke_run(root, RUN, "operator said stop")
        return reference_guest()(frame)
    res = H.run_agent(spec(root), "t", options(root, FnBroker(guest), provider))
    assert (res.status, steps, len(provider._stub.requests)) == ("revoked", [0, 1], 1)
    with pytest.raises(FileExistsError):
        revoke_run(root, RUN, "again")


def test_reply_after_revocation_is_not_delivered(tmp_path: Path) -> None:
    root = project(tmp_path)
    rev = H.Revocation(root, RUN)

    def script(messages: list, system: Any, tools: Any) -> dict:
        rev.set("signal:15")
        return text_reply("late")
    broker = FnBroker(reference_guest())
    res = H.run_agent(spec(root), "t", options(root, broker, stub(script), revocation=rev))
    assert (res.status, res.reason, res.output) == ("revoked", "revoked-after-call", "")
    assert [f["event"]["kind"] for f in broker.frames] == ["start"]


def test_ancestor_revocation_is_honoured(tmp_path: Path) -> None:
    root = project(tmp_path)
    parent = "ef" * 16
    (root / ".agentteams" / "wasm-runs" / parent).mkdir(parents=True)
    revoke_run(root, parent, "stop the tree")
    res = H.run_agent(spec(root), "t", options(root, FnBroker(reference_guest()), stub([text_reply("x")]),
                                               parent=H.RunLink(parent)))
    assert (res.status, res.reason) == ("revoked", "ancestor-revoked")
