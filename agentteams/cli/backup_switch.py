"""
backup_switch.py — CLI flags for the staleness-check and backup-lifecycle
modes (--stale-*, --prune-backups, --keep-within-days, --backup-mirror).
Mirrors goose_switch.py / package_switch.py / fleet_switch.py's own-module
pattern: cli/parser.py is at its CH-07 line ceiling, so this cohesive argument
group lives in its own module rather than growing parser.py in place.
"""

from __future__ import annotations

import argparse

from agentteams.emit import DEFAULT_BACKUP_KEEP_LAST


def add_stale_and_backup_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the staleness-check and backup-lifecycle flags on the main parser."""
    parser.add_argument(
        "--stale-check", action="store_true", dest="stale_check", default=False,
        help="Read-only: scan --output/--project (else CWD) for stale agent docs and "
             "code/scripts (VCS conflict markers, broken references, git-recency "
             "divergence, provenance-gated generated-file integrity). Exits non-zero "
             "on any Tier-1 (blocking) finding. Never edits files.",
    )
    parser.add_argument(
        "--stale-remediate", action="store_true", dest="stale_remediate", default=False,
        help="Modifier for --stale-check: also print a guided remediation plan "
             "(suggestions only; does NOT edit files, unlike --auto-correct).",
    )
    parser.add_argument(
        "--stale-no-git", action="store_true", dest="stale_no_git", default=False,
        help="Modifier for --stale-check: skip the Tier-2 git-recency signal "
             "(hermetic/CI or non-git targets).",
    )
    parser.add_argument(
        "--stale-restore", nargs="?", const="latest", default=None, metavar="TS",
        dest="stale_restore",
        help="Standalone: restore files from a --stale-remediate --yes safety snapshot "
             "(.agentteams-backups/stale-fix-<TS>/; default: latest). Recovery path for a "
             "revision that went wrong.",
    )
    parser.add_argument(
        "--prune-backups",
        nargs="?",
        type=int,
        const=DEFAULT_BACKUP_KEEP_LAST,
        default=None,
        metavar="KEEP",
        dest="prune_backups",
        help=(
            "Standalone: delete old timestamped backups under --output/--project "
            f"(else CWD) .agentteams-backups/, keeping the newest KEEP (default "
            f"{DEFAULT_BACKUP_KEEP_LAST}). The single newest backup is NEVER "
            "deleted, even with KEEP 0. Combine with --keep-within-days to also "
            "retain anything younger than N days, and --dry-run to preview. This "
            "bounds backup growth; distinct from --prune (which removes stale "
            "*agents*, not backups)."
        ),
    )
    parser.add_argument(
        "--keep-within-days",
        type=int,
        default=None,
        metavar="DAYS",
        dest="keep_within_days",
        help=(
            "Modifier for --prune-backups: in addition to the newest KEEP, retain "
            "any backup younger than DAYS (a backup with an unparseable timestamp "
            "is always kept, fail-safe)."
        ),
    )
    parser.add_argument(
        "--backup-mirror",
        default=None,
        metavar="DIR",
        dest="backup_mirror",
        help=(
            "Modifier for --update: after a backup is written, also copy it to "
            "DIR/<output-slug>/<timestamp>/ (e.g. a NAS or synced folder) so the "
            "recovery net survives local disk loss. Best-effort and non-fatal — a "
            "mirror failure warns but never breaks the update. Overrides the "
            "AGENTTEAMS_BACKUP_MIRROR environment variable."
        ),
    )
