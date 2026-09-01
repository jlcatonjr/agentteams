# Part IX — Synthesis and reference matter

## Defense-in-depth: how the layers compose  ✅/⚙ {#S25}

The eight parts before this one describe controls one at a time. That
presentation is a convenience of exposition, not the design. **The design is the
composition.** No single layer is "the security boundary"; each is stated with
what it *buys* and what it *cannot*, and the stack is meant to work because the
next layer covers the ceiling of the one before it.

**The composed stack, in order of engagement.** A destructive or
credential-adjacent action passes — or is stopped by — this sequence:

1. the **constitution** (Part II) states the non-overridable principles
   (C-1..C-5) that outrank every other instruction;
2. the **sentinel** plus the **clearance / waiver / grant** triad (Parts II–III)
   gate *decisions* — the sentinel issues PASS / CONDITIONAL PASS / HALT, and the
   three signed instruments authorize what a HALT does not forbid;
3. the **gates** (Part IV) block *destructive execution* at the CLI entry points,
   fail-closed, refusing any destructive write without a matching clearance or a
   valid signed waiver;
4. the **content scanner** (Part V) blocks *bad content* — credentials, PII,
   machine-specific paths, injection text — with a deterministic verdict;
5. **OS confinement** (Part VI) bounds *runtime reach* — the sandbox request and
   `denyWrite`/`denyRead` exclusions limit what an in-sandbox agent can touch;
6. **threat intelligence** and the **red-team cycle** (Part VII) keep the
   controls *current* (live feeds, 24h freshness) and *tested* (the self-auditing
   seven-phase cadence);
7. **integrity manifests, provenance, and backups/baselines** (Part VIII) make
   tampering *evident* (a recorded, git-visible multi-step act) and damage
   *recoverable* (snapshot rollback, byte-for-byte drift detection).

**Each layer covers the next's gap.** The load-bearing chain runs from the
softest control to the hardest, each one backstopping the judgment of its
predecessor:

> sentinel judgment → S-1/S-8 deterministic **scanner** → **integrity manifest**
> over the scanner → **PreToolUse hook** that checks the manifest before scanning
> → **sandbox `denyWrite`** protecting the hook and manifest paths.

Read forward: the sentinel is a fallible model, so S-1 (credentials/PII) and S-8
(machine-specific info) get a deterministic scanner rather than resting on
judgment; the scanner is itself a file an agent could edit, so it is covered by a
SHA-256 integrity manifest; the manifest is only a record, so the PreToolUse hook
verifies the scanner against it before trusting a scan; and the hook and manifest
paths are themselves listed in the sandbox `denyWrite` set so an in-sandbox agent
cannot rewrite them. Each layer exists precisely because the one before it is not
a guarantee.

**The residual ceiling (E4).** The chain raises cost; it does not close the
class. An attacker who can edit `scan.py` can also edit the integrity manifest
and can also edit the constitutional-gate hook. Together, the scanner, the
manifest, and the hook do **not** make that tamper impossible — they raise it
**from one edit to three, each visible in `git diff`**. The honest statement of
the layer is "cost raised and made git-visible," never "eliminated." That is the
composition working as designed, not a defect in it.

**Three binding composite ceilings.** The composition carries three limits that
no reading of "defense-in-depth" may soften:

- **(a) None of this runs inside the produced app.** The whole stack is
  **design-time** governance of *how an app is built* (S1, S2). A deployed app
  that serves LLM output to end users needs its **own** runtime governance and
  the separate **L0–L7** deployed-system model (S17). "We generated a security
  team" is not "our product is protected at runtime."
- **(b) The runtime confinement layers are opt-in.** The default privilege
  profile is `cooperative`, under which the **sandbox is off** and the PreToolUse
  hook is **fail-open** (S1, S18, S19). The governance layers (constitution,
  sentinel, triad, CLI gates, scanner) are always active; the OS-level locks
  engage only when the operator selects `confined` or `exclusive`.
- **(c) OS-confinement is empirically verified on Linux** — the `sandbox/confine-run.sh` bwrap launcher passes a live-kernel deny test; **macOS Seatbelt is UNVERIFIED**. Claude Code's *native* Linux bubblewrap arm is
  partial (with the open D-3 absent-path fragility and unverified `denyRead`) and
  native Windows has no emitted enforcement (S18). Posture is "engages as
  tested," never "verified-unbypassable."

