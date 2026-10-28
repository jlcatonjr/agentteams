"""Plan B1-B4: the daily summary's Remediation readiness section, its two notifications, and the schedule drift.

2026-10-26: a reinstall left the tools container without a self-test for the new version, and the agent's blanket
escalation then refused every executor op; neither showed in the summary. :mod:`agentteams.vulnscan.remediation_status`
reports both, plus grant expiry and servers no grant names.
"""

from __future__ import annotations

import plistlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import escalations, grant, notify, remediation_status, schedule, subproc_confine

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=UTC)


def _grant(gid: str, ops: list[str], days: float, hosts: list[str] | None = None) -> dict[str, Any]:
    return {"grant_id": gid, "ops": ops, "expires": (NOW + timedelta(days=days)).isoformat(), "hosts": hosts or [],
            "repo_roots": []}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    w: dict[str, Any] = {"grants": [], "problems": [], "escalations": [], "tools": "", "drift": ""}
    monkeypatch.setattr(grant, "load_active", lambda now=None: (w["grants"], w["problems"]))

    def opened() -> list[escalations.Escalation]:
        if isinstance(w["escalations"], Exception):
            raise w["escalations"]
        return w["escalations"]
    monkeypatch.setattr(escalations, "open_escalations", opened)
    monkeypatch.setattr(subproc_confine, "unavailable_reason", lambda tag: w["tools"])
    monkeypatch.setattr(schedule, "drift", lambda cfg: w["drift"])
    return w


def _esc(eid: str, scope: str = "*") -> escalations.Escalation:
    return escalations.Escalation(id=eid, kind="other", scope=scope, evidence="x", request_id="r", opened_ts="t", run_id="run")


def test_plan_only_use_is_never_blocked(world: dict[str, Any]) -> None:
    world["tools"] = "no passing tools-selftest"
    world["escalations"] = RuntimeError("the ledger must not be read for plan-only use (it would create its key)")
    st = remediation_status.collect({"servers": ["vk-prod"]}, now=NOW)
    assert st["configured"] is False and st["blocked"] == [] and st["tools_container"] == ""
    assert "remediation is not configured" in remediation_status.markdown(st)
    assert st["agentteams_version"]


def test_the_2026_10_26_blockers_are_reported(world: dict[str, Any]) -> None:
    world["grants"] = [_grant("vg-55fa6a15e1a32b72", ["open-pr", "stage-fix"], 29),
                       _grant("vg-8534420cd9606b6e", ["server-audit", "server-dryrun"], 3, ["vk-prod", "vk-api"])]
    world["tools"] = "no passing tools-selftest for agentteams-vulnscan-tools:1.1.0.dev0 and the current container flags"
    world["escalations"] = [_esc("ve-c40b046ff779fa5b")]
    world["drift"] = "the installed daily job runs at 09:00 but vulnscan.json's schedule says 07:30"
    st = remediation_status.collect({"agent": {"enabled": True}, "servers": ["vk-prod", "vk-api", "vk-bastion"]},
                                    now=NOW)
    assert st["configured"] and len(st["blocked"]) == 2
    assert st["blocked"][0].startswith("tools container: no passing tools-selftest")
    assert st["blocked"][1] == "escalation ve-c40b046ff779fa5b (other) blocks everything"
    assert st["grants_expiring"] == [{"grant_id": "vg-8534420cd9606b6e", "days_left": 3}]
    assert st["servers_uncovered"] == ["vk-bastion"]
    md = remediation_status.markdown(st)
    assert "**blocked**" in md and "vk-bastion" in md and "**renew it**" in md and "09:00" in md


def test_no_valid_grant_or_an_unreadable_ledger_blocks(world: dict[str, Any]) -> None:
    world["problems"] = ["3 valid grants exceed the aggregate cap of 2: all refused"]
    world["escalations"] = ValueError("ledger MAC mismatch at seq 12")
    st = remediation_status.collect({}, now=NOW)
    assert st["configured"]   # a grant exists, even though none is valid
    assert any("ledger unreadable" in b for b in st["blocked"])
    assert "no valid grant: the executor refuses every op" in st["blocked"]
    assert "aggregate cap" in remediation_status.markdown(st)


