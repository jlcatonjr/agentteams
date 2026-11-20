"""Trace normaliser for the workflow-conformance test (Phase A, request item 3, normaliser only).

Turns what a host recorded into one ordered event list plus an ``agent_session_trajectory`` packet, so the
deterministic scorer (:mod:`agentteams.redteam.workflow_score`) and :func:`behavioral_drift.detect_behavioral_drift`
can both read it. Phase A runs it only on recorded and replayed fixtures; the live recorder that produces the input
is Phase B.

Inputs:

* **Claude Code** ``--output-format stream-json`` lines. ``tool_use`` items named ``Task`` or ``Agent`` are
  invocations (``input.subagent_type``); ``Write``, ``Edit``, ``MultiEdit`` and ``NotebookEdit`` are writes;
  ``Bash`` is destructive when it matches :data:`sandbox_handoff.DESTRUCTIVE_PATTERNS` (one shared pattern list,
  not a copy), and it writes the redirect and ``tee`` targets it names, plus any plan, steps-CSV or work-summary
  path in a command that writes (``write_steps``, ``sed -i``, ``cp``...). Messages with ``parent_tool_use_id`` are
  a subagent's: their writes and commands count, their prose and ``Task`` calls do not. A workflow entry is a line
  that opens with ``Workflow <id>`` or says ``Entering/Starting/Running/Invoking Workflow <id>``. Assistant text that opens a line with ``Workflow <id>`` is a workflow entry.
* **goose** session JSONL. ``toolRequest`` items: ``developer__text_editor`` with a writing command is a write,
  ``developer__shell`` is classified like ``Bash``, and a tool whose name (after the last ``__``, ``_`` read as
  ``-``) is a roster agent is an invocation of that sub-recipe. This shape is checked only against fixtures here;
  Phase B must confirm it against real goose sessions before scoring them.
* The gate hook's ``AGENTTEAMS_GATE_LOG``: a ``deny`` or ``crash-deny`` record marks the event with the same
  ``tool_use_id`` as denied (not executed). The log is evidence; a missing line changes nothing.
* The final filesystem state: ``{repo-relative path: text}`` for steps CSVs. A steps CSV whose rows are all
  ``done`` adds a ``plan-complete`` event right after the last write to it.

Read content is data (rail S6): nothing in a trace is interpreted beyond these fields. stdlib only.
"""
from __future__ import annotations

import csv
import io
import json
import posixpath
import re
from collections.abc import Iterable
from typing import Any

from agentteams.sandbox_handoff import DESTRUCTIVE_PATTERNS

ORCHESTRATOR = "orchestrator"
INVOKE = "invoke"
WRITE = "write"
DESTRUCTIVE = "destructive"
WORKFLOW = "workflow"
PLAN_COMPLETE = "plan-complete"

_CLAUDE_INVOKE_TOOLS = frozenset({"Task", "Agent"})
_CLAUDE_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_GOOSE_WRITE_COMMANDS = frozenset({"write", "str_replace", "insert", "create", "undo_edit"})
_DENIED = frozenset({"deny", "crash-deny"})
_WORKFLOW_ENTRY_RE = re.compile(
    r"(?:^(?:#+\s*|\*\*)?|\b(?:[Ee]ntering|[Ss]tarting|[Rr]unning|[Ii]nvoking|[Nn]ow in) )Workflow (\d+[A-Z]?)\b", re.M)
#: A shell command that writes: a redirect, tee, an in-place edit, a copy or move, or the plan/CSV helpers.
_SHELL_WRITE_HINT_RE = re.compile(r">|\btee\b|\bsed\s+-i|\b(?:cp|mv|touch)\s|write_steps|update_step|atomic_rewrite")
_REDIRECT_RE = re.compile(r"(?:^|[^>&0-9-])>{1,2}\s*([^\s;&|<>()'\"]+)")
_TEE_RE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;&|<>()'\"]+)")
_TRACKED_PATH_RE = re.compile(r"[\w./~-]*(?:tmp/by-week/\d{4}-W\d{2}/[\w.-]+\.(?:plan\.md|steps\.csv)|workSummaries/[\w./-]+)")
_PLAN_RE = re.compile(r"(?:^|/)tmp/by-week/\d{4}-W\d{2}/[^/]+\.(plan\.md|steps\.csv)$")


def host_agent_slug(name: str) -> str:
    """Normalise a host's agent name to a roster slug.

    Args:
        name: ``"conflict-auditor"``, ``"Security — AgentTeamsModule"``, ``"code_hygiene"`` and the like.

    Returns:
        The lower-case hyphenated slug: ``"security"``, ``"code-hygiene"``.
    """
    base = name.split(" — ")[0]
    base = re.sub(r"\([^)]*\)", "", base).strip().lower()
    return re.sub(r"[\s_]+", "-", base)


def _relative(path: str, work_tree: str) -> tuple[str, bool]:
    """Return ``(repo-relative path, outside_work_tree)``."""
    norm = posixpath.normpath(path)
    root = posixpath.normpath(work_tree)
    if posixpath.isabs(norm):
        if norm == root or norm.startswith(root + "/"):
            return posixpath.relpath(norm, root), False
        return norm, True
    return norm, norm == ".." or norm.startswith(("../", "~"))


