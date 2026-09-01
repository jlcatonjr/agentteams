# Part I — What it is and why

## What agentteams security is  ✅/⚙ {#S1}

**Start with the adversary.** agentteams generates coordinated AI **agent
teams** from a single project description. Those agents follow instructions and
some of them hold real capability — `edit`, `execute`, cross-repository reach.
That combination is the whole threat. An agent that will do what it is told, and
*can* change files or run commands, can be steered — by text injected into
something it reads, or simply by its own error — into **destructive, bulk,
cross-repository, or credential-adjacent** actions it was never asked to take.

So the realistic in-scope adversary here is **not** a network attacker probing a
service. It is **an agent with legitimate write access acting on injected
instructions** — the failure class OWASP catalogues as **LLM06 "Excessive
Agency."** Every control in this guide is built to bound what a well-intentioned
but steerable agent can do to the tree it works in. If you are threat-modeling
this system, model that agent, not an outside intruder.

**The response is a layered governance + enforcement stack — and the layering is
the point.** No single component is "the security boundary." The claim the
system makes is weaker and more honest than that: the layers *compose*, and a
gap in one is meant to be covered by another (the composition is Part IX / S25).
The layers are:

- a **non-overridable constitution** (Part II) — the principles that outrank
  every other instruction;
- a **read-only security sentinel**, `@security` (S5) — a reviewer that can say
  HALT but cannot itself act;
- an **authorization triad** — clearance, waiver, grant (Part III) — signed
  instruments that gate decisions;
- **destructive-action gates** at the CLI entry points (Part IV);
- a **content scanner** (Part V) — deterministic credential / PII / injection
  detection;
- **OS confinement** — sandbox emission and privilege profiles (Part VI);
- **threat intelligence** and a **red-team cycle** (Part VII) that keep the
  controls current and tested;
- **integrity manifests, provenance, and backup/baseline recovery**
  (Part VIII) that make tampering evident and damage reversible.

**The first trust boundary a reviewer must hold: design-time, not runtime.** The
generated team governs *how an app is built* — it reviews the project's own
construction at design time. It does **not** run inside the produced
application. The generated `@security` agent is read-only and HALTs at *review*
time only. A shipped app that serves LLM output to end users must add its **own**
runtime governance; nothing in this stack protects that app once it is running.
The tempting overclaim — "we generated a security team, so our product is
protected at runtime" — is exactly the misconception this fact exists to block,
and it is the one a stakeholder is most likely to make.

**Two properties are load-bearing, and their violation is a vulnerability, not a
bug.** These are the two things the system promises to a reviewer:

1. **`FENCED` (module-owned) regions survive regeneration.** Content inside
   AGENTTEAMS fences is restored from template on every `--update --merge`. A
   fenced *security* region that does **not** survive a merge is a
   vulnerability — an attacker must not be able to pin a weakened security region
   into a tree and have it persist.
2. **Destructive flags are gated.** Bypassing the security-decision gate outside
   the documented `--yes` interaction is a vulnerability.

**The binding runtime-posture ceiling (E4-adjacent, F1).** This is the fact a
reviewer must not miss. The *governance* layers — the constitution, the
sentinel, clearance/waiver/grant, the CLI gates, the content scanner — are
**always active**. The *runtime OS-confinement* layers are **opt-in**. The
default privilege profile is `cooperative`, under which **the sandbox is off**
and the PreToolUse constitutional-gate hook is **fail-open**. Runtime
confinement engages **only** when the operator deliberately selects `confined`
or `exclusive`. Reading "layered stack" as "every layer is armed out of the box"
is the overclaim this ceiling exists to prevent: out of the box, the OS-level
locks are dormant by design.

**Honest ceiling.** This stack is described as engaging *as tested*, never as
"secure." What it buys is a set of composed, mostly-evident controls against a
steerable in-repo agent. What it cannot buy is a guarantee — the sentinel is a
fallible model (S5), several layers are procedural rather than code-enforced
(S2), and the strongest OS locks are off unless armed.

**Source.** `SECURITY.md` §threat-model, §design-time-vs-runtime;
`.claude/CLAUDE.md` Constitutional Core;
`agentteams/templates/universal/security.template.md`;
`agentteams/host_features.py:134-145` (cooperative default);
`agentteams/templates/universal/hooks/constitutional-gate.py:22-36`
(fail-open default). Full line-precise treatment: Edition R, S1.

## Two surfaces and where enforcement lives  ✅/⚙ {#S2}

For a threat modeler, the single most useful distinction in this system is that
there are **two separate security surfaces**, and collapsing them produces a
false sense of coverage.

| Surface | What it governs | `@security`'s role |
|---|---|---|
| **Agentic-build security** | the agents and the build process itself — the constitution, the sentinel, the gates | **governs** it |
| **Deployed-system security** | the defense-in-depth of the software a project *ships* (Part VI's L0–L7 model, S17) | only **reviews against** it |

The sentinel owns the first surface. For the second, it is a reviewer against a
reference model — it does not deploy, harden, or run inside the shipped system.
Treating a *review-against* relationship as ownership overstates what the stack
does, and is the mirror of the design-time/runtime confusion in S1.

**Enforcement lives on three deliberately separate levels.** When you assess
whether a given rule is a real guarantee, first ask *which level* it lives on —
because the levels do not offer the same kind of assurance:

1. **Code gates at the CLI entry points** — `security_gate.py`, **fail-closed**
   (every unresolved path denies). Deterministic: they run whenever the operator
   invokes a guarded CLI command.
2. **A runtime PreToolUse hook** — `constitutional-gate.py` — which catches
   **agent-initiated tool calls the CLI never sees** (an agent's own
   `Bash`/`Write`). The CLI gate structurally cannot see these; the hook is the
   counterpart surface (S19).
3. **The agent-instruction level** — the sentinel's judgment and most of the
   S-rules (S5). This is **real governance but not a deterministic code
   control**: it depends on a model reading its instructions and deciding
   correctly. Presenting an instruction-level rule as if it were code-enforced
   is a fact error — and the most common way a reader overestimates this system.

**The honest-ceiling doctrine, applied to the boundaries themselves.** Each
control below states what it *buys* and what it *cannot* — and these ceilings
are facts carried into every edition, not caveats to be softened:

- an **integrity manifest** placed beside the files it protects is a *speed bump,
  not a boundary* (S22);
- **symmetric HMAC signing** of waivers and grants defends only against a
  **keyless** forger — never against someone who holds the signing key (S6, S7);
- an **emitted sandbox block is inert** until the operator wires it into their
  own settings (S18);
- OS-confinement is empirically verified on **Linux** (the `sandbox/confine-run.sh` bwrap deny test);
  **macOS Seatbelt is unverified**; Windows is design-only (S18).

A reviewer should treat these four sentences as the load-bearing residual-risk
list for the whole system.

**Source.** `SECURITY.md` §threat-model; `agentteams/cli/security_gate.py:1-10`;
`agentteams/templates/universal/hooks/constitutional-gate.py:1-49`;
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md:31-44`.
Full treatment: Edition R, S2.

---

**Sources for Part I.** `SECURITY.md`; `.claude/CLAUDE.md`;
`agentteams/templates/universal/security.template.md`;
`agentteams/host_features.py`;
`agentteams/templates/universal/hooks/constitutional-gate.py`;
`agentteams/cli/security_gate.py`;
`agentteams/templates/universal/security-infrastructure-layers.reference.template.md`.
Line-precise provenance: `SOURCES.md` (S27).
