"""Egress gateway: the agent container's only route to the model, and the place spend is capped.

``python -m agentteams.vulnscan.gateway`` runs in its own container on two networks: the
internal network the agent container sits on, and a bridge out. It enforces:

- **Routes.** ``POST /api/v1/chat/completions`` (OpenRouter chat) and, when configured with
  ``--dialects``, ``POST /v1/messages`` (Anthropic Messages) and ``POST /v1/responses`` (OpenAI
  Responses; both also under ``/api/v1/``), plus ``GET /healthz``. Each is passed through, untranslated,
  to an upstream speaking the same dialect (:mod:`.gateway_dialects`). Every other request is 403;
  ``count_tokens`` is refused (404) with a clear message.
- **Allowed models.** The request's ``model`` must equal one of the configured models (``--model``
  plus ``--models``; exact match). On the chat route OpenRouter ``plugins``/web-search features are
  stripped and ``usage: {include: true}`` is forced so cost is reported (OpenRouter-only rewrites).
  One non-OpenRouter key is stripped there too, as a deliberate exception: goose's ``session_id``
  grouping label (a duplicate of the allowed ``user``), so it never reaches the upstream. With
  ``--chat-reasoning`` (``agent.reasoning``) the operator's ``reasoning`` object replaces the client's.
  Every route is then **deny by default**: body keys, tool types, ``anthropic-*`` header values
  outside the allowlists, server-side tools, OpenRouter fallback ``models`` and content the upstream
  would fetch are refused (400, counted as ``refused``), never stripped. Routes match the exact path;
  the one query accepted is ``?beta=true`` on the Messages routes (Claude Code adds it), and the
  client's query string is never forwarded.
- **No secret in the agent.** Each upstream's key is read once from a mounted file and added
  here in that dialect's auth header. The agent container holds a dummy key, never forwarded.
- **Spend cap.** Usage is parsed per dialect from every response (JSON or SSE stream). Spend is the
  upstream's ``usage.cost``, else the operator price table (``--prices``), else the reply counts as
  *unmetered* (Messages/Responses; chat keeps its rule: a reply with tokens or cost is metered).
  Once the budget is reached, further calls get HTTP 402. An optional token cap
  (``--max-tokens-total``, prompt + completion, all dialects; runs and debriefs pass one, 0 means none)
  refuses the same way. After ``--max-unmetered-calls`` unmetered replies (default 20) calls are
  refused too, unless ``--allow-unmetered``. The 402 message says which cap was hit. Totals go to
  ``spend.json``. Upstream calls are serialised (the cap check and the metering of one call happen
  before the next call is checked), so concurrent requests cannot overshoot a cap. Messages and
  Responses calls must carry an output ceiling (a Responses call without one reserves
  ``--responses-reserve-tokens`` when that is set: Codex sends none); every call is **reserved** first (input estimate +
  ceiling, in tokens and priced; a chat call without ``max_tokens`` reserves ``--chat-reserve-tokens``):
  a call that could cross a cap is refused (402) before it is sent. When the
  client hangs up mid-stream, the upstream reply is still read to its end (``--drain-timeout``) so
  the billed usage is metered.
- **DLP (bounds T6, never closes it).** Every request that passes the policy is scanned
  (:mod:`.gateway_dlp`): a secret or key (the upstream keys and the secrets files' values by exact
  match, known credential formats, secret-like assignments) is refused (400, ``refused``, reason
  ``dlp: <class>``, counted in ``dlp_blocks``); private package names, home paths, email addresses,
  host names and private IP addresses in message text are redacted and counted in ``dlp_redactions``.
  In a container the launcher supplies the host's context (:mod:`.gateway_context`): its host names
  (``--dlp-hostnames-file``), the enrolled projects' private package names (``--dlp-private-names-file``)
  and the host's secrets directories (``--dlp-secrets-mount``, each a read-only directory mount; files the
  operator acknowledged in ``agent.dlp_skip_ok`` are listed in ``--dlp-skip-ok-file``). A secrets directory or
  file it cannot read stops it (exit 2, after writing a count to ``dlp-refused`` next to the spend file).
  ``--dlp-responses`` also flags secrets in replies (``dlp_response_flags``) without altering them.
- **Listener.** TCP ``--listen HOST:PORT`` on the container network (default), or for host-side use
  ``--listen unix:PATH``: a 0600 Unix socket admitting only ``--peer-uid`` by kernel peer credentials
  (:mod:`.gateway_peer`).
- **No content logging.** Only counts, model, tokens and cost are recorded.

The chat upstream is the operator's route proxy (``http://host.docker.internal:8791``) or
``https://openrouter.ai``; the Messages and Responses upstreams are API bases such as
``https://openrouter.ai/api/v1``, ``https://api.anthropic.com/v1`` or ``https://api.openai.com/v1``.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import sys
import threading
import time
import urllib.parse
from collections import Counter
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import gateway_dialects as d
from . import gateway_dlp as dlp
from . import gateway_peer

# The chat helpers live in gateway_dialects; these names stay importable from here.
ROUTE, STRIP_KEYS, rewrite_body, usage_from = d.ROUTE, d.STRIP_KEYS, d.rewrite_body, d.usage_from
MAX_BODY = 16 * 1024 * 1024
MAX_REASONS = 20
DEFAULT_DRAIN_TIMEOUT = 120.0
_number = d.number


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
        self.reserved_usd, self.reserved_tokens = 0.0, 0
        self.drained = self.drain_cut = 0   # replies read on after a client hang-up; drains cut by the timeout
        self.reasons: list[str] = []        # the last distinct refusal reasons (refuse)
        # DLP (gateway_dlp): blocked requests and redactions per class; secrets flagged in replies (--dlp-responses)
        self.dlp_blocks: Counter[str] = Counter()
        self.dlp_redactions: Counter[str] = Counter()
        self.dlp_response_flags: Counter[str] = Counter()
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
        """Record one chat response's usage.

        Args:
            usage: OpenRouter ``usage`` object; None, or one with no non-zero numeric cost or token
                count, counts as an *unmetered* reply.
        """
        usage = usage if isinstance(usage, dict) else {}
        cost, prompt, completion = (_number(usage.get(k)) for k in ("cost", "prompt_tokens", "completion_tokens"))
        # absent, or present but all zero: nothing metered
        self.record(cost, int(prompt or 0), int(completion or 0), unmetered=not any((cost, prompt, completion)))

    def record(self, cost: float | None, prompt: int, completion: int, *, unmetered: bool) -> None:
        """Record one response: its spend (None: unknown), its tokens, and whether it counts as unmetered."""
        with self.lock:
            self.calls += 1
            if unmetered:
                self.unmetered += 1
            self.spent += cost or 0.0
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self._persist()

    def reserve(self, usd: float, tokens: int) -> str:
        """Reserve a call's worst case against the budget and the token cap (D3).

        Args:
            usd: Worst-case spend (0 when the model has no price-table entry).
            tokens: Input estimate plus the requested output ceiling.

        Returns:
            ``""`` when reserved (release it with :meth:`release`), else why the call is refused.
        """
        with self.lock:
            left_usd = self.budget - self.spent - self.reserved_usd
            left_tokens = self.max_tokens - self.prompt_tokens - self.completion_tokens - self.reserved_tokens
            if usd > left_usd:
                return (f"reservation refused: this call could cost ${usd:.4f}; ${max(left_usd, 0.0):.4f} of the "
                        f"${self.budget:.2f} run budget is left (lower the output ceiling, or raise the budget)")
            if self.max_tokens > 0 and tokens > left_tokens:
                return (f"reservation refused: this call could use {tokens} tokens; {max(left_tokens, 0)} of the "
                        f"{self.max_tokens}-token run cap are left")
            self.reserved_usd += usd
            self.reserved_tokens += tokens
            self._persist()
            return ""

    def release(self, usd: float, tokens: int) -> None:
        """Release a reservation once the call is metered."""
        with self.lock:
            self.reserved_usd = max(0.0, self.reserved_usd - usd)
            self.reserved_tokens = max(0, self.reserved_tokens - tokens)
            self._persist()

    def drain(self, *, cut: bool) -> None:
        """Count a reply read on after the client hung up; ``cut``: the drain timeout ended it early."""
        with self.lock:
            self.drained += 1
            self.drain_cut += int(cut)
            self._persist()

    def add_error(self) -> None:
        """Count an upstream error (a 4xx/5xx reply, or no reply: DNS, refused, timeout); not an unmetered reply."""
        with self.lock:
            self.upstream_errors += 1
            self._persist()

    def refuse(self, reason: str = "", *, dlp_class: str = "") -> None:
        """Count a refused call; keep its reason (policy text: key, tool or beta names, never content).

        The last :data:`MAX_REASONS` distinct reasons go to ``spend.json`` as ``refusal_reasons``, so the
        operator can see which allowlist entry a CLI needs (the acceptance script prints them).
        ``dlp_class``: a DLP block; counted per class in ``dlp_blocks`` (the class name only, never the match).
        """
        with self.lock:
            self.refused += 1
            if dlp_class:
                self.dlp_blocks[dlp_class] += 1
            reason = reason[:200]
            if reason and reason not in self.reasons:
                self.reasons = [*self.reasons, reason][-MAX_REASONS:]
            self._persist()

    def dlp_redacted(self, counts: Mapping[str, int]) -> None:
        """Count a relayed request's DLP redactions per class (``dlp_redactions``)."""
        if not any(counts.values()):
            return
        with self.lock:
            self.dlp_redactions.update(counts)
            self._persist()

    def dlp_response(self, cls: str) -> None:
        """Count an upstream reply in which a DLP block class was seen (``dlp_response_flags``; not altered)."""
        with self.lock:
            self.dlp_response_flags[cls] += 1
            self._persist()

    def _persist(self) -> None:
        """Write ``spend.json`` atomically (G12): a temp file beside it, then ``os.replace``.

        A reader (the host launcher) never sees a truncated file; on failure the temp is removed,
        the previous ``spend.json`` stays, and the error propagates. Called with :attr:`lock` held.
        """
        if not self.out:
            return
        text = json.dumps({"budget_usd": self.budget, "max_tokens": self.max_tokens,
                           "spent_usd": round(self.spent, 6),
                           "calls": self.calls, "refused": self.refused,
                           "unmetered": self.unmetered, "max_unmetered_calls": self.max_unmetered,
                           "upstream_errors": self.upstream_errors,
                           "allow_unmetered": self.allow_unmetered,
                           "prompt_tokens": self.prompt_tokens,
                           "completion_tokens": self.completion_tokens,
                           "reserved_usd": round(self.reserved_usd, 6), "reserved_tokens": self.reserved_tokens,
                           "drained": self.drained, "drain_cut": self.drain_cut,
                           "refusal_reasons": self.reasons,
                           "dlp_blocks": dlp.from_mapping(self.dlp_blocks),
                           "dlp_redactions": dlp.from_mapping(self.dlp_redactions),
                           "dlp_response_flags": dlp.from_mapping(self.dlp_response_flags),
                           "updated": datetime.now(UTC).isoformat()}, indent=2)
        tmp = self.out.with_name(f".{self.out.name}.tmp")
        replaced = False
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.out)
            replaced = True
        finally:
            if not replaced:   # the error propagates; only the temp is removed
                tmp.unlink(missing_ok=True)


