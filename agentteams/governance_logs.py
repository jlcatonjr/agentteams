"""governance_logs.py — keep the append-only governance CSV logs well-formed.

The logs (``references/security-decisions.log.csv``, ``references/agentteams-remediation-log.csv``,
``references/redteam-findings.log.csv``, ``references/mcp-needs.csv``,
``.github/agents/references/conflict-log.csv``) are local, often gitignored, and appended to by many sessions.
One malformed row (an unclosed quote, an unescaped comma) makes every later reader mis-parse the file, and a
rewrite that trusts the parse truncates it: 2026-10-07, ``security-decisions.log.csv`` lost 13 records that way
and was rebuilt from session transcripts.

* :func:`check_log` / :func:`check_logs` report rows whose field count differs from the header's. They are read
  by ``--verify-integrity``.
* :func:`append_row` is the one safe way to add a row: it refuses a malformed log, renders the row in memory,
  confirms it round-trips to the header's field count, and only then appends. It never opens the file for
  rewriting. (A rewrite goes through :func:`agentteams.atomicio.atomic_rewrite_csv_rows`.)
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

#: Governance logs, relative to a project or team root, checked when present.
LOG_PATHS: tuple[str, ...] = (
    "references/security-decisions.log.csv",
    "references/agentteams-remediation-log.csv",
    "references/redteam-findings.log.csv",
    "references/mcp-needs.csv",
    ".github/agents/references/conflict-log.csv",
)


def _parse(raw: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(raw.decode("utf-8", "replace"))))


def check_log(path: Path) -> list[str]:
    """Report malformed rows in one CSV log.

    Args:
        path: The log file.

    Returns:
        One problem string per malformed row (record number, expected and actual field counts), or for an
        unreadable or headerless file. Empty when the log is well-formed.

    Raises:
        Nothing: an unreadable file is reported as a problem.
    """
    try:
        rows = _parse(path.read_bytes())
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]
    if not rows or not rows[0]:
        return [f"{path}: no header row"]
    width = len(rows[0])
    return [f"{path}: record {i} has {len(r)} fields, header has {width}"
            for i, r in enumerate(rows[1:], 1) if r and len(r) != width]


def check_logs(root: Path) -> list[str]:
    """Check every governance log in :data:`LOG_PATHS` that exists under ``root``.

    Args:
        root: The project or team root.

    Returns:
        All problems found, in :data:`LOG_PATHS` order. Empty when every present log is well-formed.

    Raises:
        Nothing.
    """
    problems: list[str] = []
    for rel in LOG_PATHS:
        path = root / rel
        if path.is_file():
            problems.extend(check_log(path))
    return problems


def append_row(path: Path, row: dict[str, str] | list[str]) -> None:
    """Append one row to an existing CSV log, validated, without ever rewriting the file.

    Args:
        path: An existing log with a header row.
        row: The values, as a list in header order or a dict keyed by header names (every key must be a header
            name; missing names are written empty).

    Returns:
        None.

    Raises:
        ValueError: The log is malformed or headerless, a dict has unknown keys, a value is not a ``str``, or the
            rendered row does not round-trip to the header's field count. The file is untouched in every case.
        OSError: The file cannot be read or appended to.
    """
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
    if [len(r) for r in _parse(rendered.encode("utf-8"))] != [len(header)]:
        raise ValueError("the rendered row does not round-trip to one record of the header's width")
    with open(path, "a", encoding="utf-8", newline="") as fh:
        if raw and not raw.endswith(newline.encode()):
            fh.write(newline)
        fh.write(rendered)


__all__ = ["LOG_PATHS", "append_row", "check_log", "check_logs"]
