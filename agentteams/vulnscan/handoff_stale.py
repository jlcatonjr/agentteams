"""A stale ``reject`` becomes a *Needs you* item (vfr-c.C4; ``@security`` 2026-11-02: CLEAR).

Under handoff delivery a repository's orchestrator may answer ``reject`` for a staged fix. Nothing expires a
reject and nothing should re-ask: the orchestrator is a model, and asking again invites a different answer by chance.
So the rule is:

- **The clock** starts the first time the executor sees ``reject`` as the newest verdict for a staged fix's content
  digest (:func:`clock`): a :data:`CLOCK_KIND` row in the MAC-chained ledger, keyed by the digest alone. Its
  ``since`` is the timestamp of the digest's *earliest* start row after the newest person's answer for that digest
  (or the earliest at all, without one). A verdict in between (reject, defer, reject) writes a ``stop`` and a new
  ``start`` row for the record but never moves ``since``: only a person's answer or a new digest (a re-staged,
  changed fix) restarts the clock.
- **After** :data:`STALE_DAYS` the reject is a *Needs you* line for the operator (:func:`needs_you`) in the
  summary's handoff section and in ``handoff status``, naming the repository, the handoff and the date.
- **Nothing re-asks the orchestrator.** This module writes no packet and no request; a reject never gets the
  ``ask`` text an ``own-pr`` gets (:func:`.handoff_lapse.packet_ask`), and the line never lets ``open-pr`` proceed.
- **Only a person clears it**: an answer recorded with ``handoff answer`` (:func:`record_answer`, an
  :data:`ANSWER_KIND` ledger row written after an interactive confirmation; the agents cannot reach this CLI from
  their container), or a new digest. An answer clears only the line for that digest and restarts its clock (the
  line returns after another :data:`STALE_DAYS` if the reject still stands). It authorizes nothing:
  ``keep-rejected`` keeps the fix unopened; ``ask-again`` records that the operator will re-ask through the handoff
  tooling, by hand.

What this newly permits (Rule 12): nothing. It adds a notice; every gate stays as it was. An unreadable ledger
shows a problem, never a cleared line.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams.vulnscan import handoff

#: Days a ``reject`` on an unchanged digest waits before it becomes a Needs-you line.
STALE_DAYS = 14
REJECT = "reject"
CLOCK_KIND = "handoff-reject-clock"   # ledger row: the reject clock started (``state: start``) or stopped
ANSWER_KIND = "handoff-answer"        # ledger row: a person's recorded answer to a stale reject
ANSWERS = ("keep-rejected", "ask-again")


def _last(kind: str, repo: Path, rows: list[dict[str, Any]], **match: str) -> dict[str, Any] | None:
    return next((r for r in reversed(rows) if r.get("kind") == kind and r.get("target") == str(repo)
                 and all(r.get(k) == v for k, v in match.items())), None)


def clock(repo: Path, branch: str, *, record: bool = True) -> dict[str, str] | None:
    """Return the reject clock of the fix staged on ``branch``, starting or stopping it in the ledger.

    With ``record`` this may append a :data:`CLOCK_KIND` row, so call it inside ``ledger.locked()``.

    Args:
        repo: Canonical repository path.
        branch: The staged branch (``vulnscan/<YYYYMMDD>-<digest>``).
        record: Write the start or stop row (False: only read).

    Returns:
        ``{"digest", "handoff_id", "since", "stale"}`` (ISO dates) while the newest verdict for the digest is
        ``reject``: ``since`` from the digest's earliest start row after its newest :data:`ANSWER_KIND` row (module
        docstring). None otherwise, and when that row has no readable ``ts``.

    Raises:
        OSError: The ledger lock or file could not be used.
        ValueError: The ledger fails verification (nothing is shown as cleared on an untrusted ledger).
    """
    from agentteams.vulnscan import ledger   # lazy: the ledger module imports nothing of ours, the executor does

    found, _problems = handoff.latest_verdict(repo, branch)
    rows = ledger.read()
    last = _last(CLOCK_KIND, repo, rows)
    running = last is not None and last.get("state") == "start"
    if found is None or found.verdict != REJECT:
        if record and running:
            ledger.append(CLOCK_KIND, target=str(repo), state="stop", branch=branch[:200])
        return None
    if not (running and last is not None and last.get("digest") == found.digest):
        if not record:
            return None
        rows.append(ledger.append(CLOCK_KIND, target=str(repo), state="start", digest=found.digest,
                                  handoff_id=found.handoff_id))
    mine = [(i, r) for i, r in enumerate(rows) if r.get("target") == str(repo) and r.get("digest") == found.digest]
    answered = max((i for i, r in mine if r.get("kind") == ANSWER_KIND), default=-1)
    first = next((r for i, r in mine if i > answered and r.get("kind") == CLOCK_KIND and r.get("state") == "start"),
                 None)
    if first is None:   # an answer after the running start: the next sight starts the clock again
        if not record:
            return None
        first = ledger.append(CLOCK_KIND, target=str(repo), state="start", digest=found.digest,
                              handoff_id=found.handoff_id)
    try:
        since = datetime.fromisoformat(str(first["ts"])).astimezone().date()
    except (KeyError, ValueError):
        return None
    return {"digest": found.digest, "handoff_id": str(first.get("handoff_id", "")), "since": since.isoformat(),
            "stale": (since + timedelta(days=STALE_DAYS)).isoformat()}


def needs_you(repo: Path, rec: dict[str, str] | None, *, today: date | None = None) -> str:
    """Return the Needs-you line for a stale reject, or "" (no reject, or not yet stale: an answer restarts the clock).

    Args:
        repo: Canonical repository path.
        rec: :func:`clock`'s record, or None.
        today: The local date (tests); default today.

    Returns:
        One line naming the repository, the handoff, the digest and the reject's date.

    """
    if rec is None or (today or date.today()).isoformat() < rec["stale"]:
        return ""
    return (f"{repo.name}: the orchestrator rejected the staged fix {rec['digest']} (handoff {rec['handoff_id']}) on "
            f"{rec['since']}, unchanged for {STALE_DAYS} days. Nothing re-asks it: decide, then record your answer "
            f"with `python -m agentteams.vulnscan handoff answer --repo {repo} --answer {'|'.join(ANSWERS)}`")


def record_answer(repo: Path, branch: str, answer: str, note: str = "") -> dict[str, Any]:
    """Record a person's answer to the reject of the fix staged on ``branch``. Call inside ``ledger.locked()``.

    Args:
        repo: Canonical repository path.
        branch: The staged branch.
        answer: One of :data:`ANSWERS`.
        note: Free text kept with the row (at most 500 characters).

    Returns:
        The ledger row.

    Raises:
        ValueError: ``answer`` is unknown, nothing is staged, or the newest verdict is not ``reject``.
    """
    from agentteams.vulnscan import ledger

    if answer not in ANSWERS:
        raise ValueError(f"unknown answer {answer!r} (one of {', '.join(ANSWERS)})")
    found, _problems = handoff.latest_verdict(repo, branch) if branch else (None, [])
    if found is None or found.verdict != REJECT:
        raise ValueError(f"{repo.name}: no rejected staged fix to answer")
    return ledger.append(ANSWER_KIND, target=str(repo), digest=found.digest, handoff_id=found.handoff_id,
                         answer=answer, note=note[:500])


def cmd_answer(args: argparse.Namespace, *, ask: Callable[[str], str] = input) -> int:
    """Run ``handoff answer --repo PATH --answer WORD [--note TEXT]`` (interactive confirmation required).

    The terminal check (``stdin`` is a TTY) is not an identity check: it keeps an unattended process from recording
    an answer by accident, nothing more. That is enough here because an answer can only hide the alert for one
    digest (and restart its clock); it never authorizes anything, so a forged one cannot open a PR or clear a gate.

    Args:
        args: Parsed arguments (``repo``, ``answer``, ``note``).
        ask: Prompt function (tests).

    Returns:
        0 recorded, 1 declined, 2 refused input.
    """
    from agentteams.vulnscan import ledger

    if len(args.repo) != 1 or not args.answer:
        print("handoff answer takes exactly one --repo and an --answer", file=sys.stderr)
        return 2
    if not sys.stdin.isatty() and ask is input:
        print("refused: recording an answer needs an interactive confirmation", file=sys.stderr)
        return 2
    repo = Path(args.repo[0]).expanduser().resolve()
    branch = handoff.staged_branch(repo)
    if ask(f"Record '{args.answer}' for the rejected fix {branch or '(none)'} of {repo.name}? [y/N] "
           ).strip().lower() != "y":
        return 1
    try:
        with ledger.locked():
            row = record_answer(repo, branch, args.answer, args.note or "")
    except (OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"recorded {row['answer']} for {row['digest']} (ledger row {row['seq']})")
    return 0
