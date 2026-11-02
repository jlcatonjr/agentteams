"""wasm_agent_commands.py — the WASM agent runtime launcher: ``--run-wasm-agent``, ``--revoke-wasm-run``,
``--verify-wasm-run [--tree]`` (plan P08 §4.8, P09 §4.6).

``--run-wasm-agent SLUG (--wasm-task TEXT | --wasm-task-file PATH) [--project DIR] [--wasm-provider NAME]
[--wasm-base-url URL] [--json]`` loads the lock-verified ``--framework wasm`` team
(:func:`agentteams.wasm_agent.team.load_team`), re-checks the agent's grant against its recorded authorization
(:func:`authorize_grant`, C2), builds the agent's ``AgentSpec`` and hands it to P05's Stage 0
(:func:`agentteams.wasm_agent.launch.stage0`), which checks the pinned runtime before any model call, reads the
provider key from this process's environment (never from a flag or file), owns the run directory (R4) and writes the
hash-chained transcript.

**Base URL (@security S5).** The provider key goes to the base URL, so it comes only from the operator, at run time:
``--wasm-base-url`` or ``AGENTTEAMS_WASM_BASE_URL``. A rendered ``runtime.json`` URL (the brief's, allowed only for a
keyless preset on a loopback host) is kept only while that rule holds for the run's effective preset
(:func:`agentteams.wasm_agent.team.resolve_base_url`).

**Delegation (P09).** ``agent_spec`` is also handed to
Stage 0 as ``resolve_agent`` (P09), so the agent may delegate to its rendered ``delegates``, each sub-agent with a grant
never wider than its delegator's.

**The only chooser of ``os_confine="inherited"`` (METAPLAN R5).** When the team's ``host_os_confine`` is ``auto``
this module asks Stage 0 to run the guests with ``guest_os_confine="inherited"``: inside the host's own OS profile.
Stage 0 forwards it only when it really wrapped the host in a fresh profile and downgrades it to ``auto`` (with a
recorded notice) otherwise; nothing here claims enforcement from it. ``tests/test_wasm_framework_cli.py`` pins this
module as the only call site under ``agentteams/`` (AST scan). No flag exposes an ``os_confine`` mode or a run dir.

The task is operator input (Tier 2); Stage 0's DLP scans everything sent to a model. The run's output is model text:
printed under the data header, never interpreted (C-4).

Exit codes: 0 the run finished ``ok`` (revoke / verify: done / verified), 1 the run failed or was refused (or the
transcript does not verify), 2 usage (no team, unknown agent or provider, unreadable task).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from datetime import datetime
from typing import IO, Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
#: Largest ``--wasm-task-file`` accepted.
MAX_TASK_BYTES = 256 << 10
#: Grant limit fields (``wasm_exec.Limits``).
_LIMIT_KEYS = ("fuel", "max_memory_bytes", "timeout_s")
#: The guest ``os_confine`` this launcher asks for under each ``host_os_confine`` (see the module docstring).
_GUEST_CONFINE_NOTE = {
    "auto": "guests run inside the host's OS profile when Stage 0 wraps the host (reported 'inherited': "
            "not a fresh guest sandbox)",
    "off": "the OS sandbox switch is off: the host is not OS-confined; guests nest a fresh OS sandbox only where "
           "this machine allows it",
}


def _root(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "project", None) or os.getcwd()).resolve()


def runtime_resolver(project_root: Path) -> Any:
    """The provisioning resolver Stage 0 checks the pins with (a seam the live tests replace).

    Args:
        project_root: The project root.

    Returns:
        ``wasm_runtime.Resolver(project_root)``.

    Raises:
        Nothing.
    """
    from agentteams import wasm_runtime

    return wasm_runtime.Resolver(project_root)


def read_task(args: argparse.Namespace) -> str:
    """Return the task text from ``--wasm-task`` or ``--wasm-task-file`` (nofollow, regular file, 256 KiB cap, UTF-8).

    Args:
        args: The parsed arguments.

    Returns:
        The task (non-empty).

    Raises:
        ValueError: An empty task, or an unreadable / oversize / non-UTF-8 task file.
    """
    from agentteams.atomicio import read_regular_nofollow

    if getattr(args, "wasm_task_file", None) is not None:
        try:
            text = read_regular_nofollow(Path(args.wasm_task_file), MAX_TASK_BYTES).decode("utf-8")
        except (OSError, ValueError) as exc:
            raise ValueError(f"--wasm-task-file: {exc} (a regular file, no symlink, at most {MAX_TASK_BYTES} bytes, "
                             "UTF-8)") from exc
    else:
        text = str(getattr(args, "wasm_task", None) or "")
    if not text.strip():
        raise ValueError("the task is empty")
    return text


def guest_confinement(transcript_path: str | None, err: IO[str] | None = None) -> list[str]:
    """Return the distinct guest ``os_confinement`` values a run's transcript records (``guest-step`` records).

    Args:
        transcript_path: The run's ``transcript.jsonl``, or None.
        err: Where an unreadable transcript is reported (default stderr).

    Returns:
        The sorted values (empty when there is no transcript or no step, or the transcript cannot be read: then a
        notice says the confinement report is unavailable, so an empty report is never silent).

    Raises:
        Nothing.
    """
    if not transcript_path:
        return []
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  !  guest os_confinement report unavailable: the transcript cannot be read ({exc}); check it with "
              "--verify-wasm-run", file=sys.stderr if err is None else err)
        return []
    seen = {str(rec.get("os_confinement", "")) for rec in map(_record, lines)
            if isinstance(rec, dict) and rec.get("kind") in ("guest-step", "host-tool")}
    return sorted(seen - {""})


def authorize_grant(team: Any, slug: str, *, err: IO[str] | None = None, now: datetime | None = None) -> None:
    """Re-check, read-only, that *slug*'s grant beyond compute-only has a recorded authorization (@security C2).

    The lock is unkeyed: a writer who controls ``.agentteams/`` could re-hash a widened ``grants.json``. So at every
    launch, in a governed workspace (``enforce_decision_signing`` in the team's ``references/agent-privilege.json``),
    every grant token (``wasm:<agent>:read:<rel>``, ``wasm:<agent>:scratch``) must be covered by an active
    ``wasm-grant-widen:wasm`` exception whose bound Ed25519 decision row still re-verifies (the P15 carry-over test,
    :func:`agentteams.cli.sandbox_relaxation_gate.standing_authorization`), and limits above ``DEFAULT_LIMITS`` must
    equal the ones the render's gate classified (the build-log's recorded ``sandbox_state``). Nothing is consumed.
    Ungoverned: a ``SANDBOX RELAXATION`` notice, never a refusal.

    Args:
        team: The loaded :class:`~agentteams.wasm_agent.team.Team`.
        slug: The agent to run.
        err: Where the notice goes (default stderr).
        now: Reference time (default now, UTC).

    Returns:
        None.

    Raises:
        TeamError: Governed and not authorized, or the governance switch cannot be read (fail-closed).
    """
    from agentteams import wasm_exec as W
    from agentteams.cli.decision_log import _enforce_decision_signing
    from agentteams.cli.sandbox_effects import WASM_GRANT_WIDEN
    from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError, standing_authorization
    from agentteams.sandbox_state import grant_tokens, previous_sandbox_state
    from agentteams.wasm_agent.team import AGENTS_REL, TeamError

    grant, root = team.grants[slug], Path(team.project_root)
    tokens = tuple(sorted(grant_tokens(slug, grant, project_root=root)))
    raised = [k for k in _LIMIT_KEYS if getattr(grant.limits, k) > getattr(W.DEFAULT_LIMITS, k)]
    if not tokens and not raised:
        return
    team_dir = root / AGENTS_REL
    what = ", ".join([*tokens, *(f"limits.{k}={getattr(grant.limits, k)}" for k in raised)])
    try:
        governed = _enforce_decision_signing(team_dir)
    except RuntimeError as exc:
        raise TeamError(f"{slug}: cannot read this workspace's governance switch ({exc}); refused") from exc
    if not governed:
        print(f"  !  SANDBOX RELAXATION: {slug} runs with a grant wider than compute-only ({what}); this workspace "
              "is not governed (enforce_decision_signing off), so no signed authorization is checked.",
              file=sys.stderr if err is None else err)
        return
    if tokens:
        try:
            standing_authorization(team_dir, WASM_GRANT_WIDEN, "wasm", tokens, now=now)
        except SandboxRelaxationError as exc:
            raise TeamError(f"{slug}: its grant ({', '.join(tokens)}) has no recorded authorization in this governed "
                            f"workspace: {exc}") from exc
    if raised:
        recorded = previous_sandbox_state(team_dir, root, "wasm")
        rec = recorded.wasm_grants.get(slug) if recorded is not None else None
        if rec is None or rec.limits != grant.limits:
            raise TeamError(f"{slug}: limits above the defaults ({what}) do not match the ones the render's sandbox "
                            "gate recorded (references/build-log.json sandbox_state); re-render the team")


def _record(line: str) -> Any:
    """One transcript line as JSON, or None (the chain itself is checked by ``verify_transcript``)."""
    try:
        return json.loads(line)
    except ValueError:
        return None


def run_wasm_agent(args: argparse.Namespace, *, environ: Mapping[str, str] | None = None,
                   out: IO[str] | None = None) -> int:
    """``agentteams --run-wasm-agent SLUG (--wasm-task TEXT | --wasm-task-file PATH) [--project DIR]
    [--wasm-provider NAME] [--wasm-base-url URL]``.

    Args:
        args: The parsed arguments.
        environ: Where the provider key, the tier overrides and ``AGENTTEAMS_WASM_BASE_URL`` are read (default
            ``os.environ``).
        out: Where the operator summary goes (default stdout).

    Returns:
        The exit code.

    Raises:
        Nothing (refusals are exit codes with a message).
    """
    from agentteams.wasm_agent.launch import print_result, stage0
    from agentteams.wasm_agent.team import BASE_URL_ENV, TeamError, load_team, resolve_base_url

    stream = sys.stdout if out is None else out
    env = os.environ if environ is None else environ
    root = _root(args)
    try:
        task = read_task(args)
        team = load_team(root)
        spec = team.agent_spec(args.run_wasm_agent, provider=getattr(args, "wasm_provider", None), environ=env)
        operator_url = getattr(args, "wasm_base_url", None) or env.get(BASE_URL_ENV) or None
        base_url, url_notice = resolve_base_url(team.runtime, spec.provider, operator_url=operator_url)
        prices = team.prices()
    except (ValueError, TeamError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if url_notice:
        print(f"  !  {url_notice}", file=sys.stderr)
    try:
        authorize_grant(team, spec.slug)
    except TeamError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    host = team.runtime["host_os_confine"]
    entry = team.grant_entries[spec.slug]
    print(f"WASM agent {spec.slug}: provider {spec.provider}, model {spec.model}, tools "
          f"{', '.join(sorted(spec.tools)) or 'none'}; read-only via {entry['read_only_via']}; host_os_confine "
          f"{host} ({_GUEST_CONFINE_NOTE[host]})" + (f"; base URL {base_url}" if base_url else ""), file=stream)
    provider = getattr(args, "wasm_provider", None)
    result = stage0(spec, task, project_root=root, budget=team.budget(), environ=env, base_url=base_url,
                    host_confine="auto" if host == "auto" else "off",
                    guest_os_confine="inherited" if host == "auto" else "auto",
                    resolver=runtime_resolver(root), prices=prices,
                    resolve_agent=lambda slug: team.agent_spec(slug, provider=provider, environ=env),
                    delegation=team.runtime["delegation"])
    guests = guest_confinement(result.transcript_path)
    if getattr(args, "json", False):
        json.dump({"run": result.to_dict(), "guest_os_confinement": guests}, stream, indent=2, sort_keys=True)
        stream.write("\n")
    else:
        print_result(result, stream)
        if guests:
            note = " ('inherited' = inside the host's profile, not a fresh guest sandbox)" if "inherited" in guests \
                else ""
            print(f"guest os_confinement: {', '.join(guests)}{note}", file=stream)
    return EXIT_OK if result.status == "ok" else EXIT_FAILED


def revoke_wasm_run(args: argparse.Namespace) -> int:
    """``agentteams --revoke-wasm-run RUN_ID [--project DIR] [--revoke-reason TEXT]``: write the ``REVOKED`` sentinel.

    Args:
        args: The parsed arguments.

    Returns:
        The exit code (1 when already revoked, 2 for an unknown or malformed run id).

    Raises:
        Nothing.
    """
    from agentteams.wasm_agent.transcript import revoke_run

    try:
        path = revoke_run(_root(args), args.revoke_wasm_run, getattr(args, "revoke_reason", None) or "operator revoke")
    except FileExistsError:
        print(f"wasm run {args.revoke_wasm_run} is already revoked")
        return EXIT_FAILED
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"revoked wasm run {args.revoke_wasm_run}: {path}")
    return EXIT_OK


def verify_wasm_run(args: argparse.Namespace) -> int:
    """``agentteams --verify-wasm-run RUN_ID [--tree] [--project DIR]``: re-walk a run's transcript (read-only).

    With ``--tree`` (P09), walk the whole delegation tree from that root run
    (:func:`agentteams.wasm_agent.delegation.verify_tree`).

    Args:
        args: The parsed arguments.

    Returns:
        0 when the chain (or tree) verifies, 1 when it does not, 2 for a malformed run id.

    Raises:
        Nothing.
    """
    from agentteams.wasm_agent.transcript import TRANSCRIPT_NAME, run_dir_path, verify_transcript

    try:
        path = run_dir_path(_root(args), args.verify_wasm_run) / TRANSCRIPT_NAME
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if getattr(args, "wasm_tree", False):
        from agentteams.wasm_agent.delegation import verify_tree

        tree = verify_tree(_root(args), args.verify_wasm_run)
        print(f"wasm run tree {args.verify_wasm_run}: {'verified' if tree.ok else 'NOT VERIFIED'}; "
              f"{len(tree.runs)} run(s)")
        for err in tree.errors:
            print(f"  error: {err}")
        for orphan in tree.orphans:
            print(f"  orphan: {orphan} names a run of this tree as its parent but no parent references it")
        return EXIT_OK if tree.ok else EXIT_FAILED
    rep = verify_transcript(path)
    state = "verified" if rep.ok else "NOT VERIFIED"
    print(f"wasm run {args.verify_wasm_run}: {state}; {rep.records} record(s); "
          f"{'complete' if rep.complete else 'incomplete'}; status {rep.status or '-'}; head {rep.head}"
          + (f"; error: {rep.error}" if rep.error else ""))
    return EXIT_OK if rep.ok else EXIT_FAILED


def dispatch(args: argparse.Namespace) -> int:
    """Route to the requested launcher action (``cli/app.py`` calls this; one action per invocation).

    Args:
        args: The parsed arguments.

    Returns:
        The exit code.

    Raises:
        Nothing.
    """
    if getattr(args, "run_wasm_agent", None) is not None:
        return run_wasm_agent(args)
    if getattr(args, "revoke_wasm_run", None) is not None:
        return revoke_wasm_run(args)
    return verify_wasm_run(args)


__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_USAGE", "MAX_TASK_BYTES", "authorize_grant", "dispatch", "guest_confinement",
           "read_task", "revoke_wasm_run", "run_wasm_agent", "runtime_resolver", "verify_wasm_run"]
