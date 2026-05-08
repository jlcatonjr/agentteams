# Fleet-Update Authorization Policy

**Purpose:** Define approval authority, authorization chain, and conditional execution gates for cross-repository agent team bulk operations.

**Date Effective:** 2026-05-08  
**Authority Level:** Orchestrator (AgentTeamsModule)  
**Scope:** Fleet-update and any future bulk cross-repository operations

---

## I. Authorization Authority Chain

### Primary Authority: Orchestrator

**Role:** AgentTeamsModule Orchestrator (defined in orchestrator.agent.md)

**Responsibilities:**
- Determine scope (which repos are in scope)
- Route to secondary authorities for pre-checks and approvals
- Authorize execution of Step 1–4 (discovery, audits, pre-run checks, loop execution)
- Approve and sign off on Step 5 (results validation)

**Authority Delegation Requirement:** Orchestrator **must** obtain approvals from secondary authorities before executing bulk operations.

### Secondary Authorities

#### @repo-liaison

**Trigger:** Any cross-repository operation (Workflow 9, Protocol 1–4)

**Responsibilities:**
- Impact assessment (which repos are affected, what infrastructure changes)
- Adjacent-repos.md registry maintenance
- Orchestrator-to-orchestrator coordination protocol
- Cross-user or cross-directory scope extensions

**Required Approvals:**
- Cross-repo impact assessment (PASS) before execution
- Adjacent-repos registry populated before execution
- Orchestrator-to-orchestrator coordination complete (if applicable)

**Sign-Off Format:** Impact Report (prose summary + structured CSV with: repo_path, change_type, risk_level, requires_orchestrator_coordination, approved_yes_no)

#### @security

**Trigger:** Any operation touching Infrastructure Exception Pathway conditions (bulk file writes, batch updates, multi-repo automation)

**Responsibilities:**
- Infrastructure Exception Pathway compliance check
- Backup and rollback safety verification
- Credentials and sensitive data leak detection
- Operator authorization (does requestor have approval authority)

**Required Approvals:**
- Four Exception Pathway conditions verified:
  1. Pre-run backup will be created per target repo
  2. `--update --merge` (non-destructive mode) will be used
  3. Operator has reviewed dry-run output
  4. WARN status entries reviewed and signed off before commit
- No credentials/PII in fleet operation commands or logs
- Requestor identity verified and authorized

**Sign-Off Format:** Security clearance (PASS / CONDITIONAL PASS / HALT) with conditions documented

#### @repo-owner (if repository-specific)

**Trigger:** When a repository has its own orchestrator or explicitly requires owner approval

**Responsibilities:**
- Repo-specific approval (if orchestrator.agent.md delegates to repo owner)
- Validation that changes align with repo's own governance

**Required Approvals:** Per orchestrator.agent.md delegation (if present)

**Sign-Off Format:** Repo-specific approval (yes/no with rationale)

---

## II. Authorization Chain: Fleet-Update 2026-W19

**Execution Sequence:**

```
User Request → Orchestrator
    ↓
1. Orchestrator → @repo-liaison (Workflow 9, Protocol 1: Impact Assessment)
   [Produces Impact Report]
   ↓ (if PASS)
2. Orchestrator → @security (Infrastructure Exception Pathway review)
   [Produces clearance decision: PASS / CONDITIONAL PASS / HALT]
   ↓ (if PASS or CONDITIONAL PASS with conditions met)
3. Orchestrator → Execute Step 0–5 (pre-checks, dry-run, loop, validation)
   ↓ (upon completion)
4. Orchestrator → Operator (results summary, sign-off gate)
   ↓ (if operator approves)
5. Orchestrator → @git-operations (commit and push safeguards)
   ↓ (after commit)
6. Orchestrator → @work-summarizer (daily summary report)
```

---

## III. Conditional Execution Gates

### Gate 1: @repo-liaison Impact Assessment (REQUIRED)

**Condition:** All in-scope repos appear in `references/adjacent-repos.md` with complete governance attributes.

**Success Criteria:**
- Impact Report exists and is signed (PASS)
- All 38 repos appear in adjacent-repos.md
- Orchestrator-to-orchestrator coordination flagged (if applicable)
- No scope extension blockers identified

**Failure Condition (HALT):** Impact Report returns scope conflicts, missing registry entries, or coordination requirements not met.

