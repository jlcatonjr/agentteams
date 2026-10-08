#!/usr/bin/env python3
"""agentteams_runner: the MCP server through which non-orchestrator agents write and execute under the switch.

WHY THIS EXISTS
---------------
Under ``write_policy: "orchestrator-only"`` a non-orchestrator agent has no Write, Edit or shell tool. An agent granted
this server (the brief's ``mcp_grants``) gets exact tools that **queue requests for the out-of-session runner**
(``agentteams --serve-requests``). The runner holds the ledger key, checks every request against that agent's policy,
and is the only process that writes the project or runs commands. Plan: ``references/plans/
mcp-mediated-agent-writes.plan.md`` (R4).

SAFETY MODEL
------------
* **Holds no key and writes nothing in the project.** Its only writes are new request files in
  ``.agentteams-queue/mcp-requests/`` and acknowledgements in ``.agentteams-queue/acks/``. It reads results from
  ``.agentteams/queue-results/`` (runner-written) and, for ``read_file_hashed``, project files.
* **Not a trust boundary.** The runner re-validates every request. A tampered server could misreport or drop
  requests; the runner's own checks (identity from the nonce, the agent's policy, gates, base hashes) still bind.
  The installed copy lives in the session-write-denied control plane (``.agentteams/bin/``), is launched with
  ``python3 -I -S``, and the runner refuses to serve when its sha256 differs from the pinned one.
* **Bound to one agent.** ``--agent`` (fixed in the agent's canonical front matter) is sent as ``via_agent``; the
  runner refuses a request whose nonce belongs to another agent. ``request_status`` answers only for ids this
  instance queued.
* **Only the granted tools exist.** ``--tools`` lists them; the others are not advertised and are refused.
* **Content is data (C-4).** Tool arguments and results are passed through, never interpreted.

Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout. Self-contained: it does not import ``agentteams``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat as stat_mod
import sys
import time
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentteams_runner"
SERVER_VERSION = "1.0.0"
TOOLS = ("write_file", "delete_file", "run_command", "request_status", "read_file_hashed")
MCP_REQUESTS = ".agentteams-queue/mcp-requests"
ACKS = ".agentteams-queue/acks"
RESULTS = ".agentteams/queue-results"
HEARTBEAT = ".agentteams/runner.heartbeat"
HEARTBEAT_STALE_SECONDS = 15
REQUEST_MAX_BYTES = 2 * 1024 * 1024
RESULT_MAX_BYTES = 4 * 1024 * 1024
READ_MAX_BYTES = 256 * 1024
#: Never read, whatever the root: VCS internals, the control plane and queue (raw nonces in transit), secrets.
DENY_PARTS = (".git", ".hg", ".svn", ".agentteams", ".agentteams-queue", ".ssh", ".aws", ".gnupg", ".kube", ".docker")
DENY_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".tfstate", ".tfvars")
DENY_NAMES = (".env", ".envrc", ".netrc", ".npmrc", ".pypirc", ".pgpass", ".htpasswd", ".git-credentials")
DENY_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".env.", "credentials")

_NONCE = {"type": "string", "description": "Your dispatch nonce, from your task."}
SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file_hashed": {
        "description": "Read a UTF-8 project file and its sha256 (the base_sha256 that write_file and delete_file "
                       "need). Read-only.",
        "inputSchema": {"type": "object", "required": ["path"], "additionalProperties": False,
                        "properties": {"path": {"type": "string"}}}},
    "write_file": {
        "description": "Write a whole file through the runner. base_sha256 is the hash read_file_hashed returned, or "
                       "\"absent\" for a new file. Staged for the orchestrator's approval unless you hold a direct "
                       "grant. Returns the runner's receipt, or a request_id to poll.",
        "inputSchema": {"type": "object", "required": ["dispatch", "path", "content", "base_sha256", "rationale"],
                        "additionalProperties": False,
                        "properties": {"dispatch": _NONCE, "path": {"type": "string"}, "content": {"type": "string"},
                                       "base_sha256": {"type": "string"}, "rationale": {"type": "string"},
                                       "gates": {"type": "array", "items": {"type": "string"}}}}},
    "delete_file": {
        "description": "Ask to delete a file. Always staged for the orchestrator's approval.",
        "inputSchema": {"type": "object", "required": ["dispatch", "path", "base_sha256", "rationale"],
                        "additionalProperties": False,
                        "properties": {"dispatch": _NONCE, "path": {"type": "string"},
                                       "base_sha256": {"type": "string"}, "rationale": {"type": "string"}}}},
    "run_command": {
        "description": "Run an allowlisted command (argv list, never a shell string) in your sandbox, through the "
                       "runner. stdin content is passed only via stdin_path + stdin_content and is gated.",
        "inputSchema": {"type": "object", "required": ["dispatch", "argv", "purpose"], "additionalProperties": False,
                        "properties": {"dispatch": _NONCE, "argv": {"type": "array", "items": {"type": "string"}},
                                       "purpose": {"type": "string"}, "cwd": {"type": "string"},
                                       "expected_writes": {"type": "array", "items": {"type": "string"}},
                                       "stdin_path": {"type": "string"}, "stdin_content": {"type": "string"}}}},
    "request_status": {
        "description": "The result of a request this server queued earlier, or that it is still pending.",
        "inputSchema": {"type": "object", "required": ["request_id"], "additionalProperties": False,
                        "properties": {"request_id": {"type": "string"}}}},
}


class ToolError(Exception):
    """A refusal reported to the agent as an error result."""


class Server:
    """One agent's ``agentteams_runner`` instance."""

    def __init__(self, root: Path, agent: str, approval: str, tools: list[str], wait: float) -> None:
        self.root = Path(os.path.realpath(root))
        self.agent = agent
        self.approval = approval
        self.tools = [t for t in TOOLS if t in tools]
        self.wait = wait
        self.mine: set[str] = set()

    # -- queue client (mirrors agentteams.proposal_runner's, without importing it) --------------------------

    def _alive(self) -> bool:
        try:
            return time.time() - float((self.root / HEARTBEAT).read_text()) < HEARTBEAT_STALE_SECONDS
        except (OSError, ValueError):
            return False

    def _enqueue(self, request: dict[str, Any]) -> str:
        if not self._alive():
            raise ToolError("no runner is serving this project; ask the orchestrator (the operator starts it)")
        data = json.dumps({**request, "via_agent": self.agent}).encode("utf-8")
        if len(data) > REQUEST_MAX_BYTES:
            raise ToolError(f"request is {len(data)} bytes, over the {REQUEST_MAX_BYTES}-byte limit")
        directory = self.root / MCP_REQUESTS
        if directory.is_symlink() or not directory.is_dir():
            raise ToolError(f"{MCP_REQUESTS} is missing or not a plain directory")
        request_id = secrets.token_hex(16)
        tmp = directory / f".{request_id}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        os.rename(tmp, directory / f"{request_id}.json")
        self.mine.add(request_id)
        return request_id

    def _poll(self, request_id: str) -> dict[str, Any] | None:
        path = self.root / RESULTS / f"{request_id}.json"
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if not self._alive():
                raise ToolError("the runner stopped answering; ask the orchestrator to restart it") from None
            return None
        try:
            with os.fdopen(fd, "rb") as fh:
                data = fh.read(RESULT_MAX_BYTES + 1)
        except OSError as exc:
            raise ToolError(f"cannot read the result: {exc}") from exc
        if len(data) > RESULT_MAX_BYTES:
            raise ToolError("the runner's result is too large to read")
        result = json.loads(data)
        ack = self.root / ACKS / request_id
        try:
            os.close(os.open(ack, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600))
        except OSError as exc:  # the runner expires unacknowledged results on its own; say so rather than hide it
            result = {**result, "ack_error": str(exc)} if isinstance(result, dict) else result
        self.mine.discard(request_id)
        return result

    def _submit(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = self._enqueue(request)
        deadline = time.monotonic() + self.wait
        while time.monotonic() < deadline:
            result = self._poll(request_id)
            if result is not None:
                return result
            time.sleep(0.25)
        return {"request_id": request_id, "status": "queued",
                "note": "the runner hasn't answered yet; call request_status with this request_id"}

    # -- tools ----------------------------------------------------------------------------------------------

    def call(self, name: str, args: dict[str, Any]) -> Any:
        if name not in self.tools:
            raise ToolError(f"tool {name!r} is not granted to {self.agent}")
        if not isinstance(args, dict):
            raise ToolError("arguments must be an object")
        if name == "read_file_hashed":
            return self._read_hashed(args.get("path"))
        if name == "request_status":
            request_id = args.get("request_id")
            if request_id not in self.mine:
                raise ToolError("unknown request_id for this server (only ids it queued, and not yet answered)")
            result = self._poll(str(request_id))
            return result if result is not None else {"request_id": request_id, "status": "queued"}
        nonce = args.get("dispatch")
        if name == "write_file":
            artifact = {"kind": "change-proposal", "dispatch": nonce, "path": args.get("path"),
                        "content": args.get("content"), "base_sha256": args.get("base_sha256"),
                        "rationale": args.get("rationale"), **({"gates": args["gates"]} if "gates" in args else {})}
            kind = "apply-direct" if self.approval == "direct" else "stage-proposal"
            return self._submit({"kind": kind, "artifact": artifact})
        if name == "delete_file":
            artifact = {"kind": "delete-proposal", "dispatch": nonce, "path": args.get("path"),
                        "base_sha256": args.get("base_sha256"), "rationale": args.get("rationale")}
            return self._submit({"kind": "stage-proposal", "artifact": artifact})  # deletes are always staged
        artifact = {"kind": "command-request", "dispatch": nonce, "argv": args.get("argv"),
                    "purpose": args.get("purpose")}
        for key in ("cwd", "expected_writes"):
            if key in args:
                artifact[key] = args[key]
        if "stdin_path" in args or "stdin_content" in args:
            artifact["stdin_from_content"] = {"path": args.get("stdin_path"), "content": args.get("stdin_content")}
        return self._submit({"kind": "run-request", "artifact": artifact})

    def _read_hashed(self, rel: Any) -> dict[str, Any]:
        if not isinstance(rel, str) or not rel or "\0" in rel or rel.startswith(("/", "~")):
            raise ToolError("path must be a relative path inside the project")
        parts = Path(rel).parts
        for part in parts:
            low = part.lower()
            if (low in DENY_PARTS or low in DENY_NAMES or low.endswith(DENY_SUFFIXES)
                    or low.startswith(DENY_PREFIXES)) and low not in (".env.example", ".env.sample", ".env.template"):
                raise ToolError(f"{rel} is not readable through this server")
        real = os.path.realpath(self.root / rel)
        if not (real == str(self.root) or real.startswith(str(self.root) + os.sep)):
            raise ToolError("path resolves outside the project")
        try:
            fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {"path": rel, "exists": False, "base_sha256": "absent"}
        except OSError as exc:
            raise ToolError(f"cannot open {rel}: {exc}") from exc
        with os.fdopen(fd, "rb") as fh:
            if not stat_mod.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise ToolError(f"{rel} is not a regular file")
            data = fh.read(READ_MAX_BYTES + 1)
        if len(data) > READ_MAX_BYTES:
            raise ToolError(f"{rel} is over {READ_MAX_BYTES} bytes; this server reads whole files only up to that")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"{rel} is not UTF-8 text") from exc
        return {"path": rel, "exists": True, "content": text, "base_sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data)}


def _reply(msg_id: Any, *, result: Any = None, error: dict[str, Any] | None = None) -> None:
    out = {"jsonrpc": "2.0", "id": msg_id}
    out.update({"error": error} if error is not None else {"result": result})
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def serve(server: Server) -> int:
    """Serve JSON-RPC requests on stdin until it closes."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _reply(None, error={"code": -32700, "message": "parse error"})
            continue
        if not isinstance(msg, dict) or msg.get("id") is None:
            continue  # notifications need no reply
        method, params = msg.get("method"), msg.get("params") or {}
        if not isinstance(params, dict):
            _reply(msg["id"], error={"code": -32602, "message": "params must be an object"})
        elif method == "initialize":
            _reply(msg["id"], result={"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                                      "capabilities": {"tools": {}},
                                      "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
        elif method == "tools/list":
            _reply(msg["id"], result={"tools": [{"name": t, **SCHEMAS.get(t, {})} for t in server.tools
                                                if t in SCHEMAS or t == "request_status"]})
        elif method == "tools/call":
            try:
                value = server.call(str(params.get("name")), params.get("arguments") or {})
                _reply(msg["id"], result={"content": [{"type": "text", "text": json.dumps(value, indent=1)}]})
            except ToolError as exc:
                _reply(msg["id"], result={"content": [{"type": "text", "text": str(exc)}], "isError": True})
        elif method == "ping":
            _reply(msg["id"], result={})
        else:
            _reply(msg["id"], error={"code": -32601, "message": f"unknown method {method!r}"})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=SERVER_NAME)
    parser.add_argument("--root", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--approval", choices=("staged", "direct"), default="staged")
    parser.add_argument("--tools", required=True, help="comma-separated granted tools")
    parser.add_argument("--wait", type=float, default=20.0, help="seconds a call waits for the runner")
    args = parser.parse_args(argv)
    tools = [t for t in args.tools.split(",") if t]
    unknown = [t for t in tools if t not in TOOLS]
    if unknown or not tools:
        parser.error(f"--tools must be a non-empty list from {', '.join(TOOLS)}")
    return serve(Server(Path(args.root), args.agent, args.approval, tools, max(0.0, min(args.wait, 120.0))))


if __name__ == "__main__":
    sys.exit(main())
