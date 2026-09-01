# Audience Profiles — how each edition projects the sandboxing skeleton

> This file defines **how to project** a [`SKELETON.md`](SKELETON.md) section into each edition. The
> skeleton fixes *what* a section asserts (canonical facts) and *where* it sits (stable ID `SB…`). This
> file fixes *how deep* and *in what voice* each edition renders it. Together they guarantee: the same
> fact, in the same section, adapted — never a different fact. It mirrors the
> [Security Guide's audience profiles](../agentteams-security-guide/audience-profiles.md).

## The projection principle

```
SKELETON.md  ─────────────▶  Edition R / D / S / E
(facts + structure,          (same facts, same SB IDs,
 one source of truth)         rendered at this edition's depth + mode)
```

An edition may render a section shallower, in a different Diátaxis mode, with or without code — but it
may **not** assert a fact the skeleton does not carry, drop a section the skeleton defines, reorder the
spine, or soften an **honest ceiling**.

## The depth dial

| Dial | Meaning |
|---|---|
| **Full** | every canonical fact, elaborated, with mechanism, `file:line`, its diagram, and its ceiling |
| **Core** | the load-bearing facts + the ceiling; secondary detail linked to Edition R |
| **Light** | one-paragraph gist + analogy; no code, no `file:line` (the ceiling stays, in plain words) |
| **Skip** | omitted in this edition (a cross-reference line may remain) |

## The four editions

### Edition R — `reference/` — "the complete mechanism"
- **Reader:** a maintainer of the sandbox layer, or a thorough reader who wants the whole thing precisely.
- **Diátaxis mix:** Reference + Explanation.
- **Default depth:** Full on every section. Carries all six diagrams.
- **Format:** precise prose, `file:line` refs, tables, every honest ceiling stated, ✅/⚙ markers preserved.
- **Role:** closest to the skeleton; the other three link *into* it for depth.

### Edition D — `for-developers/` — "operators who run agentteams"
- **Reader:** someone who runs the CLI, picks a `privilege_profile`, and actually wraps
  `sandbox/confine-run.sh` (or merges the settings block / sets `GOOSE_SANDBOX`).
- **Diátaxis mix:** How-to + Reference.
- **Default depth:** Core→Full on the controls they operate (profiles, the decision + advisories, the
  three mechanisms, wiring, verification); Light on motivation.
- **Format:** runnable commands (`agentteams --description brief.json …`,
  `sandbox/confine-run.sh --scratch DIR --egress deny -- <cmd>`, `--verify-integrity`), a **"which knob
  do I turn, and what does it cost"** orientation.

### Edition S — `for-researchers/` — "security reviewers new to this stack"
- **Reader:** a security researcher/threat modeler who knows the concepts but not agentteams' internals.
- **Diátaxis mix:** Explanation + Reference (the *why*, the threat model, the guarantees and ceilings).
- **Default depth:** Core→Full, concept-first — teach the adversary (Excessive Agency, inert-until-wired
  false confidence, host-as-TCB) and the trust boundaries before the mechanism names. The capability
  matrix, the honest ceilings, and the verification status get Full; minimal code.

### Edition E — `for-everyone/` — "non-technical"
- **Reader:** a stakeholder or curious reader with no security background.
- **Diátaxis mix:** Explanation only (narrative + analogy).
- **Default depth:** Light; the deepest mechanism sections are Skipped or told purely by analogy.
- **Format:** a consistent running analogy — **a workshop with a lockable room**: the operator can ask
  for a locked room for the worker (the sandbox), but *the lock only works if the operator turns the key*
  (inert-until-wired), *the room is off by default*, *the lock is proven on one kind of building only*,
  and *a guard by the door can shout "stop!" for a few obvious dangerous acts but is not a wall*. Plain
  language, zero code, zero commands.
- **Mandatory-survive ceilings (must appear in E, in plain words — analogy alone is not enough).** The
  plain edition may simplify anything *except* these four, which mislead a stakeholder if lost:
  1. **"By default the room is unlocked."** Confinement is opt-in; the default profile leaves it off.
     (SB3, SB17.)
  2. **"The lock does nothing until the operator turns the key."** An emitted boundary is inert until
     wired/wrapped. (SB14, SB8.)
  3. **"The lock is proven on Linux; on Macs it is advice, not a proven lock; on Windows there is no
     lock to emit."** (SB11, SB12, SB20.)
  4. **"This makes a mis-steered worker's mess stay in the room; it does not stop someone who already
     holds the building's master key."** T6/host-as-TCB stays open. (SB21.)
  State each as a plain sentence, not only as analogy. An E section that drops one is a fact error.

## Depth-by-edition default matrix (per-section overrides live in SKELETON.md)

| Section theme | R | D | S | E |
|---|---|---|---|---|
| What it is & why (SB1–SB3) | Full | Core | Full | Light |
| The request (SB4–SB6) | Full | Full | Core | Light |
| The decision (SB7–SB9) | Full | Full | Full | Core |
| The mechanisms (SB10–SB13) | Full | Full/Core | Core | Light |
| Wiring & enforcement (SB14–SB17) | Full | Full | Core | Core |
| Integrity & drift (SB18–SB19) | Full | Core | Core | Light |
| Ceilings & red-team (SB20–SB21) | Full | Core | Full | Core |
| Synthesis & reference (SB22–SB23) | Full | Core | Full | Light |

> **Diagrams by edition.** R carries all six canonical graphs. D carries G1 (pipeline), G2 (decision),
> G3 (mechanisms), and G4 (hook). S carries G1, G2, and G6 (synthesis). E carries none — it tells the
> same structure in prose + analogy (a diagram is not a substitute for the mandatory-ceiling sentences).
