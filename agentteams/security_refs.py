"""security_refs.py — Build live security intelligence placeholders for rendering.

Fetches a curated set of reliable vulnerability feeds and prepares:
- Human-readable markdown snippets for agent/reference templates
- A machine-readable JSON snapshot for downstream automation
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

_KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_EPSS_URL = "https://api.first.org/data/v1/epss"
_MITRE_CVE_ROOT = "https://cveawg.mitre.org/api/cve/"


def _utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 with trailing Z."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fetch_json(url: str, timeout: int = 12) -> dict:
    """Fetch and decode JSON from a URL.

    Args:
        url: HTTPS endpoint returning JSON.
        timeout: Request timeout in seconds.

    Returns:
        Decoded JSON object.

    Raises:
        OSError: Network or JSON decoding failures.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "agentteams-security-refs/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


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


def _format_threat_summary(vulns: list[dict], epss: dict[str, dict[str, str]]) -> str:
    if not vulns:
        return "- No live vulnerability data was available; consult cached reference file."

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
        lines.append(
            f"- `{cve}` | {vuln.get('vendorProject', 'Unknown vendor')} {vuln.get('product', '')} | "
            f"{vuln.get('vulnerabilityName', 'Known exploited vulnerability')} | "
            f"added {vuln.get('dateAdded', 'n/a')}{epss_text}"
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
) -> dict[str, str]:
    """Build placeholders used by security templates.

    Args:
        output_dir: Resolved agents output directory.
        offline: If True, use cached snapshot only.
        max_items: Max vulnerabilities to include in summary.

    Returns:
        Mapping of placeholder key -> rendered string values.
    """
    generated_at = _utc_now_iso()
    cache_json = output_dir / "references" / "security-vulnerability-watch.json"

    sources: list[dict] = [
        {"name": "CISA KEV", "url": _KEV_URL, "status": "not_fetched"},
        {"name": "MITRE CVE", "url": _MITRE_CVE_ROOT, "status": "metadata_only"},
        {"name": "FIRST EPSS", "url": _EPSS_URL, "status": "not_fetched"},
    ]
    vulnerabilities: list[dict] = []
    epss_map: dict[str, dict[str, str]] = {}

    if offline:
        if cache_json.exists():
            try:
                cached = json.loads(cache_json.read_text(encoding="utf-8"))
                vulnerabilities = cached.get("vulnerabilities", [])[:max_items]
                sources = cached.get("sources", sources)
                generated_at = cached.get("generated_at", generated_at)
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
        except OSError:
            sources[0]["status"] = "unavailable"
            if cache_json.exists():
                try:
                    cached = json.loads(cache_json.read_text(encoding="utf-8"))
                    vulnerabilities = cached.get("vulnerabilities", [])[:max_items]
                    generated_at = cached.get("generated_at", generated_at)
                    # Preserve previous source status context when available
                    cached_sources = cached.get("sources", [])
                    if cached_sources:
                        sources = cached_sources
                except (json.JSONDecodeError, OSError):
                    pass

    source_registry = _format_source_registry(sources)
    threat_summary = _format_threat_summary(vulnerabilities, epss_map)
    prevention = _format_prevention_playbook(vulnerabilities)

    json_payload = {
        "generated_at": generated_at,
        "sources": sources,
        "vulnerabilities": [
            {
                "cve": v.get("cveID", ""),
                "vendor": v.get("vendorProject", ""),
                "product": v.get("product", ""),
                "name": v.get("vulnerabilityName", ""),
                "date_added": v.get("dateAdded", ""),
                "known_ransomware_campaign_use": v.get("knownRansomwareCampaignUse", ""),
                "required_action": v.get("requiredAction", ""),
                "epss": epss_map.get(v.get("cveID", ""), {}).get("epss", ""),
                "epss_percentile": epss_map.get(v.get("cveID", ""), {}).get("percentile", ""),
            }
            for v in vulnerabilities
        ],
        "methodology": {
            "prioritization": [
                "CISA KEV membership",
                "EPSS exploit probability",
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
        "SECURITY_VULNERABILITY_WATCH_JSON": json.dumps(json_payload, indent=2),
    }
