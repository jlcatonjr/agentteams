"""retire_bridge_switch.py — CLI flag + dispatch for ``--retire-bridge PAIR``.

Own-module pattern (``branch_switch.py``): ``cli/parser.py`` gains one registrar call,
``cli/parser_validate.py`` one validator call and ``cli/app.py`` one dispatch. The mode is standalone
and reads no brief; it shares ``--project``, ``--team-dir`` and ``--apply`` with the branch modes.
The removal set and the clearance live in :mod:`agentteams.bridge_retire`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def add_retire_bridge_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``--retire-bridge``.

    Args:
        parser: The main CLI argument parser to extend in place.

    Returns:
        None.
    """
    parser.add_argument(
        "--retire-bridge", metavar="PAIR", dest="retire_bridge", default=None,
        help="Remove what one bridge generated in --project (the pair dir's known artifacts and agent/skill "
             "files marked 'bridge: PAIR'); entry files are reported, never touched. Dry run unless --apply; "
             "--apply needs a @security PASS for action 'retire-bridge:PAIR:<sha256>' (consumed). Supported "
             "pair: copilot-vscode-to-claude.",
    )


def retire_bridge_active(args: argparse.Namespace) -> bool:
    """Whether ``--retire-bridge`` was requested.

    Args:
        args: The parsed namespace.

    Returns:
        True when ``--retire-bridge`` is set.
    """
    return bool(getattr(args, "retire_bridge", None))


def validate_retire_bridge_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse ``--retire-bridge`` combined with another mode (argparse ``error`` exits 2).

    Args:
        parser: The main parser (for ``parser.error``).
        args: The parsed namespace.

    Returns:
        None when the combination is valid.

    Raises:
        SystemExit: Via ``parser.error`` (exit 2) on an invalid combination.
    """
    if not retire_bridge_active(args):
        return
    clashes = [flag for flag, set_ in (
        ("--description", getattr(args, "description", None) is not None),
        ("--self", bool(getattr(args, "self_update", False))),
        ("--update", bool(getattr(args, "update", False))),
        ("--output", bool(getattr(args, "output", None))),
        ("--fleet", getattr(args, "fleet", None) is not None),
        ("--bridge-from", bool(getattr(args, "bridge_from", None))),
        ("--sync-agent-docs", bool(getattr(args, "sync_agent_docs", False))),
        ("--branch-inventory", bool(getattr(args, "branch_inventory", False))),
        ("--branch-cleanup", bool(getattr(args, "branch_cleanup", None))),
        ("--branch-post-merge", bool(getattr(args, "branch_post_merge", None))),
    ) if set_]
    if clashes:
        parser.error(f"--retire-bridge is standalone; it cannot be combined with {', '.join(clashes)}")


def run_retire_bridge_cli(args: argparse.Namespace) -> int:
    """Dispatch ``--retire-bridge``.

    Args:
        args: Parsed CLI namespace with ``--retire-bridge`` set.

    Returns:
        Exit code: 0 done/dry run, 1 refused or failed, 3 no authorization (operator step).
    """
    from agentteams import bridge_retire
    from agentteams.cli.branch_switch import TeamDirError, resolve_team_dir, team_dir_hint

    project = Path(args.project or ".").resolve()
    try:
        team_dir = resolve_team_dir(project, getattr(args, "team_dir", None))
        action = bridge_retire.build_plan(project, args.retire_bridge).action
        code, lines = bridge_retire.run_retire(project, args.retire_bridge, team_dir=team_dir,
                                               apply=bool(getattr(args, "sync_apply", False)))
        if not getattr(args, "team_dir", None):
            lines += team_dir_hint(project, team_dir, action, code, lines)
    except (bridge_retire.RetireError, TeamDirError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return bridge_retire.EXIT_FAIL
    for line in lines:
        print(line)
    return code
