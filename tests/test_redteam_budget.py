"""test_redteam_budget.py — the cumulative ceiling must actually bound a loop.

The defect this guards: ``redteam_judgment_run.py`` enforces ``--budget`` and
``MIN_REMAINING_USD`` *per invocation*, and both reset when the process does. A driver looping it
once per model was bounded by neither — a thirteen-model comparison authorised at $3.30 could
have reached the floor near $20.49 with no single child exceeding its own cap. ``@security``
found that during clearance; the plan proposing the loop did not.

Nothing failed when the cumulative check was deleted, because there was no test. These are it.
"""

from __future__ import annotations

from pathlib import Path

from agentteams.redteam.budget import MIN_REMAINING_USD, SpendCeiling

REPO_ROOT = Path(__file__).resolve().parents[1]


# ===========================================================================
# the ceiling bounds the loop
# ===========================================================================

def test_a_loop_cannot_exceed_its_cumulative_ceiling() -> None:
    """Fires: the exact scenario security clearance flagged.

    Each step costs less than any per-invocation cap would catch; only the running total does.
    """
    ceiling = SpendCeiling(total_budget=3.30)
    assert ceiling.start(25.00) == ""

    credit = 25.00
    for _ in range(10):
        credit -= 0.50  # no single step is remarkable
        verdict = ceiling.check(credit)
        if verdict:
            break
    else:  # pragma: no cover - only reached on a regression
        raise AssertionError("ten steps spent $5.00 against a $3.30 ceiling without aborting")

    assert "exceeded" in verdict
    assert ceiling.spent(credit) > 3.30


def test_the_floor_stops_the_loop_even_within_budget() -> None:
    """A generous ceiling must not authorise emptying the account."""
    ceiling = SpendCeiling(total_budget=100.00)
    ceiling.start(6.00)
    verdict = ceiling.check(4.00)
    assert "floor" in verdict


def test_a_loop_inside_its_ceiling_is_not_stopped() -> None:
    """Negative control: a ceiling that aborted healthy loops would be removed within a day."""
    ceiling = SpendCeiling(total_budget=3.30)
    ceiling.start(25.00)
    assert ceiling.check(24.00) == ""
    assert ceiling.check(23.00) == ""


# ===========================================================================
# unknown is not "fine"
# ===========================================================================

def test_an_unreadable_balance_refuses_to_start() -> None:
    assert "unreadable" in SpendCeiling(total_budget=3.30).start(None)


def test_an_unreadable_balance_stops_a_running_loop() -> None:
    """The provider is likeliest to stop answering under the load the loop is generating,
    which is exactly when treating unknown as fine would be most expensive."""
    ceiling = SpendCeiling(total_budget=3.30)
    ceiling.start(25.00)
    assert "unmeasured" in ceiling.check(None)


def test_starting_below_the_floor_is_refused() -> None:
    assert "floor" in SpendCeiling(total_budget=3.30).start(1.00)


def test_checking_without_starting_is_refused() -> None:
    """An unstarted ceiling has no opening balance, so it cannot bound anything."""
    assert "never started" in SpendCeiling(total_budget=3.30).check(20.00)


# ===========================================================================
# the constant has one home
# ===========================================================================

def test_the_floor_agrees_with_the_child_script() -> None:
    """Duplicated constants drift; this is the test that notices."""
    source = (REPO_ROOT / "scripts" / "redteam_judgment_run.py").read_text(encoding="utf-8")
    assert f"MIN_REMAINING_USD = {MIN_REMAINING_USD:.2f}" in source


def test_the_matrix_runner_uses_the_shared_ceiling_not_its_own() -> None:
    """The fix was moving this OUT of the caller. A caller that re-implements it has undone
    the fix while keeping the import."""
    source = (REPO_ROOT / "scripts" / "redteam_model_matrix_run.py").read_text(encoding="utf-8")
    assert "from agentteams.redteam.budget import" in source
    assert "SpendCeiling(" in source
    assert "started_credit" not in source, (
        "the caller is tracking its own opening balance again; the ceiling has been "
        "re-implemented locally and the next caller will not inherit it"
    )
