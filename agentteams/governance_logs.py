"""governance_logs.py — keep the append-only governance CSV logs well-formed.

The logs (``references/security-decisions.log.csv``, ``references/agentteams-remediation-log.csv``,
``references/redteam-findings.log.csv``, ``references/mcp-needs.csv``,
``.github/agents/references/conflict-log.csv``) are local, often gitignored, and appended to by many sessions.
One malformed row (an unclosed quote, an unescaped comma) makes every later reader mis-parse the file, and a
rewrite that trusts the parse truncates it: 2026-10-07, ``security-decisions.log.csv`` lost 13 records that way
and was rebuilt from session transcripts.

* :func:`check_log` / :func:`check_logs` report a malformed log: a field count that differs from the header's, a
  quote left open at end of file, or a field that has swallowed later dated rows. ``--verify-integrity`` reads
  them.
* :func:`append_row` is the validated way for code to add a row: it refuses a malformed log, renders the row in
  memory, confirms it round-trips, and appends under a lock in one write. It never opens the file for rewriting.
  (A rewrite goes through :func:`agentteams.atomicio.atomic_rewrite_csv_rows`.) Rows written by hand bypass it;
  :func:`check_logs` is what catches those.
* ``python -m agentteams.governance_logs repair LOG`` previews a fix for malformed rows, and ``--apply DIGEST``
  (operator, interactive terminal; a speed-bump, not a security boundary) applies exactly that preview: see :mod:`agentteams.governance_log_repair`.
"""

from __future__ import annotations

import csv
import io
import os
import re
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: appends are unlocked there
    fcntl = None  # type: ignore[assignment]

#: Governance logs, by file name. Each is looked for in every :data:`LOG_DIRS` entry under the given root, so a
#: project root (``references/`` and ``.github/agents/references/``) and a team dir (``references/``) both work.
LOG_NAMES: tuple[str, ...] = (
    "security-decisions.log.csv",
    "agentteams-remediation-log.csv",
    "redteam-findings.log.csv",
    "mcp-needs.csv",
    "conflict-log.csv",
)
LOG_DIRS: tuple[str, ...] = ("references", ".github/agents/references", ".claude/agents/references")
#: A line that starts a dated record (``2026-10-07,``). Inside a field it means a quote swallowed later rows.
_RECORD_START = re.compile(r"\n\d{4}-\d{2}-\d{2},")


def _parse(raw: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(raw.decode("utf-8", "replace"))))


