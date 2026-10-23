"""Egress gateway: the agent container's only route to the model, and the place spend is capped.

``python -m agentteams.vulnscan.gateway`` runs in its own container on two networks: the
internal network the agent container sits on, and a bridge out. It enforces:

- **One route.** Only ``POST /api/v1/chat/completions`` (plus ``GET /healthz``) is relayed.
  Every other request is 403.
- **One model.** The request's ``model`` must equal the configured model. OpenRouter
  ``plugins``/web-search features are stripped, and ``usage: {include: true}`` is forced so
  cost is reported.
- **No secret in the agent.** The OpenRouter key is read once from a mounted file and added
  here. The agent container holds a dummy key.
- **Spend cap.** The ``usage.cost`` of every response (JSON or SSE stream) is metered.
  Once the budget is reached, further calls get HTTP 402. An optional token cap
  (``--max-tokens-total``, prompt + completion; runs and debriefs pass one, 0 means none) refuses
  the same way. Replies without a usable ``usage`` are counted as *unmetered*; after
  ``--max-unmetered-calls`` of them (default 20) calls are refused too, unless
  ``--allow-unmetered``. The 402 message says which cap was hit. Totals go to ``spend.json``.
- **No content logging.** Only counts, model, tokens and cost are recorded.

Upstream is the operator's route proxy (``http://host.docker.internal:8791``) or
``https://openrouter.ai``.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import threading
import urllib.parse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROUTE = "/api/v1/chat/completions"
MAX_BODY = 16 * 1024 * 1024
STRIP_KEYS = ("plugins", "web_search_options", "transforms")


def _number(value: object) -> float | None:
    """Return a finite non-negative number from a usage field, or None (bools and strings are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value < float("inf") else None


class Meter:
    """Thread-safe spend accounting with a hard budget, a token cap and an unmetered-reply cap."""

    def __init__(self, budget_usd: float, out: Path | None, *, max_tokens: int = 0,
                 max_unmetered: int = 20, allow_unmetered: bool = False) -> None:
        """Start a meter.

        Args:
            budget_usd: Maximum spend for this run.
            out: Where to persist totals (``spend.json``), or None.
            max_tokens: Maximum prompt + completion tokens (0: no token cap).
            max_unmetered: Replies without a usable ``usage`` object after which calls are refused
                (an upstream that reports nothing would otherwise make every cap a no-op).
            allow_unmetered: Never refuse for unmetered replies (they are still counted).
        """
        self.budget, self.out, self.max_tokens = budget_usd, out, max_tokens
        self.max_unmetered, self.allow_unmetered = max_unmetered, allow_unmetered
        self.spent = 0.0
        self.calls = self.refused = self.unmetered = self.upstream_errors = 0
        self.prompt_tokens = self.completion_tokens = 0
        self.lock = threading.Lock()

    def exhausted_reason(self) -> str:
        """Return why further calls are refused, or ``""`` while they are allowed.

        Returns:
            A short reason naming the budget, the token cap or the unmetered-reply cap.
        """
        with self.lock:
            tokens = self.prompt_tokens + self.completion_tokens
            if self.spent >= self.budget:
                return f"run budget exhausted (${self.budget:.2f} spent)"
            if self.max_tokens > 0 and tokens >= self.max_tokens:
                return f"run token cap reached ({tokens} of {self.max_tokens} tokens)"
            if not self.allow_unmetered and self.unmetered >= self.max_unmetered:
                return (f"{self.unmetered} upstream replies carried no usable usage/cost "
                        f"(cap {self.max_unmetered}); spend cannot be metered")
            return ""

    def exhausted(self) -> bool:
        """Return whether the budget, the token cap or the unmetered-reply cap is used up.

        Returns:
            True when :meth:`exhausted_reason` is non-empty.
        """
        return bool(self.exhausted_reason())

    def add(self, usage: dict[str, Any] | None) -> None:
        """Record one response's usage.

        Args:
            usage: OpenRouter ``usage`` object; None, or one with no non-zero numeric cost or token
                count, counts as an *unmetered* reply.
        """
        usage = usage if isinstance(usage, dict) else {}
        cost, prompt, completion = (_number(usage.get(k)) for k in ("cost", "prompt_tokens", "completion_tokens"))
        with self.lock:
            self.calls += 1
            if not any((cost, prompt, completion)):   # absent, or present but all zero: nothing metered
                self.unmetered += 1
            self.spent += cost or 0.0
            self.prompt_tokens += int(prompt or 0)
            self.completion_tokens += int(completion or 0)
            self._persist()

    def add_error(self) -> None:
        """Count an upstream error reply (4xx/5xx): reported in spend.json, not an unmetered reply."""
        with self.lock:
            self.upstream_errors += 1
            self._persist()

    def refuse(self) -> None:
        """Count a refused call."""
        with self.lock:
            self.refused += 1
            self._persist()

    def _persist(self) -> None:
        if self.out:
            self.out.write_text(json.dumps({"budget_usd": self.budget, "max_tokens": self.max_tokens,
                                            "spent_usd": round(self.spent, 6),
                                            "calls": self.calls, "refused": self.refused,
                                            "unmetered": self.unmetered, "max_unmetered_calls": self.max_unmetered,
                                            "upstream_errors": self.upstream_errors,
                                            "allow_unmetered": self.allow_unmetered,
                                            "prompt_tokens": self.prompt_tokens,
                                            "completion_tokens": self.completion_tokens,
                                            "updated": datetime.now(UTC).isoformat()}, indent=2), encoding="utf-8")


def rewrite_body(raw: bytes, model: str) -> bytes:
    """Validate and normalise a chat-completions request body.

    Args:
        raw: Request body.
        model: The only model allowed.

    Returns:
        The body to forward.

    Raises:
        ValueError: Not JSON, or the wrong model.
    """
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict) or body.get("model") != model:
        raise ValueError(f"only model {model!r} is allowed through this gateway")
    for k in STRIP_KEYS:
        body.pop(k, None)
    body["usage"] = {"include": True}
    return json.dumps(body).encode("utf-8")


