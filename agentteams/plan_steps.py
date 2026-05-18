"""Reader for plan ``.steps.csv`` artifacts.

Introduced by Cluster C (typed handoffs). Tolerates quoted multi-line cells
because that is the existing convention in ``tmp/by-week/**/*.steps.csv``.
"""

from __future__ import annotations

import csv
from pathlib import Path


def read_steps(path: Path | str) -> list[dict[str, str]]:
    """Read a plan steps.csv. Returns a list of dicts keyed by header column."""
    text = Path(path).read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    return [{k: (v or "") for k, v in row.items()} for row in reader if row.get("step")]


__all__ = ["read_steps"]
