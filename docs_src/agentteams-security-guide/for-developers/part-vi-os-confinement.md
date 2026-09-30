# Part VI — OS confinement

OS confinement bounds an agent's *runtime reach* at the operating-system level.
Two ceilings govern everything below: **confinement is emitted by default but inert
until wired** (as of 2026-W39 the default profile is `confined`, which emits an OS
write-confinement boundary as a settings/config example the operator must merge; the
PreToolUse hook still stays fail-open by default because the flip needs an *explicit*
`confined`/`exclusive`, S1 fact 5), and **agentteams emits configuration; it does not
enforce it** (enforcement belongs to the harness; the empirically deny-tested path is
**Linux** — the `sandbox/confine-run.sh` launcher — while **macOS Seatbelt is
UNVERIFIED**).

## The infrastructure-layers model  ✅ *(reference doc)* {#S17}

A curated **eight-layer (L0–L7) defense-in-depth model for the deployed system a
project builds** — not for the agent build process — cross-cut by a
`design → build → baseline → tune → operate → respond → review` lifecycle
(`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-70`).

| Layer | Concern |
|---|---|
| **L0** | Governance |
| **L1** | Identity |
| **L2** | Crypto / secrets |
| **L3** | **Host & workload hardening** — where OS confinement lives |
| **L4** | Network |
| **L5** | Application & supply chain |
| **L6** | Detection / logging |
| **L7** | Resilience / backup / IR |

It draws an explicit boundary — **"infrastructure security ≠ agent security"**:
this model governs the *deployed system*; `@security` governs the *build process*
(the two-surfaces distinction, S2). Each layer degrades independently.

**Honest ceiling — guidance, not deployment.** A reference *informs*; it secures
nothing by itself
(`…security-infrastructure-layers.reference.template.md:119-130`). Posture is
**"engages as tested,"** never "verified," and the tools it names are **not
deployed by agentteams** — you deploy them, or you do not.

