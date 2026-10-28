"""agentteams.vulnscan.gateway: a call still in flight when the gateway stops is settled, never lost (rc.12).

rc.9 live run (2026-10-27, agent-error): the model's reply was cut off at the output-token limit, goose exited, and
the launcher removed the gateway (``docker rm -f``) while that call was still being relayed. ``spend.json`` kept the
call reserved (``reserved_tokens`` > 0) and counted only the call before it. Now a stopping gateway settles the call
(:meth:`gateway.Meter.shutdown`) and the launcher settles a file a killed gateway left
(:func:`gateway_settle.settle_spend_file`). Handler driving reuses ``test_vulnscan_gateway_dialects``' socketpair.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import agent_run, gateway, gateway_settle
from tests.test_vulnscan_gateway_dialects import (  # noqa: F401  (upstream: a fixture)
    MODEL,
    MSG_STREAM,
    FakeResponse,
    Upstream,
    _drive,
    _handler,
    _messages,
    _request,
    upstream,
)

PRICES = {MODEL: {"input_per_mtok": 1.0, "output_per_mtok": 10.0, "cache_read_per_mtok": 1.0,
                  "cache_write_per_mtok": 2.0}}


def _spend(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stalled_stream(release: threading.Event) -> FakeResponse:
    """A Messages stream that stops after its first event until ``release`` is set (the reply still streaming)."""
    return FakeResponse(200, list(MSG_STREAM), "text/event-stream", gates={1: release})


def _start_call(meter: gateway.Meter, upstream: Upstream, release: threading.Event,  # noqa: F811
                **kw: Any) -> threading.Thread:
    upstream.script.append(lambda: _stalled_stream(release))
    h = _handler(meter, **kw)
    t = threading.Thread(target=_drive, args=(h, _request("/v1/messages", _messages(stream=True))), daemon=True)
    t.start()
    for _ in range(200):
        if meter.inflight:
            return t
        threading.Event().wait(0.01)
    raise AssertionError("the call was never reserved")


def test_the_gap_a_call_in_flight_is_only_reserved_in_spend_json(upstream: Upstream, tmp_path: Path) -> None:  # noqa: F811
    """What a gateway killed mid-call leaves behind: the call reserved, not counted (the rc.9 evidence)."""
    out = tmp_path / "spend.json"
    release = threading.Event()
    meter = gateway.Meter(1.0, out, max_tokens=10**6)
    t = _start_call(meter, upstream, release)
    spend = _spend(out)
    assert spend["calls"] == 0 and spend["reserved_tokens"] > 0   # killed now: the call is lost
    release.set()
    t.join(10)
    spend = _spend(out)
    assert spend["calls"] == 1 and spend["reserved_tokens"] == 0 and spend["reserved_usd"] == 0


def test_shutdown_charges_a_call_still_in_flight_its_reservation(upstream: Upstream, tmp_path: Path) -> None:  # noqa: F811
    out = tmp_path / "spend.json"
    release = threading.Event()
    meter = gateway.Meter(1.0, out, max_tokens=10**6)
    t = _start_call(meter, upstream, release, prices=PRICES)
    reserved = _spend(out)
    assert reserved["reserved_usd"] > 0 and reserved["reserved_tokens"] > 0
    assert meter.shutdown(grace=0.1) == 1
    spend = _spend(out)
    assert spend["calls"] == 1 and spend["settled_inflight"] == 1
    assert spend["reserved_usd"] == 0 and spend["reserved_tokens"] == 0
    assert spend["spent_usd"] == pytest.approx(reserved["reserved_usd"])          # the worst case is charged
    assert spend["completion_tokens"] == reserved["reserved_tokens"]
    assert spend["unmetered"] == 0                                                 # it had a price
    release.set()                                                                  # the relay ends after the settle:
    t.join(10)
    assert _spend(out) == {**spend, "updated": _spend(out)["updated"]}             # nothing counted twice


def test_an_unpriced_reservation_settles_as_unmetered(upstream: Upstream, tmp_path: Path) -> None:  # noqa: F811
    out = tmp_path / "spend.json"
    release = threading.Event()
    meter = gateway.Meter(1.0, out, max_tokens=10**6)
    t = _start_call(meter, upstream, release)                                     # no price table: $0 reserved
    assert meter.shutdown(grace=0.0) == 1
    spend = _spend(out)
    assert spend["calls"] == 1 and spend["unmetered"] == 1 and spend["reserved_tokens"] == 0
    assert spend["completion_tokens"] > 0
    release.set()
    t.join(10)


def test_a_call_that_ends_within_the_grace_is_metered_not_charged(upstream: Upstream, tmp_path: Path) -> None:  # noqa: F811
    out = tmp_path / "spend.json"
    release = threading.Event()
    meter = gateway.Meter(1.0, out, max_tokens=10**6, allow_unmetered=True)
    t = _start_call(meter, upstream, release, prices=PRICES)
    threading.Timer(0.1, release.set).start()
    assert meter.shutdown(grace=5.0) == 0
    t.join(10)
    spend = _spend(out)
    assert spend["calls"] == 1 and spend["settled_inflight"] == 0
    assert spend["prompt_tokens"] == 160 and spend["completion_tokens"] == 40    # the billed usage
    assert spend["reserved_usd"] == 0 and spend["reserved_tokens"] == 0


def test_a_stopping_gateway_refuses_new_calls_without_counting_them(upstream: Upstream) -> None:  # noqa: F811
    meter = gateway.Meter(1.0, None)
    meter.shutdown(grace=0.0)
    status, body = _drive(_handler(meter), _request("/v1/messages", _messages()))
    assert status == 503 and gateway.SHUTTING_DOWN in body.decode()
    assert meter.refused == 0 and meter.reasons == [] and upstream.received == []
    assert meter.reserve(0.0, 10) == gateway.SHUTTING_DOWN


def test_record_releases_its_reservation_in_the_same_step() -> None:
    meter = gateway.Meter(1.0, None, max_tokens=1000)
    assert meter.reserve(0.5, 100) == ""
    meter.record(0.1, 10, 5, unmetered=False, reserved=(0.5, 100))
    assert (meter.inflight, meter.reserved_usd, meter.reserved_tokens) == (0, 0.0, 0)
    assert meter.settle_inflight() == 0 and meter.calls == 1


def test_serve_until_stopped_settles_on_sigterm() -> None:
    class Server:
        def __init__(self) -> None:
            self.done = threading.Event()

        def serve_forever(self) -> None:
            self.done.wait(10)

        def shutdown(self) -> None:
            self.done.set()

    meter = gateway.Meter(1.0, None, max_tokens=1000)
    assert meter.reserve(0.0, 100) == ""                                           # a call in flight
    before = signal.getsignal(signal.SIGTERM)
    threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM)).start()
    assert gateway.serve_until_stopped(Server(), meter, 0.0) == 1
    assert signal.getsignal(signal.SIGTERM) is before                              # restored
    assert meter.calls == 1 and meter.reserved_tokens == 0 and meter.closed


def test_shutdown_grace_must_be_a_finite_non_negative_number(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        gateway.main(["--model", MODEL, "--key-file", str(tmp_path / "k"), "--shutdown-grace", "-1"])
    assert exc.value.code == 2


# ---------------------------------------------------------------- the launcher's side

def _left(tmp_path: Path, **over: Any) -> Path:
    path = tmp_path / "spend.json"
    data = {"budget_usd": 2.0, "spent_usd": 0.001025, "calls": 1, "unmetered": 0, "prompt_tokens": 9000,
            "completion_tokens": 32, "reserved_usd": 0.0, "reserved_tokens": 106582, "drained": 0, **over}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_settle_spend_file_charges_a_reservation_a_killed_gateway_left(tmp_path: Path) -> None:
    path = _left(tmp_path)                                                         # the rc.9 run's shape
    assert gateway_settle.settle_spend_file(path) == 1
    spend = _spend(path)
    assert spend["calls"] == 2 and spend["reserved_tokens"] == 0 and spend["reserved_usd"] == 0
    assert spend["completion_tokens"] == 32 + 106582 and spend["unmetered"] == 1   # no price: spend unknown
    assert spend["settled_inflight"] == 1 and spend["settled_by"] == "launcher"
    assert spend["prompt_tokens"] == 9000                                          # other fields kept
    assert gateway_settle.settle_spend_file(path) == 0                             # idempotent


def test_settle_spend_file_charges_a_priced_reservation(tmp_path: Path) -> None:
    path = _left(tmp_path, reserved_usd=0.25)
    assert gateway_settle.settle_spend_file(path) == 1
    spend = _spend(path)
    assert spend["spent_usd"] == pytest.approx(0.251025) and spend["unmetered"] == 0


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"calls": 3, "reserved_tokens": 0}'])
def test_settle_spend_file_leaves_other_files_alone(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "spend.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert gateway_settle.settle_spend_file(path) == 0
    assert (path.read_text(encoding="utf-8") if content is not None else None) == content


def test_the_launcher_stops_the_gateway_with_sigterm_then_settles(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    path = _left(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_run, "_docker", lambda argv, **kw: calls.append(argv))
    agent_run._stop_gateway(tmp_path)
    assert calls == [["docker", "stop", "-t", str(agent_run.GATEWAY_STOP_SECONDS), agent_run.GATEWAY],
                     ["docker", "rm", "-f", agent_run.GATEWAY]]
    assert agent_run.GATEWAY_STOP_SECONDS > gateway.DEFAULT_SHUTDOWN_GRACE        # docker stop waits out the grace
    assert _spend(path)["reserved_tokens"] == 0 and _spend(path)["calls"] == 2


def test_a_docker_stop_that_times_out_still_removes_and_settles(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    path = _left(tmp_path)
    calls: list[list[str]] = []

    def docker(argv: list[str], **kw: Any) -> None:
        calls.append(argv)
        if argv[1] == "stop":
            raise subprocess.TimeoutExpired(argv, 80)

    monkeypatch.setattr(agent_run, "_docker", docker)
    with pytest.raises(subprocess.TimeoutExpired):
        agent_run._stop_gateway(tmp_path)
    assert calls[-1] == ["docker", "rm", "-f", agent_run.GATEWAY] and _spend(path)["reserved_tokens"] == 0


# ---------------------------------------------------------------- the gateway-writable directory (@security)

def test_a_symlinked_spend_json_is_not_read(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"calls": 1, "reserved_tokens": 5}), encoding="utf-8")
    (tmp_path / "gw").mkdir()
    (tmp_path / "gw" / "spend.json").symlink_to(target)
    assert gateway_settle.settle_spend_file(tmp_path / "gw" / "spend.json") == 0
    assert json.loads(target.read_text(encoding="utf-8")) == {"calls": 1, "reserved_tokens": 5}


def test_a_planted_temp_symlink_is_never_written_through(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    gw = tmp_path / "gw"
    gw.mkdir()
    path = _left(gw)
    (gw / ".spend.json.tmp").symlink_to(victim)                                   # the old fixed temp name
    assert gateway_settle.settle_spend_file(path) == 1
    assert victim.read_text(encoding="utf-8") == "keep"
    assert _spend(path)["reserved_tokens"] == 0 and not path.is_symlink()
    assert oct(path.stat().st_mode & 0o777) == oct(0o644)


def test_an_oversized_spend_json_is_left_alone(tmp_path: Path) -> None:
    path = _left(tmp_path, pad="x" * gateway_settle.MAX_SPEND_BYTES)
    assert gateway_settle.settle_spend_file(path) == 0


def test_a_call_that_reserved_nothing_is_still_recorded_after_the_settle() -> None:
    meter = gateway.Meter(1.0, None)
    meter.settle_inflight()
    meter.record(0.01, 3, 2, unmetered=False)                                     # never charged: counted
    meter.record(0.01, 3, 2, unmetered=False, reserved=(0.0, 10))                  # charged at the settle: not
    assert meter.calls == 1 and meter.prompt_tokens == 3
