"""agentteams.vulnscan.daily: config validation, stale warning, run lock, write failures, notifications and
``apply-worktree`` staging (round-2 items G3, G4, G8, G9, G13, G14, G16, G29, G32, G34, X13, M2, M3).

Real git (a local bare remote) for the worktree tests; OSV / PyPI answers come from the shared ``Feed`` fake.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import _http, agent_run, daily, executor, fix, notify, osv, publish, runtimes
from agentteams.vulnscan.report import EXIT_COMPROMISE, Finding, Report

from tests.test_vulnscan_core import Feed, _adv

GIT = shutil.which("git")


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated vulnscan config, state and server-kit dirs."""
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    return tmp_path


@pytest.fixture
def feed(monkeypatch: pytest.MonkeyPatch) -> Feed:
    """A canned feed: PyJWT 2.3.0 is vulnerable, 2.15.0 (100 days old) fixes it."""
    f = Feed()
    monkeypatch.setattr(_http, "fetch_json", f)
    osv._DETAILS.clear()
    runtimes._CACHE.clear()
    f.vulns = {("PyPI", "PyJWT", "2.3.0"): ["G1"]}
    f.records = {"G1": _adv("G1", "PyPI", "PyJWT", "0", "2.15.0", "CRITICAL")}
    now = dt.datetime.now(dt.UTC)
    f.urls["https://pypi.org/pypi/PyJWT/json"] = {"releases": {
        v: [{"upload_time_iso_8601": (now - dt.timedelta(days=age)).isoformat().replace("+00:00", "Z"),
             "yanked": False}] for v, age in {"2.15.0": 100}.items()}}
    return f


def _write_config(data: Any) -> Path:
    path = daily.paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def _write_heartbeat(data: Any) -> None:
    daily.heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    daily.heartbeat_path().write_text(json.dumps(data), encoding="utf-8")


def _cfg(**over: Any) -> dict[str, Any]:
    return {**daily.DEFAULT_CONFIG, "local_runtimes": False, "notify": "none", **over}


# ---------------------------------------------------------------- G3: stale_warning never raises

_NOW = dt.datetime.now(dt.UTC)


@pytest.mark.parametrize(("heartbeat", "config", "expect"), [
    ({"last_run": (_NOW - dt.timedelta(hours=50)).replace(tzinfo=None).isoformat()}, {}, "50h ago"),   # naive = UTC
    ({"last_run": 1_700_000_000}, {}, "unreadable"),
    ({"last_run": None}, {}, "unreadable"),
    ({"last_run": "yesterday"}, {}, "unreadable"),
    ([{"last_run": _NOW.isoformat()}], {}, "unreadable"),
    ("just a string", {}, "unreadable"),
    ({"last_run": _NOW.isoformat(), "exit": None}, {}, ""),
    ({"last_run": _NOW.isoformat(), "exit": [3]}, {}, ""),
    ({"last_run": (_NOW - dt.timedelta(hours=30)).isoformat()}, {"stale_after_hours": None}, "30h ago"),
    ({"last_run": (_NOW - dt.timedelta(hours=30)).isoformat()}, {"stale_after_hours": "x"}, "30h ago"),
    ({"last_run": _NOW.isoformat()}, "{not json", ""),
    ({"last_run": _NOW.isoformat()}, [1, 2], ""),
])
def test_stale_warning_never_raises_on_odd_heartbeat_or_config(xdg: Path, heartbeat: Any, config: Any,
                                                              expect: str) -> None:
    _write_config(config)
    _write_heartbeat(heartbeat)
    warning = daily.stale_warning()
    assert isinstance(warning, str) and expect in warning
    if not expect:
        assert warning == ""


def test_stale_warning_survives_an_undecodable_heartbeat(xdg: Path) -> None:
    _write_config({})
    daily.heartbeat_path().parent.mkdir(parents=True, exist_ok=True)
    daily.heartbeat_path().write_bytes(b"\xff\xfe\x00")
    assert "unreadable" in daily.stale_warning()