def make_handler(*, model: str = "", key: str = "", upstream: str = "https://openrouter.ai", meter: Meter,
                 models: Collection[str] = (), upstreams: Mapping[str, d.Upstream] | None = None,
                 prices: Mapping[str, Mapping[str, float]] | None = None,
                 max_output_tokens: int = d.DEFAULT_MAX_OUTPUT_TOKENS,
                 drain_timeout: float = DEFAULT_DRAIN_TIMEOUT,
                 anthropic_betas: Collection[str] = (),
                 chat_reserve_tokens: int = d.DEFAULT_CHAT_RESERVE_TOKENS,
                 dlp_filter: dlp.Filter | None = None,
                 dlp_responses: bool = False, responses_reserve_tokens: int = 0,
                 chat_reasoning: Mapping[str, Any] | None = None) -> type[BaseHTTPRequestHandler]:
    """Build the request handler class bound to this gateway's settings.

    Args:
        model: An allowed model id (joined with ``models``).
        key: OpenRouter API key for the chat route when ``upstreams`` is None (never logged).
        upstream: Chat base URL when ``upstreams`` is None.
        meter: Spend meter.
        models: Further allowed model ids (exact match).
        upstreams: Enabled dialects and their upstreams (default: chat only, at ``upstream`` with ``key``).
        prices: Price table from :func:`.gateway_dialects.load_prices` (default: none).
        max_output_tokens: Ceiling for ``max_tokens`` (Messages) / ``max_output_tokens`` (Responses).
        drain_timeout: Seconds to keep reading an upstream reply after the client hung up.
        anthropic_betas: ``anthropic-beta`` values allowed on the Messages route (default: none).
        chat_reserve_tokens: Output tokens reserved for a chat call that names no ``max_tokens``.
        dlp_filter: The DLP filter applied to every request (default: one that blocks the secret classes and
            the upstream keys and redacts the lesser classes; there is no way to turn it off).
        dlp_responses: Also scan each upstream reply for the block classes and count a hit in
            ``dlp_response_flags`` (the reply is relayed unchanged).
        responses_reserve_tokens: Output tokens reserved for a Responses call without ``max_output_tokens``
            (Codex sends none); 0 refuses such a call.
        chat_reasoning: The operator's ``reasoning`` object, written into every chat request
            (:func:`.gateway_dialects.reasoning_problem`); None: the client's own is forwarded.

    Returns:
        A ``BaseHTTPRequestHandler`` subclass.
    """
    allowed = frozenset(m for m in (model, *models) if m)
    if not allowed:
        raise ValueError("at least one allowed model is required")
    ups = dict(upstreams) if upstreams is not None else {d.CHAT: d.Upstream(upstream, key)}
    scanner = dlp_filter if dlp_filter is not None else dlp.Filter(secrets=[u.key for u in ups.values()])
    table = prices or {}
    served = ", ".join(f"POST {r}" for r, dialect in d.ROUTES.items() if dialect in ups)
    # D7: one upstream call at a time, held from the cap check through metering. Without it two
    # concurrent requests both pass the check and together overshoot the budget or the token cap.
    gate = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"   # responses end at connection close (simple streaming relay)

        def log_message(self, fmt: str, *args: object) -> None:  # no request/content logging
            return

        def _deny(self, code: int, msg: str, dialect: str = d.CHAT) -> None:
            err: dict[str, Any] = {"error": {"code": code, "message": msg}}
            if dialect == d.MESSAGES:   # Anthropic's error shape, so the client shows the message
                kind = "invalid_request_error" if code == 400 else "permission_error"
                err = {"type": "error", "error": {"type": kind, "code": code, "message": msg}}
            data = json.dumps(err).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path == "/healthz":
                self._deny(200, "ok")
            else:
                self._deny(403, f"only {served} is relayed")

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            # Claude Code posts to /v1/messages?beta=true (gateway protocol doc): that one query is accepted on the
            # Messages routes; any other query string leaves the path unmatched (403). Nothing of the client's
            # URL is forwarded; the upstream path is fixed per dialect.
            parts = urllib.parse.urlsplit(self.path)
            beta_query = parts.query == "beta=true" and (d.ROUTES.get(parts.path) == d.MESSAGES
                                                          or parts.path in d.COUNT_TOKENS_ROUTES)
            path = parts.path if beta_query else self.path
            if path in d.COUNT_TOKENS_ROUTES and d.MESSAGES in ups:
                self._deny(404, d.COUNT_TOKENS_REFUSAL, d.MESSAGES)
                return
            dialect = d.ROUTES.get(path)
            if dialect not in ups:
                self._deny(403, f"only {served} is relayed")
                return
            if self._refused(dialect):   # early answer: an exhausted run never waits on the gate
                return
            raw_length = (self.headers.get("Content-Length") or "0").strip()
            if not re.fullmatch(r"-?[0-9]{1,20}", raw_length):
                self._deny(400, "malformed Content-Length", dialect)
                return
            length = int(raw_length)
            if not 0 < length <= MAX_BODY:
                self._deny(413, "missing or oversized body", dialect)
                return
            try:
                checked = d.check_request(dialect, self.rfile.read(length), models=allowed,
                                          max_output_tokens=max_output_tokens, chat_reserve_tokens=chat_reserve_tokens,
                                          dlp=scanner.apply, responses_reserve_tokens=responses_reserve_tokens,
                                          chat_reasoning=chat_reasoning)
                headers = d.upstream_headers(dialect, self.headers, ups[dialect], anthropic_betas)
            except dlp.DlpBlocked as exc:   # the class name only: the matched text is never kept or echoed
                meter.refuse(f"{dialect}: dlp: {exc.cls}", dlp_class=exc.cls)
                self._deny(400, str(exc), dialect)
                return
            except d.PolicyError as exc:
                meter.refuse(f"{dialect}: {exc}")
                self._deny(400, str(exc), dialect)
                return
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                msg = "request nested too deeply" if isinstance(exc, RecursionError) else str(exc)
                self._deny(400, msg, dialect)
                return
            meter.dlp_redacted(checked.redactions)
            tokens, usd = d.reservation(table, checked) if checked.max_output else (0, 0.0)
            with gate:
                if self._refused(dialect):   # re-checked under the gate: the previous call is metered now
                    return
                why = meter.reserve(usd, tokens) if tokens else ""
                if why:
                    meter.refuse(f"{dialect}: {why}")
                    self._deny(402, why, dialect)
                    return
                try:
                    self._relay(dialect, checked, headers)
                finally:
                    if tokens:
                        meter.release(usd, tokens)

        def _refused(self, dialect: str) -> bool:
            """Answer 402 and count the refusal when a cap is used up; return whether it was."""
            why = meter.exhausted_reason()
            if why:
                meter.refuse(f"{dialect}: {why}")
                self._deny(402, why, dialect)
            return bool(why)

        def _relay(self, dialect: str, checked: d.Checked, headers: dict[str, str]) -> None:
            """Forward one validated body upstream, stream the reply back unbuffered and meter it."""
            up = urllib.parse.urlparse(ups[dialect].url)
            conn_cls = http.client.HTTPSConnection if up.scheme == "https" else http.client.HTTPConnection
            conn = conn_cls(up.hostname, up.port or (443 if up.scheme == "https" else 80), timeout=600)
            status: int | None = None
            seen = bytearray()
            try:
                conn.request("POST", up.path.rstrip("/") + d.UPSTREAM_SUFFIX[dialect], body=checked.body,
                             headers=headers)
                resp = conn.getresponse()
                status = resp.status
                try:
                    self.send_response(resp.status)
                    self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                    self.end_headers()
                    client_gone = False
                except OSError:   # the client hung up before the headers: drain and meter the billed reply anyway
                    client_gone = True
                self._stream(conn, resp, seen, client_gone=client_gone)
            except (OSError, http.client.HTTPException) as exc:
                if status is None:   # nothing came back (DNS, refused, timeout): an upstream error, nothing to meter
                    meter.add_error()
                    self._deny(502, f"upstream error: {type(exc).__name__}", dialect)
            finally:
                conn.close()
                # Meter whatever came back, also after a client hang-up (drained) or an upstream cut. A
                # reply cut short usually carries no usage and then counts as unmetered: fails closed.
                if status is not None and status >= 400:
                    meter.add_error()
                elif status is not None:
                    self._meter(dialect, checked.model, bytes(seen))
                if dlp_responses and status is not None:   # flag only: the reply was already relayed as is
                    hit = scanner.scan_response(bytes(seen[-MAX_BODY:]))
                    if hit:
                        meter.dlp_response(hit)

        def _stream(self, conn: Any, resp: Any, seen: bytearray, *, client_gone: bool = False) -> None:
            """Copy the reply to the client chunk by chunk; after a client hang-up, drain it (revision 9).

            ``client_gone``: the hang-up came before the headers could be sent; the whole reply is drained.
            """
            deadline: float | None = time.monotonic() + drain_timeout if client_gone else None
            while True:
                if deadline is not None:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        meter.drain(cut=True)
                        return
                    sock = getattr(conn, "sock", None)
                    if sock is not None:   # bounds a stalled read, not only the gaps between reads
                        sock.settimeout(left)
                chunk = resp.read1(65536)
                if not chunk:
                    break
                seen += chunk
                if deadline is None:
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except OSError:   # client hung up: keep reading upstream so the billed usage is metered
                        deadline = time.monotonic() + drain_timeout
            if deadline is not None:
                meter.drain(cut=False)

        def _meter(self, dialect: str, model: str, payload: bytes) -> None:
            """Meter one reply: upstream cost, else the price table, else unmetered (D3)."""
            usage, found = d.usage_for(dialect, payload)
            cost = usage.cost
            if cost is None and found:
                cost = d.priced(table, model, usage)
            used = bool(cost or usage.prompt_total or usage.output_tokens)
            # chat keeps its rule (a reply with tokens is metered); the new dialects also need a known spend
            unmetered = not used if dialect == d.CHAT else (cost is None or not used)
            meter.record(cost, usage.prompt_total, usage.output_tokens, unmetered=unmetered)

    return Handler


