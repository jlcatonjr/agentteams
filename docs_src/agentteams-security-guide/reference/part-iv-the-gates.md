# Part IV — The gates

The gates are the deterministic, fail-closed checks at CLI entry points — the code
half of enforcement (S2), distinct from the runtime PreToolUse hook (S19) that catches
agent-initiated tool calls. Each gate prints a **gate-first `[SEC-GATE/…]` code** so an
operator can see exactly which check fired.

| SEC-GATE family | Fires on | Cleared by |
|---|---|---|
| `[SEC-GATE/DESTRUCTIVE:*]` (S11) | Destructive writes: overwrite-update, overwrite, restore-backup | PASS / verified CONDITIONAL PASS clearance, or valid signed waiver |
| `[SEC-GATE/INTEL-FRESHNESS]` (S12) | Stale threat-intel snapshot on a generation run | Signed waiver, action `security-intel-freshness` |

## The destructive-operation gate  ✅ {#S11}

The destructive gate prints a gate-first code `[SEC-GATE/DESTRUCTIVE:*]` — the `*`
naming which destructive action fired — followed by `blocked: …` and exit 1. It wraps
the clearance engine of S8 at **three call sites / action ids**:

| Action id | Triggering invocation |
|---|---|
| `overwrite-update` | `--update --overwrite` |
| `overwrite` | Fresh generate with `--overwrite` |
| `restore-backup` | `--restore-backup` |

The gate **blocks** any of these destructive writes unless a matching **PASS** or
**verified CONDITIONAL PASS** clearance, or a **valid signed waiver**, exists — the
concrete expression of C-5 ("clearance precedes destruction"). The gate logic is in
`agentteams/cli/security_gate.py:24-37,96-214`; the call sites are wired at
`agentteams/cli/generate.py:605-615,1060-1072` and
`agentteams/cli/standalone_modes.py:62-65`. For `overwrite-update`, the refusal tips
the operator to use `--merge`, which avoids needing a clearance at all (a merge does
not destructively overwrite).

**The `--migrate` exemption is a controlled parameter, not an ambient switch.** A
`--migrate` overwrite is exempt from the gate **only via an explicit in-process
parameter threaded through the call chain** — never via ambient module state. The
module treats a public off-switch as "an off-switch, not a control": the exemption
cannot be flipped from outside the call path. `--migrate` supplies its own rollback —
a pre-fencing snapshot tag plus `--revert-migration`.

**Honest ceiling.** The gate is fail-closed and deterministic — every unresolved path
denies — but it guards **only the four CLI entry points** it wraps. Agent-initiated
`Bash`/`Write` calls never reach it; those are the PreToolUse hook's job (S19).
Collapsing the two surfaces is a fact error. The gate also cannot judge whether a
clearance *should* have been granted — it enforces that one exists, is unspent, and is
not overridden by a HALT; the soundness of the clearance itself rests on the sentinel's
judgment (S8).

## The intelligence-freshness gate  ✅ {#S12}

The freshness gate prints `[SEC-GATE/INTEL-FRESHNESS]` and blocks a **whole generation
run** when the security threat-intelligence snapshot is **stale**. Staleness is
all-or-nothing and triggers on any of: age > **24h TTL**, a future or unparseable
timestamp, an explicit `stale` status, or stale-data markers. The gate is at
`agentteams/cli/security_gate.py:262-427`, wired into generation at
`agentteams/cli/generate.py:466-471`.

**Payload-digest bind.** If `SECURITY_DATA_PAYLOAD_DIGEST` is present, it must equal
the SHA-256 of the six intel-bearing placeholders
(`agentteams/security_refs.py:835-916`). This closes the obvious evasion:
**relabelling a stale snapshot's timestamp to "now" no longer passes**, because the
digest would also have to be regenerated — which requires actually having fresh data.
Freshness is bound to *content*, not just to a timestamp field.

**Clearing it.** The gate is cleared by a signed waiver with action
`security-intel-freshness`, intended for air-gapped or offline runs. The refusal names
a **blast radius** — how many intel-bearing placeholders are in play — so the operator
sees the scope of what a waiver would cover. `--security-offline` uses cached intel but
**does not apply to cross-framework operations** (bridge/convert/interop); those still
require fresh data.

**Honest ceiling.** The gate guarantees a generation cannot silently ship stale threat
intelligence, and the digest bind prevents faking freshness with a timestamp edit. It
does **not** guarantee the *feed content itself* is accurate or complete — it enforces
recency and content-timestamp consistency, not correctness of the upstream feeds. An
offline waiver is an explicit, signed acceptance that intel is knowingly stale for that
run.

## Shrink-policy: protecting enriched fenced content  ✅ {#S13}

