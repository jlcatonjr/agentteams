"""P00 D6/D7: ``os_sandbox: off`` goes through the P15 relaxation gate, and the write-policy refusals (Rule 12).

* Governed (``enforce_decision_signing`` on disk) + brief off -> refused naming ``sandbox-os-off:<fw>``; an operator
  Ed25519-signed row clears it, registers the exception and records ``sandbox_state``; ``dry_run`` writes nothing.
* Ungoverned -> the generalised ``SANDBOX RELAXATION`` notice, no refusal.
* No switch -> the gate is not engaged: no ``sandbox_state`` record (byte-identical build-log), as before P00.
* ``write_policy: orchestrator-only`` + OS sandbox off is refused from the brief and from ``--os-sandbox off``.

Ed25519 is stubbed as in ``test_sandbox_relaxation_gate.py`` (the gate's real-signature path is covered there).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.cli import exception_registry as er
from agentteams.cli.artifacts import PrivilegeConfinementError, finalize_privilege_wiring
from agentteams.cli.sandbox_relaxation_gate import SandboxRelaxationError
from agentteams.cli.write_root_policy import WriteRootPolicyError
from agentteams.sandbox_state import OS_SANDBOX_CLI_KEY
from tests.test_sandbox_relaxation_gate import _log, _row, _team, stub_ed25519  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"


def _manifest(**brief_overrides) -> dict:
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined", **brief_overrides}
    return analyze.build_manifest(brief, framework="claude")


def _wire(manifest: dict, project: Path, team: Path, *, tokens=(), dry_run=False) -> dict:
    finalize_privilege_wiring(manifest, list(tokens), "claude", project, team_dir=team, dry_run=dry_run)
    return manifest


def test_governed_off_without_a_signed_decision_is_refused(tmp_path):
    team = _team(tmp_path)
    with pytest.raises(SandboxRelaxationError, match="sandbox-os-off:claude"):
        _wire(_manifest(os_sandbox="off"), tmp_path, team)
    assert er.list_exceptions(team) == []


def test_governed_off_with_a_signed_decision_succeeds_and_records(tmp_path, stub_ed25519, capsys):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude"))
    manifest = _wire(_manifest(os_sandbox="off"), tmp_path, team)
    assert manifest["sandbox_state"]["os"] == "off" and manifest["sandbox_state"]["os_reason"] == "brief"
    assert [r["authorization"] for r in manifest["sandbox_relaxations"]] == ["ed25519-decision"]
    assert [e["action"] for e in er.list_exceptions(team)] == ["sandbox-os-off:claude"]
    assert "SANDBOX RELAXATION: os sandbox OFF for claude by brief" in capsys.readouterr().err
    assert "claude:sandbox" not in manifest["host_features"]


def test_governed_dry_run_checks_but_writes_nothing(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row("sandbox-os-off:claude"))
    before = log.read_text()
    _wire(_manifest(os_sandbox="off"), tmp_path, team, dry_run=True)
    assert log.read_text() == before and er.list_exceptions(team) == []


def test_ungoverned_off_prints_the_notice_and_records(tmp_path, capsys):
    team = _team(tmp_path, governed=False)
    manifest = _wire(_manifest(os_sandbox="off"), tmp_path, team)
    assert [r["authorization"] for r in manifest["sandbox_relaxations"]] == ["notice-only"]
    assert "SANDBOX RELAXATION: os sandbox OFF for claude" in capsys.readouterr().err


def test_cooperative_off_is_not_a_relaxation_and_the_notice_agrees(tmp_path, capsys):
    team = _team(tmp_path)
    manifest = _wire(_manifest(privilege_profile="cooperative", os_sandbox="off"), tmp_path, team)
    assert manifest["sandbox_relaxations"] == []
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err


def test_cli_off_is_gated_like_the_brief(tmp_path):
    team = _team(tmp_path)
    manifest = _manifest(os_sandbox="on")
    manifest[OS_SANDBOX_CLI_KEY] = "off"
    with pytest.raises(SandboxRelaxationError, match="sandbox-os-off:claude"):
        _wire(manifest, tmp_path, team)


def test_tightening_back_on_retires_the_exception(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude"))
    _wire(_manifest(os_sandbox="off"), tmp_path, team)
    assert er.list_exceptions(team)[0]["status"] == "active"
    _wire(_manifest(os_sandbox="on"), tmp_path, team)
    assert er.list_exceptions(team)[0]["status"] != "active"


def test_no_switch_does_not_engage_the_gate(tmp_path):
    team = _team(tmp_path)
    manifest = _wire(_manifest(), tmp_path, team)
    assert "sandbox_state" not in manifest and "sandbox_relaxations" not in manifest
    manifest = _wire(_manifest(privilege_profile="cooperative"), tmp_path, team)  # pre-P00 behaviour: ungated
    assert "sandbox_state" not in manifest


def test_a_recorded_state_keeps_the_gate_engaged_after_the_key_is_removed(tmp_path):
    team = _team(tmp_path)
    rec = {"os": "on", "os_reason": "brief", "wasm": "off", "wasm_reason": "default-off", "wasm_languages": [],
           "wasm_grants": {}, "model_substrate": None}
    (team / "references" / "build-log.json").write_text(json.dumps({"sandbox_state": rec}))
    with pytest.raises(SandboxRelaxationError, match="sandbox-os-off:claude"):
        _wire(_manifest(privilege_profile="cooperative"), tmp_path, team)


def test_explicit_argv_token_beats_brief_off(tmp_path):
    team = _team(tmp_path)
    manifest = _wire(_manifest(os_sandbox="off"), tmp_path, team, tokens=["claude:sandbox"])
    assert "claude:sandbox" in manifest["host_features"]
    assert manifest["sandbox_state"]["os"] == "on"


def test_cli_off_with_an_explicit_token_is_refused(tmp_path):
    manifest = _manifest()
    manifest[OS_SANDBOX_CLI_KEY] = "off"
    with pytest.raises(PrivilegeConfinementError, match="conflicts"):
        _wire(manifest, tmp_path, _team(tmp_path), tokens=["claude:sandbox"])


# --- write_policy (D6; Rule-12 regression (a)) -------------------------------------------------


def test_brief_off_with_orchestrator_only_is_refused():
    with pytest.raises(ValueError, match='os_sandbox "off"'):
        _manifest(write_policy="orchestrator-only", os_sandbox="off")


def test_cli_off_with_orchestrator_only_is_refused(tmp_path):
    manifest = _manifest(write_policy="orchestrator-only")
    manifest[OS_SANDBOX_CLI_KEY] = "off"
    with pytest.raises(WriteRootPolicyError, match="--os-sandbox off"):
        _wire(manifest, tmp_path, _team(tmp_path))


def test_exclusive_inbound_advisory_needs_the_boundary(tmp_path):
    team = _team(tmp_path, governed=False)
    on = _wire(_manifest(privilege_profile="exclusive"), tmp_path, team)
    off = _wire(_manifest(privilege_profile="exclusive", os_sandbox="off"), tmp_path, team)
    code = "privilege-profile-exclusive-inbound-hardening"
    assert code in [a["code"] for a in on.get("advisories", [])]
    assert code not in [a["code"] for a in off.get("advisories", [])]  # Rule-12 regression (d)


# --- the CLI (refusals run before anything is rendered, so these are quick) --------------------


def _generate(tmp_path: Path, brief: dict, *extra: str) -> subprocess.CompletedProcess:
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(brief), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(REPO / "build_team.py"), "--description", str(path), "--project", str(tmp_path),
         "--framework", "claude", "--output", str(tmp_path / ".claude" / "agents"), "--no-scan", "--yes", *extra],
        cwd=tmp_path, capture_output=True, text=True, timeout=600,
    )


def test_cli_governed_brief_off_exits_1_naming_the_action(tmp_path):
    _team(tmp_path)
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined", "os_sandbox": "off"}
    proc = _generate(tmp_path, brief)
    assert proc.returncode == 1, proc.stdout[-800:] + proc.stderr[-800:]
    assert "sandbox-os-off:claude" in proc.stderr


def test_cli_flag_conflict_and_write_policy_refusals(tmp_path):
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined"}
    proc = _generate(tmp_path, brief, "--os-sandbox", "off", "--target-host-features", "claude:sandbox")
    assert proc.returncode == 1 and "conflicts" in proc.stderr
    proc = _generate(tmp_path, {**brief, "write_policy": "orchestrator-only"}, "--os-sandbox", "off")
    assert proc.returncode == 1 and "--os-sandbox off" in proc.stderr
    proc = _generate(tmp_path, brief, "--os-sandbox", "maybe")
    assert proc.returncode == 2 and "--os-sandbox" in proc.stderr
