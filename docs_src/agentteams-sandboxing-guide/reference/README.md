# Sandboxing — Reference Edition

> **Edition R — the complete mechanism.** Every canonical fact from [`SKELETON.md`](../SKELETON.md),
> elaborated as readable prose with mechanism, `file:line`, its diagram, and its honest ceiling. This is
> the projection closest to the skeleton; the [developers](../for-developers/README.md),
> [researchers](../for-researchers/README.md), and [everyone](../for-everyone/README.md) editions link
> *into* here for depth. ✅ = implemented & enforced in code/tests · ⚙ = design/operator-action.

## Contents

- **[Part I — What it is & why](part-i-what-it-is-and-why.md)** — the confinement problem, the in-scope
  adversary, design-time-not-runtime, and the opt-in default (SB1–SB3). *Carries the pipeline graph G1.*
- **[Part II — The request](part-ii-the-request.md)** — profiles, tokens, and the manifest (SB4–SB6).
- **[Part III — The decision](part-iii-the-decision.md)** — the capability matrix, the two advisories,
  fail-closed-by-default (SB7–SB9). *Carries the decision graph G2.*
- **[Part IV — The mechanisms](part-iv-the-mechanisms.md)** — the three emitters and what each denies
  (SB10–SB13). *Carries the mechanisms graph G3.*
- **[Part V — Wiring & runtime enforcement](part-v-wiring-and-enforcement.md)** — inert-until-wired,
  verification, and the fail-open/closed hook (SB14–SB17). *Carries the hook graph G4.*
- **[Part VI — Integrity, provenance & drift](part-vi-integrity-and-drift.md)** — tamper-tracking and
  the cross-repo source of truth (SB18–SB19). *Carries the drift graph G5.*
- **[Part VII — Honest ceilings & red-team](part-vii-ceilings-and-red-team.md)** — what is verified, and
  what is not closed (SB20–SB21).
- **[Part VIII — Synthesis & reference matter](part-viii-synthesis-and-reference.md)** — the end-to-end
  flow and the quick-reference tables (SB22–SB23). *Carries the synthesis graph G6.*

## The four load-bearing ceilings (carried in every part they touch)

1. **Opt-in** — `cooperative` default: sandbox off, hook fail-open.
2. **Inert until wired** — an emitted boundary confines nothing until the operator activates/wraps it.
3. **Verified only on Linux** — the bwrap launcher is live-kernel deny-tested; macOS Seatbelt is
   enforcement-unverified; Windows has no emittable boundary.
4. **Closes nothing absolutely** — a same-host operator/key-holder (T6) and host-as-TCB stay bounded;
   seccomp/Landlock is a further layer not yet added.
