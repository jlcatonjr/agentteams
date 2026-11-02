"""launch.py — Stage 0 of the WASM agent host: the unconfined launcher that owns ``.agentteams/`` (P05 §4.9).

:func:`stage0` (called only by P08's ``run_wasm_agent``) checks the pins, loads the provider key, creates the run
directory, the agent scratch dir and the exec area, then either

* **wraps Stage 1** in the host OS sandbox (Seatbelt/bwrap, rendered by ``_sandbox_profiles``; no write root but the
  agent's scratch dir, credential stores read-denied, network allowed for the provider) and serves its control pipe
  (``journal`` -> append + ack; ``prepare`` / ``release`` -> :class:`~agentteams.wasm_agent.broker.StepPreparer`;
  ``result``), or
* when no OS sandbox is usable (native Windows, inside another sandbox, or the OS switch off), runs the same host loop
  **in-process** with :class:`~agentteams.wasm_agent.transcript.FileJournal` and
  :class:`~agentteams.wasm_agent.broker.LocalBroker`, and says so: ``WASM host not OS-confined: <reason>`` goes to the
  output, the transcript and ``RunResult.notices`` (D2: never silent).

**Key custody.** The key is read from the environment here, sent to Stage 1 once over a dedicated pipe (with the
per-run reserved-key MAC key, which therefore binds reserved keys against guests and models, not against a compromised
Stage 1) and never placed in any environment, argv or file. Stage 1 starts with a scrubbed
environment; wasmtime children get an empty one and no inherited descriptors.

**Delegation** (P09). With ``resolve_agent`` (the lock-verified team's ``agent_spec``), Stage 0 also serves the
tree's child runs (:class:`~agentteams.wasm_agent.child_runs.ChildRuns`: run dirs, transcripts and preparers holding
each child's attenuated grant), creates every scratch dir the tree could be granted
(:func:`~agentteams.wasm_agent.delegation.reachable_scratch_dirs`, derived from the team and the root grant, never from
model output) and makes them Stage 1's only extra write roots. Without it, ``delegate`` ops are refused.

**Guest ``os_confine``** (R5). P05 never originates ``"inherited"``; it forwards the launcher's value only when it
actually wrapped Stage 1, and downgrades it to ``"auto"`` otherwise. Under a wrapped Stage 1 a fresh OS sandbox
cannot nest (Seatbelt), so ``"auto"`` becomes ``"off"`` (reported ``"none"``: the host profile still applies, but it
is not claimed) and ``"require"`` refuses.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import secrets as _secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import IO, Any

import agentteams
from agentteams import _sandbox_profiles as SP
from agentteams import model_providers as MP
from agentteams import wasm_exec as W
from agentteams import wasm_guests as G
from agentteams.vulnscan import gateway_dlp as DLP
import agentteams.wasm_agent.protocol as P
from agentteams.wasm_agent.broker import GUEST_OS_CONFINE, BrokerError, LocalBroker, StepPreparer, launch_to_dict
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.child_runs import ChildRuns
from agentteams.wasm_agent.delegation import (
    DelegationError, DelegationPolicy, RunTree, child_tools, clamp_budget, reachable_scratch_dirs,
)
from agentteams.wasm_agent.guest_source import GUEST_SHA256, GuestSourceError, load_guest_source
from agentteams.wasm_agent.host import (
    OUTPUT_HEADER, STATUSES, AgentSpec, Revocation, RunOptions, RunResult, build_provider, run_agent,
)
from agentteams.wasm_agent.transcript import FileJournal, JournalError, ensure_dir

#: What the confined host may not read (R5): the executor's credential stores plus the agentteams secrets dir (its
#: values reach Stage 1 over the key pipe instead). File denies only: Keychain items stay reachable through the Security
#: framework (securityd), which the profile does not filter.
HOST_DENY_READS = (*W.NEST_DENY_READS, DLP.SECRETS_DIR)
#: The only variables Stage 1's environment keeps (no key, token or secret name survives).
ENV_KEEP = ("PATH", "LANG", "HOME")
HOST_CONFINE = ("auto", "require", "off")
#: Test-only opt-in (``=1``) for a *guest_source* other than the pinned guest (@security P07 C1); never set outside
#: the test suite (``tests/conftest.py``).
TEST_GUEST_ENV = "AGENTTEAMS_WASM_TEST_GUEST"
_BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); from agentteams.wasm_agent.stage1 import main; "
         "raise SystemExit(main(sys.argv[2:]))")
_SECRET_NAME = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


class LaunchError(Exception):
    """Stage 0 cannot start the run (a refusal before any model call)."""


def scrubbed_env(environ: Mapping[str, str], tmp_dir: str) -> dict[str, str]:
    """Stage 1's whole environment: ``PATH``, ``LANG``, ``HOME`` and a private ``TMPDIR``, nothing else.

    Args:
        environ: The launcher's environment.
        tmp_dir: Stage 1's private temp dir.

    Returns:
        The environment (any kept name that looks like a credential is dropped too).

    Raises:
        Nothing.
    """
    env = {k: environ[k] for k in ENV_KEEP if k in environ and not any(s in k.upper() for s in _SECRET_NAME)}
    env["TMPDIR"] = tmp_dir
    return env


def guest_os_confine_for(requested: str, *, wrapped: bool) -> tuple[str, str | None]:
    """The guest ``os_confine`` Stage 0 uses, and the reason when it differs from the request (R5).

    Args:
        requested: The launcher's value (``auto`` / ``require`` / ``inherited`` / ``off``).
        wrapped: Whether Stage 1 runs inside the host OS sandbox.

    Returns:
        ``(mode, note or None)``.

    Raises:
        LaunchError: ``require`` under a wrapped host (a fresh sandbox cannot nest), or an unknown mode.
    """
    if requested not in GUEST_OS_CONFINE:
        raise LaunchError(f"guest_os_confine {requested!r} must be one of {', '.join(GUEST_OS_CONFINE)}")
    if not wrapped:
        if requested == "inherited":
            return "auto", "guest os_confine 'inherited' downgraded to 'auto': the host was not OS-confined"
        return requested, None
    if requested == "require":
        raise LaunchError("guest os_confine 'require' cannot be met inside the confined host (a fresh OS sandbox "
                          "cannot nest); the launcher may pass 'inherited'")
    if requested == "auto":
        return "off", ("guest runs inside the host's OS profile without a fresh one (reported 'none'; the launcher "
                       "may pass 'inherited' to record the host profile)")
    return requested, None


def host_profile_argv(kind: str, *, project_root: str, tmp_dir: str, scratch: str | None,
                      exec_literals: Sequence[str], exec_paths: Sequence[str], profiles: ModuleType = SP,
                      child_scratch: Sequence[str] = ()) -> list[str]:
    """The OS-sandbox prefix for Stage 1 (append the command).

    No write root but *tmp_dir* and the agent's scratch dir (a late allow inside the write-denied control plane,
    R11); the run, exec and install dirs stay readable and never writable; credential stores and the key dir are
    read-denied (R5); the network stays allowed (the host calls the provider).

    Args:
        kind: ``seatbelt`` or ``bwrap``.
        project_root: The project root (real path).
        tmp_dir: Stage 1's private temp dir (outside the project).
        scratch: The agent's scratch dir (``.agentteams/wasm/scratch/<agent>``), or None.
        exec_literals: Programs Stage 1 may execute by exact path (python, an in-place wasmtime).
        exec_paths: Directories Stage 1 may execute from (Seatbelt subpaths: the python prefix, the exec area).
        profiles: The ``_sandbox_profiles`` module.
        child_scratch: P09: the scratch dirs of the agents this run may delegate to (Stage 0 created them;
            ``delegation.reachable_scratch_dirs``), also late write allows.

    Returns:
        The argv prefix.

    Raises:
        LaunchError: An unknown kind, or a path the profile builders refuse.
    """
    common: dict[str, Any] = dict(root=Path(project_root), write_roots=[], tmp_dir=Path(tmp_dir),
                                  protected_rel=W.NEST_PROTECTED_REL, key_dir=W.NEST_KEY_DIR,
                                  deny_reads=HOST_DENY_READS, deny_network=False)
    late = ([scratch] if scratch else []) + [d for d in child_scratch if d != scratch]
    try:
        if kind == "seatbelt":
            return ["/usr/bin/sandbox-exec", "-p", profiles.seatbelt_profile(
                exec_paths=list(exec_paths), exec_literals=list(exec_literals), dev_writes=W.NEST_DEV_WRITES,
                late_write_allows=late, **common)]
        if kind == "bwrap":
            return [*profiles.bwrap_argv(cwd=Path(tmp_dir), late_binds=late, **common), "--"]
    except profiles.ConfinementError as exc:
        raise LaunchError(f"host OS profile refused: {exc}") from exc
    raise LaunchError(f"unknown host sandbox {kind!r}")


def _scratch_of(grant: Any, root: str) -> str | None:
    for m in grant.mounts:
        if m.guest == "/scratch" and m.host != W.RUN_SCRATCH:
            rel = os.path.relpath(os.path.abspath(m.host), root).split(os.sep)
            if rel[:3] != [".agentteams", "wasm", "scratch"] or len(rel) != 4:
                raise LaunchError(f"/scratch host {m.host} must be <project>/.agentteams/wasm/scratch/<agent>")
            try:
                return ensure_dir(root, rel)
            except (OSError, JournalError) as exc:
                raise LaunchError(f"cannot create the agent scratch dir: {exc}") from exc
    return None


def _ensure_scratch(root: str, path: str) -> str:
    """Create one delegation scratch dir (``<root>/.agentteams/wasm/scratch/<agent>``; Stage 0 only, R4)."""
    rel = os.path.relpath(path, root).split(os.sep)
    if rel[:3] != [".agentteams", "wasm", "scratch"] or len(rel) != 4:
        raise LaunchError(f"delegation scratch {path} must be <project>/.agentteams/wasm/scratch/<agent>")
    try:
        return ensure_dir(root, rel)
    except (OSError, JournalError) as exc:
        raise LaunchError(f"cannot create a delegation scratch dir: {exc}") from exc


# --- the control pipe (Stage 0 side) -----------------------------------------------------------------------------

def _child_open(children: ChildRuns | None, msg: Mapping[str, Any]) -> dict[str, Any]:
    """Serve one ``child-open`` (P09): Stage 0 decides, builds and records the child; the reply carries its spec."""
    if children is None:
        return {"ok": False, "code": "refused", "error": "delegation is not configured for this run"}
    try:
        slot = children.open(parent=msg["parent"], agent=msg["agent"], delegation_id=msg["id"])
    except DelegationError as exc:
        return {"ok": False, "code": exc.code, "error": exc.message}
    return {"ok": True, "run_id": slot.run_id, "spec": slot.spec.to_dict(), "dropped": list(slot.dropped),
            "depth": slot.depth}


def _routed(msg: Mapping[str, Any], keys: set[str]) -> str | None:
    """The child run a request names (``run``), None for the root; raises KeyError for a malformed request."""
    if set(msg) == keys:
        return None
    if set(msg) == keys | {"run"} and isinstance(msg["run"], str):
        return msg["run"]
    raise KeyError("request keys")


def serve_control(reader: P.LineReader, wfd: int, journal: FileJournal, preparer: StepPreparer,
                  children: ChildRuns | None = None) -> dict[str, Any] | None:
    """Serve Stage 1's requests until it sends its result or the pipe closes.

    ``journal`` / ``prepare`` / ``release`` may name a live child run (``run``, P09); ``child-open`` /
    ``child-close`` go to *children*.

    Args:
        reader: Reads Stage 1's lines.
        wfd: Writes Stage 0's replies.
        journal: The run's transcript writer.
        preparer: The run's guest preparer.
        children: The tree's child-run service, or None (delegation off).

    Returns:
        Stage 1's result object (data; validated by the caller), or None when it exited without one.

    Raises:
        Nothing (a malformed request is journaled as ``protocol-error`` and ends serving).
    """
    while True:
        try:
            msg = reader.read()
        except (P.ProtocolError, OSError) as exc:
            with contextlib.suppress(JournalError):
                journal.append("protocol-error", {"source": "stage1", "code": getattr(exc, "code", "io")})
            return None
        if msg is None:
            return None
        op, reply = msg.get("op"), {"ok": False, "error": "unknown op"}
        try:
            if op == "journal" and isinstance(msg.get("body"), dict):
                run = _routed(msg, {"op", "kind", "body"})
                sink = journal if run is None else _need(children).journal_for(run)
                reply = {"ok": True, "hash": sink.append(str(msg["kind"]), msg["body"])}
            elif op == "prepare":
                run = _routed(msg, {"op", "purpose", "language", "source"})
                prep_for = preparer if run is None else _need(children).preparer_for(run)
                ticket, launch, prep = prep_for.prepare(str(msg["purpose"]), language=msg["language"],
                                                        source=msg["source"])
                reply = {"ok": True, "ticket": ticket, "launch": launch_to_dict(launch), "prepare_s": prep}
            elif op == "release":
                run = _routed(msg, {"op", "ticket"})
                (preparer if run is None else _need(children).preparer_for(run)).release(str(msg["ticket"]))
                reply = {"ok": True}
            elif op == "child-open" and set(msg) == {"op", "parent", "agent", "id"}:
                reply = _child_open(children, msg)
            elif op == "child-close" and set(msg) == {"op", "run", "result"} and isinstance(msg["result"], dict):
                if children is not None:
                    children.close(str(msg["run"]), msg["result"])
                reply = {"ok": True}
            elif op == "result" and set(msg) == {"op", "result"} and isinstance(msg["result"], dict):
                with contextlib.suppress(OSError):
                    P.write_line(wfd, {"ok": True})
                return msg["result"]
            else:
                with contextlib.suppress(JournalError):
                    journal.append("protocol-error", {"source": "stage1", "code": "unknown-request",
                                                      "op": str(op)[:40]})
                with contextlib.suppress(OSError, P.ProtocolError):
                    P.write_line(wfd, reply)
                return None
        except KeyError:
            with contextlib.suppress(JournalError):
                journal.append("protocol-error", {"source": "stage1", "code": "unknown-request", "op": str(op)[:40]})
            with contextlib.suppress(OSError, P.ProtocolError):
                P.write_line(wfd, reply)
            return None
        except JournalError as exc:
            reply = {"ok": False, "error": str(exc)[:300]}
        except BrokerError as exc:
            reply = {"ok": False, "error": str(exc)[:300]}
        try:
            P.write_line(wfd, reply)
        except (OSError, P.ProtocolError):
            return None


def _need(children: ChildRuns | None) -> ChildRuns:
    if children is None:
        raise JournalError("delegation is not configured for this run")
    return children


# --- stage 0 -------------------------------------------------------------------------------------------------------

def _refused(reason: str, notices: list[str]) -> RunResult:
    return RunResult(status="refused", reason=reason, output="", run_id="", transcript_head="", usage={},
                     notices=notices)


def _result_from(data: Mapping[str, Any], journal: FileJournal, run_id: str, notices: list[str]) -> RunResult:
    status = data.get("status")
    if status not in STATUSES:
        return RunResult("failed", "host-bad-result", "", run_id, journal.head, {}, notices)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    timings = data.get("step_timings") if isinstance(data.get("step_timings"), list) else []
    return RunResult(status=status, reason=str(data.get("reason", ""))[:200], output=str(data.get("output", "")),
                     run_id=run_id, transcript_head=journal.head, usage=usage,
                     notices=notices + [str(n)[:300] for n in data.get("notices", []) if n not in notices][:20],
                     step_timings=[t for t in timings if isinstance(t, dict)][:1000])


def stage0(spec: AgentSpec, task: str, *, project_root: str | Path, budget: Budget, guest_source: bytes | None = None,
           guest_sha256: str | None = None, environ: Mapping[str, str] | None = None, base_url: str | None = None,
           stub_script: Sequence[Mapping[str, Any]] | None = None, state: Any = None, guest_os_confine: str = "auto",
           host_confine: str = "auto", resolver: Any = None, profiles: ModuleType = SP,
           control_plane_protected: bool = False, prices: Mapping[str, Mapping[str, float]] | None = None,
           call_timeout_s: float = 300.0, echo: IO[str] | None = None,
           resolve_agent: Callable[[str], AgentSpec] | None = None,
           delegation: Mapping[str, Any] | None = None) -> RunResult:
    """Run one agent: Stage 0 (see the module docstring).

    Args:
        spec: The agent (P08 builds it from the lock-verified team).
        task: The task text.
        project_root: The project root.
        budget: The run's caps.
        guest_source: The agent-loop guest program; None (the default) loads P07's pinned
            ``data/wasm_agent_guest.py`` through ``guest_source.load_guest_source`` (a test guest in tests).
        guest_sha256: Its pinned sha256 (checked before the first step); given together with *guest_source*. Any
            hash other than ``guest_source.GUEST_SHA256`` is refused unless :data:`TEST_GUEST_ENV` is ``1``.
        environ: Where the key is read from (default ``os.environ``).
        base_url: Optional provider base-URL override (vetted by P06).
        stub_script: ``stub`` provider only: scripted replies (operator smoke tests).
        state: The ``SandboxState``; its ``os`` flag gates host confinement (None: try it).
        guest_os_confine: From the P08 launcher (the only place ``"inherited"`` may come from).
        host_confine: ``auto`` (confine when possible), ``require`` (refuse otherwise) or ``off``.
        resolver: Provisioning (default ``wasm_runtime.Resolver(project_root)``).
        profiles: The ``_sandbox_profiles`` module.
        control_plane_protected: R3′: R1 write-denies ``.agentteams/`` to sessions, so pinned binaries run in place.
        prices: A ``gateway_dialects.load_prices`` table, or None.
        call_timeout_s: Per model call socket timeout cap.
        echo: Where the operator-facing summary goes (None: nowhere).
        resolve_agent: P09: ``slug -> AgentSpec`` from the lock-verified team (``Team.agent_spec``); None turns
            delegation off (``delegate`` ops are refused).
        delegation: P09: ``runtime.json``'s normalised ``delegation`` block (``default_share``, ``max_tree_runs``),
            or None for the defaults.

    Returns:
        The :class:`RunResult` (``transcript_path`` set when a transcript exists).

    Raises:
        Nothing for run failures (refusals become a ``refused`` result).
    """
    root = os.path.realpath(str(project_root))
    env = os.environ if environ is None else environ
    notices: list[str] = []
    if host_confine not in HOST_CONFINE:
        return _refused(f"host_confine {host_confine!r} must be one of {', '.join(HOST_CONFINE)}", notices)
    if (guest_source is None) != (guest_sha256 is None):
        return _refused("guest_source and guest_sha256 go together (or neither, for the pinned guest)", notices)
    if guest_sha256 is not None and guest_sha256 != GUEST_SHA256 and os.environ.get(TEST_GUEST_ENV) != "1":
        return _refused(f"only the pinned guest program runs (sha256 {GUEST_SHA256[:16]}...); a caller-supplied "
                        f"guest needs the test-only {TEST_GUEST_ENV}=1", notices)
    if guest_source is None:
        try:
            guest_source, guest_sha256 = load_guest_source()
        except GuestSourceError as exc:
            return _refused(str(exc)[:300], notices)
    if resolver is None:
        from agentteams import wasm_runtime
        resolver = wasm_runtime.Resolver(Path(root))
    try:   # pins first: nothing is spent on an unverifiable runtime
        resolver.resolve("wasmtime")
        resolver.resolve("python")
    except (OSError, ValueError, LookupError, RuntimeError) as exc:
        return _refused(f"runtime: {str(exc)[:200]}", notices)
    try:
        preset = MP.PROVIDERS[spec.provider] if spec.provider in MP.PROVIDERS else None
        if preset is None:
            raise MP.ModelProviderError("config", f"unknown provider {spec.provider!r}")
        key = MP.load_key(preset, env)
        if stub_script is not None and preset.family != "stub":
            raise MP.ModelProviderError("config", "stub_script is only valid for the stub provider")
    except MP.ModelProviderError as exc:
        return _refused(f"provider: {exc.message}", notices)
    os_on = True if state is None else bool(getattr(state, "os", True))
    kind = profiles.available() if os_on and host_confine != "off" else None
    if kind is None:
        why = ("the OS sandbox switch is off" if not os_on else "host_confine=off" if host_confine == "off"
               else f"no usable OS sandbox here ({sys.platform}; Seatbelt cannot nest inside another sandbox)")
        if host_confine == "require":
            return _refused(f"host_confine=require but {why}", notices)
        notices.append(f"WASM host not OS-confined: {why}")
    policy: DelegationPolicy | None = None
    child_scratch: tuple[str, ...] = ()
    if resolve_agent is not None:
        try:
            policy = DelegationPolicy(**dict(delegation or {}))
        except (TypeError, ValueError) as exc:
            return _refused(f"delegation: {exc}", notices)
        budget = clamp_budget(budget)
        tools, targets = child_tools(spec.tools, spec.grant, spec.delegates,
                                     can_delegate=budget.max_delegation_depth >= 1)
        spec = AgentSpec(**{**vars(spec), "tools": tools, "delegates": targets})
    try:
        guest_mode, note = guest_os_confine_for(guest_os_confine, wrapped=kind is not None)
        scratch = _scratch_of(spec.grant, root)
        if resolve_agent is not None:
            child_scratch = reachable_scratch_dirs(resolve_agent, spec.slug, spec.grant, project_root=root,
                                                   max_depth=budget.max_delegation_depth)
            for d in child_scratch:
                _ensure_scratch(root, d)

        def make_preparer(grant: Any) -> StepPreparer:
            return StepPreparer(project_root=root, grant=grant, guest_source=guest_source, guest_sha256=guest_sha256,
                                resolver=resolver, executor=W, guests=G, profiles=profiles, os_confine=guest_mode,
                                control_plane_protected=control_plane_protected)
        preparer = make_preparer(spec.grant)
    except (LaunchError, BrokerError, ValueError) as exc:
        return _refused(str(exc)[:300], notices)
    if note:
        notices.append(note)
    run_id, mac_key = _secrets.token_hex(16), _secrets.token_bytes(32)
    known = sorted({key, *DLP.load_secret_files(DLP.secrets_dir_files())[0]} - {""})
    try:
        journal = FileJournal(root, run_id, secrets=known, mac_key=mac_key)
    except JournalError as exc:
        return _refused(f"transcript: {exc}", notices)
    children = None
    if resolve_agent is not None and policy is not None:
        tree = RunTree(max_depth=budget.max_delegation_depth, max_child_runs=budget.max_child_runs,
                       max_tree_runs=policy.max_tree_runs)
        children = ChildRuns(project_root=root, root_run_id=run_id, root_spec=spec, tree=tree,
                             resolve_agent=resolve_agent, make_preparer=make_preparer,
                             write_roots=(*child_scratch, *([scratch] if scratch else [])), secrets=known,
                             mac_key=mac_key)
    try:
        if kind is None:
            result = _in_process(spec, task, root=root, run_id=run_id, budget=budget, key=key, base_url=base_url,
                                 stub_script=stub_script, mac_key=mac_key, known=known, journal=journal,
                                 preparer=preparer, prices=prices, notices=notices, call_timeout_s=call_timeout_s,
                                 children=children, policy=policy)
        else:
            notices.append(f"WASM host OS-confined: nested:{kind}")
            boot = {"v": P.BOOTSTRAP_VERSION, "provider": spec.provider, "key": key, "base_url": base_url,
                    "stub_script": list(stub_script) if stub_script is not None else None, "mac_key": mac_key.hex(),
                    "run_id": run_id, "project_root": root, "spec": spec.to_dict(), "task": task,
                    "budget": budget.to_dict(), "secrets": known, "prices": prices, "notices": notices,
                    "call_timeout_s": call_timeout_s, "delegation": policy.to_dict() if policy else None}
            result = _wrapped(kind, boot, root=root, scratch=scratch, journal=journal, preparer=preparer,
                              resolver=resolver, profiles=profiles, control_plane_protected=control_plane_protected,
                              env=env, budget=budget, run_id=run_id, notices=notices, children=children,
                              child_scratch=child_scratch)
    finally:
        if children is not None:
            children.close_all()
        preparer.release_all()
        journal.close()
    result.run_id = result.run_id or run_id
    result.transcript_head = journal.head
    result.transcript_path = str(journal.path)
    with contextlib.suppress(JournalError):
        journal.write_result(result.to_dict())
    if echo is not None:
        print_result(result, echo)
    return result


def _in_process(spec: AgentSpec, task: str, *, root: str, run_id: str, budget: Budget, key: str,
                base_url: str | None, stub_script: Sequence[Mapping[str, Any]] | None, mac_key: bytes,
                known: list[str], journal: FileJournal, preparer: StepPreparer, prices: Any, notices: list[str],
                call_timeout_s: float, children: ChildRuns | None = None,
                policy: DelegationPolicy | None = None) -> RunResult:
    try:
        provider = build_provider(spec.provider, key, base_url=base_url, stub_script=stub_script)
    except MP.ModelProviderError as exc:
        return _refused(f"provider: {exc.message}", notices)
    revocation = Revocation(root, run_id)
    options = RunOptions(project_root=Path(root), run_id=run_id, budget=budget, journal=journal,
                         exec_broker=LocalBroker(preparer), provider=provider, mac_key=mac_key, secrets=known,
                         prices=prices, revocation=revocation, notices=notices, call_timeout_s=call_timeout_s,
                         children=children, delegation=policy)
    with _signals(lambda signum: revocation.set(f"signal:{signum}")):
        return run_agent(spec, task, options)


@contextlib.contextmanager
def _signals(handler: Any) -> Any:
    """Route SIGINT/SIGTERM to *handler* for the block (main thread only; elsewhere a no-op)."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    for s in old:
        signal.signal(s, lambda signum, frame: handler(signum))
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def stage1_argv(python: str, fds: tuple[int, int, int]) -> list[str]:
    """The Stage 1 command: isolated python, the running agentteams tree first on ``sys.path``, the three fds.

    Args:
        python: The interpreter (Stage 0's own).
        fds: ``(key pipe read end, control-in read end, control-out write end)``.

    Returns:
        The argv (to append to the OS-sandbox prefix).

    Raises:
        Nothing.
    """
    pkg_parent = os.path.dirname(os.path.dirname(os.path.realpath(agentteams.__file__)))
    return [python, "-I", "-B", "-c", _BOOT, pkg_parent, "--key-fd", str(fds[0]), "--ctl-in", str(fds[1]),
            "--ctl-out", str(fds[2])]


