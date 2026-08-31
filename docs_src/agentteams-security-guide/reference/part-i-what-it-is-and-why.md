# Part I — What it is and why

## What agentteams security is  ✅/⚙ {#S1}

agentteams generates coordinated AI **agent teams** from a single project
description. Its security infrastructure exists for one concrete reason: an
agent that **follows instructions** and holds `edit`, `execute`, or
cross-repository reach can be steered — by text injected into something it
reads, or by its own error — into **destructive, bulk, cross-repository, or
credential-adjacent** actions. The realistic, in-scope adversary is therefore
not a network attacker but **an agent with legitimate write access acting on
injected instructions** — the case OWASP catalogues as **LLM06 "Excessive
Agency."** The whole stack is built around confining what a well-intentioned
but steerable agent can do.

**The response is a layered governance + enforcement stack.** No single
component is "the security boundary." The layers are, in composition (detailed
in Part IX / S25):

- a **non-overridable constitution** (Part II) — the principles that outrank
  every other instruction;
- a **read-only security sentinel** (`@security`, S5) — a reviewer that can say
  HALT;
- an **authorization triad** — clearance, waiver, grant (Part III) — signed
  instruments that gate decisions;
- **destructive-action gates** at CLI entry points (Part IV);
- a **content scanner** (`agentteams.scan`, Part V) — credential/PII/injection
  detection;
- **OS confinement** — sandbox emission and privilege profiles (Part VI);
- **threat intelligence** and a **red-team cycle** (Part VII) that keep the
  controls current and tested;
- **integrity manifests, provenance, and backup/baseline recovery**
  (Part VIII) that make tampering evident and damage reversible.

They **compose**; a gap in one is meant to be covered by another. That
composition, not any one layer, is the design.

**Design-time, not runtime.** The generated team governs **how an app is
built** — it performs design-time review of the project's own construction. It
does **not** run inside the produced application. The generated `@security`
agent is **read-only** and HALTs at *review* time only. A shipped app that
serves LLM output to end users must add its **own** runtime governance; nothing
in this stack protects that app at runtime. Collapsing "we generated a security
team" into "our product is protected at runtime" is precisely the
misconception this fact exists to block.

**Two load-bearing properties are claimed** (and their violation is treated as
a vulnerability, not a bug):

1. **`FENCED` (module-owned) regions survive regeneration.** Content inside
   AGENTTEAMS fences is restored from template on every `--update --merge`. A
   fenced *security* region that does **not** survive a merge is a
   vulnerability — an attacker must not be able to pin a weakened security
   region into a tree and have it persist.
2. **Destructive flags are gated.** Bypassing the security-decision gate
   outside the documented `--yes` interaction is a vulnerability.

**Default runtime posture (binding ceiling — F1).** The *governance* layers —
the constitution, the sentinel, clearance/waiver/grant, the CLI gates, the
content scanner — are **always active**. The *runtime OS-confinement* layers are
**opt-in**. The default privilege profile is `cooperative`
(`agentteams/host_features.py:134-145`), under which **the sandbox is off** and
the PreToolUse constitutional-gate hook is **fail-open**
(`agentteams/templates/universal/hooks/constitutional-gate.py:22-36`). Runtime
confinement engages **only** when the operator selects `confined` or
`exclusive`. Reading "layered stack" as "every layer is active out of the box"
is the overclaim this ceiling exists to prevent: out of the box, the OS-level
locks are dormant by design, and the operator must choose to arm them.

**Honest ceiling.** This layer is described as engaging *as tested*, never as
"secure." What the stack buys is a set of composed, mostly-evident controls
against a steerable in-repo agent; what it cannot buy is a guarantee — the
sentinel is a fallible model (S5), several layers are procedural rather than
code-enforced (S2), and the strongest OS locks are off unless armed.

**Source.** `SECURITY.md` §threat-model, §design-time-vs-runtime;
`.claude/CLAUDE.md` Constitutional Core;
`agentteams/templates/universal/security.template.md`;
`agentteams/host_features.py:134-145` (cooperative default);
`agentteams/templates/universal/hooks/constitutional-gate.py:22-36`
(fail-open default).

## Two surfaces and where enforcement lives  ✅/⚙ {#S2}

**Two distinct security surfaces must never be collapsed.**

| Surface | What it governs | `@security`'s role |
|---|---|---|
| **Agentic-build security** | the agents and the build process itself — the constitution, the sentinel, the gates | **governs** it |
| **Deployed-system security** | the defense-in-depth of the software a project *ships* (Part VI's L0–L7 model, S17) | only **reviews against** it |

The sentinel owns the first surface. For the second, it is a reviewer against a
reference model — it does not deploy, harden, or run inside the shipped system.
Treating a review-against relationship as ownership overstates what the stack
does.

**Enforcement lives on three deliberately separate levels.** An edition must
never present one level's guarantee as another's:

1. **Code gates at CLI entry points** — `agentteams/cli/security_gate.py`,
   **fail-closed** (every unresolved path denies). These are deterministic:
   they run whenever the operator invokes a guarded CLI command.
2. **A runtime PreToolUse hook** —
   `agentteams/templates/universal/hooks/constitutional-gate.py` — which catches
   **agent-initiated tool calls the CLI never sees** (an agent's own
   `Bash`/`Write`). The CLI gate cannot see these; the hook is the counterpart
   surface (S19).
3. **Agent-instruction level** — the sentinel's judgment and most of the
   S-rules (S5). This is **real governance but not a deterministic code
   control**: it depends on a model reading its instructions and deciding
   correctly. Presenting an instruction-level rule as code-enforced is a fact
   error.

**The honest-ceiling doctrine (carried into every edition).** Every control
states what it *buys* and what it *cannot*:

- an **integrity manifest** placed beside the files it protects is a *speed
  bump, not a boundary* (S22);
- **symmetric HMAC signing** of waivers/grants defends only against a
  **keyless** forger, not against someone who holds the key (S6, S7);
- an **emitted sandbox block is inert** until the operator wires it into their
  own settings (S18);
- **only macOS** OS-confinement is empirically verified; Linux is partial and
  Windows is design-only (S18).

These ceilings are facts, not caveats — they ship in every edition unsoftened.

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
