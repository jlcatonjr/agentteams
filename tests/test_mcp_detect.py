"""Tests for agentteams.mcp_detect — the MCP-suitability detection rubric.

Covers the §5 necessary-condition gate and the fail-closed / strict-coercion
hardening introduced after the adversarial audit (report §9 + step-2 audit).
"""

from __future__ import annotations

from agentteams import mcp_detect as md
from agentteams.analyze import build_manifest


def _hint(**kw):
    base = {"integration": "warehouse", "trust_tier": "first-party"}
    base.update(kw)
    return base


# --- core three-way gate (§5.2) ---------------------------------------------

def test_build_when_both_necessary_conditions_hold_via_components():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"]))
    assert c.recommendation == md.BUILD_MCP


def test_build_when_cross_host_via_target_count():
    c = md.evaluate_hint(_hint(stateful=True), target_host_count=2)
    assert c.recommendation == md.BUILD_MCP


def test_direct_when_single_component_single_host():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a"]), target_host_count=1)
    assert c.recommendation == md.USE_DIRECT_API


def test_direct_when_not_stateful():
    c = md.evaluate_hint(_hint(stateful=False, used_by_components=["a", "b"]))
    assert c.recommendation == md.USE_DIRECT_API


def test_defer_on_third_party_even_if_conditions_hold():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"], trust_tier="third-party-vetted"))
    assert c.recommendation == md.DEFER_TO_SECURITY_REVIEW
    assert c.signals["hard_gate"] is True


def test_defer_on_destructive_side_effect():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"], max_side_effect="destructive"))
    assert c.recommendation == md.DEFER_TO_SECURITY_REVIEW


# --- fail-closed posture (step-2 audit Q5) ----------------------------------

def test_missing_trust_tier_fails_closed_to_defer():
    c = md.evaluate_hint({"integration": "x", "stateful": True, "used_by_components": ["a", "b"]})
    assert c.recommendation == md.DEFER_TO_SECURITY_REVIEW


def test_unknown_trust_tier_fails_closed_to_defer():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"], trust_tier="thirdparty"))
    assert c.recommendation == md.DEFER_TO_SECURITY_REVIEW


def test_unknown_side_effect_fails_closed_to_defer():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"], max_side_effect="delete"))
    assert c.recommendation == md.DEFER_TO_SECURITY_REVIEW


# --- strict boolean coercion (step-2 audit Q4) ------------------------------

def test_stringy_false_stateful_is_not_truthy():
    # "false" must NOT flip statefulness True (would wrongly recommend BUILD)
    c = md.evaluate_hint(_hint(stateful="false", used_by_components=["a", "b"]))
    assert c.signals["statefulness"] is False
    assert c.recommendation == md.USE_DIRECT_API


def test_stringy_booleans_strict_for_surface_and_disclosure():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"],
                               large_dynamic_surface="false", commits_lazy_disclosure="true"))
    assert c.signals["large_dynamic_surface"] is False
    assert c.signals["commits_lazy_disclosure"] is False


# --- list guard (step-2 audit Q4) -------------------------------------------

def test_used_by_components_non_list_does_not_crash_or_inflate():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components="comp-a"), target_host_count=1)
    # a stray string must not be counted as >=2 components
    assert c.signals["cross_host_reuse"] is False
    assert c.recommendation == md.USE_DIRECT_API


# --- efficiency_risk tiebreaker (§4.1 / §5.1) -------------------------------

def test_efficiency_risk_flagged_when_large_surface_without_lazy():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"], large_dynamic_surface=True))
    assert c.recommendation == md.BUILD_MCP
    assert c.signals["efficiency_risk"] is True
    assert "RISK" in c.rationale


def test_no_efficiency_risk_when_lazy_committed():
    c = md.evaluate_hint(_hint(stateful=True, used_by_components=["a", "b"],
                               large_dynamic_surface=True, commits_lazy_disclosure=True))
    assert c.signals["efficiency_risk"] is False


# --- slug dedup (step-2 audit Q6) -------------------------------------------

def test_duplicate_slugs_are_disambiguated():
    desc = {"mcp_hints": [
        {"integration": "Jira Cloud", "trust_tier": "first-party"},
        {"integration": "jira-cloud", "trust_tier": "first-party"},
    ]}
    cands = md.detect_mcp_candidates(desc)
    ids = [c.candidate_id for c in cands]
    assert len(ids) == len(set(ids)), ids
    assert "jira-cloud" in ids and "jira-cloud-2" in ids


# --- empty / default ---------------------------------------------------------

def test_no_hints_returns_empty():
    assert md.detect_mcp_candidates({}) == []


# --- analyze.py wiring -------------------------------------------------------

def test_manifest_has_no_mcp_key_without_hints():
    m = build_manifest({"project_goal": "a goal here", "project_name": "p"})
    assert "mcp_candidates" not in m


def test_manifest_populates_mcp_candidates_with_hints():
    m = build_manifest({
        "project_goal": "a goal here",
        "project_name": "p",
        "mcp_hints": [{"integration": "wh", "stateful": True,
                       "used_by_components": ["a", "b"], "trust_tier": "first-party"}],
    })
    assert m["mcp_candidates"][0]["recommendation"] == md.BUILD_MCP
