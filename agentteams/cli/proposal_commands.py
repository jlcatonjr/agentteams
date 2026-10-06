"""CLI for the orchestrator-only-writes pilot.

``--issue-dispatch --agent SLUG`` records a signed dispatch nonce for the agent being dispatched; the
orchestrator puts it in the agent's task. ``--apply-proposal FILE`` / ``--run-request FILE`` act on an artifact
that must carry that nonce; the agent is read from the signed dispatch record, never from the caller.
``--verify-proposal-ledger`` checks the signed, chained ledger. The policy comes from ``--description`` and the
project root from ``--project`` (default: the current directory). See :mod:`agentteams.proposals`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agentteams import ingest
from agentteams.proposals import (
    Policy,
    ProposalError,
    UndeclaredWritesError,
    apply_proposal,
    issue_dispatch,
    load_policy,
    run_request,
    verify_ledger,
)


def _root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "project", None) or os.getcwd()).resolve()


def _policy(args: argparse.Namespace, flag: str) -> Policy:
    if not getattr(args, "description", None):
        raise ProposalError(f"{flag} requires --description BRIEF (the team's registered policy)")
    brief_path, root = Path(args.description).resolve(), _root(args)
    brief_rel = brief_path.relative_to(root).as_posix() if brief_path.is_relative_to(root) else None
    return load_policy(ingest.load(brief_path, scan_project=False), brief_rel=brief_rel)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def run_issue_dispatch(args: argparse.Namespace) -> int:
    """Print a fresh dispatch nonce for ``--agent``.

    Args:
        args: Parsed CLI arguments; reads ``agent`` and ``project``.

    Returns:
        0 when a nonce was printed, 1 when refused (no ledger key, bad agent slug).

    Raises:
        OSError: If the dispatch records cannot be written.
    """
    try:
        nonce = issue_dispatch(_root(args), getattr(args, "agent", None) or "")
    except ProposalError as exc:
        print(f"[issue-dispatch] refused: {exc}", file=sys.stderr)
        return 1
    print(nonce)
    return 0


def run_apply_proposal(args: argparse.Namespace) -> int:
    """Apply one change or deletion proposal.

    Args:
        args: Parsed CLI arguments; reads ``apply_proposal``, ``description``, ``project`` and ``dry_run``.

    Returns:
        0 when applied (or when it would be, with ``--dry-run``), 1 when refused.

    Raises:
        Nothing: refusals (``ProposalError``, including ``LedgerTamperedError``) map to exit 1.
    """
    try:
        result = apply_proposal(_read_json(args.apply_proposal), root=_root(args),
                                policy=_policy(args, "--apply-proposal"), dry_run=getattr(args, "dry_run", False))
    except (ProposalError, OSError, ValueError) as exc:
        print(f"[apply-proposal] refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def run_command_request(args: argparse.Namespace) -> int:
    """Run one command request.

    Args:
        args: Parsed CLI arguments; reads ``run_request``, ``description``, ``project`` and ``dry_run``.

    Returns:
        The command's exit code; 1 when refused; 3 when it wrote outside its declared writes or timed out.

    Raises:
        Nothing: refusals (``ProposalError``, including ``LedgerTamperedError``) map to exit 1.
    """
    try:
        result = run_request(_read_json(args.run_request), root=_root(args),
                             policy=_policy(args, "--run-request"), dry_run=getattr(args, "dry_run", False))
    except UndeclaredWritesError as exc:
        sys.stdout.write(exc.result["stdout"])
        sys.stderr.write(exc.result["stderr"])
        print(f"[run-request] FAILED: {exc}", file=sys.stderr)
        return 3
    except (ProposalError, OSError, ValueError) as exc:
        print(f"[run-request] refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({k: v for k, v in result.items() if k not in ("stdout", "stderr")}, indent=2))
    sys.stdout.write(result["stdout"])
    sys.stderr.write(result["stderr"])
    return int(result["exit"] or 0)


def run_verify_proposal_ledger(args: argparse.Namespace) -> int:
    """Verify the proposal ledger's signatures, chain and head anchor.

    Args:
        args: Parsed CLI arguments; reads ``project``.

    Returns:
        0 when the ledger is intact, 1 when a problem was found or no ledger key is set.

    Raises:
        OSError: If the ledger exists but cannot be read.
    """
    try:
        problems = verify_ledger(_root(args))
    except ProposalError as exc:
        print(f"[verify-proposal-ledger] {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"  ✗ {problem}", file=sys.stderr)
    print("Proposal ledger: OK" if not problems else f"Proposal ledger: {len(problems)} problem(s)")
    return 1 if problems else 0
