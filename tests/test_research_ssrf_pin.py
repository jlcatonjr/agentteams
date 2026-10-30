"""DNS-rebinding pin for ``search._fetch_raw`` (research-hardening R4, finding #6).

The host is resolved once by ``_vetted_addresses`` and the request goes to that vetted IP, with
the hostname kept in the ``Host`` header and in httpcore's ``sni_hostname`` extension (TLS SNI and
certificate verification). No test touches the network: ``socket.getaddrinfo`` is faked, and the
HTTP layer is either an ``httpx.MockTransport`` or the real httpx/httpcore stack over a local
``socketpair`` with ``ssl.SSLContext.wrap_socket`` stubbed.
"""

from __future__ import annotations

import socket
import ssl
import warnings
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from agentteams.research import search

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V4_B = "93.184.216.35"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


def _info(addr: str, port: int) -> tuple[Any, ...]:
    family = socket.AF_INET6 if ":" in addr else socket.AF_INET
    sockaddr: tuple[Any, ...] = (addr, port, 0, 0) if family == socket.AF_INET6 else (addr, port)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)


class _Resolver:
    """Fake ``getaddrinfo``: answers ``first`` on call one and ``later`` (rebinding) afterwards."""

    def __init__(self, first: list[str], later: list[str] | None = None) -> None:
        self.first = first
        self.later = ["127.0.0.1"] if later is None else later
        self.calls: list[str] = []

    def __call__(self, host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        self.calls.append(host)
        addrs = self.first if len(self.calls) == 1 else self.later
        return [_info(a, int(port or 443)) for a in addrs]


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's proxy settings (env or macOS system config) out of these tests."""
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(search, "_warned_proxy", False)


def _mock_stream(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    """Give every ``httpx.Client`` that ``search`` builds a ``MockTransport`` (request building,
    headers and extensions stay real httpx; only the wire is replaced)."""
    real_client = httpx.Client

    def client(**kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(search.httpx, "Client", client)


def _fetch(url: str) -> tuple[bytes, str, str] | None:
    return search._fetch_raw(url, 10_000, 2.0, 10_000, 2.0)


def test_request_goes_to_vetted_ip_with_hostname_in_host_and_sni(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = _Resolver([PUBLIC_V4])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>ok</p>")

    _mock_stream(monkeypatch, handler)
    raw = _fetch("https://news.example/a/b?q=1")
    assert raw is not None and raw[0] == b"<p>ok</p>"
    assert resolver.calls == ["news.example"]  # resolved once; the rebinding answer is never asked for
    (req,) = seen
    assert req.url.host == PUBLIC_V4
    assert req.url.raw_path == b"/a/b?q=1"
    assert req.headers["Host"] == "news.example"
    assert req.extensions["sni_hostname"] == "news.example"


def test_ipv6_address_is_bracketed_and_port_kept_in_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V6]))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"x")

    _mock_stream(monkeypatch, handler)
    assert _fetch("https://news.example:8443/p") is not None
    (req,) = seen
    assert str(req.url) == f"https://[{PUBLIC_V6}]:8443/p"
    assert req.headers["Host"] == "news.example:8443"
    assert req.extensions["sni_hostname"] == "news.example"


def test_idn_host_uses_ascii_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4]))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"x")

    _mock_stream(monkeypatch, handler)
    assert _fetch("https://bücher.example/") is not None
    assert seen[0].headers["Host"] == "xn--bcher-kva.example"
    assert seen[0].extensions["sni_hostname"] == "xn--bcher-kva.example"


@pytest.mark.parametrize("answers", [["127.0.0.1"], [PUBLIC_V4, "10.0.0.5"], [PUBLIC_V4, "::1"], []])
def test_private_or_mixed_or_empty_resolution_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, answers: list[str],
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver(answers))

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent for a refused host")

    _mock_stream(monkeypatch, handler)
    assert _fetch("https://news.example/") is None


def test_unresolvable_host_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    _mock_stream(monkeypatch, lambda r: (_ for _ in ()).throw(AssertionError("no request")))
    assert _fetch("https://news.example/") is None


def test_connect_failure_tries_next_vetted_address_then_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4, PUBLIC_V4_B]))
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        raise httpx.ConnectError("refused", request=request)

    _mock_stream(monkeypatch, handler)
    assert _fetch("https://news.example/") is None  # returns, never raises
    assert hosts == [PUBLIC_V4, PUBLIC_V4_B]


def test_second_address_answers_after_first_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4, PUBLIC_V4_B]))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == PUBLIC_V4:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, content=b"second")

    _mock_stream(monkeypatch, handler)
    raw = _fetch("https://news.example/")
    assert raw is not None and raw[0] == b"second"


def test_connect_timeout_fails_over_to_next_vetted_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4, PUBLIC_V4_B]))
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == PUBLIC_V4:
            raise httpx.ConnectTimeout("no answer", request=request)
        return httpx.Response(200, content=b"second")

    _mock_stream(monkeypatch, handler)
    raw = _fetch("https://news.example/")
    assert raw is not None and raw[0] == b"second"
    assert hosts == [PUBLIC_V4, PUBLIC_V4_B]


def _env_proxy(monkeypatch: pytest.MonkeyPatch, proxy: str, no_proxy: str | None = None) -> None:
    for name in ("NO_PROXY", "no_proxy"):
        if no_proxy is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, no_proxy)
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("https_proxy", proxy)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"x")


