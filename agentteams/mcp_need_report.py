"""mcp_need_report.py — ``agentteams --mcp-need-report``: the evidence side of the agent MCP-need protocol.

Phase N3 of ``references/plans/agent-mcp-need-protocol.design.md``. Under ``write_policy: "orchestrator-only"``
a non-orchestrator agent's need for an MCP server is decided from the runner's ledger, never from the agent's
own account (``references/mcp-need.reference.md``). This module reads the ledger and the need register and
reports, per agent:

* repeated commands, grouped by their leading words, with count, total and median time, and failures
  (non-zero exit, timeout, undeclared writes, killed survivors);
* runs that started but never finished;
* proposal bytes the orchestrator had to carry;
* refused requests (the ledger keeps a reason, not the command);
* open register rows, as the register itself labels them.

It is **read-only**: it never writes, and it never decides. For each repeated command it lists the runner-path
fix first (a broader command entry, a gate or a pre-built artifact). A server is considered only past the
operator's threshold, which this report doesn't apply.

**Integrity.** The rows are HMAC-signed with the runner's key, which no session holds, so the signatures can't
be checked here (``--verify-proposal-ledger`` does that). What *can* be checked without the key is checked:
each row's ``prev`` hash chain, the head anchor's ``count`` and ``last``, rows without a signature, and a
ledger removed while dispatch records remain. That catches careless or partial edits (deleted, reordered,
inserted, appended or truncated rows). It is **not** proof against deliberate forgery: the chain is unkeyed,
so anyone who can write the files can recompute it. A mismatch is reported on the first line and makes the
exit code 1.

**Untrusted text.** ``argv``, agent names and register text are data (C-4). Text output escapes non-printing
characters and caps each field, and the number of rows shown per agent is capped.

Stdlib only. It deliberately does not import ``proposals``, which needs POSIX-only ``fcntl``, ``subprocess`` and
the sandbox. A read-only report should load anywhere, so the two ledger paths are restated here, and
``tests/test_mcp_need_report.py`` checks that they still match.
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from agentteams.mcp_need import MCP_NEEDS_CSV

#: The runner ledger and its signed head anchor, relative to the project root (``proposals.LEDGER_REL`` /
#: ``HEAD_REL``; restated, see the module docstring).
LEDGER_REL = ".agentteams/proposal-ledger.jsonl"
HEAD_REL = ".agentteams/proposal-ledger.head"
DISPATCH_REL = ".agentteams/dispatches.jsonl"
#: Where a team's register may live, relative to the project root, in the order searched.
REGISTER_CANDIDATES: tuple[str, ...] = (
    f"references/{MCP_NEEDS_CSV}",
    f".claude/agents/references/{MCP_NEEDS_CSV}",
    f".github/agents/references/{MCP_NEEDS_CSV}",
    f".goose/references/{MCP_NEEDS_CSV}",
)
#: How many leading argv words identify "the same command" (e.g. ``lake env lean``).
DEFAULT_PREFIX_WORDS = 3
#: A command is "repeated" from this many runs.
DEFAULT_MIN_REPEATS = 2
#: Register statuses that count as open.
OPEN_STATUSES = frozenset({"open", "waiting", "proposed", "approved"})
#: Text-output caps: characters per field, and rows shown per agent and section.
MAX_FIELD = 200
MAX_ROWS = 20


class ReportPathError(ValueError):
    """A ledger or register path is a symlink, or resolves outside the project."""


def _present(path: Path) -> bool:
    """True for an existing path or any symlink, dangling included (``exists()`` alone follows links)."""
    return path.exists() or path.is_symlink()


def _inside(root: Path, path: Path) -> Path:
    """*path*, refused when it is a symlink or resolves outside *root*."""
    if path.is_symlink():
        raise ReportPathError(f"{path} is a symlink; refusing to read it")
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ReportPathError(f"{path} resolves outside the project {root}; refusing to read it")
    return resolved


def _clean(value: Any) -> str:
    """Untrusted text for display: non-printing characters escaped, capped at :data:`MAX_FIELD`."""
    text = "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(value))
    return text if len(text) <= MAX_FIELD else text[:MAX_FIELD] + "…"


def _read_ledger(path: Path) -> tuple[list[str], list[dict[str, Any] | None]]:
    """The ledger's raw lines and, per line, its parsed object (``None`` when it isn't a JSON object).

    Every line counts, blank ones included, as the runner's own verifier counts them: a blank line inserted
    into the ledger is a chain break, not something to skip.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[dict[str, Any] | None] = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        rows.append(row if isinstance(row, dict) else None)
    return lines, rows


