#!/usr/bin/env python3
"""Local proxy that injects OpenRouter provider routing into every goose request.

WHY THIS EXISTS
---------------
OpenRouter serves one model id from many upstream backends, and they are not equally
correct. Measured 2026-07-24 against ``qwen/qwen3.6-27b`` by replaying a real captured
agent payload (11 messages, 24 tools, reasoning enabled), 12 trials per backend:

    Alibaba 12/12 ok   CoreWeave 12/12 ok   Morph 12/12 ok
    Chutes  1/12 leak  Phala     1/12 leak  SiliconFlow 3/12 leak

A "leak" is the dead-turn bug: the model emits its tool call as literal
``<tool_call>`` text inside its reasoning stream instead of the structured tool-call
field, so goose sees nothing actionable, the turn ends with ``finish_reason: stop``,
and NO error is logged anywhere. See
``references/plans/goose-openrouter-tool-call-reasoning-leak-2026-07-24.report.md``.

OpenRouter can be told which backends to use -- but **goose 1.37.0 provides no way to
send that**. ``OPENROUTER_PARAMETERS`` in ``config.yaml`` is inert (verified: the key
does not appear in the 1.37.0 binary, and neither a nested ``provider`` block nor a
top-level ``transforms`` override reaches the wire, while a ``model`` change in the
same file does). ``OPENROUTER_HOST`` *is* honored, so this proxy adds the routing at
the transport layer.

Because it sits under ``OPENROUTER_HOST``, it covers **every goose surface** -- CLI,
``goose acp`` (the VS Code extension), and desktop -- unlike a ``goose run`` subprocess
wrapper, which by construction cannot see ACP traffic.

USAGE
-----
    python3 scripts/goose-openrouter-route-proxy.py --port 8791

Then in ``~/.config/goose/config.yaml``::

    OPENROUTER_HOST: http://127.0.0.1:8791

and restart goose (for the VS Code extension: reload the window, so the ``goose acp``
daemons re-read config).

Defaults to an **allowlist** of backends measured clean. Prefer ``--only`` over
``--ignore``: a denylist is only as good as your enumeration of bad backends, and an
incomplete one silently reroutes to whatever is left -- which is how an early version
of this work landed on SiliconFlow, the worst measured offender.

OPERATIONAL COST, STATED PLAINLY
--------------------------------
This is a process that must be running. If it is not, goose has no endpoint and every
request fails. That is a real tradeoff versus tolerating an intermittent dead turn;
it is why nothing here is installed or auto-started for you.

CAVEATS
-------
* "Clean" is bounded, not proven: 0/12 per backend leaves a ~22% upper bound on the
  true leak rate at 95% confidence (~10% pooled across the three). This reduces the
  failure rate; it does not guarantee zero.
* **Confirmed the hard way later the same day: the leak recurred on Morph** -- one of
  the three backends above -- with this proxy verifiably injecting the allowlist. An
  8-run end-to-end test still produced 2 silent dead turns. Treat the table as "these
  were the best of a bad set", not as a list of safe backends.
* Restricting backends can lower the effective context ceiling (Morph advertises 131k
  vs 262k for the others) and reduce availability/price competition.
* Backend behavior is not static, and **no tooling here re-measures it for you**.
  ``scripts/goose-openrouter-preflight.py --providers <model>`` lists the current
  roster (who serves the model, context, quantization) -- useful for spotting that a
  listed backend has disappeared, but it does NOT measure tool-call reliability. That
  still requires replaying a real payload N times per backend by hand; an automated
  ``--measure`` mode is logged as open work in
  ``references/agentteams-remediation-log.csv``.
* ``--only`` sets ``allow_fallbacks: false``, so if every listed backend is delisted
  upstream the request **fails loudly** rather than silently routing somewhere
  unmeasured. That is deliberate -- a silent fallback would defeat the allowlist --
  but it does mean a stale list turns into hard errors, not degraded routing.
* Secrets: the ``Authorization`` header is relayed verbatim and never logged.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_UPSTREAM = "https://openrouter.ai"

#: Backends measured clean for structured tool-calling on 2026-07-24 (12/12 each).
#: Not a permanent fact -- re-verify before relying on it.
DEFAULT_ONLY = ("Alibaba", "CoreWeave", "Morph")
MEASURED_ON = "2026-07-24"

# Hop-by-hop headers must not be forwarded (RFC 7230 6.1). ``accept-encoding`` is
# dropped so the upstream does not hand back a compressed body we would relay
# without the matching header semantics.
_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "accept-encoding",
})


def build_provider_block(only: list[str], ignore: list[str], extra: dict) -> dict:
    """Assemble the OpenRouter ``provider`` routing object.

    Args:
        only: Backend names to restrict routing to (allowlist).
        ignore: Backend names to exclude (denylist).
        extra: Additional provider-object fields (e.g. ``{"sort": "throughput"}``).

    Returns:
        The provider routing dict; empty when no routing is requested.
    """
    block: dict[str, object] = {}
    if only:
        block["only"] = list(only)
        # An allowlist that silently falls back defeats the purpose.
        block["allow_fallbacks"] = False
    if ignore:
        block["ignore"] = list(ignore)
    block.update(extra)
    return block


def inject_provider(body: bytes, provider: dict) -> tuple[bytes, dict | None]:
    """Return ``(new_body, applied_provider)`` for one request body.

    Merging is **key-level**: a caller-supplied ``provider`` block wins on the keys it
    actually sets, but this proxy's other keys are still added. A caller sending only
    ``{"ignore": [...]}`` therefore still receives this proxy's ``only`` and
    ``allow_fallbacks``. Bodies that are not a JSON object pass through untouched
    (``applied_provider`` is ``None``).

    Args:
        body: Raw request body bytes.
        provider: Routing block to merge in.

    Returns:
        The (possibly rewritten) body and the provider block actually applied.
    """
    if not provider:
        return body, None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, None
    if not isinstance(payload, dict):
        return body, None
    existing = payload.get("provider")
    payload["provider"] = (
        {**provider, **existing} if isinstance(existing, dict) else dict(provider)
    )
    return json.dumps(payload).encode(), payload["provider"]


def make_handler(upstream: str, provider: dict, verbose: bool):
    """Build the request handler class bound to this proxy's configuration.

    Args:
        upstream: Base URL to relay to.
        provider: Provider routing block to inject into chat-completion bodies.
        verbose: Whether to log each injection.

    Returns:
        A ``BaseHTTPRequestHandler`` subclass.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # silence per-request stderr noise
            pass

        def _relay(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

            if method == "POST" and self.path.endswith("/chat/completions") and body:
                body, applied = inject_provider(body, provider)
                if applied and verbose:
                    print(f"[route-proxy] injected {json.dumps(applied)}", flush=True)

            headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
            headers["Content-Length"] = str(len(body))
            req = urllib.request.Request(
                upstream + self.path, data=body, headers=headers, method=method,
            )
            # Tracks whether response headers are already on the wire. Once streaming
            # has started we must NOT write a second status line + headers into the
            # open chunked body -- that produces malformed framing, and on a broken
            # pipe raises again out of the handler. Drop the connection instead.
            started = False
            try:
                with urllib.request.urlopen(req, timeout=900) as resp:
                    for started in self._stream_back(resp):
                        pass
            except urllib.error.HTTPError as exc:
                if not started:
                    self._send_bytes(exc.code, exc.read(),
                                     exc.headers.get("Content-Type", "application/json"))
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                # Transport failure -> explicit 502 (never a hang). Programming errors
                # are deliberately NOT caught here: they should surface, not be
                # mislabelled as an upstream failure.
                if started:
                    self.close_connection = True
                else:
                    payload = json.dumps(
                        {"error": {"message": f"route-proxy: {type(exc).__name__}: {exc}"}}
                    ).encode()
                    self._send_bytes(502, payload, "application/json")

        def _stream_back(self, resp):
            """Relay the response chunk-by-chunk as it arrives, yielding ``True`` once
            the response headers are on the wire.

            Uses ``read1`` deliberately: ``read(n)`` blocks until it has n bytes *or*
            EOF, which would buffer an entire streamed turn and defeat the point --
            SSE tokens would arrive in one burst at the end. ``read1`` returns whatever
            is already available, keeping the stream live.

            Yields:
                ``True`` after the status line and headers have been sent, so the
                caller knows a later failure can no longer be reported as a fresh
                HTTP response.
            """
            self.send_response(resp.status)
            for key, value in resp.headers.items():
                if key.lower() not in _HOP:
                    self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            yield True
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
                yield True
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            self._relay("POST")

        def do_GET(self) -> None:  # noqa: N802
            self._relay("GET")

    return Handler


def main(argv: list[str] | None = None) -> int:
    """Run the routing proxy. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8791, help="local port to listen on")
    parser.add_argument("--only", default=",".join(DEFAULT_ONLY),
                        help="comma-separated backend allowlist (default: measured-clean set); "
                             "pass '' to disable")
    parser.add_argument("--ignore", default="",
                        help="comma-separated backend denylist (prefer --only)")
    parser.add_argument("--sort", default=None, choices=["price", "throughput", "latency"],
                        help="OpenRouter routing preference among permitted backends")
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM, help="upstream base URL")
    parser.add_argument("--quiet", action="store_true", help="do not log injections")
    args = parser.parse_args(argv)

    only = [p.strip() for p in args.only.split(",") if p.strip()]
    ignore = [p.strip() for p in args.ignore.split(",") if p.strip()]
    extra = {"sort": args.sort} if args.sort else {}
    provider = build_provider_block(only, ignore, extra)

    if not provider:
        print("route-proxy: no routing requested (--only and --ignore both empty); "
              "this would be a plain passthrough.", file=sys.stderr)
        return 2

    print(f"[route-proxy] 127.0.0.1:{args.port} -> {args.upstream}")
    print(f"[route-proxy] injecting provider={json.dumps(provider)}")
    if only == list(DEFAULT_ONLY):
        # A stale allowlist is the realistic failure mode here: backend rosters and
        # behavior change, and `only` + allow_fallbacks:false hard-fails (loudly, by
        # design) if every listed backend is delisted upstream.
        print(f"[route-proxy] NOTE: built-in allowlist, measured {MEASURED_ON} and not "
              "re-verified since. Check the roster is still current with: "
              "scripts/goose-openrouter-preflight.py --providers <model> "
              "(roster only -- it does not measure reliability).")
    print(f"[route-proxy] set OPENROUTER_HOST: http://127.0.0.1:{args.port} in config.yaml, "
          "then restart goose (VS Code: reload window)")
    handler = make_handler(args.upstream, provider, verbose=not args.quiet)
    try:
        ThreadingHTTPServer(("127.0.0.1", args.port), handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[route-proxy] stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