#: Variable names a key file may hold, per dialect, in order of preference (a bare key line also works).
KEY_NAMES = {d.CHAT: ("OPENROUTER_API_KEY",),
             d.MESSAGES: ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"),
             d.RESPONSES: ("OPENAI_API_KEY", "XAI_API_KEY", "OPENROUTER_API_KEY")}


def read_key(path: Path, names: tuple[str, ...]) -> str:
    """Return the API key in a key file: the first of ``names`` assigned in it, else a bare key line.

    Args:
        path: ``KEY=value`` lines (``export`` and quotes allowed, ``#`` comments ignored) or the bare key.
        names: Accepted variable names, most preferred first.

    Returns:
        The key, or ``""`` when the file holds none.

    Raises:
        OSError / UnicodeDecodeError: The file cannot be read.
    """
    found: dict[str, str] = {}
    bare = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line.startswith("#"):   # a comment is never the key
            continue
        name, eq, value = line.partition("=")
        if eq and name.strip() in names:
            found[name.strip()] = value.strip().strip("'\"")
        elif line and not eq and not bare:
            bare = line
    return next((found[n] for n in names if found.get(n)), bare)


def _csv(text: str) -> list[str]:
    return [x.strip() for x in text.split(",") if x.strip()]


def _http_url(url: str) -> bool:
    up = urllib.parse.urlparse(url)
    return up.scheme in ("http", "https") and bool(up.hostname)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m agentteams.vulnscan.gateway")
    p.add_argument("--listen", default="0.0.0.0:8080")
    p.add_argument("--model", default="", help="an allowed model id (exact match)")
    p.add_argument("--models", default="", help="further allowed model ids, comma-separated")
    p.add_argument("--dialects", default=d.CHAT, help="routes served, comma-separated: chat, messages, responses")
    p.add_argument("--key-file", type=Path, default=None,
                   help="chat upstream key file; also the default for the other upstreams")
    p.add_argument("--key-file-messages", type=Path, default=None)
    p.add_argument("--key-file-responses", type=Path, default=None)
    p.add_argument("--upstream", "--upstream-chat", dest="upstream", default="https://openrouter.ai")
    p.add_argument("--upstream-messages", default="", help="Anthropic Messages API base, e.g. .../v1")
    p.add_argument("--upstream-responses", default="", help="OpenAI Responses API base, e.g. .../v1")
    p.add_argument("--messages-auth", choices=("auto", "x-api-key", "bearer"), default="auto",
                   help="auto: bearer for openrouter.ai, else x-api-key")
    p.add_argument("--anthropic-betas", default="", help="anthropic-beta values allowed (default: none)")
    p.add_argument("--prices", type=Path, default=None, help="price table JSON {model: {input_per_mtok, ...}}")
    p.add_argument("--max-output-tokens", type=int, default=d.DEFAULT_MAX_OUTPUT_TOKENS,
                   help="ceiling for max_tokens / max_output_tokens on messages and responses")
    p.add_argument("--chat-reserve-tokens", type=int, default=d.DEFAULT_CHAT_RESERVE_TOKENS,
                   help="output tokens reserved for a chat call without max_tokens")
    p.add_argument("--responses-reserve-tokens", type=int, default=0,
                   help="output tokens reserved for a responses call without max_output_tokens (0: refuse it)")
    p.add_argument("--chat-reasoning", default="",
                   help='reasoning object written into every chat request, e.g. \'{"effort": "low"}\' (default: none)')
    p.add_argument("--drain-timeout", type=float, default=DEFAULT_DRAIN_TIMEOUT,
                   help="seconds to keep reading a reply after the client hung up")
    p.add_argument("--budget-usd", type=float, default=2.0)
    p.add_argument("--spend-file", type=Path, default=None)
    p.add_argument("--max-tokens-total", type=int, default=0, help="prompt + completion token cap (0: none)")
    p.add_argument("--max-unmetered-calls", type=int, default=20,
                   help="refuse after this many replies without usable usage/cost (>= 1)")
    p.add_argument("--allow-unmetered", action="store_true", help="never refuse for unmetered replies")
    p.add_argument("--peer-uid", type=int, default=None,
                   help="with --listen unix:PATH: the only uid admitted (default: the gateway's own)")
    p.add_argument("--dlp-secrets-file", type=Path, action="append", default=[],
                   help="env file whose secret values are blocked by exact match (repeatable; never logged)")
    p.add_argument("--dlp-secrets-dir", default=dlp.SECRETS_DIR,
                   help="directory whose *.env files are loaded like --dlp-secrets-file when present ('' : none)")
    p.add_argument("--dlp-secrets-mount", type=Path, action="append", default=[],
                   help="mounted secrets directory whose *.env files are all loaded (repeatable; R5)")
    p.add_argument("--dlp-skip-ok-file", type=Path, default=None,
                   help="acknowledged unreadable secrets files, one in-gateway path per line (agent.dlp_skip_ok)")
    p.add_argument("--dlp-hostname", action="append", default=[], help="a host name to redact (repeatable)")
    p.add_argument("--dlp-hostnames-file", type=Path, default=None,
                   help="host names to redact, one per line (the launcher's host context; kept out of argv)")
    p.add_argument("--dlp-private-names", default="", help="private package name patterns to redact (fnmatch, CSV)")
    p.add_argument("--dlp-private-names-file", type=Path, default=None, help="the same, one pattern per line")
    p.add_argument("--dlp-responses", action="store_true",
                   help="also scan upstream replies for secrets and count hits (dlp_response_flags; not altered)")
    return p


