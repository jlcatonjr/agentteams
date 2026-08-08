"""budget.py — the cumulative spend ceiling for any driver that loops a paid run.

**Why this is a module and not three lines in a script.** ``redteam_judgment_run.py`` enforces
``--budget`` and a ``MIN_REMAINING_USD`` floor *per invocation*. Both reset when the process
does. A driver that runs it once per model is therefore bounded by neither: a thirteen-model
comparison authorised at $3.30 could have walked the account down to the floor, roughly $20.49,
without any single child exceeding its own cap.

``@security`` found that during clearance — the plan that proposed the loop did not. The first
fix put a cumulative check inside ``redteam_model_matrix_run.py``, which closed the hole for
that one caller and left it open for the next script to loop the runner. A guard that lives in
the caller is a guard the next caller does not have.

**What this deliberately does not do.** It does not read credit itself. Fetching is the caller's
job, because the caller already holds the token and because a budget module that performs network
I/O cannot be tested without it. This module decides; the caller observes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Refuse to start, or to continue, below this much remaining credit. Single source of truth:
#: ``scripts/redteam_judgment_run.py`` carries the same value for its own per-invocation check,
#: and ``tests/test_redteam_model_matrix.py`` asserts the two agree rather than drift.
MIN_REMAINING_USD = 5.00


@dataclass
class SpendCeiling:
    """A cumulative spend limit observed across several paid invocations.

    Attributes:
        total_budget: Maximum cumulative spend, in USD, across the whole loop.
        floor_usd: Refuse to continue once remaining credit falls below this.
        opening_credit: Credit observed before the first invocation. Set by :meth:`start`.
        last_credit: Most recent observation, for reporting.
    """

    total_budget: float
    floor_usd: float = MIN_REMAINING_USD
    opening_credit: float | None = field(default=None)
    last_credit: float | None = field(default=None)

    def start(self, credit: float | None) -> str:
        """Record the opening balance and decide whether the loop may begin.

        Args:
            credit: Remaining credit in USD, or ``None`` when the provider could not answer.

        Returns:
            An empty string when the loop may proceed, otherwise the reason to refuse.
        """
        if credit is None:
            return "credit is unreadable; a cumulative ceiling cannot be enforced against it"
        if credit < self.floor_usd:
            return f"credit ${credit:.4f} is already under the ${self.floor_usd:.2f} floor"
        self.opening_credit = credit
        self.last_credit = credit
        return ""

    def spent(self, credit: float | None) -> float | None:
        """Return cumulative spend against the opening balance, or ``None`` if unknown."""
        if credit is None or self.opening_credit is None:
            return None
        return self.opening_credit - credit

    def check(self, credit: float | None) -> str:
        """Decide whether the loop may continue after an invocation.

        An unreadable balance **stops** the loop. The alternative — treating "unknown" as
        "fine" — is how a ceiling becomes decorative at exactly the moment it is needed, since
        the provider is likeliest to stop answering under the load the loop is generating.

        Args:
            credit: Remaining credit in USD, or ``None``.

        Returns:
            An empty string to continue, otherwise the reason to abort.
        """
        if self.opening_credit is None:
            return "ceiling was never started; refusing to continue an unmeasured loop"
        if credit is None:
            return "credit endpoint stopped answering; refusing to continue unmeasured"
        self.last_credit = credit
        spent = self.opening_credit - credit
        if spent > self.total_budget:
            return f"cumulative spend ${spent:.4f} exceeded the ${self.total_budget:.2f} ceiling"
        if credit < self.floor_usd:
            return f"credit fell to ${credit:.4f}, under the ${self.floor_usd:.2f} floor"
        return ""

    def render(self) -> str:
        """Return a one-line progress summary for the console."""
        spent = self.spent(self.last_credit)
        if spent is None:
            return f"cumulative spend unknown of ${self.total_budget:.2f}"
        return f"cumulative spend ${spent:.4f} of ${self.total_budget:.2f}"


__all__ = ["MIN_REMAINING_USD", "SpendCeiling"]
