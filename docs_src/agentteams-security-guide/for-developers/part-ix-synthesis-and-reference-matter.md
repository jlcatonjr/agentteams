# Part IX — Synthesis and reference matter

## Defense-in-depth: how the layers compose  ✅/⚙ {#S25}

The parts before this describe controls one at a time; that is exposition, not the
design. **The design is the composition** — no single layer is the boundary, and
each is stated with what it *buys* and what it *cannot*.

**The composed stack, in order of engagement.** A destructive or
credential-adjacent action passes — or is stopped by — this sequence:

1. the **constitution** (Part II) states the non-overridable C-1..C-5 principles;
2. the **sentinel** + **clearance/waiver/grant** triad (Parts II–III) gate
   *decisions* — PASS/CONDITIONAL PASS/HALT, plus signed instruments for what a
   HALT does not forbid;
3. the **gates** (Part IV) block *destructive execution* at the CLI entry points,
   fail-closed;
4. the **content scanner** (Part V) blocks *bad content* deterministically;
5. **OS confinement** (Part VI) bounds *runtime reach* via sandbox +
   `denyWrite`/`denyRead` (opt-in);
6. **threat intel + red team** (Part VII) keep the controls *current* (24h TTL)
   and *tested* (self-auditing cycle);
7. **integrity + provenance + backups/baselines** (Part VIII) make tampering
   *evident* and damage *recoverable*.

**Each layer covers the next's gap** — the load-bearing chain:

> sentinel judgment → S-1/S-8 deterministic **scanner** → **integrity manifest**
> over the scanner → **PreToolUse hook** that checks the manifest before scanning
> → **sandbox `denyWrite`** protecting the hook and manifest paths.

**The residual ceiling (E4).** An attacker who can edit `scan.py` can also edit
the manifest and the hook. Together these do **not** make that impossible — they
raise it **from one edit to three, each visible in `git diff`**. "Cost raised and
made git-visible," never "eliminated."

**Three binding composite ceilings — no reading of "defense-in-depth" softens
these:**

- **(a) None of this runs inside the produced app.** The whole stack is
  design-time governance of *how an app is built* (S1, S2). A deployed app needs
  its **own** runtime governance and the L0–L7 model (S17).
- **(b) Runtime confinement is opt-in.** Default profile `cooperative` — sandbox
  off, hook fail-open (S1, S18, S19). Governance layers are always active; OS-level
  locks engage only under `confined`/`exclusive`.
- **(c) OS-confinement is empirically verified on Linux** — the `sandbox/confine-run.sh` bwrap launcher passes a live-kernel deny test; **macOS Seatbelt is UNVERIFIED**. Claude Code's *native* Linux bubblewrap arm is
  partial (open D-3 fragility, unverified `denyRead`); native Windows has no
  emitted enforcement (S18).

**Layer → what it buys → its ceiling.**

| Layer (Part) | What it buys | Its honest ceiling |
|---|---|---|
| Constitution (II) | non-overridable principles | **not self-enforcing**; presence/reachability audited (S4) |
| Sentinel + triad (II–III) | gated decisions + signed instruments | **fallible LLM** except S-1/S-8; symmetric HMAC stops a **keyless** forger only (S5–S10) |
| Gates (IV) | block destructive execution, fail-closed | **CLI entry points only**; `--migrate` exempt via one explicit parameter (S11–S14) |
| Scanner (V) | block bad content deterministically | **shape-blind**; only **high** blocks; **no formula/CSV-injection detector** (S15–S16) |
| OS confinement (VI) | bound runtime reach | **opt-in**, **inert until wired**, **Linux-verified only; macOS Seatbelt unverified** (S17–S19) |
| Intel + red team (VII) | keep controls current + tested | snapshot is "valid as of," not a baseline; a control means nothing without its **stated tier** (S20–S21) |
| Integrity + backups (VIII) | make tampering evident + damage recoverable | manifest is a **speed bump, not a boundary**; missing manifest = "never set up" (S22–S24) |

**Stated plainly:** the stack buys composed, mostly-evident, cost-raising controls
against a steerable in-repo agent acting on injected instructions. It does not buy
a guarantee — the boundary is the composition and its git-visibility, and the two
surfaces (S2) stay distinct.

