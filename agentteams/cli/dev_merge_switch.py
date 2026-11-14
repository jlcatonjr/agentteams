"""dev_merge_switch.py — the integration-branch merge authorization's CLI flags (split from parser.py, CH-07).

Stdlib only.
"""

from __future__ import annotations

import argparse


def add_dev_merge_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--merge-pr``, ``--sign-dev-merge-authorization`` and ``--verify-dev-merge-authorization``.

    Args:
        parser: The agentteams argument parser.

    Returns:
        None.
    """
    parser.add_argument(
        "--merge-pr",
        dest="merge_pr",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Merge pull request N into the integration branch without the gate's merge prompt, under the operator-signed "
            "integration-branch merge authorization (.agentteams/dev-merge-authorization.json; the constitution's "
            "Integration-branch merge authorization section). Refuses (exit 2: ask the operator) unless the file verifies "
            "for this workspace and branch model, the PR is open, not a draft, not from a fork, based on the signed "
            "branch, CLEAN, with every check passed (at least one) and no governance, agent or workflow path, and a "
            "recorded @security PASS names dev-merge:<owner/name>:pr-<N>-<head sha> (spent on use). Merges with "
            "--merge --match-head-commit. 0 merged; 1 an error or a post-merge alarm. --output names the team dir."
        ),
    )
    parser.add_argument(
        "--sign-dev-merge-authorization",
        dest="sign_dev_merge_authorization",
        default=None,
        metavar="FILE",
        help=(
            "Operator-only, refused inside an agent session: sign the integration-branch merge authorization file "
            "(version, scope, branch, workspaces, purpose, sequence, key_id, derives_from) in place with the Ed25519 "
            "decision key, after the same preflight, review digest and confirmation as --sign-decision. --output names "
            "a governed team dir whose references/authorized-verify-keys holds <key_id>.pub.pem."
        ),
    )
    parser.add_argument(
        "--verify-dev-merge-authorization",
        action="store_true",
        dest="verify_dev_merge_authorization",
        default=False,
        help=(
            "Read-only: verify this workspace's .agentteams/dev-merge-authorization.json (signature, workspace binding by "
            "team id and git origin, HALTs, lineage, sequence) against --output's governed team dir and the brief's "
            "integration branch. Exit 0 when --merge-pr would be authorized here, 1 otherwise."
        ),
    )


__all__ = ["add_dev_merge_arguments"]
