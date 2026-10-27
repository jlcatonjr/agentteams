"""Per-dialect request policy, usage parsing and pricing for the egress gateway (:mod:`.gateway`).

Each dialect is passed through to an upstream that speaks the same dialect. Nothing is translated
between dialects: tool-calling translation is where agents silently break.

| Dialect | Routes served | Forwarded to |
|---|---|---|
| ``chat`` (OpenRouter Chat Completions) | ``/api/v1/chat/completions`` | ``<--upstream>/api/v1/chat/completions`` |
| ``messages`` (Anthropic Messages) | ``/v1/messages``, ``/api/v1/messages`` | ``<--upstream-messages>/messages`` |
| ``responses`` (OpenAI Responses) | ``/v1/responses``, ``/api/v1/responses`` | ``<--upstream-responses>/responses`` |

**Deny by default** on every route: a request is refused (not stripped) when it carries a body key, a tool
type, an ``anthropic-version`` or an ``anthropic-beta`` value outside the allowlists below, a server-side
tool, or content the upstream would fetch from a URL or a stored file. Client headers outside the allowlist
are not forwarded. The ``chat`` route first applies its original OpenRouter-only rewrites (:data:`STRIP_KEYS`
removed: ``plugins``/web search; ``usage: {include: true}`` forced; they never touch the other routes) plus one
deliberate non-OpenRouter exception, goose's ``session_id`` grouping label, stripped rather than refused, then the same deny-by-default checks (:data:`CHAT_KEYS`, ``function``
tools only, no OpenRouter ``models``/``route`` fallbacks, ``data:`` image URLs only). Every route reserves its
call before relaying: the output ceiling it names (chat: ``max_tokens`` / ``max_completion_tokens`` when
present, else :data:`DEFAULT_CHAT_RESERVE_TOKENS`; responses: ``max_output_tokens``, else the gateway's
``--responses-reserve-tokens`` when configured, since Codex sends none). A request that passes is then handed
to the DLP hook (``dlp``; :mod:`.gateway_dlp`), which refuses a secret and redacts the lesser classes in
message text.

**Usage** is parsed per dialect from a JSON reply or an SSE stream (:func:`usage_for`); spend is the
upstream's ``usage.cost`` when present, else priced from the operator's price table
(:func:`load_prices`), else unknown (the gateway counts the call as unmetered).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHAT, MESSAGES, RESPONSES = "chat", "messages", "responses"
DIALECTS = (CHAT, MESSAGES, RESPONSES)
ROUTE = "/api/v1/chat/completions"
ROUTES = {ROUTE: CHAT, "/v1/messages": MESSAGES, "/api/v1/messages": MESSAGES,
          "/v1/responses": RESPONSES, "/api/v1/responses": RESPONSES}
UPSTREAM_SUFFIX = {CHAT: ROUTE, MESSAGES: "/messages", RESPONSES: "/responses"}
COUNT_TOKENS_ROUTES = frozenset({"/v1/messages/count_tokens", "/api/v1/messages/count_tokens"})
COUNT_TOKENS_REFUSAL = ("count_tokens is not served by this gateway (refused by default; the client falls back "
                        "to its own estimate)")

#: Chat keys removed before the checks, never forwarded: OpenRouter-only features (unchanged chat behaviour) and
#: ``session_id``, the session-grouping label goose >= 1.54 sends (its value duplicates the allowed ``user``).
#: ``session_id`` is the one deliberate exception to "refused, never stripped": it is not an OpenRouter feature,
#: but stripping keeps it off the upstream, and refusing it stopped every goose run before its first model call.
#: Any other new client key is still refused (deny by default).
STRIP_KEYS = ("plugins", "web_search_options", "transforms", "session_id")

#: Request bytes per token for the input estimate of a reservation (deliberately low, so it over-estimates).
BYTES_PER_TOKEN = 3
DEFAULT_MAX_OUTPUT_TOKENS = 64_000
#: Output tokens reserved for a chat call that names no ``max_tokens`` (goose may omit it): an estimate, not a
#: bound, so the run's caps are still re-checked after metering.
DEFAULT_CHAT_RESERVE_TOKENS = 8_192

#: Chat body keys allowed after the OpenRouter-only features are stripped (deny by default, phase-3 parity):
#: what goose's OpenAI-format requests carry, plus benign sampling and routing fields.
CHAT_KEYS = frozenset({"model", "messages", "tools", "tool_choice", "parallel_tool_calls", "stream", "stream_options",
                       "max_tokens", "max_completion_tokens", "temperature", "top_p", "top_k", "min_p", "top_a",
                       "stop", "seed", "frequency_penalty", "presence_penalty", "repetition_penalty",
                       "response_format", "reasoning", "reasoning_effort", "include_reasoning", "usage", "provider",
                       "user", "verbosity", "logprobs", "top_logprobs"})
CHAT_TOOL_TYPES = frozenset({"function"})
CHAT_TOOL_CHOICE = frozenset({"auto", "none", "required"})
CHAT_SERVER_TOOLS = ("web_search", "openrouter:", "file_search", "code_interpreter", "mcp", "computer",
                     "image_generation")

# ---------------------------------------------------------------- allowlists (revision 8)

MESSAGES_KEYS = frozenset({"model", "messages", "system", "max_tokens", "metadata", "stop_sequences", "stream",
                           "temperature", "top_p", "top_k", "tools", "tool_choice", "thinking", "output_config"})
#: ``output_config`` fields allowed: effort (Claude Code sends it even with experimental betas off) and a
#: structured-output format. Both shape the reply only; anything else in it is refused.
MESSAGES_OUTPUT_CONFIG = frozenset({"effort", "format"})
MESSAGES_TOOL_TYPES = frozenset({"custom"})            # a tool without "type" is a custom tool too
MESSAGES_TOOL_CHOICE = frozenset({"auto", "any", "tool", "none"})
MESSAGES_SERVER_TOOLS = ("web_search", "web_fetch", "code_execution")
ANTHROPIC_VERSIONS = frozenset({"2023-06-01"})

RESPONSES_KEYS = frozenset({"model", "input", "instructions", "max_output_tokens", "tools", "tool_choice",
                            "parallel_tool_calls", "temperature", "top_p", "stream", "store", "background",
                            "reasoning", "text", "metadata", "truncation", "include", "prompt_cache_key",
                            "client_metadata"})
#: ``namespace`` groups function tools (Codex puts its MCP tools in one); ``tool_search`` is allowed only when
#: the client runs it (``execution: client``: Codex searching its own deferred MCP tools). Both are checked
#: recursively (:func:`_check_tools`), also where a tool list rides in an ``input`` item (``additional_tools``).
RESPONSES_TOOL_TYPES = frozenset({"function", "custom", "namespace", "tool_search"})
#: ``client_metadata`` (Codex: thread, turn and installation ids): a flat object of short strings only.
CLIENT_METADATA_MAX = (32, 4096)
RESPONSES_TOOL_CHOICE = frozenset({"auto", "none", "required"})
RESPONSES_SERVER_TOOLS = ("mcp", "web_search", "file_search", "code_interpreter", "computer_use",
                          "image_generation")
RESPONSES_INCLUDE = frozenset({"reasoning.encrypted_content"})

#: Body keys refused by name, with the reason (clearer than the generic "not in the allowlist").
NAMED_REFUSALS = {
    CHAT: {"models": "OpenRouter fallback models would bypass the gateway's model allowlist",
           "route": "OpenRouter fallback routing would bypass the gateway's model allowlist",
           "n": "several completions per call multiply the output beyond the reservation"},
    MESSAGES: {"mcp_servers": "remote MCP servers run on the provider, outside the sandbox",
               "container": "code-execution containers run on the provider, outside the sandbox"},
    RESPONSES: {"previous_response_id": "needs a stored response (store is forced false)",
                "conversation": "needs a stored conversation (store is forced false)",
                "prompt": "stored prompt templates are not allowed"},
}

#: Client headers forwarded per dialect (everything else, the dummy key included, is dropped).
FORWARD_HEADERS = {CHAT: ("Accept",), MESSAGES: ("Accept",), RESPONSES: ("Accept",)}


#: A client-supplied token (body key, tool type, header value) is echoed in a refusal reason only when it
#: matches this; anything else is shown as ``<redacted>``, so a refusal (kept in ``spend.json`` and printed by
#: the acceptance script) never carries client-chosen text.
SAFE_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def safe(value: object) -> str:
    """Return ``value`` for a refusal reason: ``repr`` of a :data:`SAFE_TOKEN` string, ``None``, else ``<redacted>``."""
    if value is None or (isinstance(value, str) and SAFE_TOKEN.fullmatch(value)):
        return repr(value)
    return "<redacted>"


class PolicyError(ValueError):
    """A request the gateway refuses by policy (deny by default); the handler counts it as refused."""


@dataclass(frozen=True)
class Upstream:
    """One dialect's upstream: base URL, key (never logged) and auth style (``bearer`` or ``x-api-key``)."""

    url: str
    key: str
    auth: str = "bearer"


