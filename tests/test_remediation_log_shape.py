"""test_remediation_log_shape.py — the remediation log must stay machine-readable.

``references/agentteams-remediation-log.csv`` is appended to by the Rule 11
retrospective and read by ``csv.DictReader`` consumers. Two rows were once written
with unquoted commas inside a field, which silently shifted every later column and
broke any consumer that read them positionally. The rows were repaired; nothing
prevented a recurrence, and that gap is itself logged in the file.

**Scope is deliberately structural.** Rule 11 makes the ``status`` lifecycle
maintainer-owned — the generated agent "never edits an existing row" — so this
asserts nothing about which statuses exist or how many are open. Every row being
``open`` is a legitimate state under that rule, not a defect. What is never
legitimate is a row that cannot be parsed.

Why a test rather than a report-only script like ``verify_audit_ledger.py``: the
ledger tolerates ``unreviewed`` rows because an unadjudicated row is an honest
transient state, so failing on it would push authors to guess. A malformed CSV row
has no such defence — it breaks every consumer, and it has already happened once.
Different tolerance, different mechanism.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "references/agentteams-remediation-log.csv"

# Authoritative header, from agentteams/liaison_logs.py: AGENTTEAMS_REMEDIATION_HEADERS.
# Imported rather than duplicated so the two cannot drift.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Fields that carry no meaning when blank. `proposed_touch_points` is omitted
#: deliberately — a finding may legitimately have no proposed remedy yet.
_REQUIRED_NON_EMPTY = ("date", "source_repo", "category", "summary", "status")


def _expected_headers() -> list[str]:
    from agentteams.liaison_logs import AGENTTEAMS_REMEDIATION_HEADERS

    return list(AGENTTEAMS_REMEDIATION_HEADERS)


def _rows() -> list[dict[str, str]]:
    with LOG_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_log_exists_and_parses() -> None:
    assert LOG_PATH.exists(), f"{LOG_PATH} is missing"
    assert _rows(), "remediation log has a header but no rows"


def test_header_matches_the_declared_field_list() -> None:
    """The header is the contract; liaison_logs.py is its source of truth."""
    with LOG_PATH.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    assert header == _expected_headers(), (
        f"header drifted from AGENTTEAMS_REMEDIATION_HEADERS: {header}"
    )


def test_no_row_has_a_shifted_or_missing_column() -> None:
    """Catch the failure that actually occurred: an unquoted comma in a field.

    ``DictReader`` signals this as a ``None`` key (too many fields) or a ``None``
    value (too few), so both are checked by field count against the raw reader —
    which reports the true shape rather than the padded dict.
    """
    expected = len(_expected_headers())
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
    """A blank date or summary makes a row unusable without being malformed."""
    blanks = [
        (index, field)
        for index, row in enumerate(_rows(), start=2)
        for field in _REQUIRED_NON_EMPTY
        if not (row.get(field) or "").strip()
    ]
    assert not blanks, f"row(s) with an empty required field: {blanks}"


@pytest.mark.parametrize("row_index", range(len(_rows())))
def test_dates_are_iso_formatted(row_index: int) -> None:
    """Rule 11 requires absolute dates; a relative one cannot be sorted or aged."""
    row = _rows()[row_index]
    date = (row.get("date") or "").strip()
    assert _ISO_DATE_RE.match(date), (
        f"row {row_index + 2}: date {date!r} is not YYYY-MM-DD"
    )


def test_no_embedded_newlines_in_fields() -> None:
    """A newline inside a field parses correctly but breaks line-oriented tooling.

    ``grep``, ``wc -l`` and ``tail`` are how this log is actually read during a
    session, so a multi-line field is a defect even though ``csv`` handles it.
    """
    offenders = [
        (index, field)
        for index, row in enumerate(_rows(), start=2)
        for field, value in row.items()
        if isinstance(value, str) and "\n" in value
    ]
    assert not offenders, f"field(s) containing a newline: {offenders}"
