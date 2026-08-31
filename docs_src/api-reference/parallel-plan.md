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
- `compute_waves(steps)` → `WaveSchedule` — Kahn layering over declared `depends_on`
  **plus** footprint-implied edges (read-after-write / write-after-write), then
  conservative refinement: shared-state denylist steps and empty/unparseable-footprint
  steps are forced to singleton waves; dependency cycles are a **blocking error**.
- `analyze_plan(csv_path)` — read + compute in one call.
- `plan_footprint(csv_path)` → `PlanFootprint` — union read/write footprint for a whole
  plan; the building block `independent_plans()` is built on.
- `independent_plans(csv_paths)` — cross-plan *any-order* (non-blocking) grouping by
  disjoint footprints (a scheduling note, **not** a claim of simultaneous execution).
- `to_json(schedule)` / `render_markdown(schedule)` — serialise the schedule.
- `render_skill()` — the `parallelize-plan` Claude skill (emitted via `bridge.py`,
  gated by the `bridge:copilot-vscode-to-claude:parallelize` host-feature token; no
  direct/non-bridge consumer exists).
- `main(argv)` — CLI: `python -m agentteams.parallel_plan STEPS.csv [...] [--json]`.

## Independence heuristic (conservative, fail-safe)

Two steps may share a wave only when their read/write footprints are disjoint
(path equality **or** directory/file containment counts as overlap) **and** neither
touches shared mutable state (git, databases, locks, network, servers, migrations).
Destructive / cross-repository / `--bridge-refresh` steps are never batched. See
`references/parallelization.reference.md` (emitted into every team) for the full
contract and the orchestrator's Workflow 0A.
