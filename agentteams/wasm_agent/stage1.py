"""stage1.py — the confined WASM agent host process (P05 §4.9; started only by ``launch.stage0``).

Stage 1 runs inside the host OS sandbox. It reads its bootstrap (provider, key, reserved-key MAC key, the agent spec,
task, budget and known secrets) **once** from the key pipe and closes it, builds the P06 provider, and runs the same
host loop as Stage 0's in-process mode, with :class:`~agentteams.wasm_agent.transcript.PipeJournal` and
:class:`~agentteams.wasm_agent.broker.PipeBroker` over the control pipe. It writes nothing under ``.agentteams/``
(R4): every record and every prepared guest directory is Stage 0's.

Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import signal
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agentteams import model_providers as MP
from agentteams import wasm_exec as W
import agentteams.wasm_agent.protocol as P
from agentteams.wasm_agent.broker import PipeBroker
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.host import AgentSpec, Revocation, RunOptions, grant_from_dict, run_agent
from agentteams.wasm_agent.transcript import PipeJournal

_BOOT_KEYS = {"v", "provider", "key", "base_url", "stub_script", "mac_key", "run_id", "project_root", "spec", "task",
              "budget", "secrets", "prices", "notices", "call_timeout_s"}


class ControlClient:
    """Stage 1's end of the control pipe: one request, one reply, strictly in turn."""

    def __init__(self, rfd: int, wfd: int) -> None:
        """Bind the pipe ends.

        Args:
            rfd: Reads Stage 0's replies.
            wfd: Writes requests to Stage 0.

        Raises:
            Nothing.
        """
        self._reader, self._wfd = P.LineReader(rfd), wfd

    def call(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send one request and wait for its reply.

        Args:
            message: The request.

        Returns:
            Stage 0's reply.

        Raises:
            OSError: The pipe closed.
            ProtocolError: A malformed reply or end of file.
        """
        P.write_line(self._wfd, message)
        reply = self._reader.read()
        if reply is None:
            raise P.ProtocolError("frame", "Stage 0 closed the control pipe")
        return reply


def spec_from_dict(data: dict[str, Any]) -> AgentSpec:
    """Rebuild the agent spec Stage 0 handed over.

    Args:
        data: ``AgentSpec.to_dict()`` output.

    Returns:
        The spec (grant re-validated by the executor).

    Raises:
        ValueError: A malformed spec (``WasmExecError`` for a bad grant).
    """
    keys = {"slug", "system_prompt", "tools", "delegates", "grant", "provider", "model", "max_tokens", "spec_sha256"}
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError("malformed agent spec")
    return AgentSpec(slug=str(data["slug"]), system_prompt=str(data["system_prompt"]), tools=frozenset(data["tools"]),
                     delegates=frozenset(data["delegates"]), grant=grant_from_dict(data["grant"], W),
                     provider=str(data["provider"]), model=str(data["model"]), max_tokens=int(data["max_tokens"]),
                     spec_sha256=str(data["spec_sha256"]))


def _provider(boot: dict[str, Any]) -> MP.Provider:
    if boot["stub_script"] is not None:
        if boot["provider"] != "stub":
            raise MP.ModelProviderError("config", "stub_script is only valid for the stub provider")
        return MP.Provider(MP.PROVIDERS["stub"], None, "", MP.StubProvider(boot["stub_script"]))
    return MP.Provider.from_key(boot["provider"], boot["key"], base_url=boot["base_url"])


def main(argv: Sequence[str]) -> int:
    """Stage 1 entry point.

    Args:
        argv: ``--key-fd N --ctl-in N --ctl-out N``.

    Returns:
        0 when a result was delivered to Stage 0, 2 otherwise.

    Raises:
        Nothing.
    """
    ap = argparse.ArgumentParser(prog="agentteams-wasm-host-stage1")
    for flag in ("--key-fd", "--ctl-in", "--ctl-out"):
        ap.add_argument(flag, type=int, required=True)
    args = ap.parse_args(list(argv))
    try:
        boot = P.read_line(args.key_fd)
    finally:
        os.close(args.key_fd)
    if not isinstance(boot, dict) or set(boot) != _BOOT_KEYS or boot.get("v") != 1:
        return 2
    control = ControlClient(args.ctl_in, args.ctl_out)
    run_id, root = str(boot["run_id"]), Path(str(boot["project_root"]))
    revocation = Revocation(root, run_id)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, lambda signum, frame: revocation.set(f"signal:{signum}"))
    try:
        spec = spec_from_dict(boot["spec"])
        provider = _provider(boot)
        options = RunOptions(project_root=root, run_id=run_id, budget=Budget.from_dict(boot["budget"]),
                             journal=PipeJournal(control), exec_broker=PipeBroker(control, W), provider=provider,
                             mac_key=bytes.fromhex(boot["mac_key"]), secrets=list(boot["secrets"]),
                             prices=boot["prices"], revocation=revocation, notices=list(boot["notices"]),
                             call_timeout_s=float(boot["call_timeout_s"]))
        task = str(boot["task"])
    except (ValueError, TypeError, KeyError, W.WasmExecError, MP.ModelProviderError):
        return 2
    finally:
        boot.clear()
    result = run_agent(spec, task, options)
    try:
        control.call({"op": "result", "result": result.to_dict()})
    except (OSError, P.ProtocolError):
        return 2
    return 0
