# Sandboxing — For Developers

> **Edition D — operators & integrators who run agentteams.** How-to + Reference: *which knob do I turn,
> and what does it cost.* Runnable commands, `file:line` pointers, Core→Full on the controls you operate.
> For the complete mechanism, follow the links into the [Reference edition](../reference/README.md).

## The 60-second version

1. Confinement is **opt-in**: set `privilege_profile` in your brief (`confined` or `exclusive`); the
   default `cooperative` emits **no** sandbox and a fail-open hook.
2. agentteams **decides** whether it can emit a boundary for your framework × OS, and **emits an
   artifact** — but that artifact is **inert until you wire it**.
3. On **Linux** you must **wrap** your agent: `sandbox/confine-run.sh --scratch DIR --egress deny --
   <your agent cmd>`. Nothing is confined until you do.

## Contents

- **[Part I — What it is & why](part-i-what-it-is-and-why.md)** (SB1–SB3)
- **[Part II — The request](part-ii-the-request.md)** (SB4–SB6) — the knobs you set
- **[Part III — The decision](part-iii-the-decision.md)** (SB7–SB9) — what agentteams will/won't emit
- **[Part IV — The mechanisms](part-iv-the-mechanisms.md)** (SB10–SB13) — the three artifacts
- **[Part V — Wiring & enforcement](part-v-wiring-and-enforcement.md)** (SB14–SB17) — how to activate + verify
- **[Part VI — Integrity & drift](part-vi-integrity-and-drift.md)** (SB18–SB19)
- **[Part VII — Ceilings & red-team](part-vii-ceilings-and-red-team.md)** (SB20–SB21)
- **[Part VIII — Synthesis & reference](part-viii-synthesis-and-reference.md)** (SB22–SB23) — the tables

*Diagrams in this edition:* G1 (pipeline, Part I), G2 (decision, Part III), G3 (mechanisms, Part IV),
G4 (hook, Part V).
