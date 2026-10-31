"""HTTP transport (P06) against a loopback fake server: retries, Retry-After, error mapping, redirects, caps.

Only ``127.0.0.1`` is contacted; any other URL fails the test. If loopback binding is blocked in the session
sandbox, every test here skips with the reason.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from agentteams import model_providers as mp
from agentteams.model_providers import _transport
from tests._model_provider_fakes import user

OK = {"id": "c1", "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _Server:
    """Answers each POST with the next scripted action and counts hits per path."""

    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.hits: dict[str, int] = {}
        self.bodies: list[bytes] = []
        self.client_gone = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                return None

            def _serve(self) -> None:
                outer.hits[self.path] = outer.hits.get(self.path, 0) + 1
                n = int(self.headers.get("Content-Length") or 0)
                outer.bodies.append(self.rfile.read(n))
                act = outer.actions.pop(0) if outer.actions else {"status": 500}
                time.sleep(act.get("delay", 0))
                body = act.get("body", b"{}")
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(act.get("status", 200))
                self.send_header("Content-Type", act.get("ctype", "application/json"))
                self.send_header("Content-Length", str(len(body)))
                for k, v in act.get("headers", {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    if act.get("stall_after"):
                        self.wfile.write(body[:act["stall_after"]])
                        self.wfile.flush()
                        time.sleep(act["stall"])
                        return
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    outer.client_gone += 1   # expected when the client stops reading (size cap, timeout)

            do_POST = _serve
            do_GET = _serve

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        except OSError as exc:  # pragma: no cover - sandbox-dependent
            pytest.skip(f"loopback bind blocked in this sandbox: {exc}")
        self.httpd.daemon_threads = True
        self.httpd.block_on_close = False
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Server]:
    real_open = urllib.request.OpenerDirector.open

    def loopback_only(self: Any, req: Any, *a: Any, **k: Any) -> Any:
        url = req.full_url if isinstance(req, urllib.request.Request) else req
        assert url.startswith("http://127.0.0.1:"), f"non-loopback URL in an offline test: {url}"
        return real_open(self, req, *a, **k)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", loopback_only)
    monkeypatch.delenv("CI", raising=False)
    srv = _Server()
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()


@pytest.fixture()
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    seen: list[float] = []
    monkeypatch.setattr(_transport, "_sleep", seen.append)
    return seen


def _call(srv: _Server, timeout: float = 5.0) -> mp.ModelReply:
    p = mp.Provider.from_key("openai-compatible", "k-loopback-123456", base_url=srv.base)
    return mp.complete(p, "m", [user("hi")], max_tokens=8, timeout=timeout)


def test_success_and_request_shape(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"body": OK}]
    r = _call(server)
    assert r.content == ({"type": "text", "text": "hello"},) and r.request_id == "c1"
    assert server.hits == {"/v1/chat/completions": 1}
    assert json.loads(server.bodies[0])["messages"] == [{"role": "user", "content": "hi"}]


def test_429_retry_after_then_success(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"status": 429, "headers": {"Retry-After": "1"}, "body": {"error": {"message": "slow"}}},
                      {"body": OK}]
    assert _call(server).stop == "end_turn"
    assert sleeps == [1.0] and server.hits["/v1/chat/completions"] == 2


def test_529_three_times_is_overloaded(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"status": 529}] * 3
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server)
    assert (err.value.kind, err.value.status, err.value.attempts) == ("overloaded", 529, 3)
    assert len(sleeps) == 2 and all(1 < s <= mp.BACKOFF_CAP_S for s in sleeps)


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (404, "not_found"), (400, "bad_request"),
                                         (413, "too_large"), (422, "bad_request")])
def test_no_retry_on_client_errors(server: _Server, sleeps: list[float], status: int, kind: str) -> None:
    server.actions = [{"status": status, "body": {"error": {"type": "x", "message": "nope"}}}]
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server)
    assert (err.value.kind, err.value.attempts) == (kind, 1) and sleeps == []
    assert "nope" in err.value.message


def test_content_filter_error_is_refused(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"status": 400, "body": {"error": {"code": "content_filter", "message": "blocked"}}}]
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server)
    assert err.value.kind == "refused"


def test_redirect_is_refused_and_not_followed(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"status": 302, "headers": {"Location": "/elsewhere"}}]
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server)
    assert err.value.kind == "protocol" and "redirect" in err.value.message
    assert "/elsewhere" not in server.hits


def test_oversize_body_is_protocol(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"body": b"[" + b"0," * (mp.MAX_RESPONSE_BYTES // 2 + 512 * 1024) + b"0]"}]
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server)
    assert err.value.kind == "protocol" and "larger than" in err.value.message and err.value.attempts == 1


def test_non_json_is_protocol(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"body": b"<html>hi</html>", "ctype": "text/html"}]
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server)
    assert err.value.kind == "protocol"
    server.actions = [{"body": b"{bad json", "ctype": "application/json"}]
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server)
    assert err.value.kind == "protocol"


def test_slow_server_times_out_with_retries(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"delay": 1.0, "body": OK}] * 3
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server, timeout=0.2)
    assert (err.value.kind, err.value.attempts) == ("timeout", 3)


def test_mid_body_stall_is_not_retried(server: _Server, sleeps: list[float]) -> None:
    server.actions = [{"body": OK, "stall_after": 5, "stall": 1.0}]
    with pytest.raises(mp.ModelProviderError) as err:
        _call(server, timeout=0.3)
    assert (err.value.kind, err.value.attempts) == ("timeout", 1) and sleeps == []


def test_connection_refused_is_network(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()                                   # nothing listens on this port now
    monkeypatch.delenv("CI", raising=False)
    p = mp.Provider.from_key("openai-compatible", "k-loopback-123456", base_url=f"http://127.0.0.1:{port}/v1")
    with pytest.raises(mp.ModelProviderError) as err:
        mp.complete(p, "m", [user("hi")], max_tokens=8, timeout=2)
    assert (err.value.kind, err.value.attempts) == ("network", 3)
