# `security_refs` — AgentTeamsModule

Build live security intelligence placeholders for rendered agent files.

Fetches curated vulnerability feeds from CISA KEV, FIRST EPSS, MITRE CVE, NVD, and OSV.dev to prepare:
- Human-readable markdown snippets for agent/reference templates
- Machine-readable JSON snapshots for downstream automation
- Control-to-evidence matrices for security-posture artifacts

> *Source: `agentteams/security_refs.py`*

---

## Constants

### `ROUTING_SCHEMA_VERSION`

> *Source: `agentteams/security_refs.py`*

Current schema version for the security placeholders artifact. Used to detect compatibility between build and consumer versions.

**Type:** `str`

---

## Functions

### `build_security_placeholders(packages, *, cache_dir=None, offline=False)`

> *Source: `agentteams/security_refs.py`*

Build security intelligence placeholders for rendering into agent/reference templates.

**Args:**

- `packages` (`list[dict[str, Any]]`) — List of package dicts, each with `name` (required), `version` (optional), `ecosystem` (optional, default: `PyPI`).
- `cache_dir` (`Path | None`, keyword-only) — Directory for threat-intel caching. If `None`, uses `~/.cache/agentteams/security-refs/`. Default: `None`.
- `offline` (`bool`, keyword-only) — If `True`, skip live network fetches and use cached data only. Default: `False`.

**Returns:** `dict[str, Any]` — Security placeholders dict with keys:
- `artifact_type`: `"security-placeholders"`
- `schema_version`: Current schema version
- `built_at`: ISO-8601 timestamp
- `packages_supplied`: Package count passed
- `llm_threats`: Markdown-formatted LLM threat taxonomy (OWASP LLM Top 10)
- `cve_summary`: Formatted CVE/exploit summary with CVSS/EPSS scores
- `osv_findings`: Package vulnerability findings from OSV.dev
- `source_registry`: Authoritative reference URLs for threat feeds
- `control_evidence`: Security control-to-test mappings

**Behavior Notes:**

- Network timeouts default to 12 seconds per request.
- Supply-chain integrity controls validate all responses against an explicit hostname allowlist.
- Response size bounds are enforced per-host to detect truncation or anomalies.
- If live fetches fail, stale cached data is used with a `⚠️ STALE DATA` warning prepended.
- Empty package list returns a valid dict with only OWASP LLM threat content.

**Raises:**

- `OSError` — If cache directory cannot be created or network integrity checks fail.
- `ValueError` — If response body exceeds host-specific size bounds.

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

To build placeholders without network access:

```python
placeholders = build_security_placeholders(
    packages=[...],
    cache_dir=Path(".cache"),
    offline=True  # Use cached threat-intel only
)
```

### Cache Location

Default: `~/.cache/agentteams/security-refs/`

Structure:
```
~/.cache/agentteams/security-refs/
├── kev.json           # CISA Known Exploited Vulnerabilities
├── epss.json          # FIRST EPSS scores
├── mitre-cves.json    # Fetched MITRE CVE records
├── osv-batch.json     # Package vulnerabilities from OSV
└── threat-intel.json  # Latest combined snapshot
```

---

## Schema Note

The returned dict conforms to the security placeholders schema (not yet released as a separate `.schema.json`, but follows semantic versioning via the `schema_version` field).

**Important:** Check `schema_version` before consuming; incompatible versions may have different field names or structures.
