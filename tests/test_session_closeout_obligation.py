"""A round that fixes logged defects must reconcile their rows before it closes.

**The failure this exists to stop is recurrent and self-inflicted.** On 2026-07-30 the
remediation log gained `resolved_date`/`resolved_evidence` precisely because 8 of 53 rows were
fixed-but-open. Over the next three rounds roughly a dozen more items were fixed — and none of
their rows were closed, because nothing in the process invoked the new columns. The next
enumeration found 48 open rows of which 13 were already done: the same defect, one day after
shipping the instrument meant to prevent it.

The lesson is that the instrument was never the missing piece. A convention documented in prose
is a reminder; a reminder that competes with finishing the work loses. So the obligation is a
test.

**What this asserts, deliberately narrowly.** Not that any particular row *should* be closed —
whether a defect is genuinely fixed is a judgment, and an automated guess that closes a live row
is worse than a stale open one (that design was proposed for this round and rejected in audit).
What it asserts is that the log stays *internally coherent* and that closure remains cheap and
visible: evidence is substantive, dates are ordered, and the closable-work signal is not silently
growing.
"""

from __future__ import annotations

import csv
import datetime as _dt
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOG = _REPO_ROOT / "references" / "agentteams-remediation-log.csv"

#: Minimum useful length for a closure claim. "fixed" is not evidence; a test path or module is.
_MIN_EVIDENCE_CHARS = 40

#: Substrings that make a closure claim checkable by a later reader — it should point AT
#: something, not merely assert. Deliberately broad: a commit/PR reference and a stated grep
#: result ("zero remaining literals") are as checkable as a test path, and an anchor list that
#: rejects them would push authors toward padding evidence to satisfy the matcher rather than
#: to inform the reader.
_EVIDENCE_ANCHORS = (
    "tests/", ".py", ".md", ".json", ".csv", ".github/",
    "verified", "re-verified", "grep", "PR #",
    # A `wontfix` is closed by a DECISION, not a code artifact — "the operator confirmed this is
    # not a defect" is the only evidence such a row can have, and demanding a file path for it
    # would push authors to invent one. Added 2026-07-31 when the ExampleResearchGroup row (a
    # parent folder, correctly unversioned) tripped an anchor list that assumed every closure
    # points at code.
    "operator confirmed", "not a defect", "by design",
)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    if not _LOG.exists():
        pytest.skip("remediation log absent from this checkout")
    with _LOG.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_closure_evidence_points_at_something_checkable(rows):
    """A closed row must let the next reader verify the claim without archaeology."""
    weak = []
    for i, row in enumerate(rows, start=2):
        if row["status"] not in ("shipped", "wontfix"):
            continue
        evidence = row["resolved_evidence"].strip()
        if len(evidence) < _MIN_EVIDENCE_CHARS:
            weak.append(f"line {i}: evidence too thin to check ({len(evidence)} chars)")
        elif not any(anchor in evidence.lower() for anchor in _EVIDENCE_ANCHORS):
            # Case-insensitive: 'Operator confirmed' at the start of a sentence is the
            # same evidence as 'operator confirmed' mid-sentence.
            weak.append(f"line {i}: evidence names no file, test, or verification")
    assert not weak, (
        "Closed row(s) whose evidence cannot be checked:\n  " + "\n  ".join(weak)
        + "\n\nA closure that cannot be re-verified is how this log became untrustworthy the "
          "first time."
    )


def test_closure_dates_are_not_before_the_row_was_logged(rows):
    """A resolved_date earlier than the row's own date is a copy-paste, not a record."""
    bad = []
    for i, row in enumerate(rows, start=2):
        resolved = row["resolved_date"].strip()
        if not resolved:
            continue
        try:
            if _dt.date.fromisoformat(resolved) < _dt.date.fromisoformat(row["date"].strip()):
                bad.append(f"line {i}: resolved {resolved} predates logged {row['date']}")
        except ValueError:
            bad.append(f"line {i}: unparseable date pair ({row['date']!r}, {resolved!r})")
    assert not bad, "\n".join(bad)


def test_the_log_records_closures_at_all(rows):
    """The floor that would have caught the original condition.

    For most of the log's life every row read `open` — including rows fixed months earlier. A log
    with no closures is not a backlog; it is a diary.
    """
    closed = [r for r in rows if r["status"] in ("shipped", "wontfix")]
    assert closed, (
        "No row in the remediation log is closed. Either nothing has ever been fixed, or "
        "closures are not being recorded — the second is what actually happened before "
        "2026-07-30."
    )


def test_a_meaningful_share_of_the_log_is_reconciled(rows):
    """A ratchet on staleness, not a target for closing rows.

    Measured 2026-07-31: 22 of 57 rows closed (~39%) after reconciling three rounds of work. The
    floor is set well below that so ordinary growth — logging new findings faster than fixing
    them, which is healthy — never trips it. What it does catch is the specific regression this
    file exists for: rounds that ship fixes and leave every row open, driving the ratio toward
    zero again.
    """
    closed = sum(1 for r in rows if r["status"] in ("shipped", "wontfix"))
    ratio = closed / len(rows)
    assert ratio >= 0.20, (
        f"Only {closed}/{len(rows)} ({ratio:.0%}) of remediation rows are reconciled. Rounds are "
        f"shipping fixes without closing the rows they fix — the condition that made this log "
        f"overstate open work by ~27% on 2026-07-31. Reconcile before closing out."
    )


def test_the_plan_convention_states_the_obligation():
    """The test enforces it; the convention has to *say* it, or nobody knows to look.

    Belt and braces on purpose: a bare test failure at close-out tells someone they broke a rule
    they were never told.
    """
    conventions = _REPO_ROOT / "references" / "filing-conventions.md"
    if not conventions.is_file():
        pytest.skip("filing-conventions.md not present in this checkout")
    text = conventions.read_text(encoding="utf-8").lower()
    assert "remediation" in text and "reconcil" in text, (
        "references/filing-conventions.md does not state the close-out reconciliation "
        "obligation that tests/test_session_closeout_obligation.py enforces."
    )
