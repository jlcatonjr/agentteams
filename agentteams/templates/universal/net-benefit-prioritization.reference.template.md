<!--
SECTION MANIFEST
| section_id               | designation   |
|--------------------------|---------------|
| net_benefit_rubric       | FENCED        |
| project_anchors          | USER-EDITABLE |
-->

<!-- AGENTTEAMS:BEGIN net_benefit_rubric v=1 -->
# Net-Benefit Prioritization Reference — {PROJECT_NAME}

How this team decides **which item to do first** when a plan or metaplan holds two or more
development or remediation items. Dependency order (`depends_on`, Workflow 0A waves) says what
*may* run; this rubric says what is *worth* running first. It is the procedure behind the
prioritization clause of the plan rule (Rule 9 in the instructions file, Rule 10 in the
orchestrator) and behind Workflow 0C steps 1, 3 and 6.

Every item gets four ratings. The order is computed from them, never set by hand.

## Ratings

**Severity (S, 1–5): how bad is it to leave the gap open?**

| S | Meaning | Example |
|---|---|---|
| 5 | A safety or authority claim is false: a HALT, `tools:` limit, signature, gate or trust root can be bypassed, or the documentation promises prevention that does not happen | a gate fails open on a surface the docs say it covers |
| 4 | A shipped mechanism gives wrong results or silently does nothing | a checker that passes on malformed input |
| 3 | The mechanism exists but is not wired, not emitted by default, or cannot be verified | template text with no script, hook or test behind it |
| 2 | Inconsistency or drift between docs, templates and code; no wrong action follows | the docs name a flag the CLI renamed |
| 1 | Cosmetic or editorial | wording, ordering |

**Breadth (B, 1–5): how much does it touch?**

| B | Meaning |
|---|---|
| 5 | Every user, team or surface the project serves |
| 4 | Every user of one surface, or a project-wide mechanism (update/merge, a shared pipeline) |
| 3 | A class of users or deployments |
| 2 | One opt-in feature or module |
| 1 | Maintainers only (internal tooling, tests, the build) |

**Cost (C, 1–5): what does remediation take?**

| C | Effort | Cost points |
|---|---|---|
| 1 | Doc or template line; under an hour | 1 |
| 2 | Small code change with tests; under half a day | 2 |
| 3 | One module or feature; about a day | 4 |
| 4 | Several modules, a migration or a re-render; several days | 8 |
| 5 | Cross-surface redesign, or needs operator signing / an external provider change | 16 |

Cost points double per tier, because effort and risk grow roughly geometrically.

**Confidence (p, 0.25–1.0, default 1.0):** the probability that the planned remediation actually
closes the gap. Lower it when the fix depends on a provider, the operator, or an unproven design.

## Formula

```
benefit          = S × B                    (1..25)
expected_benefit = p × S × B
net_benefit      = expected_benefit − cost_points(C)
```

**Order.** Items are ordered by band, then by `net_benefit` descending. Ties go to the higher S,
then the lower C, then the lower item id compared as a number (`S2` before `S10`).

**Bands:** `do-now` net ≥ 10; `next` 4 ≤ net < 10; `later` 0 ≤ net < 4; `reconsider` net < 0
(cost exceeds expected benefit: redesign the fix, narrow it, or record the gap as accepted).

**Safety floor.** An item with S = 5 whose net is below 4 is placed at the **head of the `next`
band** (after every `do-now` item, before every other `next` item), with a note that a cheaper
partial fix must be looked for. The floor changes the order, not only the label: a false safety
claim cannot be outranked by cheap cosmetic work or "accepted" by arithmetic.

**Dependencies win.** Ranking never reorders across an edge. The graph is the declared
`depends_on` plus the implicit read-after-write edges the parallelization analyzer derives from
`inputs`/`outputs`. An item's **effective rank** is the best rank of the item itself and of any item
that depends on it, directly or transitively (priority inheritance), so a cheap prerequisite of a
top item is not starved.

