# Part III — Clearance, waivers, and grants

## The authorization triad  ✅ {#S7}

agentteams separates *authorization* into three distinct instruments, one per
distinct question. Collapsing them is a category error; each has its own ledger, its
own scope, and its own answer to "may this proceed?"

| Instrument | Answers | Authorizes | Ledger |
|---|---|---|---|
| **Clearance** | May this destructive action run *here, now*? | A destructive action **locally, before it runs** (C-5) | `references/security-decisions.log.csv` (decision log) |
| **Waiver** | May this proceed past a *stop*? | **Lifts a gate block** (destructive gate, or stale-intel gate) | `references/security-waivers.log.csv` |
| **Grant** | May this reach into *another workspace*? | **Widens a cross-workspace write boundary** | `references/capability-grants.log.csv` (holder's workspace) |

A clearance is a pre-recorded decision that a specific destructive step is
acceptable — the C-5 "clearance precedes destruction" requirement made concrete. A
waiver *lifts a stop* that a gate has already raised. A grant *permits a reach*: it
widens a boundary rather than lifting a block, and is the cross-workspace analogue of
a waiver (`agentteams/cli/grants.py:1-36` states the waiver-vs-grant intent
distinction directly — a waiver "lifts a stop," a grant "permits a reach").

**None of the three overrides a HALT (C-2).** A `@security` HALT is checked *first*
and no instrument is consulted for it. A clearance authorizes a step that was never
HALTed; a waiver lifts a *gate* block, not a HALT; a grant widens a write boundary
but "a holder still cannot proceed past a HALT on the granted write"
(`agentteams/cli/grants.py:34-35`). This is the C-2 finality property carried through
the whole triad.

**One shared trust model — symmetric HMAC, fail-closed.** All three rest on the same
signed-ledger primitives (`agentteams/cli/signed_ledger.py:9-14`): a keyed
HMAC-SHA256 over an ordered field list, an ISO-8601 expiry check, and a
symlink/`..`-safe path-containment test. Each instrument uses its own key
(`AGENTTEAMS_WAIVER_SIGNING_KEY`, `AGENTTEAMS_GRANT_SIGNING_KEY`; decision-signing
when active — `agentteams/cli/security_gate.py:39-69`). When the relevant signing key
is unset the instrument **fails closed** — it refuses rather than proceeding
unsigned.

**Honest ceiling.** The signing is *symmetric*: one shared key per instrument. It
defends against a **keyless forger** — an agent that cannot read the signing key
cannot fabricate a valid row — but it does **not** defend against an actor who holds
the key (an adversarial peer team, or an agent that has captured the key). The key
must be issued out-of-band and never enter an agent session. Cross-trust-boundary
unforgeability would require asymmetric signatures, which the Python standard library
does not provide; `agentteams/cli/signed_ledger.py` (`hmac_sign`/`hmac_verify`) is the
single documented **asymmetric swap point** where such a backend would later slot in.
The triad engages as tested against a keyless forger; it is not a guarantee against a
key-holder.

## Security decisions log and the CONDITIONAL PASS lifecycle  ✅ {#S8}

Every sentinel verdict — including a plain PASS — appends a row to
`references/security-decisions.log.csv`. The current schema is **9 columns**:
`date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner`.
A legacy 6-column schema is still accepted by a schema-kind detector, so older logs
continue to read (`agentteams/cli/decision_log.py:22-63`).

The destructive gate consults this log as the clearance engine. Its decision order:

| Log state | Gate behaviour |
|---|---|
| Unretracted **HALT** anywhere | **Blocks** — checked first, over the whole log |
| **PASS** (matching, unconsumed) | Allows |
| **CONDITIONAL PASS**, `conditions_verified == "verified"` | Allows |
| **CONDITIONAL PASS**, conditions not verified | Blocks **"as if HALT"** |
| Row already consumed/used | Skipped — a clearance **cannot be replayed** |

The HALT-first, replay-proof logic lives in
`agentteams/cli/security_gate.py:96-259`; row-consumption bookkeeping is at
`agentteams/cli/security_gate.py:619-659`. Fail-closed throughout: every unresolved
path resolves to deny.

**The Pre-Execution Security Check (procedure, ⚙ at the orchestrator).** For any step
carrying a CONDITIONAL PASS, the orchestrator must read the log, confirm every
condition has recorded evidence, treat `conditions_verified = pending` as a HALT and
surface it to the user, and only flip to `verified` and proceed once all conditions
are met. The orchestrator template states this is "not optional… blocks the operation
as if HALT" (`agentteams/templates/universal/orchestrator.template.md:339-350`). This
is a procedural mirror of the deterministic gate check, not a second code control.

**Authorship and signature.** An authorizing PASS or CONDITIONAL PASS row must be
issued by an **approved author** and carry a valid signature when decision-signing is
active. `check_clearance()` is the read-only inspection counterpart — it reports what
a clearance *would* do without spending it, consuming nothing
(`agentteams/cli/decision_log.py:187-234`).

**Honest ceiling.** The verdicts recorded here are, except where the S-1/S-8 scanner
backs them, non-deterministic model judgments (S5). The log makes those judgments
*durable, ordered, and replay-proof*; it does not make them *correct*. Its guarantee
is bookkeeping integrity — a spent clearance cannot be reused, a HALT cannot be
outvoted — not the soundness of the underlying verdict.

## Signed waivers  ✅ {#S9}

A waiver clears a **gate block** — the destructive gate, or the stale-intel gate
(S12) — and is kept in `references/security-waivers.log.csv`. It **never overrides a
HALT**: a HALT is checked before any waiver is consulted (C-2).

**On-demand ledger.** Both `references/security-waivers.log.csv` and the
`references/security-approvers.txt` roster are **created on demand** — on the first
waiver issued and the first approver configured, respectively. They are **not shipped
in a fresh tree**; their absence is expected, not a defect. A reader auditing a clean
checkout should not read "no waivers file" as "waivers disabled."

**Validity requires all of the following** (`agentteams/cli/security_gate.py:511-616`):

| Requirement | Meaning |
|---|---|
| Action-id scope match | The waiver names the exact action it clears |
| `conditions_verified == "verified"` | Any attached conditions are met |
| Non-empty approver / ticket / reason | An accountable human record exists |
| Approver on the roster | The approver appears in `references/security-approvers.txt` |
| `expires_at` in the future | **Time-bounded** |
| `uses < max_uses` | **Use-counted** |
| Verifying HMAC-SHA256 signature | Signature over the business fields, excluding `timestamp`/`signature` |

On consumption, `uses` is incremented and the row is **re-signed**
(`agentteams/cli/security_gate.py:430-477`). Because `uses` is a signed field, a
tampered counter invalidates the row — an attacker cannot hand-edit `uses` back down
to reuse a spent waiver without breaking the signature. A missing signing key ⇒
**fail-closed** (`agentteams/cli/signed_ledger.py:40-92`).

`--verify-waivers` is a **read-only audit**: it validates every row without consuming
any, emitting one `valid`/`invalid`-plus-reason line per row
(`agentteams/cli/security_gate.py:39-69`).

**Honest ceiling.** Every waiver guarantee is symmetric-HMAC-bounded (S7): the
signature stops a keyless forger, not a key-holder. Scope, expiry, use-count, and
approver-roster checks constrain *what a valid waiver can do*; they do nothing against
an actor who can mint a valid signature. The waiver's value is that it makes lifting a
gate a scoped, expiring, counted, signed, roster-attributed act — visible in the log —
not that it is unforgeable.

## Capability grants  ✅ {#S10}

A grant is a **signed, scoped, time-bounded** authorization by an `issuer_team` for a
`holder_team` to perform an operation (e.g. `write`) on a `target_path` in the
issuer's workspace — the cross-workspace analogue of a waiver. Its ledger lives in the
**holder's** workspace at `references/capability-grants.log.csv` (a bearer-capability
model: the holder holds the grants issued to it). Like the waiver log, it is **created
on the first grant, not shipped** — absence in a fresh tree is expected
(`agentteams/cli/grants.py:1-36`).

