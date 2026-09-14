"""Tests for the effect-based relaxing-class classifier (effect_classifier.py, Workstream D).

Covers: the derived class from structured effect fields; the text fallback when no structure is
declared; the governance-root target predicate (incl. the R1 verify-key store); the fail-closed
INDETERMINATE default; the self-declaration disagreement REFUSAL in both directions; and the
backward-compat guarantee that ordinary benign decision rows stay non-relaxing.
"""

from __future__ import annotations

import pytest

from agentteams.cli import effect_classifier as ec
from agentteams.cli import decision_log


def test_halt_retraction_constant_matches_decision_log():
    """The classifier's HALT-retraction literal must equal decision_log's (pinned, no import cycle)."""
    assert ec._HALT_RETRACTION_VERDICT == decision_log._HALT_RETRACTION_VERDICT


# --- structured-effect derivation -------------------------------------------------------------


def test_granted_capability_is_relaxing():
    row = {"effect_grants": "write:cross-repo"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


def test_relaxes_constraint_axis_is_relaxing():
    row = {"effect_relaxes": "halt:prune-collectors"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


@pytest.mark.parametrize("flag", ["effect_destructive", "effect_cross_repo", "effect_bulk"])
def test_widening_flags_are_relaxing(flag):
    assert ec.derive_effect_class({flag: "yes"}, kind="decision") == ec.RELAXING


def test_governance_target_is_relaxing():
    row = {"effect_targets": "references/authorized-verify-keys/k1.pub.pem"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


def test_destructive_scope_target_is_relaxing_via_denylist_reuse():
    # A target the shared denylist reads as destructive is relaxing without re-listing tokens.
    row = {"effect_targets": "delete-old-manifests"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


def test_benign_declared_target_is_non_relaxing():
    row = {"effect_targets": "docs_src/book/chapter-3.md"}
    assert ec.derive_effect_class(row, kind="decision") == ec.NON_RELAXING


# --- governance-root predicate ----------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "references/authorized-verify-keys/k1.pub.pem",   # R1 verify-key store
        "References\\Authorized-Verify-Keys",              # case + backslash normalization
        "references/authorized_verify_keys/k.pub.pem",     # F-2: underscore spelling
        "references/authorizedVerifyKeys/k.pub.pem",       # F-2: camelCase spelling
        "references/verify-keys-next/k.pub.pem",           # F-2: rotation-dir spelling
        "authorized verify keys",                          # R2: whitespace-separated
        "rotate signing key",                              # R2: whitespace-separated verb
        "AUTHORIZED　VERIFY　KEYS",                # R2: NFKC ideographic space
        "references/security-approvers.txt",
        "references/authorized-managers.txt",
        "references/enforcement-integrity.json",
        "references/agent-privilege.json",
        "references/security-decisions.log.csv",
        "references/management-directives.log.csv",
        ".claude/CLAUDE.md",
        "CLAUDE.md",                                        # F-2: root constitution
        ".github/copilot-instructions.md",                 # F-2: constitution mirror
    ],
)
def test_target_is_governance_root_true(target):
    assert ec.target_is_governance_root(target) is True


@pytest.mark.parametrize("target", ["docs_src/x.md", "agentteams/cli/foo.py", "", "   "])
def test_target_is_governance_root_false(target):
    assert ec.target_is_governance_root(target) is False


# --- text fallback (no structured effect declared) --------------------------------------------


def test_halt_retraction_verdict_is_relaxing_from_text():
    row = {"verdict": "HALT-RETRACTED", "action_reviewed": "anything"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


def test_benign_decision_scope_is_non_relaxing():
    row = {"verdict": "PASS", "action_reviewed": "draft-weekly-report"}
    assert ec.derive_effect_class(row, kind="decision") == ec.NON_RELAXING


def test_destructive_decision_scope_is_relaxing():
    row = {"verdict": "PASS", "action_reviewed": "prune-stale-collectors"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


def test_empty_scope_is_indeterminate():
    row = {"verdict": "PASS", "action_reviewed": ""}
    assert ec.derive_effect_class(row, kind="decision") == ec.INDETERMINATE


def test_indeterminate_is_treated_as_relaxing():
    row = {"verdict": "PASS", "action_reviewed": ""}
    assert ec.is_relaxing(row, kind="decision") is True


def test_directive_benign_scope_non_relaxing():
    row = {"task_scope": "draft-weekly-report"}
    assert ec.derive_effect_class(row, kind="directive") == ec.NON_RELAXING


def test_directive_governance_scope_relaxing():
    row = {"task_scope": "update-signing-key"}
    assert ec.derive_effect_class(row, kind="directive") == ec.RELAXING


# --- self-declaration disagreement REFUSES (both directions) ----------------------------------


def test_declared_benign_but_derived_relaxing_refuses():
    row = {"effect_grants": "write:cross-repo", "effect_class": "non-relaxing"}
    with pytest.raises(ec.EffectClassifierError):
        ec.classify_row(row, kind="decision")


def test_declared_relaxing_but_derived_benign_refuses():
    row = {"effect_targets": "docs_src/x.md", "effect_class": "relaxing"}
    with pytest.raises(ec.EffectClassifierError):
        ec.classify_row(row, kind="decision")


def test_agreeing_declaration_passes():
    row = {"effect_grants": "write:cross-repo", "effect_class": "relaxing"}
    assert ec.classify_row(row, kind="decision") == ec.RELAXING


def test_absent_declaration_uses_derived():
    row = {"effect_targets": "docs_src/x.md"}
    assert ec.classify_row(row, kind="decision") == ec.NON_RELAXING


def test_self_declared_indeterminate_is_not_a_benign_assertion():
    # An author cannot self-assert 'indeterminate' to dodge the elevated path on a relaxing row.
    row = {"effect_grants": "write:cross-repo", "effect_class": "indeterminate"}
    assert ec.classify_row(row, kind="decision") == ec.RELAXING


def test_conservative_declared_relaxing_on_indeterminate_is_accepted():
    # Q4: an honest 'relaxing' declaration on an otherwise-undecidable row must NOT be refused —
    # both take the elevated path. Returns the derived (indeterminate) class without raising.
    row = {"verdict": "PASS", "action_reviewed": "", "effect_class": "relaxing"}
    assert ec.classify_row(row, kind="decision") == ec.INDETERMINATE
    assert ec.is_relaxing(row, kind="decision") is True


def test_declared_non_relaxing_on_indeterminate_refuses():
    # Q4 dangerous direction: claiming benign on a row not PROVEN benign is refused.
    row = {"verdict": "PASS", "action_reviewed": "", "effect_class": "non-relaxing"}
    with pytest.raises(ec.EffectClassifierError):
        ec.classify_row(row, kind="decision")


# --- adversarial F-1: a benign structured axis must NOT suppress a relaxing text axis ----------


def test_benign_target_does_not_suppress_halt_retraction():
    # The keystone hole: one innocuous effect_targets cell alongside a HALT-RETRACTED verdict.
    row = {
        "verdict": "HALT-RETRACTED",
        "action_reviewed": "prune-collectors",
        "effect_targets": "docs_src/notes.md",
    }
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING
    assert ec.is_relaxing(row, kind="decision") is True


def test_benign_target_does_not_suppress_destructive_scope():
    row = {
        "verdict": "PASS",
        "action_reviewed": "prune-stale-collectors",
        "effect_targets": "docs_src/notes.md",
    }
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


def test_benign_target_does_not_suppress_governance_scope():
    row = {
        "verdict": "PASS",
        "action_reviewed": "update-security-approvers",
        "effect_targets": "docs_src/notes.md",
    }
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING


# --- misc -------------------------------------------------------------------------------------


def test_invalid_kind_raises():
    with pytest.raises(ValueError):
        ec.derive_effect_class({}, kind="bogus")


# --- Workstream E: categorical non-eligibility -----------------------------------------------


def test_non_eligible_governance_target():
    row = {"effect_targets": "references/authorized-verify-keys/k.pub.pem"}
    assert ec.non_eligibility_reason(row, kind="decision") is not None


def test_non_eligible_governance_grant():
    row = {"effect_grants": "write:references/enforcement-integrity.json"}
    assert ec.non_eligibility_reason(row, kind="decision") is not None


def test_non_eligible_sweeping_combination():
    row = {"effect_cross_repo": "yes", "effect_destructive": "yes", "effect_bulk": "yes"}
    assert ec.non_eligibility_reason(row, kind="decision") is not None


def test_non_eligible_governance_scope_text():
    row = {"action_reviewed": "update-security-approvers", "verdict": "PASS"}
    assert ec.non_eligibility_reason(row, kind="decision") is not None


def test_eligible_exception_is_none():
    # A legitimate constraint-relaxing exception (grant cross-repo write to PROJECT files) is
    # eligible — it requires operator Ed25519 but is not categorically forbidden.
    row = {"effect_grants": "write:cross-repo", "effect_cross_repo": "yes"}
    assert ec.non_eligibility_reason(row, kind="decision") is None
    assert ec.requires_operator_signature(row, kind="decision") is True


def test_benign_row_is_eligible():
    row = {"action_reviewed": "draft-weekly-report", "verdict": "PASS"}
    assert ec.non_eligibility_reason(row, kind="decision") is None
    assert ec.requires_operator_signature(row, kind="decision") is False


def test_structured_effect_beats_benign_text():
    # A row with a destructive structured effect but a benign-looking scope text is still relaxing.
    row = {"action_reviewed": "draft-report", "effect_destructive": "true"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING
