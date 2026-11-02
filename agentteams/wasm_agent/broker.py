"""broker.py — how the WASM agent host gets a guest run prepared and executed (P05 §4.9, METAPLAN R3/R3′/R4).

Preparing a guest run writes under ``<project>/.agentteams/wasm/exec/<id>/`` (verified copies, the private ``/src``
file, read-only snapshots), and the confined host (Stage 1) may never write ``.agentteams/`` (R4). So preparing is
split from executing:

* :class:`StepPreparer` lives in **Stage 0** (unconfined). It holds the agent's grant, the pinned guest source, the
  resolver and the guest ``os_confine`` mode, and turns a request (``agent`` step or ``run_code``) into a
  ``wasm_exec.Launch`` via ``wasm_guests.argv_for`` + ``wasm_exec.materialize``. The requester never supplies a grant,
  so a compromised Stage 1 cannot widen one (C-3).
* :class:`LocalBroker` (Stage 0 in-process mode) and :class:`PipeBroker` (Stage 1, asking Stage 0 over the control
  pipe) both execute the returned launch with ``wasm_exec.execute_launch`` (the same stdin/cap checks and bounded
  runner as ``wasm_exec.run``) and release it afterwards.

There is no test-only broker in this module: tests implement the :class:`ExecBroker` protocol themselves.

Stdlib only.
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets as _secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from agentteams.wasm_agent.protocol import ProtocolError
from agentteams.wasm_agent.tools import RUN_CODE_LANGUAGES

#: The guest ``os_confine`` modes Stage 0 may use (``inherited`` only when forwarded from the P08 launcher).
GUEST_OS_CONFINE = ("auto", "require", "inherited", "off")
_LAUNCH_KEYS = ("argv", "env", "cwd", "timeout_s", "os_confinement", "os_reason", "read_only_mode", "module_sha256")


class BrokerError(Exception):
    """A guest run could not be prepared (exec dir unwritable, snapshot over its cap, runtime unresolvable ...)."""


@dataclass(frozen=True)
class GuestRun:
    """One executed guest: the executor's result plus the Stage-0 prepare time (R13 timing)."""

    result: Any
    prepare_s: float
    exec_s: float


class ExecBroker(Protocol):
    """What the host loop needs to run guests (:class:`LocalBroker`, :class:`PipeBroker`)."""

    def run(self, purpose: str, stdin: bytes, *, language: str | None = None, source: str | None = None,
            stdout_cap: int, stderr_cap: int) -> GuestRun:
        """Prepare, execute and release one guest run."""


