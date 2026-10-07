#!/usr/bin/env python3
"""agentteams read-only filesystem MCP server (stdio, stdlib-only) for generated Goose teams.

WHY THIS EXISTS
---------------
In Goose 1.37 the only tool that can print a file's contents is the ``developer`` extension's
``shell``, which can also write, delete and run anything. A read-only agent (an auditor) scoped
down to ``tree``/``analyze`` is safe but cannot read the files it audits. This server gives such
an agent real read tools with no write capability at all. See
``references/goose-tool-scoping-spike.md`` (the phase-0 spike): Goose enforces
``available_tools`` on stdio extensions, and ``tree``/``analyze`` are not workspace-confined.

SAFETY MODEL (read this before extending)
-----------------------------------------
* **Read-only by construction.** The tools are ``read_file``, ``list_dir``, ``find``, ``grep`` and
  ``stat``. Nothing in this file writes, deletes, renames, creates, runs a process or opens a socket.
  ``tests/test_goose_readfs_mcp.py`` scans the source for such calls. The scan is a tripwire, not a
  proof: code review and the integrity pin (``references/enforcement-integrity.json``) are the other
  two walls. Do NOT add a mutating tool here; a writer agent gets ``developer`` instead.
* **Workspace confinement.** Every path is resolved with ``os.path.realpath`` and must equal the
  ``--root`` or lie under it, so ``..``, absolute paths and symlinks pointing outside are refused.
  Files are opened ``O_NOFOLLOW|O_NONBLOCK`` and the open handle must be the regular file (same
  device and inode) that was checked, closing the check-then-open race. Walks never follow links.
* **In-workspace deny list.** VCS internals, ``.env`` files, keys, keystores and credential files
  are refused even inside the root (``_DEFAULT_DENY``, case-folded, plus any ``--deny`` glob).
* **Bounded, and says so.** Reads, results, walked entries, depth and grep time are capped. Every
  cap, and every file grep skips for size or type, is reported in the result, so a truncated search
  never reads as "no matches". (Denied paths are omitted silently, by policy.)
* **Content is data (C-4).** File contents are returned as tool output and never interpreted.

Known residuals, both needing a concurrent writer in the workspace: a hard link inside the root to a
file elsewhere on the same filesystem is read like any workspace file; and where the OS cannot report
an open handle's path (not macOS or Linux), a parent directory swapped for an outward link between
the check and the open is caught only if the inode differs.

Transport is newline-delimited JSON-RPC 2.0 over stdin/stdout (the MCP stdio convention), so the
server needs no network and works under a confined profile's ``deny network*``. The file is
self-contained: it does NOT import ``agentteams`` at runtime (``scripts/`` is excluded from the
packaged dist). Phase 2 of the plan ships it into generated Goose teams.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import re
import signal
import stat as stat_mod
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentteams_readfs"
SERVER_VERSION = "1.0.0"

#: Paths refused even inside the root. Matched, case-folded, against the root-relative POSIX path
#: and against each path component: secrets and VCS internals are never an audit need.
_DEFAULT_DENY: tuple[str, ...] = (
    ".git", ".hg", ".svn", ".git-credentials", ".env", ".env.*", "*.env", ".envrc",
    "*.key", "*.pem", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.kdbx",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", ".ssh", ".aws", ".gnupg", ".kube", ".docker",
    "credentials", "credentials*.json", ".netrc", ".npmrc", ".pypirc", ".pgpass", ".htpasswd",
    "*.tfstate", "*.tfstate.*", "*.tfvars",
    # agentteams' control plane: the runner's ledger and dispatch records, the request/result queues (which
    # briefly hold raw dispatch nonces) and the installed copy of this server. A reader never needs them.
    ".agentteams", ".agentteams-queue",
)
#: Template files that look like secrets but never hold them; readable despite ``.env.*``.
_ALLOW: tuple[str, ...] = (".env.example", ".env.sample", ".env.template")

MAX_READ_BYTES = 256 * 1024
MAX_READ_LINES = 2000
MAX_RESULTS = 200
MAX_ENTRIES_VISITED = 20000
MAX_DEPTH = 64
MAX_GREP_FILE_BYTES = 2 * 1024 * 1024
MAX_GREP_SECONDS = 5.0
MAX_LINE_CHARS = 1000
MAX_PATTERN_CHARS = 500
_BINARY_SNIFF = 8192
#: Everything a tool can raise on bad input or a hostile tree, named rather than caught broadly (CH-24).
_TOOL_FAILURES = (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError,
                  RecursionError, OverflowError, re.error)
_READ_FLAGS = (os.O_RDONLY | (os.O_NONBLOCK if hasattr(os, "O_NONBLOCK") else 0)
               | (os.O_NOFOLLOW if hasattr(os, "O_NOFOLLOW") else 0))


class ReadfsError(Exception):
    """A request is refused (reported to the model as a tool error)."""


class _GrepTimeout(Exception):
    """Raised by the SIGALRM handler when a grep runs past ``MAX_GREP_SECONDS``."""


class Workspace:
    """The confined root plus its deny list."""

    def __init__(self, root: Path, deny: tuple[str, ...] = ()) -> None:
        self.root = os.path.realpath(root)
        if os.path.dirname(self.root) == self.root:
            raise ValueError("refusing a filesystem root as the workspace root")
        self.deny = tuple(p.lower() for p in _DEFAULT_DENY)
        self.extra_deny = tuple(p.lower() for p in deny)  # operator globs: the allowlist never overrides these

    def rel(self, real: str) -> str:
        """Root-relative POSIX path of an already-resolved path (``.`` for the root)."""
        rel = os.path.relpath(real, self.root)
        return "." if rel == "." else Path(rel).as_posix()

    def inside(self, real: str) -> bool:
        """True when a resolved path is the root or lies under it."""
        return real == self.root or real.startswith(self.root + os.sep)

    def denied(self, rel: str) -> bool:
        """True when the root-relative path or any component matches the deny list (case-folded)."""
        if rel == ".":
            return False
        folded = rel.lower()
        parts = folded.split("/")
        # An allowlisted template name (.env.example) is judged by its directories only.
        allowed_leaf = parts[-1] in _ALLOW
        check = parts[:-1] if allowed_leaf else parts
        for pattern in self.deny:
            if not allowed_leaf and fnmatch.fnmatchcase(folded, pattern):
                return True
            if any(fnmatch.fnmatchcase(part, pattern) for part in check):
                return True
        return any(fnmatch.fnmatchcase(folded, pattern) or any(fnmatch.fnmatchcase(p, pattern) for p in parts)
                   for pattern in self.extra_deny)

    def resolve(self, path: Any) -> str:
        """Resolve a requested path to a real path inside the root, or refuse.

        Raises:
            ReadfsError: The path is not a string, escapes the root (after resolving symlinks) or
                is denied.
        """
        if path is not None and not isinstance(path, str):
            raise ReadfsError("path must be a string")
        raw = (path or ".").strip() or "."
        real = os.path.realpath(raw if os.path.isabs(raw) else os.path.join(self.root, raw))
        if not self.inside(real):
            raise ReadfsError(f"path '{raw}' is outside the workspace; refused")
        if self.denied(self.rel(real)):
            raise ReadfsError(f"path '{raw}' is on the read deny list; refused")
        return real

    def walk(self, start: str, notes: list[str]) -> Iterator[tuple[str, str]]:
        """Yield ``(real_path, rel_path)`` for readable files under ``start``.

        Never follows a link out of the root, never descends past ``MAX_DEPTH`` and stops after
        ``MAX_ENTRIES_VISITED`` directory entries. Every such cut is appended to ``notes``.
        """
        visited = 0
        base_depth = start.count(os.sep)

        def _unreadable(err: OSError) -> None:
            notes.append(f"[skipped unreadable directory: {self.rel(os.path.realpath(err.filename or start))}]")

        for dirpath, dirnames, filenames in os.walk(start, onerror=_unreadable, followlinks=False):
            visited += len(dirnames) + len(filenames)
            if visited > MAX_ENTRIES_VISITED:
                notes.append(f"[stopped: walked more than {MAX_ENTRIES_VISITED} entries; narrow the path]")
                return
            if dirpath.count(os.sep) - base_depth >= MAX_DEPTH:
                if dirnames:
                    notes.append(f"[not descended below depth {MAX_DEPTH}: {self.rel(dirpath)}]")
                dirnames[:] = []
            dirnames[:] = sorted(d for d in dirnames if not self.denied(self.rel(os.path.join(dirpath, d))))
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                real = os.path.realpath(full)
                if not self.inside(real):
                    continue  # a symlink pointing out of the workspace
                # Check the link's own path AND its target: a link named notes.txt -> .env is denied.
                if self.denied(self.rel(full)) or self.denied(self.rel(real)) or not os.path.isfile(real):
                    continue
                yield real, self.rel(full)


def _fd_path(fd: int) -> str | None:
    """Return the path the OS reports for an open handle, or None where it cannot tell."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None and hasattr(fcntl, "F_GETPATH"):  # macOS
        try:
            return fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024)).split(b"\x00", 1)[0].decode()
        except OSError:
            return None
    try:
        return os.readlink(f"/proc/self/fd/{fd}")  # Linux
    except OSError:
        return None