`--shrink-policy {preserve,warn,halt,allow}` protects operator-**enriched** content
inside AGENTTEAMS fences from being silently overwritten on `--update --merge`
(`agentteams/cli/parser.py:725-739`). The detector `_detect_fence_shrink` flags a
regenerated body when it is **<50% of the existing length**, drops **≥3 list items**,
or **loses concrete file-paths or backticked identifiers**
(`agentteams/fences.py:158-268`).

| Level | Behaviour on a detected shrink |
|---|---|
| **`preserve`** (default) | Keeps the enriched body + writes a notice |
| **`warn`** | Writes the smaller body, but saves a `.lost.<sid>.md` recovery sidecar |
| **`halt`** | Refuses the whole-file write |
| **`allow`** | Writes the smaller body silently |

The policy dispatch is in `agentteams/emit.py:302-334,685-717`.

**Security override.** Template-authoritative fences —
`invariant_core`, `security_authority`, `security_rules_invariant`,
`security_verdict_contract` — along with brief-derived fences and whole constitutional
files, **ignore `preserve`**: the template body always wins
(`agentteams/fences.py:271-300,351-403`). This is deliberate — it stops an attacker
from *pinning a weakened security region* by appending padding tokens so the regenerated
(correct) body looks like a "shrink" and gets preserved. For these fences, "shrinking"
back to the canonical template is exactly what should happen. Separately, a **security
fence rename** is detected and **refuses the merge outright** — you cannot slip past the
override by relabelling the fence.

**Honest ceiling.** `preserve` protects operator enrichment from accidental loss; the
security override protects the enforcement plane from a shrink-based pin. But the policy
governs only content *inside* fences on a merge path — it is not a general tamper
detector, and it presupposes the fence boundaries themselves are intact (a broader
tamper is the integrity manifest's and hook's concern, S22/S19). The default is the
safe one (`preserve`); `allow` writes silently and should be chosen deliberately.

## Bridge-refresh safety  ✅ *(policy)* / ⚙ *(operator Pre-Flight)* {#S14}

`--bridge-refresh` **unconditionally overwrites** target entry files — `CLAUDE.md`,
`.claude/*`, and the goose `AGENTS.md`/`.goosehints`. Its origin is a **2026-05-27
incident** where a refresh silently replaced user content, recoverable only because the
files happened to be git-tracked (`references/bridge-refresh-safety.md:5-95`). The
destructiveness is the whole point of the safety policy around it.

**Choose the right verb:**

| Command | Behaviour | Safe on a target with user content? |
|---|---|---|
| `--bridge-check` | Inspects only | Always safe |
| `--bridge-merge` | Re-renders only `AGENTTEAMS-BRIDGE`-fenced regions; skips unfenced files | **Correct default** |
| `--bridge-from` (bare) | Creates missing entry files | No — neither check nor merge |
| `--bridge-refresh` | Unconditionally overwrites entry files | No — destructive |

**`--bridge-merge` is the correct default** whenever the target already has user
content: it touches only fenced regions and leaves unfenced files alone. A bare
`--bridge-from` is neither safe-inspect nor safe-merge — it creates missing entry
files.

**Four-check Pre-Flight §II (⚙ operator procedure).** Before any `--bridge-refresh`,
all four must pass (`references/bridge-refresh-safety.md:219-245`):

1. Inventory the present entry files.
2. Each carries a bridge fence.
3. The working tree is clean for those files.
4. Each is git-tracked.

Any failure ⇒ use `--bridge-merge` instead. This procedure is mirrored as orchestrator
**Rule 14** (`agentteams/templates/universal/orchestrator.template.md:177`).

**Privilege scoping is an explicit non-goal of bridging.** A bridge carries **no**
sandbox block, capability grant, or privilege profile. A bridged target that needs
confinement must set its own profile and regenerate natively — bridging will not carry
one across.

**Honest ceiling.** The Pre-Flight is an **operator procedure (⚙)**, not a code gate:
`--bridge-refresh` will still overwrite if invoked, so the safety rests on following the
checks and preferring `--bridge-merge`. The recoverability that saved the 2026-05-27
incident depended on git-tracking — check 4 exists precisely because an untracked
overwrite is unrecoverable. The policy makes the destructive path visible and
procedurally guarded; it does not make `--bridge-refresh` non-destructive.

**Sources for Part IV.** `agentteams/cli/security_gate.py:24-37,96-214,262-427`;
`agentteams/cli/generate.py:466-471,605-615,1060-1072`;
`agentteams/cli/standalone_modes.py:62-65`;
`agentteams/security_refs.py:835-916`;
`agentteams/cli/parser.py:725-739`;
`agentteams/emit.py:302-334,685-717`;
`agentteams/fences.py:158-268,271-300,351-403`;
`references/bridge-refresh-safety.md:5-95,219-245`;
`agentteams/templates/universal/orchestrator.template.md:177`.
