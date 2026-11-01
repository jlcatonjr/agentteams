"""Futile-retry guard: stop re-staging a repository whose fix plan has not changed since nothing was fixable.

``stage-fix`` never takes a semver-major jump, so a repository whose only fixes are major upgrades fails
with "nothing fixable without a major jump" (or, when the default branch already has the fix and the local
checkout is behind, "already fixed on the default branch"), and the agent used to request it again every day,
often several times a day. This guard refuses the retry while the repository's dry-run fix plan (``fixplan-<slug>.md``,
written by every daily run) is byte-identical to the plan of the run in which that result was recorded. A new
advisory, a new release or a change on the default branch changes the plan and lifts the hold by itself.

The decision reads only the MAC-chained ledger and the run directories; it never runs git or a registry
lookup. The operator is told once per held plan (:func:`mark_held`, fixed-text notification); the daily
summary counts held repositories (:func:`held_count`). Nothing here blocks other repositories or other ops.

``stage-major`` (semver-major upgrades allowed) is held the same way but on its own: the hold is keyed on the op,
so a futile ``stage-major`` never holds ``stage-fix`` and vice versa. A ``stage-major`` that changes nothing is
recorded as :data:`MAJOR_FUTILE_DETAIL`, never as "nothing fixable without a major jump".
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
#: ``stage-major``'s futile result: even with semver-major upgrades allowed, the change fixes nothing.
MAJOR_FUTILE_DETAIL = "nothing fixable even with a major upgrade"
_HELD_OPS = ("stage-fix", "stage-major")
#: Recorded instead of :data:`FUTILE_DETAIL` when the default branch has nothing vulnerable: the fix is already
#: upstream and the local checkout (whose scan wrote the fix plan) is behind. Held the same way.
UPSTREAM_DETAIL = "already fixed on the default branch"
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


def futile_detail(major: bool) -> str:
    """Return the "nothing fixable" detail a staging op records: :data:`MAJOR_FUTILE_DETAIL` for ``stage-major``."""
    return MAJOR_FUTILE_DETAIL if major else FUTILE_DETAIL


def _last_futile(target: str, rows: list[dict[str, Any]], op: str = "stage-fix") -> tuple[str, str] | None:
    """Return (local date, detail prefix) of ``target``'s last ``op`` outcome when it was futile, else None."""
    last: dict[str, Any] | None = None
    for r in rows:
        if r.get("op") == op and r.get("target") == target and r.get("kind") in ("done", "failed"):
            last = r
    detail = str(last.get("detail", "")) if last is not None else ""
    prefix = next((p for p in (FUTILE_DETAIL, MAJOR_FUTILE_DETAIL, UPSTREAM_DETAIL) if detail.startswith(p)), "")
    if last is None or last.get("kind") != "failed" or not prefix:
        return None
    try:
        return datetime.fromisoformat(str(last["ts"])).astimezone().date().isoformat(), prefix
    except (KeyError, ValueError):
        return None


def futile_reason(op: str, target: str, rows: list[dict[str, Any]]) -> str:
    """Return why a ``stage-fix`` (or ``stage-major``) of ``target`` would only repeat a "nothing fixable" result, or "".

    Each op is held only by its own last result: a futile ``stage-major`` never holds ``stage-fix``, nor the reverse.

    Args:
        op: The requested operation (only ``stage-fix`` and ``stage-major`` are ever held).
        target: Canonical repository path.
        rows: Ledger rows.

    Returns:
        A refusal reason starting with :data:`HELD_PREFIX`, or "" when the retry may run (no such result,
        or the plan changed, or either plan is missing: unknown means retry).
    """
    if op not in _HELD_OPS:
        return ""
    found = _last_futile(target, rows, op)
    if found is None:
        return ""
    day, prefix = found
    then, now = plan_digest(target, day), plan_digest(target)
    if not then or then != now:
        return ""
    if prefix == UPSTREAM_DETAIL:
        return (f"{HELD_PREFIX}{op} found the fix {UPSTREAM_DETAIL} on {day} and this repository's fix plan "
                "has not changed since; it is not staged again until the plan changes (update the local checkout)")
    if prefix == MAJOR_FUTILE_DETAIL:
        return (f"{HELD_PREFIX}stage-major found {MAJOR_FUTILE_DETAIL} on {day} and this repository's fix plan has "
                "not changed since; it is not staged again until the plan changes (the rest needs a person)")
    return (f"{HELD_PREFIX}stage-fix found {FUTILE_DETAIL} on {day} and this repository's fix plan has not "
            "changed since; it is not staged again until the plan changes (request stage-major for its major-held "
            "rows, if the grant allows it)")


def major_next_since(target: str, rows: list[dict[str, Any]]) -> str:
    """Return the day ``target``'s ``stage-fix`` was held for :data:`FUTILE_DETAIL`, when ``stage-major`` is next.

    That is: its last ``stage-fix`` outcome failed "nothing fixable without a major jump" (never the upstream
    variant), :func:`futile_reason` still holds it (the current fix plan is the one of that day), and no
    ``stage-major`` row of any kind follows that outcome (a later ``stage-major`` result, refusal included, is
    the agent's to read, not a reason to ask again). The caller still checks the plan for major rows
    ``stage-major`` can take; the executor re-checks everything either way.

    Args:
        target: Canonical repository path.
        rows: Ledger rows, in ledger order.

    Returns:
        The local date (``YYYY-MM-DD``) of that ``stage-fix`` result, or "".
    """
    found = _last_futile(target, rows, "stage-fix")
    if found is None or found[1] != FUTILE_DETAIL or not futile_reason("stage-fix", target, rows):
        return ""
    last = max(i for i, r in enumerate(rows) if r.get("op") == "stage-fix" and r.get("target") == target
               and r.get("kind") in ("done", "failed"))
    if any(r.get("op") == "stage-major" and r.get("target") == target for r in rows[last + 1:]):
        return ""
    return found[0]


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


def nothing_vulnerable(wt: Path) -> bool:
    """Return whether a scan of the staging worktree (the default branch) finds no vulnerable package at all.

    ``fix_path`` cannot tell this apart from "only major fixes": an applied npm run and every ``composer.lock``
    are recorded whether or not anything there is vulnerable. A scan that could not complete is never "nothing".

    Args:
        wt: The executor's worktree at the base commit.

    Returns:
        True only when the scan completed, checked at least one dependency and found no package finding.
    """
    from agentteams.vulnscan import engine   # lazy: engine is heavy and the executor imports this module

    try:
        rep = engine.scan_path(wt)
    except (OSError, ValueError):
        return False
    return rep.checked > 0 and not rep.feed_errors and not any(f.kind == "package" for f in rep.findings)


def zero_change_detail(wt: Path, head: str, *, refused: bool, major: bool = False) -> str:
    """Return the stage-fix (or stage-major) refusal for a fix that changed nothing (npm ran cleanly).

    Args:
        wt: The executor's worktree at the base commit.
        head: The base ref, named in the refusal.
        refused: Whether ``fix_path`` refused some edit (then nothing is ever called "fixed upstream").
        major: The op is ``stage-major``: :data:`MAJOR_FUTILE_DETAIL` replaces :data:`FUTILE_DETAIL`.

    Returns:
        :data:`UPSTREAM_DETAIL` when the base has nothing vulnerable (the scanned local checkout is behind),
        else :func:`futile_detail`; each is held by :func:`futile_reason` until the fix plan changes.
    """
    if not refused and nothing_vulnerable(wt):
        return f"{UPSTREAM_DETAIL} ({head}): nothing vulnerable there; update the local checkout"
    return futile_detail(major)
