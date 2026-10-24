"""action_ids.py — how a decision row's ``action_reviewed`` id is matched to a gate action.

Two rules, deliberately different, because a false match means opposite things on each side:

* :func:`action_matches` decides which rows can **clear** an action. It stays a strict id match
  (``<action>`` or ``<action>`` plus a ``-_.:`` suffix): widening it would let a row named for one
  thing authorize another.
* :func:`halt_names_action` decides which HALT rows **block** an action. A HALT also blocks every
  action whose id appears as a token run in the HALT's own id, so a HALT recorded as
  ``m6-overwrite-render-1a2fad6`` blocks ``overwrite`` (C-2: a HALT is not evaded by spelling). A
  false positive only blocks, so this side errs wide (1.0 must-fix, mathAgents M6 finding).
"""

from __future__ import annotations

import re
from pathlib import Path

#: Token boundaries for the HALT-side match: any non-alphanumeric run, after camelCase and
#: letter/digit boundaries are split (``m6OverwriteRender``, ``overwrite2``, ``(prune)``, ``overwrite,``).
#: Wide on purpose: on the HALT side a false match only blocks.
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

#: The action ids agentteams' own gates are called with (``_assert_destructive_action_allowed``,
#: ``check_clearance``, ``_assert_no_unretracted_halt``); ``branch-cleanup`` is the family of the
#: per-plan ``branch-cleanup:<sha256>`` id. The pre-upgrade report checks old HALTs against these; a
#: test checks both directions against the call sites.
GATE_ACTIONS: tuple[str, ...] = ("overwrite", "prune", "restore-backup", "adopt-orphans-merge", "branch-delete",
                                 "branch-cleanup", "vulnscan-remediation", "vulnscan-publish")


def action_matches(action_reviewed: str, action: str) -> bool:
    """Return True for strict action-id style matches.

    Accepted patterns:
    - <action>
    - <action>-<suffix>
    - <action>_<suffix>
    - <action>.<suffix>
    - <action>:<suffix>

    **The suffix decorates; it never narrows.** ``prune-001`` is an instance id, and matching
    it to the ``prune`` action is the intended behaviour. But the same field gets written as a
    *scope descriptor* — ``overwrite-single-readme-only`` — and this matcher cannot tell the
    two readings apart, so a clearance that reads as narrow grants the unrestricted action
    (audit W11 / probe A5). Expressing a real restriction requires the ``scope`` column, which
    ``decision_log._scope_permits`` enforces; this function deliberately stays a pure id match,
    because inferring intent from a suffix is exactly the ambiguity that caused the finding.

    Args:
        action_reviewed: The row's ``action_reviewed`` (or ``decision``) id.
        action: The gate action being checked.

    Returns:
        Whether the row is for *action*.
    """
    action_norm = action.strip().lower()
    reviewed_norm = action_reviewed.strip().lower()
    if not action_norm:
        return False
    if reviewed_norm == action_norm:
        return True
    return reviewed_norm.startswith(
        (f"{action_norm}-", f"{action_norm}_", f"{action_norm}.", f"{action_norm}:")
    )


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(_CAMEL.sub(" ", text.strip()).lower()) if t]


def halt_names_action(halt_reviewed: str, action: str) -> bool:
    """Return True when a HALT row's id names *action*: a strict match, or *action* as a token run.

    Args:
        halt_reviewed: The HALT row's ``action_reviewed`` (or ``decision``) id.
        action: The gate action being checked.

    Returns:
        Whether the HALT blocks *action*.
    """
    if action_matches(halt_reviewed, action):
        return True
    wanted, have = _tokens(action), _tokens(halt_reviewed)
    return bool(wanted) and any(have[i:i + len(wanted)] == wanted for i in range(len(have) - len(wanted) + 1))


def newly_blocking_halts(rows: list[dict[str, str]]) -> list[tuple[int, str, str]]:
    """List unretracted HALTs that block a gate action only through the token match.

    These rows did not block under the pre-1.0 prefix-only rule and do now, so a team should
    review them before upgrading (each is cleared by a ``HALT-RETRACTED`` row with the same id).

    Args:
        rows: The decisions-log rows, in file order.

    Returns:
        ``(1-based row number, HALT id, gate action)`` for each pair, in row order.
    """
    found: list[tuple[int, str, str]] = []
    open_ids = _unretracted_halt_ids(rows)
    for index in range(len(rows)):
        reviewed = open_ids[index]
        if not reviewed:
            continue
        for action in GATE_ACTIONS:
            if halt_names_action(reviewed, action) and not action_matches(reviewed, action):
                found.append((index + 1, reviewed, action))
    return found


def describe_newly_blocking_halts(team_dir: Path) -> list[str]:
    """Report lines for :func:`newly_blocking_halts` on a team's decisions log (empty when none).

    Args:
        team_dir: The team root (the log is ``references/security-decisions.log.csv``).

    Returns:
        Human-readable lines, each naming the row, the gate action it blocks and the retraction id.
    """
    # Local import: decision_log imports this module, so a top-level import would be a cycle.
    from agentteams.cli.decision_log import _read_decision_rows

    try:
        read = _read_decision_rows(team_dir)
    except RuntimeError:
        return []  # an unreadable log is reported by the governance-log check
    rows = read[0] if read else []
    hits = newly_blocking_halts(rows)
    named = {n for n, _, _ in hits}
    others = [(i + 1, rid) for i, rid in enumerate(_unretracted_halt_ids(rows)) if rid and i + 1 not in named]
    lines: list[str] = []
    if hits:
        lines.append(f"HALT rows that newly block an agentteams gate action under the 1.0 token rule ({len(hits)}):")
        lines += [f"  row {n}: '{rid}' blocks '{act}'; if it no longer applies, retract it with a "
                  f"HALT-RETRACTED row whose action_reviewed is exactly '{rid}'" for n, rid, act in hits]
    if others:
        lines.append(f"Other unretracted HALT rows ({len(others)}): each also blocks any action your own gates "
                     "check (check_clearance) whose id appears as a token in it:")
        lines += [f"  row {n}: '{rid}'" for n, rid in others]
    return lines


def _unretracted_halt_ids(rows: list[dict[str, str]]) -> list[str]:
    """Per row: the HALT id when the row is an unretracted HALT (by exact id), else ``""``."""
    out: list[str] = []
    for index, row in enumerate(rows):
        reviewed = (row.get("action_reviewed") or row.get("decision") or "").strip()
        is_halt = (row.get("verdict") or row.get("status") or "").strip().upper() == "HALT"
        retracted = any((later.get("verdict") or later.get("status") or "").strip().upper() == "HALT-RETRACTED"
                        and (later.get("action_reviewed") or later.get("decision") or "").strip().lower()
                        == reviewed.lower() for later in rows[index + 1:])
        out.append(reviewed if is_halt and not retracted else "")
    return out


__all__ = ["GATE_ACTIONS", "action_matches", "describe_newly_blocking_halts", "halt_names_action",
           "newly_blocking_halts"]
