# Part III — Clearance, waivers, and grants

These three instruments are how the system says "yes" to something risky in a
recorded, bounded way. For a reviewer, the key facts are (a) they are three
*different* answers to three *different* questions, (b) **none of them overrides a
HALT**, and (c) they all rest on the same symmetric-signing trust model whose
ceiling is the same throughout.

## The authorization triad  ✅ {#S7}

Collapsing these three into "permissions" is a category error. Each answers a
distinct question and lives in its own ledger:

| Instrument | Answers | Authorizes | Ledger |
|---|---|---|---|
| **Clearance** | May this destructive action run *here, now*? | A destructive action **locally, before it runs** (C-5) | `references/security-decisions.log.csv` |
| **Waiver** | May this proceed past a *stop*? | **Lifts a gate block** (destructive gate, or stale-intel gate) | `references/security-waivers.log.csv` |
| **Grant** | May this reach into *another workspace*? | **Widens a cross-workspace write boundary** | `references/capability-grants.log.csv` (holder's) |

A clearance is a pre-recorded decision that a specific destructive step is
acceptable — C-5 made concrete. A waiver *lifts a stop* a gate has already
raised. A grant *permits a reach*: it widens a boundary rather than lifting a
block, and is the cross-workspace analogue of a waiver.

**None of the three overrides a HALT (C-2).** A `@security` HALT is checked
*first*, and no instrument is consulted for it. This is the C-2 finality property
carried through the whole triad, and it is the reason a reviewer can reason about
HALT independently of the authorization machinery: no amount of valid
authorization reaches past a stop.

**Honest ceiling — one shared symmetric-HMAC trust model.** All three rest on
keyed HMAC-SHA256 over an ordered field list, an expiry check, and a
symlink/`..`-safe path-containment test, each using its own key, and each
**fails closed when its key is unset** (it refuses rather than proceeding
unsigned). The ceiling is exact and applies to every instrument below: symmetric
signing defends against a **keyless forger** — an agent that cannot read the key
cannot fabricate a valid row — but **not against an actor who holds the key** (an
adversarial peer team, or an agent that has captured the key). The key must be
issued out-of-band and never enter an agent session. Cross-trust-boundary
unforgeability would require asymmetric signatures, which the Python standard
library does not provide; `signed_ledger.py` is the single documented asymmetric
swap point. The triad engages as tested against a keyless forger; it is not a
guarantee against a key-holder.

## Security decisions log and the CONDITIONAL PASS lifecycle  ✅ {#S8}

The decision log is where the sentinel's verdicts become durable, ordered, and
replay-proof — turning judgment into bookkeeping the gate can trust.

Every verdict, including a plain PASS, appends a row (current schema is 9
columns; a legacy 6-column schema is still read). The destructive gate consults
this log as its clearance engine, in this order:

| Log state | Gate behaviour |
|---|---|
| Unretracted **HALT** anywhere | **Blocks** — checked first, over the whole log |
| **PASS** (matching, unconsumed) | Allows |
| **CONDITIONAL PASS**, `conditions_verified == "verified"` | Allows |
| **CONDITIONAL PASS**, conditions not verified | Blocks **"as if HALT"** |
| Row already consumed/used | Skipped — a clearance **cannot be replayed** |

Two adversary-relevant properties: HALT is checked first (a stop cannot be
outvoted), and a consumed row is skipped (a clearance **cannot be replayed** to
authorize a second action).

**The Pre-Execution Security Check (⚙ procedure).** For any step carrying a
CONDITIONAL PASS, the orchestrator must read the log, confirm every condition has
recorded evidence, treat a `pending` verification as a HALT and surface it, and
only proceed once all conditions are met. This is a procedural mirror of the
deterministic gate check, **not a second code control** — a reviewer should count
it as instruction-level (S2), not as a guarantee.

**Honest ceiling.** The verdicts recorded here are, except where the S-1/S-8
scanner backs them, non-deterministic model judgments (S5). The log makes those
judgments *durable, ordered, and replay-proof*; it does **not** make them
*correct*. Its guarantee is bookkeeping integrity — a spent clearance cannot be
reused, a HALT cannot be outvoted — not the soundness of the underlying verdict.
Full schema and line-precise gate logic: Edition R, S8.

## Signed waivers  ✅ {#S9}

A waiver is the only sanctioned way past a *gate block* — and, by C-2, never past
a HALT. Its whole value is that lifting a stop becomes a scoped, expiring,
counted, signed, roster-attributed, logged act rather than a silent override.

**Validity requires all of:** an action-id scope match; verified conditions; a
non-empty approver/ticket/reason; the approver present on the roster; an
`expires_at` in the future (**time-bounded**); `uses < max_uses`
(**use-counted**); and a verifying HMAC-SHA256 signature over the business
fields. On consumption, `uses` is incremented and the row **re-signed** — and
because `uses` is a signed field, an attacker cannot hand-edit the counter back
down to replay a spent waiver without breaking the signature. A missing signing
key fails closed.

**On-demand ledger — a note for auditors of a clean checkout.** Both the waiver
log and the approver roster are **created on demand** (first waiver, first
approver), not shipped. Their absence in a fresh tree is expected — do **not**
read "no waivers file" as "waivers disabled." A read-only audit,
`--verify-waivers`, validates every row without consuming any.

**Honest ceiling.** Every waiver guarantee is symmetric-HMAC-bounded (S7): the
signature stops a keyless forger, not a key-holder. Scope, expiry, use-count, and
roster checks constrain *what a valid waiver can do*; they do nothing against an
actor who can mint a valid signature. The waiver's value is visibility and
bounding, not unforgeability. Full validity table and line numbers: Edition R,
S9.

## Capability grants  ✅ {#S10}

A grant is the cross-workspace analogue of a waiver: a signed, scoped,
time-bounded authorization by an `issuer_team` for a `holder_team` to perform an
operation (e.g. `write`) on a `target_path` in the issuer's workspace. Its ledger
lives in the **holder's** workspace (a bearer-capability model) and, like the
waiver log, is created on first grant, not shipped.

**The property a reviewer should note first: enforcement is generation-time
only.** On (re)generation with the sandbox on, the targets of a holder's valid
`write` grants are merged into the sandbox `allowWrite` set. A freshly issued
grant is therefore **inert until the operator re-runs an update** — there is
deliberately **no runtime path by which an agent widens its own OS boundary.** A
grant is configuration, not a live capability. This is a deliberate trust
boundary: self-granting at runtime is the escalation it forecloses.

**`validate_grant()` is fail-closed and ordered:** required fields → signature →
not expired → use-counter not exhausted → approver on roster. Cross-workspace
grants require an **explicit** approver roster — the self-clear fallback is
refused for a cross-workspace reach — and path-safety guards reject `~`,
`..`-escapes, and targets outside a signed `issuer_root`. A SHA-256 `prev_digest`
chain is verified on every read (fail-closed on tamper), so ledger tampering is
*evident*.

**Honest ceiling.** Same symmetric-HMAC bound as the rest of the triad (S7): the
signature and digest chain defend against a keyless forger and against
undetected tampering, **not** against an adversarial peer team that holds the
grant signing key. The prev_digest chain makes ledger tampering *evident*, not
*impossible*. And because enforcement is generation-time, a grant affects the OS
boundary only when the operator regenerates with the sandbox on. Full validation
order and line numbers: Edition R, S10.

---

**Sources for Part III.** `agentteams/cli/grants.py`;
`agentteams/cli/security_gate.py`; `agentteams/cli/decision_log.py`;
`agentteams/cli/signed_ledger.py`;
`agentteams/templates/universal/orchestrator.template.md`. Line-precise
provenance: `SOURCES.md` (S27).
