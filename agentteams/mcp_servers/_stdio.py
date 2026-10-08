"""_stdio.py — the newline-delimited JSON-RPC 2.0 loop shared by agentteams' first-party MCP servers.

Same transport as ``agentteams/data/goose-readfs-mcp.py``: one JSON object per line on stdin and stdout, no network.
A tool handler returns a JSON-serialisable value (sent as text content) or raises :class:`ToolError` (sent as an
error result). Arguments and results are data (C-4): nothing a client sends is executed or interpreted beyond the
handler's own validation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

#: The MCP protocol version answered when the client offers none.
DEFAULT_PROTOCOL = "2024-11-05"
#: Largest request line read (characters), and largest text result sent (UTF-8 bytes).
MAX_LINE = 1024 * 1024
MAX_RESULT = 64 * 1024


_PARSE_ERROR = object()


def _parse(line: str) -> Any:
    """The decoded request, or ``_PARSE_ERROR`` for an over-long or malformed line (answered with -32700)."""
    if len(line) > MAX_LINE:
        return _PARSE_ERROR
    try:
        return json.loads(line)
    except ValueError:
        return _PARSE_ERROR


class ToolError(Exception):
    """A tool refused or failed; the message is returned to the client as an error result."""


def _result(text: str, *, error: bool = False) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_RESULT:
        text = text.encode("utf-8")[:MAX_RESULT].decode("utf-8", "ignore") + "\n…[truncated at 64 KiB]"
    return {"content": [{"type": "text", "text": text}], **({"isError": True} if error else {})}


def serve(name: str, tools: list[dict[str, Any]], call: Callable[[str, dict[str, Any], Path], Any], root: Path,
          *, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Serve one MCP server over stdio until stdin closes.

    Args:
        name: The server name reported in ``initialize``.
        tools: The ``tools/list`` entries.
        call: ``call(tool_name, arguments, root)`` returning a JSON-serialisable value, or raising :class:`ToolError`.
        root: The project root the server works in.
        stdin: Input stream (default ``sys.stdin``).
        stdout: Output stream (default ``sys.stdout``).

    Returns:
        0 when stdin closes.

    Raises:
        Nothing: a malformed request gets a JSON-RPC error; a failing tool gets an error result.
    """
    src, out = stdin or sys.stdin, stdout or sys.stdout
    names = {t["name"] for t in tools}
    for line in src:
        if not line.strip():
            continue
        msg = _parse(line)
        if msg is _PARSE_ERROR:
            out.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32700, "message": "parse error (or line over 1 MiB)"}}) + "\n")
            out.flush()
            continue
        if not isinstance(msg, dict) or msg.get("id") is None:
            continue  # a notification (or junk): nothing to answer
        mid, method, params = msg["id"], msg.get("method"), msg.get("params") or {}
        if not isinstance(params, dict):
            reply: dict[str, Any] = {"jsonrpc": "2.0", "id": mid,
                                     "error": {"code": -32602, "message": "params must be an object"}}
        elif method == "initialize":
            res: dict[str, Any] = {"protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                                   "capabilities": {"tools": {}}, "serverInfo": {"name": name, "version": "1"}}
            reply = {"jsonrpc": "2.0", "id": mid, "result": res}
        elif method == "tools/list":
            reply = {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
        elif method == "tools/call":
            tool = params.get("name")
            args = params.get("arguments") or {}
            if tool not in names or not isinstance(args, dict):
                reply = {"jsonrpc": "2.0", "id": mid, "result": _result(f"unknown tool {tool!r}", error=True)}
            else:
                try:
                    value = call(tool, args, root)
                    reply = {"jsonrpc": "2.0", "id": mid,
                             "result": _result(json.dumps(value, indent=1, sort_keys=True, default=str))}
                except ToolError as exc:
                    reply = {"jsonrpc": "2.0", "id": mid, "result": _result(str(exc), error=True)}
        elif method == "ping":
            reply = {"jsonrpc": "2.0", "id": mid, "result": {}}
        else:
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method!r}"}}
        out.write(json.dumps(reply) + "\n")
        out.flush()
    return 0
