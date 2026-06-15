"""CSV plan-steps ↔ Claude TodoWrite projection (Phase 1).

The canonical *plan-steps CSV* is agentteams' durable plan-of-record (see
``build-team-steps.csv`` and ``references/plans/*.steps.csv``). It carries
audit-trail columns (deliverable, dependencies, evidence) that the Claude
runtime ``TodoWrite`` tool does not model.

This module provides a one-direction projection: CSV → list of Todo
items consumable by ``TodoWrite``. It also supports append-only status
writeback (mutates only the ``status`` column for a given ``step_id``,
preserves every other column byte-for-byte).

Design constraints
------------------
- CSV is canonical; TodoWrite is the projection. Structural changes
  (adding/removing steps) MUST go through the CSV. Only ``status`` may
  be mutated via :func:`update_status`.
- All file I/O uses atomic write (``write_text`` to a temp path then
  ``os.replace``) so concurrent readers see consistent rows.
- Pure stdlib; no pandas dependency.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REQUIRED_COLUMNS = (
    "phase_id",
    "step_id",
    "phase_name",
    "step_title",
    "description",
    "deliverable",
    "dependencies",
    "priority",
    "effort",
    "notes",
    "status",
)

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "done", "blocked"})

# Map CSV status → Claude TodoWrite status. "done" is a legacy synonym
# present in older CSVs; project it as "completed".
_STATUS_PROJECTION = {
    "pending": "pending",
    "in_progress": "in_progress",
    "completed": "completed",
    "done": "completed",
    "blocked": "pending",
}


@dataclass
class PlanStep:
    """A single row from a plan-steps CSV."""

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

    def to_todo(self) -> dict[str, str]:
        """Project to a Claude TodoWrite item."""
        projected_status = _STATUS_PROJECTION.get(self.status, "pending")
        content = f"{self.step_id}: {self.step_title}"
        # Active form: present-continuous of the step_title verb. Heuristic
        # only — TodoWrite tolerates any string and the orchestrator can
        # refine if needed.
        active = self.step_title
        if active and active.split()[0].lower() not in {"running", "writing", "fixing"}:
            first = active.split()[0]
            active = f"{first}ing {' '.join(active.split()[1:])}".strip() if first.isalpha() else active
        return {
            "content": content,
            "activeForm": active,
            "status": projected_status,
        }


def _validate_columns(field_names: list[str] | None, csv_path: Path) -> None:
    if not field_names:
        raise ValueError(f"plan-steps CSV {csv_path} has no header row")
    missing = [c for c in _REQUIRED_COLUMNS if c not in field_names]
    if missing:
        raise ValueError(
            f"plan-steps CSV {csv_path} is missing required columns: {missing}"
        )


def read_steps(csv_path: Path) -> list[PlanStep]:
    """Read a plan-steps CSV; return a list of :class:`PlanStep`."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"plan-steps CSV not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        _validate_columns(reader.fieldnames, csv_path)
        steps = [PlanStep(**{col: (row.get(col) or "") for col in _REQUIRED_COLUMNS}) for row in reader]
    return steps


def project_to_todos(csv_path: Path) -> list[dict[str, str]]:
    """Convenience: read CSV and return TodoWrite-shaped dicts."""
    return [step.to_todo() for step in read_steps(csv_path)]


def update_status(csv_path: Path, step_id: str, new_status: str) -> bool:
    """Mutate the ``status`` column for a single step, preserving all other
    columns and the original header order.

    Returns True if a row was updated; False if ``step_id`` not found.
    Raises ``ValueError`` on unknown status.
    """
    if new_status not in _VALID_STATUSES:
        raise ValueError(
            f"unknown status {new_status!r}; valid: {sorted(_VALID_STATUSES)}"
        )
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"plan-steps CSV not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        _validate_columns(fieldnames, csv_path)
        rows = list(reader)

    updated = False
    for row in rows:
        if row.get("step_id") == step_id:
            row["status"] = new_status
            updated = True
            break

    if not updated:
        return False

    # Atomic write: write to a sibling temp file, then os.replace.
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    fd, tmp_path = tempfile.mkstemp(
        prefix=csv_path.name + ".", suffix=".tmp", dir=str(csv_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp_fh:
            tmp_fh.write(buf.getvalue())
        os.replace(tmp_path, csv_path)
    except Exception:  # noqa: BLE001 — CH-24: cleanup-then-reraise (re-raises; hides nothing)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return True


def detect_divergence(
    csv_path: Path,
    todo_items: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Compare a TodoWrite snapshot against the canonical CSV.

    Returns a dict with three lists:

    - ``missing_in_todo``: step_ids present in CSV but absent from TodoWrite.
    - ``extra_in_todo``: content prefixes in TodoWrite that don't map to a
      known step_id.
    - ``status_mismatch``: step_ids whose projected CSV status disagrees
      with the TodoWrite status.

    Empty lists across the board ⇒ no divergence.
    """
    steps = {s.step_id: s for s in read_steps(csv_path)}
    todo_map: dict[str, dict[str, Any]] = {}
    extras: list[str] = []
    for item in todo_items:
        content = str(item.get("content", ""))
        sid, _, _ = content.partition(":")
        sid = sid.strip()
        if sid in steps:
            todo_map[sid] = item
        else:
            extras.append(content)
    missing = sorted(set(steps) - set(todo_map))
    mismatch: list[str] = []
    for sid, step in steps.items():
        if sid not in todo_map:
            continue
        projected = _STATUS_PROJECTION.get(step.status, "pending")
        if todo_map[sid].get("status") != projected:
            mismatch.append(sid)
    return {
        "missing_in_todo": missing,
        "extra_in_todo": sorted(extras),
        "status_mismatch": sorted(mismatch),
    }


_TODO_SKILL_TEMPLATE = """---
name: todo-from-plan
description: Project a canonical agentteams plan-steps CSV into Claude's TodoWrite tool. Read-only on the CSV until an explicit status writeback is requested.
bridge: copilot-vscode-to-claude
---

# Plan-steps → TodoWrite projection

When the orchestrator activates on a plan with an associated
``*.steps.csv``, project the CSV rows into ``TodoWrite`` items so the
runtime task list mirrors the canonical plan-of-record.

**Projection (read-only):**

    from agentteams.plan_steps_todo import project_to_todos
    items = project_to_todos(Path("references/plans/<plan>.steps.csv"))
    # items: list of {content, activeForm, status} ready for TodoWrite.

**Status writeback (append-only mutation):**

The CSV is canonical. Only the ``status`` column may be mutated through
``plan_steps_todo.update_status(csv_path, step_id, new_status)``.
Structural edits (adding/removing/renaming steps) must go through the
CSV directly and trigger a re-projection.

**Divergence check:**

``plan_steps_todo.detect_divergence(csv_path, todo_items)`` returns the
three-list shape used by post-production-auditor's optional
``--check-todo-divergence`` mode.
"""


def render_skill() -> str:
    """Return the Claude skill template documenting the projection."""
    return _TODO_SKILL_TEMPLATE


__all__ = [
    "PlanStep",
    "detect_divergence",
    "project_to_todos",
    "read_steps",
    "render_skill",
    "update_status",
]