def test_env_http_proxy_warns_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_proxy(monkeypatch, "http://proxy.test:3128")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_info(PUBLIC_V4, 443)])
    _mock_stream(monkeypatch, _ok)  # an explicit transport bypasses the proxy; only the warning is under test
    with pytest.warns(UserWarning, match="fail closed") as record:
        _fetch("https://news.example/")
        _fetch("https://news.example/")
    assert len([w for w in record if "fail closed" in str(w.message)]) == 1


@pytest.mark.parametrize(
    ("proxy", "no_proxy"),
    [("socks5://proxy.test:1080", None), ("http://proxy.test:3128", PUBLIC_V4), ("http://proxy.test:3128", "*")],
)
def test_env_proxy_warning_skipped_for_socks_or_bypassed_address(
    monkeypatch: pytest.MonkeyPatch, proxy: str, no_proxy: str | None,
) -> None:
    _env_proxy(monkeypatch, proxy, no_proxy)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_info(PUBLIC_V4, 443)])
    _mock_stream(monkeypatch, _ok)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _fetch("https://news.example/")


def test_non_connect_failure_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4, PUBLIC_V4_B]))
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        raise httpx.ReadTimeout("slow", request=request)

    _mock_stream(monkeypatch, handler)
    assert _fetch("https://news.example/") is None
    assert hosts == [PUBLIC_V4]


def test_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4]))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://127.0.0.1/"})

    _mock_stream(monkeypatch, handler)
    assert _fetch("https://news.example/") is None
    assert len(seen) == 1


def test_parser_disagreement_on_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # urlparse keeps the space; httpx percent-encodes it — the names differ, so refuse.
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4]))
    _mock_stream(monkeypatch, lambda r: (_ for _ in ()).throw(AssertionError("no request")))
    assert _fetch("https://exa mple.example/") is None


def test_userinfo_not_leaked_into_host_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4]))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"x")

    _mock_stream(monkeypatch, handler)
    assert _fetch("https://user:pw@news.example/") is not None
    assert seen[0].headers["Host"] == "news.example"
    assert seen[0].url.host == PUBLIC_V4


def test_is_public_https_unchanged_and_shares_the_vetting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4, PUBLIC_V6]))
    assert search.is_public_https("https://news.example/") is True
    monkeypatch.setattr(socket, "getaddrinfo", _Resolver([PUBLIC_V4, PUBLIC_V4, PUBLIC_V6]))
    assert search._vetted_addresses("https://news.example/") == [PUBLIC_V4, PUBLIC_V6]
    assert search.is_public_https("http://news.example/") is False


# --- real httpx/httpcore stack, no network ---------------------------------------------------


class _PairSocket(socket.socket):
    """One end of a local ``socketpair``; ``setsockopt`` is a no-op because httpcore sets
    ``TCP_NODELAY``, which a Unix-domain pair rejects."""

    def setsockopt(self, *args: Any) -> None:
        return None


class _Wire:
    """Stubs ``socket.create_connection`` (records the target, returns one end of a socketpair)
    and ``ssl.SSLContext.wrap_socket`` (records ``server_hostname``, then fails verification)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, preload: bytes = b"") -> None:
        self.connects: list[tuple[str, int]] = []
        self.server_hostnames: list[str | None] = []
        self.peers: list[socket.socket] = []
        self.preload = preload
        monkeypatch.setattr(socket, "create_connection", self._connect)
        monkeypatch.setattr(ssl.SSLContext, "wrap_socket", self._wrap)

    def _connect(self, address: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
        self.connects.append((address[0], address[1]))
        ours, peer = socket.socketpair()
        if self.preload:
            peer.sendall(self.preload)
        self.peers.append(peer)
        return _PairSocket(fileno=ours.detach())

    def _wrap(self, sock: socket.socket, *args: Any, server_hostname: str | None = None, **kw: Any) -> Any:
        self.server_hostnames.append(server_hostname)
        raise ssl.SSLCertVerificationError("stubbed: verification stops here")

    def close(self) -> None:
        for peer in self.peers:
            peer.close()


def test_real_stack_connects_to_vetted_ip_and_verifies_cert_against_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _Resolver([PUBLIC_V4, PUBLIC_V4_B])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    wire = _Wire(monkeypatch)
    try:
        assert _fetch("https://news.example/") is None  # TLS stub rejects -> ConnectError -> None
    finally:
        wire.close()
    assert resolver.calls == ["news.example"]  # httpx/httpcore never resolved the name
    assert wire.connects == [(PUBLIC_V4, 443), (PUBLIC_V4_B, 443)]
    assert wire.server_hostnames == ["news.example", "news.example"]


def test_real_stack_http_proxy_tunnels_to_vetted_ip_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Residual documented in ``_fetch_raw``: httpcore's CONNECT-tunnel path verifies the
    certificate against the tunnel target (the IP), not ``sni_hostname``, so the fetch fails
    closed. The pin itself holds: the proxy is asked for the vetted IP, never the name."""
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:3128")
    resolver = _Resolver([PUBLIC_V4])
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    wire = _Wire(monkeypatch, preload=b"HTTP/1.1 200 Connection established\r\n\r\n")
    try:
        with pytest.warns(UserWarning, match="fail closed"):
            assert _fetch("https://news.example/") is None
        wire.peers[0].settimeout(1.0)
        connect_request = wire.peers[0].recv(4096)
    finally:
        wire.close()
    assert wire.connects == [("proxy.test", 3128)]
    assert connect_request.startswith(f"CONNECT {PUBLIC_V4}:443 ".encode())
    assert wire.server_hostnames == [PUBLIC_V4]
    assert resolver.calls == ["news.example"]
