"""Allowlisting CONNECT proxy for one vulnscan tools-container operation (stdlib only; runs inside the tools image).

The operation's container sits on an internal Docker network with no route out; this proxy, on that network and the
default bridge, is its only way to the internet. It accepts ``CONNECT host:port`` for exactly the ``--allow``
endpoints and answers everything else (another host or port, an IP literal not listed, any other method) with 403.
git (libcurl), npm and ssh (``nc -X connect``) all speak CONNECT. Each decision is logged to stderr.

    python3 -I egress_proxy.py --listen 0.0.0.0:3128 --allow github.com:443 [--allow ...]
"""

from __future__ import annotations

import argparse
import re
import select
import socket
import socketserver
import sys

READY = "egress-proxy listening"
_REQUEST = re.compile(r"CONNECT ([A-Za-z0-9.-]{1,253}):(\d{1,5}) HTTP/1\.[01]\r?\n")
IDLE_SECONDS = 300


def decide(request_line: str, allow: frozenset[str]) -> tuple[str, int] | None:
    """Return (host, port) when ``request_line`` is a CONNECT to an allowed endpoint, else None."""
    m = _REQUEST.fullmatch(request_line)
    if not m:
        return None
    host, port = m.group(1).lower(), int(m.group(2))
    return (host, port) if f"{host}:{port}" in allow else None


def _log(msg: str) -> None:
    print(f"egress-proxy: {msg}", file=sys.stderr, flush=True)


def _pipe(a: socket.socket, b: socket.socket) -> None:
    peers = {a: b, b: a}
    while True:
        ready, _, _ = select.select(list(peers), [], [], IDLE_SECONDS)
        if not ready:
            return
        for s in ready:
            data = s.recv(65536)
            if not data:
                return
            peers[s].sendall(data)


class Handler(socketserver.StreamRequestHandler):
    """One client connection: read the CONNECT request, then relay bytes or refuse."""

    rbufsize = 0           # unbuffered: never read past the request headers
    allow: frozenset[str] = frozenset()

    def handle(self) -> None:
        line = self.rfile.readline(1024).decode("latin-1")
        for _ in range(100):   # headers, up to the blank line
            header = self.rfile.readline(8192)
            if header in (b"\r\n", b"\n", b""):
                break
        target = decide(line, self.allow)
        if target is None:
            _log(f"refused {line.strip()[:200]!r}")
            self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        try:
            upstream = socket.create_connection(target, timeout=20)
        except OSError as exc:
            _log(f"upstream {target[0]}:{target[1]} unreachable: {exc}")
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        _log(f"allowed {target[0]}:{target[1]}")
        with upstream:
            upstream.settimeout(None)
            self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            _pipe(self.connection, upstream)


class Server(socketserver.ThreadingTCPServer):
    """Threaded server; threads never keep the process alive."""

    daemon_threads = True
    allow_reuse_address = True


def main(argv: list[str] | None = None) -> int:
    """Run the proxy until killed."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--listen", default="0.0.0.0:3128")
    p.add_argument("--allow", action="append", default=[], help="host:port (repeatable)")
    args = p.parse_args(argv)
    allow = frozenset(a.lower() for a in args.allow)
    if not allow:
        _log("no --allow endpoint: refusing to start")
        return 2
    host, _, port = args.listen.rpartition(":")
    handler = type("AllowHandler", (Handler,), {"allow": allow})
    with Server((host, int(port)), handler) as srv:
        print(f"{READY} on {args.listen}; allowing {', '.join(sorted(allow))}", file=sys.stderr, flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
