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
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from agentteams import proposals as P
from agentteams import runner_wasm as RW
from agentteams.proposal_policy import Policy


def _appended_notice_log(root: Path, rel: str, before: dict[str, Any], after: dict[str, Any]) -> bool:
    """True when the hook's notice log at *rel* was only appended to (or created) during the command (M6-F7).

    Decided from the snapshots, not the live tree (@security): the after entry must be a watched-file stat
    (``("stat", size, mtime_ns, ctime_ns, ino)``). A deleted log, or one under a symlinked directory that the
    snapshot's walk does not enter, appears only as a git-status content entry and is refused. If the log existed
    before, the inode must be the same and the size no smaller (append-only, as the hook's ``>>`` writes). The only live
    check is an ``lstat`` of that same inode, to refuse a leaf symlink.
    """
    import stat as _stat

    a = after.get(rel)
    if not (isinstance(a, tuple) and a and a[0] == "stat"):
        return False
    b = before.get(rel)
    if b is not None:
        if not (isinstance(b, tuple) and b and b[0] == "stat") or a[4] != b[4] or a[1] < b[1]:
            return False
    try:
        st = os.lstat(root / rel)
    except OSError:
        return False
    return _stat.S_ISREG(st.st_mode) and st.st_ino == a[4]


def run_request(artifact: dict[str, Any], *, root: Path, policy: Policy, dry_run: bool = False,
                confine: bool = False, timeout_cap: int | None = None, expect_agent: str | None = None,
                refuse_agents: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Implementation of :func:`agentteams.proposals.run_request` (see there for the contract).

    A ``sandbox: wasm`` jail (WASM plan P12) takes the wasm branch: ``argv[0]`` must be a ``wasm:<lang>``
    pseudo-program, the guest is prepared (runtimes re-hashed) before any ledger row, and the run is the pinned
    wasmtime in a fresh OS wrap (:mod:`agentteams.runner_wasm`); it never resolves a host program.
    """
    agent = "?"
    cleanups: list[Any] = []
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
        # Per-entry timeout (P5a): a long check (e.g. a kernel audit) may set one, capped at P.MAX_COMMAND_TIMEOUT.
        timeout = max(1, min(int(entry.get("timeout") or P.COMMAND_TIMEOUT), P.MAX_COMMAND_TIMEOUT))
        if timeout_cap is not None:
            timeout = max(1, min(timeout, int(timeout_cap)))
        declared = policy.confined.get(agent) or {}
        wasm_cmd = None
        if declared.get("sandbox") == "wasm":
            if not confine:
                raise P.ProposalError(f"{agent} is on sandbox: wasm; its commands run only through the runner")
            try:
                wasm_cmd = RW.prepare_command(root, agent, argv, declared, timeout_s=timeout)
            except RW.RunnerWasmError as exc:
                raise P.ProposalError(f"confined_programs.{agent}: {exc}") from exc
        elif argv[0] in RW.WASM_ARGV0 or argv[0].startswith("wasm:"):
            raise P.ProposalError(f"{argv[0]!r} names a WASM guest, but {agent} is not on sandbox: wasm; refused")
        program = wasm_cmd.meta["path"] if wasm_cmd else P._resolve_program(root, argv[0], env)
        jail = None
        if confine and wasm_cmd is not None:
            jail = declared
        elif confine:
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
            # mathAgents M6: say once per run when the command needs an unresolved xcrun launcher, or may delete a
            # package store it can write. Either alone warns; both together refuse unless the operator opted in.
            resolver = P._confinement.xcrun_resolver()
            stores = P._confinement.package_store_warnings(jail["write"], root)
            unresolved = P._confinement.unresolved_launchers(program, jail["exec"], resolver)
            warning = P._confinement.launcher_warning(program, unresolved)
            for note in [*stores, *([warning] if warning else [])]:
                print(f"[runner] {note}", file=sys.stderr)
            refusal = P._confinement.launcher_store_refusal(agent, unresolved, stores, jail)
            if refusal:
                raise P.ProposalError(f"confined_programs.{agent}: {refusal}")
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
        wasm_row: dict[str, Any] = {}
        if wasm_cmd is not None:
            confined_as = f"{confined_as}+wasm"
            wasm_row = {"sandbox": "wasm", "wasm": {**{k: v for k, v in wasm_cmd.meta.items() if k != "path"},
                                                     "os_confinement": "nested" if sandbox else "none"}}
        P._consume(root, artifact, agent)
        P.record(root, {"action": "run-request-start", "agent": agent, "argv": argv, "purpose": purpose.strip()[:300],
                      **({"confined": confined_as} if confined_as else {}), **wasm_row})
        run_tmp, command, cmd_env, cmd_cwd = None, [program, *argv[1:]], env, cwd
        if wasm_cmd is not None:
            try:
                launch = RW.launch(wasm_cmd, root, sandbox=sandbox)
                cleanups.append(launch.cleanup)
                exec_path = RW.exec_literal(launch.argv)  # the wrap's one exec literal (R3'): never a writable place
                P._confinement.check_roots(root, [exec_path], [wasm_cmd.scratch_rel] if wasm_cmd.scratch_rel else [],
                                           Path(launch.run_dir, "tmp") if launch.run_dir else None)
            except (RW.RunnerWasmError, P._confinement.ConfinementError) as exc:
                raise P.ProposalError(f"confined_programs.{agent}: {exc}") from exc
            command, cmd_env, cmd_cwd = launch.argv, launch.env, Path(launch.cwd)  # env is empty (R9)
        before = P._snapshot(root, env)  # re-taken after the write-ahead row, so only the command's changes count
        timed_out, group_killed, survivors = False, False, False
        survivor_why: str | None = None
        report_r: int | None = None
        report_w: int | None = None
        started = time.monotonic()
        notice_days = {time.strftime("%Y-%m-%d", time.gmtime())}  # the hook names its log by UTC date (M6-F7)
        if jail is not None and wasm_cmd is None:
            run_tmp = tempfile.mkdtemp(prefix="agentteams-run-")
        if run_tmp is not None:
            cmd_env = {**env, "TMPDIR": run_tmp}
            try:
                checked_roots = P._confinement.check_roots(root, jail["exec"], jail["write"], Path(run_tmp))
            except P._confinement.ConfinementError as exc:
                shutil.rmtree(run_tmp, ignore_errors=True)
                raise P.ProposalError(f"confined_programs.{agent}: {exc}") from exc
            if sandbox == "bwrap":  # #262 item 2: survivors are counted inside the sandbox, before its teardown
                report_r, report_w = P._confinement.survivor_channel()
            if sandbox:
                try:
                    command = P._confinement.wrap(command, sandbox=sandbox, root=root, cwd=cwd,
                                                exec_paths=jail["exec"], write_roots=checked_roots,
                                                tmp_dir=Path(run_tmp), resolver=resolver, survivor_fd=report_w)
                except P._confinement.ConfinementError as exc:
                    for fd in (report_r, report_w):
                        if fd is not None:
                            os.close(fd)
                    shutil.rmtree(run_tmp, ignore_errors=True)
                    raise P.ProposalError(f"confined_programs.{agent}: {exc}") from exc
        try:
            # Own process group, so a timeout kills everything the command started before the final check.
            child = subprocess.Popen(command, cwd=cmd_cwd, env=cmd_env,
                                     stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                     start_new_session=True, pass_fds=(report_w,) if report_w is not None else ())
        except (OSError, subprocess.SubprocessError) as exc:
            if report_r is not None:
                os.close(report_r)
            raise P.ProposalError(f"command could not run: {exc}") from exc
        finally:
            if report_w is not None:
                os.close(report_w)  # only the shim holds the write end now
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
                survivor_why = "its process group was still alive after it exited"
            except ProcessLookupError:
                survivor_why = None  # the group is empty: nothing outlived the command
            if report_r is not None:
                # bwrap: the in-sandbox count is the reliable signal (the group is often already gone). A timeout
                # killed the shim too, so its missing report is not read as survivors there.
                reported = P._confinement.read_survivor_report(report_r)
                report_r = None
                if reported and not timed_out:
                    survivor_why = f"{survivor_why}; {reported}" if survivor_why else reported
            survivors = survivor_why is not None
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
        # Rule-12 carve-out 3 (P12): a wasm agent's scratch counts as declared, only when an OS sandbox bounded it.
        if wasm_cmd is not None and wasm_cmd.scratch_rel and sandbox:
            roots.append(wasm_cmd.scratch_rel + "/")
        def exempt(f: str) -> bool:  # inside a root, and never a protected file or the brief (any glob shape)
            return bool(roots) and f.startswith(tuple(roots)) and not P._protected(f, policy.protected_paths) \
                and not (policy.brief_rel and f.lower() == policy.brief_rel.lower())

        # The runner's heartbeat changes while a command runs (M6-F6). Exempt only when a sandbox ran: it keeps the
        # command out of .agentteams, so the change is the runner's. Unconfined, nothing bounds it, so it stays watched.
        runner_own = {P.RUNNER_HEARTBEAT_REL} if sandbox else set()
        # The generated Claude hook appends `.claude/hook-notices/<UTC date>.log` while a long command runs (M6-F7).
        # Same reasoning as the heartbeat: only when a sandbox ran (it keeps the command out of .claude), only that
        # exact path for the UTC day(s) the command spanned, and only if the file still exists (an append or a create,
        # never a deletion). Rule 12: this exempts one log file from the escape backstop; every other .claude path,
        # .claude/settings.json included, stays watched, and nothing is exempt unconfined.
        notice_days.add(time.strftime("%Y-%m-%d", time.gmtime()))
        hook_own = {rel for rel in (f".claude/hook-notices/{d}.log" for d in notice_days)
                    if _appended_notice_log(root, rel, before, after)} if sandbox else set()
        undeclared = sorted(f for f in changed if f not in expected_rel and not exempt(f) and f not in runner_own
                            and f not in hook_own)
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
                      **({"confined": confined_as, "survivors_killed": survivors} if confined_as else {}),
                      **wasm_row})
        (stdout, cut_out), (stderr, cut_err) = P._cap_output(proc.stdout), P._cap_output(proc.stderr)
        result = {"agent": agent, "argv": argv, "exit": proc.returncode, "duration_ms": duration_ms,
                  "stdout": stdout, "stderr": stderr, "truncated": cut_out or cut_err,
                  "undeclared_writes": undeclared, "ran": True}
        if wasm_cmd is not None:  # diagnostic only: a guest can forge wasmtime's stderr (C-4)
            result["trap"] = RW._wx.classify_trap(proc.returncode, proc.stderr or b"")
        if undeclared:
            raise P.UndeclaredWritesError(f"command wrote outside its declared writes: {', '.join(undeclared)}", result)
        if survivors:
            raise P.UndeclaredWritesError(f"command left processes running after it exited ({survivor_why}); they "
                                        "were killed and the run fails", result)
        return result
    except (P.UndeclaredWritesError, P.LedgerTamperedError):
        raise  # recorded in the run-request row, or deliberately not recorded (tampered ledger)
    except P.ProposalError as exc:
        if not dry_run:
            P._record_refusal(root, "run-request", agent, str(exc))
        raise
    finally:
        for cleanup in cleanups:  # after the final snapshot: the run dir existed in both, so it is no change
            cleanup()