class StepPreparer:
    """Stage 0's side: turns run requests into launches under the agent's own grant (see the module docstring)."""

    def __init__(self, *, project_root: str | Path, grant: Any, guest_source: bytes, guest_sha256: str,
                 resolver: Any, executor: ModuleType, guests: ModuleType, profiles: ModuleType,
                 os_confine: str, control_plane_protected: bool = False) -> None:
        """Hold everything a prepare needs; verify the guest source against its pin now.

        Args:
            project_root: The project root.
            grant: The agent's ``wasm_exec.Grant``.
            guest_source: The agent-loop guest program (P07; a test guest in tests).
            guest_sha256: Its pinned sha256.
            resolver: Provisioning (``wasm_runtime.Resolver`` or ``wasm_pins.PinnedResolver``).
            executor: The ``wasm_exec`` module.
            guests: The ``wasm_guests`` module.
            profiles: The ``_sandbox_profiles`` module.
            os_confine: The guest mode, already decided by Stage 0 (one of :data:`GUEST_OS_CONFINE`).
            control_plane_protected: R3′: run pinned binaries in place (only when R1 protects ``.agentteams/``).

        Raises:
            BrokerError: A guest source that does not match its pin, a bad mode, or a non-Grant grant.
        """
        if hashlib.sha256(guest_source).hexdigest() != guest_sha256:
            raise BrokerError("the agent guest source does not match its pinned sha256")
        if os_confine not in GUEST_OS_CONFINE:
            raise BrokerError(f"guest os_confine {os_confine!r} is not one of {GUEST_OS_CONFINE}")
        if not isinstance(grant, executor.Grant):
            raise BrokerError("grant must be a wasm_exec.Grant")
        self.root, self.grant = str(project_root), grant
        self._source, self.guest_sha256 = guest_source, guest_sha256
        self.resolver, self.executor, self.guests, self.profiles = resolver, executor, guests, profiles
        self.os_confine, self.control_plane_protected = os_confine, control_plane_protected
        self._launches: dict[str, Any] = {}

    def prepare(self, purpose: str, *, language: str | None = None, source: str | None = None
                ) -> tuple[str, Any, float]:
        """Prepare one run.

        Args:
            purpose: ``"agent"`` (the pinned guest, no source accepted) or ``"run_code"``.
            language: ``run_code`` only: one of ``tools.RUN_CODE_LANGUAGES``.
            source: ``run_code`` only: the model-authored program.

        Returns:
            ``(ticket, wasm_exec.Launch, seconds spent)``.

        Raises:
            BrokerError: On any refusal (the executor's and the guest dispatcher's included).
        """
        started = time.monotonic()
        if purpose == "agent":
            if language is not None or source is not None:
                raise BrokerError("an agent step takes no language or source (the guest is pinned)")
            if hashlib.sha256(self._source).hexdigest() != self.guest_sha256:
                raise BrokerError("the agent guest source changed in memory")
            language, src = "python", self._source
        elif purpose == "run_code":
            if language not in RUN_CODE_LANGUAGES or not isinstance(source, str):
                raise BrokerError(f"run_code needs a language in {RUN_CODE_LANGUAGES} and a string source")
            src = source
        else:
            raise BrokerError(f"unknown prepare purpose {str(purpose)[:40]!r}")
        try:
            prepared, args = self.guests.argv_for(language, src, None, grant=self.grant, project_root=self.root,
                                                  resolver=self.resolver, executor=self.executor)
            launch = self.executor.materialize(prepared, args, project_root=self.root, os_confine=self.os_confine,
                                               resolver=self.resolver, profiles=self.profiles,
                                               control_plane_protected=self.control_plane_protected)
        except (self.guests.WasmGuestError, self.executor.WasmExecError) as exc:
            raise BrokerError(str(exc)) from exc
        ticket = _secrets.token_hex(8)
        self._launches[ticket] = launch
        return ticket, launch, round(time.monotonic() - started, 4)

    def release(self, ticket: str) -> None:
        """Remove one run's exec directory.

        Args:
            ticket: From :meth:`prepare`.

        Returns:
            None.

        Raises:
            Nothing (an unknown ticket is ignored).
        """
        launch = self._launches.pop(ticket, None)
        if launch is not None:
            launch.cleanup()

    def release_all(self) -> None:
        """Remove every exec directory still held (run end).

        Returns:
            None.

        Raises:
            Nothing.
        """
        for ticket in list(self._launches):
            self.release(ticket)


def launch_to_dict(launch: Any) -> dict[str, Any]:
    """The JSON form of a launch sent to Stage 1 (no cleanup callable; Stage 0 keeps that).

    Args:
        launch: A ``wasm_exec.Launch``.

    Returns:
        ``{argv, env, cwd, timeout_s, os_confinement, os_reason, read_only_mode, module_sha256}``.

    Raises:
        Nothing.
    """
    return {k: getattr(launch, k) for k in _LAUNCH_KEYS}


def launch_from_dict(data: Mapping[str, Any], executor: ModuleType) -> Any:
    """Rebuild a launch from Stage 0's reply (Stage 1 side; ``cleanup`` is a no-op there).

    Args:
        data: :func:`launch_to_dict` output.
        executor: The ``wasm_exec`` module.

    Returns:
        A ``wasm_exec.Launch``.

    Raises:
        ProtocolError: ``shape`` for a malformed launch.
    """
    if not isinstance(data, Mapping) or set(data) != set(_LAUNCH_KEYS) or not isinstance(data["argv"], list) or \
            not all(isinstance(a, str) for a in data["argv"]) or not isinstance(data["env"], dict) or \
            data["os_confinement"] not in ("nested", "inherited", "none") or type(data["timeout_s"]) is not int:
        raise ProtocolError("shape", "malformed launch from Stage 0")
    return executor.Launch(argv=list(data["argv"]), env=dict(data["env"]), cwd=str(data["cwd"]), run_dir=None,
                           cleanup=lambda: None, os_confinement=data["os_confinement"], os_reason=str(data["os_reason"]),
                           read_only_mode=data["read_only_mode"], timeout_s=data["timeout_s"],
                           module_sha256=str(data["module_sha256"]))


