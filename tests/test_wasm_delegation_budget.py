"""Budget inheritance for WASM delegation (WASM P09 §4.3).

A child's budget is carved from its parent's remaining one (default share, explicit asks clamped), held as carved
while the child runs and settled to what it really used, so a whole tree never spends more than its root's budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.wasm_agent.budget import Budget, BudgetExceeded, BudgetLedger
from tests._wasm_delegation_helpers import (
    Team, agent, child_ids, grant, native_guests, project, routed, run, run_records, text_reply, tool_reply,
)

__all__ = ["native_guests"]


def _delegate(target: str, i: str = "d1", task: str = "sub-task") -> dict:
    return tool_reply((i, "delegate", {"agent": target, "task": task}))


# --- the ledger -----------------------------------------------------------------------------------------------------

def test_carve_default_share_and_explicit_request_clamped() -> None:
    led = BudgetLedger(Budget(max_model_calls=10, max_total_tokens=1000, max_usd=2.0))
    child = led.carve(share=0.5)
    assert (child.max_model_calls, child.max_total_tokens, child.max_usd) == (5, 500, 1.0)
    big = led.carve({"max_model_calls": 99, "max_total_tokens": 10_000}, share=0.5)
    assert (big.max_model_calls, big.max_total_tokens) == (5, 500)              # clamped to the remainder
    with pytest.raises(ValueError):
        led.carve(share=0)
    with pytest.raises(ValueError):
        led.carve(share=1.5)


def test_carved_slice_is_debited_until_settled_then_refunded() -> None:
    led = BudgetLedger(Budget(max_model_calls=4, max_total_tokens=10_000))
    child = led.carve(share=1.0)
    with pytest.raises(BudgetExceeded, match="max_model_calls"):
        led.reserve(0, 10)                                                       # all calls carved away
    led.settle(child, {"model_calls": 1, "tokens": 300, "usd": 0.0, "unmetered_calls": 1})
    assert (led.carved_calls, led.carved_tokens, led.model_calls, led.tokens) == (0, 0, 1, 300)
    led.reserve(0, 10)                                                           # the unused 3 calls came back
    assert led.totals()["child_runs"] == 1


def test_settle_unstarted_returns_the_fanout_slot_and_unknown_cost_charges_the_reservation() -> None:
    led = BudgetLedger(Budget(max_child_runs=1, max_usd=1.0))
    child = led.carve(share=0.5)
    led.settle(child, {}, started=False)
    assert led.child_runs == 0 and led.usd == 0.0
    child = led.carve(share=0.5)
    led.settle(child, {"model_calls": 1, "usd": 0.01, "unmetered_calls": 1})   # Q-2: cost unknown -> conservative
    assert led.usd == pytest.approx(0.5)


# --- through the host -------------------------------------------------------------------------------------------

def _chain(root: Path) -> Team:
    return Team(agent("orchestrator", grant(root, "docs"), delegates=("a",)),
                agent("a", grant(root, "docs"), delegates=("b",)),
                agent("b", grant(root, "docs")))


def test_child_budget_is_the_default_share_and_the_tree_stays_within_the_root(
        tmp_path: Path, native_guests: list, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    script = routed({"orchestrator": [_delegate("a"), text_reply("root done")],
                     "a": [_delegate("b"), text_reply("a done")],
                     "b": [text_reply("b done")]})
    res = run(root, _chain(root), "orchestrator", script, budget=Budget(max_model_calls=20), monkeypatch=monkeypatch)
    assert res.status == "ok", res
    top = run_records(root, res.run_id)
    a_id = child_ids(top)[0]
    a = run_records(root, a_id)
    b = run_records(root, child_ids(a)[0])
    budgets = [r[0]["budget"] for r in (top, a, b)]
    assert [x["max_model_calls"] for x in budgets] == [20, 9, 4]   # half of what is left: (20-1)/2, (9-1)/2
    totals = [r[-1]["totals"] for r in (top, a, b)]
    assert totals[2]["model_calls"] == 1 and totals[1]["model_calls"] == 3 and totals[0]["model_calls"] == 5
    assert totals[0]["model_calls"] <= 20                           # conservation: the root's totals include the tree
    assert [x["max_delegation_depth"] for x in budgets] == [2, 1, 0]


def test_exhausted_parent_is_denied_and_no_child_run_is_created(
        tmp_path: Path, native_guests: list, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    script = routed({"orchestrator": [_delegate("a"), text_reply("never")]})
    res = run(root, _chain(root), "orchestrator", script, budget=Budget(max_model_calls=1), monkeypatch=monkeypatch)
    recs = run_records(root, res.run_id)
    denied = [r for r in recs if r["kind"] == "denied"]
    assert denied[0]["code"] == "budget" and "no budget left" in denied[0]["why"]
    assert not child_ids(recs)
    assert sorted(p.name for p in (root / ".agentteams" / "wasm-runs").iterdir()) == [res.run_id]
