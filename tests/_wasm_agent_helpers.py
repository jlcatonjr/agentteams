"""Shared helpers for the WASM agent host tests (P05).

``FnBroker`` implements the host's ``ExecBroker`` protocol in pure Python: an agent step calls a Python function on the
decoded event instead of running a WASM guest. It exists only here (tests); production code has no such broker.
``reference_guest`` mirrors ``tests/fixtures/wasm_agent/agent_guest.py`` over an in-memory file map.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agentteams import model_providers as MP
from agentteams import wasm_exec as W
from agentteams.wasm_agent.broker import GuestRun
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.host import AgentSpec, RunOptions
from agentteams.wasm_agent.transcript import FileJournal

GUEST = Path(__file__).with_name("fixtures") / "wasm_agent" / "agent_guest.py"
GUEST_SRC = GUEST.read_bytes()
GUEST_SHA = hashlib.sha256(GUEST_SRC).hexdigest()
KEY = "sk-ant-api03-" + "Zq7" * 12 + "-not-real"
RUN = "cd" * 16


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in these modules may reach the network."""
    def refuse(*a: object, **k: object) -> None:
        raise AssertionError("network access in a wasm agent unit test")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def result(stdout: bytes = b"", *, exit_code: int = 0, trap: str | None = None, stderr: bytes = b"",
           conf: str = "none") -> Any:
    """A WasmResult as the executor would return it."""
    return W.WasmResult(exit_code=exit_code, stdout=stdout, stderr=stderr, trap=trap, os_confinement=conf,
                        truncated=False, os_reason="test", read_only_mode="none", wall_s=0.01, module_sha256="0" * 64)


class FnBroker:
    """``ExecBroker`` for tests: agent steps call ``fn(frame) -> op dict | bytes | WasmResult``."""

    def __init__(self, fn: Callable[[dict[str, Any]], Any], code_fn: Callable[..., Any] | None = None) -> None:
        self.fn, self.code_fn = fn, code_fn
        self.frames: list[dict[str, Any]] = []
        self.raw: list[bytes] = []
        self.code_runs: list[tuple[str, str, bytes]] = []

    def run(self, purpose: str, stdin: bytes, *, language: str | None = None, source: str | None = None,
            stdout_cap: int, stderr_cap: int) -> GuestRun:
        if purpose == "run_code":
            self.code_runs.append((str(language), str(source), stdin))
            res = self.code_fn(language, source, stdin) if self.code_fn else result(b"42\n")
            return GuestRun(res, 0.0, 0.0)
        self.raw.append(stdin)
        frame = json.loads(stdin)
        self.frames.append(frame)
        out = self.fn(frame)
        if isinstance(out, W.WasmResult):
            return GuestRun(out, 0.0, 0.0)
        data = out if isinstance(out, bytes) else json.dumps(out).encode() + b"\n"
        return GuestRun(result(data), 0.0, 0.0)


def reference_guest(files: dict[str, str] | None = None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A Python stand-in for the test guest: in-guest read_file over *files*, host tools via tool ops."""
    files = files or {}

    def advance(carry: dict[str, Any]) -> dict[str, Any]:
        while carry["todo"]:
            use = carry["todo"][0]
            if use["name"] in ("read_file", "list_dir", "search_files", "write_file"):
                path = use["input"].get("path", "")
                ok = path in files
                carry["results"].append({"tool_use_id": use["id"], "ok": ok,
                                         "content": files[path] if ok else "not found"})
                carry["todo"].pop(0)
                continue
            return {"v": 1, "op": "tool", "id": use["id"], "name": use["name"], "input": use["input"],
                    "carry": carry}
        return {"v": 1, "op": "model", "tool_results": carry["results"], "continue": False, "carry": None}

    def fn(frame: dict[str, Any]) -> dict[str, Any]:
        ev = frame["event"]
        if ev["kind"] == "start":
            return {"v": 1, "op": "model", "tool_results": [], "continue": False, "carry": None}
        if ev["kind"] == "model_reply":
            uses = [b for b in ev["content"] if b["type"] == "tool_use"]
            if not uses:
                if ev["stop"] == "max_tokens":
                    return {"v": 1, "op": "model", "tool_results": [], "continue": True, "carry": None}
                return {"v": 1, "op": "done", "status": "ok"}
            return advance({"todo": uses, "results": []})
        if ev["kind"] == "tool_result":
            carry = frame["carry"]
            use = carry["todo"].pop(0)
            carry["results"].append({"tool_use_id": use["id"], "ok": ev["ok"], "content": "guest's view"})
            return advance(carry)
        return {"v": 1, "op": "done", "status": "failed", "reason": "denied-" + ev.get("code", "x")}

    return fn


def project(tmp_path: Path) -> Path:
    """A real-path project root."""
    root = Path(os.path.realpath(tmp_path)) / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("doc text\n")
    return root


def spec(root: Path, *, tools: frozenset[str] = frozenset({"read_file", "run_code"}), system: str = "You help.",
         provider: str = "stub", grant: Any = None) -> AgentSpec:
    """An agent spec with a docs read mount."""
    if grant is None:
        grant = W.Grant((W.Mount(str(root / "docs"), "/project/docs", "ro"),), W.DEFAULT_LIMITS)
    return AgentSpec(slug="primary-producer", system_prompt=system, tools=tools, delegates=frozenset(), grant=grant,
                     provider=provider, model="test-model", max_tokens=512, spec_sha256="0" * 64)


def stub(script: Any) -> MP.Provider:
    """A stub provider whose requests are inspectable via ``provider._stub.requests``."""
    return MP.Provider(MP.PROVIDERS["stub"], None, "", MP.StubProvider(script))


def options(root: Path, broker: Any, provider: MP.Provider, *, journal: Any = None, budget: Budget | None = None,
            secrets: tuple[str, ...] = (KEY,), **kw: Any) -> RunOptions:
    """RunOptions with a FileJournal under *root* (unless *journal* is given)."""
    j = journal if journal is not None else FileJournal(root, RUN, secrets=secrets, mac_key=b"k" * 32)
    return RunOptions(project_root=root, run_id=RUN, budget=budget or Budget(), journal=j, exec_broker=broker,
                      provider=provider, mac_key=b"k" * 32, secrets=secrets, **kw)


def records(root: Path, run_id: str = RUN) -> list[dict[str, Any]]:
    """The transcript records of a run."""
    path = root / ".agentteams" / "wasm-runs" / run_id / "transcript.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()]


def text_reply(text: str, stop: str = "end_turn") -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "stop": stop}


def tool_reply(*uses: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    return {"content": [{"type": "tool_use", "id": i, "name": n, "input": a} for i, n, a in uses], "stop": "tool_use"}
