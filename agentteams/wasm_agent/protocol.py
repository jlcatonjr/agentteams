"""protocol.py — step-mode frames between the WASM agent host and its guest, and the Stage 0/1 control lines.

Step mode (METAPLAN §2.5): each guest invocation reads **one** event frame on stdin, writes **one** op frame on
stdout, and exits. The host owns the conversation; the guest only ever answers the event it was given. Frames are
closed and versioned: UTF-8 JSON, one object, LF-terminated; duplicate keys, non-finite numbers, NUL in strings,
nesting deeper than :data:`MAX_DEPTH`, unknown keys, unknown ops and a ``v`` other than :data:`PROTOCOL_VERSION`
are refused. Events are at most :data:`MAX_EVENT_BYTES`, ops :data:`MAX_OP_BYTES`, ``carry`` :data:`MAX_CARRY_BYTES`.

Everything a guest emits is data (C-4): :func:`decode_op` returns a typed op only when the frame matches its
closed shape and the agent's toolset and delegates; the host acts on nothing else.

The same strict JSON rules frame the Stage 0 <-> Stage 1 control pipe (:func:`read_line`, :func:`write_line`).

Stdlib only.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The one protocol version this host speaks. A future v2 adds a value to the event's ``accept`` list.
PROTOCOL_VERSION = 1
#: Host -> guest frame cap (bytes, including the LF).
MAX_EVENT_BYTES = 4 << 20
#: Guest -> host frame cap.
MAX_OP_BYTES = 1 << 20
#: Cap on the guest's opaque ``carry`` (canonical JSON bytes).
MAX_CARRY_BYTES = 256 << 10
#: Guest stderr kept as data in the transcript.
MAX_STDERR_KEPT = 64 << 10
#: Deepest accepted JSON nesting.
MAX_DEPTH = 32
#: Control-pipe line cap (Stage 0 <-> Stage 1). An event-sized record plus framing fits under it.
MAX_CONTROL_BYTES = 6 << 20

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$", re.ASCII)
_RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.ASCII)

#: Event kinds the host sends.
EVENT_KINDS = ("start", "model_reply", "tool_result", "delegate_result", "denied")
#: Ops a guest may answer with.
OPS = ("model", "tool", "delegate", "done")
#: ``denied`` event codes.
DENIED_CODES = ("budget", "not-granted", "dlp", "depth", "refused")
#: ``done`` reasons are free codes but must match this shape.
_REASON_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.ASCII)


class ProtocolError(Exception):
    """A frame that is not a valid v1 frame; ``code`` is a short stable token for the transcript."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Build the error.

        Args:
            code: Stable token (``frame``, ``json``, ``shape``, ``protocol-version``, ``unknown-id`` ...).
            detail: Human-readable detail (never guest text beyond a short excerpt).

        Raises:
            Nothing.
        """
        self.code = code
        self.detail = detail[:300]
        super().__init__(f"{code}: {self.detail}" if detail else code)


# --- strict JSON ---------------------------------------------------------------------------------------------------

def _no_dup(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ProtocolError("json", f"duplicate key {k[:40]!r}")
        out[k] = v
    return out


def _no_constant(name: str) -> Any:
    raise ProtocolError("json", f"non-finite number {name}")


def _check_tree(node: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ProtocolError("json", f"nesting deeper than {MAX_DEPTH}")
    if isinstance(node, str):
        if "\0" in node:
            raise ProtocolError("json", "NUL in a string")
    elif isinstance(node, dict):
        for k, v in node.items():
            if "\0" in k:
                raise ProtocolError("json", "NUL in a key")
            _check_tree(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _check_tree(v, depth + 1)
    elif isinstance(node, float) and not math.isfinite(node):
        raise ProtocolError("json", "non-finite number")


def strict_loads(data: bytes | str) -> Any:
    """Parse one JSON document under the frame rules (no duplicate keys, NaN, NUL or deep nesting).

    Args:
        data: UTF-8 bytes or text.

    Returns:
        The parsed value.

    Raises:
        ProtocolError: ``json`` for malformed JSON or a refused construct.
    """
    try:
        text = data.decode("utf-8", "strict") if isinstance(data, bytes) else data
        if text.startswith("﻿"):
            raise ProtocolError("json", "BOM")
        value = json.loads(text, object_pairs_hook=_no_dup, parse_constant=_no_constant)
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as exc:   # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise ProtocolError("json", f"{type(exc).__name__}: {str(exc)[:120]}") from exc
    _check_tree(value)
    return value


def canonical(value: Any) -> bytes:
    """Canonical JSON bytes (sorted keys, no spaces, ASCII escapes, no NaN): the hashing and MAC form.

    Args:
        value: A JSON-serialisable value.

    Returns:
        The bytes.

    Raises:
        ValueError: For a non-finite number; TypeError for a non-JSON value.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


