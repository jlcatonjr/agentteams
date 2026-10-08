"""mcp_grant_commands.py — the operator's commands for direct-write grants (R5).

Run outside every agent session. ``--sign-mcp-direct-grant`` builds a grant from the live brief (so it binds exactly
what the runner will check), signs it with the operator's Ed25519 key, verifies it before saving, installs the matching
public key into the operator verify-key store, and writes the operator-owned grants file. ``--list-mcp-direct-grants``
shows each grant and whether it verifies now; ``--revoke-mcp-direct-grant`` removes an agent's grant. The runner must
be restarted to pick up any change (it pins the file's hash).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentteams import ingest
from agentteams import mcp_direct_grants as G
from agentteams.atomicio import write_new_atomic
from agentteams.proposal_policy import ProposalError, load_policy


def _context(args: argparse.Namespace, flag: str) -> tuple[Path, dict[str, Any], Any]:
    if not getattr(args, "description", None):
        raise ProposalError(f"{flag} needs --description BRIEF (the grant binds what the brief says)")
    root = Path(getattr(args, "project", None) or os.getcwd()).resolve()
    brief_path = Path(args.description).resolve()
    brief = ingest.load(brief_path, scan_project=False)
    rel = brief_path.relative_to(root).as_posix() if brief_path.is_relative_to(root) else None
    return root, brief, load_policy(brief, brief_rel=rel)


def _save(root: Path, records: list[dict[str, Any]]) -> Path:
    path = G.grants_file_for(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return write_new_atomic(path.parent, path.name, (json.dumps(records, indent=2, sort_keys=True) + "\n").encode())


def run_sign_mcp_direct_grant(args: argparse.Namespace) -> int:
    """Sign and install a direct-write grant for ``--sign-mcp-direct-grant AGENT``.

    The record is built from the live brief (so it binds exactly what the runner checks), then signed through the
    operator flow in :func:`agentteams.cli.operator_signing.sign_mcp_direct_grant` (key from
    ``AGENTTEAMS_DECISION_ED25519_KEYFILE``, integrity and location checks, a confirmed review digest). It is
    verified before it is saved.

    Args:
        args: Parsed CLI arguments; reads ``sign_mcp_direct_grant`` (the agent), ``key_id``, ``grant_days``,
            ``max_writes``, ``confirm_review_sha256``, ``allow_checkout_signing``, ``description`` and ``project``.

    Returns:
        0 when the grant was saved; 1 when refused.

    Raises:
        Nothing: refusals are printed.
    """
    from agentteams.cli.operator_signing import sign_mcp_direct_grant

    try:
        root, brief, policy = _context(args, "--sign-mcp-direct-grant")
        agent = args.sign_mcp_direct_grant
        binding = G.expected_binding(agent, policy, brief)
        G._require_bounded(binding)
        days = int(getattr(args, "grant_days", None) or 7)
        if not 1 <= days <= G.MAX_DAYS:
            raise ProposalError(f"--grant-days must be 1-{G.MAX_DAYS}")
        max_writes = int(getattr(args, "max_writes", None) or 50)
        if not (G._KEY_ID_RE.match(getattr(args, "key_id", None) or "")):
            raise ProposalError("--key-id must name the operator key (letters, digits, . _ -)")
        now = datetime.now(UTC).replace(microsecond=0)
        record = {"grant_id": secrets.token_hex(8), "agent": agent, **binding, "issued": now.isoformat(),
                  "expires": (now + timedelta(days=days)).isoformat(), "max_writes": max_writes,
                  "key_id": args.key_id}
    except (ProposalError, OSError, ValueError, KeyError) as exc:
        print(f"[sign-mcp-direct-grant] refused: {exc}", file=sys.stderr)
        return 1
    signed = sign_mcp_direct_grant(record, confirm_sha256=getattr(args, "confirm_review_sha256", None),
                                   allow_checkout=bool(getattr(args, "allow_checkout_signing", False)))
    if signed is None:
        return 1
    record, public_pem = signed
    try:
        _install_public_key(args.key_id, public_pem)
        G.verify(record, policy, brief)  # verify before saving: a record that can't verify never lands
        records, _sha = G.read_grants(root)
        path = _save(root, [r for r in records if r.get("agent") != agent] + [record])
    except (ProposalError, OSError, ValueError) as exc:
        print(f"[sign-mcp-direct-grant] refused: {exc}", file=sys.stderr)
        return 1
    print(f"[sign-mcp-direct-grant] {agent}: grant {record['grant_id']} (direct writes, {max_writes} max, until "
          f"{record['expires']}) saved to {path}. Restart the runner to load it.")
    return 0


def _install_public_key(key_id: str, public: bytes) -> None:
    directory = Path(os.path.expanduser(G.VERIFY_KEYS_DIR))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / f"{key_id}.pub.pem"
    if path.exists():
        if path.read_bytes() != public:
            raise ProposalError(f"{path} already holds a different key; use another --key-id")
        return
    write_new_atomic(directory, path.name, public)


def run_list_mcp_direct_grants(args: argparse.Namespace) -> int:
    """List the operator's direct-write grants for the project and whether each verifies now.

    Args:
        args: Parsed CLI arguments; reads ``description`` and ``project``.

    Returns:
        0, or 1 when the file is unusable.

    Raises:
        Nothing.
    """
    try:
        root, brief, policy = _context(args, "--list-mcp-direct-grants")
        records, _sha = G.read_grants(root)
        active, problems, _ = G.active_grants(root, policy, brief)
    except (ProposalError, OSError) as exc:
        print(f"[list-mcp-direct-grants] {exc}", file=sys.stderr)
        return 1
    if not records:
        print("No direct-write grants.")
    for r in records:
        live = active.get(str(r.get("agent")), {}).get("grant_id") == r.get("grant_id")
        print(f"  {r.get('grant_id')}  {r.get('agent')}  until {r.get('expires')}  max {r.get('max_writes')} writes  "
              f"{'ACTIVE' if live else 'not active'}")
    for p in problems:
        print(f"  ✗ {p}", file=sys.stderr)
    return 0


def run_revoke_mcp_direct_grant(args: argparse.Namespace) -> int:
    """Remove ``--revoke-mcp-direct-grant AGENT``'s grant (a narrowing; no signature needed).

    Args:
        args: Parsed CLI arguments; reads ``revoke_mcp_direct_grant`` and ``project``.

    Returns:
        0 when removed or absent; 1 when the file is unusable.

    Raises:
        Nothing.
    """
    root = Path(getattr(args, "project", None) or os.getcwd()).resolve()
    try:
        records, _sha = G.read_grants(root)
        kept = [r for r in records if r.get("agent") != args.revoke_mcp_direct_grant]
        if len(kept) != len(records):
            _save(root, kept)
    except (ProposalError, OSError) as exc:
        print(f"[revoke-mcp-direct-grant] {exc}", file=sys.stderr)
        return 1
    print(f"[revoke-mcp-direct-grant] {args.revoke_mcp_direct_grant}: "
          f"{'revoked' if len(kept) != len(records) else 'no grant'}. Restart the runner.")
    return 0
