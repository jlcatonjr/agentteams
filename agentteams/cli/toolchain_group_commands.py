"""toolchain_group_commands.py — the CLI verbs for the first-party toolchain group.

``--sign-toolchain-group FILE`` (operator-only), ``--verify-toolchain-group`` and ``--verify-toolchain-pin LOCKFILE``
(both read-only). The logic lives in :mod:`agentteams.cli.toolchain_group`; this module only resolves the team dir,
project root, governance dir and team id from the CLI arguments.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentteams.cli import toolchain_group as tg
from agentteams.cli.commands_output import _resolve_output_dir


def _team_id(args: argparse.Namespace) -> str | None:
    """``--team-id``, else the brief's ``team_id``, else its slugified ``project_name`` (as ``analyze`` derives it)."""
    if getattr(args, "team_id", None):
        return str(args.team_id).strip()
    brief = getattr(args, "description", None)
    if not brief:
        return None
    try:
        description = json.loads(Path(brief).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    from agentteams.analyze import _slugify

    return description.get("team_id") or _slugify(str(description.get("project_name") or "")).lstrip("-") or None


def _resolve(args: argparse.Namespace) -> tuple[Path, Path, Path, tuple[Path, ...]] | None:
    """Return ``(team_dir, project_root, governance_dir, halt_dirs)``, or None with a message."""
    from agentteams.cli.s10_exempt_gate import _project_root_of, _surface_dirs, governance_team_dir

    team_dir = _resolve_output_dir(args)
    root = _project_root_of(team_dir)
    if root is None:
        print(f"Error: {team_dir} is not a team dir in a default layout (e.g. --output .github/agents).",
              file=sys.stderr)
        return None
    try:
        governance = governance_team_dir(team_dir)
    except RuntimeError as exc:
        print(f"Error: the signing switch cannot be read: {exc}", file=sys.stderr)
        return None
    return team_dir, root, governance, (team_dir, *_surface_dirs(root))


def _member_filter(governance: Path, halt_dirs: tuple[Path, ...]):
    """The render gate's per-member HALT filter, bound to these dirs (C-2)."""
    from agentteams.cli.s10_exempt_gate import drop_halted_members

    return lambda repos: drop_halted_members(repos, (governance, *halt_dirs))


def run_toolchain_group(args: argparse.Namespace) -> int:
    """Dispatch the toolchain-group verb named by *args*.

    Args:
        args: Parsed CLI namespace.

    Returns:
        0 on success / verified, 1 otherwise (fail-closed).
    """
    if getattr(args, "sign_toolchain_group", None):
        return tg.sign_group_file(Path(args.sign_toolchain_group), _resolve_output_dir(args),
                                  confirm_sha256=getattr(args, "confirm_review_sha256", None),
                                  allow_checkout=bool(getattr(args, "allow_checkout_signing", False)))
    resolved = _resolve(args)
    if resolved is None:
        return 1
    _team_dir, root, governance, halt_dirs = resolved
    team_id = _team_id(args)
    if not team_id:
        print("Error: name this workspace's team id with --team-id (or --description <brief>).", file=sys.stderr)
        return 1
    if getattr(args, "verify_toolchain_group", False):
        return tg.verify_group_command(root, governance, team_id, halt_dirs, member_filter=_member_filter(governance,
                                                                                                          halt_dirs))
    if not getattr(args, "allowed_signers", None):
        print("Error: --verify-toolchain-pin needs --allowed-signers <file naming the release key>.", file=sys.stderr)
        return 1
    result = tg.load_group(root, governance, team_id=team_id, halt_dirs=halt_dirs, consume=False)
    if result.present and not result.problem:
        result.repos = _member_filter(governance, halt_dirs)(result.repos)
    if result.problem:
        tg.report(result)
        return 1
    if not result.present:
        print(f"No {tg.GROUP_FILE_REL} here: no pin is under the toolchain-group rule, so nothing was checked "
              "(exit 2).", file=sys.stderr)
        return 2
    checked: list[str] = []
    try:
        problems = tg.verify_pins(Path(args.verify_toolchain_pin), result.repos, Path(args.allowed_signers),
                                  checked=checked)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Error: cannot read {args.verify_toolchain_pin}: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"::error title=Toolchain pin not verified::{problem}", file=sys.stderr)
    if problems:
        return 1
    if not checked:
        print(f"Error: {args.verify_toolchain_pin} pins no toolchain-group member, so nothing was verified "
              "(fail-closed).", file=sys.stderr)
        return 1
    print(f"{len(checked)} pin(s) to toolchain-group members in {args.verify_toolchain_pin} verify: "
          f"{', '.join(checked)}.")
    return 0


__all__ = ["run_toolchain_group"]
