"""Shrink-grant warnings in fleet reports (closes the #192 stderr-only advisory).

Fleet captures each target's output in-process; these helpers pick out the
``WARNING: --shrink-allow`` lines and the announced grants, and render the
run-level "Shrink grants" section of ``summary.md``.

``TargetResult.shrink_grant_warnings`` holds a target's warnings (a grant skipped as
unappliable, refused for want of a backup dir, or one that released nothing);
``shrink_grants_active`` holds the grants it announced. Fleet fills both straight after
the in-process run, before the dry-run and non-git branches, so every path records them.
"""
from __future__ import annotations

import re
from pathlib import Path

_SHRINK_GRANT_WARNING = "WARNING: --shrink-allow"
_SHRINK_GRANT_ACTIVE = re.compile(r"shrink override active: (\S+)")
_SHRINK_GRANT_UNUSED = re.compile(r"WARNING: --shrink-allow (\S+): released nothing")


def shrink_grant_lines(update_output: str) -> tuple[list[str], list[str]]:
    """Pick the shrink-grant warnings and the announced grants out of one target's output.

    Args:
        update_output: The captured stdout+stderr of the target's update.

    Returns:
        ``(warnings, active)``: the ``WARNING: --shrink-allow`` lines, stripped, and the grant
        entries the run announced as active.
    """
    warnings = [l.strip() for l in update_output.splitlines() if _SHRINK_GRANT_WARNING in l]
    active = [m.group(1) for m in _SHRINK_GRANT_ACTIVE.finditer(update_output)]
    return warnings, active


def shrink_grant_summary(rows: list[dict]) -> list[str]:
    """Render the run-level shrink-grant section of ``summary.md``.

    An exported ``AGENTTEAMS_SHRINK_ALLOW`` is read by every target, so a grant meant for one
    workspace "releases nothing" everywhere else. A grant is listed as unused only when every
    target that announced it reported it unused; every other warning (skipped, refused) is listed
    against its own target.

    Args:
        rows: The per-target report rows (``asdict(TargetResult)``).

    Returns:
        Markdown lines, empty when no target printed a shrink-grant warning.
    """
    announced: dict[str, int] = {}
    unused: dict[str, int] = {}
    other: list[str] = []
    for r in rows:
        for g in set(r.get("shrink_grants_active", [])):
            announced[g] = announced.get(g, 0) + 1
        for w in r.get("shrink_grant_warnings", []):
            m = _SHRINK_GRANT_UNUSED.search(w)
            if m:
                unused[m.group(1)] = unused.get(m.group(1), 0) + 1
            else:
                other.append(f"- {Path(r['workspace']).name} / {r['target']}: {w}")
    nowhere = sorted(g for g, n in unused.items() if n >= announced.get(g, n))
    if not other and not nowhere:
        return []
    lines = ["", "## Shrink grants", ""]
    if other:
        lines += ["Skipped or refused (the section stays pinned):", "", *other, ""]
    if nowhere:
        lines += ["Released nothing on any target (a typo, or the body changed since review):", "",
                  *(f"- `{g}`" for g in nowhere), ""]
    return lines
