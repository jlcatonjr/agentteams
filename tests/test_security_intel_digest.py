"""test_security_intel_digest.py — the freshness claim must be bound to the data it describes.

**The defect.** `_assert_security_intelligence_fresh` authenticated `SECURITY_DATA_GENERATED_AT`
— a string — and nothing else. Rewriting that one field relabelled a months-old snapshot as
fresh and the gate passed (2026-08-06 audit, probe A10 / W10).

**The defect behind the defect.** The first fix hashed
`security_gate._INTEL_BEARING_PLACEHOLDERS`, a tuple naming `THREAT_INTELLIGENCE`,
`SECURITY_VULNERABILITY_WATCH`, `KEV_CATALOG`, `EPSS_SCORES`, `CVE_SUMMARY`. The producer emits
none of those. The overlap was exactly zero, so the digest of a fully-populated payload equalled
the digest of `{}` — a verification that passes for every input, which is worse than no
verification because it reads as coverage.

That is what `test_the_digest_actually_depends_on_the_payload` and
`test_intel_bearing_keys_exist_in_the_producers_output` exist to prevent. The second is the one
that would have caught it: it compares the gate's constant against the producer's real output
rather than against a hand-copied list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.cli.security_gate import (
    _INTEL_BEARING_PLACEHOLDERS,
    _assert_intelligence_digest_matches,
    _stale_intel_blast_radius,
    security_intelligence_digest,
)


def _payload(**overrides: str) -> dict[str, str]:
    base = {
        "SECURITY_DATA_GENERATED_AT": "2026-08-06T00:00:00Z",
        "SECURITY_SOURCE_REGISTRY": "CISA KEV: ok",
        "SECURITY_CURRENT_THREATS_SUMMARY": "CVE-2026-0001 critical",
        "SECURITY_PREVENTION_PLAYBOOK": "patch it",
        "SECURITY_LLM_THREATS_SUMMARY": "LLM01 prompt injection",
        "SECURITY_OSV_PACKAGES_SUMMARY": "none",
        "SECURITY_CONTROL_EVIDENCE_SUMMARY": "CTRL-01 implemented",
        "SECURITY_DATA_FRESHNESS_STATUS": "fresh",
        "SECURITY_DATA_AGE_HOURS": "0.10",
        "SECURITY_DATA_TTL_HOURS": "24",
        "SECURITY_VULNERABILITY_WATCH_JSON": '{"items": 1}',
    }
    base.update(overrides)
    return base


# --- the constant must name real keys ---------------------------------------

def test_intel_bearing_keys_exist_in_the_producers_output(tmp_path: Path) -> None:
    """The gate's key list must be a subset of what the producer emits.

    Derived from the producer at runtime, deliberately, rather than compared against a second
    hand-written list — a hand-written expectation can be wrong in exactly the way the thing it
    checks is wrong, which is how the original defect survived.
    """
    from agentteams import security_refs

    emitted = set(security_refs.build_security_placeholders(output_dir=tmp_path, offline=True))
    missing = [k for k in _INTEL_BEARING_PLACEHOLDERS if k not in emitted]
    assert not missing, (
        f"security_gate._INTEL_BEARING_PLACEHOLDERS names keys the producer does not emit: "
        f"{missing}. The digest would hash empty strings for each, and the blast-radius message "
        f"would take its 'nothing populated' branch on every run."
    )


def test_the_intel_key_list_is_not_empty() -> None:
    """Anti-vacuity: a subset test passes trivially on an empty set."""
    assert _INTEL_BEARING_PLACEHOLDERS


# --- the digest must depend on the data -------------------------------------

def test_the_digest_actually_depends_on_the_payload() -> None:
    """The exact failure the original constant produced: a constant digest."""
    assert security_intelligence_digest(_payload()) != security_intelligence_digest({})


@pytest.mark.parametrize("key", _INTEL_BEARING_PLACEHOLDERS)
def test_changing_any_intel_bearing_value_changes_the_digest(key: str) -> None:
    before = security_intelligence_digest(_payload())
    after = security_intelligence_digest(_payload(**{key: "TAMPERED"}))
    assert before != after, f"digest ignores {key}, so tampering with it is undetectable"


def test_changing_a_non_intel_value_does_not_change_the_digest() -> None:
    """The negative control. An over-broad digest fires on benign changes and gets disabled.

    `SECURITY_DATA_TTL_HOURS` is configuration, not intelligence; a digest that covered it would
    refuse a payload whose only change was a policy setting.
    """
    before = security_intelligence_digest(_payload())
    after = security_intelligence_digest(_payload(SECURITY_DATA_TTL_HOURS="48"))
    assert before == after


# --- end to end: producer and verifier agree --------------------------------

def test_the_producer_emits_a_digest_the_verifier_accepts(tmp_path: Path) -> None:
    from agentteams import security_refs

    placeholders = security_refs.build_security_placeholders(output_dir=tmp_path, offline=True)
    assert placeholders.get("SECURITY_DATA_PAYLOAD_DIGEST"), "producer emits no digest"
    _assert_intelligence_digest_matches(placeholders)  # must not raise


def test_relabelling_a_payload_is_now_detected(tmp_path: Path) -> None:
    """W10's actual attack: change the data, keep the certified digest."""
    from agentteams import security_refs

    placeholders = security_refs.build_security_placeholders(output_dir=tmp_path, offline=True)
    placeholders["SECURITY_CURRENT_THREATS_SUMMARY"] = "(stale snapshot, relabelled)"
    with pytest.raises(RuntimeError, match="digest mismatch"):
        _assert_intelligence_digest_matches(placeholders)


def test_a_payload_without_a_digest_is_not_rejected() -> None:
    """Backward compatibility, stated as a limit rather than assumed.

    Payloads generated before 2026-08-06 carry no digest and cannot be verified. Refusing them
    would break every offline run against an existing cache, so the check returns early — which
    means this is a ratchet on new payloads, not a guarantee about old ones.
    """
    _assert_intelligence_digest_matches(_payload())  # no digest key: must not raise


# --- the operator-facing message must take the right branch -----------------

def test_blast_radius_reports_the_populated_branch_on_a_real_payload() -> None:
    """The pre-existing half of the defect: this sentence was always wrong.

    With a zero-overlap key list, every run reported 'no intel-bearing placeholder is populated'
    — including runs about to interpolate an entire stale threat feed.
    """
    message = _stale_intel_blast_radius(_payload())
    assert "no intel-bearing placeholder" not in message
    assert "intel-bearing placeholder(s) would be interpolated" in message


def test_blast_radius_still_reports_the_empty_branch_when_nothing_is_populated() -> None:
    """Negative control: the 'nothing populated' branch must remain reachable."""
    assert "no intel-bearing placeholder" in _stale_intel_blast_radius({})