def check_log(path: Path) -> list[str]:
    """Report malformed rows in one CSV log.

    Three signs, because an unclosed quote doesn't always change a field count (in the last column it swallows the
    rest of the file into that one field): a record whose field count differs from the header's; a strict-parse
    error (a quote still open at end of file); and a field holding a line that starts a dated record.

    Args:
        path: The log file.

    Returns:
        One problem string per finding, or for an unreadable or headerless file. Empty when well-formed.

    Raises:
        Nothing: an unreadable file is reported as a problem.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]
    rows = _parse(raw)
    if not rows or not rows[0]:
        return [f"{path}: no header row"]
    width = len(rows[0])
    problems = [f"{path}: record {i} has {len(r)} fields, header has {width}"
                for i, r in enumerate(rows[1:], 1) if r and len(r) != width]
    problems += [f"{path}: record {i} has a field holding later dated rows (an unclosed quote swallowed them)"
                 for i, r in enumerate(rows[1:], 1) if any(_RECORD_START.search(f) for f in r)]
    try:
        list(csv.reader(io.StringIO(raw.decode("utf-8", "replace")), strict=True))
    except csv.Error as exc:
        problems.append(f"{path}: strict parse fails ({exc}); a quote is probably left open")
    return problems


def log_paths(root: Path) -> list[Path]:
    """Every governance log present under ``root``, each resolved file once.

    Args:
        root: A project root or a team dir.

    Returns:
        The existing log files, in :data:`LOG_DIRS` then :data:`LOG_NAMES` order.

    Raises:
        Nothing.
    """
    seen: set[Path] = set()
    found: list[Path] = []
    for rel in LOG_DIRS:
        for name in LOG_NAMES:
            path = root / rel / name
            if path.is_file() and path.resolve() not in seen:
                seen.add(path.resolve())
                found.append(path)
    return found


def check_logs(root: Path) -> list[str]:
    """Check every governance log :func:`log_paths` finds under ``root``.

    Args:
        root: A project root or a team dir.

    Returns:
        All problems found. Empty when every present log is well-formed.

    Raises:
        Nothing.
    """
    return [problem for path in log_paths(root) for problem in check_log(path)]


def _refuse_a_chain_break(raw: bytes, header: list[str], cells: dict[str, str]) -> None:
    """Raise ValueError when appending ``cells`` would break a chained decisions log's hash chain.

    A broken chain makes the gate refuse every decision in the log, so a row that would break it is
    refused here instead: record such a decision with ``agentteams --sign-decision``, which links it.
    """
    from agentteams.cli.decision_chain import verify_chain
    from agentteams.cli.decision_log import _decision_signing_active

    rows = [dict(zip(header, r)) for r in _parse(raw)[1:] if r]
    try:
        verify_chain(rows + [cells], header, signing_active=_decision_signing_active(header))
    except RuntimeError as exc:
        raise ValueError(
            f"appending this row would break the decisions log's hash chain ({exc}); every gate would then "
            "refuse. Record the decision with `agentteams --sign-decision`, which links it"
        ) from exc


def append_row(path: Path, row: dict[str, str] | list[str]) -> None:
    """Append one row to an existing CSV log, validated, without ever rewriting the file.

    The row is rendered and checked in memory, then written with one ``os.write`` on an ``O_APPEND`` descriptor
    while holding an exclusive ``flock`` (POSIX; elsewhere unlocked), and the log is re-checked under the lock, so
    concurrent appenders using this function neither interleave nor tear a row.

    Args:
        path: An existing log with a header row.
        row: The values, as a list in header order or a dict keyed by header names (every key must be a header
            name; missing names are written empty).

    Returns:
        None.

    Raises:
        ValueError: The log is malformed or headerless, a dict has unknown keys, a security decision row has no
            action id or no verdict, or the row would break a chained decisions log, a value is not a ``str``, a value
            holds a line that starts a dated record, or the rendered row does not round-trip to the header's field
            count. The file is untouched in every case.
        OSError: The file cannot be read or appended to.
    """
    fd = os.open(path, os.O_RDWR | os.O_APPEND)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        raw = path.read_bytes()
        problems = check_log(path)
        if problems:
            raise ValueError("refusing to append to a malformed log: " + "; ".join(problems[:3]))
        header = _parse(raw)[0]
        if isinstance(row, dict):
            unknown = sorted(set(row) - set(header))
            if unknown:
                raise ValueError(f"unknown field(s) for {path.name}: {', '.join(unknown)}")
            values = [row.get(name, "") for name in header]
        else:
            values = list(row)
        if path.name == "security-decisions.log.csv" and {"action_reviewed", "decision"} & set(header):
            cells = dict(zip(header, values))
            if "prev_digest" in header:
                _refuse_a_chain_break(raw, header, cells)
            action = (cells.get("action_reviewed") or cells.get("decision") or "").strip()
            verdict = (cells.get("verdict") or cells.get("status") or "").strip()
            if not action or not verdict:
                raise ValueError("a security decision row needs its action id and its verdict "
                                 "(action_reviewed/verdict, or decision/status)")
        if any(not isinstance(v, str) for v in values):
            raise ValueError("every value must be a str")
        if len(values) != len(header):
            raise ValueError(f"row has {len(values)} fields, header has {len(header)}")
        newline = "\r\n" if b"\r\n" in raw else "\n"
        buf = io.StringIO()
        csv.writer(buf, lineterminator=newline).writerow(values)
        rendered = buf.getvalue()
        if any(_RECORD_START.search(v) for v in values):
            raise ValueError("a value holds a line that starts a dated record; check reads that as a swallowed row")
        if [len(r) for r in _parse(rendered.encode("utf-8"))] != [len(header)]:
            raise ValueError("the rendered row does not round-trip to one record of the header's width")
        data = ((newline if raw and not raw.endswith(newline.encode()) else "") + rendered).encode("utf-8")
        if os.write(fd, data) != len(data):
            raise OSError(f"short write appending to {path}")
    finally:
        os.close(fd)  # closing releases the flock


def main(argv: list[str] | None = None) -> int:
    """CLI: ``check [ROOT]``, ``append LOG --row-json JSON``, and ``repair LOG [--apply DIGEST]``.

    ``append`` is how the orchestrator records a row that a read-only agent (``@security``,
    ``@conflict-auditor``) returned: the agent cannot write, so it hands back the row and the
    orchestrator appends it here, validated by :func:`append_row` against the log's own header.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: 0 on success, 1 when ``check`` finds a problem, ``append`` refuses the row, or ``repair`` refuses,
        finds something it cannot safely fix, or (with ``--apply``) has no interactive terminal or the operator's
        confirmation does not match. ``repair`` without ``--apply`` only previews (see
        :mod:`agentteams.governance_log_repair`).

    Raises:
        None.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(prog="python -m agentteams.governance_logs",
                                     description="Check, append to, or repair the append-only governance CSV logs.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="Report malformed governance logs under ROOT.")
    chk.add_argument("root", nargs="?", default=".", help="Project root or team dir (default: .).")
    app = sub.add_parser("append", help="Append one validated row to an existing log.")
    app.add_argument("log", help="Path to the log (it must exist and have a header row).")
    app.add_argument("--row-json", required=True,
                     help="The row as a JSON object keyed by the log's header names (or a list in header order).")
    rep = sub.add_parser("repair", help="Preview (or, with --apply DIGEST, apply) a fix for malformed rows.")
    rep.add_argument("log", help="Path to the log.")
    rep.add_argument("--apply", metavar="DIGEST", default="",
                     help="Apply exactly the previewed repair whose digest the preview printed (backup written first).")
    args = parser.parse_args(argv)

    if args.cmd == "repair":
        from agentteams.governance_log_repair import apply_repair, plan_repair, render_plan

        try:
            if args.apply:
                # apply_repair asks the operator on an interactive terminal (a speed-bump, not a boundary).
                backup = apply_repair(Path(args.log), args.apply)
                print(f"repaired {args.log}; backup: {backup} (remove it once you have checked the log)")
                return 0
            plan = plan_repair(Path(args.log))
        except (ValueError, TypeError, OSError, UnicodeDecodeError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(render_plan(plan))
        return 0 if (plan.fixable or plan.clean) else 1
    if args.cmd == "check":
        problems = check_logs(Path(args.root))
        for p in problems:
            print(p)
        if not problems:
            print(f"{len(log_paths(Path(args.root)))} governance log(s) well-formed")
        return 1 if problems else 0
    try:
        row = json.loads(args.row_json)
        if not isinstance(row, (dict, list)):
            raise ValueError("--row-json must be a JSON object or list")
        append_row(Path(args.log), row)
    except (ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"appended 1 row to {args.log}")
    return 0


__all__ = ["LOG_DIRS", "LOG_NAMES", "append_row", "check_log", "check_logs", "log_paths", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