**Enforcement is generation-time only.** On (re)generation with the sandbox on, the
targets of the valid `write` grants a holder holds are merged into the sandbox
`allowWrite` set. A freshly issued grant is therefore **inert until the operator
re-runs an update** — there is deliberately **no runtime path by which an agent widens
its own OS boundary** (`agentteams/cli/grants.py:15-19`). The generation-time widening
path does not consume a use, so under today's only enforcement path `expires_at` is
the active temporal bound and `max_uses` is validated-but-not-decremented (reserved
for a future per-write consume path).

**`validate_grant()` is fail-closed and ordered**
(`agentteams/cli/grants.py:126-235,306-473`):

1. Required fields present
2. **Signature** verifies
3. Not expired
4. Use-counter not exhausted
5. Approver on the roster

Cross-workspace grants require an **explicit** approver roster — the
`{security,@security}` self-clear fallback is refused for a cross-workspace reach.
Path-safety guards reject `~`, `..`-escapes, and targets outside a signed
`issuer_root`, and a **SHA-256 `prev_digest` chain** is verified on every read
(fail-closed on tamper) (`agentteams/cli/grants.py:56-84,476-639`).

`--issue-grant` and `--verify-grants` mirror the waiver commands (issue deposits into
the holder workspace; verify is a read-only per-row audit). **C-2 parity:** a grant
widens a write boundary, it never overrides a HALT.

**Honest ceiling.** Same symmetric-HMAC bound as the rest of the triad (S7): the
signature and digest chain defend against a keyless forger and against accidental or
undetected tampering, not against an adversarial peer team that holds
`AGENTTEAMS_GRANT_SIGNING_KEY`. The prev_digest chain makes ledger tampering
*evident*; it does not make it *impossible*. And because enforcement is
generation-time, a grant's effect on the OS boundary engages only when the operator
regenerates with the sandbox on — the grant is configuration, not a live capability.

**Sources for Part III.** `agentteams/cli/grants.py:1-36,56-84,126-235,306-473,476-639`;
`agentteams/cli/security_gate.py:39-69,96-259,430-477,511-616,619-659`;
`agentteams/cli/decision_log.py:22-63,187-234`;
`agentteams/cli/signed_ledger.py:9-14,40-92`;
`agentteams/templates/universal/orchestrator.template.md:339-350`.
