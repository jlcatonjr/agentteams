"""Remediation effectiveness: the futile-retry hold, the batch preflight, the summary section and the alerts.

Covers :mod:`agentteams.vulnscan.retry_guard`, :func:`agentteams.vulnscan.executor_serve.batch_block` inside
``serve_requests``, and :mod:`agentteams.vulnscan.effectiveness`. Nothing here touches the network: PR state
comes from an injected lookup, and the tools container is reported unavailable by a patched check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import (  # noqa: E402
    effectiveness,
    executor,
    executor_serve,
    ledger,
    notify,
    paths,
    retry_guard,
    subproc_confine,
)
from agentteams.vulnscan.daily import plan_slug  # noqa: E402
from agentteams.vulnscan.report import Finding, Report  # noqa: E402
from tests._vulnscan_helpers import operator_home, sign_grant  # noqa: E402

REPO = "/repos/app"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    world = operator_home(tmp_path, monkeypatch)
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(notify, "send", lambda cfg, key, **f: sent.append((key, f)) or True)
    return {**world, "cfg": {"governance_dir": str(world["gov"])}, "sent": sent}


def _plan(day: str, text: str, repo: str = REPO) -> None:
    d = paths.state_dir() / day
    d.mkdir(parents=True, exist_ok=True)
    (d / f"fixplan-{plan_slug(Path(repo))}.md").write_text(text)


def _row(kind: str, detail: str, ts: str, target: str = REPO) -> dict[str, Any]:
    return {"op": "stage-fix", "kind": kind, "target": target, "detail": detail, "ts": ts}


# ---------------------------------------------------------------- retry_guard

def test_unchanged_plan_after_nothing_fixable_is_held(env: dict) -> None:
    _plan("2026-10-25", "plan A")
    _plan("2026-10-26", "plan A")
    rows = [_row("failed", "nothing fixable without a major jump", "2026-10-25T12:00:00+00:00")]
    why = retry_guard.futile_reason("stage-fix", REPO, rows)
    assert why.startswith(retry_guard.HELD_PREFIX) and "2026-10-25" in why
    assert retry_guard.futile_reason("open-pr", REPO, rows) == ""          # only stage-fix is held


def test_a_changed_plan_or_a_later_success_lifts_the_hold(env: dict) -> None:
    _plan("2026-10-25", "plan A")
    _plan("2026-10-26", "plan B")                                          # a new fixable release
    rows = [_row("failed", "nothing fixable without a major jump", "2026-10-25T12:00:00+00:00")]
    assert retry_guard.futile_reason("stage-fix", REPO, rows) == ""
    _plan("2026-10-26", "plan A")
    rows.append(_row("done", "{}", "2026-10-26T12:00:00+00:00"))
    assert retry_guard.futile_reason("stage-fix", REPO, rows) == ""
    other = [_row("failed", "npm failed: x", "2026-10-25T12:00:00+00:00")]   # not the futile result
    assert retry_guard.futile_reason("stage-fix", REPO, other) == ""


def test_a_missing_plan_never_holds(env: dict) -> None:
    rows = [_row("failed", "nothing fixable without a major jump", "2026-10-25T12:00:00+00:00")]
    assert retry_guard.futile_reason("stage-fix", REPO, rows) == ""


def test_mark_held_reports_once_per_plan_and_counts(env: dict) -> None:
    _plan("2026-10-26", "plan A")
    assert retry_guard.mark_held(REPO) is True
    assert retry_guard.mark_held(REPO) is False
    assert retry_guard.held_count() == 1
    _plan("2026-10-27", "plan B")
    assert retry_guard.held_count() == 0                                   # the plan moved on
    assert retry_guard.mark_held(REPO) is True


# ---------------------------------------------------------------- batch preflight

def _install(env: dict, ops: list[str]) -> None:
    from agentteams.vulnscan import grant

    (env["tmp"] / "repos").mkdir(exist_ok=True)
    rec = grant.build(ops=ops, repo_roots=[str(env["tmp"] / "repos")], hosts=["web1"], days=7, max_uses=5,
                      key_id="op", governance_dir=str(env["gov"]))
    grant.install(sign_grant(env["pem"], rec))


def _req(rid: str, **body: object) -> None:
    d = executor.spool_dir() / "requests"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rid}.json").write_text(json.dumps({"id": rid, **body}))


def test_an_unavailable_tools_container_stops_the_batch_before_the_first_request(
        env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env, ["server-audit"])
    monkeypatch.setattr(subproc_confine, "enabled", lambda cfg: True)
    monkeypatch.setattr(subproc_confine, "unavailable_reason", lambda tag: "no passing tools-selftest")
    called: list[str] = []
    monkeypatch.setattr(executor, "act", lambda *a, **k: called.append(a[0]) or {"status": "done"})
    for i in range(3):
        _req(f"{i:032x}", op="server-audit", target="web1")
    _req("f" * 32, op="report", target="run", payload={"markdown": "m"})
    out = executor.serve_requests(env["cfg"])
    assert called == ["report"]                                            # internal ops still run
    blocked = [r for r in out if r["op"] == "server-audit"]
    assert len(blocked) == 3 and all(r["detail"].startswith(executor_serve.NOT_ATTEMPTED) for r in blocked)
    rows = [r for r in ledger.read() if r.get("op") == "server-audit"]
    assert len(rows) == 1 and rows[0]["target"] == "3 requests" and "tools container" in rows[0]["detail"]


def test_no_grant_for_the_op_stops_the_batch(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env, ["server-audit"])
    called: list[str] = []
    monkeypatch.setattr(executor, "act", lambda *a, **k: called.append(a[0]) or {"status": "done"})
    _req("a" * 32, op="stage-fix", target=str(env["tmp"] / "repos" / "x"))
    _req("b" * 32, op="server-audit", target="web1")
    out = executor.serve_requests(env["cfg"])
    assert called == ["server-audit"]
    assert next(r for r in out if r["op"] == "stage-fix")["detail"].startswith(
        executor_serve.NOT_ATTEMPTED + "no valid grant")


def test_a_systemic_refusal_mid_pass_stops_the_rest_of_that_op(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env, ["server-audit"])
    calls: list[str] = []
    state = {"down": False}

    def act(op: str, target: str, cfg: dict, **kw: Any) -> dict:
        calls.append(op)
        state["down"] = True                                               # e.g. the self-test record was removed
        return {"status": "refused", "detail": "tools container unavailable: gone"}

    monkeypatch.setattr(executor, "act", act)
    monkeypatch.setattr(subproc_confine, "enabled", lambda cfg: True)
    monkeypatch.setattr(subproc_confine, "unavailable_reason", lambda tag: "gone" if state["down"] else "")
    for i in range(4):
        _req(f"{i:032x}", op="server-audit", target="web1")
    out = executor.serve_requests(env["cfg"])
    assert calls == ["server-audit"] and sum(r["detail"].startswith(executor_serve.NOT_ATTEMPTED) for r in out) == 3


def test_a_held_stage_fix_notifies_once(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = str((env["tmp"] / "repos" / "app").resolve())
    _install(env, ["stage-fix"])
    _plan("2026-10-26", "plan A", repo)
    monkeypatch.setattr(executor, "act", lambda *a, **k: {"status": "refused",
                                                          "detail": retry_guard.HELD_PREFIX + "x"})
    _req("a" * 32, op="stage-fix", target=repo)
    executor.serve_requests(env["cfg"])
    _req("b" * 32, op="stage-fix", target=repo)
    executor.serve_requests(env["cfg"])
    assert [k for k, _ in env["sent"]].count("repo-held") == 1


# ---------------------------------------------------------------- effectiveness

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def test_pr_urls_come_from_open_pr_successes_only() -> None:
    url = "https://github.com/o/r/pull/7"
    rows = [{"op": "open-pr", "kind": "done", "detail": json.dumps({"pr": url})},
            {"op": "open-pr", "kind": "done", "detail": json.dumps({"pr": url, "existing": True})},
            {"op": "open-pr", "kind": "failed", "detail": json.dumps({"pr": "https://github.com/o/r/pull/8"})},
            {"op": "open-pr", "kind": "done", "detail": json.dumps({"pr": "javascript:alert(1)"})},
            {"op": "open-pr", "kind": "done", "detail": "not json"}]
    assert effectiveness.pr_urls(rows) == [url]


def test_pr_stats_counts_open_stale_failing_unchecked_merged_and_unknown() -> None:
    views = {
        "u1": {"state": "OPEN", "createdAt": _iso(5), "statusCheckRollup": [{"conclusion": "FAILURE"}]},
        "u2": {"state": "OPEN", "createdAt": _iso(1), "statusCheckRollup": []},
        "u3": {"state": "OPEN", "createdAt": _iso(4), "statusCheckRollup": [{"conclusion": "SUCCESS"}]},
        "u4": {"state": "MERGED", "mergedAt": _iso(2)},
        "u5": {"state": "MERGED", "mergedAt": _iso(30)},
        "u6": {"state": "CLOSED"},
    }
    st = effectiveness.pr_stats([*views, "u7"], now=NOW, view=views.get)
    assert (st.open, st.stale, st.failing, st.no_checks, st.merged_recent, st.unknown, st.oldest_days) == \
        (3, 2, 1, 1, 1, 1, 5)


def _report(*keys: str) -> Report:
    rep = Report(target="daily")
    for k in keys:
        rep.findings.append(Finding(kind="package", target="app", name=k, version="1.0.0", ecosystem="npm",
                                    severity=3, ids=[f"GHSA-{k}"]))
    return rep


def test_section_counts_only_and_names_nothing(env: dict) -> None:
    st = effectiveness.PrStats(open=2, stale=1, failing=1, no_checks=0, merged_recent=3, oldest_days=4)
    md, _ = effectiveness.section(_report("b", "c"), _report("a", "b"), stats=st)
    assert "## Remediation effectiveness" in md
    assert "| Findings fixed since the previous run | 1 |" in md and "| Findings new since the previous run | 1 |" in md
    assert "| Net change in findings | +0 |" in md and "| Remediation PRs open | 2 (oldest 4 day(s)) |" in md
    assert "| Remediation PRs merged in the last 7 days | 3 |" in md
    assert "github.com" not in md and "/repos/" not in md


def test_alerts_fire_only_for_stale_or_failing_prs(env: dict) -> None:
    assert effectiveness.alerts(env["cfg"], effectiveness.PrStats(open=2)) is False
    assert effectiveness.alerts(env["cfg"], effectiveness.PrStats(open=2, stale=1)) is True
    assert env["sent"][-1] == ("stale-prs", {"count": 2, "old": 1, "failing": 0, "days": 3})
    assert notify.render("stale-prs", count=2, old=1, failing=0, days=3).startswith("2 remediation PR(s)")


def _summary(day: str, agent_state: str | None) -> None:
    d = paths.state_dir() / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text("{}")
    (d / "summary.md").write_text("# s\n" + (f"\n## Agent run\n\n- state: **{agent_state}**\n" if agent_state else ""))


def test_agent_streak_and_alert(env: dict) -> None:
    state = paths.state_dir()
    _summary("2026-10-23", "completed")
    _summary("2026-10-24", None)                                           # agent off: the streak stops here
    _summary("2026-10-25", "agent-error")
    _summary("2026-10-26", "gateway-refused")
    assert effectiveness.agent_streak(state, "2026-10-27", "completed") == 0
    assert effectiveness.agent_streak(state, "2026-10-27", "agent-error") == 3
    assert effectiveness.agent_alert(env["cfg"], state, "2026-10-27", "agent-error") is True
    assert env["sent"][-1] == ("agent-down", {"count": 3})
    _summary("2026-10-26", "completed")
    assert effectiveness.agent_alert(env["cfg"], state, "2026-10-27", "agent-error") is False   # 1 run only


def test_new_messages_render_fixed_text() -> None:
    assert "vulnscan review" in notify.render("repo-held")
    assert notify.render("agent-down", count=2).startswith("The remediation agent has not completed for 2")
    with pytest.raises(ValueError):
        notify.render("agent-down", count="two; rm -rf ~")


def test_the_staged_index_lists_staged_repositories(env: dict, tmp_path: Path) -> None:
    staged = executor.spool_dir() / "staged"
    staged.mkdir(parents=True)
    (staged / "app-1.json").write_text(json.dumps({"repo": "/repos/app", "branch": "vulnscan/x"}))
    (staged / "bad-2.json").write_text("{")
    (staged / "rel-3.json").write_text(json.dumps({"repo": "relative"}))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert executor_serve.write_staged_index(run_dir) == ["/repos/app"]
    assert json.loads((run_dir / executor_serve.STAGED_INDEX).read_text()) == {"repos": ["/repos/app"]}
