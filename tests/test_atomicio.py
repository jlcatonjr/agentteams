"""Tests for agentteams/atomicio.py::atomic_rewrite_csv_rows (open-items
backlog remediation, I6): a hand-rolled CSV rewrite that opens its target in
truncating write mode has no defense against a mid-write crash — this project
hit that failure class three times (2026-07-24, 2026-07-29, and a 2026-08-11
conflict-log.csv truncation this same session). This safe primitive verifies
the rendered CSV in memory before ever opening the destination for writing.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agentteams.atomicio import atomic_rewrite_csv_rows

_FIELDS = ["a", "b", "c"]


def _read(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_writes_well_formed_rows(tmp_path: Path):
    path = tmp_path / "ledger.csv"
    rows = [{"a": "1", "b": "2", "c": "3"}, {"a": "x", "b": "y, with a comma", "c": "z"}]
    atomic_rewrite_csv_rows(path, rows, _FIELDS)
    assert _read(path) == rows


def test_round_trips_a_comma_containing_field_correctly(tmp_path: Path):
    """The exact failure class this function exists to prevent: a free-text
    field containing an unescaped comma must round-trip as ONE field, not
    split into extra columns."""
    path = tmp_path / "ledger.csv"
    rows = [{"a": "1", "b": "contains, a comma, and another", "c": "3"}]
    atomic_rewrite_csv_rows(path, rows, _FIELDS)
    assert _read(path) == rows
    with open(path, newline="", encoding="utf-8") as f:
        raw_rows = list(csv.reader(f))
    assert len(raw_rows[1]) == 3


def test_refuses_to_write_when_a_row_has_extra_columns(tmp_path: Path):
    """A row carrying a key outside fieldnames — csv.DictWriter's own
    extrasaction='raise' default catches this before this function's own
    round-trip verification even runs. Either way, the point this function
    exists to guarantee is that nothing gets written."""
    path = tmp_path / "ledger.csv"
    rows = [{"a": "1", "b": "2", "c": "3", "d": "unexpected extra field"}]
    with pytest.raises(ValueError, match="fieldnames"):
        atomic_rewrite_csv_rows(path, rows, _FIELDS)
    assert not path.exists()


def test_does_not_touch_disk_at_all_when_verification_fails(tmp_path: Path):
    """Refusing to write must mean the destination is genuinely untouched —
    not truncated-then-abandoned, the exact failure mode this function exists
    to prevent."""
    path = tmp_path / "ledger.csv"
    path.write_text("a,b,c\noriginal,data,here\n", encoding="utf-8")
    original = path.read_text(encoding="utf-8")
    rows = [{"a": "1", "b": "2", "c": "3", "d": "extra"}]
    with pytest.raises(ValueError):
        atomic_rewrite_csv_rows(path, rows, _FIELDS)
    assert path.read_text(encoding="utf-8") == original


def test_refuses_to_write_when_a_value_is_not_a_string(tmp_path: Path):
    """2026-08-11: a hand-authored dict literal with a stray trailing comma
    inside a parenthesized multi-line string (``("text",)`` instead of
    ``"text"``) silently produces a one-element tuple. csv.DictWriter would
    str()-convert that to the literal text ``('text',)`` instead of erroring
    — the exact corruption this function exists to prevent, and the existing
    column-count round-trip check does not catch it (the tuple's str() form
    is still one well-formed field)."""
    path = tmp_path / "ledger.csv"
    rows = [{"a": "1", "b": ("oops, a tuple",), "c": "3"}]
    with pytest.raises(ValueError, match="not str"):
        atomic_rewrite_csv_rows(path, rows, _FIELDS)
    assert not path.exists()


def test_empty_rows_writes_header_only(tmp_path: Path):
    path = tmp_path / "ledger.csv"
    atomic_rewrite_csv_rows(path, [], _FIELDS)
    assert _read(path) == []
    assert path.read_text(encoding="utf-8") == "a,b,c\n"


def test_uses_unix_line_endings(tmp_path: Path):
    path = tmp_path / "ledger.csv"
    atomic_rewrite_csv_rows(path, [{"a": "1", "b": "2", "c": "3"}], _FIELDS)
    raw = path.read_bytes()
    assert b"\r\n" not in raw


def test_preserves_existing_crlf_line_endings(tmp_path: Path):
    """2026-08-13 regression: rewriting one row of a CRLF file must not silently
    reformat the whole file to LF -- an unrequested reformat unrelated to the edit."""
    path = tmp_path / "ledger.csv"
    path.write_bytes(b"a,b,c\r\n1,2,3\r\n")
    atomic_rewrite_csv_rows(path, [{"a": "1", "b": "2", "c": "3"}, {"a": "4", "b": "5", "c": "6"}], _FIELDS)
    raw = path.read_bytes()
    assert raw == b"a,b,c\r\n1,2,3\r\n4,5,6\r\n", raw
    assert _read(path) == [{"a": "1", "b": "2", "c": "3"}, {"a": "4", "b": "5", "c": "6"}]


def test_a_brand_new_file_still_defaults_to_unix_line_endings(tmp_path: Path):
    """No existing file to sniff a convention from -> LF default, unchanged behavior."""
    path = tmp_path / "new-ledger.csv"
    assert not path.exists()
    atomic_rewrite_csv_rows(path, [{"a": "1", "b": "2", "c": "3"}], _FIELDS)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
