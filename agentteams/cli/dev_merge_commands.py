"""dev_merge_commands.py — the CLI verbs for the integration-branch merge authorization.

``--sign-dev-merge-authorization FILE`` (operator-only), ``--verify-dev-merge-authorization`` (read-only) and
``--merge-pr N``. The logic lives in :mod:`agentteams.cli.dev_merge`; this module resolves the team dir, project root,
governance dir, team id and integration branch from the CLI arguments, as the toolchain-group verbs do.

Stdlib only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentteams.cli import dev_merge as dm
from agentteams.cli.commands_output import _resolve_output_dir
from agentteams.cli.toolchain_group_commands import _resolve, _team_id


def _integration_branch(root: Path) -> str | None:
    """The brief's integration branch, or None (trunk, no brief, or a malformed block: refused, never guessed)."""
    from agentteams.branch_model import integration_branch_from_project

    try:
        return integration_branch_from_project(root)
    except ValueError as exc:
        print(f"Error: the brief's git_workflow block is malformed ({exc}).", file=sys.stderr)
        return None


def run_dev_merge(args: argparse.Namespace) -> int:
    """Dispatch the integration-branch merge verb named by *args*.

    Args:
        args: Parsed CLI namespace.

    Returns:
        ``--merge-pr``: 0 merged, 2 not authorized (ask the operator), 1 an error. The verify verb: 0 or 1. The sign
        verb: 0 signed, 1 otherwise.
    """
    if getattr(args, "sign_dev_merge_authorization", None):
        return dm.sign_file(Path(args.sign_dev_merge_authorization), _resolve_output_dir(args),
                            confirm_sha256=getattr(args, "confirm_review_sha256", None),
                            allow_checkout=bool(getattr(args, "allow_checkout_signing", False)))
    resolved = _resolve(args, "no integration-branch merge authorization applies here; every merge goes through the "
                        "gate's prompt.")
    if resolved is None:
        return 2 if getattr(args, "merge_pr", None) is not None else 1
    _team_dir, root, governance, halt_dirs = resolved
    team_id = _team_id(args, root)
    if not team_id:
        print("Error: name this workspace's team id with --team-id (or --description <brief>).", file=sys.stderr)
        return 1
    branch = _integration_branch(root)
    if getattr(args, "verify_dev_merge_authorization", False):
        return dm.verify_command(root, governance, team_id, halt_dirs, integration_branch=branch)
    return dm.merge_pr(root, governance, team_id=team_id, number=args.merge_pr, integration_branch=branch,
                       halt_dirs=halt_dirs)


__all__ = ["run_dev_merge"]