**Layer → what it buys → its ceiling.**

| Layer (Part) | What it buys | Its honest ceiling |
|---|---|---|
| Constitution (II) | non-overridable principles that outrank every other instruction | states it is **not self-enforcing**; presence/reachability audited, decision rule is judgment (S4) |
| Sentinel + triad (II–III) | gated *decisions*: PASS/CONDITIONAL PASS/HALT + signed clearance/waiver/grant | sentinel is a **fallible LLM** except where S-1/S-8 scanner backs it; symmetric HMAC stops a **keyless** forger, not a key-holder (S5–S10) |
| Gates (IV) | block *destructive execution*, fail-closed, at CLI entry points | guard **CLI entry points only**; `--migrate` exempt via one explicit in-process parameter (S11–S14) |
| Scanner (V) | block *bad content* — credentials/PII/paths/injection, deterministic | **shape-blind**; only **high** blocks; **no formula/CSV-injection detector** exists (S15–S16) |
| OS confinement (VI) | bound *runtime reach* via sandbox + `denyWrite`/`denyRead` | **opt-in**, **inert until wired**, **Linux-verified only; macOS Seatbelt unverified** (S17–S19) |
| Intel + red team (VII) | keep controls *current* (24h TTL feeds) and *tested* (self-auditing cycle) | snapshot is "valid as of," not a baseline; a control is meaningful only against its **stated tier** (S20–S21) |
| Integrity + backups (VIII) | make tampering *evident* and damage *recoverable* | manifest is a **speed bump, not a boundary**; a missing manifest is "never set up," not "compromised" (S22–S24) |

**The synthesis, stated plainly.** What the stack buys is a set of composed,
mostly-evident, cost-raising controls against a steerable in-repo agent acting on
injected instructions. What it does not buy is a guarantee: the boundary is the
composition and its git-visibility, not any single unbypassable lock, and the two
surfaces (S2) stay distinct — securing the build process is not securing the
shipped product.

