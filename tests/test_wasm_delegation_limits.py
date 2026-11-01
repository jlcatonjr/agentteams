"""Delegation limits (WASM P09 §4.4) and the ``wasm_runtime.delegation`` schema block (§2, acceptance 2).

Depth, fan-out and tree-size caps, cycle refusal, targets outside the rendered ``delegates``, hard caps clamping
brief values, the ``delegate`` tool spec naming the allowed targets, the event carrying ``agent.delegates``, and a
brief that tries to add an amplification key is refused (schema and loader).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

import pytest

from agentteams import wasm_exec as W
from agentteams.wasm_agent import delegation as D
from agentteams.wasm_agent import protocol as P
from agentteams.wasm_agent import tools as T
from agentteams.wasm_agent.budget import Budget
from agentteams.wasm_agent.child_runs import ChildRuns
from agentteams.wasm_agent.team import normalize_wasm_runtime
from tests._wasm_delegation_helpers import (
    GuestPreparer, Team, agent, child_ids, grant, native_guests, project, routed, run, run_records, text_reply,
    tool_reply,
)

__all__ = ["native_guests"]
jsonschema = pytest.importorskip("jsonschema")
SCHEMA = json.loads((Path(D.__file__).parents[1] / "schemas" / "project-description.schema.json").read_text())


def _delegate(target: str, i: str = "d1") -> dict:
    return tool_reply((i, "delegate", {"agent": target, "task": "sub-task"}))


def _team(root: Path) -> Team:
    return Team(agent("orchestrator", grant(root, "docs"), delegates=("a", "b")),
                agent("a", grant(root, "docs"), delegates=("b", "orchestrator")),
                agent("b", grant(root, "docs"), delegates=("c",)),
                agent("c", grant(root, "docs")))


def _denied(recs: list[dict]) -> list[tuple[str, str]]:
    return [(r["code"], r.get("why", "")) for r in recs if r["kind"] == "denied"]


# --- depth --------------------------------------------------------------------------------------------------------

def test_depth_cap_drops_delegate_from_the_toolset_at_the_max_depth(
        tmp_path: Path, native_guests: list, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    script = routed({"orchestrator": [_delegate("a"), text_reply("root done")],
                     "a": [_delegate("b"), text_reply("a done")]})
    res = run(root, _team(root), "orchestrator", script, budget=Budget(max_delegation_depth=1),
              monkeypatch=monkeypatch)
    assert res.status == "ok"
    a_recs = run_records(root, child_ids(run_records(root, res.run_id))[0])
    assert "delegate" not in a_recs[0]["agent"]["tools"] and a_recs[0]["agent"]["delegates"] == []
    assert not child_ids(a_recs)                                    # a's delegate use was answered "not granted"
    a_tools = next(tools for who, _, tools in script.seen if who == "a")
    assert "delegate" not in {getattr(t, "name", None) or t.get("name") for t in a_tools}


def test_tree_check_denies_beyond_depth_with_the_depth_code() -> None:
    tree = D.RunTree(max_depth=1, max_child_runs=8, max_tree_runs=16)
    spec = agent("orchestrator", W.COMPUTE_ONLY, delegates=("a",))
    tree.add("r" * 32, spec, None)
    tree.add("c" * 32, agent("a", W.COMPUTE_ONLY, delegates=("b",)), "r" * 32)
    with pytest.raises(D.DelegationError) as exc:
        tree.check("c" * 32, "b")
    assert exc.value.code == "depth"


# --- fan-out, tree size, cycles, targets ---------------------------------------------------------------------------

def test_fanout_cap_refuses_the_extra_child(tmp_path: Path, native_guests: list,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    script = routed({"orchestrator": [_delegate("a", "d1"), _delegate("b", "d2"), text_reply("root done")]})
    res = run(root, _team(root), "orchestrator", script, budget=Budget(max_child_runs=1), monkeypatch=monkeypatch)
    recs = run_records(root, res.run_id)
    assert len(child_ids(recs)) == 1 and ("refused", "delegation cap reached: max_child_runs") in _denied(recs)


def test_tree_cap_refuses_once_the_tree_is_full(tmp_path: Path, native_guests: list,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    script = routed({"orchestrator": [_delegate("a", "d1"), _delegate("b", "d2"), text_reply("root done")]})
    res = run(root, _team(root), "orchestrator", script, delegation={"default_share": 0.5, "max_tree_runs": 2},
              monkeypatch=monkeypatch)
    recs = run_records(root, res.run_id)
    assert len(child_ids(recs)) == 1
    assert any(code == "refused" and "tree cap 2" in why for code, why in _denied(recs))


def test_cycle_is_refused(tmp_path: Path, native_guests: list, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    script = routed({"orchestrator": [_delegate("a"), text_reply("root done")],
                     "a": [_delegate("orchestrator"), text_reply("a done")]})
    res = run(root, _team(root), "orchestrator", script, monkeypatch=monkeypatch)
    assert res.status == "ok"
    a_recs = run_records(root, child_ids(run_records(root, res.run_id))[0])
    assert not child_ids(a_recs) and any("cycle" in why for _, why in _denied(a_recs))


def test_stage0_rechecks_target_and_cycle(tmp_path: Path) -> None:
    """Stage 0's own checks do not trust the host loop: target outside delegates, cycle, unknown parent."""
    root = project(tmp_path)
    team = _team(root)
    children = ChildRuns(project_root=str(root), root_run_id="r" * 32, root_spec=team.specs["orchestrator"],
                         tree=D.RunTree(max_depth=4, max_child_runs=8, max_tree_runs=16),
                         resolve_agent=team.agent_spec, make_preparer=lambda g: GuestPreparer(
                             grant=g, guest_source=b"", guest_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934c"
                                                                     "a495991b7852b855"),
                         write_roots=(), secrets=(), mac_key=b"k" * 32)
    with pytest.raises(D.DelegationError) as exc:
        children.open(parent="r" * 32, agent="c", delegation_id="d1")       # c is not the orchestrator's target
    assert exc.value.code == "not-granted"
    with pytest.raises(D.DelegationError, match="not live"):
        children.open(parent=secrets.token_hex(16), agent="a", delegation_id="d1")
    slot = children.open(parent="r" * 32, agent="a", delegation_id="d1")
    with pytest.raises(D.DelegationError, match="cycle"):
        children.open(parent=slot.run_id, agent="orchestrator", delegation_id="d2")
    children.close_all()


