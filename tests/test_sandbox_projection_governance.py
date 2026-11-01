"""P20 D6/D7: turning the projected model-substrate switch from on to off goes through the P15 gate (Rule 15).

* Governed, previous on (build-log record OR projected file), brief off -> refused naming
  ``sandbox-wasm-off:model-substrate``; an operator Ed25519-signed row clears it (Ed25519 stubbed as in
  ``test_sandbox_relaxation_gate.py``).
* Unset after on keeps the file as the floor: no relaxation (the kept value is the new state).
* Ungoverned: the ``SANDBOX RELAXATION`` notice only. Unset -> off and off -> on need nothing.
* Multi-sync: carries the switch and rewrites the file; an off in a governed workspace with no decision is refused
  and nothing is consumed (``consume=False``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import multi_sync as ms
from agentteams import sandbox_projection as sp
from agentteams.cli.artifacts import finalize_privilege_wiring
from agentteams.cli.os_sandbox_switch import gate_engaged
from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError
from agentteams.sandbox_state import SandboxState
from agentteams.wasm_config import WASM_SUBSTRATE_CLI_KEY
from tests._sandbox_gate_helpers import _log, _row, _team

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
ACTION = "sandbox-wasm-off:model-substrate"


@pytest.fixture(autouse=True)
def _no_live_probe(monkeypatch):
    from agentteams import host_features

    monkeypatch.setattr(host_features, "os_sandbox_mechanism_available", lambda platform=None: True)


def _manifest(substrate: str | None, **extra) -> dict:
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined"}
    if substrate is not None:
        brief["wasm_sandbox"] = {"model_substrate": substrate}
    m = analyze.build_manifest(brief, framework="claude")
    m.update(extra)
    return m


def _render(manifest: dict, project: Path, team: Path, *, dry_run: bool = False) -> dict:
    """finalize (gate) + the projection write + the build-log record, as one generate does."""
    finalize_privilege_wiring(manifest, [], "claude", project, team_dir=team, dry_run=dry_run)
    if not dry_run:
        sp.emit_projection(manifest, project, team)
        (team / "references" / "build-log.json").write_text(json.dumps({"sandbox_state": manifest["sandbox_state"]}))
    return manifest


def _decisions(team: Path) -> str:
    path = team / "references" / "security-decisions.log.csv"
    return path.read_text() if path.exists() else ""


def test_governed_on_to_off_is_refused_then_cleared_by_a_signed_row(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _render(_manifest("on"), tmp_path, team)
    assert sp.read_projection(tmp_path)["model_substrate_wasm"] == "on"
    with pytest.raises(SandboxRelaxationError, match=ACTION):
        _render(_manifest("off"), tmp_path, team)
    assert sp.read_projection(tmp_path)["model_substrate_wasm"] == "on"  # nothing rewritten on a refusal
    _log(team, _row(ACTION))
    m = _render(_manifest("off"), tmp_path, team)
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [(ACTION, "ed25519-decision")]
    assert sp.read_projection(tmp_path)["model_substrate_wasm"] == "off"
    _render(_manifest("off"), tmp_path, team)  # the next off render is not a new relaxation


def test_cli_off_is_gated_like_the_brief(tmp_path):
    team = _team(tmp_path)
    _render(_manifest("on"), tmp_path, team)
    with pytest.raises(SandboxRelaxationError, match=ACTION):
        _render(_manifest("on", **{WASM_SUBSTRATE_CLI_KEY: "off"}), tmp_path, team)


def test_the_file_alone_is_evidence_when_the_record_is_gone(tmp_path):
    team = _team(tmp_path)
    _render(_manifest("on"), tmp_path, team)
    (team / "references" / "build-log.json").unlink()
    with pytest.raises(SandboxRelaxationError, match=ACTION):
        _render(_manifest("off"), tmp_path, team)


def test_the_record_alone_is_evidence_when_the_file_is_gone(tmp_path):
    team = _team(tmp_path)
    _render(_manifest("on"), tmp_path, team)
    (tmp_path / sp.PROJECTION_REL).unlink()
    with pytest.raises(SandboxRelaxationError, match=ACTION):
        _render(_manifest("off"), tmp_path, team)
    with pytest.raises(SandboxRelaxationError, match=ACTION):  # deleting the file and the key: still the record
        _render(_manifest(None), tmp_path, team)


def test_unset_after_on_keeps_the_floor_without_a_relaxation(tmp_path, capsys):
    team = _team(tmp_path)
    _render(_manifest("on"), tmp_path, team)
    before = (tmp_path / sp.PROJECTION_REL).read_bytes()
    m = _render(_manifest(None), tmp_path, team)
    assert m["sandbox_relaxations"] == [] and m["sandbox_state"]["model_substrate"] == "on"
    assert (tmp_path / sp.PROJECTION_REL).read_bytes() == before
    assert "SANDBOX STATE KEPT" in capsys.readouterr().out
    _render(_manifest(None), tmp_path, team)  # and again: stable, no relaxation loop


def test_ungoverned_on_to_off_prints_the_notice(tmp_path, capsys):
    team = _team(tmp_path, governed=False)
    _render(_manifest("on"), tmp_path, team)
    m = _render(_manifest("off"), tmp_path, team)
    assert [r["authorization"] for r in m["sandbox_relaxations"]] == ["notice-only"]
    assert "SANDBOX RELAXATION: model-substrate WASM switch OFF; it was on." in capsys.readouterr().err


@pytest.mark.parametrize(("first", "second"), [(None, "off"), ("off", "on"), (None, "on")])
def test_tightening_or_first_setting_needs_nothing(tmp_path, first, second):
    team = _team(tmp_path)
    if first is not None:
        _render(_manifest(first), tmp_path, team)
    m = _render(_manifest(second), tmp_path, team)
    assert m["sandbox_relaxations"] == []


def test_dry_run_consumes_nothing(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _render(_manifest("on"), tmp_path, team)
    _log(team, _row(ACTION))
    before = _decisions(team)
    _render(_manifest("off"), tmp_path, team, dry_run=True)
    assert _decisions(team) == before


def test_the_substrate_switch_engages_the_gate():
    off = SandboxState(os=True, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=())
    on = SandboxState(os=True, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=(),
                      model_substrate="on")
    assert gate_engaged({}, on, None, recorded=False)
    assert not gate_engaged({}, on, None, recorded=False, wasm=False)  # sync: only a change engages it
    assert gate_engaged({}, off, on, recorded=False, wasm=False)
    assert not gate_engaged({}, off, None, recorded=False)


# --- multi-sync parity (D7) ---------------------------------------------------------------------------------------


def _brief(root: Path, substrate: str | None) -> None:
    data = {"project_name": "T", "privilege_profile": "confined"}
    if substrate is not None:
        data["wasm_sandbox"] = {"model_substrate": substrate}
    (root / ".agentteams").mkdir(exist_ok=True)
    (root / ".agentteams" / "brief.json").write_text(json.dumps(data))


def test_sync_carries_the_switch_and_rewrites_the_file(tmp_path):
    _team(tmp_path, governed=False)
    _brief(tmp_path, "on")
    priv = ms._read_brief_privilege(tmp_path)
    assert priv["model_substrate"] == "on"
    written = ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False)
    assert str((tmp_path / sp.PROJECTION_REL).resolve()) in written
    rec = sp.read_projection(tmp_path)
    assert (rec["model_substrate_wasm"], rec["team"]) == ("on", ".claude/agents")
    assert ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False) is not None  # idempotent


def test_sync_off_in_a_governed_workspace_is_refused_and_consumes_nothing(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _brief(tmp_path, "on")
    ms._emit_privilege_artifacts(tmp_path, "claude", ms._read_brief_privilege(tmp_path), dry_run=False)
    _brief(tmp_path, "off")
    with pytest.raises(ValueError, match=ACTION):
        ms._emit_privilege_artifacts(tmp_path, "claude", ms._read_brief_privilege(tmp_path), dry_run=False)
    assert sp.read_projection(tmp_path)["model_substrate_wasm"] == "on"
    _log(team, _row(ACTION))
    before = _decisions(team)
    ms._emit_privilege_artifacts(tmp_path, "claude", ms._read_brief_privilege(tmp_path), dry_run=False)
    assert _decisions(team) == before  # consume=False: a sync checks a clearance, never spends it
    assert sp.read_projection(tmp_path)["model_substrate_wasm"] == "off"


def test_sync_with_the_key_removed_keeps_the_floor(tmp_path):
    team = _team(tmp_path)
    _brief(tmp_path, "on")
    ms._emit_privilege_artifacts(tmp_path, "claude", ms._read_brief_privilege(tmp_path), dry_run=False)
    _brief(tmp_path, None)
    ms._emit_privilege_artifacts(tmp_path, "claude", ms._read_brief_privilege(tmp_path), dry_run=False)
    assert sp.read_projection(tmp_path)["model_substrate_wasm"] == "on"
    assert team.is_dir()


def test_a_bad_brief_value_projects_no_posture(tmp_path):
    _brief(tmp_path, "on")
    data = json.loads((tmp_path / ".agentteams" / "brief.json").read_text())
    data["wasm_sandbox"]["model_substrate"] = "ON"
    (tmp_path / ".agentteams" / "brief.json").write_text(json.dumps(data))
    assert ms._read_brief_privilege(tmp_path) == {}
