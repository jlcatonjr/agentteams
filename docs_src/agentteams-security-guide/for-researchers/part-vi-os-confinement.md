# Part VI — OS confinement

OS confinement is the layer that bounds an agent's *runtime reach* at the
operating-system level — below judgment (the sentinel), decisions (the gates),
and content (the scanner). It has three parts: a reference model for the
defense-in-depth of the *deployed system a project builds* (S17); the emission of
sandbox configuration and the profiles that select it (S18); and the runtime
PreToolUse hook that catches agent-initiated actions the CLI gates never see
(S19).

Two ceilings govern everything below and are worth a reviewer holding up front:

- **Confinement is opt-in.** The default profile is `cooperative`, under which
  the sandbox is **off** and the hook is **fail-open** (S1 fact 5). "OS
  confinement layer" does **not** mean "runtime confinement is on by default."
- **agentteams emits configuration; it does not enforce it.** Enforcement belongs
  to the *harness* (Claude Code's Seatbelt/bubblewrap); the empirically deny-tested path
  is **Linux** (the `sandbox/confine-run.sh` launcher), while **macOS Seatbelt is UNVERIFIED**.

## The infrastructure-layers model  ✅ *(reference doc)* {#S17}

This section exists to draw one boundary a security reviewer must not blur:
**infrastructure security ≠ agent security.** They are adjacent surfaces, and
conflating them is the same error as the design-time/runtime confusion of S1–S2.

The model is a curated **eight-layer (L0–L7) defense-in-depth model for the
deployed system a project builds** — *not* for the agent build process — cross-cut
by a `design → build → baseline → tune → operate → respond → review` lifecycle,
so each layer is considered at each stage:

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

**The boundary it draws.** This L0–L7 model governs the *deployed system*; the
`@security` sentinel governs the *build process*. The two must not be
collapsed — the recurring "two surfaces" distinction (S2). Each layer **degrades
independently**: a failure at L4 does not imply a failure at L3, so a reviewer
should assess each layer on its own, not treat the model as a monolith.

**Honest ceiling — guidance, not deployment.** This is the most important fact
about S17. **A reference informs; it secures nothing by itself.** Posture
throughout is stated as **"engages as tested,"** never "verified" or "secure,"
and the concrete tools the model names are **not deployed by agentteams** — the
operator deploys them, or does not. Treating the presence of this reference in a
generated tree as evidence that the deployed system is hardened is exactly the
category error the ceiling exists to close. For a threat modeler: this document
is a checklist of *where to look*, not a claim that the looking has been done.

**Source.**
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-70,119-130`.
Full treatment: Edition R, S17.

## Sandbox emission and privilege profiles  ✅ *(Linux bwrap deny-tested)* / ⚙ *(macOS Seatbelt UNVERIFIED, Windows design-only)* {#S18}

**The adversary:** an in-repo agent (S1) that would use OS-level reach — writing
outside its scope, or reading a credential directory — to escalate. The control
is a requested OS sandbox selected by a **privilege profile**:

| Profile | OS boundary | Emits |
|---|---|---|
| **`cooperative`** | None — **today's default** | No sandbox request |
| **`confined`** | Requested | A sandbox request token |
| **`exclusive`** | Requested + read-exclusion | A sandbox request token **and** `denyRead` |

An **unknown** profile **fails closed** — it raises rather than silently
downgrading. `denyWrite` of exact control-plane paths (`agent-privilege.json`,
the `constitutional-gate.py` hook) applies deny-over-allow so an in-sandbox agent
cannot rewrite the enforcement plane governing it; `exclusive` adds `denyRead` of
credential directories (SSH/AWS/etc.) — outbound hardening of *files, not env
vars*, which stops the confined agent reading *your* tree, not others reading it.

**The reviewer-critical fact: agentteams emits, the harness enforces.** For
Claude it injects an **inert `sandbox` example** into `settings.hooks.example.json`
(the operator must merge it; agentteams never writes a live
`.claude/settings.json`); actual enforcement is **Claude Code's own OS sandbox**
(macOS Seatbelt / Linux bubblewrap). For Goose, on **macOS only**, it emits an
Apple Seatbelt profile; Goose has **no native Linux/Windows OS sandbox.**

**Honest ceilings (all binding).**

- **Emitted blocks are inert until wired** — an un-merged example enforces
  nothing.
- **The empirically-verified path is Linux** — a live-kernel bwrap deny test (the framework-neutral `sandbox/confine-run.sh` launcher; see the [Sandboxing Guide](../../agentteams-sandboxing-guide/README.md)). **macOS Seatbelt is UNVERIFIED** (no on-mac deny test).
- **Claude Code's *native* Linux bubblewrap arm has the open D-3 fragility** (distinct
  from the verified launcher above): a `denyWrite` of a *non-existent* path fails `bwrap`
  init and blocks **all** Bash — a fail-*shut* availability defect (not a hole) that
  remains **open** in the remediation log; that native arm's `denyRead` and its product
  arm on stock Ubuntu are likewise **unverified**.
- **Native Windows has no emitted enforcement** — design-only.
- On a host where confinement cannot be enforced, the interactive path **fails
  closed** (`PrivilegeConfinementError`) unless the operator passes
  `--allow-unenforced-confinement`.

Because the default is `cooperative`, **all of this is dormant out of the box.**
Full profile mechanics and line numbers: Edition R, S18.

**Source.** `agentteams/host_features.py:134-261`;
`agentteams/frameworks/_sandbox_emit.py:25-208`;
`agentteams/frameworks/_goose_sandbox_emit.py:1-222`;
`references/agentteams-remediation-log.csv` (D-3);
`agentteams/cli/artifacts.py:321-411`.

## The constitutional-gate hook  ✅ *(fail-closed under confined/exclusive; **fail-open by default**)* {#S19}

**The adversary:** an agent's *own* `Bash`/`Write` tool calls, which never pass
through the CLI entry points the gates (Part IV) guard. The hook is the runtime
counterpart that catches them. It is a **PreToolUse hook the harness runs before
each tool call**, enforcing C-4/C-5 on agent-initiated actions. Because *the
harness* runs it — not the agent — it is "not merely another file the agents can
edit on their way past it."

**Split verdict:** `deny` for deterministic high-severity write findings
(credentials, PII, injected override text — C-4); this `deny` is the **runtime
expression of a HALT.** `ask` for procedural Bash Mandatory-Review triggers
(C-5): privilege escalation, piping remote content into a shell, recursive
force-removal, persistence mechanisms, package installs, history-rewriting
force-push — requiring operator confirmation rather than an outright deny. Before
scanning a write it **verifies the scanner against the integrity manifest** (S22);
a tampered scanner degrades to `ask`, not a silent allow.

**Default posture — fail-open, flipped fail-closed by profile.** By default
(`cooperative`) the hook is **fail-open**: if it cannot run to completion, the
action proceeds. agentteams flips it **fail-closed for `confined`/`exclusive`**;
`--allow-fallback-fail-open` restores open under those profiles. Same opt-in
posture as S18.

**Honest ceiling (E4) — cost, not impossibility.** This is the residual risk that
sits at the center of the whole stack's honesty. The hook does **not** escape a
determined multi-file tamper: an attacker who can edit the scanner can also edit
the integrity manifest and this hook file. What the composed layers do is **raise
the cost from one edit to three, each visible in `git`** — a single silent change
becomes a multi-step, recorded, diffable act. The hook file is itself one of the
`denyWrite`-protected paths (S18), which adds the (Linux-deny-tested) sandbox as a
further obstacle to that third edit. The residual ceiling stands: composed layers
raise cost and make tampering evident; they do not eliminate it. Full mechanism:
Edition R, S19.

**Source.**
`agentteams/templates/universal/hooks/constitutional-gate.py:1-209`.

---

**Sources for Part VI.**
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md`;
`agentteams/host_features.py`; `agentteams/frameworks/_sandbox_emit.py`;
`agentteams/frameworks/_goose_sandbox_emit.py`;
`references/agentteams-remediation-log.csv` (D-3);
`agentteams/cli/artifacts.py`;
`agentteams/templates/universal/hooks/constitutional-gate.py`. Line-precise
provenance: `SOURCES.md` (S27).
