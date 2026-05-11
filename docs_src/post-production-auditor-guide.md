# Post-Production Auditor Guide

Outcome-verification specialist for data-mutation and collection projects. Validates claimed completed work against source-of-truth state using risk-tiered sampling and evidence-backed verdicts.

---

## When to Use Post-Production-Auditor

The `@post-production-auditor` agent is automatically selected for projects with keywords: `pipeline`, `etl`, `collector`, or `mutation`.

**Manually trigger** post-production verification for any project with:
- Bulk data mutations (transformations, migrations, cleanups)
- Record-level deletions or status changes
- Dependencies on proof-of-completion (not just execution-log evidence)
- Compliance or audit requirements

---

## Core Capability

Post-production-auditor runs **Workflow 10C** (optional, user-editable) to:

1. **Verify claimed outcomes** against source-of-truth predicates
2. **Sample mutation results** using risk-tiered rates (Tier 1: 10%, Tier 2: 15%, Tier 3: 20%)
3. **Classify findings** as `PASS`, `PASS_WITH_NOTES`, `FAIL`, or `INCONCLUSIVE`
4. **Enforce closure gates** — blocks plan closeout if verdict is `FAIL` or `INCONCLUSIVE`

---

## Architecture

### Trigger Contract

Always invoke audit for:
- Mutations affecting ≥{MANUAL:BULK_MUTATION_THRESHOLD} records
- Bulk user-visible mutations exceeding risk threshold
- Contamination-remediation lane completion
- User-visible correctness outcomes

Risk-invoke if:
- Execute output includes `FAILED`, `UNKNOWN`, or exceptions
- Dry-run parity passed but confidence includes low/no-match rows
- Duplicate-key clusters exist

Governance-invoke if:
- Plan closeout depends on proof-of-completion
- Adversarial or conflict audits flag unverified final-state assumptions
- Contradictory completion claims exist

### Sampling Policy

Three tiers by trigger severity:

| Tier | Risk | Sample Rate | Cap |
|------|------|-------------|-----|
| 1 | Low | max(20, 10%) | 100 |
| 2 | Medium | max(35, 15%) | 150 |
| 3 | High | max(50, 20%) | 250 |

Mandatory inclusions for all tiers:
- All rows marked `FAILED` or `UNKNOWN`
- Duplicate-key clusters (up to {MANUAL:DUPLICATE_CLUSTER_CAP})
- At least one row per high-risk subgroup

### Verdict Rules

| Verdict | Criteria |
|---------|----------|
| `PASS` | No critical defects, unknown ≤2%, estimated defect rate ≤3% |
| `PASS_WITH_NOTES` | No critical defects, estimated defect ≤7%, all failed rows have remediation owner |
| `FAIL` | Any critical defect OR estimated defect rate >7% |
| `INCONCLUSIVE` | Evidence quality or runtime stability prevents defensible inference |

**Critical defects:**
- Expected target state did not persist in source-of-truth
- Wrong duplicate row retained while sibling row changed
- Claimed save/update did not persist

### Output Artifacts

10 required outputs in `tmp/by-week/YYYY-Www/{audit-slug}/`:

1. `post-production-audit-summary.md` — Executive summary with verdict and remediation requirements
2. `sample_manifest.csv` — Sampled records, inclusion criteria, tier assignment
3. `sample_verdicts.csv` — `PASS`/`FAIL`/`UNKNOWN` classification per sample row; must include `confirmed_false_positives` column (boolean)
4. `summary_metrics.json` — Defect rate, confidence bounds, unknown rate
5. `adversarial.md` — Challenges to audit design and sampling logic
6. `conflict.md` — Consistency check against authority files and plan artifacts
7. Evidence files — Screenshots, HTML, query-output extracts
8. `decision_replay_packet.json` — Query hashes (SHA-256), schema versions, tool versions, `window_start` and `window_end` timestamps, and environment metadata
9. `capability_check.json` — Resolved tools/agents and degraded-mode flags
10. `closure_gate_status.json` — Gate state (`OPEN`/`BLOCKED`/`CONDITIONAL_PASS`), verdict, approval chain

For `FAIL` verdicts, include `remediation_due_at` SLA and assigned `remediation_owner`.

---

## Configuration

### Required Manual Placeholders

Each placeholder has a defined owner. Set these values in your project description or `SETUP-REQUIRED.md`:

| Placeholder | Owner | Purpose | Validation |
|---|---|---|---|
| `{MANUAL:TRIGGER_CONTRACT_VERSION}` | **Data Team Lead** | Semantic version (e.g., "1.0") to track trigger-rule changes | Matches pattern: `\d+\.\d+` |
| `{MANUAL:BULK_MUTATION_THRESHOLD}` | **Project PM or Data Lead** | Integer record count (e.g., "100") that always triggers audit | Must be positive integer; reviewed against project risk profile |
| `{MANUAL:SOURCE_OF_TRUTH_SPEC}` | **Data Architect** | Query definition + stability test (see Applicability section) | Must include stability test query and expected result |
| `{MANUAL:DUPLICATE_CLUSTER_CAP}` | **Data Team** | Max duplicate-key cluster rows to sample (e.g., "50") | Based on expected duplicates; positive integer |
| `{MANUAL:AUDIT_SLUG}` | **Auto-generated** | Unique slug for this audit run (e.g., "collector-2026-05-10") | Format: `{project-name}-{YYYY-MM-DD}` |

### Pre-Flight Validation

Before audit execution, validate all placeholders are filled and correctly formatted:

```bash
agentteams --validate-audit-config --description brief.json
# Exit code 0: all placeholders valid and ownership confirmed
# Exit code 1: missing or invalid placeholder
```