**Pins.** Only the operator may pin an item ahead of the computed order. A pin is a `pin` column
value `operator:<YYYY-MM-DD>:<reason>`; pinned items go first, ordered by pin date and then CSV
order, and the report lists every pin. **Agents never pin.** The ranker checks a pin's form only; it
cannot tell who wrote it. So the plan file records, for every pin, the operator instruction it came
from, and the plan's `@adversarial` audit reports any pin with no such source as a finding.

**Closing rows.** Integration and closeout rows carry `kind=closing`. They are not ranked and
stay last, in CSV order unless an edge between two of them says otherwise. A non-closing row with no ratings is a warning (an error under
`--require`); it is never ranked and is never silently moved ahead of a rated item. It runs before
a rated item only when that item depends on it.

**Known limits.** The scales are ordinal and the product treats them as ratios, so S5·B1 equals
S1·B5; ties of that kind are broken toward severity. Where a fix needs operator signing, reflect
that once, in C; do not also lower p for the same reason.

## Who rates, and when ratings freeze

1. The item's author proposes S, B, C and p, each with a **one-line rationale**. A rating with no
   rationale is not accepted.
2. The plan's `@adversarial` audit checks every rating against its rationale and the anchors
   above, and recommends a revision where one does not hold. `@adversarial` is read-only: the
   orchestrator applies an accepted revision after re-checking its evidence (Rule 14).
3. The ratings **and the `depends_on` edges between rated items** are **frozen** when the plan's
   pre-execution audit closes (for a metaplan, at the end of Workflow 0C step 5). Any later change
   is recorded in the item's `notes` with the old value, the new value and the reason. Edges are
   frozen too because priority inheritance lets a new edge from a top item lift any item it points
   at. This keeps an agent from re-rating or re-wiring its own work to move it up the queue.

## Columns

A plan-steps or metaplan CSV adds these columns to the required seven:
`severity`, `breadth`, `cost`, `confidence`, the rationales `rationale_s`, `rationale_b`,
`rationale_c` (and `rationale_p` when p is set) and, where used, `kind` (`closing`) and `pin`.
A standalone backlog CSV may use `id` instead of `step`. Under `--require`, a CSV with rated rows
but no rationale columns, or a rated row with an empty rationale, is an error. Computed values (`expected_benefit`, `cost_points`, `net_benefit`,
`band`, `rank`, `effective_rank`) belong in the report, not the CSV. If a CSV carries them, the
ranker checks them against its recomputation and reports any mismatch.

## The ranker

```
python -m agentteams.plan_priority tmp/by-week/<week>/<plan>.steps.csv            # ranked table
python -m agentteams.plan_priority tmp/by-week/<week>/<plan>.steps.csv --json     # machine form
python -m agentteams.plan_priority tmp/by-week/<week>/<plan>.steps.csv --require  # pre-dispatch / CI gate
python -m agentteams.parallel_plan tmp/by-week/<week>/<plan>.steps.csv --priority # waves in rank order
```

It is **compute-only**: it never writes the CSV. Exit status 0 means the order is usable, 2 means
a rating failed validation or the dependencies form a cycle (every bad row is listed), and 1 means
the file is missing or has no `step`/`id` column. `parallel_plan --priority` lists each wave's members in rank order and breaks
write-write conflicts in favour of the higher-ranked step instead of the CSV-earlier one, so it can
change which items share a wave. Where the module is unavailable, apply this rubric by hand: the
duty in the plan rule does not depend on the tool.
<!-- AGENTTEAMS:END net_benefit_rubric -->

## Project anchors

> ⚙️ **USER-EDITABLE** — re-anchor the scale *descriptions* above for this project (what counts as
> breadth 5 here, what a cost-3 change looks like). The scales, the cost points, the formula, the
> bands, the safety floor and the pin rule are framework defaults, set in the fenced section above.
> `--update` keeps this section as the project wrote it.
