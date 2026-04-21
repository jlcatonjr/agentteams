from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import security_refs


class _FakeResponse:
    """Minimal urllib response mock that satisfies `_fetch_json`'s interface."""

    def __init__(self, payload: dict, *, url: str = "https://www.cisa.gov/fake"):
        self._body = json.dumps(payload).encode("utf-8")
        self._url = url

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_build_security_placeholders_online(monkeypatch, tmp_path: Path) -> None:
    kev_payload = {
        "catalogVersion": "2026.04.16",
        "count": 2,
        "vulnerabilities": [
            {
                "cveID": "CVE-2026-0001",
                "vendorProject": "VendorA",
                "product": "ProductA",
                "vulnerabilityName": "Remote Code Execution",
                "dateAdded": "2026-04-15",
                "requiredAction": "Apply vendor patch.",
                "knownRansomwareCampaignUse": "Known",
            },
            {
                "cveID": "CVE-2026-0002",
                "vendorProject": "VendorB",
                "product": "ProductB",
                "vulnerabilityName": "Privilege Escalation",
                "dateAdded": "2026-04-14",
                "requiredAction": "Apply mitigations.",
                "knownRansomwareCampaignUse": "Unknown",
            },
        ],
    }
    epss_payload = {
        "status": "OK",
        "data": [
            {"cve": "CVE-2026-0001", "epss": "0.9", "percentile": "0.99"},
            {"cve": "CVE-2026-0002", "epss": "0.5", "percentile": "0.80"},
        ],
    }

    def _fake_urlopen(req, timeout=12):
        url = req.full_url
        if "known_exploited_vulnerabilities.json" in url:
            return _FakeResponse(kev_payload, url=url)
        if "api.first.org/data/v1/epss" in url:
            return _FakeResponse(epss_payload, url=url)
        raise OSError(f"unexpected URL: {url}")

    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _fake_urlopen)

    output_dir = tmp_path / ".github" / "agents"
    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=False,
        max_items=2,
        skip_nvd=True,
    )

    assert "CVE-2026-0001" in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]
    assert "CISA KEV" in placeholders["SECURITY_SOURCE_REGISTRY"]
    payload = json.loads(placeholders["SECURITY_VULNERABILITY_WATCH_JSON"])
    assert len(payload["vulnerabilities"]) == 2
    # LLM threats always present
    assert "LLM01:2025" in placeholders["SECURITY_LLM_THREATS_SUMMARY"]
    assert "Prompt Injection" in placeholders["SECURITY_LLM_THREATS_SUMMARY"]
    assert "owasp.org" in placeholders["SECURITY_LLM_THREATS_SUMMARY"]
    assert "OWASP LLM Top 10" in placeholders["SECURITY_SOURCE_REGISTRY"]
    # LLM threats in JSON payload
    assert len(payload["llm_threats"]) == 10
    assert payload["llm_threats"][0]["id"] == "LLM01:2025"


def test_build_security_placeholders_nvd_enrichment(monkeypatch, tmp_path: Path) -> None:
    kev_payload = {
        "catalogVersion": "2026.04.16",
        "count": 1,
        "vulnerabilities": [
            {
                "cveID": "CVE-2026-9999",
                "vendorProject": "TestVendor",
                "product": "TestProduct",
                "vulnerabilityName": "Test RCE",
                "dateAdded": "2026-04-15",
                "requiredAction": "Patch it.",
                "knownRansomwareCampaignUse": "Unknown",
            }
        ],
    }
    epss_payload = {"status": "OK", "data": []}
    nvd_payload = {
        "vulnerabilities": [
            {
                "cve": {
                    "id": "CVE-2026-9999",
                    "metrics": {
                        "cvssMetricV31": [
                            {
                                "cvssData": {
                                    "baseScore": 9.8,
                                    "baseSeverity": "CRITICAL",
                                    "version": "3.1",
                                }
                            }
                        ]
                    },
                }
            }
        ]
    }

    def _fake_urlopen(req, timeout=12):
        url = req.full_url
        if "known_exploited_vulnerabilities.json" in url:
            return _FakeResponse(kev_payload, url=url)
        if "api.first.org/data/v1/epss" in url:
            return _FakeResponse(epss_payload, url=url)
        if "services.nvd.nist.gov" in url:
            return _FakeResponse(nvd_payload, url=url)
        raise OSError(f"unexpected URL: {url}")

    # Patch time.sleep so NVD rate-limit sleep doesn't slow tests
    monkeypatch.setattr(security_refs.time, "sleep", lambda _: None)
    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _fake_urlopen)

    output_dir = tmp_path / ".github" / "agents"
    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=False,
        max_items=1,
        skip_nvd=False,
    )

    assert "CVSS 9.8 CRITICAL" in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]
    payload = json.loads(placeholders["SECURITY_VULNERABILITY_WATCH_JSON"])
    vuln = payload["vulnerabilities"][0]
    assert vuln["cvss_score"] == "9.8"
    assert vuln["cvss_severity"] == "CRITICAL"