class DlpContextError(OSError):
    """A DLP secrets file could not be read: the gateway refuses to start (exit 2, @security rc.4 Low 5)."""


#: Why a mounted DLP file is usually unreadable, for the operator (the gateway's user follows the Docker mode, R4).
DLP_UNREADABLE_HINT = ("each mounted file must be readable by the gateway's user: uid 10001 on Docker Desktop, which "
                       "maps file ownership; your own uid on a rootful Linux Docker Engine, and uid 0 mapped to you "
                       "under rootless Docker, so a file you own and can read is readable; see 'Gateway DLP context' "
                       "and 'Docker modes' in docs_src/api-reference/vulnscan.md")


def _dlp_filter(a: argparse.Namespace, keys: Collection[str], key_files: Collection[Path]) -> dlp.Filter:
    """Build the DLP filter: the upstream keys, every secret value of the key files and the secrets files.

    Raises:
        DlpContextError: A secrets file cannot be read (fail closed).
        OSError / UnicodeDecodeError: The private-names or host-names file cannot be read (fail closed).
    """
    files = [*key_files, *a.dlp_secrets_file, *(dlp.secrets_dir_files(a.dlp_secrets_dir) if a.dlp_secrets_dir else [])]
    values, unreadable = dlp.load_secret_files(files)
    skip_ok = dlp.read_private_names(a.dlp_skip_ok_file) if a.dlp_skip_ok_file is not None else []
    mounted, bad_dirs, bad_files, _ = dlp.load_secret_mounts(a.dlp_secrets_mount, skip_ok)
    values |= mounted
    unreadable += bad_files
    if bad_dirs:   # R5: a mounted secrets directory the gateway cannot list hides every secret in it
        raise DlpContextError(f"{bad_dirs} DLP secrets director(ies) and {unreadable} file(s) could not be read")
    if unreadable:   # fail closed like the names files: a secret the filter cannot load is a secret it lets out
        raise DlpContextError(f"{unreadable} DLP secrets file(s) could not be read")
    names = _csv(a.dlp_private_names)
    if a.dlp_private_names_file is not None:
        names += dlp.read_private_names(a.dlp_private_names_file)
    hosts = [*dlp.default_hostnames(), *a.dlp_hostname]
    if a.dlp_hostnames_file is not None:
        hosts += dlp.read_private_names(a.dlp_hostnames_file)   # the same one-entry-per-line format
    return dlp.Filter(secrets={*keys, *values}, hostnames=hosts, private_names=names)