**Remediation:** @repo-liaison → Protocol 4 (Registry Maintenance) before re-attempting authorization chain.

---

### Gate 2: @security Infrastructure Exception Pathway (REQUIRED)

**Condition:** Fleet operation complies with four exception pathway conditions.

**Condition A: Pre-Run Backup Verified**
- Requirement: Each target repo has `.backups/<timestamp>/` directory
- Verification: Script checks backup existence before `--update` on each repo
- Evidence: `tmp/by-week/2026-W19/fleet-update-backup-verification.txt` (per-repo status)

**Condition B: Non-Destructive Update Mode**
- Requirement: `--update --merge --yes` is used (never bare `--update`)
- Verification: Command-line audit of generated build_team.py calls
- Evidence: Step 4 (loop execution) logs include full command for each repo

**Condition C: Operator Dry-Run Review**
- Requirement: Operator has reviewed dry-run output and approved proceeding
- Verification: Dry-run captured in `tmp/by-week/2026-W19/fleet-update-dry-run.txt`
- Evidence: Operator sign-off in `tmp/by-week/2026-W19/fleet-update-operator-approval.txt` (format: date, approver name, signature)

**Condition D: WARN Status Sign-Off**
- Requirement: Any repos with WARN status are manually reviewed before commit
- Verification: Results CSV captures WARN status; operator explicitly approves each WARN entry
- Evidence: `tmp/by-week/2026-W19/fleet-update-operator-signoff.txt` (per-repo status, reviewed_by, timestamp)

**Success Criteria (PASS):** All four conditions verified with evidence.

**Success Criteria (CONDITIONAL PASS):** Three of four conditions verified; one condition has documented workaround or explicit exception.

**Failure Condition (HALT):** One or more conditions not verified and no documented exception.

---

### Gate 3: Operator Approval (REQUIRED before commit)

**Condition:** Operator (requestor or designated approver) reviews fleet results and approves commit.

**Review Checklist:**
- [ ] Results CSV: all 38 repos processed (no missing rows)
- [ ] Backup verification: all repos have backups before update
- [ ] Update exit codes: no repos with ERROR status (exit code != 0)
- [ ] WARN status repos: manually reviewed and approved (if any)
- [ ] Diffs: reviewed for unexpected changes (security scan)
- [ ] Scope: registry and scope-boundary documents are current

**Success Criteria:** Operator checks all items and signs off in `tmp/by-week/2026-W19/fleet-update-operator-signoff.txt`.

**Failure Condition (HALT):** Operator identifies issues, requests remediation, and blocks commit.

---

## IV. Exception & Waiver Authority

### Exception Types

**Type A: Scope Extension (adding repos outside boundary)**
- Authority: Orchestrator + @repo-liaison
- Requirement: Document justification in `references/fleet-update-authorization-policy.md` (new section)
- Approval: Both agents must sign off before execution

**Type B: Conditional Bypass (waive one of four @security conditions)**
- Authority: Orchestrator + @security (joint)
- Requirement: Document specific condition, reason for bypass, compensating control
- Approval: Both agents sign off; exception recorded in `tmp/by-week/2026-W19/fleet-update-exception-log.txt`
- Example: Dry-run review (Condition C) waived because repos have no new descriptor files; only template refresh needed

**Type C: Repo-Specific Exclusion (exclude repo that has valid descriptor)**
- Authority: @repo-liaison + repo owner (if applicable)
- Requirement: Document reason in `references/adjacent-repos.md` (update_scope → excluded)
- Approval: Both agents sign off before execution

**Type D: Emergency Rollback (revert commits after push)**
- Authority: Orchestrator + @security + @git-operations
- Requirement: Document incident, reason, rollback plan
- Approval: All three agents must consent; recorded in `tmp/by-week/2026-W19/fleet-update-incident-log.txt`

### Exception Request Process

1. Identify exception need (scope change, condition waiver, exclusion, rollback)
2. Document exception request in `tmp/by-week/2026-W19/fleet-update-exception-request.md` (include justification, proposed compensating control, affected repos)
3. Route to appropriate authority for review and approval
4. If approved: update relevant governance documents and proceed with exception recorded
5. If denied: remediate and re-attempt normal authorization chain

---

## V. Approval Record Format

### @repo-liaison Impact Report

