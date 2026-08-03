"""test_constraint_line_definition.py — one definition of "this line states a rule".

`CONSTRAINT_BEARING_RE`'s docstring says why it lives in production rather than in a test:

    A notice claiming "a rule was deleted" and a ratchet counting "rules outside a fence"
    have to mean the same thing by the same definition, or neither number means anything.

They did not. `_detect_deleted_constraints` counted markdown table rows; the ratchet excluded
them, inline, in a test file. So a section-manifest row like

    | invariant_core | FENCED | ⛔ contract: responsibilities + codes |

matched `⛔`, cleared the 30-character floor, and was reported as a *deleted rule* in 5 of 54
files — while the ratchet, measuring the same library, did not count it at all.

A table row is a manifest of where rules live, not a rule. Its absence from a deployed file
says nothing about whether an obligation survived.

**A divergence that remains, deliberately.** The two consumers still differ on
`NUMBERED_RULE_RE`: the detector counts numbered bolded rules, the ratchet does not. Unifying
that would add **130 constraint lines across 24 templates** (ratchet 159 → 289) — measured
2026-08-03, not estimated. That is a re-baselining exercise with its own judgement calls
(§4.1's unfenced Constitutional Rules are an extension point by design), so it is recorded as
an open item rather than folded in here. This file pins the shared part and names the rest.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from agentteams import fences
from agentteams.fences import _detect_deleted_constraints, is_trackable_constraint_line

RULE = "⛔ You are read-only and MUST NOT edit any file under any circumstances whatsoever."
TABLE_ROW = "| invariant_core | FENCED | ⛔ contract: responsibilities + codes |"


def _rendered(body: str) -> str:
    return f"# Agent\n\n{body}\n"


# --------------------------------------------------------------------------------------
# The false positive
# --------------------------------------------------------------------------------------


def test_a_table_row_is_not_a_deleted_rule() -> None:
    """The defect: a section-manifest row absent from disk was reported as a deleted rule."""
    notices = _detect_deleted_constraints(_rendered(TABLE_ROW), "# Agent\n\nnothing here\n")
    assert notices == [], f"a markdown table row was reported as a deleted rule: {notices}"


def test_a_real_rule_absent_from_disk_still_reports() -> None:
    """Negative control. Silencing table rows must not silence the notice itself."""
    notices = _detect_deleted_constraints(_rendered(RULE), "# Agent\n\nnothing here\n")
    assert len(notices) == 1, f"a genuinely deleted rule stopped reporting: {notices}"
    assert "deleted rule" in notices[0]


def test_a_table_row_does_not_mask_a_real_rule_beside_it() -> None:
    """Both on the same page: the row is ignored, the rule is still caught."""
    notices = _detect_deleted_constraints(
        _rendered(f"{TABLE_ROW}\n\n{RULE}"), "# Agent\n\nnothing here\n"
    )
    assert len(notices) == 1, notices
    assert "read-only" in notices[0], notices


# --------------------------------------------------------------------------------------
# The shared predicate
# --------------------------------------------------------------------------------------


def test_the_predicate_excludes_table_rows_and_fragments() -> None:
    assert is_trackable_constraint_line(RULE) is True
    assert is_trackable_constraint_line(TABLE_ROW) is False
    assert is_trackable_constraint_line("  " + TABLE_ROW) is False, "leading whitespace evaded it"
    assert is_trackable_constraint_line("⛔ too short") is False, "the length floor is gone"


def test_whitespace_normalisation_matches_the_haystack() -> None:
    """The floor is measured on collapsed whitespace, as the on-disk haystack is.

    Verified equivalent to the ratchet's `strip()` form across the whole library on
    2026-08-03 — 43 files, 159 lines, identical under both. Pinned so a future change to one
    cannot silently move the other.
    """
    padded = "⛔    You  are   read-only   and    MUST NOT edit    any file whatsoever here."
    assert is_trackable_constraint_line(padded) is True


def _code_without_docstring(func) -> str:
    """Source of *func* with the docstring removed.

    Reading raw source would match the word `startswith("|")` where a docstring merely
    *describes* the exclusion it replaced — the same false positive this file exists to fix,
    committed in the test that checks for it.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    fn = tree.body[0]
    body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) and isinstance(
        fn.body[0].value, ast.Constant
    ) else fn.body
    return "\n".join(ast.unparse(node) for node in body)


def test_both_consumers_call_the_shared_predicate() -> None:
    """Structural: neither consumer may re-implement the exclusion.

    The ratchet's inline table-row test is exactly how the two definitions drifted apart while
    both claimed to measure "constraint-bearing lines".
    """
    from tests import test_unfenced_constraint_ratchet as ratchet

    ratchet_code = _code_without_docstring(ratchet._stray_count)
    assert "is_trackable_constraint_line" in ratchet_code, (
        "the ratchet re-implements the constraint-line filter instead of sharing it"
    )
    assert "startswith" not in ratchet_code, (
        f"the ratchet still carries its own table-row exclusion:\n{ratchet_code}"
    )

    detector_code = _code_without_docstring(fences._detect_deleted_constraints)
    assert "is_trackable_constraint_line" in detector_code, (
        "the deleted-constraint detector does not use the shared predicate"
    )
    assert "_CONSTRAINT_MIN_CHARS" not in detector_code, (
        f"the detector still applies its own length floor:\n{detector_code}"
    )


def test_the_real_library_still_measures_what_it_measured() -> None:
    """Anti-vacuity: the change must not move the ratchet in either direction.

    `test_no_template_gains_an_unfenced_constraint` and `test_baseline_has_no_stale_entries`
    pin all 43 entries from both sides, so a moved count fails there. This asserts the total
    so a wholesale predicate regression cannot pass by moving every file at once.
    """
    from tests.test_unfenced_constraint_ratchet import _current

    current = _current()
    assert (len(current), sum(current.values())) == (43, 159), (
        f"library measurement moved to {len(current)} files / {sum(current.values())} lines; "
        "it was 43 / 159 before the predicate was shared. Explain the move, do not re-baseline."
    )
