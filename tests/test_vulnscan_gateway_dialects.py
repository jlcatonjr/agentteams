"""agentteams.vulnscan.gateway: Messages and Responses pass-through, deny by default, per-dialect metering.

Phase 2 of the vulnscan agent-runtimes plan (design item 2, revisions 8 and 9, D2/D3). As in
``test_vulnscan_gateway.py`` the handler is driven over a ``socket.socketpair()`` and the upstream
is a scripted fake ``http.client`` connection: no port is bound and nothing touches the network.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentteams.vulnscan import gateway
from agentteams.vulnscan import gateway_dialects as d

MODEL, HELPER = "anthropic/model-a", "anthropic/model-b"
CHAT_KEY, MSG_KEY, RESP_KEY = "sk-chat-key", "sk-messages-key", "sk-responses-key"
DUMMY = "dummy-in-container"


class FakeResponse:
    """A scripted upstream response read in chunks; ``gates[i]`` (an Event) is awaited before chunk ``i``."""

    def __init__(self, status: int, chunks: list[bytes], content_type: str = "application/json",
                 gates: dict[int, threading.Event] | None = None) -> None:
        self.status, self.chunks, self.content_type = status, list(chunks), content_type
        self.gates, self.reads = gates or {}, 0

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self.content_type if name.lower() == "content-type" else default

    def read1(self, n: int = -1) -> bytes:
        gate = self.gates.get(self.reads)
        if gate is not None:
            assert gate.wait(5), "test gate never opened"
        self.reads += 1
        return self.chunks.pop(0) if self.chunks else b""


class Upstream:
    """Scripted upstream recording each request (path, host, headers, body)."""

    def __init__(self) -> None:
        self.script: list[Callable[[], FakeResponse]] = []
        self.received: list[dict[str, Any]] = []

    def conn_class(self) -> type:
        upstream = self

        class FakeConn:
            def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
                self.host, self.port = host, port

            def request(self, method: str, path: str, body: bytes | None = None,
                        headers: dict[str, str] | None = None) -> None:
                upstream.received.append({"path": path, "host": self.host, "port": self.port,
                                          "headers": dict(headers or {}), "body": json.loads(body or b"{}")})

            def getresponse(self) -> FakeResponse:
                return upstream.script.pop(0)()

            def close(self) -> None:
                return

        return FakeConn


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    up = Upstream()
    monkeypatch.setattr(gateway.http.client, "HTTPConnection", up.conn_class())
    monkeypatch.setattr(gateway.http.client, "HTTPSConnection", up.conn_class())
    return up


UPSTREAMS = {d.CHAT: d.Upstream("http://chat.invalid:8791", CHAT_KEY),
             d.MESSAGES: d.Upstream("https://msg.invalid/v1", MSG_KEY, "x-api-key"),
             d.RESPONSES: d.Upstream("https://resp.invalid/api/v1", RESP_KEY)}


def _handler(meter: gateway.Meter, **kw: Any) -> type:
    kw.setdefault("upstreams", UPSTREAMS)
    return gateway.make_handler(model=MODEL, meter=meter, **kw)


def _request(path: str, body: bytes, headers: dict[str, str] | None = None) -> bytes:
    hdrs = {"Host": "gw", "Content-Type": "application/json", "x-api-key": DUMMY,
            "Authorization": f"Bearer {DUMMY}", "Content-Length": str(len(body)), **(headers or {})}
    return (f"POST {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n").encode() + body


def _messages(model: str = MODEL, **extra: Any) -> bytes:
    body = {"model": model, "max_tokens": 1000, "messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "vulnscan_status", "input_schema": {"type": "object"}}], **extra}
    return json.dumps({k: v for k, v in body.items() if v is not None}).encode()


def _responses(model: str = MODEL, **extra: Any) -> bytes:
    body = {"model": model, "max_output_tokens": 1000, "input": "x",
            "tools": [{"type": "function", "name": "vulnscan_status", "parameters": {}}], **extra}
    return json.dumps({k: v for k, v in body.items() if v is not None}).encode()


def _chat(model: str = MODEL) -> bytes:
    return json.dumps({"model": model, "messages": [{"role": "user", "content": "x"}]}).encode()


def _drive(handler: type, raw: bytes, *, on_client: Callable[[socket.socket], bytes] | None = None) -> tuple[int, bytes]:
    """Serve one request through ``handler`` over a socketpair; return (status, body)."""
    srv, cli = socket.socketpair()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            handler(srv, ("127.0.0.1", 0), object())
        except (OSError, ValueError, TypeError, AttributeError, KeyError, http.client.HTTPException) as exc:
            errors.append(exc)                       # asserted below, never lost in the thread
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
    assert not t.is_alive() and not errors, errors
    head, _, body = data.partition(b"\r\n\r\n")
    return (int(head.split(b" ", 2)[1]) if head else 0), body


def _json(body: dict[str, Any], content_type: str = "application/json") -> Callable[[], FakeResponse]:
    return lambda: FakeResponse(200, [json.dumps(body).encode()], content_type)


def _sse(*events: dict[str, Any]) -> list[bytes]:
    return [f"event: {e.get('type', 'x')}\ndata: {json.dumps(e)}\n\n".encode() for e in events]


MSG_STREAM = _sse({"type": "message_start", "message": {"usage": {"input_tokens": 100, "output_tokens": 1,
                                                                   "cache_read_input_tokens": 50,
                                                                   "cache_creation_input_tokens": 10}}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
                  {"type": "message_delta", "usage": {"output_tokens": 40}},
                  {"type": "message_stop"})
RESP_STREAM = _sse({"type": "response.created", "response": {"usage": None}},
                   {"type": "response.output_text.delta", "delta": "hi"},
                   {"type": "response.completed", "response": {"usage": {
                       "input_tokens": 200, "output_tokens": 30, "input_tokens_details": {"cached_tokens": 80}}}})


# ---------------------------------------------------------------- pass-through and auth

@pytest.mark.parametrize("path", ["/v1/messages", "/api/v1/messages"])
def test_messages_pass_through_with_anthropic_auth(upstream: Upstream, path: str) -> None:
    upstream.script.append(_json({"type": "message", "usage": {"input_tokens": 5, "output_tokens": 2, "cost": 0.01}}))
    meter = gateway.Meter(1.0, None)
    sent = _messages(metadata={"user_id": "u"})
    status, _ = _drive(_handler(meter), _request(path, sent, {"x-stainless-os": "Linux"}))
    assert status == 200
    (got,) = upstream.received
    assert (got["host"], got["path"]) == ("msg.invalid", "/v1/messages")
    assert got["headers"]["x-api-key"] == MSG_KEY and got["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in got["headers"] and "x-stainless-os" not in got["headers"]
    assert DUMMY not in json.dumps(got)
    assert got["body"] == json.loads(sent)                       # untranslated, no OpenRouter rewrite
    assert meter.spent == 0.01 and meter.prompt_tokens == 5 and meter.unmetered == 0


@pytest.mark.parametrize("path", ["/v1/responses", "/api/v1/responses"])
def test_responses_pass_through_with_bearer_and_store_forced_off(upstream: Upstream, path: str) -> None:
    upstream.script.append(_json({"object": "response", "usage": {"input_tokens": 7, "output_tokens": 3,
                                                                  "cost": 0.02}}))
    meter = gateway.Meter(1.0, None)
    assert _drive(_handler(meter), _request(path, _responses()))[0] == 200
    (got,) = upstream.received
    assert (got["host"], got["path"]) == ("resp.invalid", "/api/v1/responses")
    assert got["headers"]["Authorization"] == f"Bearer {RESP_KEY}" and "x-api-key" not in got["headers"]
    assert got["body"]["store"] is False and "usage" not in got["body"]
    assert meter.spent == 0.02 and meter.completion_tokens == 3


def test_chat_route_keeps_its_rewrites_and_key(upstream: Upstream) -> None:
    upstream.script.append(_json({"usage": {"cost": 0.01, "prompt_tokens": 1, "completion_tokens": 1}}))
    assert _drive(_handler(gateway.Meter(1.0, None)), _request(d.ROUTE, _chat()))[0] == 200
    (got,) = upstream.received
    assert got["path"] == d.ROUTE and got["headers"]["Authorization"] == f"Bearer {CHAT_KEY}"
    assert got["body"]["usage"] == {"include": True}


def test_bearer_auth_for_an_openrouter_messages_upstream() -> None:
    up = d.Upstream("https://openrouter.ai/api/v1", "k", "bearer")
    headers = d.upstream_headers(d.MESSAGES, {"anthropic-version": "2023-06-01"}, up)
    assert headers["Authorization"] == "Bearer k" and "x-api-key" not in headers


def test_routes_not_enabled_are_403_and_count_tokens_is_refused(upstream: Upstream) -> None:
    chat_only = gateway.make_handler(model=MODEL, key=CHAT_KEY, upstream="http://up.invalid",
                                     meter=gateway.Meter(1.0, None))
    assert _drive(chat_only, _request("/v1/messages", _messages()))[0] == 403
    assert _drive(chat_only, _request("/v1/responses", _responses()))[0] == 403
    status, body = _drive(_handler(gateway.Meter(1.0, None)), _request("/v1/messages/count_tokens", _messages()))
    assert status == 404 and "count_tokens" in json.loads(body)["error"]["message"]
    assert json.loads(body)["type"] == "error"                   # Anthropic error shape on the messages route
    assert upstream.received == []


# ---------------------------------------------------------------- deny by default (revision 8)

MESSAGES_REFUSALS = [
    ({"tools": [{"type": "web_search_20250305", "name": "web_search"}]}, "server-side tool"),
    ({"tools": [{"type": "web_fetch_20250910", "name": "web_fetch"}]}, "server-side tool"),
    ({"tools": [{"type": "code_execution_20250825", "name": "code_execution"}]}, "server-side tool"),
    ({"tools": [{"type": "bash_20250124", "name": "bash"}]}, "not allowed"),
    ({"mcp_servers": [{"type": "url", "url": "https://mcp.invalid"}]}, "remote MCP"),
    ({"container": "c1"}, "containers"),
    ({"service_tier": "auto"}, "deny by default"),
    ({"max_tokens": None}, "max_tokens is required"),
    ({"max_tokens": 10**6}, "between 1 and"),
    ({"max_tokens": True}, "max_tokens is required"),
    ({"tool_choice": "auto"}, "tool_choice"),
    ({"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url",
                                                                            "url": "https://x.invalid/a.png"}}]}]},
     "fetch it itself"),
    ({"system": [{"type": "text", "text": "s"}, {"type": "document", "source": {"type": "file", "file_id": "f"}}]},
     "fetch it itself"),
]

RESPONSES_REFUSALS = [
    ({"tools": [{"type": "mcp", "server_url": "https://mcp.invalid"}]}, "server-side tool"),
    ({"tools": [{"type": "web_search_preview"}]}, "server-side tool"),
    ({"tools": [{"type": "file_search", "vector_store_ids": ["v"]}]}, "server-side tool"),
    ({"tools": [{"type": "code_interpreter"}]}, "server-side tool"),
    ({"tools": [{"type": "computer_use_preview"}]}, "server-side tool"),
    ({"tools": [{"type": "image_generation"}]}, "server-side tool"),
    ({"tools": [{"type": "local_shell"}]}, "not allowed"),
    ({"background": True}, "background"),
    ({"store": True}, "store"),
    ({"previous_response_id": "resp_1"}, "stored response"),
    ({"include": ["file_search_call.results"]}, "include"),
    ({"max_output_tokens": None}, "max_output_tokens is required"),
    ({"service_tier": "flex"}, "deny by default"),
    ({"input": [{"role": "user", "content": [{"type": "input_file", "file_url": "https://x.invalid/f"}]}]},
     "fetch it itself"),
    ({"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://x.invalid/i.png"}]}]},
     "fetch it itself"),
]


@pytest.mark.parametrize(("extra", "why"), MESSAGES_REFUSALS)
def test_messages_deny_by_default(upstream: Upstream, extra: dict[str, Any], why: str) -> None:
    meter = gateway.Meter(1.0, None)
    status, body = _drive(_handler(meter), _request("/v1/messages", _messages(**extra)))
    assert status == 400 and why in json.loads(body)["error"]["message"]
    assert meter.refused == 1 and upstream.received == []


@pytest.mark.parametrize(("extra", "why"), RESPONSES_REFUSALS)
def test_responses_deny_by_default(upstream: Upstream, extra: dict[str, Any], why: str) -> None:
    meter = gateway.Meter(1.0, None)
    status, body = _drive(_handler(meter), _request("/v1/responses", _responses(**extra)))
    assert status == 400 and why in json.loads(body)["error"]["message"]
    assert meter.refused == 1 and upstream.received == []


def test_allowed_shapes_pass(upstream: Upstream) -> None:
    upstream.script += [_json({"usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.001}})] * 2
    h = _handler(gateway.Meter(1.0, None))
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
    ok_msg = _messages(tool_choice={"type": "auto"}, thinking={"type": "enabled", "budget_tokens": 500},
                       messages=[{"role": "user", "content": [image]}])
    ok_resp = _responses(store=False, background=False, include=["reasoning.encrypted_content"],
                         tools=[{"type": "custom", "name": "apply_patch"}], tool_choice={"type": "function", "name": "f"},
                         input=[{"role": "user", "content": [{"type": "input_image",
                                                              "image_url": "data:image/png;base64,AA=="}]}])
    assert _drive(h, _request("/v1/messages", ok_msg))[0] == 200
    assert _drive(h, _request("/v1/responses", ok_resp))[0] == 200


def test_anthropic_headers_are_allowlisted(upstream: Upstream) -> None:
    upstream.script.append(_json({"usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.001}}))
    meter = gateway.Meter(1.0, None)
    beta = {"anthropic-beta": "claude-code-20250219"}
    status, body = _drive(_handler(meter), _request("/v1/messages", _messages(), beta))
    assert status == 400 and "anthropic-beta" in json.loads(body)["error"]["message"]          # none by default
    status, _ = _drive(_handler(meter), _request("/v1/messages", _messages(), {"anthropic-version": "2099-01-01"}))
    assert status == 400 and meter.refused == 2 and upstream.received == []
    allowing = _handler(meter, anthropic_betas=frozenset({"claude-code-20250219"}))
    assert _drive(allowing, _request("/v1/messages", _messages(), beta))[0] == 200
    assert upstream.received[0]["headers"]["anthropic-beta"] == "claude-code-20250219"


def test_model_list_exact_match_on_every_route(upstream: Upstream) -> None:
    upstream.script += [_json({"usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.001}})] * 3
    meter = gateway.Meter(1.0, None)
    h = _handler(meter, models=(HELPER,))
    assert _drive(h, _request("/v1/messages", _messages(HELPER)))[0] == 200
    assert _drive(h, _request("/v1/responses", _responses(MODEL)))[0] == 200
    assert _drive(h, _request(d.ROUTE, _chat(HELPER)))[0] == 200
    for path, body in (("/v1/messages", _messages(MODEL + "-x")), ("/v1/responses", _responses("other/m")),
                       (d.ROUTE, _chat("ANTHROPIC/MODEL-A"))):
        status, reply = _drive(h, _request(path, body))
        assert status == 400 and HELPER in json.loads(reply)["error"]["message"]
    assert len(upstream.received) == 3


# ---------------------------------------------------------------- streaming, usage, prices (revision 9, D3)

def test_sse_is_relayed_unbuffered(upstream: Upstream) -> None:
    first_seen = threading.Event()
    upstream.script.append(lambda: FakeResponse(200, MSG_STREAM, "text/event-stream", gates={1: first_seen}))
    meter = gateway.Meter(1.0, None, allow_unmetered=True)

    def read_first_then_rest(cli: socket.socket) -> bytes:
        data = bytearray()
        while b"message_start" not in data:          # arrives while upstream still holds the rest back
            data += cli.recv(65536)
        first_seen.set()
        while chunk := cli.recv(65536):
            data += chunk
        return bytes(data)

    status, body = _drive(_handler(meter), _request("/v1/messages", _messages(stream=True)),
                          on_client=read_first_then_rest)
    assert status == 200 and body.endswith(MSG_STREAM[-1])


def test_messages_usage_stream_and_json() -> None:
    usage, found = d.usage_for(d.MESSAGES, b"".join(MSG_STREAM))
    assert found and (usage.input_tokens, usage.cache_read, usage.cache_write, usage.output_tokens) == (100, 50, 10, 40)
    assert usage.prompt_total == 160 and usage.cost is None
    body = json.dumps({"type": "message", "usage": {"input_tokens": 9, "output_tokens": 4, "cost": 0.5}}).encode()
    usage, found = d.usage_for(d.MESSAGES, body)
    assert found and (usage.input_tokens, usage.output_tokens, usage.cost) == (9, 4, 0.5)
    assert d.usage_for(d.MESSAGES, b'event: ping\ndata: {"type": "ping"}\n\n') == (d.Usage(), False)


def test_responses_usage_stream_and_json() -> None:
    usage, found = d.usage_for(d.RESPONSES, b"".join(RESP_STREAM))
    assert found and (usage.input_tokens, usage.cache_read, usage.output_tokens) == (120, 80, 30)
    body = json.dumps({"object": "response", "usage": {"input_tokens": 9, "output_tokens": 4}}).encode()
    usage, found = d.usage_for(d.RESPONSES, body)
    assert found and (usage.input_tokens, usage.output_tokens) == (9, 4)
    assert d.usage_for(d.RESPONSES, b"".join(RESP_STREAM[:2]))[1] is False     # no response.completed


def test_price_table_meters_when_upstream_reports_no_cost(upstream: Upstream, tmp_path: Path) -> None:
    table = tmp_path / "prices.json"
    table.write_text(json.dumps({MODEL: {"input_per_mtok": 3, "output_per_mtok": 15,
                                         "cache_read_per_mtok": 0.3, "cache_write_per_mtok": 3.75}}))
    prices = d.load_prices(table)
    upstream.script += [lambda: FakeResponse(200, MSG_STREAM, "text/event-stream"),
                        lambda: FakeResponse(200, RESP_STREAM, "text/event-stream")]
    meter = gateway.Meter(1.0, tmp_path / "spend.json")
    h = _handler(meter, prices=prices)
    assert _drive(h, _request("/v1/messages", _messages(stream=True)))[0] == 200
    assert meter.spent == pytest.approx((100 * 3 + 40 * 15 + 50 * 0.3 + 10 * 3.75) / 1e6)
    assert _drive(h, _request("/v1/responses", _responses(stream=True)))[0] == 200
    assert meter.unmetered == 0 and meter.prompt_tokens == 160 + 200 and meter.completion_tokens == 70
    spend = json.loads((tmp_path / "spend.json").read_text())
    assert spend["reserved_usd"] == 0 and spend["reserved_tokens"] == 0          # reservations released


def test_no_cost_and_no_price_counts_as_unmetered(upstream: Upstream) -> None:
    upstream.script += [lambda: FakeResponse(200, MSG_STREAM, "text/event-stream")] * 2
    meter = gateway.Meter(1.0, None, max_unmetered=2)
    h = _handler(meter)
    assert [_drive(h, _request("/v1/messages", _messages()))[0] for _ in range(3)] == [200, 200, 402]
    assert meter.unmetered == 2 and meter.prompt_tokens == 320               # tokens still count toward the cap


def test_upstream_cost_wins_over_the_price_table(upstream: Upstream) -> None:
    upstream.script.append(_json({"object": "response", "usage": {"input_tokens": 10**6, "output_tokens": 0,
                                                                  "cost": 0.25}}))
    meter = gateway.Meter(5.0, None)
    prices = {MODEL: {"input_per_mtok": 1.0, "output_per_mtok": 1.0, "cache_read_per_mtok": 1.0,
                      "cache_write_per_mtok": 1.0}}
    assert _drive(_handler(meter, prices=prices), _request("/v1/responses", _responses()))[0] == 200
    assert meter.spent == 0.25


@pytest.mark.parametrize("bad", [[], {"m": {"input_per_mtok": 1}}, {"m": {"input_per_mtok": -1, "output_per_mtok": 1}},
                                 {"m": {"input_per_mtok": 1, "output_per_mtok": 1, "extra": 1}},
                                 {"m": {"input_per_mtok": True, "output_per_mtok": 1}}])
def test_bad_price_tables_are_refused(tmp_path: Path, bad: object) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        d.load_prices(path)


def test_reservation_refuses_a_call_that_could_cross_the_budget(upstream: Upstream) -> None:
    prices = {MODEL: {"input_per_mtok": 3.0, "output_per_mtok": 15.0, "cache_read_per_mtok": 3.0,
                      "cache_write_per_mtok": 6.0}}
    meter = gateway.Meter(0.01, None)                     # 1000 output tokens at $15/Mtok = $0.015 > $0.01
    status, body = _drive(_handler(meter, prices=prices), _request("/v1/messages", _messages()))
    assert status == 402 and "reservation refused" in json.loads(body)["error"]["message"]
    assert meter.refused == 1 and meter.reserved_usd == 0 and upstream.received == []
    upstream.script.append(_json({"usage": {"input_tokens": 1, "output_tokens": 1}}))
    assert _drive(_handler(meter, prices=prices), _request("/v1/messages", _messages(max_tokens=100)))[0] == 200


def test_reservation_holds_the_token_cap(upstream: Upstream) -> None:
    meter = gateway.Meter(1.0, None, max_tokens=1500)
    status, body = _drive(_handler(meter), _request("/v1/responses", _responses(max_output_tokens=1490)))
    assert status == 402 and "tokens" in json.loads(body)["error"]["message"] and upstream.received == []


# ---------------------------------------------------------------- drain after client hang-up (revision 9)

def _hang_up_after_headers(hung_up: threading.Event) -> Callable[[socket.socket], bytes]:
    def hang_up(cli: socket.socket) -> bytes:
        first = cli.recv(65536)
        cli.close()
        hung_up.set()
        return first
    return hang_up


def _slow_after_first(chunks: list[bytes], hung_up: threading.Event) -> Callable[[], FakeResponse]:
    class AfterHangUp(FakeResponse):
        def read1(self, n: int = -1) -> bytes:
            if self.reads == 1:
                hung_up.wait(5)
            if self.reads == 2:              # the gateway's first write after the hang-up fails; later reads drain
                threading.Event().wait(0.05)
            return super().read1(n)
    return lambda: AfterHangUp(200, chunks, "text/event-stream")


def test_stream_is_drained_after_client_disconnect(upstream: Upstream) -> None:
    hung_up = threading.Event()
    chunks = MSG_STREAM[:1] + [b'data: {"type": "ping"}\n\n'] * 5 + MSG_STREAM[1:]
    upstream.script.append(_slow_after_first(chunks, hung_up))
    meter = gateway.Meter(1.0, None, allow_unmetered=True)
    _drive(_handler(meter), _request("/v1/messages", _messages(stream=True)), on_client=_hang_up_after_headers(hung_up))
    assert meter.drained == 1 and meter.drain_cut == 0
    assert meter.prompt_tokens == 160 and meter.completion_tokens == 40          # the billed usage is metered


def test_drain_is_bounded_by_the_timeout(upstream: Upstream) -> None:
    hung_up = threading.Event()
    upstream.script.append(_slow_after_first(RESP_STREAM[:1] + [b"data: {}\n\n"] * 3 + RESP_STREAM[1:], hung_up))
    meter = gateway.Meter(1.0, None, allow_unmetered=True)
    _drive(_handler(meter, drain_timeout=0.0), _request("/v1/responses", _responses(stream=True)),
           on_client=_hang_up_after_headers(hung_up))
    assert meter.drained == 1 and meter.drain_cut == 1 and meter.unmetered == 1   # cut short: fails closed


# ---------------------------------------------------------------- caps across dialects

def test_token_cap_applies_across_dialects(upstream: Upstream) -> None:
    upstream.script.append(_json({"type": "message", "usage": {"input_tokens": 5000, "output_tokens": 100,
                                                               "cost": 0.0}}))
    meter = gateway.Meter(100.0, None, max_tokens=5000)
    h = _handler(meter)
    assert _drive(h, _request("/v1/messages", _messages()))[0] == 200
    for path, body in ((d.ROUTE, _chat()), ("/v1/responses", _responses()), ("/v1/messages", _messages())):
        status, reply = _drive(h, _request(path, body))
        assert status == 402 and "token cap" in json.loads(reply)["error"]["message"]
    assert len(upstream.received) == 1 and meter.refused == 3


# ---------------------------------------------------------------- main(): flags, keys, prices

class _NoServer:
    """Stands in for ThreadingHTTPServer so nothing binds; keeps the handler for driving."""

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


def test_main_dialect_flags(tmp_path: Path, no_server: type[_NoServer], upstream: Upstream,
                            capsys: pytest.CaptureFixture[str]) -> None:
    chat_key, msg_key = tmp_path / "openrouter.env", tmp_path / "anthropic.env"
    chat_key.write_text("OPENROUTER_API_KEY=sk-or-x\n")
    msg_key.write_text("# key\nexport ANTHROPIC_API_KEY='sk-ant-x'\n")
    base = ["--model", MODEL, "--listen", "127.0.0.1:9"]
    for bad in (["--dialects", "messages", "--key-file", str(chat_key)],                     # no upstream
                ["--upstream-messages", "https://a.invalid/v1", "--key-file", str(chat_key)],  # not enabled
                ["--dialects", "chat,chat", "--key-file", str(chat_key)],
                ["--dialects", "grpc", "--key-file", str(chat_key)],
                ["--dialects", "messages", "--upstream-messages", "https://a.invalid/v1"],      # no key file
                ["--key-file", str(chat_key), "--max-output-tokens", "0"],
                ["--key-file", str(chat_key), "--drain-timeout", "nan"]):
        with pytest.raises(SystemExit) as exc:
            gateway.main(base + bad)
        assert exc.value.code == 2, bad
    with pytest.raises(SystemExit):
        gateway.main(["--key-file", str(chat_key)])                                          # no model at all
    prices = tmp_path / "prices.json"
    prices.write_text("{not json")
    assert gateway.main(base + ["--key-file", str(chat_key), "--prices", str(prices)]) == 2
    other = tmp_path / "other.env"
    other.write_text("OPENAI_API_KEY=sk-oa\n")                                    # not a messages key name
    assert gateway.main(base + ["--dialects", "messages", "--upstream-messages", "https://a.invalid/v1",
                                "--key-file-messages", str(other)]) == 2
    assert no_server.instances == []
    assert gateway.main(base + ["--dialects", "chat,messages", "--key-file", str(chat_key),
                                "--key-file-messages", str(msg_key), "--upstream-messages",
                                "https://api.anthropic.invalid/v1", "--models", HELPER]) == 0
    (srv,) = no_server.instances
    upstream.script.append(_json({"usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.001}}))
    assert _drive(srv.handler, _request("/v1/messages", _messages(HELPER)))[0] == 200
    got = upstream.received[-1]["headers"]
    assert got["x-api-key"] == "sk-ant-x" and "Authorization" not in got
    out = capsys.readouterr().out
    assert "sk-ant-x" not in out and "sk-or-x" not in out


def test_main_openrouter_messages_upstream_uses_bearer(tmp_path: Path, no_server: type[_NoServer],
                                                       upstream: Upstream) -> None:
    key = tmp_path / "openrouter.env"
    key.write_text("OPENROUTER_API_KEY=sk-or-y\n")
    assert gateway.main(["--model", MODEL, "--key-file", str(key), "--dialects", "messages,responses",
                         "--upstream-messages", "https://openrouter.ai/api/v1",
                         "--upstream-responses", "https://openrouter.ai/api/v1"]) == 0
    (srv,) = no_server.instances
    upstream.script += [_json({"usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.001}})] * 2
    assert _drive(srv.handler, _request("/v1/messages", _messages()))[0] == 200
    assert _drive(srv.handler, _request("/v1/responses", _responses()))[0] == 200
    assert [r["path"] for r in upstream.received] == ["/api/v1/messages", "/api/v1/responses"]
    assert all(r["headers"]["Authorization"] == "Bearer sk-or-y" for r in upstream.received)
    assert _drive(srv.handler, _request(d.ROUTE, _chat()))[0] == 403            # chat not enabled


def test_read_key_prefers_the_dialect_name(tmp_path: Path) -> None:
    path = tmp_path / "k.env"
    path.write_text("OPENROUTER_API_KEY=or\nANTHROPIC_API_KEY=ant\n")
    assert gateway.read_key(path, gateway.KEY_NAMES[d.MESSAGES]) == "ant"
    assert gateway.read_key(path, gateway.KEY_NAMES[d.CHAT]) == "or"
    path.write_text("bare-key\n")
    assert gateway.read_key(path, gateway.KEY_NAMES[d.RESPONSES]) == "bare-key"
