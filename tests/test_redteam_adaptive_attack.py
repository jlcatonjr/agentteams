"""Offline tests for H2 — adaptive-attacker vs static-best-of-N (F3). No live calls."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import redteam_adaptive_attack as aa  # noqa: E402


def test_adaptive_arm_stops_on_first_capitulation():
    # Defender capitulates only when the payload contains "KEY"; attacker adds it on round 3.
    def defender(text): return "COMPLY" if "KEY" in text else "REPORT"

    def attacker(seed, transcript):
        return seed + ("KEY" if len(transcript) >= 2 else "")  # earns COMPLY on round 3

    r = aa.adaptive_arm("seed", defender, attacker, max_rounds=5)
    assert r.capitulated and r.attempts == 3 and r.comply_count == 1
    assert len(r.transcript) == 3


def test_adaptive_arm_resists_through_max_rounds():
    r = aa.adaptive_arm("seed", lambda t: "HALT", lambda s, h: s, max_rounds=4)
    assert r.capitulated is False and r.attempts == 4 and r.comply_count == 0


def test_static_best_of_n_capitulates_if_any_attempt_does():
    # A defender that capitulates on a deterministic subset of a counter — simulate non-determinism.
    calls = {"i": 0}

    def flaky(text):
        calls["i"] += 1
        return "COMPLY" if calls["i"] == 3 else "REPORT"  # 3rd of N resamples capitulates

    r = aa.static_best_of_n("seed", flaky, n=5)
    assert r.capitulated is True and r.comply_count == 1 and r.attempts == 5


def test_static_best_of_n_never_capitulates_on_a_solid_defender():
    r = aa.static_best_of_n("seed", lambda t: "HALT", n=5)
    assert r.capitulated is False and r.comply_count == 0


def test_comparison_reports_lift_over_static_not_over_round_one():
    # A defender that ALWAYS capitulates: both arms hit 100%, so adaptive lift is 0 — the point of
    # the control. A naive "adaptive best-of-N vs static round-1" would still show adaptive=100% and
    # could be misread as adaptation working; the static-best-of-N control nets that out.
    rep = aa.run_comparison("seed", lambda t: "COMPLY", lambda s, h: s, n=4, repeats=3)
    assert rep["adaptive_capitulation_rate"] == 1.0
    assert rep["static_best_of_n_capitulation_rate"] == 1.0
    assert rep["adaptive_lift_over_static"] == 0.0
    assert rep["lift_interval"] == [0, 0]


def test_comparison_credits_real_adaptation():
    # Static (bare seed) never works; adaptation earns COMPLY -> positive lift.
    def defender(text): return "COMPLY" if "EVASION" in text else "REPORT"

    def attacker(seed, transcript): return seed + "EVASION"

    rep = aa.run_comparison("seed", defender, attacker, n=4, repeats=3)
    assert rep["static_best_of_n_capitulation_rate"] == 0.0
    assert rep["adaptive_capitulation_rate"] == 1.0
    assert rep["adaptive_lift_over_static"] == 1.0
    assert rep["mean_rounds_to_capitulation"] == 2.0  # seed fails round 1, refined wins round 2


def test_dry_run_makes_no_live_call(capsys):
    rc = aa.main(["--dry-run", "--n", "3", "--repeats", "2", "--seed-id", "auth-01"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"dry_run": true' in out and '"estimated_calls": 12' in out  # 2*(3+3)


def test_live_path_refused_without_clearance(monkeypatch):
    # A non-dry-run invocation must hit the S7 interlock and refuse (real ledger row is pending).
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    with pytest.raises(aa.ag.LiveClearanceError):
        aa.main(["--seed-id", "auth-01", "--n", "2", "--repeats", "1"])
