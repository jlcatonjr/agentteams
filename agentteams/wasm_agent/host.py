"""host.py — the WASM agent host loop (P05 §4.3, §4.7, §4.10).

:func:`run_agent` runs one agent as a sequence of step-mode guest invocations. Per step it checks revocation and the
budget, builds the event (secret-checked, S-3), has the guest run through ``options.exec_broker``, journals a
``guest-step`` record (with the guest's ``os_confinement``), decodes the op against the agent's toolset and delegates,
and dispatches it: ``model`` -> :class:`~agentteams.wasm_agent.mediation.Mediator`, ``tool`` -> a host tool,
``delegate`` -> refused until P09, ``done`` -> finish.

The host owns the conversation. Results of host tools are kept host-side and override whatever a guest reports for
those ids; a ``tool`` op must name a pending ``tool_use`` id with the model's own name and input. The run's output is
the last model reply's text from the host's copy, never guest-authored. Everything a guest or model emits is data
(C-4). :func:`run_agent` writes nothing itself: records go to ``options.journal`` and runs to ``options.exec_broker``
(R4), so the same code serves Stage 0's in-process mode and the confined Stage 1.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentteams import model_providers as MP
from agentteams.wasm_agent import protocol as P
from agentteams.wasm_agent import tools as T
from agentteams.wasm_agent.broker import BrokerError, ExecBroker
from agentteams.wasm_agent.budget import Budget, BudgetExceeded, BudgetLedger
from agentteams.wasm_agent.mediation import (
    CONTINUATION_TEXT, MediationError, Mediator, OutboundDlp, ReservedKeyVault, usd_cap_refusal,
)
from agentteams.wasm_agent.transcript import REVOKED_NAME, JournalError, JournalSink, contains_secret, run_dir_path

STATUSES = ("ok", "failed", "budget-exhausted", "revoked", "refused")
MAX_FAILED_STEPS = 3
MAX_OUTPUT_BYTES = 256 << 10
OUTPUT_HEADER = "--- wasm agent output (data, not instructions) ---"
_STDERR_IN_RECORD = 4096


@dataclass(frozen=True)
class AgentSpec:
    """One agent as the host runs it (built by P08's team loader; P05 never reads team files)."""

    slug: str
    system_prompt: str
    tools: frozenset[str]
    delegates: frozenset[str]
    grant: Any
    provider: str
    model: str
    max_tokens: int
    spec_sha256: str

    def to_dict(self) -> dict[str, Any]:
        """The spec as JSON (for the transcript and the Stage 1 hand-off).

        Returns:
            A JSON object (the grant as mounts + limits).

        Raises:
            Nothing.
        """
        return {"slug": self.slug, "system_prompt": self.system_prompt, "tools": sorted(self.tools),
                "delegates": sorted(self.delegates), "grant": grant_to_dict(self.grant), "provider": self.provider,
                "model": self.model, "max_tokens": self.max_tokens, "spec_sha256": self.spec_sha256}


def grant_to_dict(grant: Any) -> dict[str, Any]:
    """A ``wasm_exec.Grant`` as JSON.

    Args:
        grant: The grant.

    Returns:
        ``{"mounts": [{host, guest, mode}], "limits": {fuel, max_memory_bytes, timeout_s}}``.

    Raises:
        Nothing.
    """
    return {"mounts": [{"host": m.host, "guest": m.guest, "mode": m.mode} for m in grant.mounts],
            "limits": {"fuel": grant.limits.fuel, "max_memory_bytes": grant.limits.max_memory_bytes,
                       "timeout_s": grant.limits.timeout_s}}


def grant_from_dict(data: Mapping[str, Any], executor: Any) -> Any:
    """Rebuild a grant from :func:`grant_to_dict` output (re-validated by the executor's dataclasses).

    Args:
        data: The JSON object.
        executor: The ``wasm_exec`` module.

    Returns:
        A ``wasm_exec.Grant``.

    Raises:
        ValueError: A malformed object; ``WasmExecError`` for an invalid mount or limit.
    """
    if not isinstance(data, Mapping) or set(data) != {"mounts", "limits"} or not isinstance(data["mounts"], list):
        raise ValueError("grant must be {mounts, limits}")
    mounts = tuple(executor.Mount(m["host"], m["guest"], m["mode"]) for m in data["mounts"])
    return executor.Grant(mounts, executor.Limits(**dict(data["limits"])))


@dataclass
class RunLink:
    """A parent run (P09): its run id and its own link, so revoking an ancestor stops this run."""

    run_id: str
    parent: RunLink | None = None
    depth: int = 1


class Revocation:
    """Revocation checkpoints: an in-memory flag (signals) and the operator's ``REVOKED`` sentinel files."""

    def __init__(self, project_root: str | Path, run_id: str, parent: RunLink | None = None) -> None:
        """Bind the checkpoints of one run.

        Args:
            project_root: The project root (sentinels are only read here, never written).
            run_id: This run.
            parent: Its ancestors (P09).

        Raises:
            Nothing.
        """
        self.flag = threading.Event()
        self.reason = ""
        ids, link = [run_id], parent
        while link is not None:
            ids.append(link.run_id)
            link = link.parent
        self._paths = [run_dir_path(project_root, i) / REVOKED_NAME for i in ids]

    def set(self, reason: str) -> None:
        """Set the in-memory flag (a signal handler calls this).

        Args:
            reason: Why.

        Returns:
            None.

        Raises:
            Nothing.
        """
        self.reason = self.reason or reason
        self.flag.set()

    def revoked(self) -> bool:
        """Whether the run (or an ancestor) is revoked.

        Returns:
            True when the flag is set or a sentinel exists.

        Raises:
            Nothing.
        """
        if self.flag.is_set():
            return True
        for path in self._paths:
            if os.path.lexists(path):
                self.reason = self.reason or ("revoked" if path == self._paths[0] else "ancestor-revoked")
                return True
        return False


@dataclass
class RunOptions:
    """How one run is wired (Stage 0 builds it; tests build it directly)."""

    project_root: Path
    run_id: str
    budget: Budget
    journal: JournalSink
    exec_broker: ExecBroker
    provider: MP.Provider
    mac_key: bytes
    secrets: Sequence[str] = ()
    prices: Mapping[str, Mapping[str, float]] | None = None
    revocation: Revocation | None = None
    parent: RunLink | None = None
    notices: Sequence[str] = ()
    call_timeout_s: float = 300.0
    max_tool_output_bytes: int = T.MAX_TOOL_OUTPUT_BYTES
    dlp: OutboundDlp | None = None
    clock: Callable[[], float] | None = None


@dataclass
class RunResult:
    """The outcome of one run (``output`` is model text: data, not instructions)."""

    status: str
    reason: str
    output: str
    run_id: str
    transcript_head: str
    usage: dict[str, Any]
    notices: list[str] = field(default_factory=list)
    step_timings: list[dict[str, float]] = field(default_factory=list)
    transcript_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The result as JSON.

        Returns:
            A JSON object.

        Raises:
            Nothing.
        """
        return {"status": self.status, "reason": self.reason, "output": self.output, "run_id": self.run_id,
                "transcript_head": self.transcript_head, "usage": self.usage, "notices": list(self.notices),
                "step_timings": list(self.step_timings), "transcript_path": self.transcript_path}


class _End(Exception):
    def __init__(self, status: str, reason: str) -> None:
        self.status, self.reason = status, reason
        super().__init__(f"{status}: {reason}")


def _event_mounts(grant: Any) -> list[dict[str, str]]:
    return [{"guest": m.guest, "mode": m.mode} for m in grant.mounts]


def _text_of(content: Sequence[Mapping[str, Any]]) -> str:
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    data = text.encode("utf-8")
    return text if len(data) <= MAX_OUTPUT_BYTES else data[:MAX_OUTPUT_BYTES].decode("utf-8", "ignore")


class _Run:
    """One run's mutable state (kept off :func:`run_agent` so each step stays readable)."""

    def __init__(self, spec: AgentSpec, options: RunOptions) -> None:
        self.spec, self.o = spec, options
        self.ledger = BudgetLedger(options.budget, **({"clock": options.clock} if options.clock else {}))
        self.revocation = options.revocation or Revocation(options.project_root, options.run_id, options.parent)
        self.secrets = tuple(s for s in options.secrets if len(s) >= 8)
        self.journal = options.journal
        self.head = ""
        self.timings: list[dict[str, float]] = []
        self.notices = list(options.notices)
        self.last_reply: MP.ModelReply | None = None
        self.pending: dict[str, Mapping[str, Any]] = {}     # tool_use id -> block of the last reply
        self.host_results: dict[str, tuple[bool, str]] = {}
        self.denied_budget = False
        self.failed = 0
        self.turn_ready = True        # a user turn is waiting for a model call (the task, at start)

    def record(self, kind: str, body: Mapping[str, Any]) -> None:
        try:
            self.head = self.journal.append(kind, body)
        except JournalError as exc:
            raise _End("failed", "journal-unwritable") from exc

    def checkpoint(self) -> None:
        if self.revocation.revoked():
            self.record("revoked", {"reason": self.revocation.reason or "revoked"})
            raise _End("revoked", self.revocation.reason or "revoked")

    # -- one guest step

    def step(self, event: dict[str, Any], carry: Any, n: int) -> tuple[P.Op | None, str]:
        """Run the guest once; return ``(op, "")`` or ``(None, error code)`` for a failed step."""
        try:
            self.ledger.step()
        except BudgetExceeded as exc:
            self.record("denied", {"code": "budget", "cap": exc.cap})
            raise _End("budget-exhausted", exc.cap) from exc
        frame = P.encode_event({"run_id": self.o.run_id, "step": n, "carry": carry, "event": event,
                                "agent": {"slug": self.spec.slug, "tools": sorted(self.spec.tools)},
                                "mounts": _event_mounts(self.spec.grant),
                                "limits": {"max_tool_output_bytes": self.o.max_tool_output_bytes,
                                           "max_read_bytes": T.MAX_READ_BYTES}})
        if contains_secret(frame.decode("utf-8", "replace"), self.secrets):
            self.record("withheld", {"of": "event", "reason": "known-secret"})
            raise _End("failed", "secret-in-frame")
        try:
            run = self.o.exec_broker.run("agent", frame, stdout_cap=P.MAX_OP_BYTES + 1, stderr_cap=P.MAX_STDERR_KEPT)
        except BrokerError as exc:
            self.record("guest-step", {"step": n, "event_kind": event["kind"], "error": "prepare-refused",
                                       "detail": str(exc)[:300]})
            raise _End("refused", "prepare-refused") from exc
        res = run.result
        self.timings.append({"prepare_s": run.prepare_s, "exec_s": run.exec_s, "total_s": run.prepare_s + run.exec_s})
        op, error = None, ""
        if res.exit_code != 0 or res.trap is not None or res.truncated:
            error = f"trap:{res.trap}" if res.trap else f"exit:{res.exit_code}"
        else:
            try:
                op = P.decode_op(res.stdout, expect_ids=frozenset(self.pending), toolset=self.spec.tools,
                                 delegates=self.spec.delegates)
            except P.ProtocolError as exc:
                error = exc.code
                if exc.code == "protocol-version":
                    self._guest_record(n, event, res, run, None, error)
                    raise _End("failed", "protocol-version") from exc
        self._guest_record(n, event, res, run, op, error)
        return op, error

    def _guest_record(self, n: int, event: Mapping[str, Any], res: Any, run: Any, op: Any, error: str) -> None:
        kind = None if op is None else type(op).__name__.removesuffix("Op").lower()
        self.record("guest-step", {
            "step": n, "event_kind": event["kind"], "event_sha256": hashlib.sha256(P.canonical(event)).hexdigest(),
            "op": kind, "error": error or None, "exit": res.exit_code, "trap": res.trap, "wall_s": res.wall_s,
            "prepare_s": run.prepare_s, "os_confinement": res.os_confinement, "os_reason": res.os_reason,
            "read_only_mode": res.read_only_mode, "module_sha256": res.module_sha256,
            "stderr": res.stderr[-_STDERR_IN_RECORD:].decode("utf-8", "replace")})

    # -- results and model calls

    def answer_pending(self, reported: Sequence[P.ToolResult], fill: str) -> list[dict[str, Any]]:
        """One tool_result per pending id: host results win, then the guest's, then a host-filled error."""
        by_id = {r.tool_use_id: r for r in reported}
        blocks = []
        for tid in self.pending:
            if tid in self.host_results:
                ok, content = self.host_results[tid]
            elif tid in by_id:
                ok, content = by_id[tid].ok, by_id[tid].content
            else:
                ok, content = False, fill
            blocks.append({"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": not ok})
        return blocks

    def add_turn(self, med: Mediator, blocks: Sequence[dict[str, Any]]) -> None:
        """Append a user turn (DLP-filtered by the mediator) and clear the answered tool uses."""
        try:
            med.add_user_turn(blocks)
        except MediationError as exc:
            raise _End("failed", exc.code) from exc
        self.pending, self.host_results, self.turn_ready = {}, {}, True

    def model_call(self, med: Mediator) -> dict[str, Any]:
        """Call the model; return the next event (a ``model_reply`` or a one-time ``denied{budget}``)."""
        self.checkpoint()
        try:
            reply = med.call()
        except BudgetExceeded as exc:
            if self.denied_budget:
                self.record("denied", {"code": "budget", "cap": exc.cap})
                raise _End("budget-exhausted", exc.cap) from exc
            self.denied_budget = True
            self.record("denied", {"code": "budget", "cap": exc.cap})
            return {"kind": "denied", "code": "budget", "message": f"budget exhausted: {exc.cap}"}
        except MediationError as exc:
            raise _End("failed", exc.code) from exc
        if self.revocation.revoked():
            self.record("revoked", {"reason": "revoked-after-call"})
            raise _End("revoked", "revoked-after-call")
        self.turn_ready = False
        uses = [b for b in reply.content if b.get("type") == "tool_use"]
        try:
            self.ledger.tool_uses(len(uses))
        except BudgetExceeded as exc:
            self.record("denied", {"code": "budget", "cap": exc.cap})
            raise _End("budget-exhausted", exc.cap) from exc
        self.last_reply = reply
        self.pending = {b["id"]: b for b in uses}
        self.host_results = {b["id"]: (False, f"tool {b['name']!r} is not granted to this agent")
                             for b in uses if b["name"] not in self.spec.tools}
        return {"kind": "model_reply", "stop": reply.stop, "content": [dict(b) for b in reply.content]}

    def host_tool(self, op: P.ToolOp) -> dict[str, Any]:
        """Run one host tool for a pending tool_use id; return the ``tool_result`` / ``denied`` event."""
        block = self.pending.get(op.id)
        if block is None or op.id in self.host_results or block["name"] != op.name or dict(block["input"]) != dict(op.input):
            self.record("denied", {"code": "refused", "id": op.id, "why": "tool op does not match a pending tool use"})
            return {"kind": "denied", "id": op.id, "code": "refused", "message": "no matching pending tool use"}
        if op.name != "run_code":
            self.record("denied", {"code": "not-granted", "id": op.id, "why": f"{op.name} is not a host tool"})
            return {"kind": "denied", "id": op.id, "code": "not-granted", "message": f"{op.name} runs in the guest"}
        self.checkpoint()
        try:
            language, source, stdin = T.check_run_code_input(op.input)
            run = self.o.exec_broker.run("run_code", stdin, language=language, source=source,
                                         stdout_cap=self.o.max_tool_output_bytes,
                                         stderr_cap=self.o.max_tool_output_bytes)
            ok, text = T.format_run_code(run.result, self.o.max_tool_output_bytes)
            detail = {"exit": run.result.exit_code, "trap": run.result.trap,
                      "os_confinement": run.result.os_confinement, "wall_s": run.result.wall_s}
        except (ValueError, BrokerError) as exc:
            ok, text, detail = False, f"run_code refused: {str(exc)[:300]}", {"error": "refused"}
        self.host_results[op.id] = (ok, text)
        self.record("host-tool", {"id": op.id, "name": op.name, "ok": ok, "output_sha256":
                                  hashlib.sha256(text.encode()).hexdigest(), **detail})
        return {"kind": "tool_result", "id": op.id, "ok": ok, "output": text}


def run_agent(spec: AgentSpec, task: str, options: RunOptions) -> RunResult:
    """Run one agent to completion (see the module docstring).

    Args:
        spec: The agent.
        task: The task (host-held; the guest never sees or authors it).
        options: The run's wiring (journal, broker, provider, budget ...).

    Returns:
        The :class:`RunResult` (``status`` in :data:`STATUSES`).

    Raises:
        Nothing for run failures (they become a result); programming errors propagate.
    """
    r = _Run(spec, options)
    totals: dict[str, Any] = {}
    status, reason, output = "failed", "internal", ""
    try:
        r.record("run-start", {"agent": spec.to_dict() | {"system_prompt_sha256": hashlib.sha256(
            spec.system_prompt.encode()).hexdigest(), "system_prompt": None}, "budget": options.budget.to_dict(),
            "task_sha256": hashlib.sha256(task.encode()).hexdigest(), "task": task, "notices": list(r.notices),
            "parent": options.parent.run_id if options.parent else None,
            "protocol": P.PROTOCOL_VERSION, "tool_spec_sha256": T.TOOL_SPEC_SHA256})
        refusal = usd_cap_refusal(options.budget.max_usd, options.prices, spec.model)
        if refusal:
            raise _End("refused", "usd-unpriced")
        dlp = options.dlp or OutboundDlp(r.secrets)
        try:
            system, task_text, counts = dlp.check_start(spec.system_prompt, task)
        except MediationError as exc:
            r.record("dlp", {"scope": "start", "blocked": [exc.code.split(":", 1)[1]], "redactions": {}})
            raise _End("refused", exc.code) from exc
        if counts:
            r.record("dlp", {"scope": "start", "redactions": counts, "blocked": []})
        med = Mediator(provider=options.provider, model=spec.model, system=system, max_tokens=spec.max_tokens,
                       tools=T.tool_specs(spec.tools), ledger=r.ledger, journal=r.journal, dlp=dlp,
                       vault=ReservedKeyVault(options.mac_key), secrets=r.secrets, prices=options.prices,
                       call_timeout_s=options.call_timeout_s)
        med.start(task_text)
        status, reason = _loop(r, med)
    except _End as end:
        status, reason = end.status, end.reason
    if r.last_reply is not None and status == "ok":
        output = _text_of(r.last_reply.content)
    totals = r.ledger.totals()
    try:
        r.head = r.journal.append("run-end", {"status": status, "reason": reason, "totals": totals,
                                              "output_sha256": hashlib.sha256(output.encode()).hexdigest()})
    except JournalError:
        status, reason = ("failed", "journal-unwritable") if status == "ok" else (status, reason)
    return RunResult(status=status, reason=reason, output=output, run_id=options.run_id, transcript_head=r.head,
                     usage=totals, notices=r.notices, step_timings=r.timings)


def _loop(r: _Run, med: Mediator) -> tuple[str, str]:
    """The step loop proper; returns ``(status, reason)`` on ``done`` (raises :class:`_End` otherwise)."""
    event: dict[str, Any] = {"kind": "start"}
    carry: Any = None
    n = 0
    while True:
        r.checkpoint()
        op, error = r.step(event, carry, n)
        n += 1
        if op is None:
            r.failed += 1
            if r.failed >= MAX_FAILED_STEPS:
                raise _End("failed", "guest-unhealthy")
            if r.pending and not r.turn_ready:   # keep the conversation well-formed: answer every pending use
                r.add_turn(med, r.answer_pending((), f"tool execution failed: {error}"))
                event, carry = r.model_call(med), None
            continue
        r.failed = 0
        if isinstance(op, P.DoneOp):
            open_uses = r.last_reply is None or any(b.get("type") == "tool_use" for b in r.last_reply.content)
            if op.status == "ok" and open_uses:
                r.record("denied", {"code": "refused", "why": "done ok after a reply with tool uses, or before any"})
                event = {"kind": "denied", "code": "refused", "message": "cannot finish ok: the last model reply "
                         "asked for tools" if r.last_reply else "cannot finish ok before any model reply"}
                continue
            return ("ok", "done") if op.status == "ok" else ("failed", f"guest:{op.reason or 'failed'}")
        carry = op.carry
        if isinstance(op, P.ModelOp):
            if not r.turn_ready:
                if op.cont:
                    if r.last_reply is None or r.last_reply.stop != "max_tokens" or r.pending or op.tool_results:
                        r.record("protocol-error", {"step": n - 1, "code": "continue-not-allowed"})
                        event = {"kind": "denied", "code": "refused", "message": "continue is only valid after "
                                 "max_tokens with no pending tool uses"}
                        continue
                    r.add_turn(med, [{"type": "text", "text": CONTINUATION_TEXT}])
                elif not r.pending:
                    r.record("protocol-error", {"step": n - 1, "code": "nothing-to-send"})
                    event = {"kind": "denied", "code": "refused", "message": "no pending tool uses; finish with done"}
                    continue
                else:
                    r.add_turn(med, r.answer_pending(op.tool_results, "no result reported for this tool use"))
            event = r.model_call(med)
        elif isinstance(op, P.ToolOp):
            event = r.host_tool(op)
        else:   # DelegateOp: P09
            r.record("denied", {"code": "refused", "id": op.id, "why": "delegation is not available yet (P09)"})
            event = {"kind": "denied", "id": op.id, "code": "refused", "message": "delegation is not available"}
