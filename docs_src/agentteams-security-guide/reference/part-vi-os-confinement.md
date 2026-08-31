# Part VI — OS confinement

OS confinement is the layer that bounds an agent's *runtime reach* at the operating-system
level, rather than at the level of judgment (the sentinel), decisions (the gates), or
content (the scanner). It has three parts: a reference model for the defense-in-depth of
the *deployed system a project builds* (S17); the emission of sandbox configuration and
the privilege profiles that select it (S18); and the runtime PreToolUse hook that catches
agent-initiated actions the CLI gates never see (S19).

This Part carries the most consequential honest ceilings in the guide. Two govern
everything below and are worth stating up front:

- **Confinement is opt-in.** The default privilege profile is `cooperative`, under which
  the sandbox is **off** and the hook is **fail-open** (S1 fact 5). Reading "OS
  confinement layer" as "runtime confinement is on by default" is the overclaim these
  sections exist to prevent.
- **agentteams emits configuration; it does not enforce it.** Enforcement belongs to the
  *harness* (Claude Code's Seatbelt/bubblewrap), and is empirically verified on **macOS
  only**.

---

## The infrastructure-layers model  ✅ *(reference doc)* {#S17}

The infrastructure-layers model is a curated **eight-layer (L0–L7) defense-in-depth
model for the deployed system a project builds** — not for the agent build process. It is
cross-cut by a `design → build → baseline → tune → operate → respond → review` lifecycle,
so each layer is considered at each lifecycle stage
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

### The boundary it draws

The reference states an explicit boundary: **"infrastructure security ≠ agent
security."** This L0–L7 model governs the *deployed system*; the `@security` sentinel
governs the *build process*. The two are adjacent and must **not** be collapsed — a point
that recurs throughout the guide as the "two surfaces" distinction (S2). Each layer
**degrades independently**: a failure at L4 does not imply a failure at L3.

### Honest ceiling — guidance, not deployment

**It is guidance, not deployment.** A reference *informs*; it secures nothing by itself
(`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:119-130`).
Posture throughout is stated as **"engages as tested,"** never "verified" or "secure,"
and the concrete tools the model names are **not deployed by agentteams** — the operator
deploys them, or does not. Treating the presence of this reference in a generated tree as
evidence that the deployed system is hardened is a category error the ceiling exists to
close.

**Source.**
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-70,119-130`.

---

## Sandbox emission and privilege profiles  ✅ *(macOS)* / ⚙ *(Linux partial, Windows design-only)* {#S18}

This is the section where the "emits vs enforces" distinction and the macOS-only
verification ceiling are load-bearing. Read the honest-ceilings subsection as the primary
content, not an appendix to it.

### Three privilege profiles

A privilege profile selects how much OS boundary a generated team requests
(`agentteams/host_features.py:134-261`). An **unknown** profile **fails closed** — it
raises rather than silently downgrading to something weaker.

| Profile | OS boundary | Emits |
|---|---|---|
| **`cooperative`** | None — **today's default** | No sandbox request |
| **`confined`** | Requested | A sandbox request token |
| **`exclusive`** | Requested + read-exclusion | A sandbox request token **and** `denyRead` |

### agentteams emits configuration, it does not enforce (fact 2 + fact 4)

**agentteams emits configuration; the harness enforces it.**
(`agentteams/frameworks/_sandbox_emit.py:25-208`,
`agentteams/frameworks/_goose_sandbox_emit.py:1-222`.)

- **For Claude**, agentteams injects an **inert `sandbox` example** into
  `settings.hooks.example.json`. The operator must merge it into their own live settings;
  **agentteams never writes a live `.claude/settings.json`**. Actual enforcement is
  **Claude Code's own OS sandbox** — macOS Seatbelt or Linux bubblewrap.
- **For Goose**, on **macOS only**, agentteams emits an Apple Seatbelt profile (the P1-1
  work). Goose has **no native Linux or Windows OS sandbox**.

The consequence, stated as fact 4's binding ceilings:

- **Emitted blocks are inert until the operator wires them.** An un-merged sandbox
  example enforces nothing. Enforcement is the harness's job, not agentteams'.
- **Only macOS is empirically verified.** The macOS path (Seatbelt) is the one that has
  been tested to engage.
- **Linux has the open D-3 fragility.** On Linux bubblewrap, a `denyWrite` of a
  *non-existent* path fails `bwrap` initialization, which blocks **all** Bash — a
  fail-*shut* fragility, not a security hole, but an availability defect that remains
  **open** in `references/agentteams-remediation-log.csv` (item D-3). The Linux
  `denyRead` behaviour is likewise **unverified**.
- **Native Windows has no emitted enforcement** — it is design-only.
- On a host where confinement **cannot** be enforced, the interactive path **fails
  closed** with `PrivilegeConfinementError` unless the operator explicitly passes
  `--allow-unenforced-confinement` (`agentteams/cli/artifacts.py:321-411`).

### `denyWrite` protects the control plane; `denyRead` hardens outbound

Independent of profile, the emitted sandbox applies **`denyWrite` of exact control-plane
paths** — `references/agent-privilege.json` and `.claude/hooks/constitutional-gate.py`
— on a **deny-over-allow** basis, so an agent running *inside* the sandbox cannot rewrite
the enforcement plane that governs it (`agentteams/frameworks/_sandbox_emit.py:25-208`).
The `constitutional-gate.py` hook of S19 is itself one of these protected paths.

The `exclusive` profile additionally emits **`denyRead` of credential directories**
(SSH, AWS, and similar). This is **outbound hardening** — it targets *files, not
environment variables*, and it stops the confined agent from *reading your* credential
tree; it does **not** stop other processes from reading your tree.

### The tie to the default posture (S1 fact 5)

Because the default profile is `cooperative`, **all of the above is dormant out of the
box**: no sandbox is emitted, no `denyWrite`/`denyRead` applies, and the S19 hook is
fail-open. Runtime OS confinement engages only when the operator selects `confined` or
`exclusive`. The governance layers (constitution, sentinel, gates, scanner) are always
active; the OS-confinement layer is opt-in.

**Source.** `agentteams/host_features.py:134-261`;
`agentteams/frameworks/_sandbox_emit.py:25-208`;
`agentteams/frameworks/_goose_sandbox_emit.py:1-222`;
`references/agentteams-remediation-log.csv` (D-3);
`agentteams/cli/artifacts.py:321-411`.

---

## The constitutional-gate hook  ✅ *(fail-closed under confined/exclusive; **fail-open by default**)* {#S19}

The constitutional-gate hook is the runtime counterpart to the CLI's
`security_gate.py`. Where the CLI gate guards a handful of CLI entry points, agent
`Bash`/`Write` calls never pass through those entry points — the hook is what catches them.

### Mechanism — a PreToolUse hook the harness runs

The hook is a **PreToolUse hook** the harness invokes **before** each tool call,
enforcing C-4 (content is data) and C-5 (clearance precedes destruction) on
**agent-initiated** actions (`agentteams/templates/universal/hooks/constitutional-gate.py:1-209`).
Because *the harness* runs it — not the agent — it is "not merely another file the agents
can edit on their way past it": an agent cannot skip a step the harness performs before
handing control to the agent's tool call.

### Split verdict

The hook returns one of two blocking verdicts, split by the kind of action:

- **`deny`** — for deterministic high-severity **write** findings: credentials, PII, or
  injected instruction-override text (C-4). This `deny` is the **runtime expression of a
  HALT**.
- **`ask`** — for procedural **Bash** Mandatory-Review triggers (C-5): privilege
  escalation, piping remote content into a shell, recursive force-removal, persistence
  mechanisms, package installs, and history-rewriting force-push. These require operator
  confirmation rather than an outright deny.

### Integrity check before scanning

Before it scans a write, the hook **verifies the scanner against the integrity manifest**
(S22). If `scan.py` has been tampered with, the hook returns **`ask`, not a silent
allow** — a compromised scanner degrades to operator prompting, not to a bypass.

### Default posture — fail-open, flipped fail-closed by profile

By default (the `cooperative` profile) the hook is **fail-open**: if the hook itself
cannot run to completion, the action proceeds. agentteams flips it **fail-closed for the
`confined` and `exclusive`** profiles; the operator can restore fail-open under those
profiles with `--allow-fallback-fail-open`. This is the same opt-in confinement posture
as S18: the strong runtime behaviour is tied to the non-default profiles.

### Honest ceiling (E4) — cost, not impossibility (fact 4)

**The hook does not escape a determined multi-file tamper.** An attacker who can edit
`scan.py` can also edit the integrity manifest and this hook file itself. The hook does
**not** make that impossible — it **raises the cost from one edit to three, each of them
visible in `git`**. Its value is that it converts a single silent change into a
multi-step, recorded, git-diffable act, and gives the operator a chance to notice. The
hook file is itself one of the `denyWrite`-protected control-plane paths of S18, which
adds the sandbox as a further (macOS-verified) obstacle to that third edit — but the
residual ceiling stands: composed layers raise cost and make tampering evident, they do
not eliminate it.

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