def check_chain(root: Path) -> list[str]:
    """Check what needs no key: the ``prev`` hash chain and the head anchor's ``count`` and ``last``.

    Args:
        root: The project root.

    Returns:
        Problems found, empty when the chain and head are consistent (or there is no ledger). Signatures are not
        checked; that needs the runner's key (``--verify-proposal-ledger``).

    Raises:
        ReportPathError: The ledger or head is a symlink or resolves outside *root*.
    """
    ledger, head = root / LEDGER_REL, root / HEAD_REL
    if not _present(ledger) and not _present(head):
        # The runner records every dispatch, so dispatch records without a ledger mean it was removed.
        return (["ledger and head are missing but dispatch records exist (ledger removed)"]
                if _present(root / DISPATCH_REL) else [])
    if not _present(ledger):
        return ["the head anchor exists but the ledger is missing"]
    lines, rows = _read_ledger(_inside(root, ledger))
    problems: list[str] = []
    prev = ""
    for i, (line, row) in enumerate(zip(lines, rows), 1):
        if row is None:
            problems.append(f"row {i} is not a JSON object")
        else:
            if "mac" not in row:
                problems.append(f"row {i} has no signature (`mac`)")
            if row.get("prev") != prev:
                problems.append(f"row {i} does not chain to the row before it (deleted, inserted or reordered)")
        prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
    if not _present(head):
        problems.append("the signed head anchor is missing")
    else:
        try:
            anchor = json.loads(_inside(root, head).read_text(encoding="utf-8"))
        except ValueError:
            anchor = None
        if not isinstance(anchor, dict):
            problems.append("the head anchor is unreadable")
        else:
            if anchor.get("count") != len(lines):
                problems.append(f"the head anchor counts {anchor.get('count')} rows, the ledger has {len(lines)} "
                                "(truncated or appended)")
            if lines and anchor.get("last") != prev:
                problems.append("the head anchor's last-row hash doesn't match the ledger's last row")
    return problems


def _read_register(path: Path) -> list[dict[str, str]]:
    """Register rows with an open status, as dicts."""
    with path.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("status") or "").strip() in OPEN_STATUSES]


def _failed(row: dict[str, Any]) -> bool:
    return (row.get("action") == "run-request-timeout"
            or (isinstance(row.get("exit"), int) and row["exit"] != 0)
            or bool(row.get("undeclared_writes")) or bool(row.get("survivors_killed")))


