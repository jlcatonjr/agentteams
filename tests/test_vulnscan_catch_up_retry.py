"""agentteams.vulnscan.daily (T2-C): catch-up re-runs a same-day run Docker left partial, and a re-run keeps the
summary it replaces. Docker readiness is faked (no test calls real docker).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import daily, docker_readiness as dr, paths
from agentteams.vulnscan.report import EXIT_UNSCANNED, Report

TODAY = dt.date.today().isoformat()
READY = dr.Readiness()
DOWN = dr.Readiness(cli=dr.Layer("ok"), daemon=dr.Layer("fail", dr.DAEMON, "down"))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    w: dict[str, Any] = {"docker": READY, "runs": [], "sent": []}
    monkeypatch.setattr(dr, "ensure", lambda cfg, *, start, runner=None: w["docker"])
    monkeypatch.setattr(daily, "run_daily", lambda cfg: w["runs"].append(1) or (Report(target="daily"), 0, Path()))
    monkeypatch.setattr(daily.notify, "send", lambda cfg, key, **kw: w["sent"].append(key))
    return w


def _last_run(exit_code: int, *, feed_errors: list[str], docker: dict | None = None, hours_ago: float = 1) -> Path:
    run = paths.state_dir() / TODAY
    run.mkdir(parents=True, exist_ok=True)
    (run / "summary.json").write_text(json.dumps({"feed_errors": feed_errors, "remediation": {"docker": docker or {}}}))
    when = dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago)
    daily.heartbeat_path().write_text(json.dumps({"last_run": when.isoformat(), "exit": exit_code}))
    return run


INFRA = ["vk-prod: not scanned: tools container unavailable: docker is not reachable"]


def test_a_docker_partial_run_is_retried_at_most_twice(env: dict) -> None:
    run = _last_run(EXIT_UNSCANNED, feed_errors=INFRA)
    cfg = {**daily.DEFAULT_CONFIG}
    assert daily.catch_up_due(cfg) == (True, 1)
    assert daily.catch_up(cfg) is not None and daily.catch_up(cfg) is not None
    assert env["runs"] == [1, 1] and (run / daily.INFRA_RETRY_FILE).read_text().strip() == "2"
    assert env["sent"] == [], "a retry is not a stale-run notification"
    assert daily.catch_up_due(cfg) == (False, 1) and daily.catch_up(cfg) is None and env["runs"] == [1, 1]


def test_a_docker_reason_code_alone_also_counts(env: dict) -> None:
    _last_run(EXIT_UNSCANNED, feed_errors=["x: audit timed out"],
              docker={"daemon": {"state": "fail", "code": dr.DAEMON, "message": "down"}})
    assert daily.catch_up_due({**daily.DEFAULT_CONFIG})[0]


@pytest.mark.parametrize(("exit_code", "errors", "docker", "why"), [
    (EXIT_UNSCANNED, INFRA, DOWN, "Docker is still not ready"),
    (EXIT_UNSCANNED, ["vk-prod: audit timed out"], READY, "not an infrastructure cause"),
    (1, [], READY, "the run was not partial"),
])
def test_no_retry(env: dict, exit_code: int, errors: list[str], docker: dr.Readiness, why: str) -> None:
    env["docker"] = docker
    _last_run(exit_code, feed_errors=errors)
    assert daily.catch_up_due({**daily.DEFAULT_CONFIG}) == (False, 1), why
    assert daily.catch_up({**daily.DEFAULT_CONFIG}) is None and env["runs"] == []


def test_max_retries_per_day_is_configurable_and_checked(env: dict) -> None:
    _last_run(EXIT_UNSCANNED, feed_errors=INFRA)
    assert not daily.catch_up_due({**daily.DEFAULT_CONFIG, "catch_up": {"max_retries_per_day": 0}})[0]
    for bad in ({"max_retries_per_day": -1}, {"max_retries_per_day": True}, {"retries": 2}, []):
        assert "catch_up" in daily._config_problem({**daily.DEFAULT_CONFIG, "catch_up": bad})


def test_a_stale_run_still_notifies_first(env: dict) -> None:
    _last_run(EXIT_UNSCANNED, feed_errors=INFRA, hours_ago=40)
    assert daily.catch_up({**daily.DEFAULT_CONFIG}) is not None and env["sent"] == ["run-stale"]


# ---------------------------------------------------------------- archiving

def test_a_same_day_rerun_archives_the_earlier_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    cfg = {**daily.DEFAULT_CONFIG, "local_runtimes": False, "notify": "none"}
    _, _, run = daily.run_daily(cfg, today="2026-10-29")
    first = (run / "summary.json").read_text()
    stamp = dt.datetime(2026, 10, 29, 9, 3, 7).timestamp()
    for name in ("summary.json", "summary.md"):
        os.utime(run / name, (stamp, stamp))
    daily.run_daily(cfg, today="2026-10-29")
    assert (run / "summary.090307.json").read_text() == first and (run / "summary.090307.md").is_file()
    for name in ("summary.json", "summary.md"):
        os.utime(run / name, (stamp, stamp))
    daily.run_daily(cfg, today="2026-10-29")
    assert (run / "summary.090307-1.json").is_file()
    assert paths.run_dirs() == [run]   # still one run, read from its summary.json


def test_previous_summary_reads_only_summary_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    old = paths.state_dir() / "2026-10-28"
    old.mkdir(parents=True)
    (old / "summary.json").write_text(json.dumps(Report(target="daily").to_dict()))
    (old / "summary.235959.json").write_text("{not json")   # an archive is never read
    rep, notes = daily._previous_summary(paths.state_dir(), "2026-10-29")
    assert rep is not None and notes == []
