"""security_refs.py — Build live security intelligence placeholders for rendering.

Fetches a curated set of reliable vulnerability feeds and prepares:
- Human-readable markdown snippets for agent/reference templates
- A machine-readable JSON snapshot for downstream automation

Sources:
- CISA KEV  — actively exploited vulnerability catalog
- FIRST EPSS — exploit probability scoring
- MITRE CVE  — canonical CVE records
- NVD        — CVSS base scores (optional, rate-limited; up to 5 CVEs)
- OSV.dev    — package-level vulnerability data (optional; requires package list)
- OWASP LLM Top 10 — static LLM-specific threat taxonomy (no network call)
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_EPSS_URL = "https://api.first.org/data/v1/epss"
_MITRE_CVE_ROOT = "https://cveawg.mitre.org/api/cve/"
_NVD_CVE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
_OSV_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"

# ---------------------------------------------------------------------------
# Supply chain integrity controls
# Explicit host allowlist — checked against the effective URL host after redirects.
# This is intentionally exact-match only (no suffix matching) to avoid trusting
# unrelated or compromised subdomains.
# ---------------------------------------------------------------------------
_ALLOWED_RESPONSE_HOSTS: frozenset[str] = frozenset([
    "www.cisa.gov",
    "api.first.org",
    "cveawg.mitre.org",
    "services.nvd.nist.gov",
    "api.osv.dev",
])

#: Per-host response size bounds (bytes).  Responses outside these bounds are
#: likely truncated, empty, or unexpectedly large for the expected source.
_HOST_RESPONSE_SIZE_BOUNDS: dict[str, tuple[int, int]] = {
    "www.cisa.gov": (64, 2 * 1024 * 1024),
    "api.first.org": (32, 2 * 1024 * 1024),
    "cveawg.mitre.org": (32, 2 * 1024 * 1024),
    "services.nvd.nist.gov": (32, 5 * 1024 * 1024),
    "api.osv.dev": (32, 5 * 1024 * 1024),
}

#: Marker prepended to threat summaries when stale cached data is used.
_STALE_DATA_WARNING: str = (
    "> ⚠️ **STALE DATA** — live fetch failed; content below is from a previous run "
    "and may not reflect current threat status. Treat as indicative only.\n\n"
)

# ---------------------------------------------------------------------------
# OWASP LLM Top 10 – 2025 edition  (static; updated in source on major releases)
# Reference: https://owasp.org/www-project-top-10-for-large-language-model-applications/
# ---------------------------------------------------------------------------
_OWASP_LLM_TOP10: list[dict[str, str]] = [
    {
        "id": "LLM01:2025",
        "name": "Prompt Injection",
        "summary": (
            "Attacker-controlled input overrides or hijacks LLM instructions, "
            "causing unintended actions including data exfiltration and privilege escalation."
        ),
    },
    {
        "id": "LLM02:2025",
        "name": "Sensitive Information Disclosure",
        "summary": (
            "LLM inadvertently reveals PII, credentials, or proprietary data from training "
            "or context when prompted directly or through side-channel extraction."
        ),
    },
    {
        "id": "LLM03:2025",
        "name": "Supply Chain Vulnerabilities",
        "summary": (
            "Compromised models, datasets, plugins, or integrations introduce malicious "
            "behaviour that bypasses standard code review and testing pipelines."
        ),
    },
    {
        "id": "LLM04:2025",
        "name": "Data and Model Poisoning",
        "summary": (
            "Adversarial manipulation of training or fine-tuning data degrades model "
            "integrity, introduces backdoors, or embeds biased responses."
        ),
    },
    {
        "id": "LLM05:2025",
        "name": "Improper Output Handling",
        "summary": (
            "LLM-generated content passed unsanitised to downstream systems causes "
            "XSS, SSRF, code injection, or command execution."
        ),
    },
    {
        "id": "LLM06:2025",
        "name": "Excessive Agency",
        "summary": (
            "An LLM agent operates with overly broad permissions or autonomy, amplifying "
            "the blast radius of prompt injection or logic errors to destructive real-world actions."
        ),
    },
    {
        "id": "LLM07:2025",
        "name": "System Prompt Leakage",
        "summary": (
            "The system prompt (including confidential instructions and secrets) is "
            "extracted through adversarial queries, revealing business logic or credentials."
        ),
    },
    {
        "id": "LLM08:2025",
        "name": "Vector and Embedding Weaknesses",
        "summary": (
            "Poisoned embeddings or RAG data stores cause the model to retrieve and "
            "act on attacker-controlled content, enabling indirect prompt injection at scale."
        ),
    },
    {
        "id": "LLM09:2025",
        "name": "Misinformation",
        "summary": (
            "Hallucinated or factually incorrect LLM outputs are acted upon without "
            "verification, leading to flawed decisions, compliance violations, or reputational harm."
        ),
    },
    {
        "id": "LLM10:2025",
        "name": "Unbounded Consumption",
        "summary": (
            "Uncontrolled LLM inference requests exhaust computational resources, "
            "enabling denial-of-service or cost-exhaustion attacks."
        ),
    },
]

#: Authoritative LLM/AI security reference URLs
_LLM_SECURITY_REFERENCES: list[dict[str, str]] = [
    {
        "name": "OWASP LLM Top 10 (2025)",
        "url": "https://owasp.org/www-project-top-10-for-large-language-model-applications/",
        "description": "Canonical taxonomy of the ten most critical LLM application security risks.",
    },
    {
        "name": "MITRE ATLAS",
        "url": "https://atlas.mitre.org/",
        "description": "Adversarial Threat Landscape for AI Systems — ML-specific attack techniques and mitigations.",
    },
    {
        "name": "Claude Security (Anthropic)",
        "url": "https://code.claude.com/docs/en/security",
        "description": "Anthropic-published security controls and guidance for Claude deployments.",
    },
    {
        "name": "NIST AI Risk Management Framework",
        "url": "https://airc.nist.gov/",
        "description": "NIST AI RMF — governance framework for trustworthy and responsible AI systems.",
    },
    {
        "name": "ENISA Multilayer Framework for Good Cybersecurity Practices for AI",
        "url": "https://www.enisa.europa.eu/publications/multilayer-framework-for-good-cybersecurity-practices-for-ai",
        "description": "EU guidance on securing AI systems across design, development, and deployment.",
    },
]


def _utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 with trailing Z."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_response_host(effective_url: str) -> str:
    """Return canonical lowercased host from URL, excluding any explicit port.

    Args:
        effective_url: The URL actually used (after redirects).

    Returns:
        Canonicalized host suitable for exact-match allowlist checks.

    Raises:
        OSError: URL cannot be parsed into a valid host.
    """
    parsed = urllib.parse.urlparse(effective_url)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise OSError(f"Cannot parse domain from URL: {effective_url!r}")
    try:
        # Normalize IDNA hostnames to ASCII for stable matching.
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OSError(f"Cannot normalize domain from URL: {effective_url!r}") from exc


def _check_response_domain(effective_url: str) -> None:
    """Raise OSError if the effective URL's host is not in the explicit host allowlist.

    Args:
        effective_url: The URL actually used (after redirects).

    Raises:
        OSError: Domain is not in the allowlist.
    """
    host = _canonical_response_host(effective_url)
    if host not in _ALLOWED_RESPONSE_HOSTS:
        raise OSError(
            f"Supply chain integrity: response domain {host!r} is not in the allowlist. "
            "Rejecting response."
        )


def _check_response_size(url: str, host: str, body: bytes) -> None:
    """Raise OSError if a response body is outside expected bounds for host.

    Args:
        url: Source URL requested.
        host: Canonical effective host after redirects.
        body: Raw response body bytes.

    Raises:
        OSError: Response body is smaller/larger than configured bounds.
    """
    if host not in _HOST_RESPONSE_SIZE_BOUNDS:
        raise OSError(
            f"Supply chain integrity: no response size bounds configured for host {host!r}. "
            "Rejecting response."
        )
    min_bytes, max_bytes = _HOST_RESPONSE_SIZE_BOUNDS[host]
    size = len(body)
    if size < min_bytes:
        raise OSError(
            f"Supply chain integrity: response from {url!r} is suspiciously small "
            f"({size} bytes < {min_bytes} minimum for {host})."
        )
    if size > max_bytes:
        raise OSError(
            f"Supply chain integrity: response from {url!r} exceeds size limit "
            f"({size} bytes > {max_bytes} maximum for {host})."
        )


def _response_size_bounds(host: str) -> tuple[int, int]:
    """Return configured (min_bytes, max_bytes) bounds for a host.

    Args:
        host: Canonical effective host after redirects.

    Returns:
        Tuple of (min_bytes, max_bytes).

    Raises:
        OSError: No bounds are configured for host.
    """
    bounds = _HOST_RESPONSE_SIZE_BOUNDS.get(host)
    if bounds is None:
        raise OSError(
            f"Supply chain integrity: no response size bounds configured for host {host!r}. "
            "Rejecting response."
        )
    return bounds


def _fetch_json_request(req: urllib.request.Request, timeout: int = 12) -> dict:
    """Fetch and decode JSON from a prepared request with integrity checks.

    Args:
        req: Prepared urllib request (GET or POST).
        timeout: Request timeout in seconds.

    Returns:
        Decoded JSON object.

    Raises:
        OSError: Network, JSON decoding, domain allowlist, or size bound failures.
    """
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        effective_url = resp.geturl()
        scheme = urllib.parse.urlparse(effective_url).scheme.lower()
        if scheme != "https":
            raise OSError(
                "Supply chain integrity: effective URL must use HTTPS. "
                f"Got scheme {scheme!r} for {effective_url!r}."
            )
        _check_response_domain(effective_url)
        host = _canonical_response_host(effective_url)
        _, max_bytes = _response_size_bounds(host)
        # Read with an upper bound (+1) to detect oversized payloads before
        # unbounded allocation in memory.
        body = resp.read(max_bytes + 1)

    _check_response_size(req.full_url, host, body)
    return json.loads(body.decode("utf-8", errors="replace"))


def _fetch_json(url: str, timeout: int = 12) -> dict:
    """Fetch and decode JSON from a URL with domain and size integrity checks.

    Args:
        url: HTTPS endpoint returning JSON.
        timeout: Request timeout in seconds.

    Returns:
        Decoded JSON object.

    Raises:
        OSError: Network, JSON decoding, domain allowlist, or size bound failures.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "agentteams-security-refs/1.0"})
    return _fetch_json_request(req, timeout=timeout)