def _dlp_refused(spend_file: Path | None, why: str) -> int:
    """Record a DLP-context refusal next to the spend file (counts only; the launcher ends the run not-run, R5).

    Returns:
        2, the gateway's exit status.
    """
    if spend_file is not None:
        try:
            (spend_file.parent / dlp.REFUSED_NAME).write_text(why[:300] + "\n", encoding="utf-8")
        except OSError as exc:   # exit 2 still refuses: the launcher's network connect then fails
            print(f"gateway: cannot record the refusal: {type(exc).__name__}", file=sys.stderr)
    return 2


def _server(p: argparse.ArgumentParser, a: argparse.Namespace, handler: type, meter: Meter) -> Any:
    """Bind the listener: TCP ``HOST:PORT`` (default), or ``unix:PATH`` admitting one uid (:mod:`.gateway_peer`)."""
    if a.listen.startswith("unix:"):
        uid = os.getuid() if a.peer_uid is None else a.peer_uid

        def denied(peer: int | None) -> None:
            meter.refuse(f"unix socket: a connection from uid {peer if peer is not None else '(unknown)'} was "
                         f"refused (only uid {uid} is admitted)")
        try:
            return gateway_peer.UnixGatewayServer(Path(a.listen[5:]), handler, allowed_uid=uid, on_deny=denied)
        except ValueError as exc:
            p.error(f"--listen {a.listen}: {exc}")
    if a.peer_uid is not None:
        p.error("--peer-uid needs --listen unix:PATH")
    listen = re.fullmatch(r"(.*):([0-9]{1,5})", a.listen)
    if not listen or int(listen.group(2)) > 65535:
        p.error("--listen must be HOST:PORT or unix:PATH")
    return ThreadingHTTPServer((listen.group(1), int(listen.group(2))), handler)


