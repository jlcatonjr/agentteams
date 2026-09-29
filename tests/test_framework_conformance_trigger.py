"""Tests for the standard agent-infrastructure conformance check.

Covers ``agentteams.framework_research.run_agent_conformance_check`` — the read-only, ledger-gated
(≤24h) trigger that routes ``@framework-adapters-expert`` when the already-produced upstream
snapshot shows real drift or a relocated doc. No network occurs: every snapshot is crafted on disk
and ``now`` is injected. See ``references/provider-adapter-refresh.procedure.md`` §7.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from agentteams import framework_research as fr

# Deliberately NOT today's date: the whole suite injects this instant as ``now`` and writes
# snapshots stamped relative to it, so the tests must be judged against the injected clock, never
# wall-clock time. Pinning it to a fixed future date proves the injection is honored end to end
# (snapshot-freshness included) rather than passing only because the run happens on 2026-09-29.
_NOW = dt.datetime(2030, 6, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def _iso(when: dt.datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_snapshot(repo_root: Path, *, generated_at: dt.datetime, frameworks: dict) -> None:
    """Write a minimal multi-framework snapshot with caller-controlled per-framework entries.

    ``frameworks`` maps fid -> {fetch_status, keys_diff, source_url}. Missing pieces default to a
    clean ``ok`` framework with no drift.
    """
    entries = {}
    for fid, spec in frameworks.items():
        entries[fid] = {
            "label": fid,
            "source_url": spec.get("source_url", f"https://example.test/{fid}"),
            "expert_ref": f"references/{fid}-agent-infrastructure-expert.md",
            "fetch_status": spec.get("fetch_status", "ok"),
            "keys_diff": spec.get(
                "keys_diff", {"matched": ["name"], "missing_upstream": [], "new_upstream": []}
            ),
        }
    snap_path = repo_root / fr.SNAPSHOT_REL
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(
        json.dumps({"generated_at": _iso(generated_at), "frameworks": entries}) + "\n",
        encoding="utf-8",
    )


def _write_ledger(repo_root: Path, ledger: dict) -> None:
    path = fr._agent_check_ledger_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger) + "\n", encoding="utf-8")


def _read_ledger(repo_root: Path) -> dict:
    return json.loads(fr._agent_check_ledger_path(repo_root).read_text(encoding="utf-8"))


# --- gate --------------------------------------------------------------------


def test_skips_when_checked_within_window(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, generated_at=_NOW, frameworks={"claude": {}})
    _write_ledger(tmp_path, {"last_checked": _iso(_NOW - dt.timedelta(hours=1))})

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["status"] == "fresh-skip"
    assert result["ran"] is False
    assert result["route"] is False
    # A skip must not overwrite the prior last_checked.
    assert _read_ledger(tmp_path)["last_checked"] == _iso(_NOW - dt.timedelta(hours=1))


def test_runs_when_ledger_stale(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, generated_at=_NOW, frameworks={"claude": {}})
    _write_ledger(tmp_path, {"last_checked": _iso(_NOW - dt.timedelta(hours=30))})

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["status"] == "no-drift"
    assert result["ran"] is True
    assert result["needs_agent_action"] is False
    assert _read_ledger(tmp_path)["last_checked"] == _iso(_NOW)


def test_force_bypasses_gate(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, generated_at=_NOW, frameworks={"claude": {}})
    _write_ledger(tmp_path, {"last_checked": _iso(_NOW - dt.timedelta(minutes=5))})

    result = fr.run_agent_conformance_check(tmp_path, window_hours=0.0, now=_NOW)

    assert result["ran"] is True
    assert result["status"] == "no-drift"


# --- inconclusive / stale snapshot ------------------------------------------


def test_stale_snapshot_does_not_advance_clock(tmp_path: Path) -> None:
    # Snapshot is 40h old — older than the 24h freshness bound.
    _write_snapshot(tmp_path, generated_at=_NOW - dt.timedelta(hours=40), frameworks={"claude": {}})

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["status"] == "stale-snapshot"
    ledger = _read_ledger(tmp_path)
    assert "last_checked" not in ledger  # the 24h clock must NOT advance on inconclusive (adv F2)
    assert ledger["last_attempted"] == _iso(_NOW)


def test_missing_snapshot_is_stale(tmp_path: Path) -> None:
    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)
    assert result["status"] == "stale-snapshot"
    assert result["needs_agent_action"] is False


def test_attempt_throttle_after_stale(tmp_path: Path) -> None:
    _write_snapshot(tmp_path, generated_at=_NOW - dt.timedelta(hours=40), frameworks={"claude": {}})
    _write_ledger(tmp_path, {"last_attempted": _iso(_NOW - dt.timedelta(minutes=20))})

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["status"] == "attempt-throttled"
    assert result["ran"] is False


def test_readonly_tree_degrades_without_crash(tmp_path: Path) -> None:
    # Simulate a module tree where the ledger cannot be written (read-only install): make the
    # `tmp` path a FILE so mkdir(parents=True) under it raises OSError. The check must degrade to a
    # result rather than crash — the persisted-ledger write is best-effort (adv Phase-2 F1).
    (tmp_path / "tmp").write_text("not a directory", encoding="utf-8")

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)  # must not raise

    assert result["status"] == "stale-snapshot"
    assert result["needs_agent_action"] is False


def test_write_ledger_returns_false_when_unwritable(tmp_path: Path) -> None:
    (tmp_path / "tmp").write_text("not a directory", encoding="utf-8")
    assert fr._write_agent_check_ledger(tmp_path, {"last_checked": _iso(_NOW)}) is False


def test_force_does_not_launder_stale_snapshot(tmp_path: Path) -> None:
    # --force bypasses the ledger gate but must not treat a month-old snapshot as authoritative.
    _write_snapshot(tmp_path, generated_at=_NOW - dt.timedelta(days=30), frameworks={"claude": {}})

    result = fr.run_agent_conformance_check(tmp_path, window_hours=0.0, now=_NOW)

    assert result["status"] == "stale-snapshot"


# --- classification ----------------------------------------------------------


def test_missing_upstream_routes(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        generated_at=_NOW,
        frameworks={
            "claude": {},
            "copilot-cli": {
                "keys_diff": {"matched": [], "missing_upstream": ["tools"], "new_upstream": []}
            },
        },
    )

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["status"] == "drift"
    assert result["route"] is True
    assert result["drift_frameworks"] == ["copilot-cli"]
    assert result["drift_signature"]
    assert _read_ledger(tmp_path)["last_routed_hash"] == result["drift_signature"]


def test_fetch_issue_routes(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        generated_at=_NOW,
        frameworks={"claude": {}, "codex": {"fetch_status": "moved"}},
    )

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["route"] is True
    assert result["fetch_issue_frameworks"] == ["codex"]


def test_new_upstream_alone_does_not_route(tmp_path: Path) -> None:
    # new_upstream is structurally always empty in production, but assert the predicate ignores it
    # even when present: only missing_upstream + moved/empty are actionable (adv F5).
    _write_snapshot(
        tmp_path,
        generated_at=_NOW,
        frameworks={
            "claude": {
                "keys_diff": {"matched": ["name"], "missing_upstream": [], "new_upstream": ["extra"]}
            }
        },
    )

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["status"] == "no-drift"
    assert result["route"] is False


def test_transient_status_does_not_route(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        generated_at=_NOW,
        frameworks={"claude": {}, "goose": {"fetch_status": "failed"}},
    )

    result = fr.run_agent_conformance_check(tmp_path, now=_NOW)

    assert result["status"] == "no-drift"
    assert result["fetch_issue_frameworks"] == []


# --- idempotency -------------------------------------------------------------


def test_route_is_idempotent_for_same_drift(tmp_path: Path) -> None:
    frameworks = {
        "claude": {},
        "copilot-cli": {
            "keys_diff": {"matched": [], "missing_upstream": ["tools"], "new_upstream": []}
        },
    }
    _write_snapshot(tmp_path, generated_at=_NOW, frameworks=frameworks)

    first = fr.run_agent_conformance_check(tmp_path, now=_NOW)
    assert first["route"] is True

    # A day later the same drift is still present; the cron restamped the snapshot.
    later = _NOW + dt.timedelta(hours=25)
    _write_snapshot(tmp_path, generated_at=later, frameworks=frameworks)
    second = fr.run_agent_conformance_check(tmp_path, now=later)

    assert second["needs_agent_action"] is True
    assert second["already_routed"] is True
    assert second["route"] is False  # same drift signature → no re-route


def test_new_drift_routes_again(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        generated_at=_NOW,
        frameworks={
            "copilot-cli": {
                "keys_diff": {"matched": [], "missing_upstream": ["tools"], "new_upstream": []}
            }
        },
    )
    first = fr.run_agent_conformance_check(tmp_path, now=_NOW)
    assert first["route"] is True

    # A different framework now drifts too → new signature → routes again.
    later = _NOW + dt.timedelta(hours=25)
    _write_snapshot(
        tmp_path,
        generated_at=later,
        frameworks={
            "copilot-cli": {
                "keys_diff": {"matched": [], "missing_upstream": ["tools"], "new_upstream": []}
            },
            "goose": {
                "keys_diff": {"matched": [], "missing_upstream": ["extensions"], "new_upstream": []}
            },
        },
    )
    second = fr.run_agent_conformance_check(tmp_path, now=later)
    assert second["route"] is True
    assert second["already_routed"] is False
