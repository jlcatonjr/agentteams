"""wasm_agent_guest.py — the agent loop that runs INSIDE an agent's WASM guest (WASM P07, ARCH-B).

Run by the pinned CPython-WASI interpreter as ``python -I -S -B /src/main.py`` with the stdlib at ``/lib``. It is
package data, never imported by agentteams: the host loads it through ``agentteams.wasm_agent.guest_source``, which
re-hashes it against its pin on every load.

Step mode (METAPLAN §2.5, protocol v1 in ``agentteams/wasm_agent/protocol.py``): read **one** event frame on stdin,
write **one** op frame on stdout, exit. The host owns the conversation, the system prompt, the task and the provider
key; this program sees only model replies and host-tool outcomes, and decides the agent's next move:

* ``start``                  -> ``model`` (let the host make the first call).
* ``model_reply`` (no tools) -> ``done`` (``end_turn`` / ``other``), a fixed ``continue`` after ``max_tokens`` (twice
  at most), else ``done failed``.
* ``model_reply`` (tools)    -> run the in-guest tools here, in the order the model asked; ask the host for each
  host tool (``run_code`` runs in a FRESH sandbox the host prepares, never in this guest; ``delegate`` is P09) one
  op at a time, keeping the rest in the opaque ``carry``; then ``model`` with every result.

In-guest tools (``read_file``, ``list_dir``, ``search_files``, ``write_file``) accept only ``/project/<rel>``
(read-only) and ``/scratch`` (read-write) paths that the event's ``mounts`` list; ``/src`` and ``/lib`` are refused.
That check is soft (clear errors); the hard boundary is the WASI preopen set plus the host's read-only enforcement.
``write_file`` refuses instruction-file names (R8). There is no network tool and no process tool.

Everything this program reads is data (C-4): model text, tool inputs and file contents are passed along or
answered, never interpreted as instructions; no input can change this program's policy. It holds no key and no
network capability. Only stdlib modules in ``ALLOWED_IMPORTS`` are used (a static test enforces it), and the syntax
stays valid from CPython 3.11.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from typing import Any

PROTOCOL_VERSION = 1
MAX_EVENT_BYTES = 4 << 20
MAX_OP_BYTES = 1 << 20
MAX_CARRY_BYTES = 256 << 10
MAX_DEPTH = 32
#: Bytes kept free in an op or carry for the frame around the tool results.
FRAME_SLACK = 16 << 10
#: Imports this file may use (enforced by tests/test_wasm_agent_guest_static.py).
ALLOWED_IMPORTS = ("__future__", "json", "os", "stat", "sys", "typing")
#: Must equal ``agentteams.wasm_agent.tools.TOOL_SPEC_SHA256`` (static parity test): host and guest agree on schemas.
TOOL_SPEC_SHA256 = "05a120ef2679e19bfbd9cc75a1067a03b296c698cc7d2e9e0e3b72d6af3060b9"

IN_GUEST_TOOLS = ("read_file", "list_dir", "search_files", "write_file")
HOST_TOOLS = ("run_code", "delegate")
#: Closed input shapes: tool -> (required keys, optional keys); every value is a string (mirrors the host schemas).
TOOL_INPUTS = {
    "read_file": (("path",), ()),
    "list_dir": (("path",), ()),
    "search_files": (("path", "pattern"), ()),
    "write_file": (("path", "content"), ()),
}
#: R8: names a guest may not write (mirror of ``wasm_exec.REFUSED_COPY_OUT_NAMES`` plus ``*.agent.md``).
INSTRUCTION_FILE_NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules")
INSTRUCTION_FILE_SUFFIX = ".agent.md"

MAX_CONTINUES = 2
MAX_ID_CHARS = 64
MAX_PATH_CHARS = 4096
MAX_WRITE_BYTES = 4 << 20
MAX_LIST_ENTRIES = 1000
MAX_PATTERN_CHARS = 512
SEARCH_FILE_BYTES = 1 << 20
SEARCH_TOTAL_BYTES = 64 << 20
SEARCH_MAX_MATCHES = 500
SEARCH_MAX_ENTRIES = 20000
SEARCH_LINE_CHARS = 400
#: Upper bounds on the host-sent limits (a malformed event cannot make the guest emit an oversize frame).
LIMIT_CAPS = {"max_tool_output_bytes": 512 << 10, "max_read_bytes": 4 << 20}
_EVENT_KEYS = frozenset(("v", "type", "accept", "run_id", "step", "agent", "mounts", "limits", "carry", "event"))
#: 0 where the platform lacks it; read_file/write_file then rely on their lstat check (R-2: fail closed).
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
#: Failures of this program itself that :func:`handle` turns into ``done failed guest-error-*`` (named, not broad).
_INTERNAL_ERRORS = (ArithmeticError, AttributeError, LookupError, MemoryError, OSError, RecursionError, TypeError,
                    ValueError)


class BadEvent(Exception):
    """The event frame is not a well-formed v1 event (answered with ``done failed``)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {detail}")