def _validate(p: argparse.ArgumentParser, a: argparse.Namespace) -> tuple[list[str], dict[str, str]]:
    """Check the flags (``p.error`` exits 2); return the dialects and each one's upstream URL."""
    if a.max_unmetered_calls < 1:
        p.error("--max-unmetered-calls must be >= 1")
    if a.max_tokens_total < 0:   # X11: a negative cap used to become 0, i.e. no token cap at all
        p.error("--max-tokens-total must be >= 0 (0: no token cap)")
    if not (math.isfinite(a.budget_usd) and a.budget_usd >= 0):   # nan would never compare as exhausted
        p.error("--budget-usd must be a finite number >= 0")
    if a.max_output_tokens < 1:
        p.error("--max-output-tokens must be >= 1")
    if a.chat_reserve_tokens < 1:
        p.error("--chat-reserve-tokens must be >= 1")
    if not 0 <= a.responses_reserve_tokens <= a.max_output_tokens:
        p.error("--responses-reserve-tokens must be 0..--max-output-tokens")
    if not (math.isfinite(a.drain_timeout) and a.drain_timeout >= 0):
        p.error("--drain-timeout must be a finite number >= 0")
    if a.chat_reasoning:
        try:
            why = d.reasoning_problem(json.loads(a.chat_reasoning), a.max_output_tokens)
        except ValueError:
            why = "must be JSON"
        if why:
            p.error(f"--chat-reasoning {why}")
    if not (a.model or _csv(a.models)):
        p.error("give the allowed model(s): --model and/or --models")
    dialects = _csv(a.dialects)
    if not dialects or len(set(dialects)) != len(dialects) or set(dialects) - set(d.DIALECTS):
        p.error(f"--dialects takes distinct values from {', '.join(d.DIALECTS)}")
    urls = {d.CHAT: a.upstream, d.MESSAGES: a.upstream_messages, d.RESPONSES: a.upstream_responses}
    for dialect in (d.MESSAGES, d.RESPONSES):
        if urls[dialect] and dialect not in dialects:
            p.error(f"--upstream-{dialect} is set but {dialect} is not in --dialects")
    for dialect in dialects:
        if not _http_url(urls[dialect]):
            p.error(f"--upstream{'' if dialect == d.CHAT else '-' + dialect} must be an http(s) URL")
    return dialects, {k: urls[k] for k in dialects}