def _fetch_kev(max_items: int) -> tuple[list[dict], dict]:
    """Fetch CISA KEV entries and return recent vulnerabilities + source metadata."""
    kev = _fetch_json(_KEV_URL)
    vulns = kev.get("vulnerabilities", [])
    sorted_vulns = sorted(vulns, key=lambda v: v.get("dateAdded", ""), reverse=True)
    selected = sorted_vulns[:max_items]
    source_meta = {
        "name": "CISA KEV",
        "url": _KEV_URL,
        "status": "ok",
        "catalogVersion": kev.get("catalogVersion", ""),
        "count": kev.get("count", 0),
    }
    return selected, source_meta


def _fetch_epss(cves: list[str]) -> tuple[dict[str, dict[str, str]], dict]:
    """Fetch EPSS data for the provided CVEs.

    Args:
        cves: List of CVE IDs.

    Returns:
        Tuple of (cve -> epss payload, source metadata)
    """
    if not cves:
        return {}, {
            "name": "FIRST EPSS",
            "url": _EPSS_URL,
            "status": "skipped",
            "count": 0,
        }

    cve_csv = ",".join(cves)
    q = urllib.parse.urlencode({"cve": cve_csv})
    url = f"{_EPSS_URL}?{q}"
    payload = _fetch_json(url)
    rows = payload.get("data", [])
    mapped = {row.get("cve", ""): row for row in rows if row.get("cve")}
    source_meta = {
        "name": "FIRST EPSS",
        "url": _EPSS_URL,
        "status": "ok",
        "count": len(mapped),
    }
    return mapped, source_meta