def _open_regular(ws: Workspace, real: str, limit: int) -> tuple[bytes, int]:
    """Read up to ``limit`` bytes of a regular file inside the root, refusing anything swapped in.

    The handle is opened ``O_RDONLY|O_NONBLOCK|O_NOFOLLOW``. It must be a regular file with the
    device and inode the path had when checked, and the path the OS reports for the open handle
    must still lie inside the root. ``O_NOFOLLOW`` guards only the last component, so this last
    check is what refuses a parent directory swapped for an outward link after ``resolve()``.
    Where the OS cannot report a handle's path, only the inode check applies (documented residual).

    Raises:
        ReadfsError: The path is not (or is no longer) the regular file inside the root that was checked.
    """
    try:
        before = os.stat(real)
        fd = os.open(real, _READ_FLAGS)
    except OSError as exc:
        raise ReadfsError(f"cannot open '{ws.rel(real)}': {exc.strerror}") from exc
    with os.fdopen(fd, "rb") as fh:
        after = os.fstat(fh.fileno())
        if not stat_mod.S_ISREG(after.st_mode) or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise ReadfsError("file changed while being opened; refused")
        opened = _fd_path(fh.fileno())
        if opened is not None and not ws.inside(os.path.realpath(opened)):
            raise ReadfsError("file resolved outside the workspace while being opened; refused")
        return fh.read(limit), after.st_size


