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

#: Separators that split an action id into tokens for the HALT-side match.
_TOKEN_SPLIT = re.compile(r"[-_.:/\s]+")


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
    return [t for t in _TOKEN_SPLIT.split(text.strip().lower()) if t]


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


__all__ = ["action_matches", "halt_names_action"]
