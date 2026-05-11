# Security Hardening & Threat Intelligence

Comprehensive vulnerability management integrated into the agent team generation pipeline.

---

## Overview

AgentTeams incorporates live threat intelligence (CVE, CISA KEV, EPSS) into every generation run. This guide explains:

1. **What threat data is collected**
2. **How fail-closed gates protect against stale intelligence**
3. **Waiver system for offline/air-gapped environments**
4. **24-hour auto-refresh and override controls**
5. **Agent-level vulnerability handling**

---

## Threat Intelligence Sources

### Primary Data Feeds

| Source | Data | Update Cadence | Use Case |
|--------|------|---|---|
| **CISA KEV** | Known Exploited Vulnerabilities | Real-time hourly | High-priority exploit risk |
| **NVD** | National Vulnerability Database (CVSS 3.1) | Daily official, real-time via third-party services | Comprehensive CVE coverage, severity scoring |
| **EPSS** | Exploit Prediction Scoring System (0-1 probability) | Daily | Predictive exploit likelihood |

### Generated Security Reference Files

When `@security` agent is included, the pipeline generates:

- **`security-vulnerability-watch.reference.md`** — Live snapshot of current high-EPSS/KEV vulnerabilities at generation time
- **`security.agent.md`** — Hardened instruction set for the `@security` agent including:
  - KEV prioritization rules
  - EPSS-based triage thresholds
  - Compensating-control guidance
  - Cross-repo validation procedures

---

## Fail-Closed Gating

### Freshness Enforcement

Every write-capable operation (render, emit, convert, interop) enforces a **mandatory freshness check**:

```
Is security intelligence ≤24 hours old?
  └─ YES → Proceed
  └─ NO  → BLOCK write unless:
           - Valid signed waiver exists (HMAC-SHA256 verified) AND
           - Waiver not expired (expires_at > current_time) AND
           - AGENTTEAMS_WAIVER_SIGNING_KEY is configured
```

**Complete Waiver Validation (ALL checks required):**
1. Waiver record exists in `references/security-waivers.log.csv`
2. HMAC-SHA256 signature is valid: `HMAC-SHA256(row_without_signature) == waiver.hmac_signature`
3. **Expiration is checked:** `current_time() <= waiver.expires_at`
4. Reason matches operation type (e.g., "air-gapped" for `--security-offline`)

If **ANY** check fails → Block write, return detailed error message

The 24-hour window is automated:
- First generation run fetches live data and records timestamp
- All subsequent operations within 24h use cached snapshot
- After 24h: automated refresh triggered before any write

### Why Fail-Closed?

Agent files are executable instructions. If threat intelligence is stale:
- Security misclassifications in instructions become embodied in deployed agents
- Patching delays compound across all generated teams
- Regeneration happens frequently (updates, team creation), so stale data carries high risk

→ **Fail-closed design ensures agents always have current vulnerability context.**

---

## Waiver System

### When Waivers Apply

Valid scenarios for signed waivers:

1. **Air-gapped environments** — No network access; CI runs offline with approved snapshot
2. **Network outages** — Temporary unavailability of threat feed services
3. **Rate limiting** — Transient service unavailability (fallback to cache)
4. **Policy exception** — Explicit business decision to accept stale data for bounded time

### Waiver Lifecycle

**Prerequisites:**
- `AGENTTEAMS_WAIVER_SIGNING_KEY` environment variable configured (HMAC-SHA256 key)
- Signed record in `references/security-waivers.log.csv`

**Format:**
```csv
issued_at,expires_at,reason,approver,hmac_signature
2026-05-10T15:30:00Z,2026-05-11T15:30:00Z,scheduled-maintenance,security-lead@org,abc123...
```

**Signature verification:**
- HMAC-SHA256(row_without_signature, AGENTTEAMS_WAIVER_SIGNING_KEY)
- On every write, agentteams verifies signature and expiration
- If verification fails or waiver expired: block write

### Creating and Verifying Waivers

**Waiver Format & Fields:**
```csv
issued_at,expires_at,reason,approver,hmac_signature
2026-05-10T15:30:00Z,2026-05-11T15:30:00Z,scheduled-maintenance,security-lead@org,abc123def456...
```

**Automated Waiver Creation & Signature:**
```bash
# Set your signing key
export AGENTTEAMS_WAIVER_SIGNING_KEY="your-hmac-key"

# Create new waiver (auto-signs with HMAC-SHA256)
agentteams --create-waiver \
  --reason "scheduled-maintenance" \
  --approver "security-lead@org" \
  --expires-in 24h \
  >> references/security-waivers.log.csv

# Verify all existing waivers (checks expiration + signatures)
agentteams --verify-waivers
# Output: ✅ 3 waivers valid (1 expiring in 23h)
```

**Manual Verification (if no automation available):**
```bash
# Compute HMAC-SHA256 for row (without signature field)
echo -n "2026-05-10T15:30:00Z,2026-05-11T15:30:00Z,scheduled-maintenance,security-lead@org" | \
  openssl dgst -sha256 -hmac "$AGENTTEAMS_WAIVER_SIGNING_KEY"
# Output: abc123def456... (must match waiver.hmac_signature)
```

---

## CLI Flags for Security Control

### Fetch Control

**`--security-offline`**
- Use cached snapshot only; no network fetch
- Useful in CI without internet or for reproducibility
- Blocked if cache is stale and no valid waiver exists

**`--security-no-nvd`**
- Skip NVD CVSS enrichment (saves ~7 seconds per CVE)
- CISA KEV and EPSS data still fetched
- Reduces data volume in air-gapped scenarios

### Data Volume Control

