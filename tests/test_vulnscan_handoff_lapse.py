"""vfr-b.B1b: an ``own-pr`` verdict lapses after seven days without an owner PR (operator decision OD10).

Colorado-collectors answered ``own-pr`` for a staged csv-parse 7 fix twice and never pushed its own branch, so the
fix waited indefinitely. The clock starts at the first sight of ``own-pr`` for a fix (a ``handoff-clock`` row in the
MAC-chained ledger: its timestamp is the start); a new digest, plan or verdict id, or any other verdict in between,
restarts it. Until it runs out each refusal, the summary and ``handoff status`` name the lapse date and the request
the next packet carries; after it, with no owner PR, ``open-pr`` proceeds and a ``handoff-lapsed`` row is logged once
a grant covers it. Network-free: gh's open-PR listing and the plan digest are stubbed.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, handoff, handoff_lapse, ledger, open_prs, paths, retry_guard  # noqa: E402
from tests.test_vulnscan_executor import _git_repo, _install, env  # noqa: E402,F401  (env: the shared fixture)
from tests.test_vulnscan_executor_guard import _brief  # noqa: E402

BRANCH = "vulnscan/20261025-4b4656f6a5"
HEADER = "handoff_id,verdict,branch,notes\n"
TODAY = date.today()


def _verdict(repo: Path, word: str = "own-pr", branch: str = BRANCH, hid: str = "20261025171044") -> None:
    d = repo / handoff.HANDOFF_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / handoff.VERDICTS_FILE).write_text(f"{HEADER}{hid},{word},{branch},we push our own\n", encoding="utf-8")


def _stage(repo: Path, branch: str = BRANCH, packages: tuple[object, ...] = ("csv-parse",)) -> None:
    d = executor.spool_dir() / "staged"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{executor.state_slug(repo)}.json").write_text(json.dumps(
        {"repo": str(repo), "branch": branch, "worktree": str(repo), "packages": list(packages)}), encoding="utf-8")


def _clock(repo: Path, branch: str = BRANCH) -> dict | None:
    with ledger.locked():
        return handoff_lapse.clock(repo, branch)


def _rows(kind: str, state: str | None = None) -> list[dict]:
    return [r for r in ledger.read() if r.get("kind") == kind and (state is None or r.get("state") == state)]


def _days_later(monkeypatch: pytest.MonkeyPatch, days: int) -> None:
    later = TODAY + timedelta(days=days)

    class Later(date):
        @classmethod
        def today(cls) -> date:   # type: ignore[override]
            return later
    monkeypatch.setattr(handoff_lapse, "date", Later)


@pytest.fixture
def world(env: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A handoff-delivery repository with a staged csv-parse fix and an own-pr verdict; no open PRs; one plan."""
    repo = _git_repo(env["tmp"] / "repos" / "colorado").resolve()
    _brief(repo, {"level": "remediate", "delivery": "handoff"})
    _install(env, ops=["stage-fix", "stage-major", "open-pr"], hosts=[])
    _stage(repo)
    _verdict(repo)
    state = {"repo": repo, "env": env, "prs": [], "plan": "plan-1"}
    monkeypatch.setattr(retry_guard, "plan_digest", lambda r, day=None: state["plan"])
    monkeypatch.setattr(executor, "origin_repo", lambda wt: "github.com/o/colorado")
    monkeypatch.setattr(open_prs, "list_open", lambda target: state["prs"])
    return state


def _authorize(world: dict, cfg: dict | None = None) -> dict:
    with ledger.locked():
        return executor.authorize("open-pr", str(world["repo"]), "r" * 32, cfg or world["env"]["cfg"])


