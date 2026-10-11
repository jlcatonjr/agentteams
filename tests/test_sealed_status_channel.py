"""The sandboxed orchestrator's runner channel (biographies pilot, 2026-10-11).

Claude Code applies the session's ``Read(/.agentteams/**)`` and ``Read(/.agentteams-queue/**)`` denies to sandboxed
subprocesses too (measured on 2.1.246, macOS), so the orchestrator's CLI couldn't read the runner's heartbeat or
results. The runner now publishes both in the session-readable ``.agentteams-status/`` tree, each result sealed with a
``reply_key`` that only the requesting process holds; requests are written with exclusive creates (a rename into a
read-denied directory fails).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agentteams import proposal_runner as R
from agentteams import proposals as P
from agentteams import sealed_result as S
from agentteams.cli import proposal_commands as C

from tests.test_proposal_runner import _no_env_key, key_file, project, runner  # noqa: F401  (fixtures)

RID = "ab" * 16


# --- sealing ------------------------------------------------------------------------------------


def test_seal_round_trip_and_bucketed_size():
    key = S.new_reply_key()
    data = json.dumps({"result": {"nonce": "n" * 32}}).encode()
    blob = S.seal(key, RID, data)
    assert S.open_sealed(key, RID, blob) == data
    assert len(blob) == len(S.MAGIC) + 16 + S.BUCKETS[0] + 32  # size reveals only the bucket
    assert b"nnnn" not in blob


@pytest.mark.parametrize("tamper", ["ciphertext", "tag", "iv"])
def test_tampered_blob_is_refused(tamper):
    key = S.new_reply_key()
    blob = bytearray(S.seal(key, RID, b"{}"))
    offset = {"iv": len(S.MAGIC), "ciphertext": len(S.MAGIC) + 20, "tag": len(blob) - 1}[tamper]
    blob[offset] ^= 1
    with pytest.raises(S.SealError):
        S.open_sealed(key, RID, bytes(blob))


def test_wrong_key_or_request_is_refused():
    key = S.new_reply_key()
    blob = S.seal(key, RID, b"{}")
    with pytest.raises(S.SealError):
        S.open_sealed(S.new_reply_key(), RID, blob)
    with pytest.raises(S.SealError):
        S.open_sealed(key, "cd" * 16, blob)  # a blob for one request can't be passed off as another's


def test_bad_key_and_oversize_are_refused():
    with pytest.raises(S.SealError):
        S.seal(b"short", RID, b"{}")
    with pytest.raises(S.SealError):
        S.seal(S.new_reply_key(), RID, b"x" * S.BUCKETS[-1])


# --- the runner and the client ------------------------------------------------------------------


def test_orchestrator_result_is_sealed_in_the_status_tree(runner):
    request_id = R.enqueue(runner.root, {"kind": "issue-dispatch", "agent": "producer"})
    queued = sorted(p.name for p in (runner.root / R.REQUESTS_REL).iterdir())
    assert queued == [f"{request_id}.json", f"{request_id}{R.READY_SUFFIX}"]  # no temp file, no rename
    assert json.loads((runner.root / R.REQUESTS_REL / f"{request_id}.json").read_text())["reply_key"]
    runner.serve_once()
    blob = (runner.root / R.SEALED_RESULTS_REL / f"{request_id}.sealed").read_bytes()
    assert not (runner.root / R.RESULTS_REL / f"{request_id}.json").exists()  # nothing in the clear
    result = R.wait_result(runner.root, request_id, timeout=5)
    assert result["ok"] and len(result["result"]["nonce"]) >= 16
    assert result["result"]["nonce"].encode() not in blob
    assert request_id not in R._REPLY_KEYS  # dropped once used


def test_the_runner_never_handles_the_reply_key(runner):
    request_id = R.enqueue(runner.root, {"kind": "list-staged"})
    runner.serve_once()
    result = R.wait_result(runner.root, request_id, timeout=5)
    assert result["ok"] and "reply_key" not in json.dumps(result)


def test_status_heartbeat_is_liveness(runner):
    assert R.runner_alive(runner.root)
    (runner.root / R.STATUS_HEARTBEAT_REL).unlink()
    assert not R.runner_alive(runner.root)  # the legacy heartbeat alone isn't trusted by the new client
    beat = (runner.root / R.HEARTBEAT_REL).read_text()
    assert beat.replace(".", "").isdigit()  # a timestamp only: no hostname or path


def test_a_request_without_a_reply_key_is_refused_not_answered_in_the_clear(runner):
    request_id = "ef" * 16
    path = runner.root / R.REQUESTS_REL / f"{request_id}.json"
    path.write_text(json.dumps({"kind": "issue-dispatch", "agent": "producer"}))  # an old client: no key, no marker
    old = time.time() - R.STALE_REQUEST_SECONDS - 5
    os.utime(path, (old, old))
    runner.serve_once()
    assert not (runner.root / R.SEALED_RESULTS_REL / f"{request_id}.sealed").exists()
    result = json.loads((runner.root / R.RESULTS_REL / f"{request_id}.json").read_text())
    assert not result["ok"] and "older than the runner" in result["error"] and "result" not in result


def test_an_unmarked_request_waits_until_stale(runner):
    request_id = "12" * 16
    (runner.root / R.REQUESTS_REL / f"{request_id}.json").write_text("{\"kind\": \"list-")  # half-written
    runner.serve_once()
    assert (runner.root / R.REQUESTS_REL / f"{request_id}.json").exists()  # not read before its marker


def test_a_marker_without_its_request_is_discarded(runner):
    (runner.root / R.REQUESTS_REL / f"{'34' * 16}{R.READY_SUFFIX}").write_bytes(b"")
    runner.serve_once()
    assert not list((runner.root / R.REQUESTS_REL).glob(f"*{R.READY_SUFFIX}"))


def test_another_process_learns_only_completion(runner):
    request_id = R.enqueue(runner.root, {"kind": "issue-dispatch", "agent": "producer"})
    runner.serve_once()
    R._REPLY_KEYS.pop(request_id)  # as in a separate --wait-result process
    result = R.wait_result(runner.root, request_id, timeout=5)
    assert result["sealed"] and result["ok"] is None and "nonce" not in json.dumps(result)


def test_a_forged_sealed_result_is_refused(runner):
    request_id = R.enqueue(runner.root, {"kind": "list-staged"})
    forged = S.seal(S.new_reply_key(), request_id, json.dumps({"ok": True, "result": {"staged": []}}).encode())
    (runner.root / R.SEALED_RESULTS_REL / f"{request_id}.sealed").write_bytes(forged)
    with pytest.raises(R.RunnerError, match="failed verification"):
        R.poll_result(runner.root, request_id)


def test_sealed_results_are_capped(runner, monkeypatch):
    monkeypatch.setattr(R, "SEALED_RESULT_CAP", 2)
    for i in range(4):
        (runner.root / R.SEALED_RESULTS_REL / f"{i:032x}.sealed").write_bytes(b"x")
    runner.serve_once()
    assert len(list((runner.root / R.SEALED_RESULTS_REL).glob("*.sealed"))) == 2


def test_status_tree_symlink_is_refused(project, key_file, tmp_path):  # noqa: F811
    root, policy = project
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / R.STATUS_REL).symlink_to(elsewhere)
    with pytest.raises(R.RunnerError, match="symlink"):
        R.Runner(root, root / "brief.json", policy)


def test_queue_mode_fails_closed_on_a_read_denied_control_plane(tmp_path, monkeypatch):
    class _Args:
        project = str(tmp_path)
        description = None

    def _denied(self):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(Path, "exists", _denied)
    assert C._queue_mode(_Args()) is True