def main(argv: list[str] | None = None) -> int:
    """Run the gateway until killed.

    Args:
        argv: Arguments.

    Returns:
        Exit status (2: a bad flag, or a key file or price table that cannot be used).
    """
    p = _parser()
    a = p.parse_args(argv)
    listen = re.fullmatch(r"(.*):([0-9]{1,5})", a.listen)
    if not a.listen.startswith("unix:") and (not listen or int(listen.group(2)) > 65535):
        p.error("--listen must be HOST:PORT or unix:PATH")
    dialects, urls = _validate(p, a)
    key_files = {d.CHAT: a.key_file, d.MESSAGES: a.key_file_messages or a.key_file,
                 d.RESPONSES: a.key_file_responses or a.key_file}
    upstreams: dict[str, d.Upstream] = {}
    for dialect in dialects:
        if key_files[dialect] is None:
            p.error(f"the {dialect} upstream needs a key file (--key-file"
                    f"{'' if dialect == d.CHAT else ' or --key-file-' + dialect})")
        try:
            key = read_key(key_files[dialect], KEY_NAMES[dialect])
        except (OSError, UnicodeDecodeError) as exc:
            print(f"gateway: cannot read the {dialect} key file: {type(exc).__name__}", file=sys.stderr)
            return 2
        if not key:
            print(f"gateway: no {' or '.join(KEY_NAMES[dialect])} in the {dialect} key file", file=sys.stderr)
            return 2
        host = (urllib.parse.urlparse(urls[dialect]).hostname or "").lower()
        auth = "bearer"
        if dialect == d.MESSAGES:
            openrouter = host == "openrouter.ai" or host.endswith(".openrouter.ai")
            auth = a.messages_auth if a.messages_auth != "auto" else ("bearer" if openrouter else "x-api-key")
        upstreams[dialect] = d.Upstream(urls[dialect], key, auth)
    prices: dict[str, dict[str, float]] = {}
    if a.prices is not None:
        try:
            prices = d.load_prices(a.prices)
        except (OSError, ValueError) as exc:   # ValueError covers bad JSON and a bad entry
            print(f"gateway: unusable price table: {exc}", file=sys.stderr)
            return 2
    try:
        scanner = _dlp_filter(a, [u.key for u in upstreams.values()],
                              {key_files[k] for k in dialects if key_files[k] is not None})
    except DlpContextError as exc:
        print(f"gateway: {exc}; refusing to start ({DLP_UNREADABLE_HINT})", file=sys.stderr)
        return _dlp_refused(a.spend_file, str(exc))
    except (OSError, UnicodeDecodeError) as exc:
        print(f"gateway: cannot read a DLP names file (private names or host names): {type(exc).__name__}; "
              f"refusing to start ({DLP_UNREADABLE_HINT})", file=sys.stderr)
        return _dlp_refused(a.spend_file, f"a DLP names file could not be read ({type(exc).__name__})")
    meter = Meter(a.budget_usd, a.spend_file, max_tokens=a.max_tokens_total,
                  max_unmetered=a.max_unmetered_calls, allow_unmetered=a.allow_unmetered)
    handler = make_handler(model=a.model, models=_csv(a.models), meter=meter, upstreams=upstreams, prices=prices,
                           max_output_tokens=a.max_output_tokens, drain_timeout=a.drain_timeout,
                           anthropic_betas=frozenset(_csv(a.anthropic_betas)),
                           chat_reserve_tokens=a.chat_reserve_tokens, dlp_filter=scanner,
                           dlp_responses=a.dlp_responses, responses_reserve_tokens=a.responses_reserve_tokens,
                           chat_reasoning=json.loads(a.chat_reasoning) if a.chat_reasoning else None)
    server = _server(p, a, handler, meter)
    models = ",".join(m for m in (a.model, *_csv(a.models)) if m)
    routes = " ".join(f"{k}={v}" for k, v in urls.items())
    print(f"gateway: models={models} {routes} budget=${a.budget_usd:.2f} listening {a.listen} "
          f"dlp: {len(scanner.secrets)} exact secret(s), {len(scanner.hostnames)} host name(s), "
          f"{len(scanner.private_names)} private-name pattern(s)", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
