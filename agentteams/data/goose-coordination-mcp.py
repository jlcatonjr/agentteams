#!/usr/bin/env python3
"""agentteams coordination MCP server (stdio, stdlib-only) for generated Goose teams.

WHY THIS EXISTS
---------------
Cross-repo coordination on Goose has no native construct: an agent advised to coordinate
with another repo has no legal recipe field to carry it, so the instruction degrades to
prose and the model reasons toward a tool call it cannot place (a leading trigger of the
"dead-turn" leak; see scripts/goose-openrouter-route-proxy.py). This server gives the
coordinator/liaison recipes a REAL, structured tool to call instead — turning that prose
into an auditable, file-based transport that mirrors the Claude coordination model
(adjacent-repos registry + append-only CSV ledgers + operator-transported Coordination
Requests).

SAFETY MODEL (read this before extending)
-----------------------------------------
This is the FILE-BASED, human-in-the-loop model. Every tool here only READS or RECORDS:
it files a request artifact, appends a ledger row, or records that clearance is being
requested. **No tool grants clearance, authorizes a destructive action, or executes
anything in another repo.** A human (or a human-driven @security / @repo-liaison agent)
still reviews requests and relays the second hop. Autonomous execution of cross-repo
actions is a SEPARATE, security-gated design that was HALTED at review
(references/plans/goose-autonomous-handoff-authorization.design.md) — do not add an
"execute" or "grant" tool here without that design passing re-review.

All writes are confined to the team's references directory (path-contained; a target that
escapes it is refused). Transport is newline-delimited JSON-RPC 2.0 over stdin/stdout
(the MCP stdio convention) — NO sockets/network, so the server survives a confined
profile's ``deny network*`` boundary.

This file is shipped verbatim into generated Goose teams (read-from-disk, never
inline-duplicated) and is self-contained: it does NOT import ``agentteams`` at runtime
(``scripts/`` is excluded from the packaged dist), so the small CSV-append logic is
inlined here on purpose.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

#: Ledger header schemas — kept byte-identical to agentteams/liaison_logs.py so a row this
#: server appends is indistinguishable from one the Claude path writes.
COORD_LOG_HEADERS = ["date", "adjacent_repo", "direction", "outcome"]
CHANGELOG_HEADERS = ["date", "repo_name", "action", "files_changed", "summary"]
SECURITY_DECISIONS_HEADERS = [
    "timestamp",
    "requesting_agent",
    "action_reviewed",
    "verdict",
    "conditions",
    "conditions_verified",
]

COORD_LOG_CSV = "adjacent-repos-coordination-log.csv"
SECURITY_DECISIONS_CSV = "security-decisions.log.csv"
COORD_REQUESTS_DIR = "cross-orchestrator-requests"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentteams_coordination"
SERVER_VERSION = "1.0.0"


def _utc_date() -> str:
    """Return today's date as ``YYYY-MM-DD`` (UTC)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp (seconds precision, trailing ``Z``)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: str, *, max_len: int = 48) -> str:
    """Return a filesystem-safe kebab slug of ``text`` (lowercased, alnum + dashes)."""
    s = _SLUG_RE.sub("-", (text or "").strip().lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "untitled"


def _resolve_refs_dir(explicit: str | None) -> Path:
    """Resolve the team's references directory, creating it if absent.

    Order: an explicit ``--refs-dir`` wins; else ``.goose/recipes/references`` (where the
    Goose adapter lands generated reference docs and ledger stubs) when its parent exists;
    else repo-root ``references``. The chosen directory is created if missing so a first
    write never fails on a fresh checkout.

    Args:
        explicit: The ``--refs-dir`` value, or None to auto-detect from the cwd.

    Returns:
        The resolved, existing references directory as an absolute Path.
    """
    if explicit:
        d = Path(explicit).resolve()
    else:
        goose_refs = Path.cwd() / ".goose" / "recipes" / "references"
        d = goose_refs if goose_refs.parent.is_dir() else (Path.cwd() / "references")
        d = d.resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _contained(root: Path, candidate: Path) -> bool:
    """Return True iff ``candidate`` resolves to ``root`` or a path beneath it.

    Symlink/``..``-safe: both sides are resolved before comparison. Used to refuse any
    write target that would escape the references directory.
    """
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _append_csv_row(refs_dir: Path, filename: str, headers: list[str], row: dict[str, str]) -> Path:
    """Append one row to ``refs_dir/filename``, writing the header first if new.

    Atomic: the full contents (existing rows + the new one) are rendered in memory and
    written to a temp file that is then ``os.replace``'d over the target, so a concurrent
    reader never sees a half-written file. Missing fields are written empty; unknown keys
    are ignored (the header is the schema of record).

    Raises:
        ValueError: if the resolved target escapes ``refs_dir`` (path containment).
    """
    target = refs_dir / filename
    if not _contained(refs_dir, target):
        raise ValueError(f"refused: target escapes references dir: {filename}")

    existing: list[list[str]] = []
    if target.is_file():
        with target.open("r", encoding="utf-8", newline="") as fh:
            existing = [r for r in csv.reader(fh) if r]

    buf = io.StringIO()
    w = csv.writer(buf)
    if not existing or existing[0] != headers:
        w.writerow(headers)
        # If the file existed with a different/legacy header, preserve its data rows as-is.
        data_rows = existing[1:] if existing else []
    else:
        data_rows = existing[1:]
    for r in data_rows:
        w.writerow(r)
    w.writerow([row.get(h, "") for h in headers])

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    os.replace(tmp, target)
    return target


# --------------------------------------------------------------------------------------
# Tool implementations — each returns a human-readable status string. READ or RECORD only.
# --------------------------------------------------------------------------------------

def _tool_read_adjacent_registry(refs_dir: Path, args: dict[str, Any]) -> str:
    """Read the adjacent-repos registry so the agent knows which repos it may coordinate with.

    Returns the registry markdown (``adjacent-repos.md``) plus a short index of the
    coordination ledgers present. Read-only.
    """
    registry = refs_dir / "adjacent-repos.md"
    parts: list[str] = []
    if registry.is_file():
        parts.append(f"# adjacent-repos.md\n{registry.read_text(encoding='utf-8')}")
    else:
        parts.append(
            "adjacent-repos.md not found in this team's references directory. "
            "This team may not have a registered adjacent-repos registry yet."
        )
    ledgers = [p.name for p in sorted(refs_dir.glob("*.csv"))]
    parts.append(f"\nledgers present: {', '.join(ledgers) if ledgers else '<none>'}")
    return "\n".join(parts)


def _tool_file_coordination_request(refs_dir: Path, args: dict[str, Any]) -> str:
    """Write a Coordination Request markdown artifact for another repo's orchestrator.

    Since there is NO automated cross-repo spawn substrate on any harness, this
    operator-transported artifact IS the transport: it records what another repo must do.
    It does not send, execute, or authorize anything.
    """
    target_repo = str(args.get("target_repo") or "").strip()
    task = str(args.get("task") or "").strip()
    if not target_repo or not task:
        return "ERROR: both 'target_repo' and 'task' are required."
    requesting_repo = str(args.get("requesting_repo") or Path.cwd().name).strip()
    details = str(args.get("details") or "").strip()

    reqs_dir = refs_dir / COORD_REQUESTS_DIR
    reqs_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{_utc_date()}-{_slug(target_repo)}-{_slug(task)}.md"
    target = reqs_dir / fname
    if not _contained(refs_dir, target):
        return "ERROR: refused: request path escapes references dir."

    body = (
        f"# Coordination Request → {target_repo}\n\n"
        f"- **Filed:** {_utc_timestamp()}\n"
        f"- **From (requesting repo):** {requesting_repo}\n"
        f"- **To (target repo):** {target_repo}\n"
        f"- **Status:** awaiting (operator-transported; a human relays this to the target)\n\n"
        f"## Task\n\n{task}\n\n"
        f"## Details\n\n{details or '(none provided)'}\n\n"
        "---\n"
        "*Filed by the agentteams coordination MCP server (file-based model). This artifact "
        "requests work in another repository; it does not execute or authorize it. A human "
        "(or @repo-liaison under @security clearance) relays and acts on it.*\n"
    )
    target.write_text(body, encoding="utf-8")
    return f"Filed coordination request: {target.relative_to(refs_dir.parent) if _contained(refs_dir.parent, target) else target}"


def _tool_append_coordination_log(refs_dir: Path, args: dict[str, Any]) -> str:
    """Append one row to the coordination log (adjacent-repos-coordination-log.csv).

    Records an outbound/inbound coordination exchange. Record-keeping only.
    """
    adjacent_repo = str(args.get("adjacent_repo") or "").strip()
    direction = str(args.get("direction") or "").strip()
    outcome = str(args.get("outcome") or "").strip()
    if not adjacent_repo or not direction:
        return "ERROR: 'adjacent_repo' and 'direction' are required."
    if direction not in {"outbound", "inbound"}:
        return "ERROR: 'direction' must be 'outbound' or 'inbound'."
    row = {
        "date": _utc_date(),
        "adjacent_repo": adjacent_repo,
        "direction": direction,
        "outcome": outcome or "awaiting",
    }
    _append_csv_row(refs_dir, COORD_LOG_CSV, COORD_LOG_HEADERS, row)
    return f"Recorded {direction} coordination with {adjacent_repo} (outcome: {row['outcome']})."


def _tool_request_security_clearance(refs_dir: Path, args: dict[str, Any]) -> str:
    """Record a REQUEST for @security clearance of a cross-repo action.

    Appends a row to security-decisions.log.csv with verdict ``REQUESTED`` and
    conditions_verified ``no``. This does NOT grant clearance — a human @security reviewer
    appends the actual verdict. The tool exists so a coordinator cannot proceed on an
    unreviewed cross-repo write without leaving an auditable request.
    """
    requesting_agent = str(args.get("requesting_agent") or "").strip()
    action_reviewed = str(args.get("action") or "").strip()
    if not requesting_agent or not action_reviewed:
        return "ERROR: 'requesting_agent' and 'action' are required."
    row = {
        "timestamp": _utc_timestamp(),
        "requesting_agent": requesting_agent,
        "action_reviewed": action_reviewed,
        "verdict": "REQUESTED",
        "conditions": str(args.get("conditions") or ""),
        "conditions_verified": "no",
    }
    _append_csv_row(refs_dir, SECURITY_DECISIONS_CSV, SECURITY_DECISIONS_HEADERS, row)
    return (
        "Recorded a security-clearance REQUEST (verdict=REQUESTED). This is NOT clearance — "
        "a human @security reviewer must append the verdict before the action may proceed."
    )


#: Tool registry: name -> (handler, description, JSON-Schema inputSchema). side_effects are
#: read/write only — never destructive, never "execute".
_TOOLS: dict[str, dict[str, Any]] = {
    "read_adjacent_registry": {
        "handler": _tool_read_adjacent_registry,
        "description": "Read this team's adjacent-repos registry and list coordination ledgers (read-only).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "file_coordination_request": {
        "handler": _tool_file_coordination_request,
        "description": (
            "File a Coordination Request markdown artifact asking another repo's orchestrator to "
            "do a task. Operator-transported: records the request; does not send or execute it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_repo": {"type": "string", "description": "The repo the request is for."},
                "task": {"type": "string", "description": "What the target repo should do."},
                "requesting_repo": {"type": "string", "description": "This repo (defaults to cwd name)."},
                "details": {"type": "string", "description": "Optional supporting detail."},
            },
            "required": ["target_repo", "task"],
            "additionalProperties": False,
        },
    },
    "append_coordination_log": {
        "handler": _tool_append_coordination_log,
        "description": "Append an outbound/inbound row to adjacent-repos-coordination-log.csv (record-keeping).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "adjacent_repo": {"type": "string"},
                "direction": {"type": "string", "enum": ["outbound", "inbound"]},
                "outcome": {"type": "string"},
            },
            "required": ["adjacent_repo", "direction"],
            "additionalProperties": False,
        },
    },
    "request_security_clearance": {
        "handler": _tool_request_security_clearance,
        "description": (
            "Record a REQUEST for @security clearance of a cross-repo action (verdict=REQUESTED). "
            "Does NOT grant clearance; a human reviewer appends the verdict."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "requesting_agent": {"type": "string"},
                "action": {"type": "string", "description": "The cross-repo action needing review."},
                "conditions": {"type": "string"},
            },
            "required": ["requesting_agent", "action"],
            "additionalProperties": False,
        },
    },
}


