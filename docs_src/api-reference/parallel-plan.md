# `parallel_plan` — AgentTeamsModule

Fail-safe parallel **wave** analysis for plan-steps CSVs: derives which independent
steps may be dispatched together (Workflow 0A) from an optional `depends_on` column,
under a conservative heuristic so under-declaration fails *safe* (sequential).

> *Source: `agentteams/parallel_plan.py`*

---

Activates the long-dormant dependency concept in agentteams plans. Targets the
runtime plan-steps schema (`step,agent,action,inputs,outputs,status,notes` + optional
`depends_on`) — it does **not** reuse the strict 11-column parser in
[`plan-steps-todo`](plan-steps-todo.md) (which raises on a 7-column CSV). The
dict-row reader for that same runtime schema is [`plan_steps`](plan_steps.md), and
typed handoffs over those rows are validated by [`handoff_payloads`](handoff_payloads.md).

## Key surface

- `read_steps(csv_path)` — tolerant, header-keyed reader → `list[PlanStep]`.
- `PlanStep` — one row; exposes `dep_ids()`, `read_tokens()`, `write_tokens()`,
  `touches_shared_state()`, `has_footprint()`.
- `PlanFootprint` — a whole-plan footprint (`path`, union `reads`/`writes` token sets,
  `determinate` — `False` when the plan has no parseable write footprint at all).
- `footprint_edges(steps)` → `[(first, second, kind)]` — the footprint-implied facts per step
  pair: `"raw"` (read-after-write; an ordering edge) or `"ww"` (write-write; a tie that only
  forbids sharing a wave). Shared with [`plan_priority`](plan-priority.md).
- `compute_waves(steps, priority=None)` → `WaveSchedule` — Kahn layering over declared `depends_on`
  **plus** footprint-implied edges (read-after-write; write-write ties serialized in CSV order, or
  by `priority` rank when a map from `plan_priority.priority_map` is given), then
  conservative refinement: shared-state denylist steps and empty/unparseable-footprint
  steps are forced to singleton waves; dependency cycles are a **blocking error**.
- `analyze_plan(csv_path)` — read + compute in one call.
- `plan_footprint(csv_path)` → `PlanFootprint` — union read/write footprint for a whole
  plan; the building block `independent_plans()` is built on.
- `independent_plans(csv_paths)` — cross-plan *any-order* (non-blocking) grouping by
  disjoint footprints (a scheduling note, **not** a claim of simultaneous execution; file paths
  only — shared mutable state is not compared). Workflow 0C metaplans use it to find the
  ordering edges between item plans.
- `to_json(schedule)` / `render_markdown(schedule)` — serialise the schedule.
- `render_skill()` — the `parallelize-plan` Claude skill (emitted via `bridge.py`,
  gated by the `bridge:copilot-vscode-to-claude:parallelize` host-feature token; no
  direct/non-bridge consumer exists).
- `main(argv)` — CLI: `python -m agentteams.parallel_plan STEPS.csv [...] [--json] [--priority] [--all]`. By default
  only the remaining steps are scheduled ([`plan_steps.runnable`](plan_steps.md)). `done` rows are dropped and
  satisfy their dependents. A `blocked` row and its dependents wait, and are reported in `warnings` instead of being
  placed in a wave. `--all` schedules every row, as before. A CSV without a `step` column is an `Error:` line and
  exit 1, not a traceback.
  `--priority` ranks the plan with [`plan_priority`](plan-priority.md), lists each wave's members
  in rank order and breaks write-write ties by rank; it exits 2 when the plan's ratings, ids or
  dependencies fail `plan_priority`'s validation.

## Independence heuristic (conservative, fail-safe)

Two steps may share a wave only when their read/write footprints are disjoint
(path equality **or** directory/file containment counts as overlap) **and** neither
touches shared mutable state (git, databases, locks, network, servers, migrations).
Destructive / cross-repository / `--bridge-refresh` steps are never batched. See
`references/parallelization.reference.md` (emitted into every team) for the full
contract and the orchestrator's Workflow 0A.