**Source.** synthesis of S1–S24; `SECURITY.md`;
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md`.

## Glossary  ✅ {#S26}

Each definition matches its defining section and adds no fact absent from the
skeleton.

- **authority hierarchy** (S4) — ranks **sources of fact** (what is true),
  distinct from the instruction-authority ordering; being authoritative-about-truth
  confers **no permission**.
- **backup** (S24) — a pre-write snapshot to `.agentteams-backups/…` with a
  per-file `_manifest.json` of full SHA-256 (hashed from the backup copy, TOCTOU-
  safe); restorable via `--restore-backup`, itself gated.
- **baseline** (S24) — a SHA-256 manifest of a generated tree, compared byte-for-
  byte (raw bytes only) to detect **emission drift**; `--check-baseline` exits 2 on
  drift.
- **bridge-refresh (`--bridge-refresh`)** (S14) — **unconditionally overwrites**
  target entry files; destructive, needs a four-check Pre-Flight §II.
- **`--bridge-merge`** (S14) — the correct default with user content: re-renders
  only `AGENTTEAMS-BRIDGE`-fenced regions, skips unfenced files.
- **clearance** (S7, S8) — a decision-log row authorizing a destructive action
  **locally, before it runs** (C-5); a PASS or verified CONDITIONAL PASS, consumed
  once (no replay).
- **CONDITIONAL PASS / `conditions_verified`** (S8) — a verdict that allows only
  when `conditions_verified == "verified"`; while `pending` the gate blocks "as if
  HALT."
- **`cooperative` (privilege profile)** (S18) — the default: **no OS boundary** —
  sandbox off, hook fail-open.
- **`confined` (privilege profile)** (S18) — emits a sandbox request token and
  flips the hook fail-closed; enforcement is the harness's OS sandbox.
- **Constitutional Core / C-1..C-5** (S3) — the **Tier 1, non-overridable**
  principles; a project may extend the Rules but not weaken the Core.
- **`denyRead`** (S18) — an outbound read-exclusion added by `exclusive` over
  credential dirs (files, not env vars); does not stop others reading your tree.
- **`denyWrite`** (S18) — a deny-over-allow write-exclusion of exact control-plane
  paths protecting the enforcement plane from an in-sandbox agent.
- **`exclusive` (privilege profile)** (S18) — strictest: everything `confined`
  emits plus `denyRead`; an unknown profile fails closed.
- **F-1..F-6** (S21) — the six ways a red team fools itself, checked in phase-6
  self-audit.
- **FENCED region** (S1, S13) — a module-owned region **restored from template on
  every `--update --merge`**; a security fence that does not survive a merge is a
  vulnerability.
- **grant** (S7, S10) — a signed, scoped, time-bounded cross-workspace write
  authorization; enforcement is generation-time only, so a fresh grant is inert
  until you re-run an update.
- **HALT** (S3, S5, S6) — the sentinel's terminal verdict; **C-2 makes it final** —
  checked over the whole log, no waiver overrides it.
- **honest ceiling** (throughout) — the binding doctrine that every control states
  what it buys and cannot; "engages as tested," never "secure."
- **instruction-authority ordering** (S4) — ranks **sources of instruction** by
  tier then specificity — never by recency, proximity, or forcefulness; not
  self-enforcing.
- **integrity manifest** (S22) — a SHA-256 manifest over the enforcement modules
  (including `integrity.py` itself); a **speed bump, not a boundary**.
- **PreToolUse hook** (S2, S19) — the `constitutional-gate.py` hook the harness
  runs before each tool call on **agent-initiated** actions; `deny` is a runtime
  HALT; fail-open by default, fail-closed under `confined`/`exclusive`.
- **privilege profile** (S18) — one of `cooperative`/`confined`/`exclusive`; an
  unknown profile fails closed.
- **provenance** (S23) — an honest-by-construction stamp of how an artifact was
  produced; a reusable library, **not auto-wired** into the default emit path.
- **red-team outcome** (S21) — DEFENDED / PARTIAL / EXPLOITED / DOCUMENTED-LIMIT /
  OUT-OF-TIER; an unknown outcome raises.
- **red-team tier** (S21) — **T0** (attacker supplies read content), **T1** (in-repo
  agent, write+execute), **T2** (operator's shell); a control means nothing without
  a stated tier.
- **scan verdict (high → HALT)** (S15) — any **high** ⇒ HALT, any finding ⇒
  CONDITIONAL PASS, else PASS; exemptions keyed on **provenance, not shape**.
- **SEC-GATE (DESTRUCTIVE)** (S11) — prints `[SEC-GATE/DESTRUCTIVE:*]`, exit 1;
  blocks `overwrite-update`/`overwrite`/`restore-backup` without a clearance or
  valid waiver.
- **SEC-GATE (INTEL-FRESHNESS)** (S12) — prints `[SEC-GATE/INTEL-FRESHNESS]`;
  blocks a generation run on stale intel (age > 24h, bad timestamp, failed
  payload-digest bind).
- **`@security` sentinel** (S5) — the top-priority, **read-only**
  (`tools: ['read','search']`) reviewer emitting PASS/CONDITIONAL PASS/HALT by a
  deterministic escalation table over S-1..S-10; a fallible LLM except where the
  S-1/S-8 scanner backs it.
- **shrink-policy (`--shrink-policy`)** (S13) — `{preserve,warn,halt,allow}`
  protecting operator-enriched fenced content; template-authoritative security
  fences ignore `preserve`.
- **template-authoritative fence** (S13) — a fence whose template body always wins;
  ignores `preserve`, and a security-fence rename refuses the merge outright.
- **waiver** (S7, S9) — a signed instrument clearing a **gate block** (never a
  HALT); valid only with scope match, verified conditions, a rostered approver, a
  future `expires_at`, `uses < max_uses`, and a verifying HMAC-SHA256 signature.

## Sources  ✅ {#S27}

Every canonical fact in this guide resolves to a repo file. The complete
row-by-row provenance table lives in **`SOURCES.md`** — the guide's provenance
gate (`@technical-validator` verifies each row resolves on disk and each ✅/⚙
marker is accurate; `@adversarial` verifies each honest ceiling is not overstated).
Each section's own **Source** line carries the line-precise citations. **No fact
ships from memory** — a claim without a resolvable source is a defect. See
`SOURCES.md` for the full concept→file map.

---

**Sources for Part IX.** `SOURCES.md`; `SECURITY.md`; `.claude/CLAUDE.md`;
and every file cited in S1–S26.