```
IMPACT ASSESSMENT REPORT
Date: YYYY-MM-DD
Assessor: @repo-liaison
Status: PASS | CONDITIONAL PASS | HALT

Affected Repos: 38
Orchestrator-to-Orchestrator Required: [yes/no, list repos if yes]
Scope Extension Blockers: [list any issues]

Risk Summary:
- HIGH: [count]
- MEDIUM: [count]
- LOW: [count]

Approval: SIGNED by @repo-liaison @ TIMESTAMP
```

### @security Clearance Decision

```
INFRASTRUCTURE EXCEPTION PATHWAY REVIEW
Date: YYYY-MM-DD
Reviewer: @security
Status: PASS | CONDITIONAL PASS | HALT

Condition A (Backup): [verified/pending/exception]
Condition B (Non-Destructive Mode): [verified/pending/exception]
Condition C (Dry-Run Review): [verified/pending/exception]
Condition D (WARN Sign-Off): [verified/pending/exception]

Exceptions Approved: [list any Type B waivers]
Conditions Verified: [date/timestamp]

Clearance: SIGNED by @security @ TIMESTAMP
```

### Operator Sign-Off

```
OPERATOR APPROVAL & SIGN-OFF
Date: YYYY-MM-DD
Approver: [name, role]

Review Checklist:
- [ ] Results CSV: 38 rows complete
- [ ] Backup Verification: all repos OK
- [ ] Update Exit Codes: no errors
- [ ] WARN Status Repos: [count], all reviewed
- [ ] Diffs: security scan OK
- [ ] Scope: adjacent-repos.md and scope-boundary.md current

Approval: SIGNED by [operator name] @ TIMESTAMP
Approval Method: [in-person, async comment in tmp/..., email]
```

---

## VI. Approval Timeline & Schedule

### Standard Authorization Timeline (no exceptions)

| Phase | Duration | Deliverable |
|---|---|---|
| Discovery | <5 min | Fleet discovery list (38 repos) |
| @repo-liaison Review | <30 min | Impact Report (PASS/CONDITIONAL PASS) |
| @security Review | <20 min | Clearance decision (PASS/CONDITIONAL PASS) |
| Pre-checks (backup, dry-run) | <5 min per repo | Dry-run artifacts |
| Operator Dry-Run Review | <30 min | Operator approval |
| Execution (check/update loop) | <2 min per repo | Results CSV + per-repo logs |
| Operator Results Review | <30 min | Final sign-off |
| **Total End-to-End** | **~2.5 hours** | All artifacts and approvals |

### Expedited Authorization (with documented exceptions)

If Type B exceptions are pre-approved:
- Timeline reduces to ~2 hours
- Same approval signatures required; exceptions formally recorded

### Emergency Authorization (incident response)

If security incident requires immediate rollback:
- Timeline: <15 min approval
- Authority: Orchestrator + @security joint decision
- Record: Exception log + incident report

---

## VII. Authorization History & Precedent

### Fleet-Update 2026-W19 (Current)

| Authority | Status | Date | Evidence |
|---|---|---|---|
| @repo-liaison | PENDING | — | Awaiting Impact Assessment (this authorization policy draft) |
| @security | PENDING | — | Awaiting clearance review |
| Operator | PENDING | — | Awaiting pre-checks and dry-run review |

---

## VIII. Future Authorizations & Amendments

### Fleet-Update 2026-W20 and Beyond

This policy applies to all future fleet-update cycles. Before each new cycle:

1. **Policy Review:** Orchestrator reviews this document for applicability and amendments
2. **Stakeholder Confirmation:** @repo-liaison and @security confirm willingness to review
3. **Scope Re-Audit:** Refresh adjacent-repos.md and scope-boundary.md
4. **Authorization Chain:** Re-execute authorization chain with current signatories

### Policy Amendment Process

If this policy requires amendment (new condition, new authority, exception precedent):

1. Document amendment proposal in `references/fleet-update-authorization-policy-amendments.md`
2. Route to all current authorities for review
3. Obtain signatures from all authorities
4. Update this document and publish new version with revision history

**Current Version:** 1.0 (Fleet-Update-All-Repositories, 2026-W19)  
**Last Updated:** 2026-05-08  
**Next Review:** 2026-05-29 (end of 21-day cycle)