def _fetch_nvd_cvss(cves: list[str], max_cves: int = 5) -> tuple[dict[str, dict], dict]:
    """Fetch NVD CVSS v3/v4 scores for the provided CVE IDs.

    NVD enforces a strict rate limit (1 req/6 s without an API key).
    We cap requests at *max_cves* and sleep 7 s between calls to stay safe.

    Args:
        cves: CVE IDs to look up (most-critical first).
        max_cves: Maximum number of NVD requests to make (default 5).

    Returns:
        Tuple of (cve -> {cvss_score, cvss_severity, cvss_version}, source metadata)
    """
    result: dict[str, dict] = {}
    for cve_id in cves[:max_cves]:
        try:
            q = urllib.parse.urlencode({"cveId": cve_id})
            url = f"{_NVD_CVE_URL}?{q}"
            payload = _fetch_json(url, timeout=15)
            items = payload.get("vulnerabilities", [])
            if items:
                cve_data = items[0].get("cve", {})
                metrics = cve_data.get("metrics", {})
                # Prefer CVSSv3.1 > v3.0 > v4.0 > v2
                score, severity, version = None, None, None
                for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2"):
                    entries = metrics.get(key, [])
                    if entries:
                        cv = entries[0].get("cvssData", {})
                        score = cv.get("baseScore")
                        severity = cv.get("baseSeverity") or cv.get("baseScore")
                        version = cv.get("version", key[-3:])
                        break
                if score is not None:
                    result[cve_id] = {
                        "cvss_score": str(score),
                        "cvss_severity": str(severity) if severity else "",
                        "cvss_version": str(version) if version else "",
                    }
        except OSError:
            pass
        # Respect NVD rate limit between requests
        time.sleep(7)

    source_meta = {
        "name": "NVD (NIST)",
        "url": _NVD_CVE_URL,
        "status": "ok" if result else "no_data",
        "count": len(result),
    }
    return result, source_meta


