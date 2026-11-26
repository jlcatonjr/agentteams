"""open_halts.py — a read-only listing of a team directory's HALT rows, judged by the gate's own rules (beta).

Tools that only need to know whether a HALT is still open used to re-implement the retraction rules, and could
disagree with the gate. :func:`open_halts` reads the decisions log through the gate's own reader and asks the gate's
own scan (``decision_log._open_halt_for``) for each HALT, so its answer is the gate's:

* **With an action**, it lists the HALT rows that block that action now, exactly as the destructive-action gate's
  HALT check (``decision_log._assert_no_unretracted_halt``) judges them: the action itself and each ``derives_from``
  ancestor, with the HALT-side match (``action_ids.halt_blocks``) and the retraction rules for that action.
* **Without one**, it lists every HALT row with whether it is retracted, each judged for its own id (the gate's
  retraction by exact id): a HALT listed as retracted blocks no action; one listed open blocks at least its own id.

Which actions a HALT blocks (``action_ids.halt_blocks``): an action whose id the HALT's id equals, or extends with
``-``, ``_``, ``.`` or ``:`` and more (the strict match: a HALT on ``prune-001`` blocks ``prune``); an action whose id
extends the HALT's id with ``:`` and more (the family rule: a HALT on ``server-apply`` blocks
``server-apply:remove-path:...``; only ``:`` makes a family); and an action whose tokens appear as a run in the HALT's
id (tokens split on every non-alphanumeric character, ``/`` included, and on camelCase and letter-digit boundaries).
So ``/`` makes no family: a HALT on ``family`` or ``family/gate`` does not block ``family/gate/study:sha256-...``, a
HALT on ``family/gate/study`` does (across the colon), and a HALT on ``family/gate/study`` also blocks ``family/gate``
and ``gate`` (the token rule). A HALT with no letter or digit in its id blocks every action and is retracted only by
its row handle (``action_ids.halt_row_handle``).

An empty result for an action means no HALT blocks it, not that it clears: the action still needs a clearance, and a
retracted HALT that is still the newest row matching the action refuses until a new clearance is recorded.

With an action it answers the destructive-action gate's HALT check for that one id in this one team directory; a
gate that also checks a family id (the sandbox relaxations, S-10) or other directories is answered by asking for each
id, in each directory, it checks.

Read-only: nothing is consumed or written. A point-in-time read; call it immediately before acting on the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentteams.cli.action_ids import halt_display_id, halt_retraction_id
from agentteams.cli.decision_lineage import _halt_scan_targets
from agentteams.cli.decision_log import (
    _assert_decision_chain_intact,
    _decision_signing_active,
    _open_halt_for,
    _read_decision_rows,
)


@dataclass(frozen=True)
class HaltRow:
    """One HALT row of a decisions log, as :func:`open_halts` judged it.

    Attributes:
        row: The row's 1-based number in the log (the header is not counted).
        halt_id: The id a refusal names for it and a ``HALT-RETRACTED`` row must carry to retract it (its own id; for
            a HALT with no usable id, its row handle).
        is_open: Whether the HALT is still in force (for the action asked about, or for its own id).
        retracted_by: The 1-based row of the accepted retraction that cleared it, or None while it is open.
        blocks: With an action, the id it blocks: the action or the ``derives_from`` ancestor it was found on.
            Without one, its own retraction id.
    """

    row: int
    halt_id: str
    is_open: bool
    retracted_by: int | None
    blocks: str


def _halt_rows(rows: list[dict[str, str]]) -> list[int]:
    return [i for i, row in enumerate(rows) if (row.get("verdict") or row.get("status") or "").strip().upper() == "HALT"]


def _reviewed(row: dict[str, str]) -> str:
    return (row.get("action_reviewed") or row.get("decision") or "").strip()


def open_halts(team_dir: Path, action: str | None = None) -> list[HaltRow]:
    """List a team directory's HALT rows under the gate's own rules (beta; read-only).

    Args:
        team_dir: The team directory holding ``references/security-decisions.log.csv``.
        action: A gate action id. When given, the result is the HALT rows that block it now, each once, in row order
            (every entry ``is_open``). When None, every HALT row in the log, each judged for its own id.

    Returns:
        :class:`HaltRow` entries in row order; empty when the log is absent or holds no HALT.

    Raises:
        TypeError: *team_dir* is not a :class:`pathlib.Path`, or *action* is neither a ``str`` nor None.
        ValueError: *action* is empty or only whitespace.
        RuntimeError: The gate would refuse to read the log (it is unreadable or malformed, or fails the gate's chain
            check), or (with *action*) the ``derives_from`` lineage cannot be walked.
    """
    if not isinstance(team_dir, Path):
        raise TypeError("team_dir must be a pathlib.Path")
    if action is not None and not isinstance(action, str):
        raise TypeError("action must be a str or None")
    if action is not None and not action.strip():
        raise ValueError("action must name an action id")
    read = _read_decision_rows(team_dir)
    if read is None or not read[0]:
        return []
    rows, fieldnames = read
    _assert_decision_chain_intact(rows, fieldnames)
    signing_active = _decision_signing_active(fieldnames)

    def judge(target: str) -> dict[int, int | None]:
        outcome: dict[int, int | None] = {}
        _open_halt_for(rows, target, output_dir=team_dir, signing_active=signing_active, outcome=outcome)
        return outcome

    if action is not None:
        found: dict[int, HaltRow] = {}
        for target in _halt_scan_targets(action, rows, fieldnames):   # the gate's own walk: action, then ancestors
            for index, cleared in judge(target).items():
                if cleared is None and index not in found:
                    found[index] = HaltRow(index + 1, halt_display_id(_reviewed(rows[index]), index + 1, rows[index]),
                                           True, None, target)
        return [found[i] for i in sorted(found)]
    listing: list[HaltRow] = []
    for index in _halt_rows(rows):
        own = halt_retraction_id(_reviewed(rows[index]), index + 1, rows[index])
        cleared = judge(own).get(index)
        listing.append(HaltRow(index + 1, halt_display_id(_reviewed(rows[index]), index + 1, rows[index]),
                               cleared is None, None if cleared is None else cleared + 1, own))
    return listing


__all__ = ["HaltRow", "open_halts"]
