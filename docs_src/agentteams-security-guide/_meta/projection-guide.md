# Projection guide — the skeleton-first edit workflow

**Cardinal rule.** `SKELETON.md` is the single source of structure and facts. **Every change starts at
`SKELETON.md`, then is projected into each edition.** Never add a fact, drop a section, reorder the
spine, or soften an honest ceiling in an edition alone.

## Two kinds of change

### A. A fact changed / added / corrected
1. Edit the canonical fact in `SKELETON.md` (and its `Source`; update `SOURCES.md`).
2. For each edition whose dial ≠ `Skip`, re-project the new fact at that edition's depth/mode.
3. Run `check-skeleton.py` (structure) + re-read the touched sections across editions (facts).
4. If it is a ✅/⚙ status change **or an honest-ceiling change**, update **every** edition — overclaiming
   design-as-implemented, or dropping a ceiling, is the highest-severity error class.

### B. Structure changed (add / rename / reorder / split)
1. Edit `SKELETON.md`: add the ID + facts + dials (keep IDs **stable**; renaming a *title* is fine,
   changing an *ID* ripples to every edition marker) and update the closing `## Section index`.
2. `python3 _meta/scaffold.py <edition>` for each edition to insert the stub in place.
3. Author the new section in each non-`Skip` edition.
4. `python3 _meta/check-skeleton.py` → must exit 0 before commit.

## Why structure is script-checked but facts are audited

Structure (which sections exist, in which edition) is mechanical, so `check-skeleton.py` enforces it.
Facts require judgment and are kept honest by a **verification pass** — `@technical-validator` (every
`Source` resolves on disk; every ✅ is real), `@adversarial` (are the ceilings honest? any overclaim?),
`@conflict-auditor` (no edition contradicts the skeleton) — run on the **skeleton first**, because a
wrong map would propagate into four books. Audit the map before authoring the editions.

## Section-ID markers in edition files

Mark each rendered section with its ID so the checker can find it — any of:
`## Title {#S18}` · `<!-- skeleton:S18 -->` · `[S18]`. The `{#Sx}` heading-anchor form is preferred.