class ToolError(Exception):
    """An in-guest tool refused or failed; its message becomes an ``ok: false`` result for the model."""


def _fs(path: str) -> str:
    """Map a checked guest path to the path handed to the OS (identity in the guest; tests remap it natively)."""
    return path


# --- frames ----------------------------------------------------------------------------------------------------------

def _no_dup(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out = {}
    for key, value in pairs:
        if key in out:
            raise BadEvent("bad-event", "duplicate key")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise BadEvent("bad-event", "non-finite number")


def _check_tree(node: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise BadEvent("bad-event", "nesting too deep")
    if isinstance(node, str):
        if "\0" in node:
            raise BadEvent("bad-event", "NUL in a string")
    elif isinstance(node, dict):
        for key, value in node.items():
            _check_tree(key, depth + 1)
            _check_tree(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _check_tree(value, depth + 1)


def read_event(raw: bytes) -> dict[str, Any]:
    """Parse one event frame under the protocol's strict JSON rules and check its envelope.

    Args:
        raw: Everything read from stdin (at most one byte over :data:`MAX_EVENT_BYTES`).

    Returns:
        The frame object.

    Raises:
        BadEvent: ``protocol-version`` for another ``v`` / ``accept``; ``bad-event`` for anything malformed.
    """
    if len(raw) > MAX_EVENT_BYTES:
        raise BadEvent("bad-event", "event over the size cap")
    line = raw[:-1] if raw.endswith(b"\n") else raw
    if not line.strip() or b"\n" in line or b"\r" in line:
        raise BadEvent("bad-event", "stdin must hold exactly one line")
    try:
        frame = json.loads(line.decode("utf-8", "strict"), object_pairs_hook=_no_dup, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        raise BadEvent("bad-event", type(exc).__name__) from None
    _check_tree(frame)
    if not isinstance(frame, dict):
        raise BadEvent("bad-event", "not an object")
    accept = frame.get("accept")
    if type(frame.get("v")) is not int or frame["v"] != PROTOCOL_VERSION or not isinstance(accept, list) \
            or PROTOCOL_VERSION not in accept:
        raise BadEvent("protocol-version", "this guest speaks v1 only")
    if set(frame) != _EVENT_KEYS or frame.get("type") != "event":
        raise BadEvent("bad-event", "event keys")
    for key in ("agent", "limits", "event"):
        if not isinstance(frame[key], dict):
            raise BadEvent("bad-event", f"{key} is not an object")
    if not isinstance(frame["mounts"], list) or not isinstance(frame["agent"].get("tools"), list):
        raise BadEvent("bad-event", "mounts / agent.tools")
    return frame


def encode_op(op: dict[str, Any]) -> str:
    """The op as one compact ASCII line (``ensure_ascii`` keeps UTF-8 ambiguity out of the host parser).

    Args:
        op: The op object.

    Returns:
        The line, LF-terminated.

    Raises:
        ValueError: A non-finite number (never produced by this program).
    """
    return json.dumps(op, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"


def _json_len(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=True))


def clip(text: str, cap: int) -> str:
    """Clip *text* so its JSON-encoded form is at most *cap* bytes, marking the cut.

    Args:
        text: The text.
        cap: The byte cap on ``json.dumps(text)``.

    Returns:
        *text*, or a prefix of it plus a ``[truncated: ...]`` marker (empty when not even the marker fits).

    Raises:
        Nothing.
    """
    if _json_len(text) <= cap:
        return text
    marker = f"\n[truncated: {len(text)} chars in all]"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _json_len(text[:mid] + marker) <= cap:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + marker if _json_len(text[:lo] + marker) <= cap else ""


def _fit_results(results: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Clip result contents so the list's JSON fits *budget* bytes (each gets an equal share when cut)."""
    if not results or _json_len(results) <= budget:
        return results
    share = max(64, budget // len(results) - 64)
    return [_result(r["tool_use_id"], r["ok"], clip(r["content"], share)) for r in results]


def _code(text: str) -> str:
    out = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "-" for c in str(text).lower())
    out = out.strip("-_")[:64] or "failed"
    return out


def _done(status: str, reason: str) -> dict[str, Any]:
    """A ``done`` op (reason in the host's short-code grammar: lowercase letters, digits, ``_`` and ``-``)."""
    return {"v": PROTOCOL_VERSION, "op": "done", "status": status, "reason": _code(reason)}


def _result(tool_use_id: str, ok: bool, content: str) -> dict[str, Any]:
    return {"tool_use_id": tool_use_id, "ok": ok, "content": content}


def _model_op(results: list[dict[str, Any]], cont: bool = False, carry: Any = None) -> dict[str, Any]:
    """A ``model`` op carrying in-guest (and denied) results; host-tool results are host-held (P05 deviation 5)."""
    return {"v": PROTOCOL_VERSION, "op": "model", "tool_results": _fit_results(results, MAX_OP_BYTES - FRAME_SLACK),
            "continue": cont, "carry": carry}


# --- paths -----------------------------------------------------------------------------------------------------------

def _grant_mounts(mounts: list[Any]) -> list[tuple[str, str]]:
    """The event's grant mounts this guest honours: ``/scratch`` (rw) and ``/project/<rel>`` (ro)."""
    out = []
    for m in mounts:
        if not isinstance(m, dict) or not isinstance(m.get("guest"), str) or m.get("mode") not in ("ro", "rw"):
            continue
        guest = m["guest"]
        if guest == "/scratch" or (guest.startswith("/project/") and ".." not in guest.split("/")):
            out.append((guest.rstrip("/"), m["mode"]))
    return out


def check_path(path: Any, mounts: list[Any], *, write: bool = False) -> str:
    """Validate a model-supplied guest path against the event's grant mounts (a soft check; WASI is the hard one).

    Args:
        path: The model's ``path`` value (data, C-4).
        mounts: The event's ``mounts`` list.
        write: Whether the tool writes (only ``/scratch`` with mode ``rw`` qualifies).

    Returns:
        The normalised absolute guest path.

    Raises:
        ToolError: Not absolute, ``..``, under ``/src`` or ``/lib``, or outside every honoured mount.
    """
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_CHARS or "\0" in path or "\\" in path:
        raise ToolError("path must be a non-empty absolute guest path")
    if not path.startswith("/"):
        raise ToolError("path must be absolute (/project/<dir>/... or /scratch/...)")
    parts = path.split("/")
    if ".." in parts:
        raise ToolError("path may not contain '..'")
    norm = "/" + "/".join(p for p in parts if p not in ("", "."))
    if norm in ("/src", "/lib") or norm.startswith(("/src/", "/lib/")):
        raise ToolError("path is a runtime mount, not part of this agent's grant")
    for guest, mode in _grant_mounts(mounts):
        if norm == guest or norm.startswith(guest + "/"):
            if write and (guest != "/scratch" or mode != "rw"):
                raise ToolError("writes are allowed only under /scratch")
            return norm
    raise ToolError("path is outside this agent's grant" + (" (writes go under /scratch)" if write else ""))


def instruction_file_name(name: str) -> bool:
    """Whether a file name is one an agent harness loads as instructions (R8; case-insensitive).

    Args:
        name: A path or file name (only the final component counts).

    Returns:
        True for ``CLAUDE.md``, ``AGENTS.md``, ``GEMINI.md``, ``.cursorrules`` and ``*.agent.md``.

    Raises:
        Nothing.
    """
    base = name.rsplit("/", 1)[-1].lower()
    return base in {n.lower() for n in INSTRUCTION_FILE_NAMES} or base.endswith(INSTRUCTION_FILE_SUFFIX)


def _lstat(path: str) -> os.stat_result:
    try:
        return os.stat(_fs(path), follow_symlinks=False)
    except FileNotFoundError:
        raise ToolError("no such file or directory") from None


# --- in-guest tools --------------------------------------------------------------------------------------------------

def _check_input(name: str, args: Any) -> dict[str, str]:
    """Closed-shape check of a tool input (C-4: inputs are data, validated before use)."""
    need, optional = TOOL_INPUTS[name]
    if not isinstance(args, dict) or not set(need) <= set(args) or set(args) - set(need) - set(optional):
        raise ToolError(f"{name} takes {{{', '.join(need + optional)}}}")
    for key, value in args.items():
        if not isinstance(value, str):
            raise ToolError(f"{name}: {key} must be a string")
    return args


def _read_file(args: dict[str, str], mounts: list[Any], limits: dict[str, int]) -> str:
    """Read a regular file (the final component may not be a symlink), up to the read cap."""
    path = check_path(args["path"], mounts)
    if stat.S_ISLNK(_lstat(path).st_mode):
        raise ToolError("refused: path is a symlink")
    fd = os.open(_fs(path), os.O_RDONLY | _O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ToolError("not a regular file")
        cap = min(limits["max_read_bytes"], limits["max_tool_output_bytes"])
        chunks, left = [], cap + 1
        while left > 0:
            chunk = os.read(fd, min(left, 1 << 16))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    text = data[:cap].decode("utf-8", "replace")
    if len(data) > cap:
        text += f"\n[truncated: file is {info.st_size} bytes; read the first {cap}]"
    return text


def _entries(path: str) -> list[tuple[str, str]]:
    """Sorted ``(name, kind)`` pairs of a directory, never following symlinks (kind ``f``/``d``/``l``/``o``)."""
    out = []
    with os.scandir(_fs(path)) as it:
        for entry in it:
            mode = entry.stat(follow_symlinks=False).st_mode
            kind = "l" if stat.S_ISLNK(mode) else "d" if stat.S_ISDIR(mode) else "f" if stat.S_ISREG(mode) else "o"
            out.append((entry.name, kind))
    return sorted(out)


def _list_dir(args: dict[str, str], mounts: list[Any], limits: dict[str, int]) -> str:
    """List a directory: one ``<kind> <name>`` line per entry (symlinks reported as ``l``, not followed)."""
    path = check_path(args["path"], mounts)
    if not stat.S_ISDIR(_lstat(path).st_mode):
        raise ToolError("not a directory (symlinks are not followed)")
    entries = _entries(path)
    lines = [f"{kind} {name}{'/' if kind == 'd' else ''}" for name, kind in entries[:MAX_LIST_ENTRIES]]
    if len(entries) > MAX_LIST_ENTRIES:
        lines.append(f"[truncated: {len(entries)} entries; showing {MAX_LIST_ENTRIES}]")
    return "\n".join(lines) if lines else "(empty directory)"


def _search_file(path: str, pattern: str, hits: list[str], scanned: int) -> int:
    fd = os.open(_fs(path), os.O_RDONLY | _O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return scanned
        data = os.read(fd, min(SEARCH_FILE_BYTES, SEARCH_TOTAL_BYTES - scanned))
    finally:
        os.close(fd)
    for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
        if pattern in line and len(hits) < SEARCH_MAX_MATCHES:
            hits.append(f"{path}:{number}:{line[:SEARCH_LINE_CHARS]}")
    return scanned + len(data)


def _search_files(args: dict[str, str], mounts: list[Any], limits: dict[str, int]) -> str:
    """Find lines containing a literal substring under a directory (or in one file); symlinks are skipped."""
    path, pattern = check_path(args["path"], mounts), args["pattern"]
    if not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ToolError(f"pattern must be 1-{MAX_PATTERN_CHARS} characters")
    mode = _lstat(path).st_mode
    if stat.S_ISLNK(mode):
        raise ToolError("refused: path is a symlink")
    hits, scanned, seen, stopped = [], 0, 0, ""
    stack = [path] if stat.S_ISDIR(mode) else []
    if stat.S_ISREG(mode):
        scanned = _search_file(path, pattern, hits, scanned)
    while stack and not stopped:
        current = stack.pop()
        for name, kind in _entries(current):
            seen += 1
            if seen > SEARCH_MAX_ENTRIES:
                stopped = f"entry cap {SEARCH_MAX_ENTRIES}"
            elif scanned >= SEARCH_TOTAL_BYTES:
                stopped = f"byte cap {SEARCH_TOTAL_BYTES}"
            elif len(hits) >= SEARCH_MAX_MATCHES:
                stopped = f"match cap {SEARCH_MAX_MATCHES}"
            if stopped:
                break
            child = current.rstrip("/") + "/" + name
            if kind == "d":
                stack.append(child)
            elif kind == "f":
                scanned = _search_file(child, pattern, hits, scanned)
    text = "\n".join(hits) if hits else "(no matches)"
    return text + (f"\n[stopped: {stopped}]" if stopped else "")


def _write_file(args: dict[str, str], mounts: list[Any], limits: dict[str, int]) -> str:
    """Create or overwrite a regular file under ``/scratch`` (no symlinks, no instruction-file names, size-capped)."""
    path = check_path(args["path"], mounts, write=True)
    if path == "/scratch":
        raise ToolError("path names the scratch directory itself, not a file")
    if instruction_file_name(path):
        raise ToolError("refused: instruction-file name")
    data = args["content"].encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError(f"content is {len(data)} bytes; the cap is {MAX_WRITE_BYTES}")
    if not stat.S_ISDIR(_lstat(path.rsplit("/", 1)[0]).st_mode):
        raise ToolError("parent is not a directory")
    try:
        existing = os.stat(_fs(path), follow_symlinks=False).st_mode
    except FileNotFoundError:
        existing = None                   # a new file
    if existing is not None and not stat.S_ISREG(existing):
        raise ToolError("refused: an existing non-regular file (or symlink) is in the way")
    fd = os.open(_fs(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_NOFOLLOW, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
    finally:
        os.close(fd)
    return f"wrote {len(data)} bytes to {path}"


TOOLS = {"read_file": _read_file, "list_dir": _list_dir, "search_files": _search_files, "write_file": _write_file}


def run_tool(use: dict[str, Any], mounts: list[Any], limits: dict[str, int]) -> tuple[bool, str]:
    """Run one in-guest tool for a ``tool_use`` block.

    Args:
        use: ``{id, name, input}`` (``name`` in :data:`IN_GUEST_TOOLS`).
        mounts: The event's ``mounts`` list.
        limits: The clamped event limits.

    Returns:
        ``(ok, content)``, content capped at ``limits["max_tool_output_bytes"]``.

    Raises:
        Nothing for tool failures (they become ``ok: false``).
    """
    try:
        args = _check_input(use["name"], use["input"])
        ok, text = True, TOOLS[use["name"]](args, mounts, limits)
    except ToolError as exc:
        ok, text = False, str(exc)
    except OSError as exc:
        ok, text = False, f"{type(exc).__name__}: {exc.strerror or 'operation failed'}"
    except (ValueError, MemoryError) as exc:     # UnicodeError is a ValueError
        ok, text = False, type(exc).__name__
    return ok, clip(text, limits["max_tool_output_bytes"])


# --- decision function -----------------------------------------------------------------------------------------------

def _limits(raw: dict[str, Any]) -> dict[str, int]:
    out = {}
    for key, cap in LIMIT_CAPS.items():
        value = raw.get(key)
        if type(value) is not int or value < 256:
            raise BadEvent("bad-event", f"limits.{key}")
        out[key] = min(value, cap)
    return out


def _uses(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        raise BadEvent("bad-event", "model_reply content")
    uses = []
    for block in content:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            raise BadEvent("bad-event", "content block")
        if block["type"] == "tool_use":
            if not isinstance(block.get("id"), str) or not isinstance(block.get("name"), str):
                raise BadEvent("bad-event", "tool_use block")
            uses.append({"id": block["id"], "name": block["name"], "input": block.get("input")})
    return uses


def _valid_id(value: str) -> bool:
    return 0 < len(value) <= MAX_ID_CHARS and all(c.isascii() and (c.isalnum() or c in "_-") for c in value)


def _on_model_reply(ev: dict[str, Any], carry: Any, tools: frozenset[str], mounts: list[Any],
                    limits: dict[str, int]) -> dict[str, Any]:
    """Answer a model reply: finish, continue, or start working through its tool uses in order."""
    uses, stop = _uses(ev.get("content")), ev.get("stop")
    continues = carry.get("continues", 0) if isinstance(carry, dict) else 0
    if not uses:
        if stop == "max_tokens":
            if type(continues) is int and 0 <= continues < MAX_CONTINUES:
                return _model_op([], cont=True, carry={"continues": continues + 1})
            return _done("failed", "max-tokens")
        if stop in ("end_turn", "other"):
            return _done("ok", "done")
        return _done("failed", stop if isinstance(stop, str) else "bad-stop")
    results, pending = [], []
    for use in uses:
        if not _valid_id(use["id"]):
            continue                      # the host answers an id it cannot take back with its own error
        if use["name"] not in tools:
            results.append(_result(use["id"], False, f"tool not granted: {use['name'][:64]}"))
        elif use["name"] in IN_GUEST_TOOLS:
            ok, text = run_tool(use, mounts, limits)
            results.append(_result(use["id"], ok, text))
        elif use["name"] in HOST_TOOLS:
            pending.append(use)
        else:
            results.append(_result(use["id"], False, f"unknown tool: {use['name'][:64]}"))
    return _next_host_call(results, pending)


def _delegate_input(args: Any) -> bool:
    return isinstance(args, dict) and set(args) == {"agent", "task"} and all(isinstance(v, str) for v in args.values())


def _next_host_call(results: list[dict[str, Any]], pending: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask the host for the next pending host tool (results carried along), or send every result to the model."""
    while pending:
        use = pending[0]
        if use["name"] == "delegate" and not _delegate_input(use["input"]):
            results.append(_result(use["id"], False, "delegate takes {agent, task} (strings)"))
            pending.pop(0)
            continue
        room = MAX_CARRY_BYTES - FRAME_SLACK - _json_len(pending)
        if room < 1024:
            for left in pending:          # too large to carry: answer instead of emitting an oversize frame
                results.append(_result(left["id"], False, "tool input too large to carry; not run"))
            break
        carry = {"results": _fit_results(results, room), "pending": pending}
        if use["name"] == "delegate":
            return {"v": PROTOCOL_VERSION, "op": "delegate", "id": use["id"], "agent": use["input"]["agent"],
                    "task": use["input"]["task"], "budget": None, "carry": carry}
        return {"v": PROTOCOL_VERSION, "op": "tool", "id": use["id"], "name": use["name"], "input": use["input"],
                "carry": carry}
    return _model_op(results)


def _on_host_outcome(ev: dict[str, Any], carry: Any) -> dict[str, Any]:
    """A host tool, delegation or refusal answered the first pending use: record it and move on."""
    kind = ev["kind"]
    pending = carry.get("pending") if isinstance(carry, dict) else None
    if not isinstance(pending, list) or not pending or not isinstance(carry.get("results"), list):
        if kind == "denied":
            return _done("failed", "denied-" + str(ev.get("code", "refused")))
        raise BadEvent("bad-event", f"{kind} without a pending host call")
    head, results = pending[0], carry["results"]
    if not isinstance(head, dict) or ev.get("id") != head.get("id"):
        if kind == "denied" and "id" not in ev:
            return _done("failed", "denied-" + str(ev.get("code", "refused")))
        raise BadEvent("bad-event", f"{kind} does not answer the pending call")
    pending.pop(0)
    if kind == "denied":
        message = ev.get("message") if isinstance(ev.get("message"), str) else ""
        results.append(_result(head["id"], False, f"denied ({str(ev.get('code'))[:32]}): {message[:500]}"))
    # tool_result / delegate_result: the host holds that result and puts it in the model turn itself.
    return _next_host_call(results, pending)


def decide(frame: dict[str, Any]) -> dict[str, Any]:
    """One step: the op answering this event frame.

    Args:
        frame: A frame from :func:`read_event`.

    Returns:
        A ``model``, ``tool``, ``delegate`` or ``done`` op.

    Raises:
        BadEvent: An event body this guest cannot answer.
    """
    ev, carry = frame["event"], frame["carry"]
    kind = ev.get("kind")
    tools = frozenset(t for t in frame["agent"]["tools"] if isinstance(t, str))
    if kind == "start":
        return _model_op([])
    if kind == "model_reply":
        return _on_model_reply(ev, carry, tools, frame["mounts"], _limits(frame["limits"]))
    if kind in ("tool_result", "delegate_result", "denied"):
        return _on_host_outcome(ev, carry)
    raise BadEvent("bad-event", "unknown event kind")


def handle(raw: bytes) -> str:
    """Turn raw stdin bytes into the encoded op line.

    Args:
        raw: Everything read from stdin.

    Returns:
        Exactly one op line; a ``done failed`` line for a bad event or an internal error.

    Raises:
        Nothing the guest can anticipate; anything else exits non-zero, a failed step at the host.
    """
    try:
        op = decide(read_event(raw))
    except BadEvent as exc:
        op = _done("failed", exc.reason)
    except _INTERNAL_ERRORS as exc:           # a guest bug still answers with one done frame
        sys.stderr.write(f"guest error: {type(exc).__name__}\n")
        op = _done("failed", "guest-error-" + type(exc).__name__)
    line = encode_op(op)
    if len(line) > MAX_OP_BYTES:
        line = encode_op(_done("failed", "op-too-large"))
    return line


def main() -> None:
    """Read one event from stdin, write one op line to stdout.

    Returns:
        None.

    Raises:
        Nothing (see :func:`handle`).
    """
    raw = sys.stdin.buffer.read(MAX_EVENT_BYTES + 1)
    sys.stdout.write(handle(raw))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
