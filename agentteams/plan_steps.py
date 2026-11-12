"""Reader and writer for plan ``.steps.csv`` artifacts.

Introduced by Cluster C (typed handoffs). Tolerates quoted multi-line cells
because that is the existing convention in ``tmp/by-week/**/*.steps.csv``.
:func:`write_steps` / :func:`update_step` (#21, 2026-10-01) write through ``csv.DictWriter``
with a read-back check, so a hand-written unquoted comma can no longer shift a column.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import re
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def read_steps(path: Path | str) -> list[dict[str, str]]:
    """Read a plan steps.csv. Returns a list of dicts keyed by header column."""
    text = Path(path).read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    rows: list[dict[str, str]] = []
    for row in reader:
        if not row.get("step"):
            continue
        overflow = row.pop(None, None)
        if overflow:
            warnings.warn(
                f"{path} line {reader.line_num}: row has more fields than the "
                f"header (unassigned: {overflow!r}) — an unquoted comma or stray "
                "quote likely shifted a column; verify before trusting this row.",
                stacklevel=2,
            )
        rows.append({k: (v or "") for k, v in row.items()})
    return rows


def write_steps(path: Path | str, rows: list[dict[str, str]],
                fieldnames: list[str] | None = None) -> None:
    """Write ``rows`` as a steps CSV (quoted as needed) and verify it reads back identically.

    Args:
        path: The ``.steps.csv`` path.
        rows: Row dicts keyed by column name.
        fieldnames: Column order (default: the keys of the first row).

    Raises:
        ValueError: No rows and no ``fieldnames``, a row has a key outside ``fieldnames``, or the
            read-back differs.
    """
    if not rows and not fieldnames:
        raise ValueError("write_steps needs rows or explicit fieldnames")
    cols = list(fieldnames or list(rows[0]))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        extra = set(row) - set(cols)
        if extra:
            raise ValueError(f"row {row.get('step')!r} has columns outside the header: {sorted(extra)}")
        writer.writerow({k: row.get(k, "") for k in cols})
    Path(path).write_text(buf.getvalue(), encoding="utf-8")
    back = read_steps(path)
    expected = [{k: str(r.get(k, "") or "") for k in cols} for r in rows if r.get("step")]
    if back != expected:
        raise ValueError(f"{path}: read-back differs from what was written")


def update_step(path: Path | str, step: str, **fields: str) -> None:
    """Set ``fields`` on one step of an existing steps CSV, preserving every other cell.

    Args:
        path: The ``.steps.csv`` path.
        step: The ``step`` id to update.
        **fields: Column values to set (each column must already exist in the header).

    Raises:
        KeyError: ``step`` is not in the file.
        ValueError: A field is not a header column, or the read-back differs.
    """
    text = Path(path).read_text(encoding="utf-8")
    header = next(csv.reader(text.splitlines()))
    rows = read_steps(path)
    unknown = set(fields) - set(header)
    if unknown:
        raise ValueError(f"not header columns: {sorted(unknown)}")
    hits = [r for r in rows if r["step"] == str(step)]
    if not hits:
        raise KeyError(step)
    for row in hits:
        row.update({k: str(v) for k, v in fields.items()})
    write_steps(path, rows, header)


#: The Rule 9 contract: the columns every plan steps CSV carries (``depends_on`` is optional).
RUNTIME_COLUMNS: tuple[str, ...] = ("step", "agent", "action", "inputs", "outputs", "status", "notes")
#: The status vocabulary. Anything else is reported (warn-first) rather than silently treated as done or open.
STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "done", "blocked"})
#: Values that mean finished: ``done`` plus the legacy synonym ``completed`` (common in older plans).
DONE_STATUSES: frozenset[str] = frozenset({"done", "completed"})
#: Every value the tools accept without a warning (``completed`` being the legacy synonym of ``done``).
KNOWN_STATUSES: frozenset[str] = STATUSES | DONE_STATUSES
#: Statuses that are neither finished nor runnable now: the step's dependents wait (``runnable``).
HOLDING_STATUSES: frozenset[str] = frozenset({"blocked", "in_progress"})
#: The one ``depends_on`` / footprint splitter (whitespace, commas, semicolons); ``parallel_plan`` imports it.
DEPENDS_SPLIT_RE = re.compile(r"[\s,;]+")


def validate(path: Path | str) -> tuple[list[str], list[str]]:
    """Check a steps CSV against the Rule 9 contract.

    Errors (the file is unusable): no ``step`` column and no legacy ``step_id`` either. Warnings (warn-first, per IM item 09's
    rollback note; a later release may promote some to errors): a missing required column, a duplicate ``step``
    id, a ``depends_on`` id that names no row, a status outside :data:`STATUSES`, a row with neither ``inputs``
    nor ``outputs``, and a row with more fields than the header.

    Args:
        path: The ``.steps.csv`` path.

    Returns:
        ``(errors, warnings)``, each a list of human-readable problems.

    Raises:
        OSError: The file cannot be read.
    """
    text = Path(path).read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    header = [h.strip() for h in (reader.fieldnames or [])]
    if not header:
        return [], [f"{path}: empty file (no header row)"]
    if "step" not in header:
        if "step_id" in header:                       # the older phase/step_id schema (plan_steps_todo reads it)
            return [], [f"{path}: legacy step_id schema, not the Rule 9 columns {list(RUNTIME_COLUMNS)}"]
        return [f"{path}: no 'step' column (found: {header})"], []
    errors: list[str] = []
    warns = [f"{path}: missing required column {c!r}" for c in RUNTIME_COLUMNS if c not in header]
    seen: dict[str, int] = {}
    rows: list[dict[str, str]] = []
    for row in reader:
        sid = (row.get("step") or "").strip()
        if not sid:
            continue
        if row.get(None):
            warns.append(f"{path} line {reader.line_num}: more fields than the header (an unquoted comma?)")
        if sid in seen:
            warns.append(f"{path}: duplicate step id {sid!r}")
        seen[sid] = reader.line_num
        rows.append(row)
    for row in rows:
        sid = row["step"].strip()
        status = (row.get("status") or "").strip().lower()
        if "status" in header and status not in KNOWN_STATUSES:
            warns.append(f"{path}: step {sid!r} has status {status!r}, not one of {sorted(STATUSES)} "
                         "(or the legacy 'completed')")
        if not (row.get("inputs") or "").strip() and not (row.get("outputs") or "").strip():
            warns.append(f"{path}: step {sid!r} declares no inputs/outputs footprint (scheduled alone)")
        for dep in (d for d in DEPENDS_SPLIT_RE.split(row.get("depends_on") or "") if d):
            if dep not in seen:
                warns.append(f"{path}: step {sid!r} depends on unknown step {dep!r}")
    return errors, warns


def runnable(steps: Sequence[Any]) -> tuple[list[Any], dict[str, str], list[str]]:
    """The steps still to schedule, per Workflow 0A's "remaining ``pending`` steps" (IM item 09, G-05-5).

    * A finished row (:data:`DONE_STATUSES`) is dropped and counts as a satisfied predecessor.
    * A ``blocked`` or ``in_progress`` row (:data:`HOLDING_STATUSES`) is not scheduled: a running step is never
      dispatched twice. Nor is any step that depends on one, directly or transitively; such a step *waits*.
    * A row with an unknown status is scheduled as pending and reported.

    Args:
        steps: The plan's steps in CSV order: dataclass instances with ``step``, ``status`` and ``depends_on``
            fields (``parallel_plan.PlanStep``); ``dataclasses.replace`` rewrites ``depends_on``.

    Returns:
        ``(remaining, waiting, warnings)``: the steps to schedule (finished ids removed from their
        ``depends_on``), a map of each held or waiting step id → the held step it waits on (a held step maps
        to itself), and the warnings.

    Raises:
        TypeError: A step is not a dataclass instance.
    """
    def deps(s: Any) -> list[str]:
        return [d for d in DEPENDS_SPLIT_RE.split(s.depends_on or "") if d]

    status = {s.step: (s.status or "").strip().lower() for s in steps}
    done = {sid for sid, st in status.items() if st in DONE_STATUSES}
    warns = [f"step {sid!r} has status {st!r}, not one of {sorted(STATUSES)}; scheduled as pending"
             for sid, st in status.items() if st not in KNOWN_STATUSES]
    waiting: dict[str, str] = {sid: sid for sid, st in status.items() if st in HOLDING_STATUSES}
    changed = True
    while changed:                                   # propagate "waiting" to transitive dependents
        changed = False
        for s in steps:
            if s.step in waiting or s.step in done:
                continue
            hit = next((d for d in deps(s) if d in waiting), None)
            if hit is not None:
                waiting[s.step] = waiting[hit]
                changed = True
    remaining = [dataclasses.replace(s, depends_on=" ".join(d for d in deps(s) if d not in done))
                 for s in steps if s.step not in done and s.step not in waiting]
    return remaining, waiting, warns


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m agentteams.plan_steps --check STEPS.csv [...]``.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: 1 when any file has an error (or cannot be read), else 0; warnings never change the exit code.

    Raises:
        None.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m agentteams.plan_steps",
                                     description="Validate plan steps CSVs against the Rule 9 contract.")
    parser.add_argument("--check", nargs="+", metavar="STEPS_CSV", required=True, help="Steps CSVs to validate.")
    args = parser.parse_args(argv)
    rc = 0
    for path in args.check:
        try:
            errors, warns = validate(path)
        except OSError as exc:
            errors, warns = [f"{path}: {exc}"], []
        for e in errors:
            print(f"ERROR {e}")
        for w in warns:
            print(f"WARNING {w}")
        if errors:
            rc = 1
        elif not warns:
            print(f"OK {path}")
    return rc


__all__ = ["DEPENDS_SPLIT_RE", "DONE_STATUSES", "HOLDING_STATUSES", "KNOWN_STATUSES", "RUNTIME_COLUMNS", "STATUSES",
           "main", "read_steps", "runnable", "update_step", "validate", "write_steps"]


if __name__ == "__main__":
    raise SystemExit(main())