def _fetch_osv_packages(packages: list[str], ecosystem: str = "PyPI") -> tuple[list[dict], dict]:
    """Batch-query OSV.dev for package-level vulnerabilities.

    Args:
        packages: Package names to query (e.g. ["flask", "requests"]).
        ecosystem: Package ecosystem. Defaults to "PyPI".

    Returns:
        Tuple of (list of {package, vulns_found, top_ids}, source metadata)
    """
    if not packages:
        return [], {
            "name": "OSV.dev",
            "url": _OSV_QUERYBATCH_URL,
            "status": "skipped",
            "count": 0,
        }

    queries = [{"package": {"name": p, "ecosystem": ecosystem}} for p in packages]
    body = json.dumps({"queries": queries}).encode("utf-8")
    req = urllib.request.Request(
        _OSV_QUERYBATCH_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "agentteams-security-refs/1.0",
        },
        method="POST",
    )
    payload = _fetch_json_request(req, timeout=15)

    results = payload.get("results", [])
    findings: list[dict] = []
    for pkg, result in zip(packages, results):
        vulns = result.get("vulns", [])
        if vulns:
            top_ids = [v.get("id", "") for v in vulns[:3]]
            findings.append({
                "package": pkg,
                "ecosystem": ecosystem,
                "vuln_count": len(vulns),
                "top_ids": top_ids,
            })

    source_meta = {
        "name": "OSV.dev",
        "url": _OSV_QUERYBATCH_URL,
        "status": "ok",
        "count": len(findings),
    }
    return findings, source_meta


def _format_llm_threats(include_references: bool = True) -> str:
    """Return a markdown-formatted OWASP LLM Top 10 threat list with reference links."""
    lines: list[str] = []
    lines.append("**OWASP LLM Top 10 (2025)** — risks applicable to any AI-integrated system:")
    lines.append("")
    for item in _OWASP_LLM_TOP10:
        lines.append(f"- **{item['id']} — {item['name']}**: {item['summary']}")
    if include_references:
        lines.append("")
        lines.append("**Authoritative AI/LLM Security References:**")
        lines.append("")
        for ref in _LLM_SECURITY_REFERENCES:
            lines.append(f"- [{ref['name']}]({ref['url']}): {ref['description']}")
    return "\n".join(lines)


def _format_osv_summary(findings: list[dict]) -> str:
    """Return a markdown-formatted OSV package vulnerability summary."""
    if not findings:
        return "- No package-level vulnerabilities found in OSV.dev for the declared project dependencies."
    lines: list[str] = []
    for f in findings:
        ids = ", ".join(f["top_ids"]) if f["top_ids"] else "n/a"
        plural = "y" if f["vuln_count"] == 1 else "ies"
        lines.append(
            f"- **{f['package']}** ({f['ecosystem']}): "
            f"{f['vuln_count']} known vulnerabilit{plural} — top IDs: {ids}"
        )
    return "\n".join(lines)


def _format_source_registry(sources: list[dict]) -> str:
    lines: list[str] = []
    for src in sources:
        status = src.get("status", "unknown")
        detail = []
        if src.get("catalogVersion"):
            detail.append(f"catalog {src['catalogVersion']}")
        if src.get("count") is not None:
            detail.append(f"items {src['count']}")
        suffix = f" ({', '.join(detail)})" if detail else ""
        lines.append(f"- {src.get('name', 'Source')}: {status}{suffix} — {src.get('url', '')}")
    if not lines:
        return "- No security sources available in this run."
    return "\n".join(lines)