def test_before_seven_days_open_pr_keeps_deferring_and_names_the_lapse_date(world: dict,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    lapses = (TODAY + timedelta(days=7)).isoformat()
    with pytest.raises(executor.ExecError, match="answered own-pr") as err:
        _authorize(world)
    assert f"lapses on {lapses}" in str(err.value)
    [start] = _rows(handoff_lapse.CLOCK_KIND, "start")   # the clock is a signed ledger row
    assert start["target"] == str(world["repo"]) and start["digest"] == "4b4656f6a5"
    _days_later(monkeypatch, 6)
    with pytest.raises(executor.ExecError, match="answered own-pr"):
        _authorize(world)
    assert len(_rows(handoff_lapse.CLOCK_KIND, "start")) == 1 and not _rows(handoff_lapse.LAPSED_KIND)


def test_after_seven_days_without_an_owner_pr_open_pr_proceeds_and_is_logged(world: dict,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(world["repo"])
    _days_later(monkeypatch, 7)
    assert _authorize(world)["grant_id"]
    rows = ledger.read()
    kinds = [r["kind"] for r in rows]
    assert kinds.index(handoff_lapse.LAPSED_KIND) > kinds.index("begun")   # logged after the grant was found
    [row] = _rows(handoff_lapse.LAPSED_KIND)
    assert row["op"] == "open-pr" and row["target"] == str(world["repo"]) and "4b4656f6a5" in row["detail"]
    assert "no owner PR updates csv-parse after 7 days" in row["detail"]
    [rec] = handoff.status({}, [world["repo"]])   # the summary shows it
    assert rec.lapses and f"own-pr lapsed {rec.lapses}" in handoff.markdown([rec])


def test_no_lapse_row_without_a_grant_that_covers_the_open_pr(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import grant
    monkeypatch.setattr(grant, "load_active", lambda: ([], []))   # no grant covers the open-pr
    _clock(world["repo"])
    _days_later(monkeypatch, 8)
    with pytest.raises(executor.ExecError, match="not authorized"):
        _authorize(world)
    assert not _rows(handoff_lapse.LAPSED_KIND)


def test_an_open_owner_pr_keeps_deferring_and_the_summary_names_it(world: dict,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    _clock(world["repo"])
    _days_later(monkeypatch, 30)
    world["prs"] = [{"number": 7, "title": "deps: csv-parse 7", "headRefName": "deps/csv-parse-7",
                     "author": {"login": "jlcatonjr"}, "isCrossRepository": False}]
    with pytest.raises(executor.ExecError, match="answered own-pr"):
        _authorize(world)
    [rec] = handoff.status({}, [world["repo"]])
    assert rec.owner_prs == [7] and "own-pr kept: owner PR #7 open" in handoff.markdown([rec])
    assert rec.to_dict()["owner_prs"] == [7] and not _rows(handoff_lapse.LAPSED_KIND)


@pytest.mark.parametrize("prs", [
    pytest.param([{"number": 8, "title": "csv-parse 7", "headRefName": "vulnscan/20261020-aaaaaaaaaa",
                   "isCrossRepository": False}], id="the-executors-own"),
    pytest.param([{"number": 9, "title": "csv-parse 7", "headRefName": "x", "isCrossRepository": True}], id="fork"),
    pytest.param([{"number": 10, "title": "csv-parser-utils", "headRefName": "x", "isCrossRepository": False}],
                 id="another-package"),
])
def test_owner_prs_ignore_the_executors_forks_and_other_packages(prs: list[dict]) -> None:
    assert open_prs.owner_prs(prs, ["csv-parse"]) == []


def test_an_unknown_owner_pr_state_keeps_deferring(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _clock(world["repo"])

    def gh_down(target: str) -> list:
        raise executor.ExecError("gh pr list failed")
    monkeypatch.setattr(open_prs, "list_open", gh_down)
    assert handoff_lapse.lapse_detail(world["repo"], rec, today=TODAY + timedelta(days=30)) == ""
    _stage(world["repo"], packages=(7, None))   # no usable package name: unknown, never a crash
    assert handoff_lapse.owner_pr(world["repo"]) is None
    assert "the staged packages" in handoff_lapse.packet_ask(world["repo"], BRANCH, rec)


def test_a_new_digest_plan_or_verdict_id_restarts_the_clock(world: dict) -> None:
    repo = world["repo"]
    first = _clock(repo)
    assert _clock(repo) == first and len(_rows(handoff_lapse.CLOCK_KIND, "start")) == 1   # read, not restarted
    new = "vulnscan/20261103-0123456789"
    _stage(repo, new)
    _verdict(repo, branch=new)
    assert _clock(repo, new)["digest"] == "0123456789"                                    # a re-staged, changed fix
    world["plan"] = "plan-2"
    assert _clock(repo, new)["plan"] == "plan-2"                                          # the fix plan changed
    _verdict(repo, branch=new, hid="20261104000001")
    assert _clock(repo, new)["handoff_id"] == "20261104000001"                            # a new own-pr verdict
    assert len(_rows(handoff_lapse.CLOCK_KIND, "start")) == 4


def test_own_pr_then_reject_then_own_pr_does_not_reuse_the_old_start(world: dict) -> None:
    repo = world["repo"]
    _clock(repo)
    _verdict(repo, "reject")
    assert _clock(repo) is None
    assert len(_rows(handoff_lapse.CLOCK_KIND, "stop")) == 1
    _verdict(repo)                                        # the same own-pr row again, same handoff id
    assert _clock(repo) is not None
    rows = _rows(handoff_lapse.CLOCK_KIND)
    assert [r["state"] for r in rows] == ["start", "stop", "start"]   # a fresh start, never the first one


@pytest.mark.parametrize("word", ["defer", "reject"])
def test_only_own_pr_lapses(world: dict, word: str) -> None:
    _verdict(world["repo"], word)
    assert _clock(world["repo"]) is None and not _rows(handoff_lapse.CLOCK_KIND)
    assert handoff_lapse.lapse_detail(world["repo"], None, today=TODAY + timedelta(days=30)) == ""


def test_an_unreadable_old_state_file_is_ignored(world: dict) -> None:
    (paths.state_dir() / "handoff-lapse.json").write_text("{not json", encoding="utf-8")   # the r1 state file
    rec = _clock(world["repo"])
    assert rec is not None and rec["since"] == TODAY.isoformat()   # the clock lives in the ledger only


def test_an_untrusted_ledger_starts_no_clock_and_lapses_nothing(world: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(**kw: object) -> list:
        raise ValueError("chain broken")
    monkeypatch.setattr(ledger, "read", broken)
    assert handoff_lapse.clock(world["repo"], BRANCH) is None


def test_the_packet_ask_and_status_name_the_lapse_date(world: dict, capsys: pytest.CaptureFixture[str]) -> None:
    repo = world["repo"]
    lapses = (TODAY + timedelta(days=7)).isoformat()
    [rec] = handoff.status({}, [repo])   # the summary's sight of the verdict starts the clock
    assert rec.lapses == lapses and _rows(handoff_lapse.CLOCK_KIND, "start")
    assert "open your own PR updating csv-parse" in rec.ask and f"by {lapses}" in rec.ask and BRANCH in rec.ask
    assert rec.to_dict()["ask"] == rec.ask and f"own-pr lapses {lapses}" in handoff.markdown([rec])
    from agentteams.vulnscan.__main__ import main
    assert main(["handoff", "status", "--repo", str(repo)]) == 0
    assert f"own-pr lapses: {lapses}" in capsys.readouterr().out
    _verdict(repo, "approve-staged")
    [rec] = handoff.status({}, [repo])
    assert rec.ask == "" and rec.lapses == "" and _rows(handoff_lapse.CLOCK_KIND, "stop")
