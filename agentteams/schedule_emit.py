"""Claude /schedule routine emission for the bridge (Phase 5).

Emits ``.claude/schedules.agentteams.json`` — a list of recurring routine
specs the user wires into Claude's ``/schedule`` skill. Each routine
invokes a bridged subagent stub at a cron-style cadence.

The file is documentation/configuration only: agentteams does not enroll
the routines with any host scheduler. The user runs ``/schedule create``
(or equivalent) referencing the emitted file.

Default routine set (only emitted when the matching slug exists in the
canonical source dir):

- ``work-summarizer``  — daily, 22:00 UTC
- ``drift``            — weekly Monday, 14:00 UTC
- ``post-production-auditor`` — weekly Friday, 18:00 UTC
- ``advisory``         — monthly, 1st @ 12:00 UTC

Cadences are conservative defaults; the user may edit the emitted JSON
before enrolling. The emitter never overwrites a user-edited file
unless ``overwrite=True``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# (slug, cron, description) — cron format: minute hour dom month dow.
_DEFAULT_ROUTINES: list[tuple[str, str, str]] = [
    (
        "work-summarizer",
        "0 22 * * *",
        "Daily append-first work summary capture (22:00 UTC).",
    ),
    (
        "drift",
        "0 14 * * 1",
        "Weekly template→artifact drift scan (Monday 14:00 UTC).",
    ),
    (
        "post-production-auditor",
        "0 18 * * 5",
        "Weekly post-production sampling audit (Friday 18:00 UTC).",
    ),
    (
        "advisory",
        "0 12 1 * *",
        "Monthly advisory PR roll-up (1st of month, 12:00 UTC).",
    ),
]


@dataclass
class ScheduleEmissionResult:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    omitted_routines: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


def _present_slugs(source_dir: Path) -> set[str]:
    if not source_dir.is_dir():
        return set()
    return {
        p.name[: -len(".agent.md")]
        for p in source_dir.iterdir()
        if p.is_file() and p.name.endswith(".agent.md")
    }


def build_routines(source_dir: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Return (active routines, omitted slugs not present in source)."""
    present = _present_slugs(source_dir)
    routines: list[dict[str, str]] = []
    omitted: list[str] = []
    for slug, cron, desc in _DEFAULT_ROUTINES:
        if slug not in present:
            omitted.append(slug)
            continue
        routines.append(
            {
                "name": f"agentteams-{slug}",
                "cron": cron,
                "agent": slug,
                "description": desc,
                "tier": "cheap",
                "bridge": "copilot-vscode-to-claude",
            }
        )
    return routines, omitted


def emit_schedule_artifact(
    *,
    source_dir: Path,
    output_root: Path,
    dry_run: bool = False,
    overwrite: bool = True,
) -> ScheduleEmissionResult:
    """Write ``.claude/schedules.agentteams.json`` for the active routines."""
    result = ScheduleEmissionResult()
    routines, omitted = build_routines(source_dir)
    result.omitted_routines = omitted
    out_path = output_root / ".claude" / "schedules.agentteams.json"
    if out_path.exists() and not overwrite:
        result.skipped.append(str(out_path))
        return result
    payload = {
        "_agentteams_managed": (
            "Recurring routine specs emitted by the copilot-vscode→claude "
            "bridge. Enroll with Claude's /schedule skill; agentteams does "
            "not run them itself. Edit cron fields as needed before enrolling."
        ),
        "schema_version": "1.0",
        "routines": routines,
    }
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if not dry_run:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
    result.written.append(str(out_path))
    return result


__all__ = [
    "ScheduleEmissionResult",
    "build_routines",
    "emit_schedule_artifact",
]