# --- frames --------------------------------------------------------------------------------------------------------

def encode_event(event: Mapping[str, Any]) -> bytes:
    """Encode one host -> guest event frame (adds ``v``, ``type`` and ``accept``) and check its cap.

    Args:
        event: ``run_id``, ``step``, ``agent``, ``mounts``, ``limits``, ``carry`` and ``event`` (a dict whose
            ``kind`` is in :data:`EVENT_KINDS`).

    Returns:
        The LF-terminated frame bytes.

    Raises:
        ProtocolError: ``shape`` for a malformed event, ``frame`` when it exceeds :data:`MAX_EVENT_BYTES`.
    """
    want = {"run_id", "step", "agent", "mounts", "limits", "carry", "event"}
    if set(event) != want:
        raise ProtocolError("shape", f"event keys must be exactly {sorted(want)}")
    if not isinstance(event["run_id"], str) or not _RUN_ID_RE.match(event["run_id"]):
        raise ProtocolError("shape", "run_id must be 32 lowercase hex")
    body = event["event"]
    if not isinstance(body, Mapping) or body.get("kind") not in EVENT_KINDS:
        raise ProtocolError("shape", "event.kind is not a known kind")
    frame = {"v": PROTOCOL_VERSION, "type": "event", "accept": [PROTOCOL_VERSION], **event}
    try:
        data = canonical(frame) + b"\n"
    except (TypeError, ValueError) as exc:
        raise ProtocolError("shape", f"event is not JSON: {exc}") from exc
    if len(data) > MAX_EVENT_BYTES:
        raise ProtocolError("frame", f"event frame is {len(data)} bytes; the cap is {MAX_EVENT_BYTES}")
    return data


@dataclass(frozen=True)
class ToolResult:
    """One guest-reported result for a ``tool_use`` id (in-guest tools)."""

    tool_use_id: str
    ok: bool
    content: str


@dataclass(frozen=True)
class ModelOp:
    """Ask the host to call the model with these tool results (or a fixed continuation)."""

    tool_results: tuple[ToolResult, ...]
    cont: bool
    carry: Any


@dataclass(frozen=True)
class ToolOp:
    """Ask the host to run one host tool for a pending ``tool_use`` id."""

    id: str
    name: str
    input: Mapping[str, Any]
    carry: Any


@dataclass(frozen=True)
class DelegateOp:
    """Ask the host to delegate a task to another agent (P09)."""

    id: str
    agent: str
    task: str
    budget: Mapping[str, Any] | None
    carry: Any


@dataclass(frozen=True)
class DoneOp:
    """End the run; ``status`` is ``ok`` or ``failed``."""

    status: str
    reason: str


Op = ModelOp | ToolOp | DelegateOp | DoneOp

_OP_KEYS = {
    "model": ({"v", "op", "tool_results", "continue", "carry"}, set()),
    "tool": ({"v", "op", "id", "name", "input", "carry"}, set()),
    "delegate": ({"v", "op", "id", "agent", "task", "budget", "carry"}, set()),
    "done": ({"v", "op", "status"}, {"reason"}),
}