def test_decoder_refuses_a_target_outside_delegates() -> None:
    frame = json.dumps({"v": 1, "op": "delegate", "id": "d1", "agent": "x", "task": "t", "budget": None,
                        "carry": None}).encode()
    with pytest.raises(P.ProtocolError) as exc:
        P.decode_op(frame, expect_ids=frozenset(), toolset=frozenset({"delegate"}), delegates=frozenset({"a"}))
    assert exc.value.code == "not-granted"


def test_delegate_op_budget_request_is_closed() -> None:
    assert D.check_budget_request({"max_model_calls": 2, "wall_clock_s": 1.5}) == {"max_model_calls": 2,
                                                                                    "wall_clock_s": 1.5}
    for bad in ({"max_delegation_depth": 9}, {"max_model_calls": -1}, {"max_model_calls": 1.5}, [1]):
        with pytest.raises(D.DelegationError):
            D.check_budget_request(bad)


# --- caps clamp brief values --------------------------------------------------------------------------------------

def test_hard_caps_clamp_brief_values() -> None:
    b = D.clamp_budget(Budget(max_delegation_depth=9, max_child_runs=500))
    assert (b.max_delegation_depth, b.max_child_runs) == (D.HARD_MAX_DEPTH, D.HARD_MAX_CHILD_RUNS)
    assert D.normalize_delegation({"max_tree_runs": 999}) == {"default_share": 0.5, "max_tree_runs": 64}
    tree = D.RunTree(max_depth=99, max_child_runs=99, max_tree_runs=999)
    assert (tree.max_depth, tree.max_child_runs, tree.max_tree_runs) == (4, 32, 64)


# --- what the model and the guest are told -------------------------------------------------------------------------

def test_delegate_tool_spec_names_the_allowed_targets() -> None:
    spec = next(s for s in T.tool_specs({"delegate"}, delegates={"b", "a"}) if s.name == "delegate")
    assert "Allowed agents: a, b" in spec.description and "exactly one of: a, b" in str(spec.input_schema)
    assert T.tool_specs({"delegate"})[0].description == T._SPECS["delegate"][0]   # pin shape unchanged


def test_event_carries_the_agents_delegates(tmp_path: Path, native_guests: list,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path)
    seen: list[bytes] = []
    real = W.execute_launch

    def spy(launch: object, stdin: bytes, **kw: object) -> object:
        seen.append(stdin)
        return real(launch, stdin, **kw)
    monkeypatch.setattr(W, "execute_launch", spy)
    run(root, _team(root), "orchestrator", routed({"orchestrator": [text_reply("hi")]}), monkeypatch=monkeypatch)
    assert json.loads(seen[0])["agent"]["delegates"] == ["a", "b"]


# --- schema (acceptance 2) ------------------------------------------------------------------------------------------

def _schema_errors(block: dict) -> list[str]:
    brief = {"project_goal": "A placeholder goal for a schema test fixture.", "deliverables": ["Report"],
             "wasm_runtime": {"delegation": block}}
    return [e.message for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(brief)]


def test_schema_accepts_only_default_share_and_max_tree_runs() -> None:
    node = SCHEMA["properties"]["wasm_runtime"]["properties"]["delegation"]
    assert node["additionalProperties"] is False and set(node["properties"]) == set(D.DELEGATION_KEYS)
    assert _schema_errors({"default_share": 0.25, "max_tree_runs": 8}) == []
    assert _schema_errors({"amplify": True}) and _schema_errors({"max_depth": 3})
    assert _schema_errors({"default_share": 0}) and _schema_errors({"default_share": 1.5})


def test_loader_refuses_an_amplify_key() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        normalize_wasm_runtime({"delegation": {"amplify": True}})
    assert normalize_wasm_runtime({})["delegation"] == {"default_share": 0.5, "max_tree_runs": 16}