@dataclass(frozen=True)
class Checked:
    """A validated request: the body to forward and what a reservation needs."""

    body: bytes
    model: str
    max_output: int = 0          # 0: no reservation
    input_estimate: int = 0
    redactions: Mapping[str, int] = field(default_factory=dict)   # DLP redact-and-flag counts per class


@dataclass(frozen=True)
class Usage:
    """One reply's usage, normalised across dialects. ``input_tokens`` excludes cache reads and writes."""

    cost: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @property
    def prompt_total(self) -> int:
        """All prompt-side tokens: uncached input plus cache reads and writes."""
        return self.input_tokens + self.cache_read + self.cache_write


def number(value: object) -> float | None:
    """Return a finite non-negative number from a usage field, or None (bools and strings are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value < float("inf") else None


def _json_or_none(text: str) -> object:
    """Decode JSON, or None for a malformed fragment (a partial stream line)."""
    try:
        return json.loads(text)
    except ValueError:
        return None


def _objects(payload: bytes, needle: str) -> list[dict[str, Any]]:
    """Return the JSON objects in a reply: the body itself, or each SSE ``data:`` line holding ``needle``."""
    text = payload.decode("utf-8", errors="replace")
    candidates = [text] if text.lstrip().startswith("{") else \
        [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
    out = []
    for c in candidates:
        if c and c != "[DONE]" and needle in c:
            obj = _json_or_none(c)
            if isinstance(obj, dict):
                out.append(obj)
    return out


# ---------------------------------------------------------------- chat (unchanged behaviour)

def _model_error(models: Collection[str]) -> str:
    ordered = sorted(models)
    if len(ordered) == 1:
        return f"only model {ordered[0]!r} is allowed through this gateway"
    return f"only models {ordered!r} are allowed through this gateway"


def rewrite_body(raw: bytes, model: str | Collection[str]) -> bytes:
    """Validate and normalise a chat-completions request body.

    Args:
        raw: Request body.
        model: The allowed model, or the allowed models (exact match).

    Returns:
        The body to forward.

    Raises:
        ValueError: Not JSON; :class:`PolicyError` for a model not allowed.
    """
    models = {model} if isinstance(model, str) else set(model)
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict) or body.get("model") not in models:
        raise PolicyError(_model_error(models))
    for k in STRIP_KEYS:
        body.pop(k, None)
    body["usage"] = {"include": True}
    return json.dumps(body).encode("utf-8")


def usage_from(payload: bytes) -> dict[str, Any] | None:
    """Extract the chat ``usage`` object from a JSON body or an SSE stream.

    Args:
        payload: Complete response bytes.

    Returns:
        The last ``usage`` seen, or None.
    """
    found = None
    for obj in _objects(payload, '"usage"'):
        if isinstance(obj.get("usage"), dict):
            found = obj["usage"]
    return found


# ---------------------------------------------------------------- messages / responses policy

def _remote_reference(node: object) -> str:
    """Return a description of the first content the upstream would fetch itself (URL, stored file), or ``""``.

    Walks the request iteratively (no recursion limit to hit). Refused: a ``source`` of type ``url`` or
    ``file`` (Messages), ``file_url``, ``file_id`` or a non-``data:`` ``image_url`` (Responses), and on chat
    a ``file`` part whose ``file_data`` (or ``url``) is not a ``data:`` URL, a non-``data:`` ``video_url`` or
    ``audio_url``, and an ``input_audio`` with a ``url`` or ``data`` that is a URL rather than base64.
    """
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
            continue
        if not isinstance(cur, dict):
            continue
        src = cur.get("source")
        if isinstance(src, dict) and src.get("type") in ("url", "file"):
            return f"a content source of type {src['type']!r}"
        for k in ("file_url", "file_id"):
            if k in cur:
                return f"a content part with {k!r}"
        for k in ("image_url", "video_url", "audio_url"):
            ref = cur.get(k)
            url = ref.get("url") if isinstance(ref, dict) else ref
            if k in cur and not (isinstance(url, str) and url.startswith("data:")):
                return f"an {k} that is not a data: URL"
        file = cur.get("file")
        if isinstance(file, dict):
            for k in ("file_data", "url"):
                if k in file and not (isinstance(file[k], str) and file[k].startswith("data:")):
                    return f"a file part whose {k!r} is not a data: URL"
        audio = cur.get("input_audio")
        if isinstance(audio, dict):
            data = audio.get("data")
            if "url" in audio or (isinstance(data, str) and ":" in data and not data.startswith("data:")):
                return "an input_audio that is a URL, not inline base64 data"
        stack.extend(cur.values())
    return ""


def _check_keys(dialect: str, body: dict[str, Any], allowed: frozenset[str]) -> None:
    for k in body:
        if k in NAMED_REFUSALS[dialect]:
            raise PolicyError(f"{safe(k)} is refused on the {dialect} route: {NAMED_REFUSALS[dialect][k]}")
        if k not in allowed:
            raise PolicyError(f"body key {safe(k)} is not allowed on the {dialect} route (deny by default)")


def _check_tools(dialect: str, tools: object, allowed: frozenset[str], server: tuple[str, ...]) -> None:
    if tools is None:
        return
    if not isinstance(tools, list):
        raise PolicyError("tools must be a list")
    for tool in tools:
        if not isinstance(tool, dict):
            raise PolicyError("each tool must be an object")
        kind = tool.get("type", "custom" if dialect == MESSAGES else None)
        if isinstance(kind, str) and kind.startswith(server):
            raise PolicyError(f"server-side tool {safe(kind)} is refused on the {dialect} route: it runs on the "
                              "provider, outside the sandbox")
        if kind not in allowed:
            raise PolicyError(f"tool type {safe(kind)} is not allowed on the {dialect} route (deny by default; "
                              f"allowed: {sorted(allowed)})")
        if kind == "tool_search" and tool.get("execution") != "client":
            raise PolicyError(f"tool_search with execution {safe(tool.get('execution'))} is refused on the {dialect} "
                              "route: only a search the client runs over its own tools is allowed")
        if kind == "namespace":                       # its tools: no further namespace, no tool_search
            _check_tools(dialect, tool.get("tools"), allowed - {"namespace", "tool_search"}, server)


def _check_tool_choice(dialect: str, choice: object, allowed: frozenset[str], tool_types: frozenset[str]) -> None:
    if choice is None:
        return
    kind = choice.get("type") if isinstance(choice, dict) else choice
    if isinstance(choice, str) and dialect == MESSAGES:
        kind = None                                   # Messages takes an object only
    if kind not in allowed and not (dialect != MESSAGES and isinstance(choice, dict) and kind in tool_types):
        raise PolicyError(f"tool_choice {safe(kind)} is not allowed on the {dialect} route")


def _max_output(body: dict[str, Any], key: str, ceiling: int) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(f"{key} is required (an integer) so the call can be reserved against the budget")
    if not 1 <= value <= ceiling:
        raise PolicyError(f"{key} must be between 1 and {ceiling} (the gateway's output ceiling)")
    return value


#: The gateway's DLP hook (:meth:`.gateway_dlp.Filter.apply`): scans a validated body in place, raises a
#: :class:`PolicyError` on a secret, returns the redactions per class.
Dlp = Callable[[str, dict[str, Any]], Mapping[str, int]]


def _check_chat(raw: bytes, models: Collection[str], max_output_tokens: int, reserve: int,
                dlp: Dlp | None = None) -> Checked:
    """The chat route: the OpenRouter rewrites (:func:`rewrite_body`), then deny by default and a reservation.

    Refused: a body key outside :data:`CHAT_KEYS` (``models``/``route`` fallbacks by name), a tool that is not a
    ``function`` (server-side tools by name), a ``tool_choice`` outside the allowlist, an ``image_url`` that is
    not a ``data:`` URL or another remote reference in ``messages``, and a ``max_tokens`` /
    ``max_completion_tokens`` outside ``1..max_output_tokens``. Reserved: that value, else ``reserve``.
    """
    body = json.loads(rewrite_body(raw, models))
    _check_keys(CHAT, body, CHAT_KEYS)
    _check_tools(CHAT, body.get("tools"), CHAT_TOOL_TYPES, CHAT_SERVER_TOOLS)
    _check_tool_choice(CHAT, body.get("tool_choice"), CHAT_TOOL_CHOICE, CHAT_TOOL_TYPES)
    where = _remote_reference(body.get("messages"))
    if where:
        raise PolicyError(f"{where} is refused on the chat route: the upstream would fetch it itself")
    named = [k for k in ("max_tokens", "max_completion_tokens") if k in body]
    max_out = max(_max_output(body, k, max_output_tokens) for k in named) if named else reserve
    redactions = dlp(CHAT, body) if dlp else {}
    return Checked(json.dumps(body).encode("utf-8"), body["model"], max_out, math.ceil(len(raw) / BYTES_PER_TOKEN),
                   redactions)


def check_request(dialect: str, raw: bytes, *, models: Collection[str],
                  max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                  chat_reserve_tokens: int = DEFAULT_CHAT_RESERVE_TOKENS, dlp: Dlp | None = None,
                  responses_reserve_tokens: int = 0) -> Checked:
    """Validate one request body for its dialect (deny by default) and return what to forward.

    Args:
        dialect: ``chat``, ``messages`` or ``responses``.
        raw: Request body.
        models: Allowed model ids (exact match).
        max_output_tokens: Ceiling for ``max_tokens`` / ``max_output_tokens`` (chat: also
            ``max_completion_tokens``).
        chat_reserve_tokens: Output tokens reserved for a chat call that names no output ceiling.
        dlp: The DLP hook, run on the body once the policy passed (blocks secrets, redacts the rest).
        responses_reserve_tokens: Output tokens reserved for a Responses call without ``max_output_tokens``,
            and written into the forwarded body as its ``max_output_tokens`` (0: such a call is refused).

    Returns:
        The body to forward, its model, the reservation inputs and the DLP redaction counts.

    Raises:
        PolicyError: A refusal by policy. ValueError / UnicodeDecodeError: not a JSON object.
    """
    if dialect == CHAT:
        return _check_chat(raw, models, max_output_tokens, chat_reserve_tokens, dlp)
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict) or body.get("model") not in models:
        raise PolicyError(_model_error(models))
    if dialect == MESSAGES:
        _check_keys(MESSAGES, body, MESSAGES_KEYS)
        _check_tools(MESSAGES, body.get("tools"), MESSAGES_TOOL_TYPES, MESSAGES_SERVER_TOOLS)
        _check_tool_choice(MESSAGES, body.get("tool_choice"), MESSAGES_TOOL_CHOICE, MESSAGES_TOOL_TYPES)
        oc = body.get("output_config", {})
        if not isinstance(oc, dict) or set(oc) - MESSAGES_OUTPUT_CONFIG:
            raise PolicyError(f"output_config may hold only {sorted(MESSAGES_OUTPUT_CONFIG)} on the messages route")
        max_out = _max_output(body, "max_tokens", max_output_tokens)
        where = _remote_reference([body.get("messages"), body.get("system")])
    elif dialect == RESPONSES:
        if body.get("background", False) is not False:
            raise PolicyError("'background' is refused on the responses route: background runs are stored "
                              "and continue outside the gateway's metering")
        if body.get("store", False) is not False:
            raise PolicyError("'store' must be false on the responses route (nothing is kept upstream)")
        _check_keys(RESPONSES, body, RESPONSES_KEYS)
        _check_tools(RESPONSES, body.get("tools"), RESPONSES_TOOL_TYPES, RESPONSES_SERVER_TOOLS)
        _check_tool_choice(RESPONSES, body.get("tool_choice"), RESPONSES_TOOL_CHOICE, RESPONSES_TOOL_TYPES)
        include = body.get("include", [])
        if not isinstance(include, list) or any(i not in RESPONSES_INCLUDE for i in include):
            raise PolicyError(f"include may hold only {sorted(RESPONSES_INCLUDE)}")
        _check_client_metadata(body.get("client_metadata", {}))
        for item in body.get("input") if isinstance(body.get("input"), list) else []:
            if isinstance(item, dict) and "tools" in item:   # tools carried in the input (Codex additional_tools)
                _check_tools(RESPONSES, item["tools"], RESPONSES_TOOL_TYPES, RESPONSES_SERVER_TOOLS)
        unnamed = "max_output_tokens" not in body and responses_reserve_tokens > 0
        max_out = responses_reserve_tokens if unnamed else _max_output(body, "max_output_tokens", max_output_tokens)
        if unnamed:   # enforced, not just reserved: the upstream may not answer with more (@security rc.4 Low 4)
            body["max_output_tokens"] = responses_reserve_tokens
        where = _remote_reference([body.get("input"), body.get("instructions")])
        body["store"] = False                          # the API defaults to true: forced off
    else:
        raise PolicyError(f"unknown dialect {dialect!r}")
    if where:
        raise PolicyError(f"{where} is refused on the {dialect} route: the upstream would fetch it itself")
    redactions = dlp(dialect, body) if dlp else {}
    return Checked(json.dumps(body).encode("utf-8"), body["model"], max_out, math.ceil(len(raw) / BYTES_PER_TOKEN),
                   redactions)


def _check_client_metadata(meta: object) -> None:
    """Refuse a ``client_metadata`` that is not a small flat object of short strings."""
    count, size = CLIENT_METADATA_MAX
    if not isinstance(meta, dict) or len(meta) > count or not all(
            isinstance(k, str) and SAFE_TOKEN.fullmatch(k) and isinstance(v, str) and len(v) <= size
            for k, v in meta.items()):
        raise PolicyError(f"client_metadata must be an object of at most {count} short strings on the responses route")


def upstream_headers(dialect: str, client: Mapping[str, str], up: Upstream,
                     anthropic_betas: Collection[str] = ()) -> dict[str, str]:
    """Build the headers sent upstream: the dialect's auth, plus the allowlisted client headers.

    Args:
        dialect: The route's dialect.
        client: The client's request headers (the container's dummy key is never forwarded).
        up: The upstream (its key goes into the dialect's auth header).
        anthropic_betas: ``anthropic-beta`` values the operator allows (default none).

    Returns:
        Header dict for the upstream request.

    Raises:
        PolicyError: An ``anthropic-version`` or ``anthropic-beta`` value outside the allowlist.
    """
    headers = {"Content-Type": "application/json"}
    for name in FORWARD_HEADERS[dialect]:
        headers[name] = client.get(name) or "application/json"
    if up.auth == "x-api-key":
        headers["x-api-key"] = up.key
    else:
        headers["Authorization"] = f"Bearer {up.key}"
    if dialect == MESSAGES:
        version = (client.get("anthropic-version") or "2023-06-01").strip()
        if version not in ANTHROPIC_VERSIONS:
            raise PolicyError(f"anthropic-version {safe(version)} is not allowed (allowed: {sorted(ANTHROPIC_VERSIONS)})")
        headers["anthropic-version"] = version
        betas = [b.strip() for b in (client.get("anthropic-beta") or "").split(",") if b.strip()]
        refused = [b for b in betas if b not in anthropic_betas]
        if refused:
            raise PolicyError(f"anthropic-beta [{', '.join(map(safe, refused))}] not allowed (deny by default; the operator allows "
                              f"betas with --anthropic-betas)")
        if betas:
            headers["anthropic-beta"] = ",".join(betas)
    return headers


# ---------------------------------------------------------------- usage per dialect (revision 9)

def _int(usage: Mapping[str, Any], key: str) -> int | None:
    n = number(usage.get(key))
    return int(n) if n is not None else None


def _messages_usage(payload: bytes) -> tuple[Usage, bool]:
    fields: dict[str, int] = {}
    cost: float | None = None
    found = False
    for obj in _objects(payload, '"usage"'):
        if obj.get("type") == "message_start" and isinstance(obj.get("message"), dict):
            usage = obj["message"].get("usage")
        else:                                          # message_delta, or a whole non-streamed message
            usage = obj.get("usage")
        if not isinstance(usage, dict):
            continue
        found = True
        for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            value = _int(usage, key)
            if value is not None:                     # later events carry cumulative counts
                fields[key] = value
        cost = number(usage.get("cost")) if "cost" in usage else cost
    return Usage(cost, fields.get("input_tokens", 0), fields.get("output_tokens", 0),
                 fields.get("cache_read_input_tokens", 0), fields.get("cache_creation_input_tokens", 0)), found


_RESPONSES_DONE = ("response.completed", "response.incomplete", "response.failed")


def _responses_usage(payload: bytes) -> tuple[Usage, bool]:
    usage = None
    for obj in _objects(payload, '"usage"'):
        if obj.get("type") in _RESPONSES_DONE and isinstance(obj.get("response"), dict):
            usage = obj["response"].get("usage")
        elif obj.get("object") == "response":          # a non-streamed response
            usage = obj.get("usage")
    if not isinstance(usage, dict):
        return Usage(), False
    total, out = _int(usage, "input_tokens") or 0, _int(usage, "output_tokens") or 0
    details = usage.get("input_tokens_details")
    cached = min(total, (_int(details, "cached_tokens") or 0) if isinstance(details, dict) else 0)
    return Usage(number(usage.get("cost")), total - cached, out, cached, 0), True


def usage_for(dialect: str, payload: bytes) -> tuple[Usage, bool]:
    """Return one reply's normalised usage and whether a usage object was found.

    Args:
        dialect: The route's dialect.
        payload: The complete reply (JSON body or SSE stream).

    Returns:
        ``(usage, found)``. Messages: ``message_start.message.usage`` (input and cache tokens) and
        ``message_delta.usage.output_tokens``; Responses: ``response.usage`` from ``response.completed``
        (or ``.incomplete`` / ``.failed``); chat: the last ``usage`` object.
    """
    if dialect == MESSAGES:
        return _messages_usage(payload)
    if dialect == RESPONSES:
        return _responses_usage(payload)
    usage = usage_from(payload)
    if usage is None:
        return Usage(), False
    prompt, completion = number(usage.get("prompt_tokens")), number(usage.get("completion_tokens"))
    return Usage(number(usage.get("cost")), int(prompt or 0), int(completion or 0)), True


# ---------------------------------------------------------------- price table (D3)

PRICE_FIELDS = ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "cache_write_per_mtok")


def load_prices(path: Path) -> dict[str, dict[str, float]]:
    """Read the operator's price table: ``{model: {input_per_mtok, output_per_mtok}}`` in USD per million tokens.

    Optional per model: ``cache_read_per_mtok`` (default: the input price) and ``cache_write_per_mtok``
    (default: twice the input price, the dearest provider cache write). Unknown fields are refused.

    Args:
        path: JSON file.

    Returns:
        The table with every field filled in.

    Raises:
        ValueError: Not a JSON object of models to finite non-negative prices.
    """
    table = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(table, dict):
        raise ValueError("the price table must be a JSON object of model -> prices")  # noqa: TRY004 - one error type for callers
    out: dict[str, dict[str, float]] = {}
    for model, entry in table.items():
        if not isinstance(entry, dict) or set(entry) - set(PRICE_FIELDS) or \
                not {"input_per_mtok", "output_per_mtok"} <= set(entry):
            raise ValueError(f"price entry for {model!r} needs input_per_mtok and output_per_mtok "
                             f"(optional: cache_read_per_mtok, cache_write_per_mtok) and nothing else")
        values = {k: number(v) for k, v in entry.items()}
        if any(v is None for v in values.values()):
            raise ValueError(f"price entry for {model!r} holds a value that is not a finite number >= 0")
        values.setdefault("cache_read_per_mtok", values["input_per_mtok"])
        values.setdefault("cache_write_per_mtok", 2 * values["input_per_mtok"])
        out[model] = {k: float(v) for k, v in values.items() if v is not None}
    return out


def priced(prices: Mapping[str, Mapping[str, float]], model: str, usage: Usage) -> float | None:
    """Return the USD cost of ``usage`` from the price table, or None when the model has no entry."""
    p = prices.get(model)
    if p is None:
        return None
    return (usage.input_tokens * p["input_per_mtok"] + usage.output_tokens * p["output_per_mtok"]
            + usage.cache_read * p["cache_read_per_mtok"] + usage.cache_write * p["cache_write_per_mtok"]) / 1e6


def reservation(prices: Mapping[str, Mapping[str, float]], checked: Checked) -> tuple[int, float]:
    """Return ``(tokens, usd)`` to reserve before relaying a call (D3).

    Tokens: the input estimate (request bytes / :data:`BYTES_PER_TOKEN`) plus the requested output
    ceiling. USD: those tokens at the model's price table entry, the input at the dearer of the
    input and cache-write prices; 0 when the model has no entry (only the token reservation holds).
    """
    p = prices.get(checked.model)
    usd = 0.0 if p is None else (checked.input_estimate * max(p["input_per_mtok"], p["cache_write_per_mtok"])
                                 + checked.max_output * p["output_per_mtok"]) / 1e6
    return checked.input_estimate + checked.max_output, usd
