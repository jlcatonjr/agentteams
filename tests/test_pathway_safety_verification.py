"""Regression tests: Rule S-9 (Pathway Safety Verification) exists inside the security
template's Invariant Core fence, its trigger row and HALT/CONDITIONAL-PASS rows are wired up,
and it doesn't duplicate the existing credential HALT row.

See tmp/by-week/2026-W30/pathway-safety-verification.plan.md.
"""
from __future__ import annotations

from pathlib import Path

import agentteams

_TEMPLATE = (
    Path(agentteams.__file__).parent
    / "templates"
    / "universal"
    / "security.template.md"
)


def _template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _fence() -> tuple[str, int, int]:
    text = _template_text()
    begin = text.index("<!-- AGENTTEAMS:BEGIN security_rules_invariant")
    end = text.index("<!-- AGENTTEAMS:END security_rules_invariant")
    return text, begin, end


def test_fence_version_bumped_to_3():
    text = _template_text()
    assert "<!-- AGENTTEAMS:BEGIN security_rules_invariant v=3 -->" in text
    assert "<!-- AGENTTEAMS:BEGIN security_rules_invariant v=2 -->" not in text


def test_rule_s9_exists_inside_the_invariant_fence():
    text, begin, end = _fence()
    rule_pos = text.index("**Rule S-9: Pathway Safety Verification**")
    assert begin < rule_pos < end


def test_rule_s9_lists_all_five_risk_criteria():
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert begin < rule_start < next_rule < end
    for marker in (
        "non-official / unverified source",
        "Pipes remote content directly into a shell",
        "embedding a live credential/secret value",
        "destructive or hard-to-reverse action",
        "external or shared system",
    ):
        assert marker in body, f"missing S-9 criterion: {marker!r}"


def test_rule_s9_cross_references_s1_and_s4_instead_of_duplicating():
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert "Rule S-1" in body
    assert "Rule S-4" in body
    # Must not re-derive a duplicate destructive-op or credential rule inline.
    assert "does not replace or duplicate S-4" in body


def test_mandatory_review_trigger_row_added():
    text, begin, end = _fence()
    trigger_table = text[begin:text.index("### Security Rules")]
    assert "any Rule S-9 risk criterion" in trigger_table
    assert "Unverified-pathway execution risk" in trigger_table


def test_halt_and_conditional_pass_rows_added_without_duplicating_credential_row():
    text, begin, end = _fence()
    halt_table_start = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    halt_table_end = text.index("> **Precedence rule:**")
    table = text[halt_table_start:halt_table_end]
    assert begin < halt_table_start < halt_table_end < end
    assert "criterion 2 (blind remote-content-to-shell pipe)" in table
    assert "criterion 3 (would require persisting a credential/secret)" in table
    # The existing credential row must still be the one HALT source of truth — S-9's
    # criterion-3 row explicitly defers to it rather than restating a verdict.
    assert "apply the existing credential row above, not a separate verdict" in table
    assert "criterion 1 or 5 only" in table
    assert "CONDITIONAL PASS" in table


def test_s9_does_not_gate_pathways_matching_no_criteria():
    """First-draft-audit regression: the trigger must be risk-shape, not novelty — a
    pathway matching none of the 5 criteria must be explicitly out of scope, not silently
    gated by omission."""
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert "out of scope for this rule entirely" in body


# ---------------------------------------------------------------------------
# @security review findings (post-implementation review, same session) — pinned so they
# can't silently regress.
# ---------------------------------------------------------------------------

def test_s9_criterion_1_covers_typosquatting_not_just_registry_trust():
    """HIGH finding: an official registry doesn't establish package identity — a
    lookalike/typosquatted name on the real registry must still be caught."""
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert "slopsquatting" in body or "typosquatting" in body
    halt_table = text[next_rule:text.index("> **Precedence rule:**")]
    assert "unverified package/artifact identity" in halt_table


def test_s9_blocks_conditional_pass_reuse_loophole():
    """HIGH finding: a never-persisted CONDITIONAL PASS pathway must not be re-grantable
    indefinitely as a perpetual 'first use' to dodge ever reaching a clean PASS."""
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert "does not get to re-roll" in body or "re-roll" in body
    halt_table = text[next_rule:text.index("> **Precedence rule:**")]
    assert "Repeat CONDITIONAL PASS request" in halt_table


def test_s9_criterion_4_names_privilege_escalation_and_persistence_explicitly():
    """MEDIUM finding: sudo/sudoers/cron/launchd persistence mechanisms must be named,
    not left to be caught only incidentally via another criterion."""
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert "sudoers" in body
    assert "cron" in body and "launchd" in body
    halt_table = text[next_rule:text.index("> **Precedence rule:**")]
    assert "privilege escalation or a persistence mechanism" in halt_table


def test_s9_requires_evaluating_combined_pathway_sequences():
    """MEDIUM finding: splitting a risky pathway across separately-benign steps must not
    evade the gate."""
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert "net effect" in body


def test_s9_criterion_5_does_not_gate_routine_public_fetches():
    """Regression: criterion 5 was originally unqualified ("reaches an external or shared
    system"), which would have made every routine public fetch (checking sports scores,
    weather) trigger Rule S-9's mandatory clearance gate -- directly undermining
    cli-tool-discovery.reference.md's own encouragement to try such fetches. Caught by
    @conflict-auditor while auditing tmp/by-week/2026-W30/goose-bridge-entry-actionability.plan.md.
    Criterion 5 must be scoped to privileged/stateful external interaction only."""
    text, begin, end = _fence()
    rule_start = text.index("**Rule S-9: Pathway Safety Verification**")
    next_rule = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_rule]
    assert "privileged or stateful" in body
    assert "routine, read-only fetch of public data" in body


def test_security_decisions_log_paragraph_states_current_schema_not_just_legacy():
    """Code-hygiene/template-library finding: the bottom Security Decisions Log paragraph
    must lead with the real current 9-column schema, not just gesture at it vaguely while
    Rule S-9 states it explicitly (same file, two specificity levels)."""
    text = _template_text()
    para_start = text.index("**Security Decisions Log**")
    para_end = text.index("\n\n", para_start)
    para = text[para_start:para_end]
    assert (
        "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner"
        in para
    )