# ---------------------------------------------------------------- G4 / G8 / G9: config shape and types

@pytest.mark.parametrize(("raw", "fragment"), [
    ("{not json", "not valid JSON"),
    ("[1, 2]", "expected a JSON object"),
    ('"text"', "expected a JSON object"),
    ({"repo_roots": "~/code"}, "repo_roots"),
    ({"repo_roots": [1]}, "repo_roots"),
    ({"servers": "web1"}, "servers"),
    ({"servers": {"web1": 1}}, "servers"),
    ({"fail_on": "severe"}, "fail_on"),
    ({"fail_on": 3}, "fail_on"),
    ({"cooldown_days": "14"}, "cooldown_days"),
    ({"cooldown_days": True}, "cooldown_days"),
    ({"cooldown_days": -1}, "cooldown_days"),
    ({"cooldown_days": 1.5}, "cooldown_days"),
    ({"include_worktrees": "false"}, "include_worktrees"),
    ({"include_dev": 1}, "include_dev"),
    ({"local_runtimes": "no"}, "local_runtimes"),
    ({"allow_major": "true"}, "allow_major"),
    ({"fix_mode": "apply"}, "fix_mode"),
    ({"fix_mode": None}, "fix_mode"),
    ({"unscored_as": ["high"]}, "unscored_as"),
    ({"scan_workers": 0}, "scan_workers"),
    ({"scan_workers": 9}, "scan_workers"),
    ({"scan_workers": "4"}, "scan_workers"),
    ({"scan_workers": True}, "scan_workers"),
    ({"scan_workers": 2.5}, "scan_workers"),
    ({"notify": 1}, "notify"),
    ({"notify": "email"}, "notify"),
    ({"agent": "yes"}, "agent"),
    ({"agent": ["enabled"]}, "agent"),
    ({"agent": {"enabled": "false"}}, "agent.enabled"),
    ({"agent": {"enabled": 1}}, "agent.enabled"),
    ({"stale_after_hours": "26"}, "stale_after_hours"),
    ({"stale_after_hours": None}, "stale_after_hours"),
    ({"stale_after_hours": 0}, "stale_after_hours"),
    ({"stale_after_hours": True}, "stale_after_hours"),
    ({"stale_after_hours": float("nan")}, "stale_after_hours"),
    ({"schedule": "07:30"}, "schedule"),
    ({"schedule": {"hour": 24}}, "schedule.hour"),
    ({"schedule": {"hour": -1}}, "schedule.hour"),
    ({"schedule": {"hour": "7"}}, "schedule.hour"),
    ({"schedule": {"hour": 7.5}}, "schedule.hour"),
    ({"schedule": {"hour": True}}, "schedule.hour"),
    ({"schedule": {"minute": 60}}, "schedule.minute"),
    ({"schedule": {"minute": None}}, "schedule.minute"),
    ({"governance_dir": 5}, "governance_dir"),
])
def test_load_config_refuses_malformed_files_and_wrong_types(xdg: Path, raw: Any, fragment: str) -> None:
    path = _write_config(raw)
    with pytest.raises(ValueError, match=re.escape(fragment)) as info:
        daily.load_config()
    assert str(path) in str(info.value)


def test_load_config_refuses_an_undecodable_file(xdg: Path) -> None:
    path = _write_config("")
    path.write_bytes(b"\xff\xfe{}")
    with pytest.raises(ValueError, match="not valid JSON"):
        daily.load_config()


