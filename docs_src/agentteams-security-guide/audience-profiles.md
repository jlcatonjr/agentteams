# Audience Profiles — how each edition projects the skeleton

> This file defines **how to project** a `SKELETON.md` section into each edition. The skeleton fixes
> *what* a section asserts (canonical facts) and *where* it sits (stable ID). This file fixes *how deep*
> and *in what voice* each edition renders it. Together they guarantee: the same fact, in the same
> section, adapted — never a different fact.

## The projection principle

```
SKELETON.md  ─────────────▶  Edition R / D / S / E
(facts + structure,          (same facts, same section IDs,
 one source of truth)         rendered at this edition's depth + mode)
```

An edition may render a section shallower, in a different Diátaxis mode, with or without code — but it
may **not** assert a fact the skeleton does not carry, drop a section the skeleton defines, reorder the
spine, or soften an **honest ceiling**. `_meta/check-skeleton.py` enforces the structural half.

## Method

1. **Diátaxis** — four modes: **Tutorial** (learning), **How-to** (task), **Reference** (information),
   **Explanation** (understanding). Each edition has a dominant mode-mix.
2. **Progressive disclosure** — novices get the minimum that is true and useful first, depth deferred
   behind a link; experts get completeness up front. This sets the *depth dial*.

## The depth dial

| Dial | Meaning |
|---|---|
| **Full** | every canonical fact in the section, elaborated, with mechanism, `file:line`, and its honest ceiling |
| **Core** | the load-bearing facts + the ceiling; secondary detail linked to Edition R |
| **Light** | one-paragraph gist + analogy; no code, no `file:line` (the ceiling stays, in plain words) |
| **Skip** | omitted in this edition (a cross-reference line may remain) |

## The four editions

### Edition R — `reference/` — "the complete picture"
- **Reader:** a maintainer of agentteams' security infrastructure, or a thorough reader who wants the
  whole stack precisely.
- **Diátaxis mix:** Reference + Explanation (complete + reasoned).
- **Default depth:** Full on every section.
- **Format:** precise prose, `file:line` refs, tables, every honest ceiling stated, ✅/⚙ markers preserved.
- **Role:** the projection closest to the skeleton; the other three link *into* it for depth.

### Edition D — `for-developers/` — "operators & integrators who run agentteams"
- **Reader:** someone who runs the CLI, configures profiles, issues waivers/grants, and reads code.
- **Diátaxis mix:** How-to + Reference (+ a little Tutorial).
- **Default depth:** Core→Full on the controls they operate (the triad, the gates, profiles, backups);
  Light on motivation/history.
- **Format:** runnable commands (`agentteams --verify-waivers`, `--issue-grant`, `--capture-baseline`,
  `--redteam`), `file:line` pointers, a **"which knob do I turn, and what does it cost"** orientation.

### Edition S — `for-researchers/` — "security reviewers & threat modelers, new to this stack"
- **Reader:** a security researcher/auditor who knows threat modeling but not agentteams' internals.
- **Diátaxis mix:** Explanation + Reference (the *why*, the threat model, the guarantees and their ceilings).
- **Default depth:** Core→Full, concept-first — teach the threat model (Excessive Agency, prompt
  injection, content-is-data) and the trust boundaries before using the mechanism names.
- **Format:** the adversary and each control's *stated ceiling* foregrounded; the red-team methodology
  and the two-surfaces distinction get Full; minimal code.

### Edition E — `for-everyone/` — "non-technical"
- **Reader:** a stakeholder or curious reader with no security background.
- **Diátaxis mix:** Explanation only (narrative + analogy).
- **Default depth:** Light; the deepest mechanism sections are Skipped or told purely by analogy.
- **Format:** a consistent running analogy (a constitution, a guard who can say "stop," signed permission
  slips, a locked room, a tamper-evident seal), plain language, zero code, zero commands. The ceilings
  are still told — "this makes tampering obvious and costly, it does not make it impossible."
- **Mandatory-survive ceilings (must appear in E, in plain words — analogy alone is not enough).** The
  plain-language edition may simplify anything *except* these, which mislead a stakeholder if lost:
  1. **"None of this runs inside the app you ship."** The guard is at *design/build* time; it is not in
     the delivered product. A reader must not conclude "our app is protected at runtime." (S1.3, S25.3.)
  2. **"By default the strongest locks are off."** OS confinement and the deny-hook are opt-in
     (`confined`/`exclusive`); the default profile leaves them dormant. (S1.5, S18, S19.)
  3. **"A signed slip stops an outsider, not someone who holds the signing key."** Symmetric signing is
     not unforgeable. (S6, S7.)
  4. **"Verified on Linux; on Macs it is advice, not a proven lock; Windows has no lock."** (S18.)
  State each as a plain sentence, not only as analogy. An E section that drops one is a fact error.

## Depth-by-edition default matrix (per-section overrides live in SKELETON.md)

| Section theme | R | D | S | E |
|---|---|---|---|---|
| What it is & why (S1–S2) | Full | Core | Full | Light |
| Constitution & authority (S3–S6) | Full | Core/Full | Full | Light |
| Clearance / waivers / grants (S7–S10) | Full | Full | Core | Light |
| The gates (S11–S14) | Full | Full | Core | Light |
| Content safety (S15–S16) | Full | Core | Core | Light |
| OS confinement (S17–S19) | Full | Full | Core | Light |
| Threat intel & red team (S20–S21) | Full | Core | Full | Light |
| Integrity / provenance / recovery (S22–S24) | Full | Full | Core | Light |
| Synthesis (S25) | Full | Core | Full | Light |
| Glossary (S26) | Full | Core | Full | Light |
| Sources (S27) | Full | Light | Light | Skip |

## Sources

- Diátaxis framework — the four documentation modes: <https://diataxis.fr/>.
- Progressive disclosure / cognitive-load management — a standard technical-communication principle,
  applied here as the depth dial.
