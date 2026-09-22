#!/usr/bin/env python3
"""Jev-1.13 decisions adapter — the identifiable pathway for using TypeSafe Jev from goose.

WHY THIS EXISTS
---------------
``typesafe/jev-1.13`` is a *decisions* model, not a chat model. It **rejects**
``/chat/completions`` with HTTP 400 and must be called at ``/api/alpha/decisions``
instead (see ``references/jev-openrouter-goose-access.handoff.md``). Goose's stock
OpenRouter provider only speaks chat/completions, so configuring Jev as an ordinary
goose OpenRouter model does not work. This module is the small custom adapter the
handoff calls for. It provides three surfaces:

1. ``JevDecisionsClient`` — an importable, stdlib-only client for the decisions API
   (``decide`` for the raw protocol, ``choose`` for the common single-choice case).
   Reused by ABM/decision-seam callers directly.
2. ``--serve`` — a local chat-completions **shim** that a goose *extension* can POST a
   structured-choice step to. A prompt is treated as a Jev decision **only** when it
   carries the opt-in sentinel ``[[JEV-CHOICE]]`` and enumerates options as
   ``N: description`` lines; it is then translated to a decisions call and returned as a
   chat-shaped reply. Any prompt without the sentinel (i.e. an ordinary goose turn) is
   refused with a clear 400 — never answered with a bare integer. **Every non-Jev model
   is passed straight through** to real OpenRouter, so the shim is safe to leave running.
3. ``--choose`` / ``--state`` CLI — a direct decisions call for testing.

HONEST SCOPE
------------
Jev is wired to the **decision seam**, never as goose's chat brain. It **cannot** be
goose's authoring model: a normal goose turn is a multi-message, tool-laden conversation,
and Jev only ever emits a single option key with no tool calls. Do **not** point
``GOOSE_MODEL`` at Jev globally — that would route every authoring turn to a model that
can only pick an integer. Instead, a deliberate caller (a goose extension, or the
``JevDecisionsClient`` import surface) routes only a *structured-choice* step here, marks
it with the ``[[JEV-CHOICE]]`` sentinel, and enumerates the options. The sentinel exists
precisely so an incidental numbered list in an authoring turn cannot be silently
mis-parsed as an option slate. Route only tool/option selection, classification, and
gating steps to Jev.

AUTH
----
Reads ``OPENROUTER_API_KEY`` from the environment. The key is never placed on argv and
never reaches ``ps``; it lives only in the HTTP ``Authorization`` header.

USAGE
-----
    # One structured decision from the shell:
    python3 scripts/jev-decisions-adapter.py --state "You may move to one cell." \
        --instructions "pick the option with the most sugar" \
        --option "0:sugar 5" --option "1:sugar 2" --option "2:sugar 9"      # -> 2

    # Decision-seam shim (leave running; non-Jev models pass through). A goose
    # extension POSTs a structured-choice step marked with the sentinel:
    python3 scripts/jev-decisions-adapter.py --serve --port 8792
    #   POST /api/v1/chat/completions {"model":"typesafe/jev-1.13","messages":[
    #     {"role":"user","content":"[[JEV-CHOICE]] pick the best\n0: ...\n1: ..."}]}
    # Do NOT set GOOSE_MODEL to Jev globally — it is a decision seam, not a chat brain.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

JEV_MODEL = "typesafe/jev-1.13"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_UPSTREAM = "https://openrouter.ai"
# The shim only treats a chat prompt as a decision when the caller opts in explicitly
# with this sentinel. Without it, a numbered list in an ordinary authoring turn would be
# silently mis-parsed as an option slate and answered with a bare integer. A deliberate
# caller (a goose extension routing a structured-choice step) emits the sentinel; every
# other turn is refused with a clear 400 rather than corrupted. The import surface
# (`JevDecisionsClient.choose`) passes options explicitly and needs no sentinel.
JEV_SENTINEL = "[[JEV-CHOICE]]"
_OPTION_LINE = re.compile(r"^\s*(\d+)\s*:\s*(.+?)\s*$")
# Hop-by-hop headers must not be relayed verbatim (mirrors the route proxy).
_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
})


@dataclass
class ChoiceResult:
    """Outcome of a single Jev choice.

    Attributes:
        index: The chosen option key, as a string (e.g. ``"2"``).
        confidence: Jev's scalar confidence in ``index`` (0..1), or ``None``.
        probabilities: Full distribution over option keys, or ``None``.
        cost: Billed cost of the call in USD (from ``usage.cost``), or ``0.0``.
        raw: The full decoded decisions response.
    """

    index: str
    confidence: float | None = None
    probabilities: dict[str, float] | None = None
    cost: float = 0.0
    raw: dict = field(default_factory=dict)


def parse_options(text: str) -> tuple[str, dict[str, str]]:
    """Split an encoded-choice prompt into (instructions, criteria-record).

    Every ``N: description`` line becomes ``criteria[str(N)] = description``; the
    remaining non-blank lines (excluding a literal ``description``/``index:`` header)
    form the instruction text. This is the same protocol baseAgent's
    ``JevDecisionsBackend`` emits, so prompts are interchangeable between the two.

    Args:
        text: The prompt or user message enumerating the options.

    Returns:
        A ``(instructions, criteria)`` tuple. ``criteria`` is empty when no
        ``N: description`` lines are present.
    """
    instructions: list[str] = []
    criteria: dict[str, str] = {}
    for line in text.splitlines():
        match = _OPTION_LINE.match(line)
        if match and match.group(2).lower() != "description":
            criteria[match.group(1)] = match.group(2)
        elif line.strip() and not line.lower().startswith("index:"):
            instructions.append(line.strip())
    return (" ".join(instructions) or "Choose the best option."), criteria


class JevDecisionsClient:
    """Minimal stdlib client for the Jev-1.13 decisions API.

    The API key is read once from ``OPENROUTER_API_KEY`` and sent only in the HTTP
    ``Authorization`` header — never on argv.

    Args:
        key: OpenRouter API key. Defaults to ``os.environ['OPENROUTER_API_KEY']``.
        model: Decisions model slug.
        timeout: Per-request timeout in seconds.

    Raises:
        RuntimeError: If no API key is available.
    """

    def __init__(self, key: str | None = None, model: str = JEV_MODEL,
                 timeout: float = 90.0) -> None:
        self.key = key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self.model = model
        self.timeout = timeout

    def decide(self, state: str, questions: dict) -> dict:
        """POST a full decisions request and return the decoded response.

        Args:
            state: Free-text context/observation the decision is about.
            questions: Map of question-name -> ``{type, instructions, criteria}``.

        Returns:
            The decoded decisions response (``answers``, ``usage``, ``model``, ...).

        Raises:
            RuntimeError: If the API returns an ``error`` field.
            urllib.error.URLError: On transport failure.
        """
        payload = json.dumps({
            "model": self.model, "state": state, "questions": questions,
        }).encode()
        req = urllib.request.Request(
            DECISIONS_URL, data=payload, method="POST",
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode())
        if "error" in data:
            raise RuntimeError(str(data["error"])[:200])
        return data

    def choose(self, state: str, instructions: str,
               options: dict[str, str], name: str = "choice") -> ChoiceResult:
        """Ask Jev to pick one option and return the typed result.

        Args:
            state: Context the decision is about.
            instructions: What to decide.
            options: Map of option-key -> description (the ``criteria`` record).
            name: Question name (arbitrary; echoed in the response).

        Returns:
            A :class:`ChoiceResult` with the chosen key, confidence, probabilities,
            and billed cost.

        Raises:
            ValueError: If ``options`` is empty.
        """
        if not options:
            raise ValueError("choose() requires at least one option")
        data = self.decide(state, {name: {
            "type": "choice", "instructions": instructions, "criteria": options,
        }})
        answer = (data.get("answers", {}) or {}).get(name, {}) or {}
        cost = (data.get("usage", {}) or {}).get("cost", 0.0) or 0.0
        return ChoiceResult(
            index=str(answer.get("choice", "")),
            confidence=answer.get("confidence"),
            probabilities=answer.get("probabilities"),
            cost=cost,
            raw=data,
        )


# --------------------------------------------------------------------------- shim


def _chat_reply(content: str, model: str, extra: dict | None = None) -> bytes:
    """Build a minimal OpenRouter chat-completions response body.

    Args:
        content: Assistant message content to return.
        model: Model id to echo back.
        extra: Optional fields to merge at the top level (e.g. ``usage``).

    Returns:
        The JSON-encoded response body.
    """
    body = {
        "id": f"jev-shim-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
    }
    if extra:
        body.update(extra)
    return json.dumps(body).encode()


def _extract_prompt(chat_body: dict) -> str:
    """Concatenate the user/system message text from a chat-completions body.

    Args:
        chat_body: The decoded chat-completions request.

    Returns:
        The last user message's text, or all message text joined, for option parsing.
    """
    messages = chat_body.get("messages") or []
    users = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if users:
        return users[-1] if isinstance(users[-1], str) else json.dumps(users[-1])
    return "\n".join(
        m.get("content", "") for m in messages if isinstance(m.get("content"), str)
    )


def make_shim_handler(upstream: str, client: JevDecisionsClient, verbose: bool):
    """Build the shim request handler bound to this adapter's config.

    Args:
        upstream: Base URL to relay non-Jev traffic to.
        client: A :class:`JevDecisionsClient` for Jev requests.
        verbose: Whether to log each Jev translation.

    Returns:
        A ``BaseHTTPRequestHandler`` subclass.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # silence per-request stderr noise
            pass

        def _send(self, status: int, payload: bytes,
                  content_type: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _passthrough(self, method: str, body: bytes) -> None:
            headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}
            headers["Content-Length"] = str(len(body))
            req = urllib.request.Request(
                upstream + self.path, data=body or None, headers=headers, method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=900) as resp:
                    self._send(resp.status, resp.read(),
                               resp.headers.get("Content-Type", "application/json"))
            except urllib.error.HTTPError as exc:
                self._send(exc.code, exc.read(),
                           exc.headers.get("Content-Type", "application/json"))
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                self._send(502, json.dumps(
                    {"error": {"message": f"jev-shim passthrough: {exc}"}}).encode())

        def _handle_jev(self, chat_body: dict) -> None:
            prompt = _extract_prompt(chat_body)
            if JEV_SENTINEL not in prompt:
                # Not an opted-in decision prompt. Refuse loudly instead of guessing:
                # a bare numbered list in an ordinary turn must never be answered with
                # an integer. This is the visibility signal the empty-options 400 alone
                # would miss (a numbered list parses to options but is not a decision).
                self._send(400, json.dumps({"error": {"message":
                    f"jev-shim: prompt is not a Jev decision (missing sentinel "
                    f"{JEV_SENTINEL!r}). Jev is a decisions model, not goose's chat "
                    "brain — a deliberate caller must mark structured-choice prompts "
                    "with the sentinel and enumerate options as 'N: description' "
                    "lines."}}).encode())
                return
            prompt = prompt.replace(JEV_SENTINEL, "").strip()
            instructions, options = parse_options(prompt)
            if not options:
                self._send(400, json.dumps({"error": {"message":
                    "jev-shim: sentinel present but no enumerated 'N: description' "
                    "options found."}}).encode())
                return
            try:
                result = client.choose(prompt, instructions, options)
            except Exception as exc:  # surfaced to goose as an error body, never a hang
                self._send(502, json.dumps(
                    {"error": {"message": f"jev-shim decide: {exc}"}}).encode())
                return
            if verbose:
                print(f"[jev-shim] {len(options)} options -> choice={result.index} "
                      f"conf={result.confidence} cost={result.cost}", flush=True)
            self._send(200, _chat_reply(
                result.index, JEV_MODEL,
                {"usage": {"total_cost": result.cost},
                 "jev": {"confidence": result.confidence,
                         "probabilities": result.probabilities}}))

        def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if self.path.endswith("/chat/completions") and body:
                try:
                    chat_body = json.loads(body)
                except json.JSONDecodeError:
                    chat_body = {}
                if chat_body.get("model") == JEV_MODEL:
                    self._handle_jev(chat_body)
                    return
            self._passthrough("POST", body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send(200, json.dumps(
                    {"service": "jev-decisions-adapter", "model": JEV_MODEL,
                     "upstream": upstream}).encode())
                return
            self._passthrough("GET", b"")

    return Handler


def _serve(port: int, upstream: str, verbose: bool) -> int:
    """Run the chat-completions shim. Returns a process exit code."""
    client = JevDecisionsClient()
    print(f"[jev-shim] 127.0.0.1:{port} -> {upstream}")
    print(f"[jev-shim] translating model=={JEV_MODEL} to {DECISIONS_URL}; "
          "all other models pass through")
    print(f"[jev-shim] set OPENROUTER_HOST: http://127.0.0.1:{port} and "
          f"GOOSE_MODEL: {JEV_MODEL} in config.yaml, then restart goose")
    handler = make_shim_handler(upstream, client, verbose)
    try:
        ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
    except KeyboardInterrupt:
        print("\n[jev-shim] stopped", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true",
                        help="run the goose chat-completions shim")
    parser.add_argument("--port", type=int, default=8792, help="shim listen port")
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM,
                        help="upstream base URL for passthrough traffic")
    parser.add_argument("--quiet", action="store_true", help="do not log translations")
    parser.add_argument("--state", help="context for a one-shot --choose call")
    parser.add_argument("--instructions", default="Choose the best option.",
                        help="what to decide for a one-shot --choose call")
    parser.add_argument("--option", action="append", default=[], metavar="KEY:DESC",
                        help="an option as KEY:DESC (repeatable)")
    parser.add_argument("--json", action="store_true",
                        help="print the full ChoiceResult as JSON")
    args = parser.parse_args(argv)

    if args.serve:
        return _serve(args.port, args.upstream, verbose=not args.quiet)

    if args.state is not None or args.option:
        options: dict[str, str] = {}
        for item in args.option:
            key, _, desc = item.partition(":")
            options[key.strip()] = desc.strip()
        client = JevDecisionsClient()
        result = client.choose(args.state or "", args.instructions, options)
        if args.json:
            print(json.dumps({
                "index": result.index, "confidence": result.confidence,
                "probabilities": result.probabilities, "cost": result.cost}))
        else:
            print(result.index)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
