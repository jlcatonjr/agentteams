"""P20 D1/D1a: ``SandboxState.model_substrate`` = ``--wasm-substrate`` > brief ``wasm_sandbox.model_substrate`` > a
kept projection > None, independent of ``wasm_sandbox.enabled``; the transient keys never reach the build-log.

Plan: ``tmp/by-week/2026-W44/wasm-sandbox/P20-substrate-projection.plan.md`` (D1, D1a, D3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import sandbox_state as ss
from agentteams.cli.artifacts import PrivilegeConfinementError, finalize_privilege_wiring
from agentteams.cli.wasm_sandbox_switch import WasmSwitchError, apply_wasm_switch
from agentteams.wasm_config import (
    WASM_SUBSTRATE_CLI_KEY,
    WASM_SUBSTRATE_KEPT_KEY,
    substrate_switch,
    validate_wasm_substrate_cli,
)
from tests._sandbox_gate_helpers import _team

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"


@pytest.fixture(autouse=True)
def _no_live_probe(monkeypatch):
    from agentteams import host_features

    monkeypatch.setattr(host_features, "os_sandbox_mechanism_available", lambda platform=None: True)


def _manifest(wasm: dict | None = None, **extra) -> dict:
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    if wasm is not None:
        brief["wasm_sandbox"] = wasm
    m = analyze.build_manifest(brief, framework="claude")
    m.update(extra)
    return m


@pytest.mark.parametrize(("wasm", "extra", "want"), [
    (None, {}, (None, None)),
    ({"model_substrate": "on"}, {}, ("on", "brief")),
    ({"model_substrate": "off"}, {}, ("off", "brief")),
    ({"model_substrate": "off"}, {WASM_SUBSTRATE_CLI_KEY: "on"}, ("on", "cli")),
    (None, {WASM_SUBSTRATE_CLI_KEY: "off"}, ("off", "cli")),
    (None, {WASM_SUBSTRATE_KEPT_KEY: "on"}, ("on", "kept")),
    ({"model_substrate": "off"}, {WASM_SUBSTRATE_KEPT_KEY: "on"}, ("off", "brief")),  # kept never beats the brief
])
def test_precedence_cli_then_brief_then_kept(wasm, extra, want):
    m = _manifest(wasm, **extra)
    assert substrate_switch(m) == want
    assert ss.effective_sandbox_state(m).model_substrate == want[0]


@pytest.mark.parametrize("bad", ["ON", "yes", True, 1, ""])
def test_a_bad_cli_value_fails_closed(bad):
    with pytest.raises(ValueError, match="--wasm-substrate"):
        validate_wasm_substrate_cli(bad)
    with pytest.raises(ValueError):
        ss.effective_sandbox_state(_manifest(**{WASM_SUBSTRATE_CLI_KEY: bad}))


def test_a_bad_kept_value_fails_closed():
    with pytest.raises(ValueError, match="kept"):
        substrate_switch(_manifest(**{WASM_SUBSTRATE_KEPT_KEY: "maybe"}))


@pytest.mark.parametrize("bad", ["ON", True, 1])
def test_a_bad_brief_value_fails_closed(bad):
    with pytest.raises(ValueError, match=r"wasm_sandbox\.model_substrate"):
        _manifest({"model_substrate": bad})


def test_the_substrate_switch_and_the_wasm_switch_are_independent():
    only_substrate = ss.effective_sandbox_state(_manifest({"model_substrate": "on"}))
    assert (only_substrate.wasm, only_substrate.model_substrate) == (False, "on")
    only_wasm = ss.effective_sandbox_state(_manifest({"enabled": True}))
    assert (only_wasm.wasm, only_wasm.model_substrate) == (True, None)
    both_cli = ss.effective_sandbox_state(_manifest(None, _wasm_sandbox_cli="on", **{WASM_SUBSTRATE_CLI_KEY: "off"}))
    assert (both_cli.wasm, both_cli.model_substrate) == (True, "off")


def test_apply_wasm_switch_keeps_an_existing_file_only_when_unset(tmp_path):
    proj = tmp_path / ".agentteams"
    proj.mkdir()
    (proj / "sandbox-state.json").write_text(json.dumps({"model_substrate_wasm": "off"}))
    m = _manifest()
    apply_wasm_switch(m, None, project_root=tmp_path)
    assert m[WASM_SUBSTRATE_KEPT_KEY] == "off"
    (proj / "sandbox-state.json").write_text("{not json")
    apply_wasm_switch(m, None, project_root=tmp_path)
    assert m[WASM_SUBSTRATE_KEPT_KEY] == "on"   # unreadable counts as on (fail toward the floor)
    m2 = _manifest({"model_substrate": "off"})
    m2[WASM_SUBSTRATE_KEPT_KEY] = "on"          # never trusted from input: re-derived from disk
    apply_wasm_switch(m2, None, project_root=tmp_path)
    assert WASM_SUBSTRATE_KEPT_KEY not in m2


def test_apply_wasm_switch_refuses_a_symlinked_projection_up_front(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / ".agentteams").symlink_to(real)
    with pytest.raises(WasmSwitchError, match="symlink"):
        apply_wasm_switch(_manifest({"model_substrate": "on"}), None, project_root=tmp_path)


def test_the_build_log_record_carries_the_value_and_no_transient_key(tmp_path):
    team = _team(tmp_path, governed=False)
    m = _manifest(None, **{WASM_SUBSTRATE_CLI_KEY: "on"})
    finalize_privilege_wiring(m, [], "claude", tmp_path, team_dir=team)
    assert m["sandbox_state"]["model_substrate"] == "on"
    record = json.dumps(m["sandbox_state"])
    assert WASM_SUBSTRATE_CLI_KEY not in record and WASM_SUBSTRATE_KEPT_KEY not in record
    from tests.test_sandbox_state_record import _run_log

    log = _run_log(tmp_path, sandbox_state=m["sandbox_state"])
    assert log["sandbox_state"]["model_substrate"] == "on"
    assert WASM_SUBSTRATE_CLI_KEY not in json.dumps(log)


def test_finalize_maps_a_bad_cli_value_to_a_refusal(tmp_path):
    team = _team(tmp_path, governed=False)
    with pytest.raises(PrivilegeConfinementError, match="--wasm-substrate"):
        finalize_privilege_wiring(_manifest(None, **{WASM_SUBSTRATE_CLI_KEY: "maybe"}), [], "claude", tmp_path,
                                  team_dir=team)
