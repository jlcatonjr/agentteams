"""agentteams.vulnscan.daily: scan roots and fix plans are looked up in parallel (``scan_workers``).

The parallel run must equal the serial one (same findings, same fix plans, same order of feed errors), keep the
caller's context (a ``subproc_confine`` scope is a ContextVar), and fail as the serial run fails.
"""

from __future__ import annotations

import contextvars
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import daily, engine

from tests.test_vulnscan_daily import _cfg, feed, xdg  # noqa: F401  (fixtures)
from tests.test_vulnscan_core import Feed


def _roots(base: Path, n: int) -> list[str]:
    out = []
    for i in range(n):
        root = base / "code" / f"project-{i}"
        root.mkdir(parents=True)
        (root / "requirements.txt").write_text("PyJWT==2.3.0\n")
        out.append(str(root))
    return out


def _run(cfg: dict[str, Any], today: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    report, _, run_dir = daily.run_daily(cfg, today=today)
    plans = {p.name: p.read_text() for p in sorted(run_dir.glob("fixplan-*.md"))}
    return [f for f in json.loads(json.dumps(report.to_dict()))["findings"]], plans


def test_parallel_equals_serial(xdg: Path, feed: Feed) -> None:  # noqa: F811
    roots = _roots(xdg, 6)
    serial = _run(_cfg(repo_roots=roots, scan_workers=1), "2026-10-20")
    parallel = _run(_cfg(repo_roots=roots, scan_workers=4), "2026-10-21")
    assert serial == parallel
    assert len(serial[0]) == 6 and len(serial[1]) == 6


def test_roots_are_scanned_at_the_same_time(xdg: Path, feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    roots = _roots(xdg, 6)
    lock, live, peak = threading.Lock(), [0], [0]
    real = engine.scan_path

    def slow(*args: Any, **kwargs: Any) -> Any:
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)   # a network wait
        try:
            return real(*args, **kwargs)
        finally:
            with lock:
                live[0] -= 1

    monkeypatch.setattr(engine, "scan_path", slow)
    daily.run_daily(_cfg(repo_roots=roots, scan_workers=4), today="2026-10-21")
    assert peak[0] > 1
    peak[0] = 0
    daily.run_daily(_cfg(repo_roots=roots, scan_workers=1), today="2026-10-22")
    assert peak[0] == 1


def test_workers_keep_the_callers_context() -> None:
    """A subproc_confine scope is a ContextVar: a worker sees the caller's value, as a serial call would."""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("scope", default="host")
    token = var.set("confined")
    try:
        seen = daily._parallel({"scan_workers": 4}, lambda x: (x, var.get()), list(range(8)))
    finally:
        var.reset(token)
    assert seen == [(i, "confined") for i in range(8)]   # in order, every one confined


def test_a_failing_root_costs_only_its_own_findings(xdg: Path, feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """@audit #277: the other roots are kept and the run is partially unscanned (it used to keep nothing)."""
    roots = _roots(xdg, 4)
    real = engine.scan_path

    def broken(path: Path, **kwargs: Any) -> Any:
        if path.name == "project-2":
            raise OSError("disk went away")
        return real(path, **kwargs)

    monkeypatch.setattr(engine, "scan_path", broken)
    report, code, run_dir = daily.run_daily(_cfg(repo_roots=roots, scan_workers=4), today="2026-10-21")
    assert code == daily.EXIT_UNSCANNED
    assert any(e.startswith("scan of ") and "project-2 stopped: OSError: disk went away" in e for e in report.feed_errors)
    assert not any("stopped early" in e for e in report.feed_errors)
    assert len([f for f in report.findings if f.kind == "package"]) == 3            # the other three roots are kept
    assert len(list(run_dir.glob("fixplan-*.md"))) == 3


def test_a_failing_fix_plan_costs_only_its_own_plan(xdg: Path, feed: Feed, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    roots = _roots(xdg, 3)
    real = daily.fix.fix_path

    def broken(repo: Path, **kwargs: Any) -> Any:
        if Path(repo).name == "project-1":
            raise ValueError("registry answered nonsense")
        return real(repo, **kwargs)

    monkeypatch.setattr(daily.fix, "fix_path", broken)
    report, _, run_dir = daily.run_daily(_cfg(repo_roots=roots, scan_workers=4), today="2026-10-21")
    assert any("fix plan for" in e and "project-1 stopped: ValueError" in e for e in report.feed_errors)
    assert len(list(run_dir.glob("fixplan-*.md"))) == 2


def test_a_failure_cancels_the_calls_not_yet_started() -> None:
    """After the first error no queued call starts (those already running finish), and the error is raised."""
    started: list[int] = []

    def fn(x: int) -> int:
        started.append(x)
        if x == 0:
            raise ValueError("first item failed")
        time.sleep(0.02)   # the rest are slow lookups: most are still queued when item 0 fails
        return x

    with pytest.raises(ValueError, match="first item failed"):
        daily._parallel({"scan_workers": 2}, fn, list(range(50)))
    assert len(started) < 50


def test_nested_roots_plan_a_repository_once(xdg: Path, feed: Feed) -> None:  # noqa: F811
    """repo_roots ["~/code", "~/code/project-0"]: one fix-plan lookup and one file for project-0, as serially."""
    roots = _roots(xdg, 2)
    looked_up: list[str] = []
    real = daily.fix.fix_path

    def counting(repo: Path, **kwargs: Any) -> Any:
        looked_up.append(Path(repo).name)
        return real(repo, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(daily.fix, "fix_path", counting)
        _, plans = _run(_cfg(repo_roots=[str(xdg / "code"), roots[0]], scan_workers=4), "2026-10-21")
    assert sorted(looked_up) == ["project-0", "project-1"]
    assert len(plans) == 2


def test_one_worker_is_plain_serial() -> None:
    order: list[str] = []
    out = daily._parallel({"scan_workers": 1}, lambda x: order.append(threading.current_thread().name) or x * 2, [1, 2])
    assert out == [2, 4] and set(order) == {threading.current_thread().name}
    assert daily._parallel({"scan_workers": 4}, lambda x: x, []) == []
