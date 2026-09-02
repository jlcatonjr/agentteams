# The agentteams Sandboxing Guide

A rigorous, multi-projection deep-dive into **how agentteams sandboxes the agents it generates** —
workspace write-confinement, read-exclusion, egress control, and the runtime deny-hook: how each is
**requested, decided, emitted, wired, enforced, tamper-tracked, and honestly bounded**.

This guide is a focused elaboration of **Part VI (OS confinement)** of the broader
[Security Guide](../agentteams-security-guide/README.md). It follows the same single-source method: a
[SKELETON](SKELETON.md) fixes the facts and structure once, and four editions project it at different
depths for different readers. It adds **directed graphs** (mermaid) that convey the request→enforce
pipeline, the capability decision, the three mechanisms, the fail-open/closed hook, the integrity/drift
loop, and the end-to-end synthesis.

## Start here

- **[The Map (SKELETON)](SKELETON.md)** — the single source of facts, stable section IDs (`SB1`…),
  ✅/⚙ status markers, `file:line` sources, the four load-bearing honest ceilings, and the canonical
  diagrams. Everything else projects from this.
- **[Audience Profiles](audience-profiles.md)** — how each edition renders a section (depth + voice).
- **[Sources](SOURCES.md)** — every `file:line` the facts rest on.
- **[Projection Guide](_meta/projection-guide.md)** — the rule editions follow.

## Pick your edition

| Edition | For | Depth |
|---|---|---|
| **[Reference](reference/README.md)** | a maintainer of the sandbox layer, or anyone who wants the whole mechanism precisely | Full — every fact, `file:line`, and ceiling |
| **[For Developers](for-developers/README.md)** | operators who run the CLI, pick a profile, and wrap the launcher | Core — which knob, what it costs, runnable commands |
| **[For Researchers](for-researchers/README.md)** | security reviewers new to this stack | Concept-first — the adversary, the guarantees, and their stated ceilings |
| **[For Everyone](for-everyone/README.md)** | a non-technical stakeholder | Light — a running analogy, plain-language ceilings, zero code |

## The one thing every edition keeps

The four **honest ceilings**, which no projection may drop or soften:

1. **Opt-in** — by default (`cooperative`) the sandbox is off and the deny-hook is fail-open.
2. **Inert until wired** — an emitted boundary confines nothing until the operator activates it (merges
   settings, sets `GOOSE_SANDBOX`, or wraps the process).
3. **Verified only on Linux** — the launcher's `bwrap` branch is proven by a live-kernel deny test; the
   newer macOS `build_macos` branch is emittable but enforcement-unverified (as are the native macOS
   Seatbelt paths); Windows has no emittable boundary.
4. **Closes nothing absolutely** — a same-host operator/key-holder (T6) and host-as-TCB stay bounded;
   seccomp/Landlock is a further layer not yet added.
