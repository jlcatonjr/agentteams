# `plan_priority` — AgentTeamsModule

Orders the development and remediation items of a plan or metaplan by **expected net
benefit**, subject to their dependencies. It is the executable form of
`references/net-benefit-prioritization.reference.md`, which every team receives, and of
the prioritization clause in the plan rule (Rule 9 in the instructions file, Rule 10 in the
orchestrator).

> *Source: `agentteams/plan_priority.py`*

---

Each item is rated for severity S, breadth B and cost C, each from 1 to 5, plus an
optional confidence p from 0.25 to 1.0:

```
cost_points(C)   = 1, 2, 4, 8, 16      for C = 1..5
expected_benefit = p × S × B
net_benefit      = expected_benefit − cost_points(C)
```

**Bands.**
- `do-now`: net ≥ 10.
- `next`: net ≥ 4.
- `later`: net ≥ 0.
- `reconsider`: net below 0.
- **Safety floor:** an S=5 item with net below 4 goes at the head of `next`.

Operator pins (`operator:<YYYY-MM-DD>:<reason>`) come first, ordered by pin date. Ties go to
the higher S, then the lower C, then the lower id compared as a number.

**What the module does.**
- It is **compute-only**: it never writes the CSV, which is the same contract as
  [`parallel_plan`](parallel-plan.md).
- Ranking never reorders across an edge. The graph is the declared `depends_on` plus the
  implicit read-after-write edges from `parallel_plan.footprint_edges`.
- Priority inheritance gives a prerequisite the best rank of itself and of anything that depends
  on it.
- `kind=closing` rows are never ranked and stay last, in CSV order unless an edge between two of
  them says otherwise.
- An unrated work row draws a warning (an error under `require`). It is never ranked, and it
  is ordered after every rated item unless a rated item depends on it.

## Key surface

- `read_rows(csv_path)` → `(rows, header)`. A tolerant, header-keyed reader. The key column is
  `step`, or `id` when the CSV has no `step` column (backlog CSVs).
- `rank_rows(rows, header=None, require=False)` → `PriorityReport`. It rates, validates, ranks,
  applies inheritance and returns the items in execution order.
- `analyze_priority(csv_path, require=False)` reads and ranks in one call.
- `priority_map(report)` → `{step: order}`, to pass as `parallel_plan.compute_waves(steps, priority=...)`.
- `PriorityItem` holds one row:
  - ratings: `severity`, `breadth`, `cost`, `confidence`, `pin`, `kind`;
  - computed values: `expected_benefit`, `cost_points`, `net_benefit`, `band`, `rank`,
    `effective_rank`, `order`.
- `PriorityReport` has `items`, `pins`, `warnings` and `errors`. If `errors` is not empty, the
  order must not be used for dispatch.
- `score(S, B, C, p)` and `band_for(net, S)` are the formula and the band rule.
  `natural_key(id)` is the numeric tie-break.
- `to_json(report, plan)` and `render_markdown(report)` serialise a report.
- `main(argv)` is the CLI.

## Validation

**Always errors** (exit 2; every bad row is listed):
- S, B or C outside the integers 1–5, or p outside 0.25–1.0;
- a partly rated row;
- a pin that is not `operator:<date>:<reason>`;
- a pin on an unrated or closing row;
- a duplicate id;
- a dependency cycle;
- a work row that depends on a closing row.

**Errors only under `require`** (warnings otherwise):
- an unrated work row;
- rated rows with no `rationale_s`/`_b`/`_c` columns, or a rated row with an empty rationale cell;
- a computed column already in the CSV that does not match the recomputed value.

## CLI

```
python -m agentteams.plan_priority PLAN.steps.csv [MORE.csv ...] [--json] [--require]
python -m agentteams.parallel_plan PLAN.steps.csv --priority
```

`--json` gives `{plan, items, pins, warnings, errors}` for one CSV, and a list of those objects
for several. Exit status:
- 0: the order is usable;
- 1: a file is missing or has no key column;
- 2: a validation error or a cycle.

`parallel_plan --priority` lists each wave's members in rank order, and breaks write-write
conflicts in favour of the higher-ranked step instead of the CSV-earlier one. That can change
which steps share a wave. Without the flag the schedule is unchanged.