class LocalBroker:
    """Stage 0 in-process mode: prepare with the :class:`StepPreparer` directly, execute, release."""

    def __init__(self, preparer: StepPreparer) -> None:
        """Bind the preparer.

        Args:
            preparer: Stage 0's preparer.

        Raises:
            Nothing.
        """
        self.preparer = preparer

    def run(self, purpose: str, stdin: bytes, *, language: str | None = None, source: str | None = None,
            stdout_cap: int, stderr_cap: int) -> GuestRun:
        """Prepare, execute and release one guest run.

        Args:
            purpose: ``agent`` or ``run_code``.
            stdin: Guest stdin.
            language: ``run_code`` only.
            source: ``run_code`` only.
            stdout_cap: Bytes kept from stdout.
            stderr_cap: Bytes kept from stderr.

        Returns:
            The :class:`GuestRun`.

        Raises:
            BrokerError: The prepare was refused or wasmtime could not start.
        """
        ticket, launch, prep = self.preparer.prepare(purpose, language=language, source=source)
        started = time.monotonic()
        try:
            res = self.preparer.executor.execute_launch(launch, stdin, stdout_cap=stdout_cap,
                                                        stderr_cap=stderr_cap)
        except self.preparer.executor.WasmExecError as exc:
            raise BrokerError(str(exc)) from exc
        finally:
            self.preparer.release(ticket)
        return GuestRun(res, prep, round(time.monotonic() - started, 4))


class PipeBroker:
    """Stage 1: ask Stage 0 to prepare over the control pipe, execute locally, ask it to release."""

    def __init__(self, control: Any, executor: ModuleType) -> None:
        """Bind the control client.

        Args:
            control: An object with ``call(message) -> reply``.
            executor: The ``wasm_exec`` module (for ``Launch`` / ``execute_launch`` only; Stage 1 never materializes).

        Raises:
            Nothing.
        """
        self._control, self.executor = control, executor

    def run(self, purpose: str, stdin: bytes, *, language: str | None = None, source: str | None = None,
            stdout_cap: int, stderr_cap: int) -> GuestRun:
        """As :meth:`LocalBroker.run`, with the prepare and release done by Stage 0.

        Args:
            purpose: ``agent`` or ``run_code``.
            stdin: Guest stdin.
            language: ``run_code`` only.
            source: ``run_code`` only.
            stdout_cap: Bytes kept from stdout.
            stderr_cap: Bytes kept from stderr.

        Returns:
            The :class:`GuestRun`.

        Raises:
            BrokerError: Stage 0 refused, the pipe failed, or wasmtime could not start.
        """
        started = time.monotonic()
        try:
            reply = self._control.call({"op": "prepare", "purpose": purpose, "language": language, "source": source})
        except (OSError, ProtocolError) as exc:
            raise BrokerError(f"prepare failed: {exc}") from exc
        if not reply.get("ok"):
            raise BrokerError(str(reply.get("error", "prepare refused"))[:300])
        try:
            launch = launch_from_dict(reply.get("launch"), self.executor)
        except ProtocolError as exc:
            raise BrokerError(str(exc)) from exc
        prep = round(time.monotonic() - started, 4)
        started = time.monotonic()
        try:
            res = self.executor.execute_launch(launch, stdin, stdout_cap=stdout_cap, stderr_cap=stderr_cap)
        except self.executor.WasmExecError as exc:
            raise BrokerError(str(exc)) from exc
        finally:   # a lost release is backstopped: Stage 0 releases every ticket at run end (release_all)
            with contextlib.suppress(OSError, ProtocolError):
                self._control.call({"op": "release", "ticket": str(reply.get("ticket", ""))})
        return GuestRun(res, prep, round(time.monotonic() - started, 4))