def handle_request(msg: dict[str, Any], refs_dir: Path) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC request; return the response object, or None for a notification.

    Implements the MCP subset the coordinator recipes use: ``initialize``, ``tools/list``,
    ``tools/call``. Unknown methods return a JSON-RPC ``-32601`` error. A tool that raises
    is reported as an ``isError`` tool result (never a server crash).
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
        return None  # notification: no response
    if method == "tools/list":
        return _ok(msg_id, {
            "tools": [
                {"name": n, "description": t["description"], "inputSchema": t["inputSchema"]}
                for n, t in _TOOLS.items()
            ]
        })
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = _TOOLS.get(name)
        if tool is None:
            return _tool_result(msg_id, f"ERROR: unknown tool '{name}'.", is_error=True)
        handler: Callable[[Path, dict[str, Any]], str] = tool["handler"]
        try:
            text = handler(refs_dir, args)
        except Exception as exc:  # noqa: BLE001 - report as tool error, never crash the server
            return _tool_result(msg_id, f"ERROR: {type(exc).__name__}: {exc}", is_error=True)
        is_error = text.startswith("ERROR:")
        return _tool_result(msg_id, text, is_error=is_error)

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


def serve(refs_dir: Path, stdin: io.TextIOBase, stdout: io.TextIOBase) -> None:
    """Run the newline-delimited JSON-RPC 2.0 stdio loop until stdin closes.

    Each input line is one JSON-RPC message; each response is one JSON line. Malformed
    input yields a ``-32700`` parse error (with a null id) rather than crashing the loop.
    """
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
        response = handle_request(msg, refs_dir)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. ``--refs-dir`` overrides references-dir auto-detection."""
    parser = argparse.ArgumentParser(description="agentteams coordination MCP server (stdio).")
    parser.add_argument("--refs-dir", default=None, help="References directory (default: auto-detect).")
    ns = parser.parse_args(argv)
    refs_dir = _resolve_refs_dir(ns.refs_dir)
    serve(refs_dir, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
