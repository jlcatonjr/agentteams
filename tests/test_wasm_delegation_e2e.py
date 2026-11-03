"""Live parent -> child delegation (WASM P09 §6): the pinned guest under the dev wasmtime + the stub provider.

Needs the dev wasmtime (``AGENTTEAMS_WASM_DEV_WASMTIME``) and the DEV-ONLY CPython WASI build
(``AGENTTEAMS_WASM_DEV_PYTHON``); skips otherwise. Neither is the pinned build (D9): this is development evidence.
Seatbelt cannot nest inside this session, so Stage 0 runs the tree in-process and guests run un-nested with
snapshot-enforced read-only mounts.

Every child step is a fresh wasmtime invocation carrying the child's **effective** grant: its declared ``secret/``
mount is gone (the delegator lacks it), ``/scratch`` is the child's own dir, and the limits are the minimum of both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentteams import model_providers as MP
from agentteams import wasm_exec as W
from agentteams.wasm_agent import launch as L
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.delegation import RESULT_LABEL, verify_tree
from tests._wasm_agent_helpers import no_network
from tests._wasm_delegation_helpers import (
    Team, agent, child_ids, grant, project, routed, run_records, text_reply, tool_reply,
)
from tests._wasm_helpers import needs_wasmtime
from tests.test_wasm_guests_live import DevResolver, needs_dev_python

pytestmark = [needs_wasmtime, needs_dev_python]
__all__ = ["no_network"]

PARENT_LIMITS = W.Limits(fuel=6_000_000_000, max_memory_bytes=64 << 20, timeout_s=30)
CHILD_LIMITS = W.Limits(fuel=9_000_000_000, max_memory_bytes=128 << 20, timeout_s=60)


def _dirs(argv: list[str]) -> dict[str, str]:
    """guest name -> host of every ``--dir HOST::GUEST`` in a wasmtime argv."""
    out = {}
    for i, a in enumerate(argv[:-1]):
        if a == "--dir":
            host, guest = argv[i + 1].split("::")[:2]
            out[guest] = host
    return out


@pytest.fixture
def argv_spy(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []
    real = W.execute_launch

    def spy(launch: Any, stdin: bytes, **kw: Any) -> Any:
        seen.append(list(launch.argv))
        return real(launch, stdin, **kw)
    monkeypatch.setattr(W, "execute_launch", spy)
    return seen


def test_parent_to_child_run_on_real_guests_with_the_attenuated_grant(tmp_path: Path, argv_spy: list,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    (root / "secret" / "s.txt").write_text("TOPSECRET\n")
    team = Team(agent("orchestrator", grant(root, "docs", scratch_of="orchestrator", limits=PARENT_LIMITS),
                      delegates=("primary-producer",)),
                agent("primary-producer", grant(root, "docs", "secret", scratch_of="primary-producer",
                                                limits=CHILD_LIMITS),
                      tools=frozenset({"read_file", "write_file", "run_code"})))
    script = routed({
        "orchestrator": [tool_reply(("d1", "delegate", {"agent": "primary-producer", "task": "Read the docs."})),
                         text_reply("root final")],
        "primary-producer": [tool_reply(("r1", "read_file", {"path": "/project/docs/a.md"}),
                                        ("r2", "read_file", {"path": "/project/secret/s.txt"}),
                                        ("w1", "write_file", {"path": "/scratch/c.txt", "content": "child wrote"})),
                             text_reply("child final")]})
    monkeypatch.setattr(L, "build_provider", lambda *a, **k: MP.Provider(MP.PROVIDERS["stub"], None, "",
                                                                         MP.StubProvider(script)))
    res = L.stage0(team.agent_spec("orchestrator"), "Delegate the reading.", project_root=root, budget=Budget(),
                   resolver=DevResolver(), stub_script=[], resolve_agent=team.agent_spec,
                   host_confine="off")                     # in-process Stage 1, so the spy and provider reach it
    assert (res.status, res.output) == ("ok", "root final"), res
    own = root / ".agentteams" / "wasm" / "scratch" / "primary-producer"
    assert (own / "c.txt").read_text() == "child wrote"                     # the child's own scratch
    assert not (root / ".agentteams" / "wasm" / "scratch" / "orchestrator" / "c.txt").exists()
    child_steps = [a for a in argv_spy if _dirs(a).get("/scratch") == str(own)]
    parent_steps = [a for a in argv_spy if a not in child_steps]
    assert len(child_steps) == 3 and len(parent_steps) == 4                 # a fresh guest per step
    want = W.grant_flags(PARENT_LIMITS)                                      # min(parent, child) == parent here
    for a in child_steps:
        assert set(_dirs(a)) == {"/lib", "/src", "/project/docs", "/scratch"}   # no /project/secret
        assert a[2:8] == want
    recs = run_records(root, res.run_id)
    cid = child_ids(recs)[0]
    start = next(r for r in recs if r["kind"] == "delegate-start")
    assert any(d.startswith("/project/secret:") for d in start["dropped"])
    child_turn = [m for who, m, _ in script.seen if who == "primary-producer"][-1][-1]["content"]
    results = {b["tool_use_id"]: b for b in child_turn}
    assert results["r1"]["is_error"] is False and results["r1"]["content"] == "granted doc\n"
    assert results["r2"]["is_error"] is True and "TOPSECRET" not in results["r2"]["content"]
    parent_turn = [m for who, m, _ in script.seen if who == "orchestrator"][-1][-1]["content"][0]
    assert parent_turn["content"] == RESULT_LABEL.format(slug="primary-producer", run_id=cid) + "\nchild final"
    assert verify_tree(root, res.run_id).ok
