# `plan_steps_todo`

CSV plan-steps ↔ Claude TodoWrite projection. The canonical plan-steps CSV remains the audit-bearing plan-of-record; this module projects it into TodoWrite-shaped dicts for runtime visibility in Claude. Status writeback is **append-only** and mutates only the `status` column — every other column's values and the column order are preserved (the CSV is re-serialized with minimal quoting) via atomic write.

Opt-in (for the bridge skill artifact) via [`--target-host-features bridge:copilot-vscode-to-claude:todo-projection`](host-features.md). The module is importable regardless of the host-feature flag; the flag only gates emission of the `.claude/skills/todo-from-plan.md` skill file.

## Required CSV Columns

`phase_id, step_id, phase_name, step_title, description, deliverable, dependencies, priority, effort, notes, status`

## Status Projection

| CSV `status` | TodoWrite `status` |
|---|---|
| `done`, `completed` | `completed` |
| `in-progress`, `in_progress`, `wip` | `in_progress` |
| *(other / blank)* | `pending` |

## Public Surface

```python
@dataclass
class PlanStep:
    phase_id: str
    step_id: str
    phase_name: str
    step_title: str
    description: str
    deliverable: str
    dependencies: str
    priority: str
    effort: str
    notes: str
    status: str
    def to_todo(self) -> dict[str, str]: ...
```

```python
read_steps(csv_path: Path) -> list[PlanStep]
```
Read a plan-steps CSV. Raises `FileNotFoundError` if missing or `ValueError` if required columns are absent.

```python
project_to_todos(csv_path: Path) -> list[dict[str, str]]
```
Convenience: read CSV and return TodoWrite-shaped dicts with `content`, `activeForm`, `status` keys.

```python
update_status(csv_path: Path, step_id: str, new_status: str) -> bool
```
Mutate the `status` column for a single step, preserving header order and all other column values. The file is re-serialized with minimal quoting, so it is not necessarily byte-identical to the original. Atomic via `os.replace`. Returns `True` if updated; `False` if `step_id` not found. Raises `ValueError` for unknown statuses.

```python
detect_divergence(
    csv_path: Path,
    todo_items: list[dict[str, Any]],
) -> dict[str, list[str]]
```
Compare a TodoWrite snapshot against the canonical CSV. Returns `{missing_in_todo, extra_in_todo, status_mismatch}`. The orchestrator uses this to decide whether to refresh the projection.

```python
render_skill() -> str
```
Return the contents of the `todo-from-plan.md` skill file. Pure function — no filesystem access.

## Contract

- The **CSV is canonical.** Structural changes (adding/removing/renaming steps) must go through the CSV; the projection picks them up on the next read.
- The **TodoWrite projection is read-mostly.** The only writeback is `update_status`. All other TodoWrite fields are recomputed on every projection.
- Column order and all non-`status` values in the CSV are preserved across writebacks — `update_status` re-serializes the header and every other row with minimal quoting (values and column order preserved, not necessarily byte-identical).
