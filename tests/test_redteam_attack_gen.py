"""Offline tests for the attack-generation shared core (no live model calls).

These assert the eight safety rails the plan (tmp/by-week/2026-W34/attack-generation-harnesses.plan.md)
makes load-bearing. Every test uses mocked/replayed I/O; none touches the network. The rails, and
the test that pins each:

* S1 host allowlist ....... test_s1_blocks_non_allowlisted_host / _allows_openrouter
* S1/C2 quarantine guard .. test_c2_rejects_dotdot_escape / _rejects_symlink_escape / _allows_in_root
* S2 promotion gate ....... (the standing test lives in test_redteam_promotion_gate.py)
* S3 budget/dry-run ....... test_estimate_cost_is_deterministic
* S4(ii) scorer sensitivity test_scorer_sensitivity_discloses_recall / _void_when_blind
* S5 provenance ........... test_provenance_is_defender_scoped_and_provisional_list
* S7 live interlock ....... test_s7_refuses_live_without_verified_clearance / _clears_with_verified_row
* S8 credential hygiene ... test_s8_no_key_material_in_emitted_artifacts
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import redteam_attack_gen as ag  # noqa: E402


# ---- S1: host allowlist -------------------------------------------------------------------------

def test_s1_blocks_non_allowlisted_host():
    for url in ("https://evil.example.com/x", "http://169.254.169.254/latest", "https://openrouter.ai.evil.com/"):
        with pytest.raises(ag.TargetNotAllowedError):
            ag._assert_target_allowed(url)


def test_s1_allows_openrouter():
    ag._assert_target_allowed("https://openrouter.ai/api/v1/chat/completions")  # must not raise


# ---- S1/C2: symlink-safe quarantine confinement -------------------------------------------------

def test_c2_rejects_dotdot_escape():
    for bad in ("../escape.txt", "../../etc/passwd", "sub/../../out.txt"):
        with pytest.raises(ag.QuarantineEscapeError):
            ag.guard_quarantine_path(Path(bad))


def test_c2_rejects_symlink_escape(tmp_path, monkeypatch):
    # A symlink inside the quarantine root pointing outside must not let a write escape.
    root = tmp_path / "quarantine"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    monkeypatch.setattr(ag, "QUARANTINE_ROOT", root)
    with pytest.raises(ag.QuarantineEscapeError):
        ag.guard_quarantine_path(Path("link") / "loot.txt")


def test_c2_allows_in_root():
    p = ag.guard_quarantine_path(Path("gen/cand-1.txt"))
    assert p.name == "cand-1.txt"
    assert ag.QUARANTINE_ROOT.resolve() in p.parents


def test_write_quarantine_confines(tmp_path, monkeypatch):
    monkeypatch.setattr(ag, "QUARANTINE_ROOT", tmp_path / "q")
    out = ag.write_quarantine("a/b.txt", "hello")
    assert out.read_text() == "hello"
    with pytest.raises(ag.QuarantineEscapeError):
        ag.write_quarantine("../b.txt", "nope")


# ---- S4(ii): scorer-sensitivity disclosure ------------------------------------------------------

def test_scorer_sensitivity_discloses_recall():
    s = ag.measure_scorer_sensitivity()
    # The panel is a deliberate tuned/novel mix: a working scorer catches the tuned ones and misses
    # the novel ones, so recall sits strictly between 0 and 1 — the whole point (it discloses the
    # blindness rather than asserting one control validates the oracle).
    assert 0.0 < s["recall"] < 1.0, s
    assert s["blind"] is False
    assert s["regressions"] == 0, "a tuned-stance capitulation the scorer used to catch now misses"


def test_scorer_void_when_blind(monkeypatch):
    # If the scorer catches NONE of the panel, any automated count is meaningless -> VOID.
    monkeypatch.setattr(ag, "score_response", lambda text, expected: ("MISS", False))
    with pytest.raises(ag.ScorerBlindError):
        ag.assert_scorer_not_blind()


# ---- S3: dry-run cost estimate ------------------------------------------------------------------

def test_estimate_cost_is_deterministic():
    a = ag.estimate_cost(100)
    b = ag.estimate_cost(100)
    assert a == b and a > 0


# ---- S5: provenance -----------------------------------------------------------------------------

def test_provenance_is_defender_scoped_and_provisional_list():
    sens = ag.measure_scorer_sensitivity()
    prov = ag.make_provenance("attacker-model-x", "2026-08-18T00:00:00+00:00", "attacker-model-x",
                              2.0, scorer_sensitivity=sens)
    d = prov.to_dict()
    assert isinstance(d["provisional"], list) and d["provisional"], "provisional must be a note LIST (F4)"
    joined = " ".join(d["provisional"])
    assert "@security" in joined and "LOWER BOUND" in joined  # C1 defender-scope + S4ii lower-bound
    assert any(ag.DEFENDER_SCOPE in n for n in d["notes"])


# ---- S7: live-clearance interlock ---------------------------------------------------------------

def _write_ledger(path: Path, rows: list[dict]) -> None:
    import csv
    cols = ["date", "plan_slug", "step", "decision", "status", "conditions",
            "conditions_verified", "evidence", "owner"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def test_s7_refuses_live_without_verified_clearance(tmp_path):
    # The real committed ledger row is CONDITIONAL PASS / pending -> must refuse.
    cleared, _ = ag.live_clearance_status()  # reads the real ledger
    assert cleared is False
    with pytest.raises(ag.LiveClearanceError):
        ag.require_live_clearance()


def test_s7_refuses_on_pending_row(tmp_path):
    led = tmp_path / "sd.csv"
    _write_ledger(led, [{"date": "2026-08-18", "plan_slug": ag.CAPABILITY_PLAN_SLUG, "step": "3",
                         "decision": "build", "status": "CONDITIONAL PASS", "conditions": "C1-C6",
                         "conditions_verified": "pending", "evidence": "-", "owner": "@security"}])
    cleared, reason = ag.live_clearance_status(led)
    assert cleared is False and "cleared-for-live" in reason


def test_s7_clears_only_with_verified_and_cleared_for_live(tmp_path):
    led = tmp_path / "sd.csv"
    _write_ledger(led, [{"date": "2026-09-01", "plan_slug": ag.CAPABILITY_PLAN_SLUG, "step": "P7",
                         "decision": "live campaign", "status": "cleared-for-live",
                         "conditions_verified": "verified", "evidence": "-", "owner": "@security"}])
    cleared, _ = ag.live_clearance_status(led)
    assert cleared is True
    ag.require_live_clearance(led)  # must not raise


# ---- S8: no credential material in emitted artifacts --------------------------------------------

def test_s8_no_key_material_in_emitted_artifacts(monkeypatch):
    # A provenance stamp / candidate must never carry the API key, even if one is set in the env.
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-SECRETKEYMATERIAL-abcdef123456")
    sens = ag.measure_scorer_sensitivity()
    prov = ag.make_provenance("m", "2026-08-18T00:00:00+00:00", "m", 1.0, scorer_sensitivity=sens)
    blob = prov.to_json()
    cand = ag.AttackCandidate(id="c", taxonomy_class="authority-claim", generator="m",
                              seed="", content="body", provenance=prov.to_dict())
    assert "SECRETKEYMATERIAL" not in blob
    assert "SECRETKEYMATERIAL" not in cand.content
    assert "SECRETKEYMATERIAL" not in str(cand.provenance)
