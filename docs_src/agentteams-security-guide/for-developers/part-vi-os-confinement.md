# Part VI — OS confinement

OS confinement bounds an agent's *runtime reach* at the operating-system level.
Two ceilings govern everything below: **confinement is opt-in** (default profile
`cooperative` — sandbox off, hook fail-open, S1 fact 5), and **agentteams emits
configuration; it does not enforce it** (enforcement belongs to the harness, and
is empirically verified on **macOS only**).

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

## Sandbox emission and privilege profiles  ✅ *(macOS)* / ⚙ *(Linux partial, Windows design-only)* {#S18}

**Which knob, and what it costs.** The privilege profile selects how much OS
boundary a generated team requests (`agentteams/host_features.py:134-261`). An
**unknown** profile **fails closed** (raises, never silently downgrades).

| Profile | OS boundary | Emits | What it costs / buys |
|---|---|---|---|
| **`cooperative`** | none — **today's default** | no sandbox request | zero friction; sandbox off + hook fail-open (S19) |
| **`confined`** | requested | a sandbox request token | bounds in-sandbox write reach; hook flipped fail-closed |
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
control-plane paths** — `references/agent-privilege.json` and
`.claude/hooks/constitutional-gate.py` — deny-over-allow, so an in-sandbox agent
cannot rewrite the enforcement plane governing it. `exclusive` adds **`denyRead`
of credential directories** (SSH/AWS/etc.): outbound hardening of *files, not env
vars* — it stops the confined agent reading *your* credential tree, not others
reading yours.

**Honest ceilings (binding):**

- **Emitted blocks are inert until you wire them** — an un-merged example enforces
  nothing.
- **Only macOS is empirically verified.**
- **Linux has the open D-3 fragility** — on bubblewrap a `denyWrite` of a
  *non-existent* path fails `bwrap` init, blocking **all** Bash (a fail-*shut*
  availability defect, still **open** in `references/agentteams-remediation-log.csv`,
  item D-3); Linux `denyRead` is **unverified**.
- **Native Windows has no emitted enforcement** (design-only).
- On a host that **cannot** enforce confinement the interactive path **fails
  closed** with `PrivilegeConfinementError` unless you pass
  `--allow-unenforced-confinement` (`agentteams/cli/artifacts.py:321-411`).

Because the default is `cooperative`, **all of the above is dormant out of the
box**: no sandbox, no `denyWrite`/`denyRead`, hook fail-open. Governance layers
are always active; OS confinement is opt-in.

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

**Which knob, and what it costs.** Default (`cooperative`) the hook is
**fail-open** — if it can't run to completion, the action proceeds. agentteams
flips it **fail-closed for `confined`/`exclusive`**; `--allow-fallback-fail-open`
restores fail-open under those profiles (buys availability, costs the fail-closed
guarantee).

**Honest ceiling (E4) — cost, not impossibility.** It does not escape a
determined multi-file tamper: an attacker who edits `scan.py` can also edit the
manifest and this hook. It **raises the cost from one edit to three, each visible
in `git`**. The hook file is itself one of the `denyWrite`-protected control-plane
paths of S18 (a further, macOS-verified obstacle to that third edit) — but the
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
