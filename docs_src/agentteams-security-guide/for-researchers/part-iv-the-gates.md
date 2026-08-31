# Part IV — The gates

The gates are the deterministic, fail-closed checks at the CLI entry points — the
*code* half of enforcement (S2), distinct from the runtime PreToolUse hook (S19)
that catches agent-initiated tool calls. Each prints a gate-first `[SEC-GATE/…]`
code so an operator sees exactly which check fired. The reviewer's takeaway for
the whole Part: these gates are genuine deterministic controls, but they guard
**only the CLI entry points they wrap** — agent-initiated actions are the hook's
surface, not theirs.

| SEC-GATE family | Fires on | Cleared by |
|---|---|---|
| `[SEC-GATE/DESTRUCTIVE:*]` (S11) | Destructive writes: overwrite-update, overwrite, restore-backup | PASS / verified CONDITIONAL PASS clearance, or valid signed waiver |
| `[SEC-GATE/INTEL-FRESHNESS]` (S12) | Stale threat-intel snapshot on a generation run | Signed waiver, action `security-intel-freshness` |

## The destructive-operation gate  ✅ {#S11}

**The adversary:** an agent or operator invoking a destructive overwrite without
a recorded decision that it is acceptable. **The control:** the gate wraps the
clearance engine of S8 at three call sites — `overwrite-update`
(`--update --overwrite`), `overwrite` (fresh generate with `--overwrite`), and
`restore-backup` — and **blocks** any of them unless a matching PASS or verified
CONDITIONAL PASS clearance, or a valid signed waiver, exists. That is C-5
("clearance precedes destruction") made executable. For `overwrite-update`, the
refusal tips the operator toward `--merge`, which needs no clearance because it
does not destructively overwrite.

The one exemption is disciplined: a `--migrate` overwrite is exempt **only via an
explicit in-process parameter threaded through the call chain — never via ambient
module state.** The module treats a public off-switch as "an off-switch, not a
control," and `--migrate` supplies its own rollback (a pre-fencing snapshot tag
plus `--revert-migration`).

**Honest ceiling.** The gate is fail-closed and deterministic, but it guards
**only the four CLI entry points** it wraps — agent-initiated `Bash`/`Write` never
reach it (that is S19). It also cannot judge whether a clearance *should* have
been granted; it enforces that one *exists*, is unspent, and is not overridden by
a HALT. The soundness of the clearance rests on the sentinel's judgment (S8).

## The intelligence-freshness gate  ✅ {#S12}

**The adversary:** a generation run that silently ships stale threat
intelligence — including one that tries to *look* fresh by editing a timestamp.
The gate blocks a whole generation run when the threat-intel snapshot is stale
(all-or-nothing: age > **24h TTL**, a future/unparseable timestamp, an explicit
`stale` status, or stale-data markers).

The interesting control for a reviewer is the **payload-digest bind**: if the
digest field is present, it must equal the SHA-256 of the six intel-bearing
placeholders. This closes the obvious evasion — **relabelling a stale snapshot's
timestamp to "now" no longer passes**, because reproducing the digest requires
regenerating the placeholders, i.e. actually having fresh data. Freshness is
bound to *content*, not to a self-asserted timestamp. The gate is cleared by a
signed waiver (action `security-intel-freshness`) for air-gapped/offline runs,
and its refusal names a **blast radius** — how many intel-bearing placeholders are
in play.

**Honest ceiling.** The gate guarantees a generation cannot silently ship stale
intel and cannot fake freshness with a timestamp edit. It does **not** vouch for
the *correctness or completeness* of the feed content itself — only recency and
content-timestamp consistency. An offline waiver is an explicit, signed
acceptance that intel is knowingly stale for that run.

## Shrink-policy: protecting enriched fenced content  ✅ {#S13}

**The adversary here is subtle**, which is why it matters. `--shrink-policy`
protects operator-*enriched* content inside AGENTTEAMS fences from being silently
overwritten on `--update --merge`. The detector flags a regenerated body that is
<50% of the existing length, drops ≥3 list items, or loses concrete file-paths or
backticked identifiers. Levels: `preserve` (default — keep the enriched body +
notice), `warn` (write the smaller body but save a `.lost.<sid>.md` recovery
sidecar), `halt` (refuse the whole-file write), `allow` (write silently).

The security-relevant twist is the **override**: template-authoritative fences
(`invariant_core`, `security_authority`, `security_rules_invariant`,
`security_verdict_contract`), brief-derived fences, and whole constitutional
files **ignore `preserve` — the template body always wins.** This exists to
defeat a specific attack: an adversary padding a weakened security region with
tokens so the regenerated (correct) body looks like a "shrink" and gets
preserved. For these fences, "shrinking" back to canonical is exactly right. A
**security-fence rename** is separately detected and **refuses the merge
outright**, so the override cannot be dodged by relabelling.

**Honest ceiling.** `preserve` protects operator enrichment from accidental loss;
the override protects the enforcement plane from a shrink-based pin. But the
policy governs only content *inside* fences on a merge path — it is **not a
general tamper detector**, and it presupposes the fence boundaries are intact
(broader tamper is S22/S19's concern). The default is the safe one; `allow`
writes silently and should be chosen deliberately.

## Bridge-refresh safety  ✅ *(policy)* / ⚙ *(operator Pre-Flight)* {#S14}

`--bridge-refresh` **unconditionally overwrites** target entry files
(`CLAUDE.md`, `.claude/*`, goose `AGENTS.md`/`.goosehints`). Its origin is a
**2026-05-27 incident** where a refresh silently replaced user content,
recoverable only because the files were git-tracked. The destructiveness is the
reason the safety policy exists.

**Choose the right verb:** `--bridge-check` inspects only (always safe);
`--bridge-merge` re-renders only `AGENTTEAMS-BRIDGE`-fenced regions and skips
unfenced files (**the correct default** whenever the target has user content); a
bare `--bridge-from` creates missing entry files (neither check nor merge);
`--bridge-refresh` overwrites (destructive). Before any `--bridge-refresh`, a
four-check Pre-Flight §II must all pass (inventory present entry files; each
carries a bridge fence; working tree clean for them; each git-tracked); any
failure ⇒ use `--bridge-merge`. This is mirrored as orchestrator Rule 14.

A boundary a reviewer should note: **privilege scoping is an explicit non-goal of
bridging.** A bridge carries **no** sandbox block, capability grant, or privilege
profile; a bridged target needing confinement sets its own profile and
regenerates natively.

**Honest ceiling.** The Pre-Flight is an **operator procedure (⚙), not a code
gate** — `--bridge-refresh` will still overwrite if invoked, so safety rests on
following the checks and preferring `--bridge-merge`. The recoverability that
saved the 2026-05-27 incident depended on git-tracking; check 4 exists precisely
because an untracked overwrite is unrecoverable. The policy makes the destructive
path visible and procedurally guarded; it does not make `--bridge-refresh`
non-destructive. Full command table and line numbers: Edition R, S14.

---

**Sources for Part IV.** `agentteams/cli/security_gate.py`;
`agentteams/cli/generate.py`; `agentteams/cli/standalone_modes.py`;
`agentteams/security_refs.py`; `agentteams/cli/parser.py`; `agentteams/emit.py`;
`agentteams/fences.py`; `references/bridge-refresh-safety.md`;
`agentteams/templates/universal/orchestrator.template.md`. Line-precise
provenance: `SOURCES.md` (S27).
