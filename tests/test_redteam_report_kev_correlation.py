"""Regression tests for the I3 KEV/OSV advisory annotation in report.py/cycle.py.

See tmp/by-week/2026-W34/redteam-osint-ost-implementation.plan.md. Confirms:
(a) the skeleton stays a skeleton — annotation is advisory text only, never a decision;
(b) no new network call is added — cycle.load_kev_terms only ever reads a local file;
(c) absence of a cache degrades to no annotation, never an error.
"""
from __future__ import annotations

import json

from agentteams.redteam.kev_correlation import KEV_CACHE_REL, load_kev_terms
from agentteams.redteam.report import _kev_correlation_note, render_remediation_skeleton
from agentteams.redteam.runner import Findings


def _empty_findings(exploited=()) -> Findings:
    return Findings(
        schema_version=1,
        generated_at="2026-08-17T00:00:00Z",
        probe_module=None,
        exploited=list(exploited),
    )


class _FakeSelfAudit:
    findings = []
    advisory = []


def test_no_annotation_without_cache_terms():
    assert _kev_correlation_note("Some Package failed a check", None) == ""
    assert _kev_correlation_note("Some Package failed a check", frozenset()) == ""


def test_annotation_appears_on_a_real_match():
    note = _kev_correlation_note(
        "widgetlib parsing bug found", frozenset({"widgetlib"})
    )
    assert "widgetlib" in note
    assert "advisory" in note
    assert "not a claim of relevance" in note  # honesty ceiling, mirrors research.verify


def test_no_annotation_on_a_short_term_to_avoid_false_positive_noise():
    # A 2-3 char term would match almost any sentence -- must be filtered out.
    assert _kev_correlation_note("a normal finding about a widget", frozenset({"a"})) == ""


def test_no_annotation_when_no_term_actually_appears():
    assert _kev_correlation_note("totally unrelated text", frozenset({"widgetlib"})) == ""


def test_render_remediation_skeleton_stays_a_skeleton_with_or_without_terms():
    """The verifier/rehearsal-target columns must stay blank either way -- I3 must never
    turn the skeleton into a pre-decided plan."""
    findings = _empty_findings(exploited=["A5"])
    without = render_remediation_skeleton(findings, _FakeSelfAudit())
    with_terms = render_remediation_skeleton(findings, _FakeSelfAudit(), frozenset({"widgetlib"}))
    for text in (without, with_terms):
        assert "| A5 returned EXPLOITED" in text or "A5 returned EXPLOITED" in text
        # verifier/rehearsal-target columns are the two trailing empty cells
        assert text.count("| | |") == 1 or text.rstrip().endswith("| |")


def test_load_kev_terms_returns_empty_set_when_cache_absent(tmp_path):
    assert load_kev_terms(tmp_path) == frozenset()


def test_load_kev_terms_returns_empty_set_on_malformed_json(tmp_path):
    path = tmp_path / KEV_CACHE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert load_kev_terms(tmp_path) == frozenset()


def test_load_kev_terms_extracts_vendor_product_and_package_names(tmp_path):
    path = tmp_path / KEV_CACHE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "vulnerabilities": [{"vendor": "ExampleVendor", "product": "ExampleProduct"}],
        "osv_packages": [{"package": "widgetlib"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    terms = load_kev_terms(tmp_path)
    assert terms == frozenset({"ExampleVendor", "ExampleProduct", "widgetlib"})


def test_load_kev_terms_never_makes_a_network_call(tmp_path, monkeypatch):
    """Static guard: the loader must not import anything network-capable."""
    import agentteams.redteam.kev_correlation as mod

    src = open(mod.__file__, encoding="utf-8").read()
    load_fn_src = src[src.index("def load_kev_terms") :]
    assert "urlopen" not in load_fn_src
    assert "httpx" not in load_fn_src
    assert "requests" not in load_fn_src


def test_load_kev_terms_lives_outside_the_no_except_execution_path():
    """kev_correlation.py must NOT be one of the redteam engine's no-except execution-path
    modules -- its try/except degrade-gracefully behavior would trip
    test_redteam_audit_workflow.py::test_the_execution_path_contains_no_except_clauses if it
    did (this was caught once already, moving the function out of cycle.py)."""
    from tests.test_redteam_audit_workflow import _EXECUTION_PATH_MODULES

    assert "kev_correlation.py" not in _EXECUTION_PATH_MODULES
