"""Tests for agentteams/wasm_agent/protocol.py (WASM P05): closed, versioned, size-capped step-mode frames."""

from __future__ import annotations

import json
import os

import pytest

from agentteams.wasm_agent import protocol as P

IDS = frozenset({"t1", "t2"})
TOOLS = frozenset({"read_file", "run_code"})


def _op(obj: object) -> bytes:
    return json.dumps(obj).encode() + b"\n"


def _decode(raw: bytes, ids: frozenset[str] = IDS) -> P.Op:
    return P.decode_op(raw, expect_ids=ids, toolset=TOOLS, delegates=frozenset({"helper"}))


def test_round_trip_each_op() -> None:
    m = _decode(_op({"v": 1, "op": "model", "tool_results": [{"tool_use_id": "t1", "ok": True, "content": "x"}],
                     "continue": False, "carry": {"k": [1]}}))
    assert isinstance(m, P.ModelOp) and m.tool_results == (P.ToolResult("t1", True, "x"),) and m.carry == {"k": [1]}
    t = _decode(_op({"v": 1, "op": "tool", "id": "t2", "name": "run_code", "input": {"a": 1}, "carry": None}))
    assert isinstance(t, P.ToolOp) and (t.id, t.name, dict(t.input)) == ("t2", "run_code", {"a": 1})
    d = _decode(_op({"v": 1, "op": "delegate", "id": "d1", "agent": "helper", "task": "x", "budget": None,
                     "carry": None}))
    assert isinstance(d, P.DelegateOp) and d.agent == "helper"
    assert _decode(_op({"v": 1, "op": "done", "status": "failed", "reason": "gave-up"})) == P.DoneOp("failed", "gave-up")
    assert _decode(b'{"v":1,"op":"done","status":"ok"}') == P.DoneOp("ok", "")   # trailing LF optional


@pytest.mark.parametrize("raw, code", [
    (b'{"v":1,"op":"done","status":"ok","status":"failed"}\n', "json"),     # duplicate key
    (b'{"v":1,"op":"done","status":"ok","x":NaN}\n', "json"),
    (b'{"v":1,"op":"done","status":"ok","x":Infinity}\n', "json"),
    (b'{"v":1,"op":"done","status":"ok\\u0000"}\n', "json"),               # NUL
    (b"\xef\xbb\xbf" + b'{"v":1,"op":"done","status":"ok"}\n', "json"),   # BOM
    (b'{"v":1,"op":"done","status":"ok"}\n{"v":1}\n', "frame"),             # two lines
    (b"", "frame"), (b"\n", "frame"), (b"   \n", "frame"),
    (b'{"v":1,"op":"done","status":"ok"}\r\n', "frame"),
    (b'[1]\n', "shape"),
    (b'{"v":1,"op":"explode"}\n', "shape"),
    (b'{"v":1,"op":"done","status":"ok","extra":1}\n', "shape"),
    (b'{"v":2,"op":"done","status":"ok"}\n', "protocol-version"),
    (b'{"v":true,"op":"done","status":"ok"}\n', "protocol-version"),
    (b'{"op":"done","status":"ok"}\n', "protocol-version"),
    (b'{"v":1,"op":"done","status":"maybe"}\n', "shape"),
    (b'{"v":1,"op":"tool","id":"t1","name":"write_file","input":{},"carry":null}\n', "not-granted"),
    (b'{"v":1,"op":"tool","id":"bad id","name":"run_code","input":{},"carry":null}\n', "shape"),
    (b'{"v":1,"op":"delegate","id":"d","agent":"stranger","task":"x","budget":null,"carry":null}\n', "not-granted"),
])
def test_refusals(raw: bytes, code: str) -> None:
    with pytest.raises(P.ProtocolError) as exc:
        _decode(raw)
    assert exc.value.code == code


