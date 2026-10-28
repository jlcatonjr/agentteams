"""Futile-retry guard: stop re-staging a repository whose fix plan has not changed since nothing was fixable.

``stage-fix`` never takes a semver-major jump, so a repository whose only fixes are major upgrades fails
with "nothing fixable without a major jump", and the agent used to request it again every day, often several
times a day. This guard refuses the retry while the repository's dry-run fix plan (``fixplan-<slug>.md``,
written by every daily run) is byte-identical to the plan of the run in which that result was recorded. A new
advisory, a new release or a change on the default branch changes the plan and lifts the hold by itself.

The decision reads only the MAC-chained ledger and the run directories; it never runs git or a registry
lookup. The operator is told once per held plan (:func:`mark_held`, fixed-text notification); the daily
summary counts held repositories (:func:`held_count`). Nothing here blocks other repositories or other ops.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths

FUTILE_DETAIL = "nothing fixable without a major jump"
HELD_PREFIX = "held: "
_RUN_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _held_file() -> Path:
    return paths.state_dir() / "held-repos.json"


def _slug(repo: str) -> str:
    from agentteams.vulnscan.daily import (
        plan_slug,  # lazy: daily imports the executor, which imports this
    )

    return plan_slug(Path(repo))


def plan_digest(repo: str, day: str | None = None) -> str:
    """Return the sha256 of the newest fix plan for ``repo`` written on or before ``day`` (any day when None), or ""."""
    state = paths.state_dir()
    try:
        days = sorted((p.name for p in state.iterdir() if _RUN_DIR.match(p.name) and p.is_dir()), reverse=True)
    except OSError:
        return ""
    name = f"fixplan-{_slug(repo)}.md"
    for d in days:
        if (day is None or d <= day) and (state / d / name).is_file():
            try:
                return hashlib.sha256((state / d / name).read_bytes()).hexdigest()
            except OSError:   # unreadable: unknown, so never held
                return ""
    return ""


def _last_futile_day(target: str, rows: list[dict[str, Any]]) -> str | None:
    """Return the local date of ``target``'s last stage-fix outcome when it was "nothing fixable", else None."""
    last: dict[str, Any] | None = None
    for r in rows:
        if r.get("op") == "stage-fix" and r.get("target") == target and r.get("kind") in ("done", "failed"):
            last = r
    if last is None or last.get("kind") != "failed" or not str(last.get("detail", "")).startswith(FUTILE_DETAIL):
        return None
    try:
        return datetime.fromisoformat(str(last["ts"])).astimezone().date().isoformat()
    except (KeyError, ValueError):
        return None


def futile_reason(op: str, target: str, rows: list[dict[str, Any]]) -> str:
    """Return why a ``stage-fix`` of ``target`` would only repeat a "nothing fixable" result, or "".

    Args:
        op: The requested operation (only ``stage-fix`` is ever held).
        target: Canonical repository path.
        rows: Ledger rows.

    Returns:
        A refusal reason starting with :data:`HELD_PREFIX`, or "" when the retry may run (no such result,
        or the plan changed, or either plan is missing: unknown means retry).
    """
    if op != "stage-fix":
        return ""
    day = _last_futile_day(target, rows)
    if day is None:
        return ""
    then, now = plan_digest(target, day), plan_digest(target)
    if not then or then != now:
        return ""
    return (f"{HELD_PREFIX}stage-fix found {FUTILE_DETAIL} on {day} and this repository's fix plan has not "
            "changed since; it is not staged again until the plan changes (a major upgrade is a manual change)")


def _read_held() -> dict[str, str]:
    try:
        data = json.loads(_held_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def mark_held(target: str) -> bool:
    """Record ``target`` as held at its current plan; return True only the first time for that plan."""
    digest = plan_digest(target)
    held = _read_held()
    if not digest or held.get(target) == digest:
        return False
    held[target] = digest
    path = _held_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(held, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


def held_count() -> int:
    """Return how many recorded repositories are still held (their newest plan is the one they were held at)."""
    return sum(1 for repo, digest in _read_held().items() if plan_digest(repo) == digest)
