# `security_refs` — AgentTeamsModule

Build live security intelligence placeholders for rendered agent files.

Fetches curated vulnerability feeds from CISA KEV, FIRST EPSS, MITRE CVE, NVD, and OSV.dev to prepare:
- Human-readable markdown snippets for agent/reference templates
- Machine-readable JSON snapshots for downstream automation
- Control-to-evidence matrices for security-posture artifacts

> *Source: `agentteams/security_refs.py`*

---

## Functions

### `build_security_placeholders(*, output_dir, offline=False, max_items=15, tools=None, skip_nvd=False, osv_ecosystem="PyPI")`

> *Source: `agentteams/security_refs.py`*

Build security intelligence placeholders for rendering into agent/reference templates. All parameters are keyword-only.

**Args:**

- `output_dir` (`Path`, keyword-only, required) — Resolved agents output directory. The threat-intel cache snapshot is read from / written under `output_dir/references/security-vulnerability-watch.json`.
- `offline` (`bool`, keyword-only) — If `True`, skip live network fetches and use the cached snapshot only. Default: `False`.
- `max_items` (`int`, keyword-only) — Maximum number of vulnerabilities to include in the summary. Default: `15`.
- `tools` (`list[str] | None`, keyword-only) — Optional list of project tool/package names for OSV.dev lookup. If `None`, the OSV source is skipped. Default: `None`.
- `skip_nvd` (`bool`, keyword-only) — If `True`, skip NVD CVSS enrichment (saves ~35 s for 5 CVEs). Default: `False`.
- `osv_ecosystem` (`str`, keyword-only) — Package ecosystem for OSV.dev queries. Default: `"PyPI"`.

**Returns:** `dict[str, str]` — Mapping of placeholder key → rendered string value, with keys:
- `SECURITY_DATA_GENERATED_AT`: ISO-8601 timestamp the data was generated.
- `SECURITY_SOURCE_REGISTRY`: Rendered registry of source feeds and their fetch status.
- `SECURITY_CURRENT_THREATS_SUMMARY`: Formatted KEV/CVE threat summary with EPSS and (optional) CVSS scores. Prefixed with a stale-data warning when cached data was used.
- `SECURITY_PREVENTION_PLAYBOOK`: Markdown prevention/remediation playbook. Prefixed with a stale-data warning when cached data was used.
- `SECURITY_LLM_THREATS_SUMMARY`: Markdown LLM threat taxonomy (OWASP LLM Top 10).
- `SECURITY_OSV_PACKAGES_SUMMARY`: Rendered OSV.dev package-vulnerability findings.
- `SECURITY_CONTROL_EVIDENCE_SUMMARY`: Rendered security control-to-evidence matrix.
- `SECURITY_DATA_FRESHNESS_STATUS`: `"fresh"`, `"stale"`, or `"unknown"`.
- `SECURITY_DATA_AGE_HOURS`: Age of the data in hours (string, 2 decimals), or empty if unknown.
- `SECURITY_DATA_TTL_HOURS`: TTL in hours (currently `"24"`).
- `SECURITY_VULNERABILITY_WATCH_JSON`: The full machine-readable snapshot as a JSON string (also persisted to the cache file under `output_dir/references/`).

**Behavior Notes:**

- Network timeouts default to 12 seconds per request.
- Supply-chain integrity controls validate all responses against an explicit exact-match hostname allowlist.
- Response size bounds are enforced per-host to detect truncation or anomalies.
- If live fetches fail and a cached snapshot exists, the stale snapshot is used and a `⚠️ STALE DATA` warning is prepended to the threat-summary and prevention-playbook placeholders; freshness status becomes `"stale"`.
- When `tools` is `None`/empty, OSV lookup is skipped; the result still contains the OWASP LLM threat content and control-evidence matrix.

**Raises:**

- `OSError` — Raised for all supply-chain size-bound violations (response smaller than the per-host minimum, larger than the per-host maximum, or no bounds configured for the response host).

---

### Data Sources

| Source | Purpose | Coverage |
|--------|---------|----------|
| CISA KEV | Actively exploited CVEs | Global; high-confidence confirmed exploits |
| FIRST EPSS | Exploit probability | 0–1 score; empirically calibrated |
| MITRE CVE | CVE metadata | Canonical CVE records with descriptions |
| NVD | CVSS base scores | Vector detail (optional; 5-CVE limit due to rate limits) |
| OSV.dev | Package-level vulnerabilities | Language-agnostic; supports PyPI, npm, Cargo, etc. |
| OWASP LLM Top 10 | LLM-specific threats | Static taxonomy (2025 edition); no network call |

---

### Network Integrity

All network requests validate:

1. **Response domain** — Exact-match host allowlist (no suffix matching to prevent subdomain compromise)
2. **Response size** — Per-host bounds to detect truncation
3. **Stale data handling** — Failed fetches gracefully degrade to cached data with warning

---

## Cache Management

### Offline Mode

To build placeholders without network access (uses the cached snapshot only):

```python
placeholders = build_security_placeholders(
    output_dir=Path(".github/agents"),
    offline=True,  # Use cached threat-intel only
)
```

### Cache Location

The snapshot is a single JSON file inside the output tree, not a user-home cache directory:

```
<output_dir>/references/security-vulnerability-watch.json
```

This file is both the source consumed in `offline=True` mode and the snapshot serialized into the `SECURITY_VULNERABILITY_WATCH_JSON` placeholder. It holds `generated_at`, `sources`, `vulnerabilities`, `osv_packages`, `control_evidence`, a `freshness` block (`status`, `age_hours`, `ttl_hours`, `used_stale_cache`, `offline`), the OWASP LLM threat list, LLM security references, and a `methodology` block.

---

## Schema Note

The returned dict is a flat `dict[str, str]` mapping the fixed `SECURITY_*` placeholder keys (listed under **Returns** above) to rendered string values; it is not a versioned schema object and carries no `schema_version` field. The structured snapshot is instead embedded as a JSON string under `SECURITY_VULNERABILITY_WATCH_JSON` (and persisted to `<output_dir>/references/security-vulnerability-watch.json`).

**Important:** Templates consume these placeholders by key substitution. The keys are stable; the rendered values reflect the latest fetched (or cached) threat intelligence.
