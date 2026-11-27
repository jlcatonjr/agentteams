"""merge_pr_main.py — the dedicated entry point for ``--merge-pr`` (1.3.2; operator ruling 2026-11-26).

``agentteams --merge-pr N`` (the console script sends it here, see :mod:`agentteams.cli.entry`) or
``python -m agentteams.cli.merge_pr_main --merge-pr N`` merges pull request *N* into the
integration branch without the gate's prompt, under the signed authorization. It loads only the merge gate's own
modules, every one of them integrity-pinned (``tests/test_merge_pr_entry_tree.py`` imports the route and the gate's
deferred imports in a fresh interpreter and checks what loaded; ``tests/test_dev_merge_import_tree_pinned.py`` checks
the same tree from the source), so no
module outside the pinned trust path runs before the gate. The full ``agentteams`` CLI loads ~190 modules, most of
them the render pipeline; ``python build_team.py --merge-pr`` therefore refuses and points here.

Stdlib only.
"""

from __future__ import annotations

import argparse
import sys

from agentteams.cli.dev_merge_commands import run_dev_merge


def build_parser() -> argparse.ArgumentParser:
    """The entry point's arguments: the same names the full CLI uses for ``--merge-pr``.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="agentteams --merge-pr",
        description="Merge a pull request into the integration branch under the signed authorization "
                    "(exit 0 merged, 2 not authorized: ask the operator, 1 an error).")
    parser.add_argument("--merge-pr", dest="merge_pr", type=int, metavar="N", required=True,
                        help="the pull-request number")
    parser.add_argument("--project", default=None, help="the project root (default: the current directory)")
    parser.add_argument("--output", default=None, help="the governed team dir (default: resolved from the project)")
    parser.add_argument("--team-id", dest="team_id", default=None, help="this workspace's team id")
    parser.add_argument("--description", default=None, help="the project brief (to read the team id)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run ``agentteams-merge-pr``.

    Args:
        argv: The arguments (default: ``sys.argv[1:]``).

    Returns:
        0 merged; 2 a condition does not hold (ask the operator); 1 an error.
    """
    args = build_parser().parse_args(argv)
    args.sign_dev_merge_authorization = None      # the attributes the shared resolvers read
    args.verify_dev_merge_authorization = False
    args.sign_toolchain_group = None
    args.verify_toolchain_pin = None
    args.allow_checkout_signing = False
    return run_dev_merge(args)


if __name__ == "__main__":   # pragma: no cover - exercised through the console script
    sys.exit(main())