def _write_event(path: str, work_tree: str, tool_use_id: str | None) -> dict[str, Any]:
    rel, outside = _relative(path, work_tree)
    m = _PLAN_RE.search(rel)
    return {"kind": WRITE, "path": rel, "cross_repo": outside, "tool_use_id": tool_use_id,
            "plan_artifact": (m.group(1).split(".")[0] if m else None)}


def _shell_events(command: str, work_tree: str, tool_use_id: str | None) -> list[dict[str, Any]]:
    """Events for one shell command: the files it visibly writes, then a destructive event if it matches."""
    events: list[dict[str, Any]] = []
    if _SHELL_WRITE_HINT_RE.search(command):
        paths = _REDIRECT_RE.findall(command) + _TEE_RE.findall(command) + _TRACKED_PATH_RE.findall(command)
        seen: set[str] = set()
        for path in paths:
            if path in seen or path.startswith("/dev/") or path.isdigit():
                continue
            seen.add(path)
            events.append(_write_event(path, work_tree, tool_use_id))
    for kind, pattern in DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            events.append({"kind": DESTRUCTIVE, "detail": kind, "tool_use_id": tool_use_id})
            break
    return events


def _workflow_events(text: str) -> list[dict[str, Any]]:
    return [{"kind": WORKFLOW, "workflow": m.group(1)} for m in _WORKFLOW_ENTRY_RE.finditer(text)]


def _parse_lines(lines: Iterable[str], skipped: list[int]) -> Iterable[Any]:
    """Yield each parsed JSON line; count a non-blank line that is not JSON in ``skipped[0]``."""
    for line in lines:
        if not str(line).strip():
            continue
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, TypeError):
            skipped[0] += 1


def events_from_claude_stream(lines: Iterable[str], work_tree: str,
                              skipped: list[int] | None = None) -> list[dict[str, Any]]:
    """Extract events from Claude Code ``stream-json`` output.

    Args:
        lines: The stream, one JSON object per line; blank and non-JSON lines are skipped.
        work_tree: Absolute path of the fresh tree the run worked in.
        skipped: A one-item counter of non-blank lines that were not JSON (the scorer voids such a trace).

    Returns:
        Events in stream order (no ``seq`` yet; :func:`normalise` numbers them).
    """
    events: list[dict[str, Any]] = []
    for obj in _parse_lines(lines, skipped if skipped is not None else [0]):
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        # A subagent's own messages carry parent_tool_use_id: its writes and commands are real, but its prose and
        # any Task call are not the orchestrator's workflow entries or invocations.
        sub_agent = bool(obj.get("parent_tool_use_id"))
        content = (obj.get("message") or {}).get("content") or []
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                if not sub_agent:
                    events.extend(_workflow_events(str(item.get("text", ""))))
                continue
            if item.get("type") != "tool_use":
                continue
            name, args, tid = item.get("name"), item.get("input") or {}, item.get("id")
            if name in _CLAUDE_INVOKE_TOOLS and args.get("subagent_type"):
                if sub_agent:
                    continue
                events.append({"kind": INVOKE, "agent": host_agent_slug(str(args["subagent_type"])), "tool_use_id": tid})
            elif name in _CLAUDE_WRITE_TOOLS and (args.get("file_path") or args.get("notebook_path")):
                events.append(_write_event(str(args.get("file_path") or args.get("notebook_path")), work_tree, tid))
            elif name == "Bash":
                events.extend(_shell_events(str(args.get("command", "")), work_tree, tid))
    return events


def events_from_goose_session(lines: Iterable[str], work_tree: str, roster: set[str],
                              skipped: list[int] | None = None) -> list[dict[str, Any]]:
    """Extract events from a goose session JSONL.

    Args:
        lines: The session file, one message per line.
        work_tree: Absolute path of the fresh tree the run worked in.
        roster: The team's agent slugs; a tool named after one is a sub-recipe invocation.
        skipped: A one-item counter of non-blank lines that were not JSON.

    Returns:
        Events in session order.
    """
    events: list[dict[str, Any]] = []
    for obj in _parse_lines(lines, skipped if skipped is not None else [0]):
        content = obj.get("content") if isinstance(obj, dict) else None
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and obj.get("role") == "assistant":
                events.extend(_workflow_events(str(item.get("text", ""))))
                continue
            if item.get("type") != "toolRequest":
                continue
            call = ((item.get("toolCall") or {}).get("value")) or {}
            name, args, tid = str(call.get("name", "")), call.get("arguments") or {}, item.get("id")
            if name.endswith("developer__text_editor"):
                if args.get("command") in _GOOSE_WRITE_COMMANDS and args.get("path"):
                    events.append(_write_event(str(args["path"]), work_tree, tid))
            elif name.endswith("developer__shell"):
                events.extend(_shell_events(str(args.get("command", "")), work_tree, tid))
            else:
                slug = host_agent_slug(name.rsplit("__", 1)[-1])
                if slug in roster:
                    events.append({"kind": INVOKE, "agent": slug, "tool_use_id": tid})
    return events