def test_depth_33_refused_and_32_accepted() -> None:
    def nest(n: int) -> object:
        v: object = 0
        for _ in range(n):
            v = [v]
        return v
    ok = {"v": 1, "op": "tool", "id": "t1", "name": "run_code", "input": {}, "carry": nest(30)}
    assert isinstance(_decode(_op(ok)), P.ToolOp)
    bad = dict(ok, carry=nest(40))
    with pytest.raises(P.ProtocolError) as exc:
        _decode(_op(bad))
    assert exc.value.code == "json"


def test_size_caps() -> None:
    big = {"v": 1, "op": "done", "status": "ok", "reason": "x"}
    raw = _op(big).rstrip(b"\n") + b" " * P.MAX_OP_BYTES
    with pytest.raises(P.ProtocolError, match="frame"):
        _decode(raw)
    carry = {"v": 1, "op": "tool", "id": "t1", "name": "run_code", "input": {}, "carry": "a" * (P.MAX_CARRY_BYTES + 1)}
    with pytest.raises(P.ProtocolError) as exc:
        _decode(_op(carry))
    assert exc.value.code == "frame"
    event = {"run_id": "0" * 32, "step": 0, "agent": {}, "mounts": [], "limits": {}, "carry": None,
             "event": {"kind": "tool_result", "id": "t1", "ok": True, "output": "x" * P.MAX_EVENT_BYTES}}
    with pytest.raises(P.ProtocolError, match="frame"):
        P.encode_event(event)


def test_expect_ids_exactly_once() -> None:
    dup = {"v": 1, "op": "model", "continue": False, "carry": None,
           "tool_results": [{"tool_use_id": "t1", "ok": True, "content": ""}] * 2}
    with pytest.raises(P.ProtocolError) as exc:
        _decode(_op(dup))
    assert exc.value.code == "duplicate-id"
    unknown = dict(dup, tool_results=[{"tool_use_id": "zz", "ok": True, "content": ""}])
    with pytest.raises(P.ProtocolError) as exc:
        _decode(_op(unknown))
    assert exc.value.code == "unknown-id"
    partial = dict(dup, tool_results=[{"tool_use_id": "t2", "ok": False, "content": "e"}])
    assert len(_decode(_op(partial)).tool_results) == 1   # a missing id is the host's to fill


def test_encode_event_shape_and_version() -> None:
    event = {"run_id": "ab" * 16, "step": 3, "agent": {"slug": "a", "tools": []}, "mounts": [], "limits": {},
             "carry": None, "event": {"kind": "start"}}
    frame = json.loads(P.encode_event(event))
    assert frame["v"] == P.PROTOCOL_VERSION and frame["accept"] == [1] and frame["type"] == "event"
    with pytest.raises(P.ProtocolError):
        P.encode_event(dict(event, event={"kind": "telepathy"}))
    with pytest.raises(P.ProtocolError):
        P.encode_event(dict(event, run_id="nothex"))
    with pytest.raises(P.ProtocolError):
        P.encode_event({**event, "extra": 1})


def test_control_lines_round_trip_and_cap() -> None:
    r, w = os.pipe()
    try:
        P.write_line(w, {"op": "journal", "n": 1})
        P.write_line(w, {"op": "release", "ticket": "x"})
        os.close(w)
        reader = P.LineReader(r)
        assert reader.read() == {"op": "journal", "n": 1}
        assert reader.read() == {"op": "release", "ticket": "x"}
        assert reader.read() is None
    finally:
        os.close(r)
    r, w = os.pipe()
    try:
        os.write(w, b'{"a":1')
        os.close(w)
        with pytest.raises(P.ProtocolError, match="mid-line"):
            P.LineReader(r).read()
    finally:
        os.close(r)
    r, w = os.pipe()
    try:
        os.write(w, b"x" * 300 + b"\n")
        os.close(w)
        with pytest.raises(P.ProtocolError, match="exceeds"):
            P.LineReader(r, cap=100).read()
    finally:
        os.close(r)
