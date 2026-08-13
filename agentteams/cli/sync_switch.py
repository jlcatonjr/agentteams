"""sync_switch.py — CLI flags + dispatch for multi-framework pinned sync.

Mirrors goose_switch.py / package_switch.py / fleet_switch.py's own-module
pattern: cli/parser.py is at its CH-07 line ceiling, so this standalone-mode
flag group + dispatch lives in its own module.

Flags:
- ``--sync-init --pin <framework>`` bootstraps the pin (seeds canonical from the
  pinned framework, projects to the whole sync set, records baselines).
- ``--sync`` runs one on-demand pass (detects changed frameworks from commit
  diffs, reconciles to the pin, projects to all, logs conflicts).
- ``--sync-since <commit>`` overrides the change-detection anchor.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def add_sync_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the multi-framework pinned-sync flags on the main parser."""
    group = parser.add_argument_group("multi-framework pinned sync")
    group.add_argument(
        "--sync-init",
        action="store_true",
        dest="sync_init",
        default=False,
        help=(
            "Bootstrap pinned multi-framework sync: seed the canonical hub from "
            "the --pin framework, project it to every framework in the sync set, "
            "and record per-framework baselines. Writes .agentteams/pin.json."
        ),
    )
    group.add_argument(
        "--pin",
        metavar="FRAMEWORK",
        default=None,
        dest="pin",
        help=(
            "The bootstrap-pin framework for --sync-init. Seeds canonical and "
            "wins every conflict thereafter (each conflict is logged for review)."
        ),
    )
    group.add_argument(
        "--sync",
        action="store_true",
        dest="sync",
        default=False,
        help=(
            "Run one on-demand sync pass: detect frameworks changed since the "
            "last synced commit, reconcile changes to the pin (pin wins conflicts, "
            "peer capability changes are withheld and logged), project canonical "
            "to every framework, and advance the sync anchor."
        ),
    )
    group.add_argument(
        "--sync-since",
        metavar="COMMIT",
        default=None,
        dest="sync_since",
        help="Override the change-detection anchor (default: pin's last_synced_commit).",
    )


def run_sync_cli(args: argparse.Namespace) -> int:
    """Dispatch --sync-init / --sync. Returns a process exit code."""
    from agentteams.multi_sync import CONFLICT_LOG_SUBPATH, run_sync, sync_init

    root = Path(getattr(args, "project", None) or ".").resolve()
    dry = bool(getattr(args, "dry_run", False))
    dry_label = " (dry-run)" if dry else ""

    if getattr(args, "sync_init", False):
        pin = getattr(args, "pin", None)
        if not pin:
            print("Error: --sync-init requires --pin <framework>", file=sys.stderr)
            return 2
        try:
            result = sync_init(root, pin=pin, dry_run=dry)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print(f"Pinned sync initialized{dry_label}:")
        print(f"  {result.note}")
        print(f"  projected to: {', '.join(result.projected_frameworks)}")
        return 0

    try:
        result = run_sync(root, since=getattr(args, "sync_since", None), dry_run=dry)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Sync{dry_label}: {result.note}")
    if result.changed_frameworks:
        print(f"  changed:   {', '.join(result.changed_frameworks)}")
        print(f"  projected: {', '.join(result.projected_frameworks)}")
    if result.conflicts:
        print(
            f"  {len(result.conflicts)} conflict(s) resolved to the pin — "
            f"logged to {CONFLICT_LOG_SUBPATH} for review."
        )
    return 0