def apply_gate_log(events: list[dict[str, Any]], gate_lines: Iterable[str],
                   skipped: list[int] | None = None) -> list[dict[str, Any]]:
    """Mark events the gate denied (by ``tool_use_id``).

    Args:
        events: Events from a host extractor.
        gate_lines: ``AGENTTEAMS_GATE_LOG`` lines.
        skipped: A one-item counter of gate-log lines that were not JSON (they void the score, like trace lines).

    Returns:
        The same events, each with ``denied`` set.
    """
    denied: set[str] = set()
    for rec in _parse_lines(gate_lines, skipped if skipped is not None else [0]):
        if isinstance(rec, dict) and rec.get("decision") in _DENIED and rec.get("tool_use_id"):
            denied.add(str(rec["tool_use_id"]))
    for ev in events:
        ev["denied"] = bool(ev.get("tool_use_id")) and ev.get("tool_use_id") in denied
    return events


def _all_done(text: str) -> bool:
    rows = list(csv.DictReader(io.StringIO(text)))
    return bool(rows) and all((r.get("status") or "").strip() == "done" for r in rows)


def add_plan_completion(events: list[dict[str, Any]], final_state: dict[str, str]) -> list[dict[str, Any]]:
    """Insert a ``plan-complete`` event after the last write to each all-``done`` steps CSV.

    When the trace shows no write to that CSV, the event is appended with ``position: unknown``.

    Args:
        events: Events with ``denied`` set.
        final_state: ``{repo-relative path: text}`` of the steps CSVs at the end of the run.

    Returns:
        A new event list.
    """
    out = list(events)
    for path, text in sorted(final_state.items()):
        if not path.endswith(".steps.csv") or not _all_done(text):
            continue
        last = max((i for i, e in enumerate(out)
                    if e["kind"] == WRITE and e.get("path") == path and not e.get("denied")), default=None)
        if last is not None:
            out.insert(last + 1, {"kind": PLAN_COMPLETE, "path": path, "denied": False, "position": "known"})
        else:  # completed by a write the trace does not show: record it, position unknown (Rule 10 still applies)
            out.append({"kind": PLAN_COMPLETE, "path": path, "denied": False, "position": "unknown"})
    return out


def to_trajectory(events: list[dict[str, Any]], session_slug: str) -> dict[str, Any]:
    """Build an ``agent_session_trajectory`` from executed invocations.

    Each invocation is a round trip (orchestrator -> agent -> orchestrator), so the edge list is one contiguous
    chain and every edge is ``mediated_by`` the orchestrator, as :mod:`behavioral_drift` expects.

    Args:
        events: Normalised events.
        session_slug: The run's identifier.

    Returns:
        ``{"session_slug", "root_agent", "handoff_edges"}``.
    """
    edges: list[dict[str, Any]] = []
    for ev in events:
        if ev["kind"] != INVOKE or ev.get("denied"):
            continue
        for frm, to in ((ORCHESTRATOR, ev["agent"]), (ev["agent"], ORCHESTRATOR)):
            edges.append({"sequence": len(edges) + 1, "from_agent": frm, "to_agent": to,
                          "mediated_by": ORCHESTRATOR})
    return {"session_slug": session_slug, "root_agent": ORCHESTRATOR, "handoff_edges": edges}


def normalise(host: str, lines: Iterable[str], *, work_tree: str, session_slug: str,
              roster: set[str] | None = None, gate_lines: Iterable[str] = (),
              final_state: dict[str, str] | None = None) -> dict[str, Any]:
    """Normalise one recorded run.

    Args:
        host: ``"claude"`` or ``"goose"``.
        lines: The host's trace.
        work_tree: Absolute path of the fresh tree.
        session_slug: The run's identifier.
        roster: Agent slugs (required for goose).
        gate_lines: ``AGENTTEAMS_GATE_LOG`` lines, if any.
        final_state: Steps CSV texts at the end of the run.

    Returns:
        An ``agent_session_trajectory`` with an added ``events`` list (each numbered by ``seq``) and
        ``skipped_lines``, the count of trace and gate-log lines that were not JSON.

    Raises:
        ValueError: For an unknown host.
    """
    skipped = [0]
    if host == "claude":
        events = events_from_claude_stream(lines, work_tree, skipped)
    elif host == "goose":
        events = events_from_goose_session(lines, work_tree, roster or set(), skipped)
    else:
        raise ValueError(f"unknown host {host!r}")
    events = add_plan_completion(apply_gate_log(events, gate_lines, skipped), final_state or {})
    for i, ev in enumerate(events, 1):
        ev["seq"] = i
    trajectory = to_trajectory(events, session_slug)
    trajectory["events"] = events
    trajectory["skipped_lines"] = skipped[0]
    return trajectory


__all__ = [
    "DESTRUCTIVE",
    "INVOKE",
    "ORCHESTRATOR",
    "PLAN_COMPLETE",
    "WORKFLOW",
    "WRITE",
    "add_plan_completion",
    "host_agent_slug",
    "apply_gate_log",
    "events_from_claude_stream",
    "events_from_goose_session",
    "normalise",
    "to_trajectory",
]
