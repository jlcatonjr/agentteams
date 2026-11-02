"""The ``own-pr`` lapse (vfr-b.B1b, operator decision OD10: "ask the owner, then lapse").

Under handoff delivery (:mod:`.handoff`) a repository's orchestrator may answer ``own-pr`` for a staged fix: it will
open its own PR. Colorado-collectors answered so twice for a csv-parse 7 branch it prepared and never pushed, so the
fix waited indefinitely. The rule:

- **The clock** starts the first time the executor sees ``own-pr`` as the newest verdict for a staged fix
  (:func:`clock`). Its start is a :data:`CLOCK_KIND` row in the MAC-chained ledger, so ``since`` is the row's own
  timestamp: no unsigned state file can move it. A start counts only for the same fix digest, fix plan
  (:func:`.retry_guard.plan_digest`) and verdict ``handoff_id``; any change restarts it. Whenever the newest verdict is
  seen to be anything but ``own-pr`` the clock is stopped (a ``stop`` row), so own-pr → reject → own-pr starts over.
- **While it runs** the executor keeps deferring (``open-pr`` is refused as before) and the refusal, the summary and
  ``handoff status`` name the lapse date. :func:`packet_ask` is the text each run's handoff packet carries to ask the
  orchestrator for its PR: for a repository with no Claude session (colorado), the packet is the only channel. The
  packets themselves are written by the operator's handoff tooling, outside this package.
- **After** :data:`LAPSE_DAYS`, when no open owner PR updates the staged packages (:func:`.open_prs.owner_prs`),
  :func:`gate` lets ``open-pr`` proceed under the existing grant, and :func:`log` writes a :data:`LAPSED_KIND` ledger
  row once ``executor.authorize`` has found a grant that covers it. No new op, grant or capability: the PR is the
  executor's ordinary ``open-pr``.

What the lapse newly permits (Rule 12): only ``own-pr`` lapses. ``defer`` and ``reject`` never do, and a later verdict
for the same digest withdraws the ``own-pr`` and stops the clock. Every uncertainty keeps deferring: an unreadable
ledger starts no clock and lapses nothing, and a gh failure or a staged record without packages or worktree counts as
"an owner PR may exist".
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import handoff
from agentteams.vulnscan.executor_env import ExecError

#: Days an ``own-pr`` verdict defers the executor's PR before it lapses (OD10).
LAPSE_DAYS = 7
OWN_PR = "own-pr"
CLOCK_KIND = "handoff-clock"     # ledger row: the clock started (``state: start``) or stopped (``state: stop``)
LAPSED_KIND = "handoff-lapsed"   # ledger row: open-pr proceeded on a lapse (written after the grant check)


def _packages(st: dict[str, Any]) -> list[str]:
    """Return a staged record's package names (strings only; [] when the field is missing or not a list)."""
    names = st.get("packages")
    return [p for p in names if isinstance(p, str) and p] if isinstance(names, list) else []


