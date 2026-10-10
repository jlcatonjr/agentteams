"""CLI for the orchestrator-only-writes pilot.

``--issue-dispatch --agent SLUG`` records a signed dispatch nonce for the agent being dispatched; the
orchestrator puts it in the agent's task. ``--apply-proposal FILE`` / ``--run-request FILE`` act on an artifact
that must carry that nonce; the agent is read from the signed dispatch record, never from the caller.
``--verify-proposal-ledger`` checks the signed, chained ledger. The policy comes from ``--description`` and the
project root from ``--project`` (default: the current directory). See :mod:`agentteams.proposals`.

**Queue mode (P4a).** Under ``write_policy: "orchestrator-only"``, or once a runner has served the project,
these commands don't act themselves: they queue the request for the out-of-session runner
(``--serve-requests``), which alone holds the ledger key, and print its result. See
:mod:`agentteams.proposal_runner`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

from agentteams import confinement as _confinement
from agentteams import ingest
from agentteams import proposal_runner as R
from agentteams.proposal_policy import GATE_ARGV_KEY, GATE_EXEC_KEY, gate_argv_digest
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
    try:
        confined_file = _confinement.read_confined_file(root)
    except _confinement.ConfinementError as exc:
        raise ProposalError(str(exc)) from exc
    return load_policy(ingest.load(brief_path, scan_project=False), brief_rel=brief_rel, confined_file=confined_file)


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _queue_mode(args: argparse.Namespace) -> bool:
    """Queue for the runner under the switch, or once any runner has served this project (fail closed)."""
    if (_root(args) / R.HEARTBEAT_REL).exists() or (_root(args) / R.LOCK_REL).exists():
        return True  # the lock is never deleted: once a runner has served this project, stay in queue mode
    if getattr(args, "description", None):
        try:
            brief = ingest.load(Path(args.description).resolve(), scan_project=False)
        except (OSError, ValueError):
            return True  # can't tell whether the switch is on: fail closed (queue, never sign directly)
        return brief.get("write_policy") == "orchestrator-only"
    return False


def _queued(args: argparse.Namespace, label: str, request: dict) -> tuple[int, dict | None]:
    """Queue *request*, wait for the runner, and map a refusal to exit 1 (or 3 for undeclared writes)."""
    try:
        result = R.wait_result(_root(args), R.enqueue(_root(args), request),
                               timeout=float(getattr(args, "wait_timeout", None) or 120))
    except ProposalError as exc:
        print(f"[{label}] refused: {exc}", file=sys.stderr)
        return 1, None
    if not result.get("ok"):
        print(f"[{label}] refused: {result.get('error', 'refused by the runner')}", file=sys.stderr)
        return int(result.get("exit") or 1), None
    return 0, result.get("result")


def run_issue_dispatch(args: argparse.Namespace) -> int:
    """Print a fresh dispatch nonce for ``--agent``.

    Args:
        args: Parsed CLI arguments; reads ``agent`` and ``project``.

    Returns:
        0 when a nonce was printed, 1 when refused (no ledger key, bad agent slug).

    Raises:
        OSError: If the dispatch records cannot be written.
    """
    if _queue_mode(args):
        code, result = _queued(args, "issue-dispatch",
                               {"kind": "issue-dispatch", "agent": getattr(args, "agent", None) or ""})
        if result is not None:
            print(result["nonce"])
        return code
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
    if _queue_mode(args):
        try:
            artifact = _read_json(args.apply_proposal)
        except (OSError, ValueError) as exc:
            print(f"[apply-proposal] refused: {exc}", file=sys.stderr)
            return 1
        code, result = _queued(args, "apply-proposal", {"kind": "apply-proposal", "artifact": artifact,
                                                         "dry_run": bool(getattr(args, "dry_run", False))})
        if result is not None:
            print(json.dumps(result, indent=2))
        return code
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
    if _queue_mode(args):
        try:
            artifact = _read_json(args.run_request)
        except (OSError, ValueError) as exc:
            print(f"[run-request] refused: {exc}", file=sys.stderr)
            return 1
        code, result = _queued(args, "run-request", {"kind": "run-request", "artifact": artifact,
                                                      "dry_run": bool(getattr(args, "dry_run", False))})
        if result is not None:
            print(json.dumps({k: v for k, v in result.items() if k not in ("stdout", "stderr")}, indent=2))
            sys.stdout.write(result.get("stdout", ""))
            sys.stderr.write(result.get("stderr", ""))
            return int(result.get("exit") or 0)
        return code
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
    if _queue_mode(args):
        code, result = _queued(args, "verify-proposal-ledger", {"kind": "verify-ledger"})
        if result is None:
            return code
        problems = result["problems"]
    else:
        try:
            problems = verify_ledger(_root(args))
        except ProposalError as exc:
            print(f"[verify-proposal-ledger] {exc}", file=sys.stderr)
            return 1
    for problem in problems:
        print(f"  ✗ {problem}", file=sys.stderr)
    print("Proposal ledger: OK" if not problems else f"Proposal ledger: {len(problems)} problem(s)")
    return 1 if problems else 0


def run_confined_path(args: argparse.Namespace) -> int:
    """Print the operator-owned ``confined_programs`` file for the project (P5b).

    Args:
        args: Parsed CLI arguments; reads ``project``.

    Returns:
        0.

    Raises:
        Nothing.
    """
    path = _confinement.confined_file_for(_root(args))
    print(f"{path} ({'present' if os.path.lexists(path) else 'absent'})")
    return 0


def run_install_confined(args: argparse.Namespace) -> int:
    """Validate a ``confined_programs`` object against the brief's policy and install it as the operator file.

    Args:
        args: Parsed CLI arguments; reads ``install_confined``, ``description``, ``project`` and
            ``confirm_review_sha256``.

    Returns:
        0 when installed. 1 when the file is malformed, the policy refuses it, or the brief also defines
        ``confined_programs``. Also 1 when ``--confirm-review-sha256`` is missing (the JSON and its hash are
        printed for review) or doesn't match the bytes that would be installed.

    Raises:
        Nothing: errors map to exit 1.
    """
    try:
        if not getattr(args, "description", None):
            raise ProposalError("--install-confined requires --description BRIEF (its gates validate the file)")
        data = _read_json(args.install_confined)
        if not isinstance(data, dict):
            raise ProposalError("the file must hold a JSON object of {agent: {exec, write}}")
        brief_path, root = Path(args.description).resolve(), _root(args)
        brief_rel = brief_path.relative_to(root).as_posix() if brief_path.is_relative_to(root) else None
        brief = ingest.load(brief_path, scan_project=False)
        gates = brief.get("proposal_gates") or {}
        if GATE_EXEC_KEY in data and isinstance(gates, dict):  # a malformed proposal_gates: load_policy refuses it
            # Bind each gate_exec entry to its gate's current argv; the operator reviews these digests with the rest.
            # Malformed or unknown gates get no digest here; load_policy below refuses them with a clear message.
            data = {**data, GATE_ARGV_KEY: {
                name: gate_argv_digest(gates[name]["argv"]) for name in (data[GATE_EXEC_KEY] or {})
                if isinstance(gates.get(name), dict) and isinstance(gates[name].get("argv"), list)}}
        # Static checks only: the runner repeats check_roots against the live tree before every command, and a
        # write root such as lean/.lake may not exist yet.
        load_policy(brief, brief_rel=brief_rel, confined_file=(data, "candidate"))
        payload = _confinement.confined_bytes(data)
        digest = hashlib.sha256(payload).hexdigest()
        confirmed = (getattr(args, "confirm_review_sha256", None) or "").strip().lower()
        if not confirmed:
            # The review step: the operator sees exactly what will be installed, and installs only those bytes.
            sys.stdout.write(payload.decode("utf-8"))
            print(f"[install-confined] review the JSON above (it will be installed at "
                  f"{_confinement.confined_file_for(root)}), then rerun with --confirm-review-sha256 {digest}")
            return 1
        if confirmed != digest:
            raise ProposalError(f"--confirm-review-sha256 does not match the file ({digest}); it changed after "
                                "review, or the hash is wrong. Review it again")
        path = _confinement.install_confined_file(root, data)
    except (ProposalError, _confinement.ConfinementError, OSError, ValueError, TypeError) as exc:
        print(f"[install-confined] refused: {exc}", file=sys.stderr)
        return 1
    print(f"[install-confined] installed {path} (sha256 {digest}); restart --serve-requests to load it")
    return 0


def _confined_reinstall_hint(args: argparse.Namespace, exc: Exception) -> list[str]:
    """The exact review-and-reinstall commands when the runner refuses the operator confined file.

    An upgrade can tighten what that file must record (``gate_argv_sha256`` since P5c), so a runner that worked
    before refuses to start. The operator's fix is the two-step ``--install-confined`` review on the installed
    file itself; this spells it out with the real paths.

    Args:
        args: Parsed CLI arguments (``project``, ``description``).
        exc: The start-up error.

    Returns:
        Lines to print; empty unless the error asks for ``--install-confined`` and the file exists.

    Raises:
        Nothing.
    """
    if "--install-confined" not in str(exc) or not getattr(args, "description", None):
        return []
    try:
        root = _root(args)
        installed = _confinement.confined_file_for(root)
    except (OSError, ValueError, _confinement.ConfinementError):
        return []
    if not installed.is_file():
        return []
    brief = Path(args.description).resolve()
    return ["[serve-requests] to review and reinstall it, run outside every agent session:",
            f"  agentteams --install-confined {shlex.quote(str(installed))} --project {shlex.quote(str(root))} "
            f"--description {shlex.quote(str(brief))}",
            "  then rerun that command with --confirm-review-sha256 <the sha256 it prints>, and restart "
            "--serve-requests."]


def run_serve_requests(args: argparse.Namespace) -> int:
    """Run the out-of-session runner: hold the ledger key and serve the project's queue.

    Start it outside every agent session. It pins ``--project`` and ``--description`` at start, reads the
    key from its key file (``AGENTTEAMS_PROPOSAL_LEDGER_KEY_FILE`` or ``~/.config/agentteams/keys/
    proposal-ledger.key``), and serves until interrupted, or once with ``--once``.

    Args:
        args: Parsed CLI arguments; reads ``project``, ``description`` and ``once``.

    Returns:
        0 on a clean stop; 1 when it can't start (no key file, another runner, bad brief) or the brief changed.

    Raises:
        Nothing: start-up and serving errors map to exit 1.
    """
    try:
        if os.name != "posix":
            raise ProposalError("the runner needs POSIX file locking; not supported on this platform")
        runner = R.Runner(_root(args), Path(args.description).resolve(), _policy(args, "--serve-requests"))
    except (ProposalError, OSError, ValueError) as exc:
        print(f"[serve-requests] cannot start: {exc}", file=sys.stderr)
        for line in _confined_reinstall_hint(args, exc):
            print(line, file=sys.stderr)
        return 1
    print(f"[serve-requests] serving {runner.root} (brief {runner.brief_path.name}); Ctrl-C to stop")
    try:
        if getattr(args, "once", False):
            print(f"[serve-requests] served {runner.serve_once()} request(s)")
        else:
            runner.serve_forever()
    except KeyboardInterrupt:
        pass
    except ProposalError as exc:
        print(f"[serve-requests] stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.close()
    return 0


def run_wait_result(args: argparse.Namespace) -> int:
    """Wait again for a queued request's result (a long run can outlast the first wait).

    Args:
        args: Parsed CLI arguments; reads ``wait_result``, ``project`` and ``wait_timeout``.

    Returns:
        0 when the result is a success, else its exit code (1 refused, 3 undeclared writes).

    Raises:
        Nothing: errors map to exit 1.
    """
    try:
        result = R.wait_result(_root(args), args.wait_result,
                               timeout=float(getattr(args, "wait_timeout", None) or 120))
    except ProposalError as exc:
        print(f"[wait-result] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else int(result.get("exit") or 1)


def run_staged(args: argparse.Namespace) -> int:
    """``--list-staged`` / ``--show-staged SID`` / ``--apply-staged SID`` / ``--reject-staged SID`` (R3).

    The orchestrator's side of MCP-mediated agent writes: agents granted the ``agentteams_runner`` server stage
    their writes; the orchestrator lists them, reads one in full on cause, and approves or rejects by id. Staged
    records live in the runner's control plane, so every operation goes through the runner.

    Args:
        args: Parsed CLI arguments; reads ``list_staged``, ``show_staged``, ``apply_staged``, ``reject_staged``,
            ``reject_reason``, ``project`` and ``wait_timeout``.

    Returns:
        0 on success; 1 when refused (no runner, unknown or expired id, a re-run check refusing).

    Raises:
        Nothing: refusals are printed.
    """
    if getattr(args, "list_staged", False):
        code, result = _queued(args, "list-staged", {"kind": "list-staged"})
        if result is None:
            return code
        staged = result.get("staged") or []
        if not staged:
            print("No staged proposals.")
        for entry in staged:
            if entry.get("malformed"):
                print(f"  {entry['sid']}  (malformed record)")
                continue
            gates = ",".join(str(g.get("name", g)) if isinstance(g, dict) else str(g) for g in entry.get("gates") or [])
            print(f"  {entry['sid']}  {entry.get('agent')}  {entry.get('kind')}  {entry.get('path')}  "
                  f"{entry.get('bytes')} B  gates[{gates or 'none'}]  expires {entry.get('expires')}")
        return 0
    if getattr(args, "show_staged", None):
        code, result = _queued(args, "show-staged", {"kind": "show-staged", "sid": args.show_staged})
        if result is not None:
            print(json.dumps(result, indent=2))
        return code
    if getattr(args, "apply_staged", None):
        code, result = _queued(args, "apply-staged", {"kind": "apply-staged", "sid": args.apply_staged})
        if result is not None:
            print(f"[apply-staged] applied {result.get('path')} for {result.get('agent')} "
                  f"(sha256 {result.get('new_sha256') or 'deleted'})")
        return code
    code, result = _queued(args, "reject-staged", {"kind": "reject-staged", "sid": args.reject_staged,
                                                    "reason": getattr(args, "reject_reason", None) or ""})
    if result is not None:
        print(f"[reject-staged] rejected {result.get('path')} from {result.get('agent')}")
    return code
