"""proposal_run.py — the command-request half of :mod:`agentteams.proposals` (carved out at R2, CH-07).

:func:`agentteams.proposals.run_request` is the public entry point and delegates here. Every shared helper and
constant is reached through ``proposals`` at call time, so patching ``proposals.COMMAND_TIMEOUT`` and the like
still takes effect.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from agentteams import proposals as P
from agentteams.proposal_policy import Policy


def run_request(artifact: dict[str, Any], *, root: Path, policy: Policy, dry_run: bool = False,
                confine: bool = False, timeout_cap: int | None = None, expect_agent: str | None = None,
                refuse_agents: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Implementation of :func:`agentteams.proposals.run_request` (see there for the contract)."""
    agent = "?"
    try:
        P._check_shape(artifact, "command-request")
        agent = P._identity(root, artifact, dry_run=dry_run, expect_agent=expect_agent, refuse_agents=refuse_agents)
        argv, purpose = artifact.get("argv"), artifact.get("purpose")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            raise P.ProposalError("argv must be a non-empty list of strings (never a shell string)")
        if not isinstance(purpose, str) or not purpose.strip():
            raise P.ProposalError("a non-empty purpose is required")
        entry = P._matching_entry(argv, agent, policy)
        if entry is None:
            raise P.ProposalError(f"{argv!r} is not on {agent}'s command allowlist; refused")
        cwd_rel = artifact.get("cwd") or "."
        if not isinstance(cwd_rel, str) or cwd_rel != (entry.get("cwd") or "."):
            raise P.ProposalError(f"cwd must be the entry's pinned cwd {entry.get('cwd') or '.'!r}")
        cwd = root if cwd_rel == "." else root / P._rel_inside(root, cwd_rel)
        expected = artifact.get("expected_writes") or []
        if not (isinstance(expected, list) and all(isinstance(p, str) for p in expected)):
            raise P.ProposalError("expected_writes must be a list of paths")
        expected_rel = set()
        for path in expected:
            rel = P._rel_inside(root, path)
            allowed = entry.get("writes") or []
            if not any(fnmatch.fnmatch(rel.lower(), str(g).lower()) for g in allowed):
                P._check_destination(root, rel, policy, agent)  # else it must be an ordinary scoped write
            elif P.control_plane_of(rel, platform="darwin") or P.nested_control_plane(rel):
                raise P.ProposalError(f"{rel} is inside the control plane; no command may declare it")
            elif policy.brief_rel and rel.lower() == policy.brief_rel.lower():
                raise P.ProposalError(f"{rel} is the brief that defines this policy; refused (C-3)")
            expected_rel.add(rel)
        env = P._scrubbed_env()
        program = P._resolve_program(root, argv[0], env)
        jail = None
        if confine:
            jail = policy.confined.get(agent)
            if jail is None:
                raise P.ProposalError(f"{agent} has no confined_programs entry; the runner runs nothing unconfined")
            if not P._confinement.exec_allows(program, jail["exec"]):
                raise P.ProposalError(f"{program} is outside {agent}'s confined exec paths; refused")
            for rel in jail["write"]:
                prefix = rel.strip("/").lower() + "/"
                hit = P._protected(prefix + "x", policy.protected_paths) or any(
                    str(p).replace("\\", "/").lower().startswith(prefix) for p in policy.protected_paths) or (
                    policy.brief_rel and P._in_scope(policy.brief_rel, [prefix]))
                if hit:
                    raise P.ProposalError(f"confined_programs.{agent}.write {rel!r} covers a protected path or the "
                                        "brief; changes there would count as declared")
            try:  # before any snapshot or ledger row: a swapped root must refuse, not trip the write check
                P._confinement.check_roots(root, jail["exec"], jail["write"])
            except P._confinement.ConfinementError as exc:
                raise P.ProposalError(f"confined_programs.{agent}: {exc}") from exc
        stdin_bytes = None
        spec = artifact.get("stdin_from_content")
        if spec is not None:
            if not (isinstance(spec, dict) and set(spec) == {"path", "content"} and isinstance(spec["path"], str)
                    and isinstance(spec["content"], str)):
                raise P.ProposalError("stdin_from_content must be exactly {path, content}")
            if "stdin_gates" not in entry:
                raise P.ProposalError("this command entry accepts no stdin content (no stdin_gates registered)")
            P._run_gates(root, P._rel_inside(root, spec["path"]), spec["content"], list(entry["stdin_gates"]), policy,
                       confine=confine)
            stdin_bytes = spec["content"].encode("utf-8")
        if dry_run:
            return {"agent": agent, "argv": argv, "exit": None, "stdout": "", "stderr": "", "truncated": False,
                    "undeclared_writes": [], "ran": False}
        before = P._snapshot(root, env)
        if before is None:
            raise P.ProposalError("the project is not a git worktree, or git's own paths could not be resolved; "
                                "refusing to run unchecked (fail-closed)")
        sandbox = P._sandbox_for(policy) if jail is not None else None
        confined_as = sandbox or ("unconfined-opt-out" if jail is not None else None)
        P._consume(root, artifact, agent)
        P.record(root, {"action": "run-request-start", "agent": agent, "argv": argv, "purpose": purpose.strip()[:300],
                      **({"confined": confined_as} if confined_as else {})})
        before = P._snapshot(root, env)  # re-taken after the write-ahead row, so only the command's changes count
        timed_out, group_killed, survivors = False, False, False
        # Per-entry timeout (P5a): a long check (e.g. a kernel audit) may set one, capped at P.MAX_COMMAND_TIMEOUT.
        timeout = max(1, min(int(entry.get("timeout") or P.COMMAND_TIMEOUT), P.MAX_COMMAND_TIMEOUT))
        if timeout_cap is not None:
            timeout = max(1, min(timeout, int(timeout_cap)))
        started = time.monotonic()
        run_tmp = tempfile.mkdtemp(prefix="agentteams-run-") if jail is not None else None
        command = [program, *argv[1:]]
        if run_tmp is not None:
            env = {**env, "TMPDIR": run_tmp}
            try:
                checked_roots = P._confinement.check_roots(root, jail["exec"], jail["write"], Path(run_tmp))
            except P._confinement.ConfinementError as exc:
                shutil.rmtree(run_tmp, ignore_errors=True)
                raise P.ProposalError(f"confined_programs.{agent}: {exc}") from exc
            if sandbox:
                command = P._confinement.wrap(command, sandbox=sandbox, root=root, cwd=cwd, exec_paths=jail["exec"],
                                            write_roots=checked_roots, tmp_dir=Path(run_tmp))
        try:
            # Own process group, so a timeout kills everything the command started before the final check.
            child = subprocess.Popen(command, cwd=cwd, env=env,
                                     stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                     start_new_session=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise P.ProposalError(f"command could not run: {exc}") from exc
        try:
            out, err = child.communicate(stdin_bytes, timeout=timeout)
            proc = subprocess.CompletedProcess(argv, child.returncode, out, err)
        except subprocess.TimeoutExpired:
            # The command ran (partly): its writes and any ledger tampering must still be checked.
            timed_out = True
            try:
                os.killpg(child.pid, 9)
                group_killed = True
            except ProcessLookupError:
                group_killed = False  # the group had already exited; recorded on the timeout row
            try:  # bounded: a grandchild that left the group (setsid) may still hold the pipes open
                out, err = child.communicate(timeout=P.POST_KILL_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                for stream in (child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()
                out, err = b"", b"[output abandoned: a process outside the killed group kept the pipes open]"
            proc = subprocess.CompletedProcess(argv, -9, out or b"", err or b"")
        if jail is not None:
            # Anything the command left running could write after the snapshot: kill the group, fail the run.
            try:
                os.killpg(child.pid, 9)
                survivors = True
            except ProcessLookupError:
                survivors = False  # the group is empty: nothing outlived the command
            if run_tmp:
                shutil.rmtree(run_tmp, ignore_errors=True)
        after = P._snapshot(root, env)
        if after is None:
            raise P.LedgerTamperedError("the git worktree vanished during the command; nothing more is recorded")
        changed = {f for f, h in after.items() if before.get(f, "∅") != h} | (set(before) - set(after))
        tampered = sorted(f for f in changed if f in (P.LEDGER_REL, P.HEAD_REL, P.DISPATCH_REL))
        if tampered:
            # Never chain onto a ledger the command altered: stop and leave it for the operator.
            raise P.LedgerTamperedError(f"the command changed {', '.join(tampered)}; nothing more is recorded")
        # Under confinement, the operator-declared write roots (build dirs) bound what the kernel let the command
        # write, so changes inside them count as declared; anything else changed is still undeclared.
        # Only when a sandbox actually ran: under the unconfined opt-out nothing at the OS level bounded them.
        roots = [w.strip("/") + "/" for w in jail["write"]] if jail is not None and sandbox else []
        def exempt(f: str) -> bool:  # inside a root, and never a protected file or the brief (any glob shape)
            return bool(roots) and f.startswith(tuple(roots)) and not P._protected(f, policy.protected_paths) \
                and not (policy.brief_rel and f.lower() == policy.brief_rel.lower())

        undeclared = sorted(f for f in changed if f not in expected_rel and not exempt(f))
        if timed_out:
            P.record(root, {"action": "run-request-timeout", "agent": agent, "argv": argv,
                          "duration_ms": int((time.monotonic() - started) * 1000),
                          "group_killed": group_killed, "undeclared_writes": undeclared, "purpose": purpose.strip()[:300]})
            raise P.UndeclaredWritesError(
                f"command timed out after {timeout}s"
                + (f"; it wrote outside its declared writes: {', '.join(undeclared)}" if undeclared else ""),
                {"agent": agent, "argv": argv, "exit": -9, "stdout": P._cap_output(proc.stdout)[0],
                 "stderr": P._cap_output(proc.stderr)[0],
                 "truncated": P._cap_output(proc.stdout)[1] or P._cap_output(proc.stderr)[1],
                 "undeclared_writes": undeclared, "ran": True})
        duration_ms = int((time.monotonic() - started) * 1000)
        P.record(root, {"action": "run-request", "agent": agent, "argv": argv, "exit": proc.returncode,
                      "duration_ms": duration_ms,
                      "undeclared_writes": undeclared, "purpose": purpose.strip()[:300],
                      **({"confined": confined_as, "survivors_killed": survivors} if confined_as else {})})
        (stdout, cut_out), (stderr, cut_err) = P._cap_output(proc.stdout), P._cap_output(proc.stderr)
        result = {"agent": agent, "argv": argv, "exit": proc.returncode, "duration_ms": duration_ms,
                  "stdout": stdout, "stderr": stderr, "truncated": cut_out or cut_err,
                  "undeclared_writes": undeclared, "ran": True}
        if undeclared:
            raise P.UndeclaredWritesError(f"command wrote outside its declared writes: {', '.join(undeclared)}", result)
        if survivors:
            raise P.UndeclaredWritesError("command left processes running after it exited; they were killed and "
                                        "the run fails", result)
        return result
    except (P.UndeclaredWritesError, P.LedgerTamperedError):
        raise  # recorded in the run-request row, or deliberately not recorded (tampered ledger)
    except P.ProposalError as exc:
        if not dry_run:
            P._record_refusal(root, "run-request", agent, str(exc))
        raise
