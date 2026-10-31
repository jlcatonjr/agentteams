"""Tests for agentteams/wasm_agent/transcript.py (WASM P05, R4): Stage 0's hash-chained transcript."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agentteams.wasm_agent import protocol as P
from agentteams.wasm_agent.launch import serve_control
from agentteams.wasm_agent.transcript import (
    FileJournal, JournalError, PipeJournal, reserved_mac, run_dir_path, verify_transcript,
)

RUN = "ab" * 16
SECRET = "sk-test-not-a-real-key-0123456789"


def _root(tmp_path: Path) -> Path:
    root = Path(os.path.realpath(tmp_path)) / "proj"
    root.mkdir()
    return root


def _journal(root: Path, **kw: object) -> FileJournal:
    return FileJournal(root, RUN, secrets=[SECRET], **kw)   # type: ignore[arg-type]


def _fill(j: FileJournal) -> None:
    j.append("run-start", {"agent": "a"})
    j.append("guest-step", {"step": 0})
    j.append("run-end", {"status": "ok"})


def _lines(j: FileJournal) -> list[bytes]:
    return j.path.read_bytes().splitlines()


def test_chain_verifies_and_modes(tmp_path: Path) -> None:
    j = _journal(_root(tmp_path))
    _fill(j)
    j.close()
    rep = verify_transcript(j.path)
    assert rep.ok and rep.complete and rep.records == 3 and rep.head == j.head and rep.status == "ok"
    assert stat.S_IMODE(os.stat(j.run_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(j.path).st_mode) == 0o600
    j.write_result({"status": "ok"})
    assert stat.S_IMODE(os.stat(j.run_dir / "result.json").st_mode) == 0o600


@pytest.mark.parametrize("tamper", ["edit", "delete", "reorder"])
def test_tampering_detected(tmp_path: Path, tamper: str) -> None:
    j = _journal(_root(tmp_path))
    _fill(j)
    j.close()
    lines = _lines(j)
    if tamper == "edit":
        rec = json.loads(lines[1])
        rec["step"] = 9
        lines[1] = json.dumps(rec).encode()
    elif tamper == "delete":
        del lines[1]
    else:
        lines[1], lines[2] = lines[2], lines[1]
    j.path.chmod(0o600)
    j.path.write_bytes(b"\n".join(lines) + b"\n")
    assert not verify_transcript(j.path).ok


def test_incomplete_without_run_end_and_host_exited_on_close(tmp_path: Path) -> None:
    j = _journal(_root(tmp_path))
    j.append("run-start", {})
    rep = verify_transcript(j.path)
    assert rep.ok and not rep.complete
    j.close()                         # Stage 1 never sent run-end
    rep = verify_transcript(j.path)
    assert rep.complete and rep.status == "failed"
    assert json.loads(_lines(j)[-1])["reason"] == "host-exited"


def test_writer_keys_and_unknown_kinds_refused(tmp_path: Path) -> None:
    j = _journal(_root(tmp_path))
    for key in ("seq", "prev", "hash", "ts", "run_id", "kind"):
        with pytest.raises(JournalError):
            j.append("guest-step", {key: 1})
    with pytest.raises(JournalError):
        j.append("telepathy", {})
    with pytest.raises(JournalError):
        j.append("guest-step", {"x": "y" * (5 << 20)})
    assert j.seq == 0


def test_known_secret_withheld(tmp_path: Path) -> None:
    j = _journal(_root(tmp_path))
    j.append("run-start", {})
    j.append("model-reply", {"content": [{"type": "text", "text": f"the key is {SECRET}"}], "reserved": []})
    j.append("guest-step", {"stderr": "café " + SECRET})   # survives JSON's \\u escaping too
    assert SECRET.encode() not in j.path.read_bytes()
    kinds = [json.loads(x)["kind"] for x in _lines(j)]
    assert kinds == ["run-start", "withheld", "withheld"] and j.withheld == 2
    with pytest.raises(JournalError):
        j.write_result({"output": SECRET})


def test_reserved_mac_checked(tmp_path: Path) -> None:
    key = os.urandom(32)
    j = _journal(_root(tmp_path), mac_key=key)
    j.append("run-start", {})
    entry = {"turn": 1, "index": 0, "type": "tool_use", "id": "t1", "pid_sha256": "-", "sig_sha256": "ab"}
    j.append("model-reply", {"content": [], "reserved": [dict(entry, mac=reserved_mac(key, entry))]})
    with pytest.raises(JournalError, match="MAC"):
        j.append("model-reply", {"content": [], "reserved": [dict(entry, mac="0" * 64)]})
    with pytest.raises(JournalError, match="shape"):
        j.append("model-reply", {"content": [], "reserved": [{"turn": 1}]})


def test_symlinked_run_dir_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / ".agentteams").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / ".agentteams" / "wasm-runs").symlink_to(elsewhere)
    with pytest.raises(JournalError):
        _journal(root)
    assert not list(elsewhere.iterdir())


def test_existing_run_dir_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _journal(root).close()
    with pytest.raises(JournalError):
        _journal(root)
    with pytest.raises(ValueError):
        run_dir_path(root, "../x")


class _Prep:
    def prepare(self, *a: object, **k: object) -> None:
        raise AssertionError("not used")

    def release(self, ticket: str) -> None:
        pass


def _serve(tmp_path: Path, lines: list[bytes]) -> tuple[FileJournal, dict | None, list[dict]]:
    """Feed raw request lines to serve_control; return the journal, the result and Stage 0's replies."""
    j = _journal(_root(tmp_path))
    req_r, req_w = os.pipe()
    rep_r, rep_w = os.pipe()
    os.write(req_w, b"".join(lines))
    os.close(req_w)
    try:
        result = serve_control(P.LineReader(req_r), rep_w, j, _Prep())   # type: ignore[arg-type]
    finally:
        os.close(req_r)
        os.close(rep_w)
    replies = []
    reader = P.LineReader(rep_r)
    while (msg := reader.read()) is not None:
        replies.append(msg)
    os.close(rep_r)
    return j, result, replies


