"""agentteams#595: a carried-over exception never outlives its signed decision's sunset.

The registry entry's ``expires`` and ``issued`` are unsigned. The bound decision row's ``date`` is signed, so a carry-over
lapses at signed date + the gate's TTL, whatever ``expires`` says (the earlier of the two). The rows below are signed
2026-10-31 (the helper's default), so the sunset is 2027-01-29T00:00Z.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentteams.cli import exception_registry as er
from agentteams.cli import s10_deprecation_gate as dep
from agentteams.cli import s10_exempt_gate as s10
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.sandbox_state import SandboxState
from tests._sandbox_gate_helpers import _log, _row, _team

_T0 = datetime(2026, 11, 1, tzinfo=UTC)
_SUNSET = datetime(2027, 1, 29, tzinfo=UTC)   # 2026-10-31 (signed) + 90 days, midnight UTC
_REPO = "jlcatonjr/baseAgent"
_S10_ACTION = s10.action_for(_REPO)


def _st(os: bool = True) -> SandboxState:
    return SandboxState(os=os, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=(),
                        wasm_grants={}, model_substrate=None)


def _os_off(before: bool) -> list[gate.Relaxation]:
    return gate.plan_relaxations(_st(os=not before), _st(os=False), framework_id="claude", profile_would_emit=True)


def _registered(tmp_path):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude"))
    gate.authorize(team, _os_off(before=False), consume=True, now=_T0)
    return team


def _mutate(team, **changes) -> None:
    registry = er.load_registry(team)
    registry["exceptions"][0].update(changes)
    er.write_registry(team, registry)


def test_registration_counts_the_sunset_from_the_signed_date(tmp_path, stub_ed25519):
    team = _registered(tmp_path)
    [entry] = er.list_exceptions(team)
    assert datetime.fromisoformat(entry["expires"]) == _SUNSET


def test_an_expires_pushed_forward_does_not_extend_it(tmp_path, stub_ed25519, capsys):
    team = _registered(tmp_path)
    _mutate(team, expires="2030-01-01T00:00:00+00:00")
    later = _os_off(before=True)
    one_minute_before = _SUNSET - timedelta(minutes=1)
    assert gate.authorize(team, later, consume=True, now=one_minute_before)[0]["authorization"] == "active-exception"
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, later, consume=True, now=_SUNSET)   # day 90, 00:00 UTC
    assert "lapsed at 2027-01-29 (90 days after its signed decision)" in capsys.readouterr().err


def test_a_legacy_entry_registered_late_is_bounded_not_refused(tmp_path, stub_ed25519):
    # Before #595 an entry recorded expires = registration + 90 days, past the signed sunset by its lag.
    team = _registered(tmp_path)
    _mutate(team, issued="2026-11-10T00:00:00+00:00", expires="2027-02-08T00:00:00+00:00")
    later = _os_off(before=True)
    assert gate.authorize(team, later, consume=True, now=_SUNSET - timedelta(days=1))[0]["authorization"] == \
        "active-exception"
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, later, consume=True, now=_SUNSET + timedelta(days=1))


@pytest.mark.parametrize("issued,why", [
    ("2026-10-01T00:00:00+00:00", "before its decision row was signed"),
    ("not-a-time", "unreadable issued time"),
])
def test_an_entry_issued_before_its_decision_or_unreadably_is_refused(tmp_path, stub_ed25519, capsys, issued, why):
    team = _registered(tmp_path)
    _mutate(team, issued=issued)
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, _os_off(before=True), consume=True, now=_T0 + timedelta(days=1))
    assert why in capsys.readouterr().err


def test_issued_within_the_skew_of_the_signed_date_is_accepted(tmp_path, stub_ed25519):
    team = _registered(tmp_path)
    _mutate(team, issued="2026-10-30T12:00:00+00:00")   # half a day before the signed date: clock skew
    assert gate.authorize(team, _os_off(before=True), consume=True, now=_T0 + timedelta(days=1))[0][
        "authorization"] == "active-exception"


@pytest.mark.parametrize("row,why", [
    ({"date": "yesterday"}, "unreadable date"),
    ({}, "unreadable date"),
    ({"date": "2026-11-05"}, "in the future"),
    ({"date": "2026-08-01"}, "lapsed at 2026-10-30"),
])
def test_the_signed_lapse_reason(row, why):
    assert why in (gate._signed_lapse_reason(row, _T0, 90) or "")


def test_the_signed_lapse_reason_holds_inside_the_window():
    assert gate._signed_lapse_reason({"date": "2026-10-31"}, _SUNSET - timedelta(seconds=1), 90) is None
    assert gate._signed_lapse_reason({"timestamp": "2026-10-31T00:00:00"}, _T0, 90) is None   # naive = UTC


def test_a_stale_new_row_gets_no_fresh_sunset_in_the_sandbox_gate(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row("sandbox-os-off:claude", stamp="2026-07-01"))
    before = log.read_bytes()
    with pytest.raises(gate.SandboxRelaxationError, match="the operator re-signs it"):
        gate.authorize(team, _os_off(before=False), consume=True, now=_T0)
    assert log.read_bytes() == before and not er.list_exceptions(team)   # nothing consumed or registered


def test_the_s10_exemption_is_bounded_the_same_way(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row(_S10_ACTION))
    assert s10.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == [_REPO]
    [entry] = er.list_exceptions(team)
    assert datetime.fromisoformat(entry["expires"]) == _SUNSET
    _mutate(team, expires="2030-01-01T00:00:00+00:00")
    assert s10.authorized_exempt_repos(team, [_REPO], consume=True, now=_SUNSET - timedelta(days=1)) == [_REPO]
    assert s10.authorized_exempt_repos(team, [_REPO], consume=True, now=_SUNSET) == []


def test_a_stale_new_s10_row_is_refused(tmp_path, stub_ed25519, capsys):
    team = _team(tmp_path)
    _log(team, _row(_S10_ACTION, stamp="2026-07-01"))
    assert s10.authorized_exempt_repos(team, [_REPO], consume=True, now=_T0) == []
    assert "the operator re-signs it" in capsys.readouterr().err
    assert not er.list_exceptions(team)


def test_the_deprecation_gate_keeps_its_messages():
    assert dep._lapse_reason(None, _T0) == "is not bound to a row in the decisions log"
    assert dep._lapse_reason({"date": "2026-08-01"}, _T0) == "lapsed at 2026-10-30 (90 days after its signed decision)"
    assert dep._signed_lapse_reason is gate._signed_lapse_reason


def test_the_deprecation_gate_end_to_end(tmp_path, stub_ed25519, capsys):
    team = _team(tmp_path)
    _log(team, _row(dep.DEPRECATION_ACTION))
    assert dep.authorized_deprecation(team, True, consume=True, now=_T0) == "signed"
    _mutate(team, expires="2030-01-01T00:00:00+00:00")   # pushed forward: still bounded by the signed date
    assert dep.authorized_deprecation(team, True, consume=True, now=_SUNSET - timedelta(days=1)) == "signed"
    assert dep.authorized_deprecation(team, True, consume=True, now=_SUNSET) is None
    stale = _team(tmp_path / "stale")
    _log(stale, _row(dep.DEPRECATION_ACTION, stamp="2026-07-01"))
    assert dep.authorized_deprecation(stale, True, consume=True, now=_T0) is None
    assert "the operator re-signs it" in capsys.readouterr().err
    assert not er.list_exceptions(stale)