def _is_binary(head: bytes) -> bool:
    """True when the sniffed head of a file contains a NUL byte."""
    return b"\x00" in head


def _pattern(args: dict[str, Any], key: str) -> str:
    """Return a required, non-empty, bounded string argument, or refuse."""
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ReadfsError(f"'{key}' must be a non-empty string")
    if len(value) > MAX_PATTERN_CHARS:
        raise ReadfsError(f"'{key}' is longer than {MAX_PATTERN_CHARS} characters")
    return value


def _tool_read_file(ws: Workspace, args: dict[str, Any]) -> str:
    """Read a text file with line numbers, paged by ``offset``/``limit``."""
    real = ws.resolve(args.get("path"))
    if not os.path.isfile(real):
        raise ReadfsError(f"'{args.get('path')}' is not a file")
    data, size = _open_regular(ws, real, MAX_READ_BYTES + 1)
    if _is_binary(data[:_BINARY_SNIFF]):
        return f"{ws.rel(real)}: binary file, {size} bytes (not shown)"
    truncated_bytes = len(data) > MAX_READ_BYTES
    lines = data[:MAX_READ_BYTES].decode("utf-8", errors="replace").splitlines()
    offset = max(int(args.get("offset") or 1), 1)
    limit = min(max(int(args.get("limit") or MAX_READ_LINES), 1), MAX_READ_LINES)
    window = lines[offset - 1: offset - 1 + limit]
    last = offset - 1 + len(window)
    header = f"{ws.rel(real)} (lines {offset}-{last} of {len(lines)}{'+' if truncated_bytes else ''}; {size} bytes)"
    body = "\n".join(f"{offset + i:6d}  {line[:MAX_LINE_CHARS]}" for i, line in enumerate(window))
    more = last < len(lines) or truncated_bytes
    return f"{header}\n{body}" + ("\n[truncated: call again with a larger offset]" if more else "")


