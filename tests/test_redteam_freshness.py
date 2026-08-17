"""Regression tests for agentteams/redteam/freshness.py (improvement item I2).

No test in this file makes a real network call — `research.search.web_search` is
mocked at its source location (`freshness.py` does a lazy, in-function import, so
patching the source module is what actually takes effect on each call).

See tmp/by-week/2026-W34/redteam-osint-ost-implementation.plan.md.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

from agentteams.redteam.freshness import (
    Candidate,
    gather_candidates,
    render_candidates_report,
    run_freshness_check,
)


def _fake_source(title: str, url: str, snippet: str):
    from agentteams.research.search import Source

    return Source(title=title, url=url, snippet=snippet)


def test_gather_candidates_uses_the_declared_query_set():
    seen_queries = []

    def fake_web_search(query, k=3, timeout_s=8.0):
        seen_queries.append(query)
        return [_fake_source("A Result", "https://example.test/a", "snippet")]

    with patch("agentteams.research.search.web_search", side_effect=fake_web_search):
        candidates = gather_candidates(("q1", "q2"))

    assert seen_queries == ["q1", "q2"]
    assert len(candidates) == 2
    assert all(isinstance(c, Candidate) for c in candidates)


def test_gather_candidates_degrades_to_empty_list_without_the_research_extra(monkeypatch):
    """Simulates `agentteams[research]` not being installed: the lazy import must fail
    cleanly, not raise out of gather_candidates."""
    monkeypatch.setitem(sys.modules, "agentteams.research.search", None)
    candidates = gather_candidates(("q1",))
    assert candidates == []


def test_render_candidates_report_groups_by_query_and_never_claims_verification():
    candidates = [
        Candidate(query="q1", title="T1", url="https://example.test/1", snippet="s1"),
        Candidate(query="q1", title="T2", url="https://example.test/2", snippet="s2"),
    ]
    text = render_candidates_report(candidates, generated_at="2026-08-17T00:00:00Z")
    assert "## q1" in text
    assert "T1" in text and "T2" in text
    assert "verified" not in text.lower()  # honesty ceiling: never claims verification
    assert "nothing here has been corroborated" in text


def test_render_candidates_report_empty_explains_why():
    text = render_candidates_report([])
    assert "No candidates" in text


def test_run_freshness_check_writes_under_references(tmp_path):
    def fake_web_search(query, k=3, timeout_s=8.0):
        return [_fake_source("Found", "https://example.test/x", "snippet")]

    with patch("agentteams.research.search.web_search", side_effect=fake_web_search):
        path = run_freshness_check(tmp_path)

    assert path == tmp_path / "references" / "redteam-freshness-candidates.md"
    assert path.exists()
    assert "Found" in path.read_text(encoding="utf-8")


def test_run_freshness_check_dry_run_writes_nothing(tmp_path):
    with patch("agentteams.research.search.web_search", return_value=[]):
        path = run_freshness_check(tmp_path, dry_run=True)
    assert not path.exists()


def test_never_writes_to_registry_or_battery_module():
    """Static guard: freshness.py must not import anything that lets it mutate the probe
    corpus or the registry — 'measures and reports, never remediates' applies here too."""
    import agentteams.redteam.freshness as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "constitutional_redteam_battery" not in src
    assert "registry.RESULTS" not in src
    assert "record(" not in src