def _carry(value: Any) -> Any:
    try:
        size = len(canonical(value))
    except (TypeError, ValueError) as exc:
        raise ProtocolError("shape", "carry is not JSON") from exc
    if size > MAX_CARRY_BYTES:
        raise ProtocolError("frame", f"carry is {size} bytes; the cap is {MAX_CARRY_BYTES}")
    return value


def _id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise ProtocolError("shape", f"{what} must match [A-Za-z0-9_-]{{1,64}}")
    return value


def single_line(stdout: bytes, cap: int) -> bytes:
    """Return the one frame line of *stdout*: exactly one non-empty line, an optional trailing LF, within *cap*.

    Args:
        stdout: The raw bytes.
        cap: The byte cap.

    Returns:
        The line without its LF.

    Raises:
        ProtocolError: ``frame`` otherwise.
    """
    if len(stdout) > cap:
        raise ProtocolError("frame", f"frame is {len(stdout)} bytes; the cap is {cap}")
    line = stdout[:-1] if stdout.endswith(b"\n") else stdout
    if not line.strip() or b"\n" in line or b"\r" in line:
        raise ProtocolError("frame", "stdout must be exactly one non-empty line")
    return line


def decode_op(stdout: bytes, *, expect_ids: frozenset[str], toolset: frozenset[str],
              delegates: frozenset[str]) -> Op:
    """Decode and validate one guest op frame.

    Args:
        stdout: The guest's whole stdout.
        expect_ids: The ``tool_use`` ids of the last model reply a ``model`` op may answer (each at most once).
        toolset: The agent's tool vocabulary (a ``tool`` op's name must be in it).
        delegates: Agents this agent may delegate to.

    Returns:
        A :class:`ModelOp`, :class:`ToolOp`, :class:`DelegateOp` or :class:`DoneOp`.

    Raises:
        ProtocolError: ``frame`` / ``json`` / ``shape`` / ``protocol-version`` / ``unknown-id`` / ``duplicate-id``
            / ``not-granted``.
    """
    doc = strict_loads(single_line(stdout, MAX_OP_BYTES))
    if not isinstance(doc, dict):
        raise ProtocolError("shape", "an op frame is a JSON object")
    if doc.get("v") != PROTOCOL_VERSION or type(doc.get("v")) is not int:
        raise ProtocolError("protocol-version", f"op v={doc.get('v')!r}; this host accepts [{PROTOCOL_VERSION}]")
    op = doc.get("op")
    if op not in _OP_KEYS:
        raise ProtocolError("shape", f"unknown op {str(op)[:40]!r}")
    need, optional = _OP_KEYS[op]
    if not need <= set(doc) or set(doc) - need - optional:
        raise ProtocolError("shape", f"{op} op keys must be {sorted(need)} (+{sorted(optional)})")
    if op == "model":
        raw = doc["tool_results"]
        if not isinstance(raw, list) or not isinstance(doc["continue"], bool):
            raise ProtocolError("shape", "model op: tool_results is a list and continue a bool")
        results, seen = [], set()
        for item in raw:
            if not isinstance(item, dict) or set(item) != {"tool_use_id", "ok", "content"} or \
                    not isinstance(item["ok"], bool) or not isinstance(item["content"], str):
                raise ProtocolError("shape", "a tool result is {tool_use_id, ok: bool, content: str}")
            tid = _id(item["tool_use_id"], "tool_use_id")
            if tid not in expect_ids:
                raise ProtocolError("unknown-id", f"tool_use_id {tid!r} answers no pending tool use")
            if tid in seen:
                raise ProtocolError("duplicate-id", f"tool_use_id {tid!r} answered twice")
            seen.add(tid)
            results.append(ToolResult(tid, item["ok"], item["content"]))
        return ModelOp(tuple(results), doc["continue"], _carry(doc["carry"]))
    if op == "tool":
        name = doc["name"]
        if not isinstance(name, str) or name not in toolset:
            raise ProtocolError("not-granted", f"tool {str(name)[:40]!r} is not in this agent's toolset")
        if not isinstance(doc["input"], dict):
            raise ProtocolError("shape", "tool op input must be an object")
        return ToolOp(_id(doc["id"], "id"), name, doc["input"], _carry(doc["carry"]))
    if op == "delegate":
        agent = doc["agent"]
        if not isinstance(agent, str) or agent not in delegates:
            raise ProtocolError("not-granted", f"delegate target {str(agent)[:40]!r} is not allowed")
        if not isinstance(doc["task"], str) or not (doc["budget"] is None or isinstance(doc["budget"], dict)):
            raise ProtocolError("shape", "delegate op: task is a string, budget an object or null")
        return DelegateOp(_id(doc["id"], "id"), agent, doc["task"], doc["budget"], _carry(doc["carry"]))
    status, reason = doc["status"], doc.get("reason", "")
    if status not in ("ok", "failed") or not isinstance(reason, str) or (reason and not _REASON_RE.match(reason)):
        raise ProtocolError("shape", "done op: status ok|failed, reason a short code")
    return DoneOp(status, reason)


