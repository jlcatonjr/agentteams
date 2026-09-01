# Projection Guide — turning the sandboxing SKELETON into editions

> The rule an editor follows to keep the four editions faithful projections of one source. It mirrors
> the [Security Guide's projection method](../../agentteams-security-guide/_meta/projection-guide.md),
> narrowed to this guide.

## The one rule

**Change [`SKELETON.md`](../SKELETON.md) first, then project.** Never add a fact, drop a section, or
reorder the spine in an edition alone. The skeleton is the single source of *what is true*;
[`audience-profiles.md`](../audience-profiles.md) is the single source of *how deep and in what voice*.

## Projecting one section (`SB…`)

1. **Anchor.** Mark the section in the edition with its ID so the map and the book cross-check (a
   heading anchor `{#SB8}` or an HTML comment `<!-- skeleton:SB8 -->`).
2. **Depth.** Read the dial from the section (or the default matrix in `audience-profiles.md`): Full /
   Core / Light / Skip. Render at that depth — no shallower on a Full, no deeper facts on a Light.
3. **Facts.** State every canonical fact the dial calls for, adapted in voice — never a fact the
   skeleton does not carry, never a contradiction of one it does.
4. **Marker.** Preserve the ✅/⚙ status. Overclaiming a ⚙ control as ✅ is a fact error.
5. **Ceiling.** State the section's honest ceiling at every depth — including Light (in plain words) and
   the four **mandatory-survive ceilings** in Edition E.
6. **Diagram.** Include the section's canonical graph only in the editions `audience-profiles.md` lists
   (R: all; D: G1–G4; S: G1/G2/G6; E: none — prose + analogy, with the mandatory-ceiling sentences).

## What an edition may change

- **Depth** (dial), **Diátaxis mode** (tutorial / how-to / reference / explanation), **voice**
  (precise ↔ analogy), **presence of code / `file:line`** (R+D yes; S minimal; E none), and **which
  diagrams** it carries.

## What an edition may never change

- The **facts**, the **section structure + IDs**, the **spine order**, the **✅/⚙ markers**, and any
  **honest ceiling**. A projection that softens "inert until wired", "macOS UNVERIFIED", "opt-in by
  default", or "closes nothing absolutely" is a fact error, not a stylistic choice.

## Self-check before shipping an edition

- Every `SB…` the dial says to include is present and anchored.
- No fact appears that is not in the skeleton.
- Every ✅/⚙ marker matches the skeleton.
- Every honest ceiling for an included section is stated at the edition's depth.
- Edition E carries all four mandatory-survive ceilings as plain sentences.
- The diagrams present are exactly those `audience-profiles.md` assigns to this edition, and no diagram
  edge contradicts its section's prose.
