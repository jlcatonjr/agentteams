"""Shared helpers for the WASM delegation tests (WASM P09).

``GuestPreparer`` stands in for ``broker.StepPreparer`` (tests only; production has no such class): every agent step
runs the **real pinned guest program** (``agentteams/data/wasm_agent_guest.py``) as a native, isolated Python process,
and ``run_code`` runs a fixed native program. Each instance records the grant Stage 0 handed it, so a test can assert
the grant every child step ran under. The live wasmtime path is ``tests/test_wasm_delegation_e2e.py``.

``Team`` is a minimal stand-in for P08's lock-verified team: ``agent_spec(slug)`` returns declared specs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from agentteams import model_providers as MP
from agentteams import wasm_exec as W
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.guest_source import guest_path
from agentteams.wasm_agent.host import AgentSpec
from tests._wasm_agent_helpers import text_reply, tool_reply
from tests._wasm_helpers import FakeResolver as _FakeWasmtime
from tests.test_wasm_agent_launch import _fake_profiles

__all__ = ["text_reply", "tool_reply"]

LIMITS = W.Limits(fuel=7_000_000_000, max_memory_bytes=64 << 20, timeout_s=30)


class GuestPreparer:
    """Test-only preparer: agent steps run the pinned guest natively; records its grant (see the module docstring)."""

    instances: list[GuestPreparer] = []

    def __init__(self, **kw: Any) -> None:
        if hashlib.sha256(kw["guest_source"]).hexdigest() != kw["guest_sha256"]:
            from agentteams.wasm_agent.broker import BrokerError
            raise BrokerError("the agent guest source does not match its pinned sha256")
        self.grant, self.executor = kw["grant"], W
        self.prepared: list[str] = []
        GuestPreparer.instances.append(self)

    def prepare(self, purpose: str, *, language: str | None = None, source: str | None = None) -> tuple:
        self.prepared.append(purpose)
        argv = ([sys.executable, "-I", "-B", str(guest_path())] if purpose == "agent"
                else [sys.executable, "-I", "-c", "print(42)"])
        launch = W.Launch(argv=argv, env={}, cwd="/", run_dir=None, cleanup=lambda: None, os_confinement="none",
                          os_reason="native-test", read_only_mode="none", timeout_s=30, module_sha256="0" * 64)
        return f"t{len(self.prepared)}", launch, 0.0

    def release(self, ticket: str) -> None:
        pass

    def release_all(self) -> None:
        pass


class Resolver(_FakeWasmtime):
    """Pins check: resolves ``wasmtime`` and ``python`` to the running interpreter (never executed as wasm here)."""

    def __init__(self) -> None:
        self.path, self.version, self.calls = sys.executable, "0", 0

    def resolve(self, component: str) -> Path:
        return Path(sys.executable)


@pytest.fixture
def native_guests(monkeypatch: pytest.MonkeyPatch) -> list[GuestPreparer]:
    """Route Stage 0's preparers (root and children) to :class:`GuestPreparer`; return the instance list."""
    GuestPreparer.instances = []
    monkeypatch.setattr(L, "StepPreparer", GuestPreparer)
    return GuestPreparer.instances


def project(tmp_path: Path) -> Path:
    """A real-path project root with ``docs/`` and ``secret/``."""
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "docs" / "sub").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("granted doc\n")
    (root / "docsx").mkdir()
    (root / "secret").mkdir()
    (root / "secret" / "s.txt").write_text("not granted\n")
    return root


def scratch(root: Path, slug: str) -> str:
    return str(root / ".agentteams" / "wasm" / "scratch" / slug)


def grant(root: Path, *ro: str, scratch_of: str | None = None, limits: W.Limits = LIMITS) -> Any:
    """A grant: ``/project/<rel>`` ro for each *ro*, plus ``/scratch`` at *scratch_of*'s own dir."""
    mounts = [W.Mount(str(root / rel), f"/project/{rel}", "ro") for rel in ro]
    if scratch_of:
        mounts.append(W.Mount(scratch(root, scratch_of), "/scratch", "rw"))
    return W.Grant(tuple(mounts), limits)


def agent(slug: str, g: Any, *, tools: frozenset[str] | None = None, delegates: tuple[str, ...] = ()) -> AgentSpec:
    """A declared agent spec (the prompt names the slug so a scripted model can route on it)."""
    t = tools if tools is not None else frozenset({"read_file", "run_code", *(("delegate",) if delegates else ())})
    return AgentSpec(slug, f"You are {slug}.", t, frozenset(delegates), g, "stub", "stub", 512, "0" * 64)


class Team:
    """``agent_spec(slug)`` over a dict of declared specs (P08 ``Team`` stand-in)."""

    def __init__(self, *specs: AgentSpec) -> None:
        self.specs = {s.slug: s for s in specs}
        self.asked: list[str] = []

    def agent_spec(self, slug: str) -> AgentSpec:
        from agentteams.wasm_agent.team import TeamError
        self.asked.append(slug)
        if slug not in self.specs:
            raise TeamError(f"no agent {slug!r}")
        return self.specs[slug]


def routed(replies: Mapping[str, list[Any]]) -> Callable[..., Any]:
    """A stub-provider script that answers each agent (found by its system prompt) from its own reply list."""
    queues = {k: list(v) for k, v in replies.items()}
    seen: list[tuple[str, list[Any], Any]] = []

    def script(messages: list, system: Any, tools: Any) -> dict:
        text = str(system)
        who = text[text.index("You are ") + 8:].split(".", 1)[0] if "You are " in text else "?"
        queues.setdefault(who, [])
        seen.append((who, messages, tools))
        item = queues[who].pop(0) if queues[who] else text_reply(f"{who} idle")
        return item(messages) if callable(item) else item
    script.seen = seen  # type: ignore[attr-defined]
    return script


def run(root: Path, team: Team, root_slug: str, script: Any, *, budget: Budget | None = None,
        delegation: Mapping[str, Any] | None = None, monkeypatch: pytest.MonkeyPatch | None = None,
        **kw: Any) -> Any:
    """Run *root_slug* through Stage 0 (in-process unless *profiles* says otherwise; pinned guest via
    :class:`GuestPreparer`) with delegation on."""
    if callable(script) and monkeypatch is not None:
        monkeypatch.setattr(L, "build_provider", lambda *a, **k: MP.Provider(MP.PROVIDERS["stub"], None, "",
                                                                             MP.StubProvider(script)))
        stub_script: Any = []
    else:
        stub_script = script
    return L.stage0(team.agent_spec(root_slug), kw.pop("task", "Do the task."), project_root=root,
                    budget=budget or Budget(), resolver=Resolver(), profiles=kw.pop("profiles", _fake_profiles(None)),
                    stub_script=stub_script, resolve_agent=team.agent_spec, delegation=delegation, **kw)


def records(path: str | Path) -> list[dict[str, Any]]:
    """Every record of a transcript."""
    return [json.loads(x) for x in Path(path).read_text().splitlines()]


def run_records(root: Path, run_id: str) -> list[dict[str, Any]]:
    """The transcript records of *run_id*."""
    return records(root / ".agentteams" / "wasm-runs" / run_id / "transcript.jsonl")


def child_ids(recs: list[dict[str, Any]]) -> list[str]:
    """The child run ids a parent's transcript started."""
    return [r["child_run_id"] for r in recs if r["kind"] == "delegate-start"]
