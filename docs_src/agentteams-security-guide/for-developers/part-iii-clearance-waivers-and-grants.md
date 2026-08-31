# Part III — Clearance, waivers, and grants

## The authorization triad  ✅ {#S7}

Three instruments, one per question. Pick the right one; they are kept in
separate ledgers.

| Instrument | Answers | Authorizes | Ledger | Key env var |
|---|---|---|---|---|
| **Clearance** | may this destructive action run *here, now*? | a destructive action **locally, before it runs** (C-5) | `references/security-decisions.log.csv` | decision-signing (when active) |
| **Waiver** | may this proceed past a *stop*? | **lifts a gate block** (destructive or stale-intel gate) | `references/security-waivers.log.csv` | `AGENTTEAMS_WAIVER_SIGNING_KEY` |
| **Grant** | may this reach into *another workspace*? | **widens a cross-workspace write boundary** | `references/capability-grants.log.csv` (holder's workspace) | `AGENTTEAMS_GRANT_SIGNING_KEY` |

A waiver "lifts a stop"; a grant "permits a reach" — the grant is the
cross-workspace analogue of a waiver
(`agentteams/cli/grants.py:1-36`).

**None of the three overrides a HALT (C-2).** HALT is checked *first* and no
instrument is consulted for it (`agentteams/cli/grants.py:34-35`).

**One shared trust model — symmetric HMAC, fail-closed**
(`agentteams/cli/signed_ledger.py:9-14`): keyed HMAC-SHA256 over an ordered field
list, an ISO-8601 expiry check, and a symlink/`..`-safe containment test. Each
instrument uses its own key (`AGENTTEAMS_WAIVER_SIGNING_KEY`,
`AGENTTEAMS_GRANT_SIGNING_KEY`; decision-signing when active,
`agentteams/cli/security_gate.py:39-69`). **Key unset ⇒ fails closed** — it
refuses rather than proceeding unsigned.

**Honest ceiling — what it costs.** Signing is *symmetric* (one shared key per
instrument). It stops a **keyless forger** but not an actor who **holds the key**.
Issue keys out-of-band and never let one enter an agent session. Asymmetric
signatures aren't in the stdlib; `agentteams/cli/signed_ledger.py`
(`hmac_sign`/`hmac_verify`) is the single documented **asymmetric swap point**.

## Security decisions log and the CONDITIONAL PASS lifecycle  ✅ {#S8}

Every sentinel verdict — including PASS — appends a row to
`references/security-decisions.log.csv`. Current schema is **9 columns**:
`date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner`.
A legacy 6-column schema is still accepted by a schema-kind detector
(`agentteams/cli/decision_log.py:22-63`).

The destructive gate consults this log as the clearance engine:

| Log state | Gate behaviour |
|---|---|
| Unretracted **HALT** anywhere | **Blocks** — checked first, over the whole log |
| **PASS** (matching, unconsumed) | Allows |
| **CONDITIONAL PASS**, `conditions_verified == "verified"` | Allows |
| **CONDITIONAL PASS**, not verified | Blocks **"as if HALT"** |
| Row already consumed | Skipped — a clearance **cannot be replayed** |

HALT-first, replay-proof logic: `agentteams/cli/security_gate.py:96-259`;
consumption bookkeeping: `agentteams/cli/security_gate.py:619-659`. Fail-closed
throughout.

**Pre-Execution Security Check (⚙ orchestrator procedure).** For any CONDITIONAL
PASS step: read the log, confirm every condition has evidence, treat
`conditions_verified = pending` as HALT and surface to the user, and only flip to
`verified` and proceed once all conditions are met — "not optional… blocks the
operation as if HALT" (`agentteams/templates/universal/orchestrator.template.md:339-350`).
This mirrors the code check; it is not a second code control.

**Inspect without spending:** `check_clearance()` reports what a clearance
*would* do, consuming nothing
(`agentteams/cli/decision_log.py:187-234`). An authorizing PASS/CONDITIONAL PASS
row must be issued by an **approved author** and carry a valid signature when
decision-signing is active.

**Honest ceiling.** The log makes verdicts *durable, ordered, replay-proof*; it
does not make them *correct* (S5). Its guarantee is bookkeeping integrity — a
spent clearance can't be reused, a HALT can't be outvoted — not verdict
soundness.

## Signed waivers  ✅ {#S9}

A waiver clears a **gate block** (destructive gate or stale-intel gate, S12);
kept in `references/security-waivers.log.csv`. It **never overrides a HALT**.

**On-demand ledger.** Both `references/security-waivers.log.csv` and the
`references/security-approvers.txt` roster are **created on demand** (first waiver
/ first approver), **not shipped**. Their absence in a fresh tree is expected —
do not read "no waivers file" as "waivers disabled."

**Validity requires all of** (`agentteams/cli/security_gate.py:511-616`):

| Requirement | Meaning |
|---|---|
| Action-id scope match | names the exact action it clears |
| `conditions_verified == "verified"` | attached conditions met |
| Non-empty approver / ticket / reason | accountable human record |
| Approver on the roster | appears in `references/security-approvers.txt` |
| `expires_at` in the future | **time-bounded** |
| `uses < max_uses` | **use-counted** |
| Verifying HMAC-SHA256 signature | over business fields, excluding `timestamp`/`signature` |

On consumption `uses` is incremented and the row **re-signed**
(`agentteams/cli/security_gate.py:430-477`) — because `uses` is a signed field, a
tampered counter invalidates the row. Missing signing key ⇒ **fail-closed**
(`agentteams/cli/signed_ledger.py:40-92`).

**The command:**

```
agentteams --verify-waivers
```

A **read-only audit** — validates every row without consuming any, one
`valid`/`invalid`+reason line per row
(`agentteams/cli/security_gate.py:39-69`).

**Honest ceiling — what it costs.** Every waiver guarantee is symmetric-HMAC
bounded (S7): the signature stops a keyless forger, not a key-holder. Scope,
expiry, use-count, and roster constrain *what a valid waiver can do*; they do
nothing against an actor who can mint a valid signature. The value is that
lifting a gate becomes a scoped, expiring, counted, signed, roster-attributed,
logged act — not that it is unforgeable.

## Capability grants  ✅ {#S10}

A grant is a **signed, scoped, time-bounded** authorization by an `issuer_team`
for a `holder_team` to perform an op (e.g. `write`) on a `target_path` in the
issuer's workspace — the cross-workspace analogue of a waiver. Its ledger lives
in the **holder's** workspace (`references/capability-grants.log.csv`,
bearer-capability model), **created on the first grant, not shipped**
(`agentteams/cli/grants.py:1-36`).

