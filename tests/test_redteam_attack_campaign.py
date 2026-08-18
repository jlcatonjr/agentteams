"""Offline tests for H3 — automated attack generation + diverse review (F10). No live calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import redteam_attack_campaign as camp  # noqa: E402


def test_dedup_drops_exact_duplicates():
    import hashlib

    def rec(text):
        return {"content": text, "content_sha256": hashlib.sha256(text.encode()).hexdigest()}

    got = camp.dedup([rec("a"), rec("a"), rec("b")])
    assert [c["content"] for c in got] == ["a", "b"]


def test_review_judge_is_distinct_from_capitulation_scorer():
    # The review judge answers validity, not capitulation — it must use review_fn, not score_response.
    calls = []

    def review_fn(text, cls):
        calls.append((text, cls))
        return {"valid": True, "reason": "looks like a real injection of that class"}

    out = camp.review_candidate_validity("some payload", "authority-claim", review_fn)
    assert out["valid"] is True and calls == [("some payload", "authority-claim")]


def test_campaign_reports_automated_vs_human_split(tmp_path, monkeypatch):
    import scripts.redteam_attack_gen as ag
    monkeypatch.setattr(ag, "QUARANTINE_ROOT", tmp_path / "q")

    # Generator makes 2 per class; reviewer accepts only candidates containing "good".
    def generate_fn(cls, n):
        return [f"good {cls} 0", f"bad {cls} 1"][:n]

    def review_fn(text, cls):
        return {"valid": "good" in text, "reason": "heuristic"}

    rep = camp.run_campaign(["authority-claim", "tool-arg"], 2, generate_fn, review_fn,
                            clock_iso="2026-08-18T00:00:00+00:00")
    assert rep.n_generated == 4 and rep.n_unique == 4
    assert rep.n_reviewer_accepted == 2 and rep.acceptance_rate == 0.5
    assert rep.per_class["authority-claim"] == {"unique": 2, "accepted": 1}


def test_campaign_quarantines_and_never_touches_corpus(tmp_path, monkeypatch):
    import scripts.redteam_attack_gen as ag
    monkeypatch.setattr(ag, "QUARANTINE_ROOT", tmp_path / "q")
    corpus = REPO_ROOT / "tests" / "redteam" / "payloads.json"
    before = corpus.read_bytes()

    rep = camp.run_campaign(["authority-claim"], 2, lambda c, n: [f"x{i}" for i in range(n)],
                            lambda t, c: {"valid": True, "reason": ""},
                            clock_iso="2026-08-18T00:00:00+00:00")
    assert rep.quarantine_path is not None
    manifest = json.loads(Path(rep.quarantine_path).read_text())
    assert all(m["provenance"]["provisional"] for m in manifest)
    assert (tmp_path / "q").resolve() in Path(rep.quarantine_path).resolve().parents
    assert corpus.read_bytes() == before, "campaign must not modify the tracked corpus"


def test_dedup_collapses_a_repetitive_generator(tmp_path, monkeypatch):
    import scripts.redteam_attack_gen as ag
    monkeypatch.setattr(ag, "QUARANTINE_ROOT", tmp_path / "q")
    # Generator emits the same string every time -> unique collapses to 1 per class.
    rep = camp.run_campaign(["authority-claim"], 5, lambda c, n: ["same"] * n,
                            lambda t, c: {"valid": True, "reason": ""},
                            clock_iso="2026-08-18T00:00:00+00:00")
    assert rep.n_generated == 5 and rep.n_unique == 1


def test_dry_run_makes_no_live_call(capsys):
    rc = camp.main(["--dry-run", "--classes", "a,b", "--n-per-class", "3"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"dry_run": true' in out and '"estimated_calls": 12' in out  # 2 classes * 3 * 2


def test_live_path_refused_without_clearance(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    with pytest.raises(camp.ag.LiveClearanceError):
        camp.main(["--classes", "authority-claim", "--n-per-class", "1"])
