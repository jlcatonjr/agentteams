"""branch_switch.py — CLI flags + dispatch for the branch lifecycle modes.

Own-module pattern (``agent_doc_sync_switch.py``, ``sync_switch.py``): ``cli/parser.py`` gains one
registrar call and ``cli/app.py`` one dispatch. All three modes are standalone and read no brief:

* ``--branch-inventory`` — read-only classification + deletion plan
  (:mod:`agentteams.branch_inventory`);
* ``--branch-cleanup PLAN.json`` — execute a plan under a ``@security`` clearance
  (:func:`agentteams.branch_cleanup.run_cleanup`); dry run unless ``--apply``;
* ``--branch-post-merge BRANCH`` — delete the branch just merged ``--no-ff`` under an operator
  Ed25519 ``branch-delete`` grant (:func:`agentteams.branch_cleanup.run_post_merge`); dry run
  unless ``--apply``.

The procedure they implement is the emitted ``references/branch-lifecycle.reference.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Team agents dirs probed (in order) for the security log / grant trust anchors.
_TEAM_DIR_CANDIDATES: tuple[str, ...] = (".claude/agents", ".github/agents", ".codex/agents")


def add_branch_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the branch lifecycle flags.

    Args:
        parser: The main CLI argument parser to extend in place.

    Returns:
        None. The parser gains a "branch lifecycle" group.
    """
    group = parser.add_argument_group("branch lifecycle")
    group.add_argument(
        "--branch-inventory", action="store_true", dest="branch_inventory", default=False,
        help="Read-only: classify every local branch and every branch on the push remote of "
             "--project (default: .) against the default branch, apply holds, and derive a "
             "deletion plan of leased commands. Never writes a ref (runs git fetch --prune).",
    )
    group.add_argument(
        "--branch-cleanup", metavar="PLAN.json", dest="branch_cleanup", default=None,
        help="Execute a --branch-inventory deletion plan. Dry run unless --apply; --apply needs "
             "a @security PASS for action 'branch-cleanup:<plan sha256>' (consumed).",
    )
    group.add_argument(
        "--branch-post-merge", metavar="BRANCH", dest="branch_post_merge", default=None,
        help="Delete BRANCH just merged --no-ff (its tip must be the second parent of the push "
             "remote's default tip). Dry run unless --apply; --apply needs a valid operator "
             "Ed25519 'branch-delete' capability grant (exit 3 when none).",
    )
    group.add_argument(
        "--branch-report", metavar="DIR", dest="branch_report", default=None,
        help="With --branch-inventory: write branch-inventory.csv/.json and "
             "branch-deletion-plan.json into DIR.",
    )
    group.add_argument("--push-remote", metavar="NAME", dest="push_remote", default="origin",
                       help="The only remote branch modes act on (default: origin).")
    group.add_argument("--default-branch", metavar="NAME", dest="default_branch", default=None,
                       help="Default branch (default: the push remote's HEAD).")
    group.add_argument("--stale-days", metavar="N", type=int, dest="stale_days", default=14,
                       help="Days without activity before unmerged work is stale (default: 14).")
    group.add_argument(
        "--operator-email", metavar="EMAIL", action="append", dest="operator_emails", default=[],
        help="Author email treated as the operator (repeatable; git config user.email is always "
             "included). Other authors place an Owner hold.",
    )
    group.add_argument("--no-api", action="store_true", dest="branch_no_api", default=False,
                       help="Do not query GitHub (no Merged-by-PR; no remote deletions planned).")
    group.add_argument(
        "--team-dir", metavar="DIR", dest="team_dir", default=None,
        help="Team agents dir holding the security log, roster and verify keys: one of "
             ".claude/agents, .github/agents or .codex/agents under --project (default: the "
             "first that exists). Any other path is refused.",
    )
    group.add_argument("--team-id", metavar="ID", dest="team_id", default=None,
                       help="Grant holder id for --branch-post-merge (default: from build-log).")


def branch_mode_active(args: argparse.Namespace) -> bool:
    """Whether any branch lifecycle mode was requested.

    Args:
        args: The parsed namespace.

    Returns:
        True when ``--branch-inventory``, ``--branch-cleanup`` or ``--branch-post-merge`` is set.
    """
    return bool(getattr(args, "branch_inventory", False) or getattr(args, "branch_cleanup", None)
                or getattr(args, "branch_post_merge", None))


