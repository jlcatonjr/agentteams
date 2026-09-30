"""CLI runners for cross-workspace capability grants (CH-07 carve of ``commands.py``).

``--verify-grants``, ``--issue-grant`` (HMAC, non-widening grants only) and ``--sign-grant``
(operator Ed25519, the only minter of a grant that widens ``allowWrite``). ``commands.py``
re-imports the runners so existing import sites keep working.

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

from agentteams.cli.commands_output import _resolve_output_dir

#: Spec fields every grant minter requires.
_GRANT_SPEC_REQUIRED: tuple[str, ...] = (
    "issuer_team", "holder_team", "target_path", "permitted_ops",
    "expires_at", "max_uses", "approver", "ticket_id", "reason_code",
)


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


def _load_grant_spec(path: str, flag: str) -> dict | None:
    """Read and shape-check a grant JSON spec; print the error and return None on failure."""
    import json

    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Error: unable to read {flag} spec {path!r}: {exc}", file=sys.stderr)
        return None
    if not isinstance(spec, dict):
        print(f"Error: {flag} spec must be a JSON object", file=sys.stderr)
        return None
    missing = [k for k in _GRANT_SPEC_REQUIRED if k not in spec]
    if missing:
        print(f"Error: grant spec missing required field(s): {', '.join(missing)}", file=sys.stderr)
        return None
    return spec


def _spec_kwargs(spec: dict) -> dict:
    """Map a grant spec to the minter keyword arguments (fresh id + timestamp)."""
    import secrets
    from datetime import datetime, timezone

    return dict(
        issuer_team=str(spec["issuer_team"]), holder_team=str(spec["holder_team"]),
        target_path=str(spec["target_path"]), permitted_ops=str(spec["permitted_ops"]),
        expires_at=str(spec["expires_at"]), max_uses=int(spec["max_uses"]),
        approver=str(spec["approver"]), ticket_id=str(spec["ticket_id"]),
        reason_code=str(spec["reason_code"]),
        grant_id=f"grant-{secrets.token_hex(8)}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        # G-6 (optional): when the spec declares the issuer's tree, it is signed into the
        # grant and an absolute target_path outside it is rejected (P2-4 containment).
        issuer_root=str(spec.get("issuer_root", "")),
    )


def _report_issued(record: dict[str, str], ledger_root: Path, team_dir: Path) -> None:
    """Print the one-line summary of a freshly minted grant."""
    from agentteams.cli import grants

    print(
        f"Issued capability grant {record['grant_id']} ({record.get('sig_scheme') or 'hmac'}): "
        f"{record['issuer_team']} → {record['holder_team']} may {record['permitted_ops']} "
        f"{record['target_path']} (expires {record['expires_at']}, max_uses {record['max_uses']})"
    )
    print(f"  appended to {ledger_root / grants.GRANT_LOG_REL}")
    print(f"  verified against team dir {team_dir}")


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

    spec = _load_grant_spec(args.issue_grant, "--issue-grant")
    if spec is None:
        return 1
    try:
        ledger_root, team_dir = _grant_dirs(args)
        record = grants.issue_grant(ledger_root, team_dir=team_dir, **_spec_kwargs(spec))
    except (grants.GrantError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _report_issued(record, ledger_root, team_dir)
    return 0


def _run_sign_grant(args: argparse.Namespace) -> int:
    """``--sign-grant SPEC.json``: operator-only Ed25519 minter for a capability grant.

    Mirrors ``--sign-decision``: reads the operator private key from the file named by
    ``AGENTTEAMS_DECISION_ED25519_KEYFILE`` (warning when it sits outside the read-denied
    ``~/.config/agentteams/keys``, is a symlink, or is wider than 600), signs the
    purpose-tagged payload, verifies the row against the holder TEAM dir's verify-key store and
    roster BEFORE appending, and appends it to the holder ledger. The spec carries the grant
    fields plus ``key_id``. It is the only minter of a grant that widens ``allowWrite``.

    Args:
        args: Parsed CLI namespace (``sign_grant`` holds the spec path).

    Returns:
        0 on success, 1 on any error (fail-closed).
    """
    import os

    from agentteams.cli import grants
    from agentteams.frameworks._sandbox_emit import signing_keyfile_warnings

    spec = _load_grant_spec(args.sign_grant, "--sign-grant")
    if spec is None:
        return 1
    key_id = str(spec.get("key_id") or "").strip()
    if not key_id:
        print("Error: --sign-grant spec must name a key_id (the verify key's file stem)",
              file=sys.stderr)
        return 1
    keyfile = os.getenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", "")
    if not keyfile:
        print(
            "Error: AGENTTEAMS_DECISION_ED25519_KEYFILE is not set — it must name the operator "
            "private key file (never an agent env). Refusing to sign (fail-closed).",
            file=sys.stderr,
        )
        return 1
    for warning in signing_keyfile_warnings(keyfile):
        print(f"Warning: {warning}", file=sys.stderr)
    try:
        private_pem = Path(keyfile).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot read operator private key file: {exc}", file=sys.stderr)
        return 1
    try:
        ledger_root, team_dir = _grant_dirs(args)
        record = grants.sign_ed25519_grant(
            ledger_root, team_dir=team_dir, private_pem=private_pem, key_id=key_id,
            **_spec_kwargs(spec),
        )
    except (grants.GrantError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _report_issued(record, ledger_root, team_dir)
    return 0


__all__ = ["_grant_dirs", "_run_issue_grant", "_run_sign_grant", "_run_verify_grants"]
