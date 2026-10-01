"""CLI runners for cross-workspace capability grants (CH-07 carve of ``commands.py``).

``--verify-grants``, ``--issue-grant`` (HMAC, non-widening grants only) and ``--sign-grant``
(operator Ed25519, the only minter of a grant that widens ``allowWrite``). ``commands.py``
re-imports the runners so existing import sites keep working. The grant spec helpers and the
whole ``--sign-grant`` body live in the integrity-pinned ``operator_signing.py``; this module keeps
only directory resolution and the runners that delegate to it.

**Which directories (mirrors the generate path).** The ledger lives in the holder workspace root
the update reads it from: ``--output`` → ``--project`` → CWD (the generate path's
``project_root``). The TEAM dir (approver roster and ``authorized-verify-keys`` store) is the
directory that ``--update`` with the same ``--framework``/``--output`` writes the team to: the
adapter's ``normalize_output_path(--output)``, else ``get_agents_dir(root)``. In a
multi-framework project, pass the ``--framework``/``--output`` of the team whose sandbox the
grant should widen (today only a Claude sandbox consumes grants).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agentteams.cli import operator_signing
from agentteams.cli.commands_output import _resolve_output_dir


def _grant_dirs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return ``(ledger_root, team_dir)`` for a grant command, mirroring the generate path.

    Args:
        args: Parsed CLI namespace (``output``, ``project``, ``framework``).

    Returns:
        The holder workspace root (ledger) and the holder TEAM dir (roster + verify keys).

    Raises:
        ValueError: ``--framework`` names a non-rendering target (canonical/generic).
    """
    from agentteams.frameworks.registry import FRAMEWORKS

    ledger_root = _resolve_output_dir(args)
    framework = getattr(args, "framework", None) or "copilot-vscode"
    if framework not in FRAMEWORKS:
        raise ValueError(f"--framework {framework!r} has no team directory for grants")
    adapter = FRAMEWORKS[framework]()
    if getattr(args, "output", None):
        return ledger_root, adapter.normalize_output_path(ledger_root)
    return ledger_root, adapter.get_agents_dir(ledger_root)


def _run_verify_grants(args: argparse.Namespace) -> int:
    """``--verify-grants``: read-only report of every cross-workspace grant's validity.

    Validates every row of the holder ledger (scheme sufficiency, signature, expiry, use-limit,
    TEAM-dir approver roster) without consuming any, and prints one line per problem. An
    HMAC-signed ``write`` grant is reported with its ``--sign-grant`` migration message.

    Args:
        args: Parsed CLI namespace.

    Returns:
        0 when all grants are valid (or none exist), 1 otherwise.
    """
    from agentteams.cli import grants

    try:
        ledger_root, team_dir = _grant_dirs(args)
        problems = grants.verify_grants(ledger_root, team_dir=team_dir)
    except (grants.GrantError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    log_path = ledger_root / grants.GRANT_LOG_REL
    if not log_path.exists():
        print(f"No capability grants found at {log_path}")
        return 0
    if not problems:
        print(f"All capability grants valid at {log_path} (team dir {team_dir})")
        return 0
    for problem in problems:
        print(f"  [BAD] {problem}", file=sys.stderr)
    print(f"\n{len(problems)} invalid capability grant(s).", file=sys.stderr)
    return 1


def _run_issue_grant(args: argparse.Namespace) -> int:
    """``--issue-grant SPEC.json``: mint an HMAC-signed, NON-widening capability grant.

    Signs with ``AGENTTEAMS_GRANT_SIGNING_KEY`` and appends to the HOLDER ledger. A spec whose
    ``permitted_ops`` include ``write`` is refused with the ``--sign-grant`` migration message
    (an HMAC grant can no longer widen ``allowWrite``). Fails closed if the key is unset, the
    approver is off the TEAM-dir roster, or the spec is malformed.

    Args:
        args: Parsed CLI namespace (``issue_grant`` holds the spec path).

    Returns:
        Process exit code (0 on success).
    """
    from agentteams.cli import grants

    spec = operator_signing.load_grant_spec(args.issue_grant, "--issue-grant")
    if spec is None:
        return 1
    try:
        ledger_root, team_dir = _grant_dirs(args)
        record = grants.issue_grant(
            ledger_root, team_dir=team_dir, **operator_signing.grant_spec_kwargs(spec))
    except (grants.GrantError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    operator_signing.report_issued(record, ledger_root, team_dir)
    return 0


def _run_sign_grant(args: argparse.Namespace) -> int:
    """``--sign-grant SPEC.json``: operator-only Ed25519 minter for a capability grant.

    A one-line delegator: the key read, the signed field values, the signature, verify-before-
    append and the append live in the integrity-pinned
    :func:`agentteams.cli.operator_signing.sign_grant` (pin-signing-cli-modules). This runner only
    chooses the spec path and supplies the directory lookup as a callable, so the lookup's
    ``ValueError`` still fires after the key read (unchanged error precedence).

    Args:
        args: Parsed CLI namespace (``sign_grant`` holds the spec path).

    Returns:
        0 on success, 1 on any error (fail-closed).
    """
    return operator_signing.sign_grant(
        args.sign_grant, lambda: _grant_dirs(args),
        confirm_sha256=getattr(args, "confirm_review_sha256", None),
        allow_checkout=bool(getattr(args, "allow_checkout_signing", False)))


__all__ = ["_grant_dirs", "_run_issue_grant", "_run_sign_grant", "_run_verify_grants"]
