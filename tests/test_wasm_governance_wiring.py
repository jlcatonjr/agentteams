"""P01 D5: WASM on->off and grant widening go through the P15 relaxation gate (Rule 15, D7).

* Governed + WASM on -> off: refused naming ``sandbox-wasm-off:<fw>``; an operator Ed25519-signed row clears it.
* Governed + new ``read_paths`` / ``scratch``: refused naming ``wasm-grant-widen:<fw>``; a row granting exactly the
  added tokens clears it; a superset row is refused. Turning WASM on with widened grants in the same run is a
  widening (relative to compute-only). Removing a grant retires the registry entry.
* WASM on with no grant is not a relaxation (off -> on is free).
* WASM evidence on disk with no record still engages the gate on a WASM-off render (``gate_engaged``).
* Ungoverned: notices only. ``dry_run``: nothing consumed or registered.
* ``--wasm-sandbox`` reaches the gate exactly like the brief; a bad value refuses the render.

Ed25519 is stubbed as in ``test_sandbox_relaxation_gate.py`` (the real-signature path is covered there).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.cli import exception_registry as er
from agentteams.cli.artifacts import PrivilegeConfinementError, finalize_privilege_wiring
from agentteams.cli.os_sandbox_switch import gate_engaged
from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError
from agentteams.sandbox_state import SandboxState
from tests.test_sandbox_relaxation_gate import _log, _row, _team, stub_ed25519  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
_AGENT = "primary-producer"
_DOCS = f"wasm:{_AGENT}:read:docs"
_SCRATCH = f"wasm:{_AGENT}:scratch"


@pytest.fixture(autouse=True)
def _no_live_probe(monkeypatch):
    """The D6 notes probe the live OS mechanism; keep these tests hermetic."""
    from agentteams import host_features

    monkeypatch.setattr(host_features, "os_sandbox_mechanism_available", lambda platform=None: True)


def _manifest(wasm: dict | None) -> dict:
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined"}
    if wasm is not None:
        brief["wasm_sandbox"] = wasm
    return analyze.build_manifest(brief, framework="claude")


def _wire(manifest: dict, project: Path, team: Path, *, dry_run=False, cli=None) -> dict:
    finalize_privilege_wiring(manifest, [], "claude", project, team_dir=team, dry_run=dry_run, wasm_sandbox_cli=cli)
    return manifest


def _record(team: Path, manifest: dict) -> None:
    """Persist the render's sandbox_state as build_team does (the next render reads it)."""
    (team / "references" / "build-log.json").write_text(json.dumps({"sandbox_state": manifest["sandbox_state"]}))


def _on(**agent) -> dict:
    return {"enabled": True, **({"agents": {_AGENT: agent}} if agent else {})}


def test_off_to_on_without_grants_is_free_and_recorded(tmp_path):
    team = _team(tmp_path)
    m = _wire(_manifest(_on()), tmp_path, team)
    assert m["sandbox_relaxations"] == [] and m["sandbox_state"]["wasm"] == "on"
    assert m["sandbox_state"]["wasm_reason"] == "brief"


