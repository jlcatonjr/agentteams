# Part IV — The gates

The gates are the deterministic, fail-closed checks at CLI entry points — the
code half of enforcement (S2), distinct from the runtime PreToolUse hook (S19).
Each prints a **gate-first `[SEC-GATE/…]` code** so you can see exactly which
check fired.

| SEC-GATE family | Fires on | Cleared by |
|---|---|---|
| `[SEC-GATE/DESTRUCTIVE:*]` (S11) | destructive writes: overwrite-update, overwrite, restore-backup | PASS / verified CONDITIONAL PASS clearance, or valid signed waiver |
| `[SEC-GATE/INTEL-FRESHNESS]` (S12) | stale threat-intel snapshot on a generation run | signed waiver, action `security-intel-freshness` |

## The destructive-operation gate  ✅ {#S11}

Prints `[SEC-GATE/DESTRUCTIVE:*]` (the `*` names which action fired), then
`blocked: …` and exit 1. It wraps the clearance engine of S8 at **three call
sites / action ids**:

| Action id | Triggering invocation |
|---|---|
| `overwrite-update` | `--update --overwrite` |
| `overwrite` | fresh generate with `--overwrite` |
| `restore-backup` | `--restore-backup` |

The gate **blocks** any of these unless a matching **PASS** / **verified
CONDITIONAL PASS** clearance or a **valid signed waiver** exists — the concrete
expression of C-5. Logic: `agentteams/cli/security_gate.py:24-37,96-214`; call
sites: `agentteams/cli/generate.py:605-615,1060-1072` and
`agentteams/cli/standalone_modes.py:62-65`.

**Which knob avoids the gate: use `--merge`.** For `overwrite-update` the refusal
tips you to `--update --merge`, which does not destructively overwrite and so
needs no clearance at all.

**The `--migrate` exemption is a controlled parameter, not an ambient switch.** A
`--migrate` overwrite is exempt **only via an explicit in-process parameter
threaded through the call chain** — never ambient module state (a public
off-switch "is an off-switch, not a control"). `--migrate` supplies its own
rollback: a pre-fencing snapshot tag plus `--revert-migration`.

**Honest ceiling.** Fail-closed and deterministic, but it guards **only the four
CLI entry points** it wraps — agent-initiated `Bash`/`Write` never reach it
(that's the hook's job, S19). It also cannot judge whether a clearance *should*
have been granted; it enforces that one exists, is unspent, and isn't overridden
by a HALT.

## The intelligence-freshness gate  ✅ {#S12}

Prints `[SEC-GATE/INTEL-FRESHNESS]` and blocks a **whole generation run** when
the threat-intel snapshot is **stale**. Staleness is all-or-nothing: age >
**24h TTL**, a future/unparseable timestamp, an explicit `stale` status, or
stale-data markers. Gate: `agentteams/cli/security_gate.py:262-427`, wired at
`agentteams/cli/generate.py:466-471`.

**Payload-digest bind.** If `SECURITY_DATA_PAYLOAD_DIGEST` is present it must
equal the SHA-256 of the six intel-bearing placeholders
(`agentteams/security_refs.py:835-916`). So **relabelling a stale snapshot's
timestamp to "now" no longer passes** — reproducing the digest requires
regenerating the placeholders, i.e. actually having fresh data. Freshness is
bound to *content*, not a timestamp field.

**Which knob clears it.** A signed waiver with action `security-intel-freshness`
(for air-gapped/offline runs). The refusal names a **blast radius** — how many
intel-bearing placeholders are in play. `--security-offline` uses cached intel
but **does not apply to cross-framework operations** (bridge/convert/interop),
which still require fresh data.

**Honest ceiling.** Guarantees a generation cannot silently ship stale intel, and
the digest bind prevents faking freshness. It does **not** vouch that the *feed
content* is accurate or complete — it enforces recency and content-timestamp
consistency, not correctness. An offline waiver is an explicit, signed acceptance
that intel is knowingly stale for that run.

## Shrink-policy: protecting enriched fenced content  ✅ {#S13}