def _format_threat_summary(
    vulns: list[dict],
    epss: dict[str, dict[str, str]],
    nvd: dict[str, dict] | None = None,
) -> str:
    if not vulns:
        return "- No live vulnerability data was available; consult cached reference file."

    nvd = nvd or {}
    lines: list[str] = []
    for vuln in vulns:
        cve = vuln.get("cveID", "UNKNOWN-CVE")
        e = epss.get(cve, {})
        epss_score = e.get("epss")
        pct = e.get("percentile")
        epss_text = ""
        if epss_score:
            pct_text = f", percentile {pct}" if pct else ""
            epss_text = f" | EPSS {epss_score}{pct_text}"
        nvd_entry = nvd.get(cve, {})
        cvss_text = ""
        if nvd_entry.get("cvss_score"):
            cvss_text = (
                f" | CVSS {nvd_entry['cvss_score']}"
                + (f" {nvd_entry['cvss_severity']}" if nvd_entry.get("cvss_severity") else "")
            )
        lines.append(
            f"- `{cve}` | {vuln.get('vendorProject', 'Unknown vendor')} {vuln.get('product', '')} | "
            f"{vuln.get('vulnerabilityName', 'Known exploited vulnerability')} | "
            f"added {vuln.get('dateAdded', 'n/a')}{epss_text}{cvss_text}"
        )
    return "\n".join(lines)


def _format_prevention_playbook(vulns: list[dict]) -> str:
    actions: list[str] = []
    for vuln in vulns:
        action = (vuln.get("requiredAction") or "").strip()
        if action and action not in actions:
            actions.append(action)
        if len(actions) >= 4:
            break

    base = [
        "- Prioritize remediation for KEV-listed CVEs as actively exploited threats.",
        "- Triage by exploitability (EPSS) and internet exposure before lower-risk backlog items.",
        "- Enforce patch windows with owner, SLA, and verification evidence for each critical CVE.",
        "- When patching is blocked, define compensating controls (WAF rules, ACL tightening, feature disablement).",
        "- Add detections for exploitation attempts and verify telemetry coverage for affected assets.",
    ]
    if actions:
        base.append("- Vendor/CISA required actions:")
        base.extend([f"  - {a}" for a in actions])
    return "\n".join(base)