def usage_from(payload: bytes) -> dict[str, Any] | None:
    """Extract the ``usage`` object from a JSON body or an SSE stream.

    Args:
        payload: Complete response bytes.

    Returns:
        The last ``usage`` seen, or None.
    """
    text = payload.decode("utf-8", errors="replace")
    found = None
    candidates = [text] if text.lstrip().startswith("{") else \
        [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
    for c in candidates:
        if not c or c == "[DONE]" or '"usage"' not in c:
            continue
        obj = _json_or_none(c)
        if isinstance(obj, dict) and isinstance(obj.get("usage"), dict):
            found = obj["usage"]
    return found


def _json_or_none(text: str) -> object:
    """Decode JSON, or None for a malformed fragment (a partial stream line)."""
    try:
        return json.loads(text)
    except ValueError:
        return None


def make_handler(*, model: str, key: str, upstream: str, meter: Meter) -> type[BaseHTTPRequestHandler]:
    """Build the request handler class bound to this gateway's settings.

    Args:
        model: Allowed model id.
        key: OpenRouter API key (never logged).
        upstream: Base URL to forward to.
        meter: Spend meter.

    Returns:
        A ``BaseHTTPRequestHandler`` subclass.
    """
    up = urllib.parse.urlparse(upstream)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"   # responses end at connection close (simple streaming relay)

        def log_message(self, fmt: str, *args: object) -> None:  # no request/content logging
            return

        def _deny(self, code: int, msg: str) -> None:
            data = json.dumps({"error": {"code": code, "message": msg}}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/healthz":
                self._deny(200, "ok")
            else:
                self._deny(403, "only POST /api/v1/chat/completions is relayed")

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path != ROUTE:
                self._deny(403, "only POST /api/v1/chat/completions is relayed")
                return
            why = meter.exhausted_reason()
            if why:
                meter.refuse()
                self._deny(402, why)
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                self._deny(413, "missing or oversized body")
                return
            try:
                body = rewrite_body(self.rfile.read(length), model)
            except (ValueError, UnicodeDecodeError) as exc:
                self._deny(400, str(exc))
                return
            conn_cls = http.client.HTTPSConnection if up.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(up.hostname, up.port or (443 if up.scheme == "https" else 80), timeout=600)
            status: int | None = None
            seen = bytearray()
            try:
                conn.request("POST", up.path.rstrip("/") + ROUTE, body=body, headers={
                    "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                    "Accept": self.headers.get("Accept", "application/json")})
                resp = conn.getresponse()
                status = resp.status
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                self.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    seen += chunk
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException) as exc:
                if status is None:   # nothing came back: nothing to meter
                    self._deny(502, f"upstream error: {type(exc).__name__}")
            finally:
                conn.close()
                # Meter whatever came back, even when the client hung up mid-stream (V2-5): a partial
                # stream usually carries no usage and then counts as unmetered, which fails closed.
                if status is not None and status >= 400:
                    meter.add_error()
                elif status is not None:
                    meter.add(usage_from(bytes(seen)))

    return Handler


def main(argv: list[str] | None = None) -> int:
    """Run the gateway until killed.

    Args:
        argv: Arguments.

    Returns:
        Exit status.
    """
    p = argparse.ArgumentParser(prog="python -m agentteams.vulnscan.gateway")
    p.add_argument("--listen", default="0.0.0.0:8080")
    p.add_argument("--model", required=True)
    p.add_argument("--key-file", type=Path, required=True)
    p.add_argument("--upstream", default="https://openrouter.ai")
    p.add_argument("--budget-usd", type=float, default=2.0)
    p.add_argument("--spend-file", type=Path, default=None)
    p.add_argument("--max-tokens-total", type=int, default=0, help="prompt + completion token cap (0: none)")
    p.add_argument("--max-unmetered-calls", type=int, default=20,
                   help="refuse after this many replies without usable usage/cost (>= 1)")
    p.add_argument("--allow-unmetered", action="store_true", help="never refuse for unmetered replies")
    a = p.parse_args(argv)
    if a.max_unmetered_calls < 1:
        p.error("--max-unmetered-calls must be >= 1")
    key = ""
    for line in a.key_file.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line.startswith("OPENROUTER_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("'\"")
        elif line and "=" not in line and not key:
            key = line
    if not key:
        print("gateway: no OPENROUTER_API_KEY in the key file", file=sys.stderr)
        return 2
    host, port = a.listen.rsplit(":", 1)
    meter = Meter(a.budget_usd, a.spend_file, max_tokens=max(0, a.max_tokens_total),
                  max_unmetered=a.max_unmetered_calls, allow_unmetered=a.allow_unmetered)
    server = ThreadingHTTPServer((host, int(port)),
                                 make_handler(model=a.model, key=key, upstream=a.upstream, meter=meter))
    print(f"gateway: model={a.model} upstream={a.upstream} budget=${a.budget_usd:.2f} listening {a.listen}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