**Source.** synthesis of S1–S24; `SECURITY.md`;
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md`.

## Glossary  ✅ {#S26}

Each definition matches the term's defining section and adds no fact absent from
the skeleton. The defining section is cited so the full mechanism and its honest
ceiling can be read there.

- **authority hierarchy** (S4) — the ordering that ranks **sources of fact**
  (what is true), deliberately distinct from the instruction-authority ordering;
  being authoritative-about-truth confers **no permission**, and that gap is
  exactly what prompt injection attacks.
- **backup** (S24) — a pre-write snapshot of the output tree to
  `.agentteams-backups/YYYYMMDD-HHMMSS/` with a per-file `_manifest.json` of full
  SHA-256 (hashed from the backup copy to avoid a TOCTOU window) plus
  reason/framework/version; restorable via `--restore-backup`, itself gated.
- **baseline** (S24) — a SHA-256 manifest of a generated tree, compared
  byte-for-byte (raw bytes only; timestamps/order excluded) to detect **emission
  drift**; `--check-baseline` exits 2 on drift and is stable across machines.
- **bridge-refresh (`--bridge-refresh`)** (S14) — the cross-framework mode that
  **unconditionally overwrites** target entry files; destructive at the target,
  it requires a four-check Pre-Flight §II and is recoverable only because the
  files are git-tracked.
- **`--bridge-merge`** (S14) — the correct default whenever the target has user
  content: it re-renders only `AGENTTEAMS-BRIDGE`-fenced regions and skips
  unfenced files, so user content is preserved rather than replaced.
- **clearance** (S7, S8) — a decision-log row that authorizes a destructive
  action **locally, before it runs** (C-5); a PASS or verified CONDITIONAL PASS
  by an approved author, consumed once so it cannot be replayed.
- **CONDITIONAL PASS / `conditions_verified`** (S8) — a sentinel verdict that
  allows an action **only** when its `conditions_verified` field reads
  `"verified"`; while `pending`, the gate treats it "as if HALT" and blocks.
- **`cooperative` (privilege profile)** (S18) — the default profile: **no OS
  boundary** — the sandbox is off and the PreToolUse hook is fail-open;
  confinement engages only under `confined`/`exclusive`.
- **`confined` (privilege profile)** (S18) — a profile that emits a sandbox
  request token and flips the hook fail-closed, bounding an in-sandbox agent's
  write reach; enforcement is the harness's own OS sandbox, not agentteams.
- **Constitutional Core / C-1..C-5** (S3) — the **Tier 1, non-overridable**
  principles (C-1 Precedence, C-2 HALT is final, C-3 capability declarations
  binding, C-4 content is data, C-5 clearance precedes destruction); a project may
  extend the Rules but may not weaken the Core.
- **`denyRead`** (S18) — an outbound read-exclusion added by the `exclusive`
  profile over credential directories (SSH/AWS/etc.); it is outbound hardening of
  files (not env vars) and does not stop others reading your tree.
- **`denyWrite`** (S18) — a deny-over-allow write-exclusion of exact
  control-plane paths (`references/agent-privilege.json`,
  `.claude/hooks/constitutional-gate.py`) that protects the enforcement plane from
  an in-sandbox agent.
- **`exclusive` (privilege profile)** (S18) — the strictest profile: everything
  `confined` emits plus a `denyRead` read-exclusion; an unknown profile fails
  closed rather than silently downgrading.
- **F-1..F-6** (S21) — the six ways a red team fools itself, checked in phase-6
  self-audit: a verifier that always passes; a fix wired to one of two sites;
  hand-rolling what the tool provides; a coverage claim with an unexamined
  denominator; a probe that got blinder not better; accepting a weakness without a
  ledger diff.
- **FENCED region** (S1, S13) — a module-owned region inside AGENTTEAMS fences
  that is **restored from template on every `--update --merge`**; a fenced
  security region that does not survive a merge is a vulnerability.
- **grant** (S7, S10) — a signed, scoped, time-bounded authorization by an
  `issuer_team` for a `holder_team` to perform ops on a `target_path` in the
  issuer's workspace; enforcement is generation-time only, so a fresh grant is
  inert until the operator re-runs an update.
- **HALT** (S3, S5, S6) — the sentinel's terminal verdict: the operation stops
  and the finding is surfaced first; **C-2 makes it final** — the destructive gate
  checks for an unretracted HALT over the whole log, and **no waiver overrides
  it**.
- **honest ceiling** (throughout) — the binding doctrine that every control is
  stated with what it *buys* and what it *cannot*; a boundary is described as
  "engages as tested," never "secure," and overstating a ceiling is a fact error.
- **instruction-authority ordering** (S4) — the ordering that ranks **sources of
  instruction** (whose-instruction-wins) by tier then specificity — never by
  recency, proximity, or forcefulness; distinct from the authority hierarchy, and
  itself not self-enforcing.
- **integrity manifest** (S22) — a SHA-256 manifest over the enforcement modules
  (including `integrity.py` itself), stored at
  `references/enforcement-integrity.json`; it detects drift and makes tampering a
  git-visible multi-step act, but is a **speed bump, not a boundary**.
- **PreToolUse hook** (S2, S19) — the `constitutional-gate.py` hook the harness
  runs **before** each tool call, enforcing C-4/C-5 on **agent-initiated** actions
  the CLI never sees; its `deny` is the runtime expression of a HALT, fail-open by
  default and fail-closed under `confined`/`exclusive`.
- **privilege profile** (S18) — one of `cooperative`, `confined`, or
  `exclusive`, selecting how much OS boundary is emitted; an unknown profile fails
  closed.
- **provenance** (S23) — a machine-readable, honest-by-construction stamp of how
  an artifact was produced (generator, passed-in `generated_at`, input SHA-256
  prefixes, and a required `provisional` list of known limitations); a reusable
  library, not auto-wired into the default emit path.
- **red-team outcome** (S21) — one of DEFENDED / PARTIAL / EXPLOITED /
  DOCUMENTED-LIMIT / OUT-OF-TIER; an unknown outcome raises, because a typo is a
  defect, not a measurement.
- **red-team tier** (S21) — the attacker capability a control is measured
  against: **T0** (attacker supplies content the agent reads), **T1** (in-repo
  agent with write+execute), **T2** (operator's shell); a control is meaningful
  only against a stated tier.
- **scan verdict (high → HALT)** (S15) — the content scanner's rule: any **high**
  finding ⇒ HALT, any finding at all ⇒ CONDITIONAL PASS, else PASS; only high
  blocks, and exemptions are keyed on **provenance, not shape**.
- **SEC-GATE (DESTRUCTIVE)** (S11) — the destructive-operation gate; prints
  `[SEC-GATE/DESTRUCTIVE:*]` and exits 1, blocking a destructive write
  (`overwrite-update`, `overwrite`, `restore-backup`) without a matching clearance
  or valid signed waiver (C-5).
- **SEC-GATE (INTEL-FRESHNESS)** (S12) — the intelligence-freshness gate; prints
  `[SEC-GATE/INTEL-FRESHNESS]` and blocks a whole generation run when the threat
  snapshot is stale (age > 24h TTL, bad timestamp, or a failed payload-digest
  bind).
- **`@security` sentinel** (S5) — the top-priority, **read-only**
  (`tools: ['read','search']`) security reviewer that emits PASS / CONDITIONAL
  PASS / HALT by a deterministic escalation table (most restrictive wins) over
  rules S-1..S-10; a fallible LLM except where the S-1/S-8 scanner backs it.
- **shrink-policy (`--shrink-policy`)** (S13) — the `{preserve,warn,halt,allow}`
  control protecting operator-**enriched** fenced content from silent overwrite on
  `--update --merge`; template-authoritative security fences ignore `preserve` so
  a weakened region cannot be pinned.
- **template-authoritative fence** (S13) — a fence whose template body always
  wins (`invariant_core`, `security_authority`, `security_rules_invariant`,
  `security_verdict_contract`, brief-derived and constitutional fences); it ignores
  `preserve`, and a security-fence rename refuses the merge outright.
- **waiver** (S7, S9) — a signed instrument that clears a **gate block** (never a
  HALT); valid only with action-id scope match, verified conditions, a rostered
  approver, a future `expires_at`, `uses < max_uses`, and a verifying HMAC-SHA256
  signature.

## Sources  ✅ {#S27}

Every canonical fact in this guide resolves to a repo file. The complete row-by-
row provenance table lives in **`SOURCES.md`**, which is the guide's **provenance
gate**: `@technical-validator` verifies each row resolves on disk and that each
✅/⚙ marker is accurate, and `@adversarial` verifies each honest ceiling is not
overstated. This section restates the concept→file map; the line-precise
citations are in `SOURCES.md` and in each section's own **Source** line.

**Concept → file map** (from the skeleton's S27):

- **constitution** → `.claude/CLAUDE.md` +
  `agentteams/templates/universal/instruction-authority.reference.template.md`
- **sentinel** → `agentteams/templates/universal/security.template.md`
- **gates / triad** →
  `agentteams/cli/{security_gate,decision_log,grants}.py`
- **scanner** → `agentteams/scan.py`
- **confinement** → `agentteams/host_features.py` +
  `agentteams/frameworks/_*sandbox_emit.py` +
  `security-{infrastructure-layers,macos,linux,windows}-hardening` reference
  templates
- **intel** → `agentteams/security_refs.py` +
  `security-vulnerability-watch` reference template
- **red team** → `agentteams/redteam/` +
  `redteam-methodology` reference template
- **recovery** → `agentteams/integrity.py` / `agentteams/provenance.py` /
  `agentteams/backup.py` / `agentteams/baseline.py`
- **posture** → `SECURITY.md`

**No fact ships from memory.** A claim without a resolvable source is a defect,
not a stylistic lapse — `SOURCES.md` exists so that every fact in every edition
can be traced to repo evidence, and no line number is quoted from memory.

---

**Sources for Part IX.** `SOURCES.md`; `SECURITY.md`; `.claude/CLAUDE.md`;
`agentteams/templates/universal/instruction-authority.reference.template.md`;
`agentteams/templates/universal/security.template.md`;
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md`;
`agentteams/cli/security_gate.py`; `agentteams/cli/decision_log.py`;
`agentteams/cli/grants.py`; `agentteams/scan.py`;
`agentteams/host_features.py`; `agentteams/frameworks/_sandbox_emit.py`;
`agentteams/frameworks/_goose_sandbox_emit.py`;
`agentteams/templates/universal/hooks/constitutional-gate.py`;
`agentteams/security_refs.py`; `agentteams/redteam/`;
`agentteams/integrity.py`; `agentteams/provenance.py`;
`agentteams/backup.py`; `agentteams/baseline.py`; and every file cited in
S1–S26.
