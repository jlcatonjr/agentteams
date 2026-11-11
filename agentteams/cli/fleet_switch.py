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
            "subfolders with --update --merge (except --fleet-exclude matches and "
            "release-locked workspaces). Discovers .github/agents/ and "
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
            "`all` = copilot-vscode + claude + goose + codex + grok (a .codex/agents or "
            ".grok/agents team with a marker; an interop projection gets its refresh command, never an "
            "--update; a symlinked .grok tree is skipped). "
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
        "--fleet-exclude",
        metavar="GLOB",
        action="append",
        default=None,
        dest="fleet_exclude",
        help=(
            "Skip folders under DIR matching GLOB (repeatable). A pattern matches the "
            "folder's path relative to DIR or its name (e.g. 'agentteams*'). A matched "
            "folder is neither updated nor descended into, and gets a SKIP row."
        ),
    )
    fleet_group.add_argument(
        "--fleet-skip-parent",
        action="store_true",
        default=False,
        dest="fleet_skip_parent",
        help=(
            "Do not update DIR's own workspace (it gets a SKIP row); workspaces below DIR "
            "are unaffected. For a DIR that is a non-git workspace with no snapshot rollback, "
            "which --fleet-exclude cannot name."
        ),
    )
    fleet_group.add_argument(
        "--fleet-ignore-locks",
        metavar="GLOB",
        action="append",
        default=None,
        dest="fleet_ignore_locks",
        help=(
            "Render the workspaces of release-locked repositories matching GLOB anyway "
            "(repeatable; GLOB matches the locked repository's folder, by path relative to DIR "
            "or by name; '*' for all). By default, a workspace whose repository's "
            "toolchain.lock pins agentteams (agentteams= / agentteams.tag=) is skipped with a "
            "SKIP row, because it moves only through its own pin process."
        ),
    )
    fleet_group.add_argument(
        "--fleet-allow-no-verify",
        action="store_true",
        default=False,
        dest="fleet_allow_no_verify",
        help=(
            "No effect (kept so existing invocations work): fleet snapshots are "
            "written to refs/agentteams/fleet-snapshots/ without git commit, so no "
            "commit hook runs and nothing lands on the checked-out branch."
        ),
    )
