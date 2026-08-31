# Post-Production Auditor Guide

Outcome-verification specialist for any task domain (software, docs, operations, data). Validates claimed completed work against source-of-truth state using risk-tiered sampling and evidence-backed verdicts.

---

## When to Use Post-Production-Auditor

The `@post-production-auditor` agent is automatically selected using contextual trigger matching in `agentteams/analyze.py`.

Auto-selection requires co-occurring cues, not a single keyword:
- At least one operation/state-change cue (for example: `migration`, `deploy`, `release`, `cleanup`, `reconcile`, `mutation`)
- At least one verification/proof cue (for example: `verify`, `validation`, `outcome`, `final state`, `proof-of-completion`, `source-of-truth`)

Legacy pipeline wording remains supported when paired with verification cues (for example: `etl`, `collector`, `pipeline` + `verify`).

**Manually trigger** post-production verification for any project with:
- High-impact state changes (records, files, artifacts, endpoints)
- Deletions, rewrites, migrations, releases, or remediations requiring proof-of-result
- Dependencies on proof-of-completion (not just execution-log evidence)
- Compliance or audit requirements

Manual override path:
- Set `selected_archetypes` in your project description to include `post-production-auditor` when you want explicit inclusion regardless of auto-selection.

---

## Core Capability

Post-production-auditor runs **Workflow 10C** (optional, user-editable) to:

1. **Verify claimed outcomes** against source-of-truth predicates
2. **Sample outcome results** using risk-tiered rates (Tier 1: 10%, Tier 2: 15%, Tier 3: 20%)
3. **Classify findings** as `PASS`, `PASS_WITH_NOTES`, `FAIL`, or `INCONCLUSIVE`
4. **Enforce closure gates** — blocks plan closeout if verdict is `FAIL` or `INCONCLUSIVE`

---

## Architecture

### Trigger Contract

Always invoke audit for:
- Irreversible or high-impact state changes affecting ≥{MANUAL:BULK_MUTATION_THRESHOLD} scoped units
- Bulk user-visible state changes exceeding risk threshold
- Contamination-remediation lane completion
- User-visible correctness outcomes

Risk-invoke if:
- Execute output includes `FAILED`, `UNKNOWN`, or exceptions
- Dry-run parity passed but confidence includes low/no-match or low-evidence buckets
- Identity-collision clusters exist

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
- All sampled units marked `FAILED` or `UNKNOWN`
- Identity-collision clusters (up to {MANUAL:DUPLICATE_CLUSTER_CAP})
- At least one sampled unit per high-risk subgroup

### Verdict Rules

| Verdict | Criteria |
|---------|----------|
| `PASS` | No critical defects, unknown ≤2%, estimated defect rate ≤3% |
| `PASS_WITH_NOTES` | No critical defects, estimated defect ≤7%, all failed sampled units have remediation owner |
| `FAIL` | Any critical defect OR estimated defect rate >7% |
| `INCONCLUSIVE` | Evidence quality or runtime stability prevents defensible inference |

**Critical defects:**
- Expected target state did not persist in source-of-truth
- Wrong identity-collision sibling retained while target sibling changed
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

Each placeholder has a defined owner. Set these values in `SETUP-REQUIRED.md` (or extend your intake schema if you want them directly in project descriptions):

| Placeholder | Owner | Purpose | Validation |
|---|---|---|---|
| `{MANUAL:TRIGGER_CONTRACT_VERSION}` | **Data Team Lead** | Semantic version (e.g., "1.0") to track trigger-rule changes | Matches pattern: `\d+\.\d+` |
| `{MANUAL:BULK_MUTATION_THRESHOLD}` | **Project PM or Domain Lead** | Integer scoped-unit threshold (e.g., "100") that always triggers audit | Must be positive integer; reviewed against project risk profile |
| `{MANUAL:SOURCE_OF_TRUTH_SPEC}` | **Data Architect** | Query definition + stability test (see Applicability section) | Must include stability test query and expected result |
| `{MANUAL:DUPLICATE_CLUSTER_CAP}` | **Domain Team** | Max identity-collision cluster units to sample (e.g., "50") | Based on expected collisions; positive integer |
| `{MANUAL:AUDIT_SLUG}` | **Project Team (manual-required)** | Unique slug for this audit run (e.g., "release-2026-05-10") | Format: `{project-name}-{YYYY-MM-DD}` |

### Pre-Flight Validation

Before audit execution, validate all placeholders are filled and correctly formatted. The current CLI does not provide a dedicated `--validate-audit-config` flag.

Recommended validation path:

```bash
agentteams --description brief.json --project /path/to/project --framework copilot-vscode --check
agentteams --description brief.json --project /path/to/project --framework copilot-vscode --scan-security
# --check detects drift/structural issues in generated teams
# --scan-security surfaces unresolved placeholders and security concerns
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

If remediation includes destructive mutation or irreversible state change:
- Route to `@security` for approval
- Require either:
  - Verified signed clearance record (HMAC-SHA256 waiver; see **[Waiver System](security-hardening-guide.md#waiver-system)** in the Security Hardening guide for format, lifecycle, and verification procedures), OR
  - Two-person manual approval with immutable audit evidence
- Document approval chain in `decision_replay_packet.json`

### Break-Glass Escalation

If `@security` is unavailable >4 hours and remediation is time-critical, break-glass escalation is permitted:

**Approval Authority (ALL required):**
- Two independent authorized signers
- Recommended: one technical owner and one compliance/security approver
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

**Emergency Contacts (store in operational runbook or incident reference, not in schema-validated brief fields unless you extend your schema):**
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
- Stable target identity (immutable/versioned keys, stable file paths, or deterministic artifact identifiers) — **must be validated before audit**
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
  - If test fails: block and halt audit (error code naming is profile-specific)

### NOT Valid For

- **Eventual-consistent databases (DynamoDB, Cassandra) without consistency bounds** — Pre-flight check must detect and block
- Projects lacking stable entity identity (detected via stability test; audit blocks if test fails)
- Systems where true state may diverge across replicas during audit window

> ⚠️ **If you attempt to audit an eventually-consistent system:** The audit must block immediately with a consistency-model violation outcome and fail-closed gate behavior.

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
  Execute a production release with schema migration and verify final state
  matches acceptance predicates before closeout.

# Auto-selects post-production-auditor due to operation + verification cues

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