def build_report(root: Path, *, register: Path | None = None, prefix_words: int = DEFAULT_PREFIX_WORDS,
                 min_repeats: int = DEFAULT_MIN_REPEATS) -> dict[str, Any]:
    """Summarize the runner ledger and the need register per agent.

    Args:
        root: The project root (holds ``.agentteams/proposal-ledger.jsonl``).
        register: The need register. Default: the first of :data:`REGISTER_CANDIDATES` that exists.
        prefix_words: Leading argv words that identify the same command.
        min_repeats: Runs from which a command counts as repeated.

    Returns:
        ``{"ledger", "register" (paths or None), "chain_problems" (list), "unreadable_rows" (int),
        "signatures_verified": False, "agents": {slug: {...}}}``. Each agent has ``commands`` (repeated only,
        most total time first: ``prefix``, ``count``, ``total_ms``, ``median_ms``, ``failed``), ``unfinished``
        (runs started without a closing row), ``proposals`` (``count``, ``bytes_total``, ``bytes_median``),
        ``refused`` and ``register`` (rows the register labels ``verified`` vs not; the label is the register's
        own claim).

    Raises:
        ValueError: *prefix_words* or *min_repeats* below 1.
        ReportPathError: The ledger, head or register is a symlink or resolves outside *root*.
    """
    if prefix_words < 1 or min_repeats < 1:
        raise ValueError("prefix_words and min_repeats must be at least 1")
    root = Path(root)
    ledger = root / LEDGER_REL
    chain = check_chain(root)
    lines, parsed = _read_ledger(_inside(root, ledger)) if _present(ledger) else ([], [])
    rows = [r for r in parsed if r is not None]
    if register is None:
        register = next((root / c for c in REGISTER_CANDIDATES if _present(root / c)), None)
    reg_rows = _read_register(_inside(root, register)) if register is not None and _present(register) else []

    runs: dict[str, dict[tuple[str, ...], list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    started: dict[str, int] = defaultdict(int)
    finished: dict[str, int] = defaultdict(int)
    sizes: dict[str, list[int]] = defaultdict(list)
    refused: dict[str, int] = defaultdict(int)
    for row in rows:
        action, agent = row.get("action"), str(row.get("agent") or "?")
        if action == "run-request-start":
            started[agent] += 1
        elif action in ("run-request", "run-request-timeout") and isinstance(row.get("argv"), list):
            finished[agent] += 1
            runs[agent][tuple(str(a) for a in row["argv"][:prefix_words])].append(row)
        elif action == "apply-proposal" and isinstance(row.get("bytes"), int):
            sizes[agent].append(row["bytes"])
        elif isinstance(action, str) and action.endswith("-refused"):
            refused[agent] += 1

    agents: dict[str, dict[str, Any]] = {}
    names = set(runs) | set(started) | set(sizes) | set(refused) | {str(r.get("agent") or "?") for r in reg_rows}
    for agent in sorted(names):
        commands = []
        for prefix, group in runs.get(agent, {}).items():
            if len(group) < min_repeats:
                continue
            times = [r["duration_ms"] for r in group if isinstance(r.get("duration_ms"), int)]
            commands.append({"prefix": list(prefix), "count": len(group), "total_ms": sum(times),
                             "median_ms": int(statistics.median(times)) if times else None,
                             "failed": sum(1 for r in group if _failed(r))})
        commands.sort(key=lambda c: (-c["total_ms"], -c["count"]))
        agent_sizes = sizes.get(agent, [])
        own = [r for r in reg_rows if r.get("agent") == agent]
        agents[agent] = {
            "commands": commands,
            "unfinished": max(0, started.get(agent, 0) - finished.get(agent, 0)),
            "proposals": {"count": len(agent_sizes), "bytes_total": sum(agent_sizes),
                          "bytes_median": int(statistics.median(agent_sizes)) if agent_sizes else None},
            "refused": refused.get(agent, 0),
            "register": {"verified": [r for r in own if (r.get("verified") or "").strip() == "yes"],
                         "unverified": [r for r in own if (r.get("verified") or "").strip() != "yes"]},
        }
    return {"ledger": str(ledger) if ledger.exists() else None,
            "register": str(register) if register is not None and register.exists() else None,
            "chain_problems": chain, "unreadable_rows": sum(1 for r in parsed if r is None),
            "signatures_verified": False, "agents": agents}


def format_report(report: dict[str, Any]) -> str:
    """Render :func:`build_report` output as plain text, with untrusted fields escaped and capped.

    Args:
        report: The dict from :func:`build_report`.

    Returns:
        The report text. Its first line gives the chain and head check result.

    Raises:
        Nothing.
    """
    problems = report["chain_problems"]
    out = [("chain/head: consistent" if not problems else "chain/head: NOT CONSISTENT: " + "; ".join(problems))
           + " (signatures not checked: run --verify-proposal-ledger)",
           "MCP need report (read-only; decides nothing)",
           f"ledger: {report['ledger'] or '(none found)'}" + (
               f"  [{report['unreadable_rows']} unreadable row(s) skipped]" if report["unreadable_rows"] else ""),
           f"register: {report['register'] or '(none found)'}", ""]
    if not report["agents"]:
        out.append("No agent activity or open register rows.")
    for agent, data in report["agents"].items():
        out.append(f"== {_clean(agent)}")
        for c in data["commands"][:MAX_ROWS]:
            median = f"{c['median_ms']} ms median" if c["median_ms"] is not None else "no timings"
            out.append(f"  repeated: {_clean(' '.join(c['prefix']))}  x{c['count']}, {c['total_ms']} ms total, "
                       f"{median}" + (f", {c['failed']} failed" if c["failed"] else ""))
            out.append("    first try a runner-path fix: broaden or adjust the command entry, add a gate, or "
                       "pre-build what it rebuilds (mcp-need.reference.md Q4)")
        if len(data["commands"]) > MAX_ROWS:
            out.append(f"  ... {len(data['commands']) - MAX_ROWS} more (use --json)")
        if data["unfinished"]:
            out.append(f"  unfinished runs: {data['unfinished']} (started, never closed: a crash or an edit)")
        p = data["proposals"]
        if p["count"]:
            out.append(f"  proposals: {p['count']}, {p['bytes_total']} bytes carried (median {p['bytes_median']})")
        if data["refused"]:
            out.append(f"  refused requests: {data['refused']} (the ledger keeps a reason, not the command)")
        for label, key in (("register says verified", "verified"), ("register: unverified", "unverified")):
            for r in data["register"][key][:MAX_ROWS]:
                out.append(f"  [{label}] {_clean(r.get('id'))}: {_clean(r.get('capability'))} -> "
                           f"{_clean(r.get('decision'))} ({_clean(r.get('status'))})")
        out.append("")
    out.append("Apply mcp-need.reference.md: security gate first, then the operator's threshold, then runner-path "
               "fixes, then a server.")
    return "\n".join(out)


def run(root: Path, *, register: Path | None = None, as_json: bool = False) -> int:
    """CLI entry for ``--mcp-need-report``: print the report and return an exit code.

    Args:
        root: The project root.
        register: An explicit register path, else auto-detected.
        as_json: Print JSON instead of text.

    Returns:
        0 when the chain and head are consistent (a missing ledger is a valid empty report). 1 when they aren't,
        or when a path is refused.

    Raises:
        Nothing.
    """
    try:
        report = build_report(root, register=register)
    except ReportPathError as exc:
        print(f"[mcp-need-report] refused: {exc}")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True) if as_json else format_report(report))
    return 1 if report["chain_problems"] else 0
