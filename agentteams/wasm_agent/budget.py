"""budget.py — per-run spend and effort caps for the WASM agent host (P05 §4.6).

A :class:`Budget` is agentteams policy, not a provider fact. :class:`BudgetLedger` enforces it: a model call
reserves its worst case (``max_tokens`` plus an input estimate, and USD when priced) *before* the call and commits the
real usage after, refunding the rest; steps, tool calls and the wall clock are counted against a monotonic deadline.
:meth:`BudgetLedger.carve` hands a child run (P09) a slice of what is left, debited from the parent.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

#: Bytes per token for the input estimate (as ``vulnscan.gateway_dialects.BYTES_PER_TOKEN``; deliberately pessimistic).
BYTES_PER_TOKEN = 3


class BudgetExceeded(Exception):
    """A cap would be (or was) exceeded; ``cap`` names it."""

    def __init__(self, cap: str, detail: str = "") -> None:
        """Build the error.

        Args:
            cap: The cap's field name (``max_model_calls``, ``wall_clock_s`` ...).
            detail: Human-readable detail.

        Raises:
            Nothing.
        """
        self.cap = cap
        super().__init__(f"budget exhausted: {cap}" + (f" ({detail})" if detail else ""))


@dataclass(frozen=True)
class Budget:
    """Caps for one run (defaults are agentteams policy)."""

    max_model_calls: int = 50
    max_total_tokens: int = 400_000
    max_output_tokens_per_call: int = 8_192
    max_steps: int = 120
    max_tool_calls: int = 200
    wall_clock_s: float = 1_800.0
    max_usd: float | None = None
    max_delegation_depth: int = 2
    max_child_runs: int = 8

    def __post_init__(self) -> None:
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            if f.name == "max_usd" and v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                raise ValueError(f"Budget.{f.name} must be a finite non-negative number")
        if self.max_output_tokens_per_call < 1 or self.wall_clock_s <= 0:
            raise ValueError("Budget.max_output_tokens_per_call must be >= 1 and wall_clock_s > 0")

    def to_dict(self) -> dict[str, Any]:
        """The budget as a JSON object (for the transcript and the Stage 1 hand-off).

        Returns:
            ``{field: value}``.

        Raises:
            Nothing.
        """
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Budget:
        """Rebuild a budget from :meth:`to_dict` output, refusing unknown fields.

        Args:
            data: The mapping.

        Returns:
            The budget.

        Raises:
            ValueError: Unknown fields or invalid values.
        """
        names = {f.name for f in dataclasses.fields(cls)}
        if not isinstance(data, Mapping) or set(data) - names:
            raise ValueError(f"budget fields must be a subset of {sorted(names)}")
        return cls(**dict(data))


@dataclass(frozen=True)
class Reservation:
    """A pending model call's worst case, held until :meth:`BudgetLedger.commit`."""

    tokens: int
    usd: float


