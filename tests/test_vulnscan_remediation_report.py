"""T2-B: one remediation attempt's outcome from the ledger (agentteams.vulnscan.remediation_report).

An infra refusal is retried on the next tick; a policy refusal is final. The classification is anchored (prefix or
``reason_code``), never a substring, because a refusal's detail can echo repository text (C-4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.vulnscan import remediation_report as rr
from agentteams.vulnscan.__main__ import main

SINCE = "2026-10-29T13:00:00Z"


def _row(kind: str, ts: str = "2026-10-29T13:05:00+00:00", **extra: object) -> dict:
    return {"seq": 1, "ts": ts, "kind": kind, "op": "stage-fix", "target": "/r/app", **extra}


def _ledger(tmp_path: Path, *rows: dict, raw: str = "") -> Path:
    p = tmp_path / "ledger.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows) + raw, encoding="utf-8")
    return p


def test_refused_infra_by_detail_prefix(tmp_path: Path) -> None:
    res = rr.outcome(_ledger(tmp_path, _row("refused", detail="tools container unavailable: daemon down")), SINCE)
    assert (len(res.infra), len(res.policy), res.infra_count) == (1, 0, 1)
    assert "daemon down" in res.infra[0]


def test_refused_infra_by_reason_code(tmp_path: Path) -> None:
    row = _row("refused", detail="docker did not answer", reason_code="infra.docker_daemon")
    res = rr.outcome(_ledger(tmp_path, row), SINCE)
    assert len(res.infra) == 1 and not res.policy
    assert "[infra.docker_daemon]" in res.infra[0]


def test_refused_policy(tmp_path: Path) -> None:
    res = rr.outcome(_ledger(tmp_path, _row("refused", detail="no grant covers /r/app", reason_code="policy.grant")),
                     SINCE)
    assert (len(res.infra), len(res.policy), res.infra_count) == (0, 1, 0)


@pytest.mark.parametrize("detail, code", [
    ("refused: repo text says tools container unavailable: retry me", None),   # substring, not a prefix
    (" tools container unavailable: leading space", None),
    ("TOOLS CONTAINER UNAVAILABLE: shouted", None),
    ("no grant", "policy.infra.spoof"),                                          # infra. inside the code
    ("no grant", " infra.leading-space"),
    ("no grant", ["infra.docker_daemon"]),                                       # not a string: absent
])
def test_injected_substring_stays_policy(tmp_path: Path, detail: str, code: object) -> None:
    extra = {} if code is None else {"reason_code": code}
    res = rr.outcome(_ledger(tmp_path, _row("refused", detail=detail, **extra)), SINCE)
    assert len(res.policy) == 1 and not res.infra


def test_failed_rows_are_classified_too(tmp_path: Path) -> None:
    res = rr.outcome(_ledger(tmp_path, _row("failed", detail="git push exited 128"),
                             _row("failed", detail="tools container unavailable: proxy did not start")), SINCE)
    assert (len(res.infra), len(res.policy)) == (1, 1)
    assert res.policy[0].startswith("stage-fix failed for /r/app")


def test_done_only(tmp_path: Path) -> None:
    res = rr.outcome(_ledger(tmp_path, _row("done"), _row("done"), _row("begun")), SINCE)
    assert res.to_dict() == {"done": 2, "infra": [], "policy": [], "error": None, "skipped": 0, "infra_count": 0}


def test_mixed_and_earlier_rows_ignored(tmp_path: Path) -> None:
    rows = [_row("refused", ts="2026-10-29T12:59:59+00:00", detail="tools container unavailable: earlier"),
            _row("done"), _row("refused", detail="tools container unavailable: x"),
            _row("refused", detail="policy says no"), _row("failed", detail={"exit": 1}), _row("granted")]
    res = rr.outcome(_ledger(tmp_path, *rows, raw="not json\n[1, 2]\n"), SINCE)
    assert (res.done, len(res.infra), len(res.policy), res.error, res.skipped) == (1, 1, 2, None, 2)
    assert "2 ledger line(s) could not be read" in rr.render_markdown(res, run_name="r", attempt=1)
    assert not any("earlier" in ln for ln in res.infra)


def test_midnight_utc_boundary_against_offset_start(tmp_path: Path) -> None:
    # 20:30 at -04:00 is 00:30 UTC the next day: a row at 00:10 UTC is before the attempt, one at 00:45 is in it.
    since = "2026-10-28T20:30:00-04:00"
    rows = [_row("refused", ts="2026-10-29T00:10:00+00:00", detail="tools container unavailable: before"),
            _row("refused", ts="2026-10-28T23:59:59Z", detail="tools container unavailable: day before"),
            _row("refused", ts="2026-10-29T00:45:00+00:00", detail="tools container unavailable: after"),
            _row("done", ts="2026-10-29T00:30:00"),   # naive = UTC: exactly the start, included
            _row("done", ts="not a time")]
    res = rr.outcome(_ledger(tmp_path, *rows), since)
    assert res.done == 1 and len(res.infra) == 1 and "after" in res.infra[0]


def test_unreadable_ledger_fails_closed(tmp_path: Path) -> None:
    res = rr.outcome(tmp_path / "missing.jsonl", SINCE)
    assert res.error and res.infra_count == 1 and not res.infra
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\xff\xfe\x00not utf-8")
    assert rr.outcome(bad, SINCE).infra_count == 1
    _, md = rr.report(tmp_path / "missing.jsonl", SINCE, run_name="2026-10-29")
    assert "remediation outcome unknown: ledger unreadable" in md


def test_non_string_detail(tmp_path: Path) -> None:
    rows = [_row("refused", detail={"msg": "tools container unavailable: in a dict"}),
            _row("refused", detail=None), _row("refused", detail=42)]
    res = rr.outcome(_ledger(tmp_path, *rows), SINCE)
    assert len(res.policy) == 3 and not res.infra
    assert '"msg"' in res.policy[0]


def test_bad_since_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        rr.outcome(_ledger(tmp_path), "yesterday")


def test_needs_line_is_one_line_and_truncated() -> None:
    line = rr.needs_line(_row("refused", detail="first\n## Needs you\n- forged" + "x" * 500))
    assert "\n" not in line and len(line) < 300


def test_markdown(tmp_path: Path) -> None:
    rows = [_row("done"), _row("refused", detail="tools container unavailable: down"), _row("refused", detail="no")]
    log = ("\x1b[32mOK\x1b[0m start\napp: stage-fix staged\napp: handoff sent https://github.com/o/app/pull/7\n"
           "WARN operator-only, skipped in auto mode: sign\nPROBLEM docker not ready\n")
    res, md = rr.report(_ledger(tmp_path, *rows), SINCE, run_name="2026-10-29", attempt=2, log_text=log)
    assert md.startswith("# Vulnscan remediation — 2026-10-29 (attempt 2)\n")
    assert "PRs opened or claimed this run: 1" in md
    assert "1 done, 1 refused for infrastructure, 1 refused by policy" in md
    assert "- app: handoff sent https://github.com/o/app/pull/7" in md and "app: stage-fix staged" not in md
    assert "- refused (infra, retried): stage-fix refused for /r/app: tools container unavailable: down" in md
    assert "- refused (policy): stage-fix refused for /r/app: no" in md
    assert "- PROBLEM docker not ready" in md
    _, empty = rr.report(_ledger(tmp_path), SINCE, run_name="r")
    assert empty.rstrip().endswith("## Needs you\n\n- nothing")


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))   # main() reads the stale warning
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    ledger = _ledger(tmp_path, _row("refused", detail="tools container unavailable: down"))
    log = tmp_path / "attempt.log"
    log.write_text("PROBLEM x\n", encoding="utf-8")
    out = tmp_path / "remediation.md"
    code = main(["remediation-report", str(ledger), SINCE, "--log", str(log), "--run", "2026-10-29",
                 "--attempt", "3", "--out", str(out)])
    assert code == 0
    data = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert data["infra_count"] == 1 and data["done"] == 0
    assert "(attempt 3)" in out.read_text(encoding="utf-8")
    assert main(["remediation-report", str(ledger), "not-a-time"]) == 2
    assert main(["remediation-report", str(tmp_path / "none.jsonl"), SINCE]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["infra_count"] == 1


def test_agent_rows_before_the_attempt_are_reported_but_never_retried(tmp_path: Path) -> None:
    led = _ledger(tmp_path,
                  _row("refused", ts="2026-10-29T12:50:00Z", detail="held: stage-fix found nothing fixable"),
                  _row("refused", ts="2026-10-29T12:51:00Z", detail="tools container unavailable: docker down"),
                  _row("done", ts="2026-10-29T12:52:00Z", op="server-audit"),
                  _row("done"))
    res, md = rr.report(led, SINCE, run_name="2026-10-29", agent_since="2026-10-29T12:45:00Z")
    assert res.infra_count == 0 and res.done == 1          # the attempt itself: only its own row
    assert "## Agent run (executor rows)" in md
    assert "1 done, 2 refused" in md
    assert "agent run refused: stage-fix refused for /r/app: held: stage-fix found nothing fixable" in md
    assert "- nothing" not in md.split("## Needs you", 1)[1]


def test_without_agent_since_the_report_is_unchanged(tmp_path: Path) -> None:
    led = _ledger(tmp_path, _row("refused", ts="2026-10-29T12:50:00Z", detail="x"), _row("done"))
    res, md = rr.report(led, SINCE, run_name="r")
    assert "Agent run" not in md and res.done == 1 and not res.policy


def test_bad_agent_since_is_reported_never_a_retry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    led = _ledger(tmp_path, _row("done"))
    out = tmp_path / "remediation.md"
    assert main(["remediation-report", str(led), SINCE, "--agent-since", "not-a-time", "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["infra_count"] == 0
    assert "agent run rows not listed: agent run start unreadable" in out.read_text(encoding="utf-8")