def test_build_security_placeholders_osv_packages(monkeypatch, tmp_path: Path) -> None:
    kev_payload = {"catalogVersion": "2026.04.16", "count": 0, "vulnerabilities": []}
    epss_payload = {"status": "OK", "data": []}
    osv_payload = {
        "results": [
            {"vulns": [{"id": "GHSA-xxxx-yyyy-zzzz"}, {"id": "GHSA-aaaa-bbbb-cccc"}]},
            {"vulns": []},
        ]
    }

    def _fake_urlopen(req, timeout=12):
        url = req.full_url if hasattr(req, "full_url") else req
        if "known_exploited_vulnerabilities.json" in url:
            return _FakeResponse(kev_payload, url=url)
        if "api.first.org/data/v1/epss" in url:
            return _FakeResponse(epss_payload, url=url)
        if "api.osv.dev" in url:
            return _FakeResponse(osv_payload, url=url)
        raise OSError(f"unexpected URL: {url}")

    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _fake_urlopen)

    output_dir = tmp_path / ".github" / "agents"
    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=False,
        max_items=0,
        skip_nvd=True,
        tools=["requests", "flask"],
    )

    assert "requests" in placeholders["SECURITY_OSV_PACKAGES_SUMMARY"]
    assert "GHSA-xxxx-yyyy-zzzz" in placeholders["SECURITY_OSV_PACKAGES_SUMMARY"]
    # flask has no vulns — should not appear in summary
    assert "flask" not in placeholders["SECURITY_OSV_PACKAGES_SUMMARY"]
    payload = json.loads(placeholders["SECURITY_VULNERABILITY_WATCH_JSON"])
    assert len(payload["osv_packages"]) == 1
    assert payload["osv_packages"][0]["package"] == "requests"


def test_build_security_placeholders_offline_from_cache(tmp_path: Path) -> None:
    output_dir = tmp_path / ".github" / "agents"
    ref_dir = output_dir / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)

    cache_payload = {
        "generated_at": "2026-04-16T00:00:00Z",
        "sources": [
            {"name": "CISA KEV", "url": "https://example.test", "status": "cached", "count": 1}
        ],
        "vulnerabilities": [
            {
                "cveID": "CVE-2026-1111",
                "vendorProject": "CacheVendor",
                "product": "CacheProduct",
                "vulnerabilityName": "Cached Vulnerability",
                "dateAdded": "2026-04-12",
                "requiredAction": "Patch now.",
                "knownRansomwareCampaignUse": "Unknown",
            }
        ],
    }
    (ref_dir / "security-vulnerability-watch.json").write_text(
        json.dumps(cache_payload),
        encoding="utf-8",
    )

    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=True,
        max_items=5,
    )

    assert "CVE-2026-1111" in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]
    assert "cached" in placeholders["SECURITY_SOURCE_REGISTRY"]


# ---------------------------------------------------------------------------
# Supply chain integrity tests (Component 4)
# ---------------------------------------------------------------------------


def test_check_response_domain_allows_valid_domains() -> None:
    """_check_response_domain must not raise for URLs in the allowlist."""
    for url in [
        "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
        "https://api.first.org/data/v1/epss?cve=CVE-2026-0001",
        "https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=CVE-2026-0001",
        "https://api.osv.dev/v1/querybatch",
        "https://cveawg.mitre.org/api/cve/CVE-2026-0001",
    ]:
        security_refs._check_response_domain(url)  # must not raise


def test_check_response_domain_rejects_unexpected_domain() -> None:
    """_check_response_domain must raise OSError for domains not in allowlist."""
    with pytest.raises(OSError, match="allowlist"):
        security_refs._check_response_domain("https://evil.example.com/kev.json")