If validation fails, update `SETUP-REQUIRED.md` with filled values and re-run validation.

---

## Closure Gating

Post-production audit enforces fail-closed closure behavior:

| Verdict | Gate State | Allows Closeout? |
|---------|-----------|------------------|
| `PASS` | `OPEN` | ✅ Yes |
| `PASS_WITH_NOTES` | `CONDITIONAL_PASS` | ⚠️ Yes, with approver signature |
| `FAIL` | `BLOCKED` | ❌ No (requires remediation + re-audit) |
| `INCONCLUSIVE` | `BLOCKED` | ❌ No (requires re-run or escalation) |

Orchestrator closeout is prohibited while `closure_gate_status.json` has `gate_state=BLOCKED`.

---

## Escalation & Remediation

### Destructive Mutation Clearance

If remediation includes destructive mutation:
- Route to `@security` for approval
- Require either:
  - Verified signed clearance record (HMAC-SHA256 waiver; see **[Waiver System](security-hardening-guide.md#waiver-system)** in the Security Hardening guide for format, lifecycle, and verification procedures), OR
  - Two-person manual approval with immutable audit evidence
- Document approval chain in `decision_replay_packet.json`

### Break-Glass Escalation

If `@security` is unavailable >4 hours and remediation is time-critical, break-glass escalation is permitted:

**Approval Authority (ALL required):**
- One: Data team lead OR Engineering manager
- One: Compliance officer OR CISO
- Signature method: Both signers countersign in `decision_replay_packet.json` (immutable audit record)

**Availability Check:**
- Automated check at execution: `@security` agent availability status in closure_gate_status.json
- If unavailable >4 hours AND impact quantified as critical: escalation authorized
- If only one signer available: document exception and escalate to CTO

**Execution:**
1. Permit remediation with `EXCEPTION` status in `closure_gate_status.json`
2. Both signers countersign in decision_replay_packet.json (e.g., "alice@org", "bob@org")
3. Execute with git-commit signatures from both signers
4. Schedule mandatory retrospective security audit within 5 business days
5. Document business justification, emergency contacts, and approval chain

**Emergency Contacts (add to project description):**
```yaml
emergency_contacts:
  data_team_lead: alice@org
  compliance_officer: bob@org
  cto_escalation: charlie@org
```

---

## Applicability & Limitations

### Valid For

- Projects with defined source-of-truth predicates and stability test (see below)
- Stable entity identity (immutable or versioned primary keys) — **must be validated before audit**
- Strong consistency or bounded staleness (atomic snapshots available)

### Entity Identity Stability Check (Required)

Before audit proceeds, you **must** validate that entity identity is stable:

1. **Add to SOURCE_OF_TRUTH_SPEC:**
   ```
   Primary Key: user_id (type: bigint, immutable=true)
   Stability Test Query: SELECT COUNT(*) FROM users WHERE id IN (...sample...) AND version = 1
   Expected: COUNT(*) = sample_size (no identity changes during mutation window)
   ```

2. **Pre-flight check:** The audit runs this query against a sample before proceeding
   - If test passes: continues with audit
   - If test fails: blocks with error `STABILITY_CHECK_FAILED` and halts audit

### NOT Valid For

- **Eventual-consistent databases (DynamoDB, Cassandra) without consistency bounds** — Pre-flight check will detect and block with error `CONSISTENCY_MODEL_VIOLATION`
- Projects lacking stable entity identity (detected via stability test; audit blocks if test fails)
- Systems where true state may diverge across replicas during audit window

> ⚠️ **If you attempt to audit an eventually-consistent system:** The audit will block immediately with error `CONSISTENCY_MODEL_VIOLATION. Source-of-truth spec indicates eventual consistency without bounds. Pre-flight check failed.`

→ **For eventual-consistent systems:** Use a domain-specific audit profile that accounts for replication windows and consistency bounds.

---

## Non-Blocking Limitations

These affect operational convenience but do not block core audit functionality:

1. **Persistent audit trail** — Currently stores in ephemeral `tmp/`; production deployments may require repository-level storage or external integration
2. **Cross-run comparison** — Each audit is independent; manual export required for trend analysis
3. **Metrics rollups** — No built-in dashboard; extract and aggregate externally
4. **Cryptographic verification** — HMAC-SHA256 verification requires runtime utility support; dual-approval fallback is valid

---

## Operational Readiness

✅ **This agent is ready for production use.** Core audit capabilities (source-of-truth sampling, verdict generation, closure gating) are fully functional. Listed limitations are operational enhancements, not functional blockers.

---

## Example Workflow

```yaml
# Project description excerpt
project_goal: |
  Run an ETL mutation pipeline on 100K+ customer records 
  and verify no data was lost or incorrectly transformed.

# Auto-selects post-production-auditor due to "pipeline" + "mutation" keywords

# In orchestrator.agent.md (USER-EDITABLE section), add Workflow 10C:
Workflow 10C:
  name: Post-Production Verification
  trigger: After implementation claims completion
  steps:
    1. Run @post-production-auditor with sampled source-of-truth checks
    2. Review verdict, defects, and remediation requirements
    3. If FAIL/INCONCLUSIVE: block closeout, route to remediation
    4. If PASS/PASS_WITH_NOTES: allow closeout, log approver signature
```

---

## References

- **Template:** `agentteams/templates/domain/post-production-auditor.template.md`
- **Orchestrator Workflow 10C:** `orchestrator.template.md` (user-editable section)
- **Configuration:** `docs_src/template-authoring.md` → Post-Production Auditor Registration Notes
- **Closure Gate Schema:** `schemas/post-production-audit-closure-gate-status.schema.json`
- **Decision Replay Schema:** `schemas/post-production-audit-decision-replay-packet.schema.json`
