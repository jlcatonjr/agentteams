"""Reader for plan ``.steps.csv`` artifacts.

Introduced by Cluster C (typed handoffs). Tolerates quoted multi-line cells
because that is the existing convention in ``tmp/by-week/**/*.steps.csv``.
"""

from __future__ import annotations

import csv
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


__all__ = ["read_steps"]
