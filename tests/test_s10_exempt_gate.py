"""The Rule 15 gate for Rule S-10's local-repository carve-out (@security condition 3, 2026-11-03): listing a
repository in the brief is not enough in a governed workspace; it needs the operator's Ed25519-signed decision."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from agentteams.cli import exception_registry as er
from agentteams.cli import s10_exempt_gate as gate
from tests._decision_gate_helpers import _log, _row, _team

_T0 = datetime(2026, 11, 3, tzinfo=UTC)
_REPO = "jlcatonjr/agentteams"
_ACTION = "s10-cooldown-exempt:jlcatonjr/agentteams"


def _registry(team) -> list[dict]:
    return json.loads((team / er.EXCEPTION_REGISTRY_REL).read_text(encoding="utf-8"))["exceptions"]


def test_ungoverned_renders_the_listed_repositories_with_a_notice(tmp_path, capsys) -> None:
    team = _team(tmp_path, governed=False)
    assert gate.authorized_exempt_repos(team, [_REPO, "bare", "x/y"], consume=True) == [_REPO, "x/y"]
    assert "not governed" in capsys.readouterr().err


def test_governed_without_a_decision_leaves_it_out_and_names_the_row(tmp_path, capsys) -> None:
    team = _team(tmp_path)
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []
    err = capsys.readouterr().err
    assert "NOT exempted" in err and f"scope={_ACTION}" in err and "--sign-decision" in err


def test_an_hmac_row_is_valid_but_insufficient(tmp_path, stub_ed25519, capsys) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, scheme=""))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []
    assert "INSUFFICIENT" in capsys.readouterr().err


def test_scope_and_effect_must_be_exactly_the_action(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION, scope="s10-cooldown-exempt"))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []
    team2 = _team(tmp_path / "b")
    _log(team2, _row(_ACTION, relaxes=f"{_ACTION};s10-cooldown-exempt:jlcatonjr/other"))
    assert gate.authorized_exempt_repos(team2, [_REPO], consume=True) == []


def test_a_halt_on_the_family_blocks_it(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION), _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-02"))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True) == []


def test_a_signed_decision_registers_a_bounded_exception_that_later_renders_reuse(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == [_REPO]
    assert "yes" in log.read_text()  # consumed
    [entry] = _registry(team)
    assert entry["action"] == _ACTION and entry["status"] == "active" and entry["relaxing"] is True
    assert datetime.fromisoformat(entry["expires"]) == _T0 + timedelta(days=gate.S10_EXEMPT_TTL_DAYS)
    # A later render: the row is spent, the registry entry carries it.
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0 + timedelta(days=30)) == [_REPO]
    # After the sunset it lapses: the cooldown applies again until a fresh decision is signed.
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0 + timedelta(days=91)) == []


def test_a_dry_run_or_check_writes_nothing(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    before = log.read_bytes()
    assert gate.authorized_exempt_repos(team, [_REPO], consume=False, now=_T0) == [_REPO]
    assert log.read_bytes() == before and not (team / er.EXCEPTION_REGISTRY_REL).exists()


def test_only_the_signed_repository_is_exempted(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_exempt_repos(team, [_REPO, "jlcatonjr/researchteam"], consume=True, now=_T0) == [_REPO]


def test_a_halt_after_registration_removes_it_at_once(tmp_path, stub_ed25519) -> None:
    team = _team(tmp_path)
    _log(team, _row(_ACTION))
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == [_REPO]
    _log(team, _row(_ACTION, verdict="HALT", stamp="2026-11-04"))   # on the instance
    assert gate.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0 + timedelta(days=1)) == []
    team2 = _team(tmp_path / "b")
    _log(team2, _row(_ACTION))
    assert gate.authorized_exempt_repos(team2, [_REPO], consume=True, now=_T0) == [_REPO]
    _log(team2, _row("s10-cooldown-exempt", verdict="HALT", stamp="2026-11-04"))   # on the family
    assert gate.authorized_exempt_repos(team2, [_REPO], consume=True, now=_T0 + timedelta(days=1)) == []


def test_case_only_duplicates_render_once(tmp_path) -> None:
    team = _team(tmp_path, governed=False)
    assert gate.authorized_exempt_repos(team, ["JLCatonJr/AgentTeams", _REPO], consume=False) == ["JLCatonJr/AgentTeams"]