@pytest.mark.parametrize("data", [
    {},
    {"repo_roots": ["~/code"], "servers": ["web1"], "fail_on": "any", "cooldown_days": 30, "include_worktrees": True,
     "fix_mode": "apply-worktree", "notify": "command:/usr/bin/true", "stale_after_hours": 12.5,
     "agent": {"enabled": True, "team_dir": "/t"}, "schedule": {"hour": 0, "minute": 59}},
    {"agent": None, "notify": "desktop", "fail_on": "none"},
])
def test_load_config_accepts_valid_shapes(xdg: Path, data: dict[str, Any]) -> None:
    _write_config(data)
    cfg = daily.load_config()
    for key, value in data.items():
        if key != "schedule":
            assert cfg[key] == value


@pytest.mark.parametrize(("agent", "key"), [
    ({"budget_usd": 0}, "agent.budget_usd"),
    ({"budget_usd": "2"}, "agent.budget_usd"),
    ({"max_minutes": float("inf")}, "agent.max_minutes"),
    ({"max_tokens_total": 1.5}, "agent.max_tokens_total"),
    ({"max_unmetered_calls": 0}, "agent.max_unmetered_calls"),
    ({"allow_unmetered": "no"}, "agent.allow_unmetered"),
    ({"debrief_budget_usd": -1}, "agent.debrief_budget_usd"),
    ({"debrief_max_minutes": None}, "agent.debrief_max_minutes"),
    ({"debrief_max_tokens": True}, "agent.debrief_max_tokens"),
    ({"team_dir": 5}, "agent.team_dir"),
    ({"key_file": ["k"]}, "agent.key_file"),
    ({"model": ""}, "agent.model"),
    ({"model": 3}, "agent.model"),
    ({"upstream": "ftp://proxy:21"}, "agent.upstream"),
    ({"upstream": "http://"}, "agent.upstream"),
    ({"upstream": 8791}, "agent.upstream"),
    ({"framework": None}, "agent.framework"),
    ({"recipe": {}}, "agent.recipe"),
    ({"image": 1}, "agent.image"),
    ({"command": "goose run"}, "agent.command"),
    ({"command": ["goose", 1]}, "agent.command"),
    ({"framework": "grok"}, "agent.framework"),        # known, but its driver is not in this release
    ({"framework": "codex"}, "agent.framework"),       # the default model is not an OpenAI model (revision 13)
    ({"framework": "goose2"}, "agent.framework"),
])
def test_load_config_validates_every_agent_value(xdg: Path, agent: dict[str, Any], key: str) -> None:
    """Item 9: a bad agent.* value fails at load, naming the key (the CLI then exits 2)."""
    path = _write_config({"agent": agent})
    with pytest.raises(ValueError, match=re.escape(key)) as info:
        daily.load_config()
    assert str(path) in str(info.value)


def test_load_config_accepts_a_fully_specified_agent_block(xdg: Path) -> None:
    agent = {"enabled": True, "team_dir": "/t", "key_file": "/k", "model": "m/x", "upstream": "https://proxy.example",
             "framework": "goose", "recipe": "r.yaml", "image": "", "command": ["goose", "run"], "budget_usd": 1,
             "max_minutes": 10, "max_tokens_total": 5, "max_unmetered_calls": 2, "allow_unmetered": True,
             "debrief_budget_usd": 0.5, "debrief_max_minutes": 5, "debrief_max_tokens": 100}
    _write_config({"agent": agent})
    assert daily.load_config()["agent"] == agent


def test_cli_exits_2_naming_a_bad_agent_value(xdg: Path, capsys: pytest.CaptureFixture) -> None:
    """agent-chat (and every config-loading command) exits 2 with the reason, no traceback (items 8, 9)."""
    from agentteams.vulnscan import __main__ as cli

    _write_config({"agent": {"debrief_max_tokens": "lots"}})
    assert cli.main(["agent-chat"]) == 2
    err = capsys.readouterr().err
    assert "is unusable" in err and "agent.debrief_max_tokens" in err and "Traceback" not in err


# ---------------------------------------------------------------- G34: old config shapes

