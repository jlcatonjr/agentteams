# Part I — What it is and why

## What agentteams security is  ✅/⚙ {#S1}

agentteams generates coordinated AI **agent teams** from a project description.
The security stack exists because an agent that **follows instructions** and
holds `edit`/`execute`/cross-repo reach can be steered — by injected text or its
own error — into **destructive, bulk, cross-repository, or credential-adjacent**
actions. The realistic in-scope adversary is **an agent with legitimate write
access acting on injected instructions** (OWASP LLM06, "Excessive Agency").

**What you get is a layered stack, and no single layer is the boundary** — they
compose (S25): a non-overridable constitution (Part II), a read-only sentinel
(S5), the clearance/waiver/grant triad (Part III), destructive-action gates
(Part IV), a content scanner (Part V), OS confinement (Part VI), threat intel +
a red-team cycle (Part VII), and integrity/backup recovery (Part VIII).

**Design-time, not runtime.** The team governs *how an app is built*. It does
**not** run inside the produced app; the generated `@security` agent is
**read-only** and HALTs at review time only. An app that serves LLM output to
end users must add its **own** runtime governance.

**Two load-bearing properties** (violation = vulnerability, not a bug): (1)
`FENCED` module-owned regions survive regeneration — restored from template on
every `--update --merge`; a security fence that does not survive a merge is a
vulnerability. (2) Destructive flags are gated — bypassing the security-decision
gate outside the documented `--yes` interaction is a vulnerability.

**Which knob, and what it costs — the default posture (binding ceiling).** The
*governance* layers (constitution, sentinel, triad, CLI gates, scanner) are
**always active**. The *runtime OS-confinement* layers are **opt-in**:

| Knob | Default | Cost of the default |
|---|---|---|
| Privilege profile (`--privilege-profile`) | `cooperative` (`agentteams/host_features.py:134-145`) | Sandbox **off**; the PreToolUse hook is **fail-open** (`agentteams/templates/universal/hooks/constitutional-gate.py:22-36`) |
| Runtime OS confinement | dormant | Engages only when you select `confined`/`exclusive` (S18) |

Reading "layered stack" as "every layer is armed out of the box" is the exact
overclaim this ceiling prevents: the OS-level locks are dormant until you arm
them.

**Honest ceiling.** The stack engages *as tested*, never "secure": the sentinel
is a fallible model (S5), several layers are procedural not code-enforced (S2),
and the strongest OS locks are off unless armed.

**Source.** `SECURITY.md` §threat-model, §design-time-vs-runtime;
`.claude/CLAUDE.md` Constitutional Core;
`agentteams/templates/universal/security.template.md`;
`agentteams/host_features.py:134-145`;
`agentteams/templates/universal/hooks/constitutional-gate.py:22-36`.

## Two surfaces and where enforcement lives  ✅/⚙ {#S2}

**Two surfaces you must not collapse:**

| Surface | What it governs | `@security`'s role |
|---|---|---|
| **Agentic-build security** | the agents + the build process (constitution, sentinel, gates) | **governs** |
| **Deployed-system security** | the defense-in-depth of the software you *ship* (Part VI's L0–L7, S17) | only **reviews against** |

**Enforcement lives on three deliberately separate levels** — do not treat one
level's guarantee as another's:

1. **Code gates at CLI entry points** — `agentteams/cli/security_gate.py`,
   **fail-closed** (every unresolved path denies). Deterministic; they run when
   you invoke a guarded CLI command.
2. **A runtime PreToolUse hook** —
   `agentteams/templates/universal/hooks/constitutional-gate.py` — catches
   **agent-initiated tool calls the CLI never sees** (an agent's own
   `Bash`/`Write`). Its counterpart is S19.
3. **Agent-instruction level** — the sentinel's judgment and most S-rules (S5).
   **Real governance, not a deterministic code control.** Presenting an
   instruction-level rule as code-enforced is a fact error.

**Honest-ceiling doctrine (carried into every edition, unsoftened):** an
integrity manifest beside the files it protects is a *speed bump, not a boundary*
(S22); symmetric HMAC signing defends only a **keyless** forger, not a key-holder
(S6, S7); an emitted sandbox is **inert until wired** (S18); **only macOS**
OS-confinement is empirically verified (S18).

**Source.** `SECURITY.md` §threat-model; `agentteams/cli/security_gate.py:1-10`;
`agentteams/templates/universal/hooks/constitutional-gate.py:1-49`;
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-44`.

---

**Sources for Part I.** `SECURITY.md`; `.claude/CLAUDE.md`;
`agentteams/templates/universal/security.template.md`;
`agentteams/host_features.py`;
`agentteams/templates/universal/hooks/constitutional-gate.py`;
`agentteams/cli/security_gate.py`;
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md`.
