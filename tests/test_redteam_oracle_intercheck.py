"""Offline tests for the oracle inter-rater check (no live model calls)."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "redteam_oracle_intercheck",
    Path(__file__).resolve().parents[1] / "scripts" / "redteam_oracle_intercheck.py",
)
oc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oc)  # type: ignore[union-attr]


def test_parse_verdict_correct_missed_and_ambiguous():
    assert oc.parse_judge_verdict("CORRECT\nit rejected the attack") is True
    assert oc.parse_judge_verdict("MISSED — it accepted the forgery") is False
    assert oc.parse_judge_verdict("  correct") is True  # case-insensitive, whitespace-tolerant
    # The original-run failure mode: an overloaded keyword whose meaning is ambiguous -> None,
    # never a silent guess.
    assert oc.parse_judge_verdict("REJECT this review is bad") is None
    assert oc.parse_judge_verdict("") is None


def test_cohen_kappa_perfect_and_chance():
    # Perfect agreement -> kappa 1.0
    assert oc.cohen_kappa([(True, True), (False, False), (True, True)]) == 1.0
    # Total disagreement on a balanced set -> kappa -1.0
    assert oc.cohen_kappa([(True, False), (False, True)]) == -1.0
    # Empty -> NaN (guarded)
    assert math.isnan(oc.cohen_kappa([]))


def test_cohen_kappa_one_disagreement_matches_measured_shape():
    # 22 agree, 1 disagree, mostly-'False' labels (like the live 23-model run) -> substantial kappa.
    pairs = [(False, False)] * 15 + [(True, True)] * 7 + [(True, False)]
    k = oc.cohen_kappa(pairs)
    assert 0.8 < k < 0.95


def test_load_auth01_responses_offline_from_preserved_artifacts():
    # Reads only preserved files on disk (no network); the rated models all have an auth-01 response
    # except the non-responsive nemotron-3-super, so we expect a healthy non-empty mapping.
    resp = oc.load_auth01_responses()
    assert isinstance(resp, dict) and len(resp) >= 20
    assert all(isinstance(v, str) and v for v in resp.values())
