"""vfr-b.B1b: an ``own-pr`` verdict lapses after seven days without an owner PR (operator decision OD10).

Colorado-collectors answered ``own-pr`` for a staged csv-parse 7 fix twice and never pushed its own branch, so the
fix waited indefinitely. The clock starts at the first sight of ``own-pr`` for a fix digest (a new digest or a changed
plan restarts it); until it runs out each refusal, the summary and ``handoff status`` name the lapse date and the
request the next packet carries; after it, with no owner PR, ``open-pr`` proceeds and a ``handoff-lapsed`` row is
logged. Network-free: gh's open-PR listing and the plan digest are stubbed.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, handoff, handoff_lapse, ledger, open_prs, retry_guard  # noqa: E402
from tests.test_vulnscan_executor import _git_repo, _install, env  # noqa: E402,F401  (env: the shared fixture)
from tests.test_vulnscan_executor_guard import _brief  # noqa: E402

BRANCH = "vulnscan/20261025-4b4656f6a5"
HEADER = "handoff_id,verdict,branch,notes\n"
DAY0 = date(2026, 10, 25)


def _verdict(repo: Path, word: str = "own-pr", branch: str = BRANCH) -> None:
    d = repo / handoff.HANDOFF_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / handoff.VERDICTS_FILE).write_text(f"{HEADER}20261025171044,{word},{branch},we push our own\n",
                                           encoding="utf-8")


def _stage(repo: Path, branch: str = BRANCH, packages: tuple[str, ...] = ("csv-parse",)) -> None:
    d = executor.spool_dir() / "staged"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{executor.state_slug(repo)}.json").write_text(json.dumps(
        {"repo": str(repo), "branch": branch, "worktree": str(repo), "packages": list(packages)}), encoding="utf-8")


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
    monkeypatch.setattr(executor, "_origin_repo", lambda wt: "github.com/o/colorado")
    monkeypatch.setattr(open_prs, "list_open", lambda target: state["prs"])
    return state


def _authorize(world: dict) -> dict:
    with ledger.locked():
        return executor.authorize("open-pr", str(world["repo"]), "r" * 32, world["env"]["cfg"])


def test_before_seven_days_open_pr_keeps_deferring_and_names_the_lapse_date(world: dict) -> None:
    repo = world["repo"]
    assert handoff_lapse.clock(repo, BRANCH, today=DAY0)["lapses"] == "2026-11-01"   # the clock starts here
    assert handoff_lapse.lapsed(repo, BRANCH, today=DAY0 + timedelta(days=6)) == ""
    assert handoff_lapse.clock(repo, BRANCH, today=DAY0 + timedelta(days=6))["since"] == "2026-10-25"   # not restarted
    today = date.today()   # authorize uses the real date: a clock started today has a week to run
    (executor.paths.state_dir() / "handoff-lapse.json").unlink()
    with pytest.raises(executor.ExecError, match="answered own-pr") as err:
        _authorize(world)
    assert f"lapses on {(today + timedelta(days=7)).isoformat()}" in str(err.value)
    assert not [r for r in ledger.read() if r.get("kind") == handoff_lapse.LAPSED_KIND]


def test_after_seven_days_without_an_owner_pr_open_pr_proceeds_and_is_logged(world: dict) -> None:
    repo = world["repo"]
    handoff_lapse.clock(repo, BRANCH, today=date.today() - timedelta(days=7))
    assert "no owner PR updates csv-parse after 7 days" in handoff_lapse.lapsed(repo, BRANCH)
    assert _authorize(world)["grant_id"]
    [row] = [r for r in ledger.read() if r.get("kind") == handoff_lapse.LAPSED_KIND]
    assert row["op"] == "open-pr" and row["target"] == str(repo) and "4b4656f6a5" in row["detail"]
    [rec] = handoff.status({}, [repo])   # (c) the summary shows it
    assert rec.lapses and f"own-pr lapsed {rec.lapses}" in handoff.markdown([rec])


def test_an_open_owner_pr_keeps_deferring(world: dict) -> None:
    handoff_lapse.clock(world["repo"], BRANCH, today=date.today() - timedelta(days=30))
    world["prs"] = [{"number": 7, "title": "deps: csv-parse 7", "headRefName": "deps/csv-parse-7",
                     "author": {"login": "jlcatonjr"}, "isCrossRepository": False}]
    assert handoff_lapse.lapsed(world["repo"], BRANCH) == ""
    with pytest.raises(executor.ExecError, match="answered own-pr"):
        _authorize(world)


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
    handoff_lapse.clock(world["repo"], BRANCH, today=date.today() - timedelta(days=30))

    def gh_down(target: str) -> list:
        raise executor.ExecError("gh pr list failed")
    monkeypatch.setattr(open_prs, "list_open", gh_down)
    assert handoff_lapse.lapsed(world["repo"], BRANCH) == ""


def test_a_new_digest_or_a_changed_plan_restarts_the_clock(world: dict) -> None:
    repo = world["repo"]
    handoff_lapse.clock(repo, BRANCH, today=DAY0)
    late = DAY0 + timedelta(days=10)
    new = "vulnscan/20261103-0123456789"
    _stage(repo, new)
    _verdict(repo, branch=new)
    assert handoff_lapse.clock(repo, new, today=late)["since"] == late.isoformat()      # a re-staged, changed fix
    assert handoff_lapse.lapsed(repo, new, today=late) == ""
    world["plan"] = "plan-2"
    assert handoff_lapse.clock(repo, new, today=late + timedelta(days=8))["since"] == \
        (late + timedelta(days=8)).isoformat()                                             # the fix plan changed


@pytest.mark.parametrize("word", ["defer", "reject"])
def test_only_own_pr_lapses(world: dict, word: str) -> None:
    _verdict(world["repo"], word)
    assert handoff_lapse.clock(world["repo"], BRANCH, today=DAY0) is None
    assert handoff_lapse.lapsed(world["repo"], BRANCH, today=DAY0 + timedelta(days=30)) == ""


def test_the_packet_ask_and_status_name_the_lapse_date(world: dict, capsys: pytest.CaptureFixture[str]) -> None:
    repo = world["repo"]
    ask = handoff_lapse.packet_ask(repo, BRANCH)
    lapses = (date.today() + timedelta(days=7)).isoformat()
    assert "open your own PR updating csv-parse" in ask and f"by {lapses}" in ask and BRANCH in ask
    [rec] = handoff.status({}, [repo])
    assert rec.lapses == lapses and rec.ask == ask and rec.to_dict()["ask"] == ask
    assert f"own-pr lapses {lapses}" in handoff.markdown([rec])
    from agentteams.vulnscan.__main__ import main
    assert main(["handoff", "status", "--repo", str(repo)]) == 0
    assert f"own-pr lapses: {lapses}" in capsys.readouterr().out
    _verdict(repo, "approve-staged")
    assert handoff_lapse.packet_ask(repo, BRANCH) == ""