def test_pipe_records_are_chained_by_stage0_only(tmp_path: Path) -> None:
    good = P.canonical({"op": "journal", "kind": "run-start", "body": {"a": 1}}) + b"\n"
    forged = P.canonical({"op": "journal", "kind": "guest-step", "body": {"prev": "0" * 64, "hash": "x"}}) + b"\n"
    unknown = P.canonical({"op": "journal", "kind": "telepathy", "body": {}}) + b"\n"
    result = P.canonical({"op": "result", "result": {"status": "ok"}}) + b"\n"
    j, res, replies = _serve(tmp_path, [good, forged, unknown, result])
    assert res == {"status": "ok"}
    assert [r["ok"] for r in replies] == [True, False, False, True]
    assert j.seq == 1
    j.close()
    assert verify_transcript(j.path).status == "failed"     # host never sent run-end -> host-exited


def test_malformed_request_is_a_protocol_error_record(tmp_path: Path) -> None:
    j, res, _ = _serve(tmp_path, [b'{"op":"journal","kind":"run-start","body":{},"body":{}}\n'])
    assert res is None
    j.close()
    kinds = [json.loads(x)["kind"] for x in _lines(j)]
    assert kinds[0] == "protocol-error" and kinds[-1] == "run-end"
    assert verify_transcript(j.path).ok


def test_pipe_journal_needs_an_ack() -> None:
    class Dead:
        def call(self, msg: dict) -> dict:
            raise OSError("closed")

    class Refuses:
        def call(self, msg: dict) -> dict:
            return {"ok": False, "error": "disk full"}

    for control in (Dead(), Refuses()):
        with pytest.raises(JournalError):
            PipeJournal(control).append("run-start", {})