**`--security-max-items N`** (default: 15)
- Include only top N vulnerabilities by EPSS/KEV priority
- Keeps generated files compact
- Example: `--security-max-items 5` for minimal security reference

### Offline Mode

**`--security-offline --security-max-items 10`**
- Combined: minimal data volume + no network
- Useful for reproducible builds in CI with locked threat snapshot

---

## Agent-Level Vulnerability Handling

### `@security` Agent

The security agent receives live vulnerability context in its instructions:

**Input:** `security.agent.md` (generated from live threat feed)
- Current KEV list with exploit indicators
- High-EPSS vulnerabilities needing immediate attention
- Compensating-control guidance for delayed patches

**Responsibilities:**
1. Clear destructive operations (high-risk writes, external repos)
2. Assess credential exposure in generated files
3. Validate freshness of threat intelligence before closure decisions
4. Enforce cross-repo coordination security rules

### `@code-hygiene` Agent

Performs static security checks:
- Unresolved `{MANUAL:*}` placeholders (may contain credentials)
- Absolute filesystem paths containing usernames (PII risk)
- API keys or tokens embedded in templates

---

## 24-Hour Auto-Refresh

### Mechanism

| Event | Behavior |
|-------|----------|
| First generation in 24h window | Fetch live data, cache locally, record timestamp |
| Subsequent operations within 24h | Use cached data |
| Operation after 24h elapsed | Auto-fetch live data before proceeding |
| Fetch fails with valid waiver | Allow operation with `WAIVER` status logged |
| Fetch fails without waiver | **BLOCK** operation, return error |

### Controlling Refresh

**Force refresh (ignore cache):**
```bash
agentteams --description brief.json --update
# Fetches live data even if cache is recent
```

**Force offline (use cache, don't fetch):**
```bash
agentteams --description brief.json --security-offline
# Uses existing cache; blocks if stale without waiver
```

**Check freshness without writing:**
```bash
agentteams --description brief.json --check
# Read-only: validates freshness, reports status, exits without changes
```

---

## Security Scan

**`--scan-security`** flag performs post-generation security checks on all agent files:

1. **PII detection** — Absolute paths containing usernames
2. **Credential patterns** — API keys, tokens, passwords (regex)
3. **Unresolved placeholders** — `{MANUAL:*}` and `{UPPER_SNAKE_CASE}` left unfilled
4. **Artifact validation** — JSON schemas for output files

Exit code 1 if issues found; use `--auto-correct` to attempt repairs (requires `--post-audit`).

---

## Governance Integration

### Constitutional Rules

*(These are Constitutional Rules #1 and #11 from the Orchestrator; see the orchestrator template for the complete set of 12 immutable rules.)*

**Rule #1 — Security Before Destructive Operations**
- File deletions, bulk edits (≥3 files), external repo writes all require `@security` clearance before proceeding

**Rule #11 — Cross-Repository Writes**
- Any modification outside `src/` requires `@repo-liaison` assessment + `@security` clearance

### Security Decisions Log

`references/security-decisions.log.csv` records all security gate clearances:

```csv
operation_id,timestamp,operation_type,decision,conditions,conditions_verified,notes
op-2026-05-10-001,2026-05-10T14:30:00Z,cross-repo-write,CONDITIONAL_PASS,"waiver-expires-2026-05-11, approval-from-security-lead",pending,"Cross-repo: collector-management, scope: 5 files, approver: @security"
```

**Workflow:**
1. Dangerous operation requested (cross-repo write, destructive mutation clearance)
2. `@security` routes through threat intelligence, approval chain
3. Decision logged with conditions
4. Conditions verified before execution
5. Decision audit-able via `conflict-auditor`

### Constitutional Rules

**Rule #1 — Security Before Destructive Operations**
- File deletions, bulk edits (≥3 files), external repo writes all require `@security` clearance

**Rule #11 — Cross-Repository Writes**
- Any modification outside `src/` requires `@repo-liaison` assessment + `@security` clearance

---

## Troubleshooting

### "Security gate blocked: stale or unavailable"

**Cause:** Intelligence is >24h old and no valid waiver exists

**Solutions:**
1. **Retry with network:** `agentteams --description brief.json --update` (forces fresh fetch)
2. **Use offline mode:** `agentteams --description brief.json --security-offline` (uses cache; only if cache exists)
3. **Create waiver:** Add signed record to `references/security-waivers.log.csv` (see Waiver System above)

### "Unresolved placeholder: {MANUAL:...}"

**Cause:** Security reference file generated with unfilled manual placeholder

**Solution:** Run `--scan-security` to identify, then fill in `SETUP-REQUIRED.md` or project description

### "EPSS data unavailable"

**Cause:** Transient service issue or rate limiting

**Solution:**
1. Retry in 5 minutes (third-party services may recover)
2. Use `--security-no-nvd` to skip NVD and proceed with CISA KEV + EPSS only
3. Use `--security-offline` if cache is recent enough

---

## Best Practices

1. **Pin threat snapshots in CI** — Use `--check` in read-only CI jobs to verify freshness without writing
2. **Rotate signing keys** — Change `AGENTTEAMS_WAIVER_SIGNING_KEY` quarterly
3. **Audit waivers** — Review `references/security-waivers.log.csv` during security reviews
4. **Monitor EPSS trends** — Generated `security.agent.md` shows exploit likelihood; escalate high values
5. **Document exceptions** — Use `notes` column in `security-decisions.log.csv` to explain policy overrides

---

## References

- **CLI Flags:** `docs_src/cli-reference.md` → Security Intelligence Options
- **Security Agent Template:** `agentteams/templates/universal/security.template.md`
- **Threat Intelligence Module:** `agentteams/security_refs.py`
- **Audit & Scan Modules:** `agentteams/audit.py`, `agentteams/scan.py`
