"""The effect classifier's sandbox axis (P15 D2): any row naming a sandbox relaxation needs the operator's Ed25519."""

from __future__ import annotations

import pytest

from agentteams.cli import effect_classifier as ec
from agentteams.cli.sandbox_effects import SANDBOX_RELAXATION_KINDS


@pytest.mark.parametrize("kind", SANDBOX_RELAXATION_KINDS)
@pytest.mark.parametrize("column", ["scope", "action_reviewed", "decision"])
def test_text_only_row_is_relaxing_and_needs_operator_signature(kind, column):
    row = {column: f"{kind}:claude"}
    assert ec.derive_effect_class(row, kind="decision") == ec.RELAXING
    assert ec.is_sandbox_relaxation(row, kind="decision")
    assert ec.requires_operator_signature(row, kind="decision")


@pytest.mark.parametrize("kind", SANDBOX_RELAXATION_KINDS)
def test_effect_relaxes_naming_a_kind_needs_operator_signature(kind):
    row = {"action_reviewed": "benign-label", "effect_relaxes": f"{kind}:goose"}
    assert ec.is_sandbox_relaxation(row, kind="decision")
    assert ec.requires_operator_signature(row, kind="decision")


@pytest.mark.parametrize("grant", ["wasm:a:scratch", "wasm:a:read:data/raw", "native-exec:a"])
def test_sandbox_grant_tokens_need_operator_signature(grant):
    row = {"action_reviewed": "routine-label", "effect_grants": grant}
    assert ec.is_sandbox_relaxation(row, kind="decision")
    assert ec.requires_operator_signature(row, kind="decision")


def test_directive_kind_uses_task_scope():
    assert ec.is_sandbox_relaxation({"task_scope": "wasm_to_os switch for x"}, kind="directive")
    assert ec.requires_operator_signature({"task_scope": "no sandbox please"}, kind="directive")


def test_declared_non_relaxing_on_a_sandbox_row_is_refused():
    with pytest.raises(ec.EffectClassifierError):
        ec.classify_row({"scope": "sandbox-os-off:claude", "effect_class": "non-relaxing"}, kind="decision")


def test_benign_row_is_unchanged():
    row = {"scope": "render-docs", "action_reviewed": "render-docs"}
    assert not ec.is_sandbox_relaxation(row, kind="decision")
    assert ec.derive_effect_class(row, kind="decision") == ec.NON_RELAXING
    assert not ec.requires_operator_signature(row, kind="decision")


def test_widening_read_of_a_trust_root_is_non_eligible_even_signed():
    row = {"scope": "wasm-grant-widen:claude",
           "effect_grants": "wasm:analyst:read:references/authorized-verify-keys"}
    assert "governance/trust" in (ec.non_eligibility_reason(row, kind="decision") or "")
    ok = {"scope": "wasm-grant-widen:claude", "effect_grants": "wasm:analyst:read:data/raw"}
    assert ec.non_eligibility_reason(ok, kind="decision") is None


def test_kinds_are_exported():
    for name in ("SANDBOX_OS_OFF", "SANDBOX_WASM_OFF", "WASM_GRANT_WIDEN", "NATIVE_EXEC_KEEP", "WASM_TO_OS_SWITCH",
                 "MCP_APPROVAL_AUTO"):
        assert name in ec.__all__ and getattr(ec, name) in SANDBOX_RELAXATION_KINDS


def test_kind_argument_is_validated():
    with pytest.raises(ValueError):
        ec.is_sandbox_relaxation({}, kind="row")