# --- control-pipe lines (Stage 0 <-> Stage 1) ----------------------------------------------------------------------

def write_line(fd: int, message: Mapping[str, Any]) -> None:
    """Write one control message as a canonical JSON line to *fd* (all bytes, or raise).

    Args:
        fd: A pipe write end.
        message: The message object.

    Returns:
        None.

    Raises:
        ProtocolError: ``frame`` when the line exceeds :data:`MAX_CONTROL_BYTES`.
        OSError: When the pipe is closed or the write fails.
    """
    data = canonical(dict(message)) + b"\n"
    if len(data) > MAX_CONTROL_BYTES:
        raise ProtocolError("frame", f"control line is {len(data)} bytes; the cap is {MAX_CONTROL_BYTES}")
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


class LineReader:
    """Reads capped JSON lines from a pipe fd (one buffer per fd, so no line is split across readers)."""

    def __init__(self, fd: int, cap: int = MAX_CONTROL_BYTES) -> None:
        """Bind the reader.

        Args:
            fd: The pipe read end.
            cap: Largest accepted line, in bytes.

        Raises:
            Nothing.
        """
        self.fd, self.cap, self._buf = fd, cap, bytearray()

    def read(self) -> dict[str, Any] | None:
        """Read the next message.

        Returns:
            The decoded object, or None at end of file (with no partial line pending).

        Raises:
            ProtocolError: ``frame`` for an oversize or truncated line, ``json`` / ``shape`` for a non-object.
            OSError: On a read failure.
        """
        while b"\n" not in self._buf:
            if len(self._buf) > self.cap:
                raise ProtocolError("frame", f"control line exceeds {self.cap} bytes")
            chunk = os.read(self.fd, 1 << 16)
            if not chunk:
                if self._buf:
                    raise ProtocolError("frame", "control pipe closed mid-line")
                return None
            self._buf += chunk
        line, _, rest = bytes(self._buf).partition(b"\n")
        self._buf = bytearray(rest)
        if len(line) > self.cap:
            raise ProtocolError("frame", f"control line exceeds {self.cap} bytes")
        doc = strict_loads(line)
        if not isinstance(doc, dict):
            raise ProtocolError("shape", "a control line is a JSON object")
        return doc


def read_line(fd: int, cap: int = MAX_CONTROL_BYTES) -> dict[str, Any] | None:
    """Read exactly one control line from *fd* byte-wise (for a one-shot pipe such as the key pipe).

    Args:
        fd: The pipe read end.
        cap: Largest accepted line.

    Returns:
        The decoded object, or None at end of file before any byte.

    Raises:
        ProtocolError: As :meth:`LineReader.read`.
        OSError: On a read failure.
    """
    return LineReader(fd, cap).read()


def json_list(value: Any, what: str) -> Sequence[Any]:
    """Return *value* when it is a list, else refuse (a small shape helper for control messages).

    Args:
        value: The value.
        what: Its name, for the error.

    Returns:
        The list.

    Raises:
        ProtocolError: ``shape`` when it is not a list.
    """
    if not isinstance(value, list):
        raise ProtocolError("shape", f"{what} must be a list")
    return value