def test_notifications_are_fixed_text(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(notify, "send", lambda cfg, key, **f: sent.append((key, f)) or True)
    world["grants"] = [_grant("vg-55fa6a15e1a32b72", ["stage-fix"], 2)]
    world["escalations"] = [_esc("ve-c40b046ff779fa5b", "/repo")]
    st = remediation_status.collect({}, now=NOW)
    remediation_status.notify({}, st)
    assert sent == [("remediation-blocked", {"count": 1}), ("grant-expiring", {"id": "vg-55fa6a15e1a32b72", "days": 2})]
    for key, fields in sent:   # both render with identifiers and counts only
        assert notify.render(key, **fields)
    assert "vulnscan grant list" in notify.render("grant-expiring", id="vg-55fa6a15e1a32b72", days=2)


def test_installed_time_and_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "_mechanism", lambda: "launchd")
    monkeypatch.setattr(schedule, "_agent_path", lambda label: tmp_path / f"{label}.plist")
    assert schedule.installed_time() is None and schedule.drift({"schedule": {"hour": 7, "minute": 30}}) == ""
    (tmp_path / f"{schedule.LABEL}.plist").write_bytes(plistlib.dumps(
        {"Label": schedule.LABEL, "StartCalendarInterval": {"Hour": 9, "Minute": 0}}))
    assert schedule.installed_time() == (9, 0)
    msg = schedule.drift({"schedule": {"hour": 7, "minute": 30}})
    assert "runs at 09:00" in msg and "says 07:30" in msg and "schedule install --apply" in msg
    assert schedule.drift({"schedule": {"hour": 9, "minute": 0}}) == ""
    assert "WARNING: the installed daily job runs at 09:00" in schedule.status({"schedule": {"hour": 7, "minute": 30}})


def test_installed_time_from_cron_and_systemd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schedule, "_mechanism", lambda: "cron")

    class Done:
        stdout = f"5 4 * * * echo other\n15 6 * * * env X=1 python -m agentteams.vulnscan daily {schedule.CRON_TAG}\n"
    monkeypatch.setattr(schedule.subprocess, "run", lambda *a, **k: Done())
    assert schedule.installed_time() == (6, 15)
    monkeypatch.setattr(schedule, "_mechanism", lambda: "systemd")
    monkeypatch.setattr(schedule.Path, "home", lambda: tmp_path)
    timer = tmp_path / ".config" / "systemd" / "user" / f"{schedule.UNIT}.timer"
    timer.parent.mkdir(parents=True)
    timer.write_text(schedule.systemd_units(7, 30)[1], encoding="utf-8")
    assert schedule.installed_time() == (7, 30)


def test_the_daily_summary_carries_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             world: dict[str, Any]) -> None:
    """summary.md gets the section, summary.json the ``remediation`` key, the heartbeat the version (B1, B4)."""
    import json

    from agentteams.vulnscan import daily
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    monkeypatch.setattr(daily, "_collect", lambda cfg, today, run_dir, total, staged: None)
    monkeypatch.setattr(daily.notify, "send", lambda *a, **k: True)
    world["grants"] = [_grant("vg-55fa6a15e1a32b72", ["stage-fix"], 2)]
    _report, _code, run = daily.run_daily({**daily.DEFAULT_CONFIG, "local_runtimes": False}, today="2026-10-27")
    assert "## Remediation readiness" in (run / "summary.md").read_text(encoding="utf-8")
    data = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert [g["grant_id"] for g in data["remediation"]["grants_expiring"]] == ["vg-55fa6a15e1a32b72"]
    beat = json.loads(daily.heartbeat_path().read_text(encoding="utf-8"))
    assert beat["agentteams_version"] == remediation_status.version() and "remediation_blocked" in beat


def test_summary_sections_survive_unreadable_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import daily, handoff, server_fixplan

    def broken(*a: Any, **k: Any) -> Any:
        raise OSError("disk gone")
    monkeypatch.setattr(handoff, "repos_with_staged_fixes", broken)
    monkeypatch.setattr(server_fixplan, "write", broken)
    assert "could not read the handoff state" in daily._handoff_section({})
    assert "could not write them: OSError" in daily._server_plans({"servers": ["vk-api"]}, tmp_path, daily.Report(target="daily"))
