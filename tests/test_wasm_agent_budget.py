"""Tests for agentteams/wasm_agent/budget.py (WASM P05): reserve-before-call, caps, carve, wall clock."""

from __future__ import annotations

import pytest

from agentteams.wasm_agent.budget import Budget, BudgetExceeded, BudgetLedger


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_reserve_commit_refund() -> None:
    led = BudgetLedger(Budget(max_total_tokens=10_000))
    res = led.reserve(3000, 1000)                    # 1000 input estimate + 1000 output
    assert res.tokens == 2000 and led.reserved_tokens == 2000 and led.model_calls == 1
    led.commit(res, 700, None)
    assert (led.tokens, led.reserved_tokens, led.unmetered_calls) == (700, 0, 1)
    res2 = led.reserve(0, 10)
    led.refund(res2)
    assert (led.reserved_tokens, led.model_calls) == (0, 1)


@pytest.mark.parametrize("budget, args, cap", [
    (Budget(max_model_calls=0), (0, 10), "max_model_calls"),
    (Budget(max_output_tokens_per_call=100), (0, 101), "max_output_tokens_per_call"),
    (Budget(max_total_tokens=100), (300, 10), "max_total_tokens"),
    (Budget(max_usd=0.01), (0, 10, 0.02), "max_usd"),
])
def test_each_cap_refuses_before_the_call(budget: Budget, args: tuple, cap: str) -> None:
    led = BudgetLedger(budget)
    with pytest.raises(BudgetExceeded) as exc:
        led.reserve(*args)
    assert exc.value.cap == cap and led.model_calls == 0 and led.reserved_tokens == 0


def test_steps_tools_and_wall_clock() -> None:
    clock = Clock()
    led = BudgetLedger(Budget(max_steps=2, max_tool_calls=3, wall_clock_s=10), clock=clock)
    led.step()
    led.step()
    with pytest.raises(BudgetExceeded, match="max_steps"):
        led.step()
    led.tool_uses(3)
    with pytest.raises(BudgetExceeded, match="max_tool_calls"):
        led.tool_uses(1)
    clock.t += 11
    with pytest.raises(BudgetExceeded, match="wall_clock_s"):
        led.reserve(0, 1)
    assert led.remaining_s() < 0


def test_carve_clamps_and_debits_parent() -> None:
    led = BudgetLedger(Budget(max_total_tokens=1000, max_child_runs=2, max_delegation_depth=2))
    child = led.carve({"max_total_tokens": 5000, "max_steps": 3})
    assert child.max_total_tokens == 1000 and child.max_steps == 3 and child.max_delegation_depth == 1
    with pytest.raises(BudgetExceeded, match="max_total_tokens"):
        led.reserve(0, 10)                           # the child holds the rest
    led.carve()
    with pytest.raises(BudgetExceeded, match="max_child_runs"):
        led.carve()
    with pytest.raises(BudgetExceeded, match="max_delegation_depth"):
        BudgetLedger(Budget(max_delegation_depth=0)).carve()
    with pytest.raises(ValueError):
        BudgetLedger(Budget()).carve({"bogus": 1})


def test_budget_validation_and_round_trip() -> None:
    b = Budget(max_usd=1.5)
    assert Budget.from_dict(b.to_dict()) == b
    for bad in ({"max_steps": -1}, {"wall_clock_s": float("nan")}, {"max_tool_calls": True}, {"wall_clock_s": 0}):
        with pytest.raises(ValueError):
            Budget(**bad)
    with pytest.raises(ValueError):
        Budget.from_dict({"nope": 1})
