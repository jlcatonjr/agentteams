"""Offline tests for H4 — sustained multi-turn siege with a configurable turn budget. No live calls."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import redteam_sustained_siege as ss  # noqa: E402


def test_siege_runs_full_turn_budget_no_early_stop():
    # A defender that ALWAYS capitulates: H2's adaptive arm would stop at turn 1; the siege must
    # run every turn in the budget so the erosion curve stays observable after the first breach.
    r = ss.siege_run("seed", lambda t: "COMPLY", lambda s, h: s, turns=6)
    assert r.turns == 6 and len(r.transcript) == 6
    assert r.breach_count == 6 and r.first_breach_turn == 1 and r.held_all_turns is False


def test_siege_defender_that_holds_records_no_breach():
    r = ss.siege_run("seed", lambda t: "HALT", lambda s, h: s, turns=8)
    assert r.held_all_turns is True and r.breach_count == 0 and r.first_breach_turn is None
    assert r.early_breach_rate == 0.0 and r.late_breach_rate == 0.0


def test_siege_late_half_breach_rate_exceeds_early_when_attacker_refines():
    # A payload that only lands once the attacker has accumulated >= 5 turns of context. On this
    # (content-only) defender the late-half breach rate exceeds the early half because the ATTACKER's
    # payload improved over turns — an adaptation signal, not defender erosion (see module docstring).
    def defender(text):
        return "COMPLY" if "WORNDOWN" in text else "REPORT"

    def attacker(seed, transcript):
        return seed + ("WORNDOWN" if len(transcript) >= 5 else "")

    r = ss.siege_run("seed", defender, attacker, turns=10)
    assert r.first_breach_turn == 6  # holds turns 1-5, breaks on turn 6
    assert r.late_breach_rate > r.early_breach_rate  # late half worse: attacker refinement landed
    assert r.held_all_turns is False


def test_siege_first_breach_turn_is_first_not_last():
    calls = {"i": 0}

    def defender(text):
        calls["i"] += 1
        return "COMPLY" if calls["i"] in (3, 7) else "REPORT"

    r = ss.siege_run("seed", defender, lambda s, h: s, turns=8)
    assert r.first_breach_turn == 3 and r.breach_count == 2


def test_siege_rejects_nonpositive_turns():
    with pytest.raises(ValueError):
        ss.siege_run("seed", lambda t: "HALT", lambda s, h: s, turns=0)


def test_campaign_reports_any_breach_rate_and_interval():
    # Deterministic defender/attacker -> every siege breaches on the same turn; interval collapses.
    def defender(text):
        return "COMPLY" if "X" in text else "REPORT"

    def attacker(seed, transcript):
        return seed + ("X" if len(transcript) >= 2 else "")  # breaks on turn 3

    rep = ss.run_siege_campaign("seed", defender, attacker, turns=5, repeats=4)
    assert rep["any_breach_rate"] == 1.0
    assert rep["mean_first_breach_turn"] == 3.0
    assert rep["first_breach_interval"] == [3, 3]
    assert rep["late_minus_early_delta"] >= 0.0  # breaches land in the late half (attacker refinement)


def test_campaign_flat_defender_shows_no_delta():
    rep = ss.run_siege_campaign("seed", lambda t: "HALT", lambda s, h: s, turns=6, repeats=3)
    assert rep["any_breach_rate"] == 0.0 and rep["mean_first_breach_turn"] is None
    assert rep["first_breach_interval"] is None and rep["late_minus_early_delta"] == 0.0


def test_dry_run_makes_no_live_call(capsys):
    rc = ss.main(["--dry-run", "--turns", "10", "--repeats", "2", "--seed-id", "auth-01"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"dry_run": true' in out
    assert '"estimated_calls": 38' in out  # 2 * (10 + 9)


def test_live_path_refused_without_clearance(monkeypatch):
    # A non-dry-run invocation must hit the S7 interlock and refuse (real ledger row is pending).
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    with pytest.raises(ss.ag.LiveClearanceError):
        ss.main(["--seed-id", "auth-01", "--turns", "3", "--repeats", "1"])
