"""Reader and writer for plan ``.steps.csv`` artifacts.

Introduced by Cluster C (typed handoffs). Tolerates quoted multi-line cells
because that is the existing convention in ``tmp/by-week/**/*.steps.csv``.
:func:`write_steps` (#21, 2026-10-01) writes through ``csv.DictWriter`` and checks the text reads
back before it replaces the file, so a hand-written unquoted comma can no longer shift a column.
:func:`update_step` and :func:`set_step` change one row and leave every other byte as it was.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import os
import re
import shutil
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def read_steps(path: Path | str) -> list[dict[str, str]]:
    """Read a plan steps.csv. Returns a list of dicts keyed by header column."""
    return _parse_steps(Path(path).read_text(encoding="utf-8"), path)


def _parse_steps(text: str, path: Path | str) -> list[dict[str, str]]:
    """Parse steps-CSV *text* the way :func:`read_steps` does; *path* only labels the warning."""
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
    """Write ``rows`` as a whole steps CSV (quoted as needed), replacing any file at ``path``.

    The text is checked before the file is touched: it must read back as the rows given. The
    write is a rename, so a refusal or a failure leaves an existing file as it was. A file that
    already uses CRLF line endings keeps them; a symlink stays a link. To change one step of an
    existing table use :func:`update_step` or :func:`set_step`, which keep every other row.

    Args:
        path: The ``.steps.csv`` path.
        rows: Row dicts keyed by column name.
        fieldnames: Column order (default: the keys of the first row).

    Raises:
        ValueError: No rows and no ``fieldnames``, a row has a key outside ``fieldnames``, or the
            text would not read back as the rows given. Nothing is written.
        OSError: The file cannot be written.
    """
    if not rows and not fieldnames:
        raise ValueError("write_steps needs rows or explicit fieldnames")
    cols = list(fieldnames or list(rows[0]))
    target = Path(path).resolve()
    ending = "\n"
    if target.is_file():
        with target.open("rb") as fh:
            ending = "\r\n" if fh.readline().endswith(b"\r\n") else "\n"
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator=ending)
    writer.writeheader()
    for row in rows:
        extra = set(row) - set(cols)
        if extra:
            raise ValueError(f"row {row.get('step')!r} has columns outside the header: {sorted(extra)}")
        writer.writerow({k: row.get(k, "") for k in cols})
    expected = [{k: str(r.get(k, "") or "") for k in cols} for r in rows if r.get("step")]
    if _parse_steps(buf.getvalue(), path) != expected:
        raise ValueError(f"{path}: the table would not read back as the rows given; nothing was written")
    _replace_text(target, buf.getvalue())


def _replace_text(target: Path, text: str) -> None:
    """Write *text* to *target* by rename, keeping the mode of a file already there."""
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if target.exists():
            shutil.copymode(target, tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def update_step(path: Path | str, step: str, **fields: str) -> None:
    """Set ``fields`` on one step of an existing steps CSV by rewriting that row only.

    Every other byte of the file is kept: line endings, blank lines, quoting, and rows that are
    themselves malformed. Any column may be set and the values are not checked against a
    vocabulary; :func:`set_step` is the form that checks a status. Before 1.4.0 this rewrote
    the whole table, which converted CRLF line endings, dropped cells from rows wider than the
    header, and set every row that shared the id.

    Args:
        path: The ``.steps.csv`` path.
        step: The ``step`` id to update, compared with surrounding spaces ignored. It must name
            exactly one row.
        **fields: Column values to set (each column must already exist in the header).

    Raises:
        KeyError: ``step`` is not in the file.
        ValueError: A field is not a header column, a duplicated ``step`` id, a header with a
            repeated column, a target row wider than the header, or a result that does not
            read back as intended. Nothing is written.
        OSError: The file cannot be read or written.
    """
    if not fields:
        return
    _set_cells(path, step, {k: str(v) for k, v in fields.items()}, unknown="not header columns: {names}")


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


def _records(text: str) -> tuple[list[str], list[tuple[int, int, list[str]]]]:
    """Physical lines of *text* (endings kept) and its CSV records as ``(first_line, end_line, cells)``."""
    lines = list(io.StringIO(text, newline=""))
    reader = csv.reader(iter(lines))
    records: list[tuple[int, int, list[str]]] = []
    prev = 0
    for cells in reader:
        records.append((prev, reader.line_num, cells))
        prev = reader.line_num
    return lines, records


def set_step(path: Path | str, step: str, *, status: str | None = None, note: str | None = None,
             append_note: str | None = None) -> dict[str, str]:
    """Set one step's status and/or notes by rewriting that row only.

    The command-line form of a step update: it exists so that a session marks a step with one
    command instead of writing its own CSV-editing script. It replaces the physical lines of the
    one row and leaves every other byte of the file as it was: line endings, blank lines, quoting, and rows that are
    themselves malformed. The new text is checked before anything is written, and the write is
    a rename, so a refusal or a failure leaves the file untouched.

    Args:
        path: The ``.steps.csv`` path.
        step: The ``step`` id to update, compared with surrounding spaces ignored. It must name
            exactly one row.
        status: New status. Must be one of :data:`STATUSES`; the legacy ``completed`` is not written.
        note: Text that replaces the step's ``notes``.
        append_note: Non-empty text added after the step's existing ``notes``, separated by ``"; "``.

    Returns:
        The updated row, keyed by header column.

    Raises:
        KeyError: ``step`` is not in the file.
        ValueError: Nothing to set, both ``note`` and ``append_note`` given, an empty appended
            note, a status outside the vocabulary, a duplicated ``step`` id, a header with a
            repeated or missing column, a target row wider than the header, or a result that
            does not read back as intended.
        OSError: The file cannot be read or written.
    """
    if status is None and note is None and append_note is None:
        raise ValueError("nothing to set: give a status, a note, or both")
    if note is not None and append_note is not None:
        raise ValueError("give either a replacement note or an appended note, not both")
    if append_note is not None and not append_note.strip():
        raise ValueError("the appended note is empty")
    changes: dict[str, str] = {}
    if status is not None:
        value = status.strip().lower()
        if value not in STATUSES:
            hint = " (write 'done'; 'completed' is read as a legacy synonym only)" if value == "completed" else ""
            raise ValueError(f"status {status!r} is not one of {sorted(STATUSES)}{hint}")
        changes["status"] = value

    if note is not None:
        changes["notes"] = note
    return _set_cells(path, step, changes, append_note=append_note)


def _set_cells(path: Path | str, step: str, changes: dict[str, str], *, append_note: str | None = None,
               unknown: str | None = None) -> dict[str, str]:
    """Replace the physical lines of one step's row with *changes* applied; the core of the row writers.

    *append_note* is added after the row's existing ``notes``. *unknown* is the message for a column the
    header lacks (``{names}`` is filled in); the default names the missing column. Raises as
    :func:`set_step` documents.
    """
    changes = dict(changes)
    target = Path(path).resolve()  # a symlinked table stays a link: the rename replaces what it points to
    with target.open(encoding="utf-8", newline="") as fh:
        text = fh.read()
    lines, records = _records(text)
    if not records:
        raise ValueError("the file is empty")
    header = [c.strip() for c in records[0][2]]
    if len(set(header)) != len(header):
        raise ValueError("the header repeats a column")
    wanted = ["step", *changes, *(["notes"] if append_note is not None else [])]
    missing = [c for c in dict.fromkeys(wanted) if c not in header]
    if missing:
        if unknown:
            raise ValueError(unknown.format(names=sorted(missing)))
        raise ValueError(f"the table has no {' or '.join(repr(m) for m in missing)} column")
    step_at = header.index("step")
    key = str(step).strip()
    hits = [r for r in records[1:] if len(r[2]) > step_at and r[2][step_at].strip() == key]
    if not hits:
        raise KeyError(step)
    if len(hits) > 1:
        raise ValueError(f"step {step!r} names {len(hits)} rows; fix the duplicate id first")
    first, last, cells = hits[0]
    if len(cells) > len(header):
        raise ValueError(f"step {step!r} has more cells than the header (an unquoted comma?); fix the row first")
    cells = cells + [""] * (len(header) - len(cells))
    if append_note is not None:
        existing = cells[header.index("notes")].strip()
        changes["notes"] = f"{existing}; {append_note}" if existing else append_note
    for column, value in changes.items():
        cells[header.index(column)] = value

    ending = lines[last - 1][len(lines[last - 1].rstrip("\r\n")):]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(cells)
    new_text = "".join(lines[:first]) + buf.getvalue() + ending + "".join(lines[last:])

    _, after = _records(new_text)
    expected = [list(r[2]) for r in records]
    expected[records.index(hits[0])] = cells
    if [r[2] for r in after] != expected:
        raise ValueError("the updated table does not read back as intended; nothing was written")
    _replace_text(target, new_text)
    return dict(zip(header, cells))


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
    """CLI: ``--check STEPS.csv [...]`` validates; ``--set STEPS.csv STEP`` updates one step.

    Args:
        argv: Arguments (defaults to ``sys.argv[1:]``).

    Returns:
        int: With ``--check``, 1 when any file has an error (or cannot be read), else 0; warnings never
        change the exit code. With ``--set``, 0 when the step was updated and 1 when it was refused.

    Raises:
        None.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m agentteams.plan_steps",
        description="Validate plan steps CSVs against the Rule 9 contract, or update one step.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", nargs="+", metavar="STEPS_CSV", help="Steps CSVs to validate.")
    mode.add_argument("--set", nargs=2, metavar=("STEPS_CSV", "STEP"), dest="set_step",
                      help="Update one step of a steps CSV (use with --status, --note or --append-note).")
    parser.add_argument("--status", help=f"With --set: the new status, one of {', '.join(sorted(STATUSES))}.")
    notes = parser.add_mutually_exclusive_group()
    notes.add_argument("--note", help="With --set: replace the step's notes.")
    notes.add_argument("--append-note", dest="append_note", help="With --set: add to the step's notes.")
    args = parser.parse_args(argv)
    if args.set_step:
        path, step = args.set_step
        try:
            row = set_step(path, step, status=args.status, note=args.note, append_note=args.append_note)
        except KeyError:
            print(f"ERROR {path}: no step {step!r}")
            return 1
        except (ValueError, OSError) as exc:
            print(f"ERROR {path}: {exc}")
            return 1
        print(f"OK {path}: step {step} status={row.get('status', '')}")
        return 0
    if args.status is not None or args.note is not None or args.append_note is not None:
        parser.error("--status, --note and --append-note apply to --set only")
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
           "main", "read_steps", "runnable", "set_step", "update_step", "validate", "write_steps"]


if __name__ == "__main__":
    raise SystemExit(main())
