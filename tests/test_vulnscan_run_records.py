"""Truthful agent-run records (defect #1): per-run status/transcript, the report bound to its run,
no ``completed`` without the run's own report, connection failures counted as upstream errors."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_run, gateway, report_store, run_records
from tests.test_vulnscan_agent import _a, _FakeProc, _launchable, dirs  # noqa: F401 - dirs is a fixture
from tests.test_vulnscan_gateway import _chat, _drive, _handler, _request

GAI = ("Ran into this error: Server error: Server error (502 Bad Gateway) at http://gw:8080/api/v1/chat/completions: "
       "upstream error: gaierror.\n")


def _agent(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], *,
           report: str | None, transcript: str = "", spend_calls: int = 3) -> None:
    """The fake agent: writes spend.json, prints ``transcript`` and submits ``report`` (if any), exits 0."""
    def popen(argv: list[str], **kw: object) -> _FakeProc:
        (dirs["run"] / "gateway" / "spend.json").write_text(json.dumps({"calls": spend_calls, "upstream_errors": 0}))
        kw["stdout"].write(transcript)   # type: ignore[attr-defined]
        kw["stdout"].flush()             # type: ignore[attr-defined]
        if report is not None:
            report_store.save(dirs["run"], report, request_id="e" * 32)
        return _FakeProc(calls, argv, 0)
    monkeypatch.setattr(agent_run.subprocess, "Popen", popen)


def test_second_run_keeps_the_first_runs_record_and_never_shows_its_report(dirs: dict[str, Path],
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, report="# first run's report", transcript="first run\n")
    first = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert first["run_state"] == "completed" and first["report"]["request_id"] == "e" * 32
    assert "# first run" in report_store.render_for_review(dirs["run"])

    _agent(dirs, monkeypatch, calls, report=None, transcript="working\n" + GAI)   # ends on a gateway 502, exit 0
    second = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert second["run_state"] == "agent-error" and second["report"] is None
    assert "submitted no report" in second["reason"] and "gaierror" in second["reason"]
    assert second["archived_previous"] == first["run_id"]

    agent = dirs["run"] / "agent"
    latest = json.loads((agent / "status.json").read_text())
    assert latest["run_id"] == second["run_id"] and GAI in (agent / "transcript.log").read_text()
    kept = agent / run_records.RUNS_DIR / first["run_id"]
    assert json.loads((kept / "status.json").read_text())["run_state"] == "completed"
    assert (kept / "transcript.log").read_text() == "first run\n" and (kept / "spend.json").is_file()
    archived = (dirs["run"] / report_store.REPORT_DIR / f"{first['run_id']}.report.md").read_text()
    assert "- run state: completed" in archived              # the first run's report, finalized by it, untouched
    review = report_store.render_for_review(dirs["run"])
    assert "first run's report" not in review and report_store.NO_REPORT_LINE in review
    assert run_records.run_dirs_of(dirs["run"]) == [kept]


def test_the_run_lock_is_held_until_the_runs_record_is_written(dirs: dict[str, Path],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """A next run must not archive this run's report and transcript before this run has finalized them."""
    import fcntl
    calls = _launchable(dirs, monkeypatch)
    _agent(dirs, monkeypatch, calls, report="# the report")
    real, held = report_store.finalize, []

    def probe(*args: object, **kw: object) -> object:
        with open(agent_run.run_lock_path(), "a+") as other:
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        return real(*args, **kw)

    monkeypatch.setattr(report_store, "finalize", probe)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert st["run_state"] == "completed" and held == [True]


def test_a_run_whose_record_cannot_be_archived_writes_nothing(dirs: dict[str, Path],
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    out = dirs["run"] / "agent"
    out.mkdir()
    (out / "status.json").write_text(json.dumps({"run_id": "2026-10-22-aaaaaa", "run_state": "completed"}))
    (out / run_records.RUNS_DIR).write_text("not a directory")   # mkdir under it fails
    _launchable(dirs, monkeypatch)
    st = agent_run.run_agent({"agent": _a(dirs)}, dirs["run"])
    assert st["run_state"] == "not-run" and st["status_written"] is False and "archive" in st["reason"]
    assert json.loads((out / "status.json").read_text())["run_id"] == "2026-10-22-aaaaaa"


def test_an_earlier_runs_report_is_not_shown_or_finalized_as_the_latest_runs(dirs: dict[str, Path]) -> None:
    run = dirs["run"]
    report_store.save(run, "# morning report", request_id="f" * 32)        # legacy layout: never archived
    (run / "agent").mkdir()
    (run / "agent" / "status.json").write_text(json.dumps({"run_id": "2026-10-22-bbbbbb",
                                                           "started": "2999-01-01T00:00:00+00:00"}))
    review = report_store.render_for_review(run)
    assert "morning report" not in review and "2026-10-22-bbbbbb" in review and "submitted no report" in review
    assert report_store.finalize(run, "completed", since="2999-01-01T00:00:00+00:00") is None
    assert "- run state: pending" in (report_store.load(run) or "")
    fields = report_store.finalize(run, "completed", since="2000-01-01T00:00:00.5+00:00")
    assert fields is not None and fields["run state"] == "completed"


def test_archive_previous_is_a_no_op_on_a_fresh_run_dir(dirs: dict[str, Path]) -> None:
    assert run_records.archive_previous(dirs["run"]) is None and run_records.run_dirs_of(dirs["run"]) == []


def test_archive_names_never_collide_and_status_without_run_id_is_kept(dirs: dict[str, Path]) -> None:
    out = dirs["run"] / "agent"
    out.mkdir()
    for _ in range(2):
        (out / "status.json").write_text(json.dumps({"run_id": "2026-10-22-cccccc"}))
        run_records.archive_previous(dirs["run"])
    (out / "transcript.log").write_text("crashed before its status was written")
    name = run_records.archive_previous(dirs["run"])
    assert name and name.startswith("earlier-")
    assert sorted(p.name for p in (out / run_records.RUNS_DIR).iterdir()) == sorted(
        ["2026-10-22-cccccc", "2026-10-22-cccccc-2", name])


@pytest.mark.parametrize("exc", [socket.gaierror(8, "nodename nor servname"), ConnectionRefusedError(61, "refused"),
                                 TimeoutError("timed out")])
def test_gateway_counts_connection_failures_as_upstream_errors(monkeypatch: pytest.MonkeyPatch,
                                                                exc: OSError, tmp_path: Path) -> None:
    class Unreachable:
        def __init__(self, *a: object, **kw: object) -> None:
            return

        def request(self, *a: object, **kw: object) -> None:
            raise exc

        def close(self) -> None:
            return
    monkeypatch.setattr(gateway.http.client, "HTTPConnection", Unreachable)
    monkeypatch.setattr(gateway.http.client, "HTTPSConnection", Unreachable)
    meter = gateway.Meter(1.0, tmp_path / "spend.json")
    status, body = _drive(_handler(meter), _request(_chat()))
    assert status == 502 and type(exc).__name__ in json.loads(body)["error"]["message"]
    assert meter.upstream_errors == 1 and meter.calls == 0
    assert json.loads((tmp_path / "spend.json").read_text())["upstream_errors"] == 1