def test_old_config_without_newer_keys_gets_every_default(xdg: Path, feed: Feed) -> None:
    _write_config({"repo_roots": [], "servers": [], "fail_on": "high"})       # a first-release config
    cfg = daily.load_config()
    assert set(daily.DEFAULT_CONFIG) <= set(cfg)
    assert cfg["agent"] == daily.DEFAULT_CONFIG["agent"] and cfg["schedule"] == {"hour": 7, "minute": 30}
    _, code, run_dir = daily.run_daily({**cfg, "local_runtimes": False, "notify": "none"}, today="2026-10-22")
    assert code == 0 and (run_dir / "summary.md").is_file()


@pytest.mark.parametrize(("schedule", "want"), [({"hour": 6}, {"hour": 6, "minute": 30}),
                                                ({"minute": 5}, {"hour": 7, "minute": 5}),
                                                ({}, {"hour": 7, "minute": 30})])
def test_partial_schedule_is_filled_from_the_defaults(xdg: Path, schedule: dict[str, int],
                                                      want: dict[str, int]) -> None:
    _write_config({"schedule": schedule})
    assert daily.load_config()["schedule"] == want
    assert daily.DEFAULT_CONFIG["schedule"] == {"hour": 7, "minute": 30}     # the default is never mutated


# ---------------------------------------------------------------- G13: run lock + atomic heartbeat