def _tool_list_dir(ws: Workspace, args: dict[str, Any]) -> str:
    """List one directory: files with sizes, subdirectories and links (outward links flagged)."""
    real = ws.resolve(args.get("path"))
    if not os.path.isdir(real):
        raise ReadfsError(f"'{args.get('path') or '.'}' is not a directory")
    out = []
    for name in sorted(os.listdir(real)):
        full = os.path.join(real, name)
        rel = ws.rel(full)
        if ws.denied(rel):
            continue
        st = os.lstat(full)
        if stat_mod.S_ISLNK(st.st_mode):
            inside = ws.inside(os.path.realpath(full))
            out.append(f"link  {rel}" + ("" if inside else "  (points outside the workspace; unreadable)"))
        elif stat_mod.S_ISDIR(st.st_mode):
            out.append(f"dir   {rel}/")
        else:
            out.append(f"file  {rel}  {st.st_size}")
        if len(out) >= MAX_RESULTS:
            out.append(f"[truncated at {MAX_RESULTS} entries]")
            break
    return "\n".join(out) or "(empty)"


def _tool_find(ws: Workspace, args: dict[str, Any]) -> str:
    """Find files whose root-relative path or name matches a glob."""
    pattern = _pattern(args, "pattern")
    start = ws.resolve(args.get("path"))
    notes: list[str] = []
    hits = []
    for _real, rel in ws.walk(start, notes):
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(rel), pattern):
            hits.append(rel)
            if len(hits) >= MAX_RESULTS:
                notes.append(f"[truncated at {MAX_RESULTS} results]")
                break
    return "\n".join(hits + notes) or "(no matches)"


def _grep_files(ws: Workspace, regex: re.Pattern[str], glob: str, start: str, notes: list[str]) -> list[str]:
    """Collect ``path:line: text`` matches; large and binary files are skipped and noted."""
    files = [(start, ws.rel(start))] if os.path.isfile(start) else ws.walk(start, notes)
    hits: list[str] = []
    too_big = skipped = 0
    for real, rel in files:
        if not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(os.path.basename(rel), glob)):
            continue
        if os.path.getsize(real) > MAX_GREP_FILE_BYTES:
            too_big += 1
            continue
        try:
            data, _ = _open_regular(ws, real, MAX_GREP_FILE_BYTES)
        except ReadfsError:
            skipped += 1
            continue
        if _is_binary(data[:_BINARY_SNIFF]):
            skipped += 1
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if regex.search(line[:MAX_LINE_CHARS]):
                hits.append(f"{rel}:{number}: {line[:MAX_LINE_CHARS]}")
                if len(hits) >= MAX_RESULTS:
                    notes.append(f"[truncated at {MAX_RESULTS} matches]")
                    return hits
    if too_big:
        notes.append(f"[skipped {too_big} file(s) over {MAX_GREP_FILE_BYTES} bytes]")
    if skipped:
        notes.append(f"[skipped {skipped} binary or unopenable file(s)]")
    return hits


def _tool_grep(ws: Workspace, args: dict[str, Any]) -> str:
    """Search text files for a regular expression, under a wall-clock limit."""
    flags = re.IGNORECASE if args.get("ignore_case") else 0
    try:
        regex = re.compile(_pattern(args, "pattern"), flags)
    except re.error as exc:
        raise ReadfsError(f"invalid regular expression: {exc}") from exc
    glob = args.get("glob") or "*"
    if not isinstance(glob, str):
        raise ReadfsError("'glob' must be a string")
    start = ws.resolve(args.get("path"))
    notes: list[str] = []
    timed = hasattr(signal, "setitimer")

    def _alarm(_signum: int, _frame: Any) -> None:
        raise _GrepTimeout

    previous = signal.signal(signal.SIGALRM, _alarm) if timed else None
    try:
        if timed:
            signal.setitimer(signal.ITIMER_REAL, MAX_GREP_SECONDS)
        hits = _grep_files(ws, regex, glob, start, notes)
    except _GrepTimeout:
        raise ReadfsError(f"grep exceeded {MAX_GREP_SECONDS:g}s (simplify the pattern or narrow the path)") from None
    finally:
        if timed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    return "\n".join(hits + notes) or "(no matches)"


def _tool_stat(ws: Workspace, args: dict[str, Any]) -> str:
    """Report a path's type, size and modification time as JSON."""
    real = ws.resolve(args.get("path"))
    st = os.stat(real)
    kind = "dir" if stat_mod.S_ISDIR(st.st_mode) else ("file" if stat_mod.S_ISREG(st.st_mode) else "other")
    return json.dumps({"path": ws.rel(real), "type": kind, "size": st.st_size, "mtime": int(st.st_mtime)})