class BudgetLedger:
    """Thread-safe counters for one run against a :class:`Budget`."""

    def __init__(self, budget: Budget, *, clock: Callable[[], float] = time.monotonic) -> None:
        """Start the ledger (the wall-clock deadline starts now).

        Args:
            budget: The caps.
            clock: A monotonic clock (tests inject one).

        Raises:
            Nothing.
        """
        self.budget = budget
        self._clock = clock
        self._deadline = clock() + budget.wall_clock_s
        self._lock = threading.Lock()
        self.model_calls = self.steps = self.tool_calls = self.child_runs = 0
        self.tokens = self.reserved_tokens = 0
        self.usd = self.reserved_usd = 0.0
        self.unmetered_calls = 0
        self.carved_tokens = 0

    # -- time

    def remaining_s(self) -> float:
        """Seconds left on the wall clock (may be negative).

        Returns:
            The remainder.

        Raises:
            Nothing.
        """
        return self._deadline - self._clock()

    def check_clock(self) -> None:
        """Refuse once the wall clock is spent.

        Returns:
            None.

        Raises:
            BudgetExceeded: ``wall_clock_s``.
        """
        if self.remaining_s() <= 0:
            raise BudgetExceeded("wall_clock_s")

    # -- counters

    def step(self) -> None:
        """Count one guest invocation.

        Returns:
            None.

        Raises:
            BudgetExceeded: ``max_steps`` or ``wall_clock_s``.
        """
        self.check_clock()
        with self._lock:
            if self.steps >= self.budget.max_steps:
                raise BudgetExceeded("max_steps")
            self.steps += 1

    def tool_uses(self, n: int) -> None:
        """Count *n* tool uses from one model reply (host and in-guest alike).

        Args:
            n: The number of ``tool_use`` blocks.

        Returns:
            None.

        Raises:
            BudgetExceeded: ``max_tool_calls`` (the counter is not advanced).
        """
        with self._lock:
            if self.tool_calls + n > self.budget.max_tool_calls:
                raise BudgetExceeded("max_tool_calls")
            self.tool_calls += n

    def reserve(self, input_bytes: int, max_tokens: int, usd_estimate: float = 0.0) -> Reservation:
        """Reserve one model call's worst case before making it.

        Args:
            input_bytes: The request's size, for the input-token estimate.
            max_tokens: The call's output ceiling.
            usd_estimate: The worst-case USD cost (0 when unpriced).

        Returns:
            The reservation.

        Raises:
            BudgetExceeded: ``max_model_calls``, ``max_output_tokens_per_call``, ``max_total_tokens``, ``max_usd``
                or ``wall_clock_s``; nothing is reserved.
        """
        self.check_clock()
        b = self.budget
        tokens = -(-max(0, input_bytes) // BYTES_PER_TOKEN) + max_tokens
        with self._lock:
            if self.model_calls >= b.max_model_calls:
                raise BudgetExceeded("max_model_calls")
            if max_tokens > b.max_output_tokens_per_call:
                raise BudgetExceeded("max_output_tokens_per_call", f"{max_tokens} > {b.max_output_tokens_per_call}")
            if self.tokens + self.reserved_tokens + self.carved_tokens + tokens > b.max_total_tokens:
                raise BudgetExceeded("max_total_tokens")
            if b.max_usd is not None and self.usd + self.reserved_usd + usd_estimate > b.max_usd:
                raise BudgetExceeded("max_usd")
            self.model_calls += 1
            self.reserved_tokens += tokens
            self.reserved_usd += usd_estimate
        return Reservation(tokens, usd_estimate)

    def commit(self, reservation: Reservation, tokens: int, usd: float | None) -> None:
        """Replace a reservation by the call's real usage (the unused part is refunded).

        Args:
            reservation: From :meth:`reserve`.
            tokens: Real input + output tokens.
            usd: Real USD cost, or None when unmetered.

        Returns:
            None.

        Raises:
            Nothing (an over-run is recorded; the next :meth:`reserve` refuses).
        """
        with self._lock:
            self.reserved_tokens -= reservation.tokens
            self.reserved_usd -= reservation.usd
            self.tokens += max(0, int(tokens))
            if usd is None:
                self.unmetered_calls += 1
            else:
                self.usd += max(0.0, float(usd))

    def refund(self, reservation: Reservation) -> None:
        """Drop a reservation whose call was never made (the call count is returned too).

        Args:
            reservation: From :meth:`reserve`.

        Returns:
            None.

        Raises:
            Nothing.
        """
        with self._lock:
            self.reserved_tokens -= reservation.tokens
            self.reserved_usd -= reservation.usd
            self.model_calls -= 1

    def carve(self, request: Mapping[str, Any] | None = None) -> Budget:
        """A child run's budget: *request* clamped to what is left, debited from this ledger (P09).

        Args:
            request: Optional ``{field: value}`` asks (unknown fields refused).

        Returns:
            The child's :class:`Budget`.

        Raises:
            BudgetExceeded: ``max_child_runs`` or ``max_delegation_depth`` (depth 0 left).
            ValueError: An unknown field in *request*.
        """
        req = dict(request or {})
        names = {f.name for f in dataclasses.fields(Budget)}
        if set(req) - names:
            raise ValueError(f"unknown budget fields {sorted(set(req) - names)}")
        with self._lock:
            b = self.budget
            if self.child_runs >= b.max_child_runs:
                raise BudgetExceeded("max_child_runs")
            if b.max_delegation_depth < 1:
                raise BudgetExceeded("max_delegation_depth")
            left_tokens = max(0, b.max_total_tokens - self.tokens - self.reserved_tokens - self.carved_tokens)
            left = {
                "max_model_calls": max(0, b.max_model_calls - self.model_calls),
                "max_total_tokens": left_tokens,
                "max_output_tokens_per_call": b.max_output_tokens_per_call,
                "max_steps": max(0, b.max_steps - self.steps),
                "max_tool_calls": max(0, b.max_tool_calls - self.tool_calls),
                "wall_clock_s": max(0.001, self.remaining_s()),
                "max_usd": None if b.max_usd is None else max(0.0, b.max_usd - self.usd - self.reserved_usd),
                "max_delegation_depth": b.max_delegation_depth - 1,
                "max_child_runs": max(0, b.max_child_runs - self.child_runs - 1),
            }
            out: dict[str, Any] = {}
            for name, cap in left.items():
                ask = req.get(name)
                if ask is None or cap is None:
                    out[name] = cap if ask is None else ask
                else:
                    out[name] = min(ask, cap)
            child = Budget(**out)
            self.child_runs += 1
            self.carved_tokens += child.max_total_tokens
        return child

    def totals(self) -> dict[str, Any]:
        """The counters, for the transcript and :class:`RunResult`.

        Returns:
            A JSON object.

        Raises:
            Nothing.
        """
        with self._lock:
            return {"model_calls": self.model_calls, "steps": self.steps, "tool_calls": self.tool_calls,
                    "tokens": self.tokens, "usd": round(self.usd, 6), "unmetered_calls": self.unmetered_calls,
                    "child_runs": self.child_runs}