def test_a_second_daily_run_exits_clearly_while_one_holds_the_lock(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 capsys: pytest.CaptureFixture[str]) -> None:
    collected: list[str] = []
    monkeypatch.setattr(daily, "_collect", lambda *a, **k: collected.append("ran"))
    daily.run_lock_path().parent.mkdir(parents=True, exist_ok=True)
    with open(daily.run_lock_path(), "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report, code, run_dir = daily.run_daily(_cfg(), today="2026-10-22")
    assert code == 3 and collected == [] and "another daily run" in report.feed_errors[0]
    assert "another daily run" in capsys.readouterr().err
    assert not daily.heartbeat_path().exists() and not (run_dir / "summary.md").exists()
    assert run_dir.parent == daily.run_lock_path().parent                      # next to the heartbeat
    _, code, _ = daily.run_daily(_cfg(), today="2026-10-22")                    # released → runs
    assert code == 0 and collected == ["ran"]


def test_heartbeat_is_replaced_atomically(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_heartbeat({"last_run": "2026-10-21T07:30:00+00:00", "exit": 0})
    before = daily.heartbeat_path().read_bytes()

    def failing_replace(src: str | Path, dst: str | Path) -> None:
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(daily.os, "replace", failing_replace)
    _, code, _ = daily.run_daily(_cfg(), today="2026-10-22")
    assert code == 3 and daily.heartbeat_path().read_bytes() == before          # never truncated / half written
    assert not [p for p in daily.heartbeat_path().parent.iterdir() if p.name.startswith(".heartbeat")]


# ---------------------------------------------------------------- G14: disk full in _finish

class Spy:
    """A notify.send stand-in that renders through the real notify.render (an unsafe field raises)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def __call__(self, cfg: dict[str, Any], key: str, **fields: Any) -> bool:
        self.sent.append((key, notify.render(key, **fields)))
        return True


def test_a_failed_summary_write_notifies_and_exits_3(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    spy = Spy()
    monkeypatch.setattr(daily.notify, "send", spy)
    (daily.paths.state_dir() / "2026-10-22" / "summary.md").mkdir(parents=True)   # the write fails (OSError)
    report, code, _ = daily.run_daily(_cfg(), today="2026-10-22")
    assert code == 3 and any("could not write" in e for e in report.feed_errors)
    assert spy.sent and spy.sent[-1][0] == "run-done" and "partially unscanned" in spy.sent[-1][1]
    assert "could not write" in capsys.readouterr().err


def test_a_failed_write_never_hides_compromise(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy()
    monkeypatch.setattr(daily.notify, "send", spy)
    monkeypatch.setattr(daily, "_collect", lambda cfg, today, run_dir, total, staged: total.findings.append(
        Finding(kind="ioc", target="web1", name="ioc", detail="x")))
    (daily.paths.state_dir() / "2026-10-22" / "summary.json").mkdir(parents=True)
    _, code, _ = daily.run_daily(_cfg(), today="2026-10-22")
    assert code == EXIT_COMPROMISE and "COMPROMISE INDICATORS" in spy.sent[-1][1]


# ---------------------------------------------------------------- G32: malformed previous summary

@pytest.mark.parametrize("bad", ["[]", "{not json", '{"findings": [1]}', '{"findings": "x"}', '"str"'])
def test_malformed_previous_summary_falls_back_with_a_note(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                           bad: str) -> None:
    finding = Finding(kind="package", target="svc", name="PyJWT", version="2.3.0", ecosystem="PyPI",
                      severity=4, ids=["G1"])
    monkeypatch.setattr(daily, "_collect", lambda cfg, today, run_dir, total, staged: total.findings.append(finding))
    daily.run_daily(_cfg(), today="2026-10-20")                                 # a good older run
    (daily.paths.state_dir() / "2026-10-21").mkdir()
    (daily.paths.state_dir() / "2026-10-21" / "summary.json").write_text(bad)
    _, code, run_dir = daily.run_daily(_cfg(), today="2026-10-22")
    md = (run_dir / "summary.md").read_text()
    assert code == 1 and "2026-10-21" in md and "unreadable" in md
    assert "**NEW**" not in md                                                  # compared with 2026-10-20


def test_summary_json_carries_an_integer_schema_version_and_is_read_back(xdg: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 23: summary.json has ``"schema_version": 1``; the next run's diff and review read it as before."""
    from agentteams.vulnscan import review

    finding = Finding(kind="package", target="svc", name="PyJWT", version="2.3.0", ecosystem="PyPI", severity=4)
    monkeypatch.setattr(daily, "_collect", lambda cfg, today, run_dir, total, staged: total.findings.append(finding))
    _, _, first = daily.run_daily(_cfg(), today="2026-10-21")
    data = json.loads((first / "summary.json").read_text())
    assert data["schema_version"] == 1 and type(data["schema_version"]) is int
    assert {"target", "findings", "checked", "feed_errors", "unscannable", "generated_at"} <= set(data)
    _, _, second = daily.run_daily(_cfg(), today="2026-10-22")
    md = (second / "summary.md").read_text()
    assert "unreadable" not in md and "**NEW**" not in md                     # compared with the first run
    assert review.Report.from_dict(data).findings[0].name == "PyJWT"


def test_no_readable_previous_summary_marks_everything_new_with_a_note(xdg: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    finding = Finding(kind="package", target="svc", name="PyJWT", version="2.3.0", ecosystem="PyPI", severity=4)
    monkeypatch.setattr(daily, "_collect", lambda cfg, today, run_dir, total, staged: total.findings.append(finding))
    (daily.paths.state_dir() / "2026-10-21").mkdir(parents=True)
    (daily.paths.state_dir() / "2026-10-21" / "summary.json").write_text("[]")
    _, _, run_dir = daily.run_daily(_cfg(), today="2026-10-22")
    md = (run_dir / "summary.md").read_text()
    assert "**NEW**" in md and "unreadable" in md


# ---------------------------------------------------------------- G29: a notification per daily event

def _inject(code_finding: Finding | None, feed_error: str = "") -> Any:
    def collect(cfg: dict[str, Any], today: str, run_dir: Path, total: Report, staged: list[str]) -> None:
        if code_finding:
            total.findings.append(code_finding)
        if feed_error:
            total.feed_errors.append(feed_error)
    return collect


@pytest.mark.parametrize(("finding", "feed_error", "fail_on", "label"), [
    (Finding(kind="package", target="a", name="p", severity=4, ids=["X"]), "", "high", "action needed"),
    (None, "host web1 unreachable", "high", "partially unscanned"),
    (Finding(kind="ioc", target="web1", name="ioc", detail="d"), "", "high", "COMPROMISE INDICATORS"),
    (Finding(kind="package", target="a", name="p", severity=1, ids=["Y"]), "", "none", "new findings"),
])
def test_run_done_notification_renders_for_every_outcome(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                         finding: Finding | None, feed_error: str, fail_on: str,
                                                         label: str) -> None:
    spy = Spy()
    monkeypatch.setattr(daily.notify, "send", spy)
    monkeypatch.setattr(daily, "_collect", _inject(finding, feed_error))
    daily.run_daily(_cfg(fail_on=fail_on), today="2026-10-22")
    assert [k for k, _ in spy.sent] == ["run-done"] and label in spy.sent[0][1]


def test_every_state_daily_sends_is_one_notify_accepts() -> None:
    assert set(daily.RUN_LABELS.values()) | {daily.NEW_FINDINGS_LABEL} <= notify._STATES
    assert set(agent_run.RUN_STATES) <= notify._STATES


def test_a_clean_run_with_nothing_new_sends_nothing(xdg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = Spy()
    monkeypatch.setattr(daily.notify, "send", spy)
    monkeypatch.setattr(daily, "_collect", _inject(None))
    daily.run_daily(_cfg(), today="2026-10-22")
    assert spy.sent == []


@pytest.mark.parametrize("status", [{"state": "agent-error", "reason": "x"}, {"state": "timed-out"},
                                    {"state": "not-run", "reason": "no key"}, {"reason": "no state at all"},
                                    {"state": "weird\nstate"}, OSError("docker gone")])
def test_agent_not_completed_notification_renders(xdg: Path, monkeypatch: pytest.MonkeyPatch,
                                                  status: dict[str, Any] | Exception) -> None:
    spy = Spy()
    monkeypatch.setattr(daily.notify, "send", spy)
    monkeypatch.setattr(daily, "_collect", _inject(None))

    def run_agent(cfg: dict[str, Any], run_dir: Path) -> dict[str, Any]:
        if isinstance(status, Exception):
            raise status
        return dict(status)
    monkeypatch.setattr(agent_run, "run_agent", run_agent)
    monkeypatch.setattr(daily, "publish_run", lambda cfg, run_dir, run_id: None)
    daily.run_daily(_cfg(agent={"enabled": True}), today="2026-10-22")
    # agent mode on with no grant: the readiness check (remediation_status, plan B2) reports it first
    assert [k for k, _ in spy.sent] == ["remediation-blocked", "agent-not-completed"]


@pytest.mark.parametrize(("result", "key"), [({"status": "done"}, "publish-done"),
                                             ({"status": "failed", "detail": "x"}, "publish-failed"),
                                             (None, "publish-failed")])
def test_publish_notifications_render(xdg: Path, monkeypatch: pytest.MonkeyPatch, result: dict[str, Any] | None,
                                      key: str) -> None:
    from agentteams.vulnscan import report_store, review

    spy = Spy()
    monkeypatch.setattr(daily.notify, "send", spy)
    monkeypatch.setattr(publish, "load_active", lambda: ([{"id": "g1"}], []))
    monkeypatch.setattr(publish, "publish", lambda grant, run_dir, run_id, files: result)
    monkeypatch.setattr(review, "build", lambda for_publish=False: "review\n")
    if result is None:
        def broken(run_dir: Path) -> str:
            raise OSError("gone")
        monkeypatch.setattr(report_store, "render_for_review", broken)
    else:
        monkeypatch.setattr(report_store, "render_for_review", lambda run_dir: "report\n")
    run_dir = xdg / "state" / "2026-10-22"
    run_dir.mkdir(parents=True)
    daily.publish_run(_cfg(), run_dir, "r1")
    assert [k for k, _ in spy.sent] == [key]


# ---------------------------------------------------------------- G16 / X13 / M3: apply-worktree staging

def _repo_with_remote(tmp: Path) -> Path:
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp / "repos" / "svc"
    repo.mkdir(parents=True)
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "t@example.com")
    _run(repo, "config", "user.name", "t")
    (repo / "requirements.txt").write_text("PyJWT==2.3.0\nflask>=3\n")
    (repo / "app.py").write_text("print('hi')\n")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "init")
    _run(repo, "remote", "add", "origin", str(remote))
    _run(repo, "push", "-q", "origin", "main")
    _run(repo, "remote", "set-head", "origin", "main")
    return repo


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _wt(repo: Path, date: str = "2026-10-22") -> Path:
    return repo.parent / f"{repo.name}.worktrees" / f"vulnscan-{date}"


needs_git = pytest.mark.skipif(not GIT, reason="git not available")


@needs_git
def test_stage_worktree_fixes_stages_a_validated_pin_change(xdg: Path, feed: Feed,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(xdg)
    validated: list[Path] = []
    real_validate = executor.validate_diff
    monkeypatch.setattr(executor, "validate_diff", lambda wt, base: validated.append(wt) or real_validate(wt, base))
    wt, md = daily.stage_worktree_fixes(repo, _cfg(), "2026-10-22")
    assert wt == str(_wt(repo)) and "2.15.0" in md and validated == [_wt(repo)]
    assert (_wt(repo) / "requirements.txt").read_text() == "PyJWT==2.15.0\nflask>=3\n"
    assert _run(repo, "status", "--porcelain") == "" and _run(_wt(repo), "log", "-1", "--format=%s") == "init\n"


@needs_git
def test_stage_worktree_fixes_ignores_the_cooldown_setting(xdg: Path, feed: Feed,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(xdg)
    seen: list[int] = []
    real = fix.fix_path

    def spy(root: Path, **kw: Any) -> fix.FixResult:
        seen.append(kw["cooldown_days"])
        return real(root, **kw)
    monkeypatch.setattr(daily.fix, "fix_path", spy)
    daily.stage_worktree_fixes(repo, _cfg(cooldown_days=1), "2026-10-22")
    daily.stage_worktree_fixes(repo, _cfg(cooldown_days=30), "2026-10-23")
    assert seen == [0, 0] and executor.REMEDIATION_COOLDOWN_DAYS == 0   # S-10's remediation carve-out


@needs_git
def test_stage_worktree_fixes_refuses_a_change_validate_diff_rejects(xdg: Path, feed: Feed,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(xdg)

    def rogue(root: Path, **kw: Any) -> fix.FixResult:
        (root / "app.py").write_text("import os; os.system('x')\n")                # outside pins/lockfiles
        return fix.FixResult(applied=True)
    monkeypatch.setattr(daily.fix, "fix_path", rogue)
    wt, md = daily.stage_worktree_fixes(repo, _cfg(), "2026-10-22")
    assert wt == "" and "refus" in md and "app.py" in md
    assert not _wt(repo).exists()                                              # the bad worktree is removed
    assert "vulnscan/2026-10-22" not in _run(repo, "branch", "--list")


@needs_git
def test_same_day_rerun_reuses_its_own_clean_worktree(xdg: Path, feed: Feed) -> None:
    repo = _repo_with_remote(xdg)
    first, _ = daily.stage_worktree_fixes(repo, _cfg(), "2026-10-22")
    again, md = daily.stage_worktree_fixes(repo, _cfg(), "2026-10-22")
    assert first == again == str(_wt(repo)) and "not reusing" not in md
    assert (_wt(repo) / "requirements.txt").read_text() == "PyJWT==2.15.0\nflask>=3\n"


@needs_git
@pytest.mark.parametrize("tamper", ["plain-dir", "symlink", "foreign-change", "local-commit", "other-branch"])
def test_same_day_rerun_checks_an_existing_worktree_before_reuse(xdg: Path, feed: Feed, tamper: str) -> None:
    repo = _repo_with_remote(xdg)
    wt = _wt(repo)
    if tamper in ("plain-dir", "symlink"):
        planted = xdg / "planted"
        planted.mkdir()
        (planted / "requirements.txt").write_text("PyJWT==2.3.0\n")
        wt.parent.mkdir(parents=True)
        if tamper == "symlink":
            wt.symlink_to(planted, target_is_directory=True)
        else:
            shutil.copytree(planted, wt)
        target = planted / "requirements.txt" if tamper == "symlink" else wt / "requirements.txt"
    else:
        branch = "vulnscan/2026-10-22" if tamper != "other-branch" else "feature"
        _run(repo, "worktree", "add", "-q", "-b", branch, str(wt), "origin/main")
        target = wt / "requirements.txt"
        if tamper == "foreign-change":
            (wt / "app.py").write_text("print('edited by a person')\n")
        if tamper == "local-commit":
            (wt / "app.py").write_text("print('committed')\n")
            _run(wt, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qam", "local")
    before = target.read_text()
    got, md = daily.stage_worktree_fixes(repo, _cfg(), "2026-10-22")
    assert got == "" and "not reusing" in md
    assert target.read_text() == before                                        # nothing was written into it


@needs_git
def test_missing_origin_head_is_refused_not_staged_off_the_local_branch(xdg: Path, feed: Feed) -> None:
    repo = _repo_with_remote(xdg)
    _run(repo, "remote", "set-head", "origin", "-d")
    _run(repo, "remote", "set-url", "origin", str(xdg / "gone.git"))          # git >= 2.48 fetch would restore it
    _run(repo, "checkout", "-q", "-b", "feature")
    (repo / "app.py").write_text("print('feature work')\n")
    _run(repo, "commit", "-qam", "feature")
    wt, md = daily.stage_worktree_fixes(repo, _cfg(), "2026-10-22")
    assert wt == "" and "origin/HEAD" in md and not _wt(repo).exists()


# ---------------------------------------------------------------- M2: daily._git is hardened

@needs_git
def test_daily_git_ignores_repository_fsmonitor_and_times_out_fetch(xdg: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(xdg)
    marker = xdg / "fsmonitor-ran"
    hook = xdg / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    hook.chmod(0o755)
    _run(repo, "config", "core.fsmonitor", str(hook))
    monkeypatch.setenv("GIT_DIR", str(xdg / "elsewhere"))                       # inherited GIT_* never leak in
    assert daily._git(repo, "status", "--porcelain").returncode == 0
    assert not marker.exists()
    seen: list[dict[str, Any]] = []
    real = subprocess.run

    def spy(argv: list[str], **kw: Any) -> subprocess.CompletedProcess:
        seen.append({"argv": argv, **kw})
        return real(argv, **kw)
    monkeypatch.setattr(daily.subprocess, "run", spy)
    daily._git(repo, "fetch", "-q", "origin")
    assert seen and seen[-1].get("timeout") and "core.hooksPath=/dev/null" in seen[-1]["argv"]
    assert seen[-1]["env"]["GIT_CONFIG_NOSYSTEM"] == "1" and "GIT_DIR" not in seen[-1]["env"]


@needs_git
def test_a_hung_fetch_is_reported_not_raised(xdg: Path, feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(xdg)
    real = daily._git

    def slow(r: Path, *args: str, **kw: Any) -> subprocess.CompletedProcess:
        if args[:1] == ("fetch",):
            raise subprocess.TimeoutExpired(["git", "fetch"], 1)
        return real(r, *args, **kw)
    monkeypatch.setattr(daily, "_git", slow)
    wt, md = daily.stage_worktree_fixes(repo, _cfg(), "2026-10-22")
    assert wt == str(_wt(repo)) and "fetch" in md and "timed out" in md
