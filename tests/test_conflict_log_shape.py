"""test_conflict_log_shape.py — the conflict log must stay machine-readable.

Mirrors tests/test_remediation_log_shape.py's structural pattern (same
justification: a malformed row breaks every consumer, not just the one that
wrote it). Scoped to structure only, not to `status`/`category` vocabulary —
unlike agentteams-remediation-log.csv, this file has no single Python-side
source of truth for its header or status lifecycle (it is maintained entirely
by agent instructions in .github/agents/conflict-auditor.agent.md, not by any
agentteams/*.py module), and this session alone has used `open`/`OPEN`/
`RESOLVED`/`ACCEPT` across different rows — asserting a fixed vocabulary here
would be a separate, larger normalization task, not this test's job.

This file exists because this exact failure class (unescaped comma in a
free-text field, silently shifting every later column) has now hit THIS log
directly: a 2026-08-11 rewrite script crashed mid-write on two pre-existing
malformed rows and, because it opened the file in truncating mode first,
destroyed 17 rows before the crash was caught (12 recovered from the
session's own conversation context, 5 permanently lost — see this file's own
2026-08-11 COUNT_MISMATCH row).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / ".github/agents/references/conflict-log.csv"

_EXPECTED_HEADER = ["date", "category", "code", "severity", "file", "description", "status", "resolution"]
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: `resolution` is omitted — a still-open row legitimately has none yet.
#: `category`/`code` are omitted too — a "clean audit, no conflicts found" row
#: (conflict-auditor Rule 5: a clean audit must still produce a log entry)
#: legitimately has neither, since no defined category fits an absence of
#: findings (confirmed against a real row: 2026-07-23, orchestrator.md
#: consistency check, status=RESOLVED with an explanatory resolution and no
#: category — not a malformed row, a different legitimate row shape).
_REQUIRED_NON_EMPTY = ("date", "severity", "file", "description", "status")


def _rows() -> list[dict[str, str]]:
    with LOG_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_log_exists_and_parses() -> None:
    assert LOG_PATH.exists(), f"{LOG_PATH} is missing"
    assert _rows(), "conflict log has a header but no rows"


def test_header_matches_expected_columns() -> None:
    with LOG_PATH.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == _EXPECTED_HEADER, f"header drifted: {header}"


def test_no_row_has_a_shifted_or_missing_column() -> None:
    """Catch the failure that actually occurred: an unquoted comma in a field.

    Checked against the raw reader (true shape), not the padded DictReader
    dict, matching test_remediation_log_shape.py's own approach.
    """
    expected = len(_EXPECTED_HEADER)
    with LOG_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)  # header, asserted separately
        offenders = [
            (lineno, len(row), row[:2])
            for lineno, row in enumerate(reader, start=2)
            if row and len(row) != expected
        ]
    assert not offenders, (
        f"row(s) with the wrong field count (expected {expected}): {offenders}. "
        "A comma inside a field must be quoted, or every later column shifts."
    )


def test_required_fields_are_populated() -> None:
    blanks = [
        (index, field)
        for index, row in enumerate(_rows(), start=2)
        for field in _REQUIRED_NON_EMPTY
        if not (row.get(field) or "").strip()
    ]
    assert not blanks, f"row(s) with an empty required field: {blanks}"


def test_dates_are_iso_formatted() -> None:
    bad = [
        (index, row["date"])
        for index, row in enumerate(_rows(), start=2)
        if not _ISO_DATE_RE.match((row.get("date") or "").strip())
    ]
    assert not bad, f"row(s) with a non-ISO date: {bad}"


def test_no_embedded_newlines_in_fields() -> None:
    """A newline inside a field parses correctly but breaks grep/wc -l/tail,
    how this log is actually read during a session."""
    offenders = [
        (index, field)
        for index, row in enumerate(_rows(), start=2)
        for field, value in row.items()
        if isinstance(value, str) and "\n" in value
    ]
    assert not offenders, f"field(s) containing a newline: {offenders}"
