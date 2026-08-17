"""Regression tests for agentteams/redteam/coverage.py (improvement item I1).

See tmp/by-week/2026-W34/redteam-osint-ost-implementation.plan.md.
"""
from __future__ import annotations

from agentteams.redteam.coverage import TAXONOMY_PATH, compute_coverage, load_taxonomy


def test_taxonomy_file_loads_and_has_both_lists():
    entries = load_taxonomy()
    ids = {tid for tid, _ in entries}
    assert "AML.TA0002" in ids  # MITRE ATLAS Reconnaissance
    assert "LLM01" in ids  # OWASP LLM Top 10 Prompt Injection
    assert len(entries) == 16 + 10


def test_compute_coverage_splits_covered_and_uncovered():
    tagged = [("LLM01",), (), ("LLM06", "AML.TA0002")]
    report = compute_coverage(tagged, taxonomy_path=TAXONOMY_PATH)
    covered_ids = {tid for tid, _ in report.covered}
    assert {"LLM01", "LLM06", "AML.TA0002"} <= covered_ids
    assert "LLM02" not in covered_ids
    assert len(report.covered) + len(report.uncovered) == 26


def test_compute_coverage_empty_refs_covers_nothing():
    report = compute_coverage([(), (), ()], taxonomy_path=TAXONOMY_PATH)
    assert report.covered == []
    assert len(report.uncovered) == 26


def test_render_lists_uncovered_entries():
    report = compute_coverage([("LLM01",)], taxonomy_path=TAXONOMY_PATH)
    text = report.render()
    assert "LLM02" in text  # an uncovered entry should be named
    assert "1/26" in text


def test_battery_backfill_uses_only_defensible_tags_present_in_taxonomy():
    """Every external_refs value the battery actually sets must resolve to a real
    taxonomy id -- catches a typo'd or invented id at test time, not at review time."""
    import importlib

    battery = importlib.import_module("tests.constitutional_redteam_battery")
    from agentteams.redteam.registry import RESULTS

    RESULTS.clear()
    for probe_fn in battery.PROBES:
        probe_fn()
    taxonomy_ids = {tid for tid, _ in load_taxonomy()}
    tagged_probes = [p for p in RESULTS if p.external_refs]
    assert tagged_probes, "expected at least one backfilled probe (B9, C0-C3)"
    for probe in tagged_probes:
        for ref in probe.external_refs:
            assert ref in taxonomy_ids, f"{probe.pid} tags unknown id {ref!r}"
