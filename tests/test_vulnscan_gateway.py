"""agentteams.vulnscan.gateway handler tests without binding a port (E22/A5, D7).

The handler class from ``gateway.make_handler`` is driven over a ``socket.socketpair()``: one end
is handed to the handler as its request socket, a raw HTTP request is written into the other.
The upstream is a scripted fake ``http.client.HTTPConnection`` that records what it received.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentteams.vulnscan import gateway

MODEL = "m/1"
KEY = "sk-gateway-key"


class FakeResponse:
    """A scripted upstream response read in chunks through ``read1``."""

    def __init__(self, status: int, chunks: list[bytes], content_type: str = "application/json",
                 fail_after: int | None = None) -> None:
        self.status, self.chunks, self.content_type, self.fail_after = status, list(chunks), content_type, fail_after
        self.reads = 0

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.content_type if name.lower() == "content-type" else default

    def read1(self, n: int = -1) -> bytes:
        if self.fail_after is not None and self.reads >= self.fail_after:
            raise http.client.IncompleteRead(b"")
        self.reads += 1
        return self.chunks.pop(0) if self.chunks else b""


class Upstream:
    """Scripted upstream: each new connection takes the next scripted behaviour."""

    def __init__(self) -> None:
        self.script: list[Callable[[], FakeResponse]] = []
        self.received: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def conn_class(self) -> type:
        upstream = self

        class FakeConn:
            def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
                self.host, self.port = host, port

            def request(self, method: str, path: str, body: bytes | None = None,
                        headers: dict[str, str] | None = None) -> None:
                with upstream.lock:
                    upstream.received.append({"method": method, "path": path, "host": self.host,
                                              "body": json.loads(body or b"{}"), "headers": dict(headers or {})})

            def getresponse(self) -> FakeResponse:
                with upstream.lock:
                    make = upstream.script.pop(0)
                return make()

            def close(self) -> None:
                return

        return FakeConn


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    up = Upstream()
    monkeypatch.setattr(gateway.http.client, "HTTPConnection", up.conn_class())
    monkeypatch.setattr(gateway.http.client, "HTTPSConnection", up.conn_class())
    return up


def _json_reply(usage: dict[str, Any] | None, status: int = 200) -> Callable[[], FakeResponse]:
    body: dict[str, Any] = {"choices": [{"message": {"content": "hi"}}]}
    if usage is not None:
        body["usage"] = usage
    return lambda: FakeResponse(status, [json.dumps(body).encode()])


def _request(body: bytes | None = None, *, path: str = gateway.ROUTE, method: str = "POST",
             headers: dict[str, str] | None = None, length: str | None = "auto") -> bytes:
    lines = [f"{method} {path} HTTP/1.1", "Host: gw"]
    hdrs = {"Content-Type": "application/json", "Authorization": "Bearer agent-dummy-key", **(headers or {})}
    if body is not None and length == "auto":
        hdrs["Content-Length"] = str(len(body))
    elif length not in (None, "auto"):
        hdrs["Content-Length"] = str(length)
    lines += [f"{k}: {v}" for k, v in hdrs.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + (body or b"")


def _chat(model: str = MODEL, **extra: Any) -> bytes:
    return json.dumps({"model": model, "messages": [{"role": "user", "content": "x"}], **extra}).encode()


def _drive(handler: type, raw: bytes, *, on_client: Callable[[socket.socket], bytes] | None = None) -> tuple[int, bytes]:
    """Serve one request through ``handler`` over a socketpair; return (status, body)."""
    srv, cli = socket.socketpair()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            handler(srv, ("127.0.0.1", 0), object())
        except (OSError, ValueError, TypeError, AttributeError, KeyError, http.client.HTTPException) as exc:
            # a handler crash would otherwise vanish in this thread: recorded and asserted below
            errors.append(exc)
        finally:
            srv.close()

    t = threading.Thread(target=run)
    t.start()
    cli.sendall(raw)
    if on_client is not None:
        data = on_client(cli)
    else:
        data = bytearray()
        while chunk := cli.recv(65536):
            data += chunk
        data = bytes(data)
    cli.close()
    t.join(10)
    assert not t.is_alive()
    assert not errors, errors
    if not data:
        return 0, b""
    head, _, body = data.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), body


def _handler(meter: gateway.Meter, upstream_url: str = "http://up.invalid:8791") -> type:
    return gateway.make_handler(model=MODEL, key=KEY, upstream=upstream_url, meter=meter)


# ---------------------------------------------------------------- routes, model, body (A5)

def test_routes_healthz_and_wrong_methods(upstream: Upstream) -> None:
    h = _handler(gateway.Meter(1.0, None))
    assert _drive(h, _request(method="GET", path="/healthz"))[0] == 200
    assert _drive(h, _request(method="GET", path=gateway.ROUTE))[0] == 403
    assert _drive(h, _request(_chat(), path="/api/v1/embeddings"))[0] == 403
    assert _drive(h, _request(_chat(), path=gateway.ROUTE + "/../keys"))[0] == 403
    assert _drive(h, _request(_chat(), path=gateway.ROUTE + "?x=1"))[0] == 403
    assert upstream.received == []


def test_wrong_model_and_bad_json_are_400(upstream: Upstream) -> None:
    h = _handler(gateway.Meter(1.0, None))
    status, body = _drive(h, _request(_chat(model="openai/gpt-x")))
    assert status == 400 and MODEL in json.loads(body)["error"]["message"]
    assert _drive(h, _request(b"{not json"))[0] == 400
    assert _drive(h, _request(b"\xff\xfe"))[0] == 400
    assert _drive(h, _request(b"[1, 2]"))[0] == 400
    assert upstream.received == []


def test_body_limits(upstream: Upstream, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway, "MAX_BODY", 1024)
    h = _handler(gateway.Meter(1.0, None))
    assert _drive(h, _request(_chat(pad="x" * 2000)))[0] == 413              # over the cap
    assert _drive(h, _request(_chat(), length=None))[0] == 413                 # no Content-Length
    assert _drive(h, _request(_chat(), length="0"))[0] == 413
    assert _drive(h, _request(_chat(), length="-5"))[0] == 413
    assert _drive(h, _request(_chat(), length="abc"))[0] == 400                # not a number: an answer, not a crash
    assert upstream.received == []


def test_features_stripped_key_replaced_and_usage_forced(upstream: Upstream, tmp_path: Path) -> None:
    upstream.script.append(_json_reply({"cost": 0.01, "prompt_tokens": 3, "completion_tokens": 2}))
    meter = gateway.Meter(1.0, tmp_path / "spend.json")
    status, body = _drive(_handler(meter, "http://up.invalid:8791/base/"),
                          _request(_chat(plugins=[{"id": "web"}], web_search_options={"x": 1}, transforms=["t"])))
    assert status == 200 and json.loads(body)["usage"]["cost"] == 0.01
    (got,) = upstream.received
    assert got["path"] == "/base" + gateway.ROUTE and got["host"] == "up.invalid"
    assert got["headers"]["Authorization"] == f"Bearer {KEY}"                  # agent's dummy key never forwarded
    assert "agent-dummy-key" not in json.dumps(got)
    assert not {"plugins", "web_search_options", "transforms"} & set(got["body"])
    assert got["body"]["usage"] == {"include": True} and got["body"]["model"] == MODEL
    spend = json.loads((tmp_path / "spend.json").read_text())
    assert spend["calls"] == 1 and spend["spent_usd"] == 0.01 and spend["prompt_tokens"] == 3


# ---------------------------------------------------------------- caps and metering (E22)

def test_budget_exhausted_is_402_with_reason(upstream: Upstream) -> None:
    upstream.script.append(_json_reply({"cost": 1.5}))
    meter = gateway.Meter(1.0, None)
    h = _handler(meter)
    assert _drive(h, _request(_chat()))[0] == 200
    status, body = _drive(h, _request(_chat()))
    assert status == 402 and "budget exhausted" in json.loads(body)["error"]["message"]
    assert meter.refused == 1 and len(upstream.received) == 1


def test_token_cap_is_402(upstream: Upstream) -> None:
    upstream.script.append(_json_reply({"cost": 0.0, "prompt_tokens": 60, "completion_tokens": 40}))
    h = _handler(gateway.Meter(100.0, None, max_tokens=100))
    assert _drive(h, _request(_chat()))[0] == 200
    status, body = _drive(h, _request(_chat()))
    assert status == 402 and "token cap" in json.loads(body)["error"]["message"]


def test_unmetered_replies_cap_is_402(upstream: Upstream) -> None:
    upstream.script += [_json_reply(None), _json_reply({"cost": 0})]
    meter = gateway.Meter(100.0, None, max_unmetered=2)
    h = _handler(meter)
    assert [_drive(h, _request(_chat()))[0] for _ in range(3)] == [200, 200, 402]
    assert meter.unmetered == 2 and "usage" in meter.exhausted_reason()


def test_upstream_429_is_passed_through_and_counted_as_an_error(upstream: Upstream) -> None:
    upstream.script.append(lambda: FakeResponse(429, [b'{"error": "rate limited"}']))
    meter = gateway.Meter(1.0, None, max_unmetered=1)
    status, body = _drive(_handler(meter), _request(_chat()))
    assert status == 429 and b"rate limited" in body
    assert meter.upstream_errors == 1 and meter.unmetered == 0 and meter.calls == 0 and not meter.exhausted()


def test_sse_stream_usage_is_metered(upstream: Upstream) -> None:
    chunks = [b'data: {"choices": [{"delta": {"content": "a"}}]}\n\n',
              b'data: {"choices": [], "usage": {"cost": 0.25, "prompt_tokens": 10, "completion_tokens": 5}}\n\n',
              b"data: [DONE]\n\n"]
    upstream.script.append(lambda: FakeResponse(200, chunks, "text/event-stream"))
    meter = gateway.Meter(1.0, None)
    status, body = _drive(_handler(meter), _request(_chat(stream=True), headers={"Accept": "text/event-stream"}))
    assert status == 200 and body.endswith(b"data: [DONE]\n\n")
    assert upstream.received[0]["headers"]["Accept"] == "text/event-stream"
    assert meter.spent == 0.25 and meter.completion_tokens == 5 and meter.unmetered == 0


def test_client_disconnect_mid_stream_still_meters(upstream: Upstream) -> None:
    hung_up = threading.Event()
    chunks = [b'data: {"choices": [{"delta": {"content": "a"}}]}\n\n'] * 3 + \
             [b'data: {"usage": {"cost": 0.5}}\n\n']

    class SlowResponse(FakeResponse):
        def read1(self, n: int = -1) -> bytes:
            if self.reads == 1:
                hung_up.wait(5)
                time.sleep(0.05)
            return super().read1(n)

    upstream.script.append(lambda: SlowResponse(200, chunks, "text/event-stream"))
    meter = gateway.Meter(1.0, None)

    def hang_up(cli: socket.socket) -> bytes:
        first = cli.recv(65536)                     # headers (and maybe the first chunk), then hang up
        cli.close()
        hung_up.set()
        return first

    _drive(_handler(meter), _request(_chat(stream=True)), on_client=hang_up)
    assert meter.calls == 1 and meter.unmetered == 1 and meter.spent == 0.0   # partial stream: fails closed


def test_upstream_disconnect_and_timeout(upstream: Upstream) -> None:
    upstream.script.append(lambda: FakeResponse(200, [b'data: {"x": 1}\n\n'], "text/event-stream", fail_after=1))

    def timeout() -> FakeResponse:
        raise TimeoutError("timed out")

    upstream.script.append(timeout)
    meter = gateway.Meter(1.0, None)
    h = _handler(meter)
    assert _drive(h, _request(_chat()))[0] == 200            # headers already sent; the cut is metered
    assert meter.calls == 1 and meter.unmetered == 1
    status, body = _drive(h, _request(_chat()))
    assert status == 502 and "TimeoutError" in json.loads(body)["error"]["message"]
    assert meter.calls == 1 and meter.upstream_errors == 0     # nothing came back: nothing to meter


# ---------------------------------------------------------------- D7: concurrency

class _ContendedLock:
    """A ``threading.Lock`` stand-in that sets :attr:`contended` when an acquire has to wait (V14)."""

    contended = threading.Event()

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __enter__(self) -> bool:
        if not self._lock.acquire(blocking=False):
            _ContendedLock.contended.set()           # another call holds the gate: this one now waits
            self._lock.acquire()
        return True

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


def test_concurrent_calls_do_not_overshoot_the_budget(upstream: Upstream, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    first_in, release = threading.Event(), threading.Event()

    def slow_costly() -> FakeResponse:
        first_in.set()
        release.wait(5)
        return FakeResponse(200, [json.dumps({"usage": {"cost": 1.0}}).encode()])

    upstream.script += [slow_costly, _json_reply({"cost": 1.0})]
    meter = gateway.Meter(1.0, tmp_path / "spend.json")
    _ContendedLock.contended = threading.Event()
    with monkeypatch.context() as m:                 # only the handler's gate is instrumented
        m.setattr(gateway, "threading", type("T", (), {"Lock": _ContendedLock}))
        h = _handler(meter)
    statuses: list[int] = []
    t1 = threading.Thread(target=lambda: statuses.append(_drive(h, _request(_chat()))[0]))
    t1.start()
    assert first_in.wait(5)
    t2 = threading.Thread(target=lambda: statuses.append(_drive(h, _request(_chat()))[0]))
    t2.start()
    assert _ContendedLock.contended.wait(5), "the second call never waited on the gate"   # event, not sleep
    release.set()
    t1.join(10)
    t2.join(10)
    assert sorted(statuses) == [200, 402]
    assert len(upstream.received) == 1 and meter.spent == 1.0 and meter.refused == 1
    assert json.loads((tmp_path / "spend.json").read_text())["refused"] == 1                # V15


# ---------------------------------------------------------------- V16 / G12: metering inputs and spend.json

@pytest.mark.parametrize("usage", [{"cost": -1.0}, {"cost": float("nan")}, {"cost": True},
                                   {"prompt_tokens": -5, "completion_tokens": float("nan")},
                                   {"prompt_tokens": True, "completion_tokens": False}, {"cost": float("inf")}])
def test_bad_usage_numbers_meter_nothing_and_count_unmetered(usage: dict[str, Any]) -> None:
    meter = gateway.Meter(1.0, None, max_unmetered=5)
    meter.add({"cost": 0.25, "prompt_tokens": 3, "completion_tokens": 2})
    meter.add(usage)                                                           # V16
    assert (meter.spent, meter.prompt_tokens, meter.completion_tokens) == (0.25, 3, 2)
    assert meter.calls == 2 and meter.unmetered == 1


def test_spend_json_is_replaced_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "spend.json"
    meter = gateway.Meter(1.0, out)
    meter.add({"cost": 0.1})
    good = out.read_text()
    replaced: list[tuple[str, str]] = []
    real_replace = gateway.os.replace

    def replace(src: str, dst: str) -> None:
        replaced.append((str(src), str(dst)))
        raise OSError("disk full at rename")

    monkeypatch.setattr(gateway.os, "replace", replace)
    with pytest.raises(OSError):
        meter.add({"cost": 0.2})                                                # G12
    assert replaced and replaced[0][1] == str(out) and replaced[0][0] != str(out)
    assert out.read_text() == good and json.loads(good)["calls"] == 1           # never a truncated file
    assert sorted(p.name for p in tmp_path.iterdir()) == ["spend.json"]         # the temp is removed
    monkeypatch.setattr(gateway.os, "replace", real_replace)
    meter.add({"cost": 0.3})
    assert json.loads(out.read_text())["calls"] == 3


# ---------------------------------------------------------------- main() argument validation

class _NoServer:
    """Stands in for ThreadingHTTPServer so nothing binds."""

    instances: ClassVar[list[_NoServer]] = []

    def __init__(self, addr: tuple[str, int], handler: type) -> None:
        self.addr, self.handler = addr, handler
        _NoServer.instances.append(self)

    def serve_forever(self) -> None:
        return


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> type[_NoServer]:
    _NoServer.instances = []
    monkeypatch.setattr(gateway, "ThreadingHTTPServer", _NoServer)
    return _NoServer


def test_main_argument_validation(tmp_path: Path, no_server: type[_NoServer],
                                  capsys: pytest.CaptureFixture[str]) -> None:
    good = tmp_path / "k.env"
    good.write_text("export OPENROUTER_API_KEY='sk-x'\n")
    base = ["--model", "m", "--key-file", str(good), "--listen", "127.0.0.1:9"]
    for bad in (["--max-unmetered-calls", "0"], ["--max-unmetered-calls", "x"], ["--budget-usd", "nan"],
                ["--budget-usd", "-1"], ["--budget-usd", "inf"], ["--listen", "nocolon"], ["--listen", "h:port"],
                ["--max-tokens-total", "-5"], ["--max-tokens-total", "-1"]):            # X11: never "no cap"
        with pytest.raises(SystemExit) as exc:
            gateway.main(base + bad)
        assert exc.value.code == 2, bad
    empty = tmp_path / "empty.env"
    empty.write_text("# nothing\nOTHER=1\n")
    assert gateway.main(["--model", "m", "--key-file", str(empty)]) == 2
    assert gateway.main(["--model", "m", "--key-file", str(tmp_path / "missing.env")]) == 2
    assert no_server.instances == []
    assert gateway.main(base) == 0
    (srv,) = no_server.instances
    assert srv.addr == ("127.0.0.1", 9)
    assert "sk-x" not in capsys.readouterr().out
