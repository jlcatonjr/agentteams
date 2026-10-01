"""Reader and writer for plan ``.steps.csv`` artifacts.

Introduced by Cluster C (typed handoffs). Tolerates quoted multi-line cells
because that is the existing convention in ``tmp/by-week/**/*.steps.csv``.
:func:`write_steps` / :func:`update_step` (#21, 2026-10-01) write through ``csv.DictWriter``
with a read-back check, so a hand-written unquoted comma can no longer shift a column.
"""

from __future__ import annotations

import csv
import io
import warnings
from pathlib import Path


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


__all__ = ["read_steps", "update_step", "write_steps"]