_PATH = {"type": "string", "description": "Path relative to the workspace root (default '.')."}
_TOOLS: dict[str, dict[str, Any]] = {
    "read_file": {
        "description": "Read a text file in the workspace, with line numbers. Use offset/limit to page.",
        "inputSchema": {"type": "object", "required": ["path"], "properties": {
            "path": _PATH,
            "offset": {"type": "integer", "minimum": 1, "description": "First line (1-based)."},
            "limit": {"type": "integer", "minimum": 1, "description": f"Lines to return (max {MAX_READ_LINES})."},
        }},
        "handler": _tool_read_file,
    },
    "list_dir": {
        "description": "List one directory in the workspace (files with sizes, subdirectories, links).",
        "inputSchema": {"type": "object", "properties": {"path": _PATH}},
        "handler": _tool_list_dir,
    },
    "find": {
        "description": "Find files in the workspace whose path or name matches a glob (e.g. '*.py', 'docs/*.md').",
        "inputSchema": {"type": "object", "required": ["pattern"], "properties": {
            "pattern": {"type": "string"}, "path": _PATH,
        }},
        "handler": _tool_find,
    },
    "grep": {
        "description": "Search text files in the workspace for a regular expression; returns path:line: text.",
        "inputSchema": {"type": "object", "required": ["pattern"], "properties": {
            "pattern": {"type": "string"}, "path": _PATH,
            "glob": {"type": "string", "description": "Only files matching this glob (default '*')."},
            "ignore_case": {"type": "boolean"},
        }},
        "handler": _tool_grep,
    },
    "stat": {
        "description": "Report a workspace path's type, size and modification time.",
        "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": _PATH}},
        "handler": _tool_stat,
    },
}


def handle_request(msg: dict[str, Any], ws: Workspace) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC request; return the response object, or None for a notification.

    Implements ``initialize``, ``tools/list`` and ``tools/call``. Unknown methods return a JSON-RPC
    ``-32601`` error. A refused or failing tool call is an ``isError`` tool result, never a crash.
    """
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "tools/list":
        return _ok(msg_id, {"tools": [
            {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
            for n, t in _TOOLS.items()
        ]})
    if method == "tools/call":
        params = msg.get("params")
        args = params.get("arguments") if isinstance(params, dict) else None
        if not isinstance(params, dict) or not isinstance(args if args is not None else {}, dict):
            return _tool_result(msg_id, "ERROR: params and arguments must be objects.", is_error=True)
        name = params.get("name")
        tool = _TOOLS.get(name) if isinstance(name, str) else None
        if tool is None:
            return _tool_result(msg_id, f"ERROR: unknown tool '{name}'.", is_error=True)
        handler: Callable[[Workspace, dict[str, Any]], str] = tool["handler"]
        try:
            return _tool_result(msg_id, handler(ws, args or {}))
        except ReadfsError as exc:
            return _tool_result(msg_id, f"ERROR: {exc}", is_error=True)
        except _TOOL_FAILURES as exc:  # a tool failure is an error result, never a server crash
            return _tool_result(msg_id, f"ERROR: {type(exc).__name__}: {exc}", is_error=True)
    if msg_id is None:
        return None
    return _err(msg_id, -32601, f"method not found: {method}")


def _ok(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON-RPC success envelope."""
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC error envelope."""
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_result(msg_id: Any, text: str, *, is_error: bool = False) -> dict[str, Any]:
    """Build an MCP ``tools/call`` result envelope carrying a single text content block."""
    return _ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error})


def serve(ws: Workspace, stdin: io.TextIOBase, stdout: io.TextIOBase) -> None:
    """Run the newline-delimited JSON-RPC 2.0 stdio loop until stdin closes."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            stdout.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            stdout.flush()
            continue
        response = handle_request(msg, ws) if isinstance(msg, dict) else _err(None, -32600, "invalid request")
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``--root`` (default: the current directory) and repeatable ``--deny`` globs."""
    parser = argparse.ArgumentParser(description="agentteams read-only filesystem MCP server (stdio).")
    parser.add_argument("--root", default=".", help="Workspace root (default: current directory).")
    parser.add_argument("--deny", action="append", default=[], help="Extra glob to refuse (repeatable).")
    ns = parser.parse_args(argv)
    if not os.path.isdir(ns.root):
        parser.error(f"--root {ns.root!r} is not a directory")
    try:
        ws = Workspace(Path(ns.root), tuple(ns.deny))
    except ValueError as exc:
        parser.error(str(exc))
    serve(ws, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
