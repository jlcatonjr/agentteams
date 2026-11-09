"""clearance_binding.py — "is there a verified @security PASS for scope X bound to this file's exact bytes?"

Operator scripts that must run only as reviewed used to hand-roll this gate (the manager-roster rollout's script took
twelve review rounds to bind its clearance to its own sha256, 2026-11-08). :func:`check_file_clearance` is the shared
answer, exposed as ``agentteams --check-clearance SCOPE --clearance-file PATH``. The carve-out registry
(:mod:`agentteams.carveouts`) uses it for an entry's declared ``clearance``.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def clearance_action(scope: str, path: Path) -> str:
    """The gate action id binding *scope* to *path*'s exact bytes: ``<scope>:sha256-<hex>``.

    ``action_matches`` lets a row's id carry a suffix but never lets a shorter id match a longer action, so a row for
    the bare scope, or for another file's sha, can't satisfy this action.

    Args:
        scope: The clearance scope (an action id).
        path: The file the clearance is bound to.

    Returns:
        The action id.

    Raises:
        ValueError: *scope* is not a plain action id.
        OSError: *path* can't be read.
    """
    if not _SCOPE_RE.match(scope):
        raise ValueError(f"'{scope}' is not a plain action id")
    return f"{scope}:sha256-{hashlib.sha256(path.read_bytes()).hexdigest()}"


def check_file_clearance(team_dir: Path, scope: str, path: Path) -> tuple[bool, str]:
    """Whether a verified @security PASS exists for *scope* bound to *path*'s sha256, with no HALT on *scope* itself.

    Read-only: spends nothing. The caller must then use the bytes it checked (hash, then run the same file).

    Args:
        team_dir: The team directory holding ``references/security-decisions.log.csv``.
        scope: The clearance scope.
        path: The file the clearance must be bound to.

    Returns:
        ``(cleared, reason)``; *reason* says why not, and names the action id a clearance row must use.
    """
    from agentteams.cli.decision_log import _assert_no_unretracted_halt
    from agentteams.cli.security_gate import check_clearance

    try:
        action = clearance_action(scope, path)
    except (ValueError, OSError) as exc:
        return False, str(exc)
    try:  # a HALT on the bare scope stops every sha-bound clearance under it (C-2)
        _assert_no_unretracted_halt(team_dir, action=scope)
    except RuntimeError as exc:
        return False, str(exc)
    ok, reason = check_clearance(team_dir, action=action)
    return ok, "" if ok else f"{reason} (a clearance for this file's exact bytes uses action_reviewed '{action}')"