def test_check_response_domain_rejects_subdomain_spoofing() -> None:
    """A domain like cisa.gov.evil.com must be rejected despite containing 'cisa.gov'."""
    with pytest.raises(OSError, match="allowlist"):
        security_refs._check_response_domain("https://cisa.gov.evil.com/kev.json")


def test_fetch_json_rejects_empty_response(monkeypatch) -> None:
    """_fetch_json must raise OSError when response body is below minimum size."""

    class _TinyResponse:
        def read(self):
            return b"{}"  # 2 bytes — below _MIN_RESPONSE_BYTES of 10

        def geturl(self):
            return "https://www.cisa.gov/fake"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        security_refs.urllib.request,
        "urlopen",
        lambda req, timeout=12: _TinyResponse(),
    )
    with pytest.raises(OSError, match="suspiciously small"):
        security_refs._fetch_json("https://www.cisa.gov/fake")


def test_fetch_json_rejects_disallowed_domain(monkeypatch) -> None:
    """_fetch_json must raise OSError when effective URL is from a disallowed domain."""
    payload = json.dumps({"vulnerabilities": []}).encode("utf-8")

    class _RedirectResponse:
        def read(self):
            return payload

        def geturl(self):
            return "https://attacker.example.com/redirected"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        security_refs.urllib.request,
        "urlopen",
        lambda req, timeout=12: _RedirectResponse(),
    )
    with pytest.raises(OSError, match="allowlist"):
        security_refs._fetch_json("https://www.cisa.gov/fake")


def test_stale_cache_warning_applied_on_kev_failure(monkeypatch, tmp_path: Path) -> None:
    """When live KEV fetch fails and cache is used, threat summary must include stale warning."""
    output_dir = tmp_path / ".github" / "agents"
    ref_dir = output_dir / "references"
    ref_dir.mkdir(parents=True, exist_ok=True)

    cache_payload = {
        "generated_at": "2025-01-01T00:00:00Z",
        "sources": [],
        "vulnerabilities": [
            {
                "cveID": "CVE-2025-0001",
                "vendorProject": "OldVendor",
                "product": "OldProduct",
                "vulnerabilityName": "Old RCE",
                "dateAdded": "2025-01-01",
                "requiredAction": "Patch.",
                "knownRansomwareCampaignUse": "Unknown",
            }
        ],
        "osv_packages": [],
    }
    (ref_dir / "security-vulnerability-watch.json").write_text(
        json.dumps(cache_payload), encoding="utf-8"
    )

    def _always_fail(req, timeout=12):
        raise OSError("network unavailable")

    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _always_fail)

    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=False,
        max_items=5,
        skip_nvd=True,
    )

    assert "STALE DATA" in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]
    assert "STALE DATA" in placeholders["SECURITY_PREVENTION_PLAYBOOK"]
    assert "CVE-2025-0001" in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]


def test_stale_cache_warning_absent_on_live_fetch(monkeypatch, tmp_path: Path) -> None:
    """When live fetch succeeds, the stale warning must NOT appear in output."""
    kev_payload = {
        "catalogVersion": "2026.04.21",
        "count": 1,
        "vulnerabilities": [
            {
                "cveID": "CVE-2026-9000",
                "vendorProject": "LiveVendor",
                "product": "LiveProduct",
                "vulnerabilityName": "Live RCE",
                "dateAdded": "2026-04-21",
                "requiredAction": "Patch immediately.",
                "knownRansomwareCampaignUse": "Unknown",
            }
        ],
    }
    epss_payload = {"status": "OK", "data": []}

    def _fake_urlopen(req, timeout=12):
        url = req.full_url
        if "known_exploited_vulnerabilities.json" in url:
            return _FakeResponse(kev_payload, url=url)
        if "api.first.org" in url:
            return _FakeResponse(epss_payload, url=url)
        raise OSError(f"unexpected URL: {url}")

    monkeypatch.setattr(security_refs.urllib.request, "urlopen", _fake_urlopen)

    output_dir = tmp_path / ".github" / "agents"
    placeholders = security_refs.build_security_placeholders(
        output_dir=output_dir,
        offline=False,
        max_items=1,
        skip_nvd=True,
    )

    assert "STALE DATA" not in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]
    assert "CVE-2026-9000" in placeholders["SECURITY_CURRENT_THREATS_SUMMARY"]

