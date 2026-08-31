"""
fleet_switch.py — CLI flags for the multi-workspace `--fleet` update mode.
Mirrors goose_switch.py / package_switch.py's own-module pattern: cli/parser.py
is at its CH-07 line ceiling, so this cohesive argument group lives in its own
module rather than growing parser.py in place.
"""

from __future__ import annotations

import argparse


def add_fleet_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the --fleet multi-workspace update flags on the main parser."""
    fleet_group = parser.add_argument_group("fleet update (multi-workspace)")
    fleet_group.add_argument(
        "--fleet",
        metavar="DIR",
        default=None,
        help=(
            "Update every agent-infrastructure workspace under DIR and its "
            "subfolders with --update --merge. Discovers .github/agents/ and "
            ".claude/ targets, snapshots each git workspace via a commit, applies "
            "the merge, then analyses the diff. Default is a DRY-RUN preview; pass "
            "--yes to apply. Non-destructive: merge-only; .claude is bridge-merged."
        ),
    )
    fleet_group.add_argument(
        "--fleet-frameworks",
        choices=["github", "claude", "goose", "both", "all"],
        default="both",
        help=(
            "Which infrastructures to update per workspace (default: both). "
            "`both` = copilot-vscode + claude (backward-compatible). "
            "`all` = copilot-vscode + claude + goose. "
            "`goose` = Goose workspaces only."
        ),
    )
    fleet_group.add_argument(
        "--fleet-report",
        metavar="DIR",
        default=None,
        help="Directory for the fleet report (default: <DIR>/.agentteams-fleet/<run-id>/).",
    )
    fleet_group.add_argument(
        "--fleet-allow-no-verify",
        action="store_true",
        default=False,
        dest="fleet_allow_no_verify",
        help=(
            "Allow snapshot commits to bypass pre-commit hooks (--no-verify / "
            "core.hooksPath=/dev/null). Off by default — hooks run normally and a "
            "warning is printed if a hook blocks the snapshot. Use this flag only "
            "when workspace hooks are known-safe to skip (e.g., a commit-signing "
            "hook that would reject the ephemeral internal snapshot commit)."
        ),
    )