def build_security_placeholders(
    *,
    output_dir: Path,
    offline: bool = False,
    max_items: int = 15,
    tools: list[str] | None = None,
    skip_nvd: bool = False,
    osv_ecosystem: str = "PyPI",
) -> dict[str, str]:
    """Build placeholders used by security templates.

    Args:
        output_dir: Resolved agents output directory.
        offline: If True, use cached snapshot only.
        max_items: Max vulnerabilities to include in summary.
        tools: Optional list of project tool/package names for OSV lookup.
        skip_nvd: If True, skip NVD CVSS enrichment (saves ~35 s for 5 CVEs).
        osv_ecosystem: Package ecosystem for OSV.dev queries (default "PyPI").

    Returns:
        Mapping of placeholder key -> rendered string values.
    """
    generated_at = _utc_now_iso()
    cache_json = output_dir / "references" / "security-vulnerability-watch.json"

    sources: list[dict] = [
        {"name": "CISA KEV", "url": _KEV_URL, "status": "not_fetched"},
        {"name": "MITRE CVE", "url": _MITRE_CVE_ROOT, "status": "metadata_only"},
        {"name": "FIRST EPSS", "url": _EPSS_URL, "status": "not_fetched"},
        {"name": "NVD (NIST)", "url": _NVD_CVE_URL, "status": "skipped"},
        {"name": "OSV.dev", "url": _OSV_QUERYBATCH_URL, "status": "not_fetched"},
        {"name": "OWASP LLM Top 10", "url": "https://owasp.org/www-project-top-10-for-large-language-model-applications/", "status": "static"},
        {"name": "MITRE ATLAS", "url": "https://atlas.mitre.org/", "status": "static"},
    ]
    vulnerabilities: list[dict] = []
    epss_map: dict[str, dict[str, str]] = {}
    nvd_map: dict[str, dict] = {}
    osv_findings: list[dict] = []
    _used_stale_cache: bool = False

    if offline:
        if cache_json.exists():
            try:
                cached = json.loads(cache_json.read_text(encoding="utf-8"))
                vulnerabilities = cached.get("vulnerabilities", [])[:max_items]
                sources = cached.get("sources", sources)
                generated_at = cached.get("generated_at", generated_at)
                osv_findings = cached.get("osv_packages", [])
            except (json.JSONDecodeError, OSError):
                pass
    else:
        try:
            vulnerabilities, kev_source = _fetch_kev(max_items=max_items)
            sources[0] = kev_source
            cves = [v.get("cveID", "") for v in vulnerabilities if v.get("cveID")]
            try:
                epss_map, epss_source = _fetch_epss(cves)
                sources[2] = epss_source
            except OSError:
                sources[2]["status"] = "unavailable"
            if not skip_nvd and cves:
                try:
                    nvd_map, nvd_source = _fetch_nvd_cvss(cves)
                    sources[3] = nvd_source
                except OSError:
                    sources[3]["status"] = "unavailable"
        except OSError:
            sources[0]["status"] = "unavailable"
            if cache_json.exists():
                try:
                    cached = json.loads(cache_json.read_text(encoding="utf-8"))
                    vulnerabilities = cached.get("vulnerabilities", [])[:max_items]
                    generated_at = cached.get("generated_at", generated_at)
                    osv_findings = cached.get("osv_packages", [])
                    _used_stale_cache = True
                    # Preserve previous source status context when available
                    cached_sources = cached.get("sources", [])
                    if cached_sources:
                        sources = cached_sources
                except (json.JSONDecodeError, OSError):
                    pass

        # OSV package lookup (independent of KEV; only when tool list provided)
        if tools:
            try:
                osv_findings, osv_source = _fetch_osv_packages(tools, ecosystem=osv_ecosystem)
                sources[4] = osv_source
            except OSError:
                sources[4]["status"] = "unavailable"
        else:
            sources[4]["status"] = "skipped"

    source_registry = _format_source_registry(sources)
    threat_summary = _format_threat_summary(vulnerabilities, epss_map, nvd_map)
    prevention = _format_prevention_playbook(vulnerabilities)
    llm_threats = _format_llm_threats(include_references=True)
    osv_summary = _format_osv_summary(osv_findings)

    if _used_stale_cache:
        threat_summary = _STALE_DATA_WARNING + threat_summary
        prevention = _STALE_DATA_WARNING + prevention

    # Build per-vuln entries for JSON payload including CVSS when available
    vuln_records: list[dict[str, Any]] = []
    for v in vulnerabilities:
        cve_id = v.get("cveID", "")
        record: dict[str, Any] = {
            "cve": cve_id,
            "vendor": v.get("vendorProject", ""),
            "product": v.get("product", ""),
            "name": v.get("vulnerabilityName", ""),
            "date_added": v.get("dateAdded", ""),
            "known_ransomware_campaign_use": v.get("knownRansomwareCampaignUse", ""),
            "required_action": v.get("requiredAction", ""),
            "epss": epss_map.get(cve_id, {}).get("epss", ""),
            "epss_percentile": epss_map.get(cve_id, {}).get("percentile", ""),
        }
        if cve_id in nvd_map:
            record["cvss_score"] = nvd_map[cve_id].get("cvss_score", "")
            record["cvss_severity"] = nvd_map[cve_id].get("cvss_severity", "")
            record["cvss_version"] = nvd_map[cve_id].get("cvss_version", "")
        vuln_records.append(record)

    json_payload: dict[str, Any] = {
        "generated_at": generated_at,
        "sources": sources,
        "vulnerabilities": vuln_records,
        "osv_packages": osv_findings,
        "llm_threats": [
            {"id": t["id"], "name": t["name"], "summary": t["summary"]}
            for t in _OWASP_LLM_TOP10
        ],
        "llm_security_references": _LLM_SECURITY_REFERENCES,
        "methodology": {
            "prioritization": [
                "CISA KEV membership",
                "EPSS exploit probability",
                "NVD CVSS base score",
                "asset exposure and business criticality",
            ],
            "refresh_process": "Generated during team initialization and update runs.",
        },
    }

    return {
        "SECURITY_DATA_GENERATED_AT": generated_at,
        "SECURITY_SOURCE_REGISTRY": source_registry,
        "SECURITY_CURRENT_THREATS_SUMMARY": threat_summary,
        "SECURITY_PREVENTION_PLAYBOOK": prevention,
        "SECURITY_LLM_THREATS_SUMMARY": llm_threats,
        "SECURITY_OSV_PACKAGES_SUMMARY": osv_summary,
        "SECURITY_VULNERABILITY_WATCH_JSON": json.dumps(json_payload, indent=2),
    }