**Enforcement is generation-time only.** On (re)generation with the sandbox on,
valid `write` grants' targets are merged into the sandbox `allowWrite` set. A
freshly issued grant is therefore **inert until you re-run an update** — there is
deliberately **no runtime path for an agent to widen its own OS boundary**
(`agentteams/cli/grants.py:15-19`). The generation-time path does not consume a
use, so today `expires_at` is the active temporal bound and `max_uses` is
validated-but-not-decremented (reserved for a future per-write consume path).

**`validate_grant()` is fail-closed and ordered**
(`agentteams/cli/grants.py:126-235,306-473`):

1. Required fields present → 2. **Signature** verifies → 3. Not expired →
4. Use-counter not exhausted → 5. Approver on the roster.

Cross-workspace grants require an **explicit** approver roster — the
`{security,@security}` self-clear fallback is refused. Path-safety guards reject
`~`, `..`-escapes, and targets outside a signed `issuer_root`; a **SHA-256
`prev_digest` chain** is verified on every read (fail-closed on tamper)
(`agentteams/cli/grants.py:56-84,476-639`).

**The commands:**

```
agentteams --issue-grant …     # deposits a signed grant into the holder workspace
agentteams --verify-grants     # read-only per-row audit (mirrors --verify-waivers)
```

**C-2 parity:** a grant widens a write boundary, never overrides a HALT.

**Honest ceiling — what it costs.** Same symmetric-HMAC bound (S7): signature and
digest chain defend a keyless forger and detect accidental/undetected tampering,
**not** an adversarial peer team holding `AGENTTEAMS_GRANT_SIGNING_KEY`. The
prev_digest chain makes ledger tampering *evident*, not *impossible*. And because
enforcement is generation-time, a grant is configuration — its effect on the OS
boundary engages only when you regenerate with the sandbox on.

---

**Sources for Part III.**
`agentteams/cli/grants.py:1-36,56-84,126-235,306-473,476-639`;
`agentteams/cli/security_gate.py:39-69,96-259,430-477,511-616,619-659`;
`agentteams/cli/decision_log.py:22-63,187-234`;
`agentteams/cli/signed_ledger.py:9-14,40-92`;
`agentteams/templates/universal/orchestrator.template.md:339-350`.