**Source.**
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-70,119-130`.

## Sandbox emission and privilege profiles  ✅ *(Linux bwrap deny-tested)* / ⚙ *(macOS Seatbelt UNVERIFIED, Windows design-only)* {#S18}

**Which knob, and what it costs.** The privilege profile selects how much OS
boundary a generated team requests (`agentteams/host_features.py:134-261`). An
**unknown** profile **fails closed** (raises, never silently downgrades).

| Profile | OS boundary | Emits | What it costs / buys |
|---|---|---|---|
| **`cooperative`** | none — the opt-out | no sandbox request | zero friction; sandbox off + hook fail-open (S19) |
| **`confined`** | requested — **today's default** | a sandbox request token | bounds in-sandbox write reach; hook flipped fail-closed only when explicitly selected |
| **`exclusive`** | requested + read-exclusion | sandbox token **and** `denyRead` | adds outbound credential-dir read-exclusion |

**agentteams emits configuration; the harness enforces it**
(`agentteams/frameworks/_sandbox_emit.py:25-208`,
`agentteams/frameworks/_goose_sandbox_emit.py:1-222`):

- **For Claude**, agentteams injects an **inert `sandbox` example** into
  `settings.hooks.example.json`. **You** merge it into your live settings —
  agentteams **never writes a live `.claude/settings.json`**. Enforcement is
  **Claude Code's own OS sandbox** (macOS Seatbelt / Linux bubblewrap).
- **For Goose**, on **macOS only**, agentteams emits an Apple Seatbelt profile
  (P1-1). Goose has **no native Linux/Windows OS sandbox**.

**`denyWrite` protects the control plane; `denyRead` hardens outbound.**
Independent of profile, the emitted sandbox applies **`denyWrite` of exact
control-plane paths** — `<agents dir>/references/agent-privilege.json`,
`.claude/hooks/constitutional-gate.py` and the verify-key store directory
`<agents dir>/references/authorized-verify-keys/` — deny-over-allow, so an in-sandbox agent
cannot rewrite the enforcement plane governing it. `denyWrite` binds Bash only; the
built-in Write/Edit tools are bound by the `permissions.deny` `Edit(...)` rules emitted beside
it, and Claude Code 2.1.251 on Linux binds each directory `denyWrite` entry read-only (captured
bwrap argv, 2026-09-30; other versions untested). Every
profile also **`denyRead`s the operator private-key directory** `~/.config/agentteams/keys`
(F-1; plus `Read(...)` permission rules for the built-in tools), so a sandboxed agent cannot
read the key and self-sign; the environment variables naming or holding signing keys are still
inherited. `exclusive` adds **`denyRead`
of credential directories** (SSH/AWS/etc.): outbound hardening of *files, not env
vars* — it stops the confined agent reading *your* credential tree, not others
reading yours.

**Honest ceilings (binding):**

- **Emitted blocks are inert until you wire them** — an un-merged example enforces
  nothing.
- **The empirically-verified path is Linux** — a live-kernel bwrap deny test (the framework-neutral `sandbox/confine-run.sh` launcher; see the [Sandboxing Guide](../../agentteams-sandboxing-guide/README.md)). **macOS Seatbelt is UNVERIFIED** (no on-mac deny test).
- **Claude Code's *native* Linux bubblewrap arm has the open D-3 fragility** (distinct from the verified launcher) — on bubblewrap a `denyWrite` of a
  *non-existent* path fails `bwrap` init, blocking **all** Bash (a fail-*shut*
  availability defect, still **open** in `references/agentteams-remediation-log.csv`,
  item D-3); Linux `denyRead` (incl. `~/`) is verified under the AppArmor precondition below.
- **Claude Code's native sandbox fails OPEN without `failIfUnavailable` (fixed
  2026-09-30).** Measured on Linux with `socat` missing: the old block (`enabled: true`,
  `allowUnsandboxedCommands: false`) let Claude Code print "Sandbox disabled … Commands
  will run WITHOUT sandboxing" and run unconfined. The block now emits
  `failIfUnavailable: true` on macOS/Linux targets (omitted on native Windows, where the
  block is advisory), so Claude Code refuses to start instead. Linux needs `bwrap` **and**
  `socat` (`scripts/install-sandbox-deps.sh`); Ubuntu's
  `kernel.apparmor_restrict_unprivileged_userns=1` makes every sandboxed command fail closed
  at the seccomp step. That native arm is now verified end-to-end on Linux (2026-09-30: Ubuntu, Claude Code 2.1.251, bubblewrap 0.11.1, socat 1.8.1.1) **only after** installing Claude Code's documented `/etc/apparmor.d/bwrap` profile, which disables Ubuntu's `bwrap-userns-restrict` host-wide; without it sandboxed commands fail closed. Operators who
  merged an earlier block: re-run `--update` and re-merge the `sandbox` block.
- **Native Windows has no emitted enforcement** (design-only).
- On a host that **cannot** enforce confinement the interactive path **fails
  closed** with `PrivilegeConfinementError` unless you pass
  `--allow-unenforced-confinement` (`agentteams/cli/artifacts.py:321-411`).

Because agentteams only emits an example the operator must merge, **all of the
above is dormant out of the box until you wire it in**: the default `confined`
profile emits the sandbox + `denyWrite`/`denyRead` example, but it enforces nothing
unmerged, and the hook stays fail-open until an explicit `confined`/`exclusive`
flips it. Governance layers are always active; OS confinement enforcement is opt-in.

**Source.** `agentteams/host_features.py:134-261`;
`agentteams/frameworks/_sandbox_emit.py:25-208`;
`agentteams/frameworks/_goose_sandbox_emit.py:1-222`;
`references/agentteams-remediation-log.csv` (D-3);
`agentteams/cli/artifacts.py:321-411`.

## The constitutional-gate hook  ✅ *(fail-closed under confined/exclusive; **fail-open by default**)* {#S19}

The **PreToolUse hook** the harness runs **before** each tool call, enforcing
C-4/C-5 on **agent-initiated** actions — the counterpart to `security_gate.py`,
which only guards CLI entry points that agent `Bash`/`Write` never reach. Because
the *harness* runs it, it is "not merely another file the agents can edit on their
way past it" (`agentteams/templates/universal/hooks/constitutional-gate.py:1-209`).

**Split verdict:**

- **`deny`** — deterministic high-severity **write** findings (credentials/PII/
  injected override text, C-4). Its `deny` is the **runtime expression of a HALT**.
- **`ask`** — procedural **Bash** Mandatory-Review triggers (C-5): privilege
  escalation, piping remote content into a shell, recursive force-removal,
  persistence, package installs, history-rewriting force-push.

**Integrity check before scanning:** before scanning a write it **verifies the
scanner against the integrity manifest** (S22); a tampered `scan.py` yields
**`ask`, not a silent allow**.

**Which knob, and what it costs.** The hook is **fail-open by default** — if it
can't run to completion, the action proceeds. agentteams flips it **fail-closed
only for an *explicit* `confined`/`exclusive`** in the brief (the defaulted
`confined` leaves it fail-open); `--allow-fallback-fail-open` restores fail-open
under those profiles (buys availability, costs the fail-closed guarantee).

**Honest ceiling (E4) — cost, not impossibility.** It does not escape a
determined multi-file tamper: an attacker who edits `scan.py` can also edit the
manifest and this hook. It **raises the cost from one edit to three, each visible
in `git`**. The hook file is itself one of the `denyWrite`-protected control-plane
paths of S18 (a further, Linux-deny-tested obstacle to that third edit) — but the
residual ceiling stands: composed layers raise cost and make tampering evident,
they do not eliminate it.

**Source.**
`agentteams/templates/universal/hooks/constitutional-gate.py:1-209`.

---

**Sources for Part VI.**
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-70,119-130`;
`agentteams/host_features.py:134-261`;
`agentteams/frameworks/_sandbox_emit.py:25-208`;
`agentteams/frameworks/_goose_sandbox_emit.py:1-222`;
`references/agentteams-remediation-log.csv` (D-3);
`agentteams/cli/artifacts.py:321-411`;
`agentteams/templates/universal/hooks/constitutional-gate.py:1-209`.
