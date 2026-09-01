# agentteams Security Infrastructure — a handbook

A handbook that explains **(1) what agentteams' security infrastructure is for** and **(2) how its
layers work and compose into defense-in-depth** — written as **four editions of one book**, each for a
different reader, all projected from a single shared outline so they never disagree on the facts.

> **Status:** the outline (skeleton) is set. If you are reviewing the structure, start with
> [`SKELETON.md`](SKELETON.md) — the map everything else follows; otherwise jump to your edition below.

## The four editions

| Edition | For | How it reads |
|---|---|---|
| [`reference/`](reference/) | the complete picture / maintainers | full depth, precise, every section, every ceiling |
| [`for-developers/`](for-developers/) | operators & integrators who run agentteams | task-oriented, runnable commands, "which knob and what it costs" |
| [`for-researchers/`](for-researchers/) | security reviewers & threat modelers | threat-model-first, the guarantees and their honest ceilings |
| [`for-everyone/`](for-everyone/) | non-technical | plain-language narrative, analogy, no code |

Pick the one that fits you; each links into `reference/` for more depth.

## How this handbook is built (and why the editions agree)

This is a **single-source, multi-projection** handbook:

```
SKELETON.md  ──▶  reference/   for-developers/   for-researchers/   for-everyone/
(the map:            (same section IDs, same facts, rendered at each edition's depth + voice)
 structure + facts)
```

- **[`SKELETON.md`](SKELETON.md)** — the map. Every section has a stable ID (`S3`, `S18`), the
  **canonical facts** it asserts, the **source** each fact rests on, a ✅/⚙ status marker, and a
  per-edition **depth dial**. The single place a fact or the structure may change.
- **[`audience-profiles.md`](audience-profiles.md)** — *how* each edition projects a section (depth +
  Diátaxis mode).
- **[`SOURCES.md`](SOURCES.md)** — provenance: each canonical claim → the repo file it comes from.
- **[`_meta/`](_meta/)** — the machinery that keeps the editions honest:
  - [`projection-guide.md`](_meta/projection-guide.md) — the skeleton-first edit workflow.
  - [`scaffold.py`](_meta/scaffold.py) — projects the skeleton into an edition's heading tree.
  - [`check-skeleton.py`](_meta/check-skeleton.py) — a conformance gate: fails if any edition drifts
    from the map (missing/extra/wrongly-present sections). Run it before committing edits.

The upshot: a fact is stated **once**, in the skeleton, and every edition renders that same fact at its
own depth. You cannot fix a claim in one book and forget the others.

## The one rule that shapes every page: the honest-ceiling doctrine

Every control is described with **what it buys and what it cannot**. A boundary "engages as tested,"
never "is secure." A signed ledger stops a *keyless* forger, not a key-holder. An emitted sandbox is
inert until an operator wires it, and is empirically verified on Linux — macOS Seatbelt is unverified. Overstating a ceiling is a
fact error — in every edition, including the plain-language one.

## What the handbook covers (the section map at a glance)

What it is & why (the Excessive-Agency threat model; two surfaces) · the constitution (C-1..C-5) &
instruction-authority ordering · the `@security` sentinel, HALT & capability limits · the
clearance/waiver/grant triad · the destructive & intel-freshness gates, shrink-policy, bridge-refresh
safety · the content scanner & feed sanitization · OS confinement (L0–L7 model, sandbox profiles, the
PreToolUse hook) · threat intelligence & the red-team cycle · integrity manifests, provenance, backups
& baselines · a synthesis of how the layers compose · glossary · sources. Full detail in
[`SKELETON.md`](SKELETON.md).

## For maintainers

Every claim is marked ✅ *implemented & enforced* or ⚙ *design/procedural*, and carries a source — no
fabricated facts. Edit via the skeleton; keep `check-skeleton.py` green.