def validate_branch_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse incoherent branch flag combinations (argparse ``error`` exits 2).

    Args:
        parser: The main parser (for ``parser.error``).
        args: The parsed namespace.

    Returns:
        None when the combination is valid.

    Raises:
        SystemExit: Via ``parser.error`` (exit 2) on an invalid combination.
    """
    modes = [f for f, on in (
        ("--branch-inventory", getattr(args, "branch_inventory", False)),
        ("--branch-cleanup", getattr(args, "branch_cleanup", None)),
        ("--branch-post-merge", getattr(args, "branch_post_merge", None)),
    ) if on]
    if len(modes) > 1:
        parser.error(f"{' and '.join(modes)} are mutually exclusive")
    if getattr(args, "branch_report", None) and not getattr(args, "branch_inventory", False):
        parser.error("--branch-report requires --branch-inventory")
    if not modes:
        return
    if getattr(args, "stale_days", 14) < 1:
        parser.error("--stale-days must be at least 1")
    clashes = [flag for flag, set_ in (
        ("--description", getattr(args, "description", None) is not None),
        ("--self", bool(getattr(args, "self_update", False))),
        ("--update", bool(getattr(args, "update", False))),
        ("--output", bool(getattr(args, "output", None))),
        ("--fleet", getattr(args, "fleet", None) is not None),
        ("--sync-agent-docs", bool(getattr(args, "sync_agent_docs", False))),
    ) if set_]
    if clashes:
        parser.error(f"{modes[0]} is standalone; it cannot be combined with {', '.join(clashes)}")


class TeamDirError(ValueError):
    """Raised when ``--team-dir`` is not one of the project's own team agents dirs."""


def _team_dir(project: Path, explicit: str | None) -> Path:
    """Resolve the team agents dir — only ever one of the project's own.

    The team dir supplies every trust anchor these modes rely on: the verify-key store, the
    approver roster and the security-decisions log. Accepting an arbitrary path would let an
    agent point it at a scratch directory holding its own key, roster and PASS row and authorize
    itself (security review B1, 2026-10-04), bypassing the sandbox's write-deny on the real dirs.

    Args:
        project: The resolved project root.
        explicit: The ``--team-dir`` value, if any.

    Returns:
        The resolved team dir.

    Raises:
        TeamDirError: ``explicit`` does not resolve to ``<project>/{.claude,.github,.codex}/agents``.
    """
    allowed = {(project / rel).resolve() for rel in _TEAM_DIR_CANDIDATES}
    if explicit:
        chosen = Path(explicit).resolve()
        if chosen not in allowed:
            raise TeamDirError(
                f"--team-dir {explicit!r} is not one of this project's team dirs "
                f"({', '.join(_TEAM_DIR_CANDIDATES)}); refusing an external trust root"
            )
        return chosen
    for rel in _TEAM_DIR_CANDIDATES:
        if (project / rel / "references").is_dir():
            return (project / rel).resolve()
    return (project / _TEAM_DIR_CANDIDATES[0]).resolve()


def _print_table(inventory: dict) -> None:
    meta = inventory["meta"]
    print(f"Branch inventory — {meta['repo']} ({meta['push_remote']}/{meta['default_branch']} at "
          f"{meta['default_tip'][:7]}; api {meta['api_status']})")
    for rec in inventory["refs"]:
        holds = ",".join(rec["holds"]) or "-"
        print(f"  {rec['ref_type']:6} {rec['branch']:44} {rec['state']:17} {rec['action']:15} "
              f"{rec['via'] or '-':8} holds={holds}")
    for note in meta["notes"]:
        print(f"  NOTE: {note}")
    plan = inventory["plan"]
    print(f"Deletion plan: {len(plan['items'])} item(s), sha256 {plan['sha256'][:12]} "
          f"(clearance action branch-cleanup:{plan['sha256']})")


def run_branch_cli(args: argparse.Namespace) -> int:
    """Run the requested branch mode for a validated namespace.

    Args:
        args: Parsed CLI namespace with one branch mode set.

    Returns:
        Exit code: 0 done/dry run, 1 refused or failed, 3 no authorization (operator step).
    """
    from agentteams import branch_cleanup, branch_inventory

    project = Path(args.project or ".").resolve()
    cfg = branch_inventory.InventoryConfig(
        repo=project, push_remote=args.push_remote, default_branch=args.default_branch,
        stale_days=args.stale_days, operator_emails=tuple(args.operator_emails),
        use_api=not args.branch_no_api,
    )
    try:
        if args.branch_inventory:
            inventory = branch_inventory.build_inventory(cfg)
            if getattr(args, "json", False):
                print(json.dumps(inventory, indent=2))
            else:
                _print_table(inventory)
            if args.branch_report:
                for path in branch_inventory.write_reports(inventory, Path(args.branch_report)):
                    print(f"  ✓  wrote {path}")
            print(f"Branch inventory: {branch_inventory.summary_line(inventory)}")
            return branch_cleanup.EXIT_OK
        team_dir = _team_dir(project, args.team_dir)
        apply = bool(getattr(args, "sync_apply", False))
        if args.branch_cleanup:
            code, lines = branch_cleanup.run_cleanup(Path(args.branch_cleanup), cfg,
                                                     team_dir=team_dir, apply=apply)
        else:
            code, lines = branch_cleanup.run_post_merge(args.branch_post_merge, cfg,
                                                        team_dir=team_dir, team_id=args.team_id,
                                                        apply=apply)
    except (branch_inventory.InventoryError, branch_cleanup.CleanupError, TeamDirError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return branch_cleanup.EXIT_FAIL
    for line in lines:
        print(line)
    return code
