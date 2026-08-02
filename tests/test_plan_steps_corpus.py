"""The real plan-step CSVs on disk must round-trip through ``plan_steps.read_steps``.

**Why prose was not enough.** The CSV-safety instruction — write with ``csv.writer``, re-parse via
``read_steps`` — has existed near-verbatim in both orchestrator agent files since 2026-07-22. Two
days later this repo produced several rounds of the exact corruption it forbids: unescaped commas
in free-text fields spilling into columns that do not exist. The instruction was present, in two
places, and did not prevent recurrence. An instruction that competes with finishing the work loses;
a test does not.

``read_steps`` already implements the detection half (``row.pop(None, None)`` plus a
``UserWarning`` on overflow). What was missing is anything that points it at the files that
actually accumulate.

**Why this is a ratchet and not a zero-gate.** Measured 2026-07-31, 13 of 187 files already
overflow, the oldest from 2026-W19. They are historical records of finished work; rewriting them
now would edit the record to make a test pass, which is the wrong direction. The baseline is
listed by path — not as a count — so each entry stays visible and can be repaired individually,
and so a *new* file joining the list fails loudly.

**Why this test skips instead of passing when the corpus is absent.** ``tmp/`` is gitignored and
carries zero tracked files, so in CI this glob matches nothing. A test that returns green on an
empty corpus would read as coverage while checking nothing at all — the worse failure. It skips
with a reason instead, and does its work on the developer machines where the CSVs are written.
"""

from __future__ import annotations

import csv
import warnings
from pathlib import Path

import pytest

from agentteams.plan_steps import read_steps

_TMP = Path(__file__).resolve().parents[1] / "tmp"

#: Files already overflowing when this guard was introduced (2026-07-31). Listed individually so
#: the debt stays legible: repairing one means deleting its line, and nothing may be added.
_KNOWN_OVERFLOWING: frozenset[str] = frozenset({
    "by-week/2026-W19/collector-management-ignore-large-backup-2026-05-04.steps.csv",
    "by-week/2026-W19/remove-vk-references-from-past-commits-2026-05-04.steps.csv",
    "by-week/2026-W21/api-doc-audit-and-refresh-2026-05-20.steps.csv",
    "by-week/2026-W21/api-doc-opportunity-pass-2-2026-05-20.steps.csv",
    "by-week/2026-W21/api-doc-review-and-expansion-2026-05-20.steps.csv",
    "by-week/2026-W21/infra-audit/remediation/RA1-heal-persistence.steps.csv",
    "by-week/2026-W21/progress-and-open-issues-review-2026-05-21.steps.csv",
    "by-week/2026-W21/template-refactoring-audit-and-plan-2026-05-21.steps.csv",
    "by-week/2026-W21/template-refactoring-implementation-2026-05-21.steps.csv",
    "by-week/2026-W23/pola-hygiene-review-2026-06-05.steps.csv",
    "by-week/2026-W29/technical-validator-ch-rename.steps.csv",
    "by-week/2026-W30/goose-context-bloat-management.steps.csv",
    "by-week/2026-W31/ready-items.steps.csv",
})


def _corpus() -> list[Path]:
    return sorted(_TMP.glob("by-week/**/*.steps.csv"))


def _overflows(path: Path) -> str | None:
    """Return the first complaint ``read_steps`` makes about ``path``, or None."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            read_steps(path)
        # A CSV too broken to parse is a stronger failure than an overflow warning, not a pass.
        # Narrow rather than blanket (CH-24): these are the ways a real file on disk fails to
        # read — malformed CSV, bad encoding, unreadable file, or a header the reader rejects.
        except (csv.Error, UnicodeDecodeError, OSError, ValueError) as exc:
            return f"{type(exc).__name__}: {exc}"
    return str(caught[0].message) if caught else None


@pytest.fixture(scope="module")
def corpus() -> list[Path]:
    files = _corpus()
    if not files:
        pytest.skip(
            "no tmp/by-week/**/*.steps.csv on this machine — tmp/ is gitignored, so CI has an "
            "empty corpus. Skipping rather than passing: an empty glob is not evidence."
        )
    return files


def test_no_new_steps_csv_overflows_its_header(corpus):
    """The ratchet. A newly written plan CSV must round-trip cleanly."""
    regressions = []
    for path in corpus:
        rel = path.relative_to(_TMP).as_posix()
        if rel in _KNOWN_OVERFLOWING:
            continue
        complaint = _overflows(path)
        if complaint:
            regressions.append(f"{rel}: {complaint}")

    assert not regressions, (
        "plan-step CSV(s) with fields spilling past the header:\n  "
        + "\n  ".join(regressions)
        + "\n\nWrite these with csv.writer and re-parse via plan_steps.read_steps() before "
          "committing. A free-text field containing a comma must be quoted, not truncated."
    )


def test_the_baseline_does_not_grow_silently(corpus):
    """A repaired file must be removed from the baseline, so the debt cannot be padded."""
    present = {p.relative_to(_TMP).as_posix() for p in corpus}
    stale = [
        rel for rel in sorted(_KNOWN_OVERFLOWING)
        if rel in present and _overflows(_TMP / rel) is None
    ]
    assert not stale, (
        "file(s) listed as known-overflowing now parse cleanly:\n  " + "\n  ".join(stale)
        + "\n\nDelete them from _KNOWN_OVERFLOWING — a baseline that keeps repaired entries "
          "hides the fact that the debt shrank."
    )