def _exec_allow(root: str, resolver: Any, control_plane_protected: bool) -> tuple[list[str], list[str]]:
    """Seatbelt exec allows for Stage 1: python (literal + its prefix), the exec area, an in-place wasmtime."""
    python = os.path.realpath(sys.executable)
    literals, paths = [python], [ensure_dir(root, (".agentteams", "wasm", "exec"))]
    prefix = os.path.realpath(sys.base_prefix)
    home = os.path.realpath(os.path.expanduser("~"))
    if prefix not in ("/", home) and not W.path_inside(root, prefix) and not W.path_inside(prefix, root):
        paths.append(prefix)
    if control_plane_protected:
        literals.append(os.path.realpath(str(resolver.resolve("wasmtime"))))
    return literals, paths


def _stop(proc: subprocess.Popen) -> None:
    """Kill Stage 1 and reap it (no zombie, no orphaned descriptor holder)."""
    with contextlib.suppress(OSError):
        proc.kill()
    proc.wait()


def _wrapped(kind: str, boot: dict[str, Any], *, root: str, scratch: str | None, journal: FileJournal,
             preparer: StepPreparer, resolver: Any, profiles: ModuleType, control_plane_protected: bool,
             env: Mapping[str, str], budget: Budget, run_id: str, notices: list[str],
             children: ChildRuns | None = None, child_scratch: Sequence[str] = ()) -> RunResult:
    tmp_dir = tempfile.mkdtemp(prefix="agentteams-wasm-host-")
    pipes: list[int] = []
    proc: subprocess.Popen | None = None
    try:
        literals, paths = _exec_allow(root, resolver, control_plane_protected)
        key_r, key_w = os.pipe()
        in_r, in_w = os.pipe()       # Stage 0 -> Stage 1
        out_r, out_w = os.pipe()     # Stage 1 -> Stage 0
        pipes += [key_r, key_w, in_r, in_w, out_r, out_w]
        argv = host_profile_argv(kind, project_root=root, tmp_dir=tmp_dir, scratch=scratch, exec_literals=literals,
                                 exec_paths=paths, profiles=profiles, child_scratch=child_scratch) + stage1_argv(literals[0], (key_r, in_r, out_w))
        proc = subprocess.Popen(argv, pass_fds=(key_r, in_r, out_w), env=scrubbed_env(env, tmp_dir), cwd=tmp_dir,
                                stdin=subprocess.DEVNULL, start_new_session=False)
        for fd in (key_r, in_r, out_w):
            os.close(fd)
            pipes.remove(fd)
        try:
            P.write_line(key_w, boot)
        except P.ProtocolError as exc:   # an oversize bootstrap (task/spec/secrets): Stage 1 never starts its run
            _stop(proc)
            boot.clear()
            with contextlib.suppress(JournalError):
                journal.append("protocol-error", {"source": "stage0", "code": "boot-" + exc.code})
            return RunResult("refused", f"host-launch: bootstrap {exc.code}", "", run_id, journal.head, {}, notices)
        except OSError as exc:           # Stage 1 died before reading it; it then exits without a result
            with contextlib.suppress(JournalError):
                journal.append("protocol-error", {"source": "stage0", "code": "key-pipe",
                                                  "detail": type(exc).__name__})
        os.close(key_w)
        pipes.remove(key_w)
        boot.clear()   # drop Stage 0's extra reference to the key
        watchdog = threading.Timer(budget.wall_clock_s + 120.0, proc.kill)
        watchdog.daemon = True
        watchdog.start()
        with _signals(lambda signum: proc.send_signal(signal.SIGTERM) if proc and proc.poll() is None else None):
            data = serve_control(P.LineReader(out_r), in_w, journal, preparer, children)
        watchdog.cancel()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _stop(proc)
        if data is None:
            return RunResult("failed", "host-exited", "", run_id, journal.head, {}, notices)
        return _result_from(data, journal, run_id, notices)
    except (LaunchError, OSError) as exc:
        if proc is not None and proc.poll() is None:
            _stop(proc)
        return RunResult("refused", f"host-launch: {str(exc)[:200]}", "", run_id, journal.head, {}, notices)
    finally:
        for fd in pipes:
            with contextlib.suppress(OSError):
                os.close(fd)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def print_result(result: RunResult, out: IO[str]) -> None:
    """Print the operator-facing summary: notices, the output under its data header, the transcript head.

    Args:
        result: The run result.
        out: The stream.

    Returns:
        None.

    Raises:
        Nothing.
    """
    for n in result.notices:
        print(n, file=out)
    print(f"wasm agent run {result.run_id or '-'}: {result.status} ({result.reason})", file=out)
    if result.output:
        print(OUTPUT_HEADER, file=out)
        print(result.output, file=out)
        print("--- end of wasm agent output ---", file=out)
    if result.transcript_head:
        print(f"transcript head: {result.transcript_head}", file=out)
