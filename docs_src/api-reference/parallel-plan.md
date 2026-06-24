# `parallel_plan` — AgentTeamsModule

Fail-safe parallel **wave** analysis for plan-steps CSVs: derives which independent
steps may be dispatched together (Workflow 0A) from an optional `depends_on` column,
under a conservative heuristic so under-declaration fails *safe* (sequential).

> Source: `agentteams/parallel_plan.py`

---

Activates the long-dormant dependency concept in agentteams plans. Targets the
runtime plan-steps schema (`step,agent,action,inputs,outputs,status,notes` + optional
`depends_on`) — it does **not** reuse the strict 11-column parser in
[`plan-steps-todo`](plan-steps-todo.md) (which raises on a 7-column CSV).

## Key surface

- `read_steps(csv_path)` — tolerant, header-keyed reader → `list[PlanStep]`.
- `PlanStep` — one row; exposes `dep_ids()`, `read_tokens()`, `write_tokens()`,
  `touches_shared_state()`, `has_footprint()`.
- `compute_waves(steps)` → `WaveSchedule` — Kahn layering over declared `depends_on`
  **plus** footprint-implied edges (read-after-write / write-after-write), then
  conservative refinement: shared-state denylist steps and empty/unparseable-footprint
  steps are forced to singleton waves; dependency cycles are a **blocking error**.
- `analyze_plan(csv_path)` — read + compute in one call.
- `independent_plans(csv_paths)` — cross-plan *any-order* (non-blocking) grouping by
  disjoint footprints (a scheduling note, **not** a claim of simultaneous execution).
- `to_json(schedule)` / `render_markdown(schedule)` — serialise the schedule.
- `render_skill()` — the `parallelize-plan` Claude skill (emitted via `bridge.py`,
  gated by the `parallelize` host feature).
- `main(argv)` — CLI: `python -m agentteams.parallel_plan STEPS.csv [...] [--json]`.

## Independence heuristic (conservative, fail-safe)

Two steps may share a wave only when their read/write footprints are disjoint
(path equality **or** directory/file containment counts as overlap) **and** neither
touches shared mutable state (git, databases, locks, network, servers, migrations).
Destructive / cross-repository / `--bridge-refresh` steps are never batched. See
`references/parallelization.reference.md` (emitted into every team) for the full
contract and the orchestrator's Workflow 0A.
