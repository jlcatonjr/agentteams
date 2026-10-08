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
        ValueError: The log is malformed or headerless, a dict has unknown keys, a value is not a ``str``, a value
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


__all__ = ["LOG_DIRS", "LOG_NAMES", "append_row", "check_log", "check_logs", "log_paths"]