def test_governed_on_to_off_is_refused_without_a_row_and_accepted_with_one(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _record(team, _wire(_manifest(_on()), tmp_path, team))
    with pytest.raises(SandboxRelaxationError, match="sandbox-wasm-off:claude"):
        _wire(_manifest({"enabled": False}), tmp_path, team)
    _log(team, _row("sandbox-wasm-off:claude"))
    m = _wire(_manifest({"enabled": False}), tmp_path, team)
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [
        ("sandbox-wasm-off:claude", "ed25519-decision")]
    assert m["sandbox_state"]["wasm"] == "off"


def test_cli_off_is_gated_like_the_brief(tmp_path):
    team = _team(tmp_path)
    _record(team, _wire(_manifest(_on()), tmp_path, team))
    with pytest.raises(SandboxRelaxationError, match="sandbox-wasm-off:claude"):
        _wire(_manifest(_on()), tmp_path, team, cli="off")


def test_removing_the_block_after_on_stays_gated(tmp_path):
    team = _team(tmp_path)
    _record(team, _wire(_manifest(_on()), tmp_path, team))
    with pytest.raises(SandboxRelaxationError, match="sandbox-wasm-off:claude"):
        _wire(_manifest(None), tmp_path, team)


def test_wasm_evidence_without_a_record_engages_the_gate(tmp_path):
    team = _team(tmp_path)
    (tmp_path / ".agentteams" / "wasm").mkdir(parents=True)
    (tmp_path / ".agentteams" / "wasm" / "installed.json").write_text("{}")
    with pytest.raises(SandboxRelaxationError, match="sandbox-wasm-off:claude"):
        _wire(_manifest(None), tmp_path, team)


def test_gate_engaged_wasm_cases():
    off = SandboxState(os=True, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=())
    on = SandboxState(os=True, os_reason="brief", wasm=True, wasm_reason="brief", wasm_languages=("python",))
    assert gate_engaged({"wasm_sandbox": {}}, off, None, recorded=False)
    assert gate_engaged({"_wasm_sandbox_cli": "off"}, off, None, recorded=False)
    assert gate_engaged({}, off, on, recorded=False)
    assert not gate_engaged({}, off, on, recorded=False, wasm=False)  # the sync projection derives no WASM half
    assert not gate_engaged({"wasm_sandbox": {}}, off, None, recorded=False, wasm=False)
    assert not gate_engaged({}, off, None, recorded=False)


def test_governed_turning_on_with_a_grant_is_a_widening(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    with pytest.raises(SandboxRelaxationError, match="wasm-grant-widen:claude") as exc:
        _wire(_manifest(_on(read_paths=["docs"], scratch=True)), tmp_path, team)
    assert _DOCS in str(exc.value) and _SCRATCH in str(exc.value)
    _log(team, _row("wasm-grant-widen:claude", grants=f"{_DOCS};{_SCRATCH}"))
    m = _wire(_manifest(_on(read_paths=["docs"], scratch=True)), tmp_path, team)
    assert [r["authorization"] for r in m["sandbox_relaxations"]] == ["ed25519-decision"]
    assert m["sandbox_state"]["wasm_grants"][_AGENT]["tokens"] == [_DOCS, _SCRATCH]
    assert [e["action"] for e in er.list_exceptions(team)] == ["wasm-grant-widen:claude"]


def test_governed_added_read_path_needs_an_exact_row(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("wasm-grant-widen:claude", grants=_SCRATCH))
    _record(team, _wire(_manifest(_on(scratch=True)), tmp_path, team))
    widened = _on(read_paths=["docs"], scratch=True)
    with pytest.raises(SandboxRelaxationError, match="wasm-grant-widen:claude"):
        _wire(_manifest(widened), tmp_path, team)
    _log(team, _row("wasm-grant-widen:claude", grants=f"{_DOCS};wasm:{_AGENT}:read:src"))  # superset: refused
    with pytest.raises(SandboxRelaxationError, match="exactly this run's tokens"):
        _wire(_manifest(widened), tmp_path, team)


def test_exact_row_for_the_added_path_is_accepted(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    # Each widening is its own standing exception under the shared aggregate cap (default 1; METAPLAN §7).
    (team / er.EXCEPTION_REGISTRY_REL).write_text(json.dumps({"cap": {"max_active_relaxing": 5}, "exceptions": []}))
    _log(team, _row("wasm-grant-widen:claude", grants=_SCRATCH))
    _record(team, _wire(_manifest(_on(scratch=True)), tmp_path, team))
    _log(team, _row("wasm-grant-widen:claude", grants=_DOCS, stamp="2026-10-31T01"))
    m = _wire(_manifest(_on(read_paths=["docs"], scratch=True)), tmp_path, team)
    assert {(r["kind"], r["authorization"]) for r in m["sandbox_relaxations"]} >= {
        ("wasm-grant-widen", "ed25519-decision")}


def test_removing_the_grant_retires_the_registry_entry(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("wasm-grant-widen:claude", grants=_SCRATCH))
    _record(team, _wire(_manifest(_on(scratch=True)), tmp_path, team))
    assert er.list_exceptions(team)[0]["status"] == "active"
    _wire(_manifest(_on()), tmp_path, team)
    assert er.list_exceptions(team)[0]["status"] != "active"


def test_governed_dry_run_checks_but_writes_nothing(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row("wasm-grant-widen:claude", grants=_SCRATCH))
    before = log.read_text()
    _wire(_manifest(_on(scratch=True)), tmp_path, team, dry_run=True)
    assert log.read_text() == before and er.list_exceptions(team) == []


def test_ungoverned_widening_and_off_print_notices_only(tmp_path, capsys):
    team = _team(tmp_path, governed=False)
    m = _wire(_manifest(_on(read_paths=["docs"])), tmp_path, team)
    assert [r["authorization"] for r in m["sandbox_relaxations"]] == ["notice-only"]
    _record(team, m)
    m = _wire(_manifest({"enabled": False, "agents": {_AGENT: {"read_paths": ["docs"]}}}), tmp_path, team)
    assert [r["action"] for r in m["sandbox_relaxations"]] == ["sandbox-wasm-off:claude"]
    out = capsys.readouterr()
    assert "SANDBOX RELAXATION" in out.err and "inert while wasm_sandbox is off" in out.out


def test_bad_cli_value_and_symlinked_read_path_refuse_the_render(tmp_path):
    team = _team(tmp_path)
    with pytest.raises(PrivilegeConfinementError, match="--wasm-sandbox"):
        _wire(_manifest(None), tmp_path, team, cli="ON")
    (tmp_path / "outside").mkdir()
    (tmp_path / "docs").symlink_to(tmp_path / "outside")
    with pytest.raises(PrivilegeConfinementError, match="symlink"):
        _wire(_manifest(_on(read_paths=["docs"])), tmp_path, team)


def test_no_wasm_key_does_not_engage_the_gate(tmp_path):
    team = _team(tmp_path)
    m = _wire(_manifest(None), tmp_path, team)
    assert "sandbox_state" not in m and "sandbox_relaxations" not in m
