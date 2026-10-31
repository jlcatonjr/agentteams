"""P00 D7 / METAPLAN R9: ``os_sandbox`` round-trips through the CAI ``privilege`` block and drives projection.

* ``_read_brief_privilege`` captures the raw brief value (validated; a typo projects nothing, with a notice);
  cooperative + ``on`` projects, cooperative alone or + ``off`` does not.
* ``_emit_privilege_artifacts`` derives the effective state from the projected manifest: ``confined`` + ``off``
  projects no boundary (Rule-12 regression (c)) but keeps the control-plane stubs; ``cooperative`` + ``on`` projects
  one; no ``os_sandbox`` is byte-identical to before.
* A governed workspace refuses a sync that would drop a boundary with no active exception, and sync never consumes.
* The ``agent-cai.schema.json`` privilege description lists the field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentteams import multi_sync as ms
from agentteams.host_features import expand_privilege_profile
from tests._sandbox_gate_helpers import _log, _row, _team

REPO = Path(__file__).resolve().parent.parent


def _brief(root: Path, data: dict) -> None:
    path = root / ".agentteams" / "brief.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("data, expected", [
    ({"privilege_profile": "confined", "os_sandbox": "off"},
     {"privilege_profile": "confined", "privilege_profile_explicit": True, "os_sandbox": "off"}),
    ({"privilege_profile": "cooperative", "os_sandbox": "on"},
     {"privilege_profile": "cooperative", "privilege_profile_explicit": True, "os_sandbox": "on"}),
    ({"privilege_profile": "cooperative", "os_sandbox": "off"}, {}),
    ({"privilege_profile": "cooperative"}, {}),
    ({"privilege_profile": "confined"}, {"privilege_profile": "confined", "privilege_profile_explicit": True}),
])
def test_capture(tmp_path, data, expected):
    _brief(tmp_path, data)
    assert ms._read_brief_privilege(tmp_path) == expected


def test_an_invalid_value_projects_nothing_with_a_notice(tmp_path, capsys):
    _brief(tmp_path, {"privilege_profile": "confined", "os_sandbox": "OFF"})
    assert ms._read_brief_privilege(tmp_path) == {}
    assert "os_sandbox" in capsys.readouterr().err


def test_projected_manifest_without_the_switch_is_as_before(tmp_path):
    for fw in ("claude", "goose", "codex", "grok"):
        m = ms._projected_privilege_manifest(tmp_path, fw, {"privilege_profile": "confined"})
        assert m["host_features"] == expand_privilege_profile("confined", fw) and "os_sandbox" not in m


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


def _names(written: list[str]) -> set[str]:
    return {Path(p).name for p in written}


def test_confined_off_projects_no_boundary_but_keeps_the_stubs(tmp_path, linux):
    priv = {"privilege_profile": "confined", "privilege_profile_explicit": True, "os_sandbox": "off"}
    written = ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False)
    assert not (tmp_path / "sandbox" / "confine-run.sh").exists()
    assert not (tmp_path / ".claude" / "settings.hooks.example.json").exists()
    on = ms._emit_privilege_artifacts(tmp_path / "on", "claude", {"privilege_profile": "confined"}, dry_run=True)
    stubs = {n for n in _names(on) if n.endswith((".txt", "management-authority.json"))}
    assert stubs and stubs <= _names(written)  # Rule 12: never fewer stubs


def test_cooperative_on_projects_a_boundary(tmp_path, linux):
    priv = {"privilege_profile": "cooperative", "privilege_profile_explicit": True, "os_sandbox": "on"}
    ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False)
    assert (tmp_path / "sandbox" / "confine-run.sh").is_file()
    assert "sandbox" in json.loads((tmp_path / ".claude" / "settings.hooks.example.json").read_text())


def test_write_roots_check_follows_the_effective_state(tmp_path):
    priv = {"privilege_profile": "confined", "os_sandbox": "off", "workspace_write_roots": ["/elsewhere"]}
    ms._check_projected_write_roots(tmp_path, priv, ["claude"], None)  # no boundary consumes the roots
    with pytest.raises(ValueError, match="acceptance"):
        ms._check_projected_write_roots(tmp_path, {**priv, "os_sandbox": "on"}, ["claude"], None)


def test_governed_sync_off_is_refused_and_consumes_nothing(tmp_path, linux, stub_ed25519):
    team = _team(tmp_path)  # .claude/agents, enforce_decision_signing on disk
    priv = {"privilege_profile": "confined", "privilege_profile_explicit": True, "os_sandbox": "off"}
    with pytest.raises(ValueError, match="sync refused.*sandbox-os-off:claude"):
        ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=True)
    log = _log(team, _row("sandbox-os-off:claude"))
    before = log.read_text()
    ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False)
    assert log.read_text() == before  # checked with consume=False


def test_os_sandbox_round_trips_through_team_cai(tmp_path, linux):
    ad = tmp_path / ".claude" / "agents"
    ad.mkdir(parents=True)
    (ad / "orchestrator.md").write_text(
        "---\nname: orchestrator\ndescription: The orchestrator.\n---\n\n# Orchestrator\n\nBody.\n", encoding="utf-8")
    _brief(tmp_path, {"project_name": "T", "privilege_profile": "cooperative", "os_sandbox": "on"})
    ms.sync_init(tmp_path, pin="claude", frameworks=["claude", "goose"])
    team = json.loads((tmp_path / ".agentteams" / "canonical" / "team.cai.json").read_text())
    assert team["privilege"]["os_sandbox"] == "on"
    assert (tmp_path / "sandbox" / "confine-run.sh").is_file()


def test_cai_schema_lists_the_field():
    schema = json.loads((REPO / "agentteams" / "schemas" / "agent-cai.schema.json").read_text())
    assert "os_sandbox" in schema["properties"]["privilege"]["description"]


# --- wave-2 @security S1: the projection gate engages on any on -> off over the previous state ---------------------


def _example_evidence(root: Path) -> None:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.hooks.example.json").write_text(json.dumps({"sandbox": {"enabled": True}}))


def test_governed_sync_cooperative_over_evidence_is_refused(tmp_path, linux):
    _team(tmp_path)
    _example_evidence(tmp_path)
    priv = {"privilege_profile": "cooperative", "privilege_profile_explicit": True}  # no os_sandbox key
    with pytest.raises(ValueError, match="sync refused.*sandbox-os-off:claude"):
        ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=True)


def test_governed_sync_cooperative_over_evidence_with_a_signed_decision_proceeds(tmp_path, linux, stub_ed25519):
    team = _team(tmp_path)
    _example_evidence(tmp_path)
    log = _log(team, _row("sandbox-os-off:claude"))
    before = log.read_text()
    priv = {"privilege_profile": "cooperative", "privilege_profile_explicit": True}
    ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False)
    assert log.read_text() == before  # checked, never consumed


def test_ungoverned_sync_cooperative_over_evidence_prints_the_notice(tmp_path, linux, capsys):
    _team(tmp_path, governed=False)
    _example_evidence(tmp_path)
    priv = {"privilege_profile": "cooperative", "privilege_profile_explicit": True}
    ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=True)
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err  # the dry pre-check prints nothing
    ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False)
    assert "SANDBOX RELAXATION: os sandbox OFF for claude (reason: profile:cooperative)" in capsys.readouterr().err


def test_sync_without_evidence_is_unchanged(tmp_path, linux, capsys):
    _team(tmp_path)  # governed, no record, no evidence
    priv = {"privilege_profile": "cooperative", "privilege_profile_explicit": True}
    ms._emit_privilege_artifacts(tmp_path, "claude", priv, dry_run=False)
    assert "SANDBOX RELAXATION" not in capsys.readouterr().err
