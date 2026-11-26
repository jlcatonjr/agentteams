"""The mediator's charge when a provider call fails (#573).

A failure after at least one HTTP attempt may have been billed, so the reservation's worst case is kept: tokens
always, and USD when the run is priced (under a ``max_usd`` cap it always is). A failure before any attempt refunds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import model_providers as MP
from agentteams.wasm_agent.budget import Budget, BudgetExceeded, BudgetLedger
from agentteams.wasm_agent.mediation import MediationError, Mediator, OutboundDlp, ReservedKeyVault
from agentteams.wasm_agent.protocol import canonical
from agentteams.wasm_agent.transcript import FileJournal

from tests._wasm_agent_helpers import KEY, RUN, no_network, project, stub  # noqa: F401  (no_network: autouse fixture)

PRICES = {"test-model": {"input_per_mtok": 1.0, "output_per_mtok": 2.0, "cache_read_per_mtok": 1.0,
                         "cache_write_per_mtok": 2.0}}


def _failing(attempts: int):
    def script(messages: list, system: str | None, tools: tuple) -> dict:
        raise MP.ModelProviderError("server", "boom", attempts=attempts)
    return script


def _mediator(tmp_path: Path, attempts: int, *, prices: dict | None, budget: Budget | None = None) -> Mediator:
    root = project(tmp_path)
    med = Mediator(provider=stub(_failing(attempts)), model="test-model", system="You help.", max_tokens=512,
                   tools=(), ledger=BudgetLedger(budget or Budget()),
                   journal=FileJournal(root, RUN, secrets=(KEY,), mac_key=b"k" * 32), dlp=OutboundDlp((KEY,)),
                   vault=ReservedKeyVault(b"k" * 32), secrets=(KEY,), prices=prices, call_timeout_s=5.0)
    med.start("do the task")
    return med


def _reserved(med: Mediator) -> tuple[int, float]:
    """What one call on the current conversation reserves (taken and refunded on a scratch ledger)."""
    scratch = BudgetLedger(Budget())
    original, med.ledger = med.ledger, scratch
    try:
        size = len(canonical(med.vault.restore(med.messages))) + len(med.system.encode())  # as Mediator.call sizes it
        r = scratch.reserve(size, med.max_tokens, med._usd_estimate(size))
    finally:
        med.ledger = original
    return r.tokens, r.usd


def test_failure_after_attempt_keeps_worst_case_tokens_and_usd(tmp_path: Path) -> None:
    med = _mediator(tmp_path, 1, prices=PRICES)
    tokens, usd = _reserved(med)
    assert usd > 0
    with pytest.raises(MediationError) as err:
        med.call()
    led = med.ledger
    assert err.value.code == "provider-unavailable"
    assert led.tokens == tokens and led.usd == pytest.approx(usd) and led.unmetered_calls == 0
    assert led.reserved_tokens == 0 and led.reserved_usd == pytest.approx(0.0) and led.model_calls == 1


def test_failure_after_attempt_unpriced_counts_unmetered(tmp_path: Path) -> None:
    med = _mediator(tmp_path, 2, prices=None)
    tokens, _ = _reserved(med)
    with pytest.raises(MediationError):
        med.call()
    led = med.ledger
    assert led.tokens == tokens and led.usd == 0 and led.unmetered_calls == 1 and led.model_calls == 1


def test_failure_before_attempt_refunds(tmp_path: Path) -> None:
    """Negative control: nothing was sent, so nothing is charged and the call is returned."""
    med = _mediator(tmp_path, 0, prices=PRICES)
    with pytest.raises(MediationError):
        med.call()
    led = med.ledger
    assert led.tokens == 0 and led.usd == 0 and led.model_calls == 0 and led.unmetered_calls == 0
    assert led.reserved_tokens == 0 and led.reserved_usd == pytest.approx(0.0)


def test_usd_cap_reached_by_repeated_failures(tmp_path: Path) -> None:
    probe = _mediator(tmp_path / "probe", 1, prices=PRICES)
    _, usd = _reserved(probe)
    med = _mediator(tmp_path / "run", 1, prices=PRICES, budget=Budget(max_usd=usd * 2.5))
    for _ in range(2):
        with pytest.raises(MediationError):
            med.call()
    with pytest.raises(BudgetExceeded) as err:
        med.call()
    assert err.value.cap == "max_usd"
