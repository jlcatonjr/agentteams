"""test_constitutional_redteam.py — the red-team battery, run as a standing regression.

The battery was written on 2026-08-06 to audit whether this project's constitution (C-1..C-5)
could be overridden. It found 21 working exploits. Every one of them is now either fixed, or
recorded as a deliberate limitation with its residue named.

Keeping it in `tmp/` would have meant the exploit inventory and the test suite drift apart the
moment someone refactors a control. So it runs here, on every suite run, and the assertion is
the one that matters: **no probe may return EXPLOITED.** A regression in any of the fixes
re-opens a measured attack, and the run says which one.

The battery's other outcome classes are permitted and are asserted individually below, so a
fix that quietly downgrades itself to "documented limitation" also fails.
"""

from __future__ import annotations

import pytest

from tests.constitutional_redteam_battery import (  # noqa: E402
    DEFENDED,
    DOC_LIMIT,
    EXPLOITED,
    OUT_OF_TIER,
    PARTIAL,
    PROBES,
    RESULTS,
)

#: Probes allowed to return something other than DEFENDED, each with the reason. A probe not in
#: this map MUST be DEFENDED. Adding an entry is a deliberate act that has to state a reason —
#: which is the point: it makes accepting a weakness visible in a diff rather than in a tally.
ALLOWED_NON_DEFENDED: dict[str, tuple[str, str]] = {
    "A5": (PARTIAL,
           "the `scope` column narrows when present, and a scope-SHAPED action_reviewed suffix "
           "with no scope column now warns that it restricts nothing. Still PARTIAL because "
           "`scope` is opt-in: making it mandatory would invalidate every existing clearance "
           "row in every deployed team"),
    "A9": (OUT_OF_TIER,
           "T2 (operator shell) is out of scope for every control here; the HMAC is sound and "
           "the key is the trust anchor. The sub-finding closed: an off-roster approver is now "
           "refused, and key custody is documented in docs_src/security-hardening-guide.md"),
    "A10": (PARTIAL,
            "producer and verifier now agree (the digest is emitted AND checked, and the "
            "intel-key constant was pointed at keys that exist — see "
            "tests/test_security_intel_digest.py). Still PARTIAL because a payload carrying no "
            "digest cannot be verified, so pre-2026-08-06 caches remain unbound: a ratchet on "
            "new payloads, not a guarantee about old ones"),
    "B4": (DOC_LIMIT,
           "semantic (non-literal) overrides are outside the deterministic scanner by design; "
           "scan.py states this explicitly"),
    "C3": (DOC_LIMIT,
           "front matter cannot be fenced, so a capability grant has no restore-on-update "
           "guarantee; the escalation is reported rather than reverted"),
}


@pytest.fixture(scope="module")
def results() -> dict:
    """Run every probe once and index the outcomes by probe id."""
    RESULTS.clear()
    # Deliberately uncaught (CH-24): a probe that raises errors this fixture, which errors every
    # test in the module. That is the loud failure a security battery should have — swallowing
    # it into a PROBE-ERROR row would let a battery of broken probes report all-clear.
    for probe in PROBES:
        probe()
    return {p.pid: p for p in RESULTS}


def test_every_probe_ran(results: dict) -> None:
    """Anti-vacuity: the assertions below say nothing if the battery silently shrank."""
    assert len(results) == len(PROBES), (
        f"{len(PROBES)} probes defined but {len(results)} results recorded — a probe returned "
        f"without calling record(), so its attack was never scored"
    )


def test_no_measured_exploit_survives(results: dict) -> None:
    """The headline assertion. Any EXPLOITED result is a re-opened attack."""
    exploited = [
        f"{p.pid} [{p.article}/{p.tier}] {p.name} — {p.evidence[:160]}"
        for p in results.values() if p.outcome == EXPLOITED
    ]
    assert not exploited, "constitutional exploits are live again:\n" + "\n".join(exploited)


def test_every_non_defended_probe_is_explicitly_accepted(results: dict) -> None:
    """A weakness may be accepted, but only on the record.

    Without this, a fix could regress from DEFENDED to PARTIAL and the suite would stay green
    because PARTIAL is not EXPLOITED. Accepting a weakness has to cost a diff.
    """
    unexpected = []
    for p in results.values():
        if p.outcome == DEFENDED:
            continue
        allowed = ALLOWED_NON_DEFENDED.get(p.pid)
        if allowed is None or allowed[0] != p.outcome:
            expected = allowed[0] if allowed else DEFENDED
            unexpected.append(f"{p.pid}: got {p.outcome}, accepted value is {expected}")
    assert not unexpected, (
        "probe outcomes changed without an accompanying entry in ALLOWED_NON_DEFENDED:\n"
        + "\n".join(unexpected)
    )


def test_the_accepted_list_has_no_stale_entries(results: dict) -> None:
    """The other direction: a probe that got FIXED must lose its exemption.

    Otherwise the accepted list silently accumulates permission for weaknesses that no longer
    exist, and stops describing the system.
    """
    stale = [
        pid for pid, (outcome, _) in ALLOWED_NON_DEFENDED.items()
        if pid in results and results[pid].outcome == DEFENDED
    ]
    assert not stale, (
        f"these probes now DEFEND and no longer need an exemption: {stale}. "
        f"Remove them from ALLOWED_NON_DEFENDED."
    )


def test_controls_still_pass(results: dict) -> None:
    """Anti-vacuity: the control probes must be DEFENDED, or the battery proves nothing.

    A battery whose controls fail is measuring a broken harness, not a secure system — and it
    would report all-clear either way.
    """
    failed_controls = [
        p.pid for p in results.values()
        if p.name.startswith("CONTROL:") and p.outcome != DEFENDED
    ]
    assert not failed_controls, f"control probes failed: {failed_controls}"
