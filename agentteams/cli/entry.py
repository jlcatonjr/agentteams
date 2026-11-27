"""entry.py — the ``agentteams`` console script (1.3.2; operator ruling 2026-11-26).

A ``--merge-pr`` invocation goes straight to :mod:`agentteams.cli.merge_pr_main`, which loads only the merge gate's
integrity-pinned modules. Every other invocation imports ``build_team`` (the full CLI and render pipeline) as before.
This module imports nothing from agentteams at module level, so nothing unpinned runs before the gate.

Stdlib only.
"""

from __future__ import annotations

import sys


def _is_merge_pr(argv: list[str]) -> bool:
    return any(a == "--merge-pr" or a.startswith("--merge-pr=") for a in argv)


def main(argv: list[str] | None = None) -> int:
    """Run ``agentteams``.

    Args:
        argv: The arguments (default: ``sys.argv[1:]``).

    Returns:
        The process exit status.
    """
    args = sys.argv[1:] if argv is None else argv
    if _is_merge_pr(args):
        from agentteams.cli import merge_pr_main

        return merge_pr_main.main(args)
    import build_team

    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    return build_team.main()
