"""The gateway's DLP and peer-credential wiring (W2): handler, spend.json, main() flags, Unix listener, review.

Credential-shaped strings are built at runtime (see ``test_vulnscan_gateway_dlp``).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import gateway, gateway_peer, review
from agentteams.vulnscan import gateway_dialects as d
from agentteams.vulnscan import gateway_dlp as dlp
from tests.test_vulnscan_gateway import _NoServer, no_server  # noqa: F401  (no_server: a fixture)
from tests.test_vulnscan_gateway_dialects import (  # noqa: F401  (upstream: a fixture)
    MODEL,
    UPSTREAMS,
    Upstream,
    _drive,
    _handler,
    _json,
    _request,
    upstream,
)
from tests.test_vulnscan_gateway_dlp import SECRETS, fake

ROUTE = {d.CHAT: d.ROUTE, d.MESSAGES: "/v1/messages", d.RESPONSES: "/v1/responses"}
USAGE = {"usage": {"cost": 0.001, "prompt_tokens": 5, "completion_tokens": 5,
                   "input_tokens": 5, "output_tokens": 5}, "object": "response", "type": "message"}


def _raw(dialect: str, text: str) -> bytes:
    if dialect == d.CHAT:
        body: dict[str, Any] = {"model": MODEL, "messages": [{"role": "user", "content": text}]}
    elif dialect == d.MESSAGES:
        body = {"model": MODEL, "max_tokens": 100, "messages": [{"role": "user", "content": text}]}
    else:
        body = {"model": MODEL, "max_output_tokens": 100, "input": text}
    return json.dumps(body).encode()


@pytest.mark.parametrize("dialect", d.DIALECTS)
def test_a_block_is_refused_counted_and_never_sent(upstream: Upstream, tmp_path: Path, dialect: str,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    spend = tmp_path / "spend.json"
    meter = gateway.Meter(1.0, spend)
    secret = SECRETS["anthropic-key"]
    status, reply = _drive(_handler(meter), _request(ROUTE[dialect], _raw(dialect, f"key: {secret}")))
    assert status == 400 and upstream.received == []
    assert json.loads(reply)["error"]["message"].startswith("dlp: anthropic-key ")
    data = json.loads(spend.read_text())
    assert data["refused"] == 1 and data["dlp_blocks"] == {"anthropic-key": 1}
    assert data["refusal_reasons"] == [f"{dialect}: dlp: anthropic-key"]
    captured = capsys.readouterr()
    for blob in (reply.decode(), spend.read_text(), captured.out, captured.err):   # no matched content anywhere
        assert secret not in blob and secret[-20:] not in blob


def test_the_upstream_keys_are_blocked_by_default(upstream: Upstream) -> None:
    meter = gateway.Meter(1.0, None)
    for dialect, up in UPSTREAMS.items():
        status, _ = _drive(_handler(meter), _request(ROUTE[dialect], _raw(dialect, f"echo {up.key}!")))
        assert status == 400
    assert dict(meter.dlp_blocks) == {"known-secret": 3} and upstream.received == []


@pytest.mark.parametrize("dialect", d.DIALECTS)
def test_redactions_reach_the_upstream_and_spend_json(upstream: Upstream, tmp_path: Path, dialect: str) -> None:
    spend = tmp_path / "spend.json"
    meter = gateway.Meter(1.0, spend)
    f = dlp.Filter(hostnames=["build-box-7"])
    text = "repo /Users/user/app on build-box-7 (10.0.0.4), owner dev@corp.example.org"
    for _ in range(2):                              # the second, identical request is not recounted
        upstream.script.append(_json(USAGE))
        status, _ = _drive(_handler(meter, dlp_filter=f), _request(ROUTE[dialect], _raw(dialect, text)))
        assert status == 200
    sent = upstream.received[-1]["body"]
    got = {d.CHAT: lambda b: b["messages"][0]["content"], d.MESSAGES: lambda b: b["messages"][0]["content"],
           d.RESPONSES: lambda b: b["input"]}[dialect](sent)
    assert got == "repo ~/app on [REDACTED:hostname] ([REDACTED:private-ip]), owner [REDACTED:email]"
    data = json.loads(spend.read_text())
    assert data["dlp_redactions"] == {"email": 1, "home-path": 1, "hostname": 1, "private-ip": 1}
    assert data["dlp_blocks"] == {} and data["refused"] == 0
    assert "/Users/user" not in spend.read_text() and "corp.example.org" not in spend.read_text()


def test_dlp_responses_flags_without_altering(upstream: Upstream, tmp_path: Path) -> None:
    secret = SECRETS["github-token"]
    reply_body = {**USAGE, "choices": [{"message": {"content": f"found {secret}"}}]}
    meter = gateway.Meter(1.0, tmp_path / "spend.json")
    upstream.script.append(_json(reply_body))
    status, reply = _drive(_handler(meter, dlp_responses=True), _request(d.ROUTE, _raw(d.CHAT, "hello")))
    assert status == 200 and json.loads(reply) == reply_body          # relayed unchanged
    data = json.loads((tmp_path / "spend.json").read_text())
    assert data["dlp_response_flags"] == {"github-token": 1} and secret not in json.dumps(data)
    upstream.script.append(_json(reply_body))
    _drive(_handler(meter), _request(d.ROUTE, _raw(d.CHAT, "hello")))  # default: replies are not scanned
    assert dict(meter.dlp_response_flags) == {"github-token": 1}


# ---------------------------------------------------------------- main(): flags and secrets files

def test_main_loads_secrets_without_printing_them(tmp_path: Path, no_server: type[_NoServer],  # noqa: F811
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    key = "sk-" + "or-v1-" + fake(64, 5, "0123456789abcdef")
    other = "FIXTURE_TOKEN=" + fake(24, 11)
    (tmp_path / "k.env").write_text(f"OPENROUTER_API_KEY={key}\nSECOND_SECRET={fake(20, 3)}\n")
    (tmp_path / "extra.env").write_text(other + "\n")
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "x.env").write_text("DIR_TOKEN=" + fake(30, 13) + "\n")
    names = tmp_path / "names.txt"
    names.write_text("acme-*\n")
    assert gateway.main(["--model", "m", "--key-file", str(tmp_path / "k.env"), "--listen", "127.0.0.1:9",
                         "--dlp-secrets-file", str(tmp_path / "extra.env"), "--dlp-secrets-dir", str(secrets_dir),
                         "--dlp-hostname", "build-box-7", "--dlp-private-names", "@corp/*",
                         "--dlp-private-names-file", str(names)]) == 0
    out = capsys.readouterr()
    assert "dlp: 4 exact secret(s)" in out.out and "2 private-name pattern(s)" in out.out
    for value in (key, fake(20, 3), fake(24, 11), fake(30, 13)):
        assert value not in out.out + out.err
    assert len(no_server.instances) == 1
    assert gateway.main(["--model", "m", "--key-file", str(tmp_path / "k.env"), "--dlp-secrets-dir", "",
                         "--dlp-private-names-file", str(tmp_path / "missing.txt")]) == 2


def test_main_filter_blocks_secrets_file_values(tmp_path: Path, upstream: Upstream) -> None:
    value = fake(24, 11)
    extra = tmp_path / "extra.env"
    extra.write_text(f"FIXTURE_TOKEN={value}\n")
    a = gateway._parser().parse_args(["--model", MODEL, "--dlp-secrets-file", str(extra), "--dlp-secrets-dir", ""])
    f = gateway._dlp_filter(a, ["upstream-key-" + fake(12)], [])
    meter = gateway.Meter(1.0, None)
    status, _ = _drive(_handler(meter, dlp_filter=f), _request(d.ROUTE, _raw(d.CHAT, f"cat says {value}")))
    assert status == 400 and dict(meter.dlp_blocks) == {"known-secret": 1}


def test_listen_and_peer_uid_flags(tmp_path: Path, no_server: type[_NoServer]) -> None:  # noqa: F811
    key = tmp_path / "k.env"
    key.write_text("OPENROUTER_API_KEY=x\n")
    base = ["--model", "m", "--key-file", str(key), "--dlp-secrets-dir", ""]
    for bad in (["--listen", "nocolon"], ["--listen", "127.0.0.1:9", "--peer-uid", "0"],
                ["--listen", f"unix:{tmp_path / 'missing-dir' / 's.sock'}"]):
        with pytest.raises(SystemExit) as exc:
            gateway.main(base + bad)
        assert exc.value.code == 2, bad


# ---------------------------------------------------------------- peer credentials (Unix listener)

def test_peer_uid_from_a_socketpair() -> None:
    a, b = socket.socketpair()
    try:
        assert gateway_peer.peer_uid(a) == os.getuid()
        assert gateway_peer.peer_allowed(a, os.getuid()) == (True, os.getuid())
        assert gateway_peer.peer_allowed(b, os.getuid() + 1) == (False, os.getuid())
    finally:
        a.close()
        b.close()
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert gateway_peer.peer_allowed(tcp, os.getuid()) == (False, None)   # no credentials: refused
    finally:
        tcp.close()


@pytest.fixture
def sock_dir() -> Any:
    path = Path(tempfile.mkdtemp(prefix="gw", dir="/tmp"))   # short: AF_UNIX paths are limited to ~100 bytes
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_socket_path_problems(sock_dir: Path) -> None:
    assert gateway_peer.socket_path_problem(sock_dir / "s.sock") == ""
    assert "longer" in gateway_peer.socket_path_problem(sock_dir / ("x" * 120))
    (sock_dir / "file").write_text("")
    assert "not a socket" in gateway_peer.socket_path_problem(sock_dir / "file")
    assert gateway_peer.socket_path_problem(sock_dir / "nope" / "s.sock").startswith("the socket directory cannot")
    os.chmod(sock_dir, 0o777)
    try:
        assert "writable by group or others" in gateway_peer.socket_path_problem(sock_dir / "s.sock")
    finally:
        os.chmod(sock_dir, 0o700)


def _serve(sock_dir: Path, allowed_uid: int, meter: gateway.Meter) -> gateway_peer.UnixGatewayServer:
    denied: list[int | None] = []
    srv = gateway_peer.UnixGatewayServer(sock_dir / "gw.sock", _handler(meter), allowed_uid=allowed_uid,
                                         on_deny=lambda uid: (denied.append(uid), meter.refuse("unix: denied")))
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


def _get_healthz(path: Path) -> bytes:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5)
    try:
        c.connect(str(path))
        c.sendall(b"GET /healthz HTTP/1.0\r\nHost: gw\r\n\r\n")
        data = bytearray()
        while chunk := c.recv(65536):
            data += chunk
        return bytes(data)
    except ConnectionResetError:
        return b""
    finally:
        c.close()


def test_the_unix_listener_admits_only_the_allowed_uid(sock_dir: Path) -> None:
    meter = gateway.Meter(1.0, None)
    srv = _serve(sock_dir, os.getuid(), meter)
    try:
        assert oct(os.stat(sock_dir / "gw.sock").st_mode & 0o777) == "0o600"
        assert _get_healthz(sock_dir / "gw.sock").startswith(b"HTTP/1.0 200")
    finally:
        srv.shutdown()
        srv.server_close()
    assert not (sock_dir / "gw.sock").exists()
    srv = _serve(sock_dir, os.getuid() + 1, meter)        # a stale-free rebind; now our uid is not the allowed one
    try:
        assert _get_healthz(sock_dir / "gw.sock") == b""
        assert meter.refused == 1 and meter.reasons == ["unix: denied"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_non_socket_at_the_path_is_refused(sock_dir: Path) -> None:
    (sock_dir / "gw.sock").write_text("not a socket")
    with pytest.raises(ValueError, match="not a socket"):
        gateway_peer.UnixGatewayServer(sock_dir / "gw.sock", _handler(gateway.Meter(1.0, None)), allowed_uid=0)


# ---------------------------------------------------------------- review surfaces the counts

def test_review_lines_show_class_counts_only(tmp_path: Path) -> None:
    spend = tmp_path / "spend.json"
    assert review._gateway_dlp_lines(spend) == []
    spend.write_text(json.dumps({"dlp_blocks": {"github-token": 2, "Weird Class<script>": 1, "jwt": 0},
                                 "dlp_redactions": {"email": 3}, "dlp_response_flags": {}}))
    lines = review._gateway_dlp_lines(spend)
    assert lines == ["- gateway DLP refused request(s) carrying a secret or key (by class): other 1, github-token 2",
                     "- gateway DLP redactions in requests (by class; not an anomaly): email 3"]
    spend.write_text("[]")
    assert review._gateway_dlp_lines(spend) == []