def _last_clock(repo: Path, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the newest :data:`CLOCK_KIND` row for ``repo``, or None."""
    return next((r for r in reversed(rows) if r.get("kind") == CLOCK_KIND and r.get("target") == str(repo)), None)


def clock(repo: Path, branch: str, *, record: bool = True) -> dict[str, str] | None:
    """Return the ``own-pr`` clock of the fix staged on ``branch``, starting or stopping it in the ledger.

    With ``record`` this appends a :data:`CLOCK_KIND` row, so call it inside ``ledger.locked()``: a ``start`` when the
    newest verdict is ``own-pr`` and no start for this digest, plan and verdict exists, a ``stop`` when the verdict is
    anything else and the clock was running. Besides the executor's ``open-pr`` gate, ``handoff status`` and the daily
    summary call it with ``record``, so those read-only views can write clock rows too (only where the ledger key is
    reachable; elsewhere the write fails and nothing is recorded).

    Args:
        repo: Canonical repository path.
        branch: The staged branch (``vulnscan/<YYYYMMDD>-<digest>``).
        record: Write the start or stop row (False: only read).

    Returns:
        ``{"digest", "plan", "since", "lapses", "handoff_id"}`` (ISO dates) while an ``own-pr`` clock runs; None
        otherwise, and when the ledger cannot be read (nothing lapses on an untrusted ledger).
    """
    from agentteams.vulnscan import ledger, retry_guard   # lazy: both import the executor, which imports this

    found, _problems = handoff.latest_verdict(repo, branch)
    try:
        last = _last_clock(repo, ledger.read())
    except (OSError, ValueError):
        return None
    running = last is not None and last.get("state") == "start"
    if found is None or found.verdict != OWN_PR:
        if record and running:
            ledger.append(CLOCK_KIND, target=str(repo), state="stop", branch=branch[:200])
        return None
    key = {"digest": found.digest, "plan": retry_guard.plan_digest(str(repo)), "handoff_id": found.handoff_id}
    if not (running and all(last.get(k) == v for k, v in key.items())):
        if not record:
            return None
        last = ledger.append(CLOCK_KIND, target=str(repo), state="start", **key)
    try:
        since = datetime.fromisoformat(str(last["ts"])).astimezone().date()
    except (KeyError, ValueError):
        return None
    return {**key, "since": since.isoformat(), "lapses": (since + timedelta(days=LAPSE_DAYS)).isoformat()}


def owner_pr(repo: Path) -> list[int] | None:
    """Return the open owner PRs that update the staged fix's packages, or None when that cannot be told.

    Args:
        repo: Canonical repository path (its staged record names the packages and the executor's worktree).

    Returns:
        PR numbers (``[]``: none); None when the staged record has no packages or worktree, or gh fails.
    """
    from agentteams.vulnscan import executor, open_prs   # lazy: executor imports this module

    st = executor.read_staged(repo)
    packages, wt = _packages(st), st.get("worktree")
    if not packages or not isinstance(wt, str) or not wt:
        return None
    try:
        return open_prs.owner_prs(open_prs.list_open(executor.origin_repo(Path(wt))), packages)
    except (ExecError, OSError, subprocess.SubprocessError):   # incl. a git or gh timeout
        return None


def due(rec: dict[str, str] | None, today: date | None = None) -> bool:
    """Return whether a :func:`clock` record has run its :data:`LAPSE_DAYS`.

    Args:
        rec: :func:`clock`'s record, or None.
        today: The local date (tests); default today.

    Returns:
        True on or after the lapse date.
    """
    return rec is not None and (today or date.today()).isoformat() >= rec["lapses"]


def lapse_detail(repo: Path, rec: dict[str, str] | None, *, today: date | None = None) -> str:
    """Return the :data:`LAPSED_KIND` detail when the ``own-pr`` of ``rec`` has lapsed, else "".

    Args:
        repo: Canonical repository path.
        rec: :func:`clock`'s record, or None.
        today: The local date (tests); default today.

    Returns:
        The detail when the clock is :func:`due` and no open owner PR updates the staged packages; "" otherwise
        (no ``own-pr``, too early, an owner PR, or unknown).
    """
    if rec is None or not due(rec, today) or owner_pr(repo) != []:
        return ""
    from agentteams.vulnscan import executor   # lazy: executor imports this module

    names = ", ".join(_packages(executor.read_staged(repo)))[:300]
    return (f"own-pr verdict (handoff {rec['handoff_id']}) on fix {rec['digest']} since {rec['since']}: no owner PR "
            f"updates {names} after {LAPSE_DAYS} days, so the executor opens its staged PR (OD10)")


def gate(op: str, repo: Path, cfg: dict[str, Any]) -> str:
    """Apply the handoff gate (H3) to ``open-pr``, with the ``own-pr`` lapse. Call inside ``ledger.locked()``.

    Args:
        op: Requested operation (only ``open-pr`` under handoff delivery is gated).
        repo: Canonical repository path.
        cfg: Daily config (:func:`.handoff.required`).

    Returns:
        The lapse detail for :func:`log` when ``open-pr`` may proceed only because the ``own-pr`` lapsed; else "".

    Raises:
        ExecError: The repository's orchestrator has not approved the staged fix (the reason, and for a running
            ``own-pr`` clock its lapse date).
    """
    if op != "open-pr" or not handoff.required(cfg, repo):
        return ""
    branch = handoff.staged_branch(repo)
    problem = handoff.approval_problem(repo, branch) if branch else ""   # no staged fix: open_pr says so
    if not problem:
        return ""
    rec = clock(repo, branch)   # computed once: the lapse and the refusal read the same record
    if lapse := lapse_detail(repo, rec):
        return lapse
    raise ExecError(problem + pending_detail(rec))


def log(lapse: str, *, target: str, request_id: str = "") -> None:
    """Write the :data:`LAPSED_KIND` ledger row for :func:`gate`'s lapse ("" writes nothing). Call inside the lock.

    Args:
        lapse: :func:`gate`'s result.
        target: Canonical repository path.
        request_id: The request id.
    """
    if lapse:
        from agentteams.vulnscan import ledger

        ledger.append(LAPSED_KIND, op="open-pr", target=target, request_id=request_id, detail=lapse[:1000])


def pending_detail(rec: dict[str, str] | None) -> str:
    """Return the lapse date to add to an ``own-pr`` refusal (``; the own-pr lapses on <date> ...``), or "".

    Args:
        rec: :func:`clock`'s record, or None.

    Returns:
        The suffix, or "" without a running clock.
    """
    if rec is None:
        return ""
    return (f"; the own-pr lapses on {rec['lapses']} unless an open PR of the owner updates these packages "
            "(then the executor opens its own)")


def packet_ask(repo: Path, branch: str, rec: dict[str, str] | None) -> str:
    """Return the request each run's handoff packet carries while an ``own-pr`` clock runs, or "".

    The packet is written by the operator's handoff tooling (outside this package); this is its text, so the
    repository's orchestrator (the only channel to a repository with no Claude session) is asked for its PR and
    told the lapse date. Only host-derived values go in: dates, the digest and package names from the staged record.

    Args:
        repo: Canonical repository path.
        branch: The staged branch.
        rec: :func:`clock`'s record, or None.

    Returns:
        One paragraph, or "" without a running clock.
    """
    from agentteams.vulnscan import executor   # lazy: executor imports this module

    if rec is None:
        return ""
    names = ", ".join(_packages(executor.read_staged(repo)))[:300] or "the staged packages"
    return (f"You answered own-pr on {rec['since']} for the staged fix {branch} (handoff {rec['handoff_id']}). Please "
            f"open your own PR updating {names} and push it. If no open PR of yours updates them by {rec['lapses']}, "
            "the executor opens its staged PR instead (lapse rule OD10).")
