"""Regression tests: Rule S-10 (Dependency Vetting Before Install) exists inside the security
template's Invariant Core fence, its verdict row and cross-references are wired up, and the
prevention playbook carries the matching baseline lines.

See tmp/by-week/2026-W33/npm-release-cooldown-security-default.plan.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import agentteams
from agentteams import security_refs
from agentteams.security_feed_render import _format_prevention_playbook
from agentteams.security_refs import _STATIC_SOURCE_ENTRIES, _merge_static_sources

_TEMPLATE = (
    Path(agentteams.__file__).parent
    / "templates"
    / "universal"
    / "security.template.md"
)


def _template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _fence() -> tuple[str, int, int]:
    text = _template_text()
    begin = text.index("<!-- AGENTTEAMS:BEGIN security_rules_invariant")
    end = text.index("<!-- AGENTTEAMS:END security_rules_invariant")
    return text, begin, end


def test_rule_s10_exists_inside_the_invariant_fence():
    text, begin, end = _fence()
    rule_pos = text.index("**Rule S-10: Dependency Vetting Before Install**")
    assert begin < rule_pos < end


def test_rule_s10_names_the_vetting_sources_and_cooldown():
    text = _template_text()
    rule_start = text.index("**Rule S-10: Dependency Vetting Before Install**")
    next_section = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    body = text[rule_start:next_section]
    for marker in (
        "https://security.snyk.io/",
        "https://docs.npmjs.com/auditing-package-dependencies-for-security-vulnerabilities",
        "OSV.dev",
        "`npm audit`",
        "default 14 days",
        "CVE/GHSA/OSV",
    ):
        assert marker in body, f"Rule S-10 is missing {marker!r}"


def test_rule_s10_cooldown_never_delays_remediation():
    """The cooldown governs adopting new releases, not patching known-vulnerable ones —
    without this clause it reads as contradicting the KEV patch-urgency guidance."""
    text = _template_text()
    rule_start = text.index("**Rule S-10: Dependency Vetting Before Install**")
    next_section = text.index("### HALT vs. CONDITIONAL PASS Escalation Criteria")
    assert "never delays remediating" in text[rule_start:next_section]


def test_verdict_table_has_a_rule_s10_row():
    text, begin, end = _fence()
    row_pos = text.index("younger than the Rule S-10 cooldown")
    assert begin < row_pos < end
    row_line = text[text.rfind("\n", 0, row_pos): text.index("\n", row_pos)]
    assert "CONDITIONAL PASS" in row_line


def test_rule_s10_is_cross_referenced_not_restated():
    text = _template_text()
    s9_start = text.index("**Rule S-9: Pathway Safety Verification**")
    s10_start = text.index("**Rule S-10: Dependency Vetting Before Install**")
    assert "Rule S-10" in text[s9_start:s10_start], "S-9 checklist must cross-reference S-10"
    slop = text.index("**Supply-chain / slopsquatting**")
    assert "Rule S-10" in text[slop: text.index("\n", slop)], (
        "the slopsquatting bullet must cross-reference S-10"
    )


def test_playbook_carries_dependency_vetting_baseline_lines():
    playbook = _format_prevention_playbook([])
    assert "Before any package/module install" in playbook
    assert "package-release cooldown (default 14 days)" in playbook
    assert "never delays remediating" in playbook


def test_static_source_entries_include_snyk_and_npm_audit():
    names = {entry["name"] for entry in _STATIC_SOURCE_ENTRIES}
    assert "Snyk Vulnerability DB" in names
    assert "npm audit docs" in names


def test_stale_cache_fallback_restores_static_sources(monkeypatch, tmp_path: Path) -> None:
    """The stale-cache path (online fetch fails, cache exists) replaces the whole sources
    list with the cached one. A cache written by a pre-S-10 module version carries the old
    8-entry list — the merge must restore the Snyk/npm entries without disturbing the
    positional slots the fetch code updates by index (sources[0]/[2]/[3]/[4])."""
    output_dir = tmp_path / ".github" / "agents"
    ref_dir = output_dir / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)

    pre_s10_sources = [
        {"name": "CISA KEV", "url": "https://example.test/kev", "status": "ok"},
        {"name": "MITRE CVE", "url": "https://example.test/cve", "status": "metadata_only"},
        {"name": "FIRST EPSS", "url": "https://example.test/epss", "status": "ok"},
        {"name": "NVD (NIST)", "url": "https://example.test/nvd", "status": "ok"},
        {"name": "OSV.dev", "url": "https://example.test/osv", "status": "ok"},
        {"name": "OWASP LLM Top 10", "url": "https://example.test/owasp", "status": "static"},
        {"name": "MITRE ATLAS", "url": "https://example.test/atlas", "status": "static"},
        {"name": "MITRE CWE", "url": "https://example.test/cwe", "status": "static"},
    ]
    cache_payload = {
        "generated_at": "2026-08-01T00:00:00Z",
        "sources": pre_s10_sources,
        "vulnerabilities": [
            {
                "cve": "CVE-2026-2222",
                "vendor": "StaleVendor",
                "product": "StaleProduct",
                "name": "Stale Vulnerability",
                "date_added": "2026-07-30",
                "required_action": "Patch it.",
                "known_ransomware_campaign_use": "Unknown",
            }
        ],
    }
    (ref_dir / "security-vulnerability-watch.json").write_text(
        json.dumps(cache_payload), encoding="utf-8",
    )

    def _fail_urlopen(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _fail_urlopen)

    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=False,
        max_items=5,
    )

    registry = placeholders["SECURITY_SOURCE_REGISTRY"]
    assert "CVE-2026-2222" in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]
    assert "Snyk Vulnerability DB" in registry
    assert "npm audit docs" in registry
    # Slot integrity: the cached positional entries are intact and not overwritten.
    assert "CISA KEV" in registry
    assert "OSV.dev" in registry


def test_merge_static_sources_restores_missing_entries_without_duplicating():
    restored = _merge_static_sources(
        [{"name": "CISA KEV", "url": "https://example.test", "status": "cached"}]
    )
    names = [s["name"] for s in restored]
    assert names.count("Snyk Vulnerability DB") == 1
    assert names.count("npm audit docs") == 1
    # Idempotent: a second merge adds nothing.
    assert len(_merge_static_sources(restored)) == len(restored)
