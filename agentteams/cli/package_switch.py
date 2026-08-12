"""
package_switch.py — CLI flags + dispatch for --package-team (open-items
remediation OPEN-5). Mirrors goose_switch.py's own-module pattern: cli/parser.py
is near its own CH-07 line ceiling, so a standalone-mode flag group with real
option surface gets its own module rather than growing parser.py in place.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def add_package_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the --package-team portable-archive flags on the main parser."""
    group = parser.add_argument_group("portable team package")
    group.add_argument(
        "--package-team",
        dest="package_team",
        metavar="SOURCE_DIR",
        default=None,
        help=(
            "Package a native-framework source team as a portable zip: a "
            "durable canonical directory (.agentteams/canonical/) plus its "
            "generic bridge, both derived from the same export — not a "
            "canonical directory itself (use --bridge-from ... --bridge-"
            "source-framework canonical --framework generic for that case)."
        ),
    )
    group.add_argument(
        "--package-source-framework",
        dest="package_source_framework",
        metavar="NAME",
        default=None,
        help="Optional source framework override for --package-team (auto-detected when omitted).",
    )


def run_package_team(args: argparse.Namespace) -> int:
    """Dispatch --package-team. Returns an exit code (0 ok, 1 error)."""
    from agentteams import security_refs
    from agentteams.cli import security_gate
    from agentteams.team_package import package_team

    source_dir = Path(args.package_team).resolve()
    output_zip = Path(args.output).resolve() if args.output else Path.cwd() / "team-package.zip"
    dry_run = bool(getattr(args, "dry_run", False))

    if not dry_run:
        placeholders = security_refs.build_security_placeholders(
            output_dir=output_zip.parent, offline=False, max_items=1, tools=None, skip_nvd=True,
        )
        security_gate._assert_security_intelligence_fresh(placeholders, output_dir=output_zip.parent)

    dry_label = " (dry-run)" if dry_run else ""
    print(
        f"Packaging team{dry_label}:\n"
        f"  source: {source_dir}\n"
        f"  output: {output_zip}"
    )

    try:
        result = package_team(
            source_dir=source_dir,
            output_zip=output_zip,
            source_framework=getattr(args, "package_source_framework", None),
            dry_run=dry_run,
            overwrite=bool(getattr(args, "overwrite", False)),
        )
    except (ValueError, FileNotFoundError, FileExistsError, IsADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not result.success:
        for err in result.errors:
            print(f"  ✗ {err}", file=sys.stderr)
        return 1

    for notice in result.notices:
        print(f"  {notice}")
    if not dry_run:
        print(f"  ✓ Wrote {result.zip_path} ({result.agent_count} agent(s), source: {result.source_framework})")
    return 0