`--shrink-policy {preserve,warn,halt,allow}` protects operator-**enriched**
content inside AGENTTEAMS fences from silent overwrite on `--update --merge`
(`agentteams/cli/parser.py:725-739`). `_detect_fence_shrink` flags a regenerated
body that is **<50% of the existing length**, drops **≥3 list items**, or
**loses concrete file-paths/backticked identifiers** (`agentteams/fences.py:158-268`).

| Level | Behaviour on a detected shrink |
|---|---|
| **`preserve`** (default) | keeps the enriched body + writes a notice |
| **`warn`** | writes the smaller body, saves a `.lost.<sid>.md` recovery sidecar |
| **`halt`** | refuses the whole-file write |
| **`allow`** | writes the smaller body silently |

Dispatch: `agentteams/emit.py:302-334,685-717`.

**Security override.** Template-authoritative fences (`invariant_core`,
`security_authority`, `security_rules_invariant`, `security_verdict_contract`),
brief-derived fences, and whole constitutional files **ignore `preserve`** — the
template body always wins (`agentteams/fences.py:271-300,351-403`). This stops an
attacker pinning a *weakened* security region by appending padding tokens so the
correct regenerated body looks like a "shrink." A **security fence rename** is
detected and **refuses the merge outright**.

**Honest ceiling — what it costs.** `preserve` protects enrichment from
accidental loss; the override protects the enforcement plane from a shrink-based
pin. But it governs only content *inside* fences on a merge path — it is not a
general tamper detector and presupposes fence boundaries are intact (broader
tamper is S22/S19). The default is safe; **`allow` writes silently — choose it
deliberately**.

## Bridge-refresh safety  ✅ *(policy)* / ⚙ *(operator Pre-Flight)* {#S14}

`--bridge-refresh` **unconditionally overwrites** target entry files
(`CLAUDE.md`, `.claude/*`, goose `AGENTS.md`/`.goosehints`). Origin: a
**2026-05-27 incident** where a refresh silently replaced user content,
recoverable only because the files were git-tracked
(`references/bridge-refresh-safety.md:5-95`).

**Choose the right verb:**

| Command | Behaviour | Safe on a target with user content? |
|---|---|---|
| `--bridge-check` | inspects only | always safe |
| `--bridge-merge` | re-renders only `AGENTTEAMS-BRIDGE`-fenced regions; skips unfenced files | **correct default** |
| `--bridge-from` (bare) | creates missing entry files | no — neither check nor merge |
| `--bridge-refresh` | unconditionally overwrites entry files | no — destructive |

**Four-check Pre-Flight §II (⚙ operator procedure)** before any
`--bridge-refresh` — all four must pass
(`references/bridge-refresh-safety.md:219-245`):

1. inventory the present entry files;
2. each carries a bridge fence;
3. the working tree is clean for those files;
4. each is git-tracked.

Any failure ⇒ use `--bridge-merge`. Mirrored as orchestrator **Rule 14**
(`agentteams/templates/universal/orchestrator.template.md:177`).

**Privilege scoping is an explicit non-goal of bridging.** A bridge carries **no**
sandbox block, capability grant, or privilege profile. A bridged target that
needs confinement must set its own profile and regenerate natively.

**Honest ceiling.** The Pre-Flight is an **operator procedure (⚙)**, not a code
gate: `--bridge-refresh` will still overwrite if invoked. Recoverability from the
2026-05-27 incident depended on git-tracking — check 4 exists because an untracked
overwrite is unrecoverable. The policy makes the destructive path visible and
procedurally guarded; it does not make `--bridge-refresh` non-destructive.

---

**Sources for Part IV.**
`agentteams/cli/security_gate.py:24-37,96-214,262-427`;
`agentteams/cli/generate.py:466-471,605-615,1060-1072`;
`agentteams/cli/standalone_modes.py:62-65`;
`agentteams/security_refs.py:835-916`;
`agentteams/cli/parser.py:725-739`;
`agentteams/emit.py:302-334,685-717`;
`agentteams/fences.py:158-268,271-300,351-403`;
`references/bridge-refresh-safety.md:5-95,219-245`;
`agentteams/templates/universal/orchestrator.template.md:177`.
