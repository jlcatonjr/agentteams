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

**Complete waiver validation (ALL checks required).** These rules are enforced by
`agentteams/cli/security_gate.py` (`_validate_security_waiver`), which is the
authoritative source for the schema and signature documented below:
1. A waiver record exists in `references/security-waivers.log.csv` with the full
   11-column header (see [Waiver System](#waiver-system)).
2. `action_reviewed` matches the operation — the intel-freshness gate looks for
   `security-intel-freshness`.
3. `conditions_verified` is exactly `verified`.
4. `approver`, `ticket_id`, and `reason_code` are all non-empty.
5. `expires_at` is a valid ISO-8601 timestamp in the future.
6. `max_uses > 0` and `uses < max_uses` (the gate increments `uses` on each spend).
7. `AGENTTEAMS_WAIVER_SIGNING_KEY` is configured and the HMAC-SHA256 signature is
   valid (payload below); the stored signature is compared case-insensitively.

If **ANY** check fails → block the write and return a detailed error.

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
- `AGENTTEAMS_WAIVER_SIGNING_KEY` environment variable configured (the HMAC-SHA256 key).
- A signed record in `references/security-waivers.log.csv`.

**Schema (authoritative source: `agentteams/cli/security_gate.py`).** The log is a CSV
with **11 columns** — do not hand-copy a subset; the gate rejects any other header:

```csv
timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,ticket_id,reason_code,conditions_verified,signature
2026-05-10T15:30:00Z,waiver-freshness-001,security-intel-freshness,2026-05-11T15:30:00Z,1,0,security-lead@org,SEC-1238,maintenance,verified,<hex-signature>
```

- `action_reviewed` — the gated action; the intel-freshness gate looks for `security-intel-freshness`.
- `max_uses` / `uses` — the gate consumes one use per spend (requires `uses < max_uses`).
- `conditions_verified` — must be exactly `verified`.
- `signature` — lowercase hex HMAC-SHA256 over the payload below.

**HMAC payload.** The signature signs **9 pipe-joined fields**, excluding `timestamp`
and `signature`, in this exact order:

```
waiver_id|action_reviewed|expires_at|max_uses|uses|approver|ticket_id|reason_code|conditions_verified
```

### Creating and verifying waivers

> **No `--create-waiver` command exists.** Minting a waiver mints the security gate's
> escape credential, so it is intentionally a manual, key-holder-owned step (a
> maintainer-owned `--create-waiver` may be added later). The snippet below is
> **reproducible by a careful key-holder, but it is a brittle stopgap** — keep the
> signing key offline and prefer refreshing live intel where possible.

**Mint a signed waiver (Python — mirrors `security_gate.py` exactly):**
```python
import hashlib, hmac, os

key = os.environ["AGENTTEAMS_WAIVER_SIGNING_KEY"].encode("utf-8")
row = {
    "timestamp": "2026-05-10T15:30:00Z",
    "waiver_id": "waiver-freshness-001",
    "action_reviewed": "security-intel-freshness",
    "expires_at": "2026-05-11T15:30:00Z",
    "max_uses": "1",
    "uses": "0",                       # newly minted waivers start unspent
    "approver": "security-lead@org",
    "ticket_id": "SEC-1238",
    "reason_code": "maintenance",
    "conditions_verified": "verified",
}
fields = ["waiver_id", "action_reviewed", "expires_at", "max_uses", "uses",
          "approver", "ticket_id", "reason_code", "conditions_verified"]
payload = "|".join(row[f] for f in fields)               # 9 fields, pipe-joined
row["signature"] = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()

header = ["timestamp", *fields, "signature"]             # 11-column order
print(",".join(header))
print(",".join(row[c] for c in header))                  # append this row to the log
```

**Verify existing waivers (read-only; never consumes a use):**
```bash
export AGENTTEAMS_WAIVER_SIGNING_KEY="your-hmac-key"
agentteams --verify-waivers --output /path/to/project
#   [OK ] waiver-freshness-001 (action=security-intel-freshness)
#   1 waiver(s): 1 valid, 0 invalid.
```
`--verify-waivers` reports the signature, expiry, use-limit, and condition status of
every waiver **without minting or consuming one** (exit non-zero if any is invalid).
Without `AGENTTEAMS_WAIVER_SIGNING_KEY`, rows are reported invalid (unverifiable)
rather than silently skipped.

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
