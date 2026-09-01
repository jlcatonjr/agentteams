# Sandboxing — For Researchers

> **Edition S — security reviewers & threat modelers, new to this stack.** Explanation + Reference: the
> *why*, the adversary, the guarantees and their ceilings. Concept-first — the threat model before the
> mechanism names. Minimal code. For the mechanism internals, follow into the
> [Reference edition](../reference/README.md).

## The claim under review

agentteams boxes a **design-time agent** (one that builds a project) so an *injected or mis-followed
instruction* cannot escape the declared workspace. This edition foregrounds the **adversary**, the
**trust boundaries**, and — most important for a reviewer — each control's **stated ceiling**, because
the failure mode this subsystem most guards against is *false confidence* (an emitted boundary that
confines nothing).

## Contents

- **[Part I — What it is & why](part-i-what-it-is-and-why.md)** (SB1–SB3) — the adversary + opt-in posture
- **[Part II — The request](part-ii-the-request.md)** (SB4–SB6)
- **[Part III — The decision](part-iii-the-decision.md)** (SB7–SB9) — the capability/advisory model
- **[Part IV — The mechanisms](part-iv-the-mechanisms.md)** (SB10–SB13)
- **[Part V — Wiring & enforcement](part-v-wiring-and-enforcement.md)** (SB14–SB17) — inert-until-wired
- **[Part VI — Integrity & drift](part-vi-integrity-and-drift.md)** (SB18–SB19)
- **[Part VII — Ceilings & red-team](part-vii-ceilings-and-red-team.md)** (SB20–SB21) — the honest verdicts
- **[Part VIII — Synthesis & reference](part-viii-synthesis-and-reference.md)** (SB22–SB23)

*Diagrams:* G1 (pipeline, Part I), G2 (decision, Part III), G6 (synthesis, Part VIII).
