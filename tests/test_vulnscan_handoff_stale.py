"""vfr-c.C4: a ``reject`` unchanged for 14 days becomes a *Needs you* line; nothing re-asks the orchestrator.

Only a person's recorded answer (``handoff answer``, a ledger row) or a new digest clears it. Network-free.
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, handoff, handoff_stale, ledger  # noqa: E402
from tests.test_vulnscan_handoff_lapse import BRANCH, _rows, _stage, _verdict, world  # noqa: E402,F401
from tests.test_vulnscan_executor import env  # noqa: E402,F401  (the shared fixture)

TODAY = date.today()
OTHER = "vulnscan/20261101-aaaaaaaaaa"


def _later(monkeypatch: pytest.MonkeyPatch, days: int) -> None:
    """Move "today" ``days`` ahead, for the clock's reading and for the ledger's own row timestamps."""
    later = TODAY + timedelta(days=days)
    now = datetime.now(UTC) + timedelta(days=days)

    class Later(date):
        @classmethod
        def today(cls) -> date:   # type: ignore[override]
            return later

    class Stamp(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:   # type: ignore[override]
            return now
    monkeypatch.setattr(handoff_stale, "date", Later)
    monkeypatch.setattr(ledger, "datetime", Stamp)


def _status(repo: Path) -> handoff.RepoHandoff:
    [rec] = handoff.status({}, [repo])
    return rec


def _answer(repo: Path, word: str = "keep-rejected", reply: str = "y") -> int:
    args = argparse.Namespace(repo=[repo], answer=word, note="seen")
    return handoff_stale.cmd_answer(args, ask=lambda prompt: reply)


def test_a_fresh_reject_is_not_yet_a_needs_you_line(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    _later(monkeypatch, 13)
    rec = _status(repo)
    assert rec.state == "reject" and rec.needs_you == "" and _rows(handoff_stale.CLOCK_KIND, "start")


def test_after_14_days_the_reject_needs_you_and_nothing_re_asks(world: dict, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    _status(repo)   # first sight: the clock starts
    _later(monkeypatch, 14)
    before = len(ledger.read())
    rec = _status(repo)
    assert "rejected the staged fix 4b4656f6a5 (handoff 20261025171044)" in rec.needs_you
    assert "Nothing re-asks it" in rec.needs_you and "handoff answer" in rec.needs_you
    assert rec.ask == ""                                   # no packet text asks the orchestrator again
    assert len(ledger.read()) == before                    # the second sight writes nothing (no new request)
    assert "**Needs you:**" in handoff.markdown([rec]) and rec.to_dict()["needs_you"] == rec.needs_you
    assert list((repo / handoff.HANDOFF_DIR).glob("*.md")) == []   # no packet is written
    with pytest.raises(executor.ExecError, match="answered reject"):   # and open-pr is still refused
        with ledger.locked():
            executor.authorize("open-pr", str(repo), "r" * 32, world["env"]["cfg"])


def test_a_persons_recorded_answer_clears_it(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    _status(repo)
    _later(monkeypatch, 15)
    assert _status(repo).needs_you
    assert _answer(repo, reply="n") == 1 and _status(repo).needs_you   # declined: nothing recorded
    assert _answer(repo) == 0
    [row] = _rows(handoff_stale.ANSWER_KIND)
    assert (row["digest"], row["answer"], row["target"]) == ("4b4656f6a5", "keep-rejected", str(repo))
    assert _status(repo).needs_you == ""      # cleared: the answer restarts the clock (day 15)
    _later(monkeypatch, 28)
    assert _status(repo).needs_you == ""
    _later(monkeypatch, 29)
    assert "on " + (TODAY + timedelta(days=15)).isoformat() in _status(repo).needs_you   # 14 days after the answer
    with pytest.raises(executor.ExecError, match="answered reject"):   # the answer authorizes nothing
        with ledger.locked():
            executor.authorize("open-pr", str(repo), "r" * 32, world["env"]["cfg"])


def test_a_new_digest_clears_it_and_restarts_the_clock(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    _status(repo)
    _later(monkeypatch, 20)
    assert _status(repo).needs_you
    _stage(repo, OTHER)                  # a re-staged, changed fix
    _verdict(repo, "reject", branch=OTHER)
    assert _status(repo).needs_you == ""   # the new digest's clock starts now (day 20)
    _later(monkeypatch, 34)
    assert "aaaaaaaaaa" in _status(repo).needs_you
    assert [r["digest"] for r in _rows(handoff_stale.CLOCK_KIND, "start")] == ["4b4656f6a5", "aaaaaaaaaa"]


def test_an_answer_for_one_digest_does_not_clear_another(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    _status(repo)
    assert _answer(repo) == 0   # answered early, for this digest
    _stage(repo, OTHER)
    _verdict(repo, "reject", branch=OTHER)
    _status(repo)
    rows = [r for r in ledger.read() if r.get("kind") == handoff_stale.CLOCK_KIND and r.get("state") == "start"]
    rec = {"digest": "aaaaaaaaaa", "handoff_id": "x", "since": TODAY.isoformat(),
           "stale": (TODAY + timedelta(days=14)).isoformat()}
    assert rows and handoff_stale.needs_you(repo, rec, today=TODAY + timedelta(days=14))


def test_reject_defer_reject_keeps_the_first_since(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    _status(repo)                          # day 0: the clock starts
    _later(monkeypatch, 5)
    _verdict(repo, "defer")
    _status(repo)                          # a stop row
    _later(monkeypatch, 9)
    _verdict(repo, "reject")
    rec = _status(repo)                    # a new start row, but since stays day 0
    assert rec.needs_you == "" and len(_rows(handoff_stale.CLOCK_KIND, "start")) == 2
    _later(monkeypatch, 14)
    before = len(ledger.read())
    rec = _status(repo)
    assert f"on {TODAY.isoformat()}, unchanged for 14 days" in rec.needs_you
    assert rec.ask == "" and len(ledger.read()) == before                    # still nothing re-asks
    assert list((repo / handoff.HANDOFF_DIR).glob("*.md")) == []


def test_a_start_row_without_a_timestamp_reads_as_no_clock(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    real = ledger.read
    monkeypatch.setattr(ledger, "read", lambda **kw: [{k: v for k, v in r.items() if k != "ts"} for r in real(**kw)])
    with ledger.locked():
        handoff_stale.clock(repo, BRANCH)        # writes the start
        assert handoff_stale.clock(repo, BRANCH) is None   # its ts unreadable: no clock, never a KeyError


def test_another_verdict_stops_the_clock(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = world["repo"]
    _verdict(repo, "reject")
    _status(repo)
    _verdict(repo, "approve-staged")
    _later(monkeypatch, 30)
    assert _status(repo).needs_you == "" and _rows(handoff_stale.CLOCK_KIND, "stop")


def test_answers_are_refused_without_a_terminal_a_reject_or_a_known_word(world: dict) -> None:
    repo = world["repo"]
    assert handoff_stale.cmd_answer(argparse.Namespace(repo=[repo], answer="keep-rejected", note="")) == 2
    assert _answer(repo) == 2          # the verdict is own-pr: nothing rejected to answer
    _verdict(repo, "reject")
    assert _answer(repo, "approve") == 2   # an answer can never be an approval
    assert _rows(handoff_stale.ANSWER_KIND) == []
