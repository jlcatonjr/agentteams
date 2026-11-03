"""vfr-d D6: a writer's same-repository open PR holds its packages back until the Rule S-VM deadline.

Conditions (``@security`` 2026-11-02): live collaborator-permission API, hold only if the diff moves the package to
the fixed version or later, the hold expires at the S-VM deadline, the report says "held by #N", the diff is parsed
as data, names are capped; a gh failure holds nothing. Network-free: gh is a fake.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentteams.vulnscan import fix, open_prs, osv, writer_prs
from agentteams.vulnscan.executor_env import ExecError
from tests.test_vulnscan_executor import env  # noqa: F401  (the shared fixture)

REPO = "github.com/o/svc"
DIFF = """diff --git a/requirements.txt b/requirements.txt
index 1..2 100644
--- a/requirements.txt
+++ b/requirements.txt
@@ -1,2 +1,2 @@
-Flask-Cors==4.0.0
+Flask-Cors==6.0.1
 gunicorn==20.1.0
diff --git a/web/package-lock.json b/web/package-lock.json
--- a/web/package-lock.json
+++ b/web/package-lock.json
@@ -10,7 +10,7 @@
     "node_modules/vite": {
-      "version": "6.4.4",
+      "version": "6.4.5",
       "resolved": "https://registry.npmjs.org/vite/-/vite-6.4.5.tgz",
diff --git a/README.md b/README.md
+Flask-Cors==99.0.0
"""
WRITER = {"number": 31, "title": "deps: my own bumps", "headRefName": "deps/bumps", "author": {"login": "alice"},
          "isCrossRepository": False}


def _gh(perm: str = "write", diff: str = DIFF, fail: str = "") -> tuple[list[list[str]], object]:
    calls: list[list[str]] = []

    def gh(argv: list[str]) -> str:
        calls.append(argv)
        if fail and argv[0] == fail:
            raise ExecError(f"gh {fail} failed: HTTP 502")
        if argv[0] == "api":
            return json.dumps({"permission": perm, "role_name": perm})
        return diff
    return calls, gh


@pytest.fixture
def lock_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._vulnscan_helpers import operator_env
    operator_env(tmp_path, monkeypatch)


# ---------------------------------------------------------------- the diff is data

def test_the_diff_yields_only_executor_files_and_plain_names() -> None:
    assert writer_prs.parse_diff(DIFF) == {("requirements.txt", "flask-cors"): ["6.0.1"],
                                           ("web/package-lock.json", "vite"): ["6.4.5"]}
    odd = DIFF.replace("Flask-Cors==6.0.1", "Flask-Cors==6.0.1;rm -rf").replace('"6.4.5"', '"$(id)"')
    assert writer_prs.parse_diff(odd) == {("requirements.txt", "flask-cors"): ["6.0.1"]}   # the marker is not version


def test_permission_comes_from_the_collaborator_api(lock_env: None) -> None:
    calls, gh = _gh("admin")
    assert writer_prs.can_write(REPO, "alice", gh=gh)
    assert calls == [["api", "--hostname", "github.com", "repos/o/svc/collaborators/alice/permission"]]
    for perm in ("read", "triage", "none"):
        assert not writer_prs.can_write(REPO, "alice", gh=_gh(perm)[1])
    assert not writer_prs.can_write(REPO, "alice", gh=_gh(fail="api")[1])          # gh failure: no hold
    assert not writer_prs.can_write(REPO, "alice;rm", gh=_gh()[1])                 # an odd login is never asked


@pytest.mark.parametrize("pr,why", [
    ({**WRITER, "isCrossRepository": True}, "fork"),
    ({**WRITER, "isCrossRepository": None}, "unknown origin"),
    ({**WRITER, "headRefName": "vulnscan/20261101-abcdef0123"}, "the executor's own"),
    ({**WRITER, "author": {"login": "app/dependabot"}}, "a bot (open_prs handles it)"),
])
def test_only_a_same_repo_person_pr_is_a_candidate(lock_env: None, pr: dict, why: str) -> None:
    calls, gh = _gh()
    assert writer_prs.holds(REPO, [pr], gh=gh) == {} and calls == [], why


def test_a_reader_a_huge_diff_too_many_names_or_a_gh_failure_hold_nothing(lock_env: None,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    assert writer_prs.holds(REPO, [WRITER], gh=_gh("read")[1]) == {}
    assert writer_prs.holds(REPO, [WRITER], gh=_gh(fail="pr")[1]) == {}
    many = "diff --git a/requirements.txt b/requirements.txt\n" + "".join(f"+p{i}==1.0.0\n" for i in range(51))
    assert writer_prs.holds(REPO, [WRITER], gh=_gh(diff=many)[1]) == {}
    monkeypatch.setattr(writer_prs, "MAX_DIFF_BYTES", 100)
    assert writer_prs.holds(REPO, [WRITER], gh=_gh()[1]) == {}


def test_a_hold_starts_once_in_the_ledger(lock_env: None) -> None:
    from agentteams.vulnscan import ledger
    with ledger.locked():
        first = writer_prs.holds(REPO, [WRITER], gh=_gh()[1])
        again = writer_prs.holds(REPO, [WRITER], gh=_gh()[1])
    assert first == again and first["flask-cors"][0].number == 31
    assert len([r for r in ledger.read() if r.get("kind") == writer_prs.CLOCK_KIND]) == 2   # flask-cors, vite


def test_a_new_pr_for_the_same_package_keeps_the_first_clock(lock_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing a PR and opening another never renews a hold (@security review: keyed by repository and package)."""
    from agentteams.vulnscan import ledger
    with ledger.locked():
        first = writer_prs.holds(REPO, [WRITER], gh=_gh()[1])
        renewed = writer_prs.holds(REPO, [{**WRITER, "number": 32, "headRefName": "deps/again"}], gh=_gh()[1])
    [h1], [h2] = first["flask-cors"], renewed["flask-cors"]
    assert h2.number == 32 and h2.since == h1.since
    _severity(monkeypatch, 3)
    assert writer_prs.deadline(h2, ["GHSA-x"]) == writer_prs.deadline(h1, ["GHSA-x"])
    rows = [r for r in ledger.read() if r.get("kind") == writer_prs.CLOCK_KIND and r.get("name") == "flask-cors"]
    assert [r["number"] for r in rows] == [31]   # one clock; the new PR wrote none


def test_an_expired_hold_stays_expired_when_a_new_pr_opens(lock_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import ledger
    _severity(monkeypatch, 4)   # critical: 24 h
    with ledger.locked():
        writer_prs.holds(REPO, [WRITER], gh=_gh()[1])
        claims = writer_prs.Claims({}, writer_prs.holds(REPO, [{**WRITER, "number": 33}], gh=_gh()[1]))
    [hold] = claims.holds["flask-cors"]
    row = _row()
    writer_prs.leave_to_prs([row], claims, now=hold.since.astimezone(UTC) + timedelta(hours=25))
    assert row.action == "fix" and "the hold by #33 expired" in row.note


def test_staging_claims_fail_open_on_a_listing_failure(lock_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(target: str) -> list:
        raise ExecError("gh pr list (open PRs) failed: HTTP 503")
    monkeypatch.setattr(open_prs, "list_open", broken)
    claims = writer_prs.staging_claims(lambda: REPO, gh=_gh()[1])
    assert claims == {} and claims.holds == {}


# ---------------------------------------------------------------- which rows are held, and until when

def _hold(path: str = "requirements.txt", versions: tuple[str, ...] = ("6.0.1",), hours: float = 0) -> writer_prs.Claims:
    since = datetime(2026, 11, 2, 9, tzinfo=UTC)
    return writer_prs.Claims({}, {"flask-cors": [writer_prs.Hold(31, path, versions, since)]}), since + timedelta(
        hours=hours)


def _row() -> fix.PinChange:
    return fix.PinChange("requirements.txt", "Flask-Cors", "4.0.0", "6.0.0", ids=["GHSA-x"], note="1 advisories")


def _severity(monkeypatch: pytest.MonkeyPatch, rank: int) -> None:
    monkeypatch.setattr(osv, "details", lambda vid: {"id": vid})
    monkeypatch.setattr(osv, "severity", lambda rec: rank)


def test_a_move_to_the_fixed_version_or_later_is_held_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _severity(monkeypatch, 3)
    claims, now = _hold(hours=24 * 6)
    row = _row()
    writer_prs.leave_to_prs([row], claims, now=now)
    assert row.action == "pr-held" and row.note.startswith("held by #31") and "expires 2026-11-09 09:00 UTC" in row.note
    assert open_prs.left_to_prs([row], claims) == {"Flask-Cors": [31]}   # the PR body's "left to #31"


@pytest.mark.parametrize("claims_args", [{"versions": ("5.9.0",)}, {"versions": ("6.0.1", "5.0.0")},
                                         {"path": "other/requirements.txt"}, {"versions": ()}])
def test_a_move_short_of_the_fix_or_in_another_file_is_not_held(monkeypatch: pytest.MonkeyPatch,
                                                                claims_args: dict) -> None:
    _severity(monkeypatch, 3)
    claims, now = _hold(**claims_args)
    row = _row()
    writer_prs.leave_to_prs([row], claims, now=now)
    assert row.action == "fix"


@pytest.mark.parametrize("rank,hours,held", [(4, 23, True), (4, 25, False), (3, 24 * 7 + 1, False),
                                             (0, 24 * 6, True), (2, 24 * 29, True), (2, 24 * 31, False)])
def test_the_hold_expires_at_the_s_vm_deadline(monkeypatch: pytest.MonkeyPatch, rank: int, hours: float,
                                               held: bool) -> None:
    _severity(monkeypatch, rank)
    claims, now = _hold(hours=hours)
    row = _row()
    writer_prs.leave_to_prs([row], claims, now=now)
    assert (row.action == "pr-held") is held
    if not held:
        assert "the hold by #31 expired (Rule S-VM deadline): fixed here" in row.note


def test_an_unreadable_advisory_gets_the_shortest_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import _http

    def down(vid: str) -> dict:
        raise _http.FeedError("osv down")
    monkeypatch.setattr(osv, "details", down)
    claims, now = _hold(hours=25)
    row = _row()
    writer_prs.leave_to_prs([row], claims, now=now)
    assert row.action == "fix"


def test_bot_claims_still_come_first_and_plain_dicts_still_work() -> None:
    row = _row()
    writer_prs.leave_to_prs([row], {"flask-cors": [227]})
    assert row.action == "pr-held" and row.note.startswith("left to #227")


# ---------------------------------------------------------------- end to end through the executor

def _executor_world(env: dict, monkeypatch: pytest.MonkeyPatch, gh: object) -> Path:
    from tests.test_vulnscan_stage_major import _pip_world
    monkeypatch.setattr(writer_prs, "_gh", gh)
    _severity(monkeypatch, 3)
    return _pip_world(env, monkeypatch, [WRITER])


def test_stage_fix_leaves_a_writers_package_and_the_plan_says_held_by(env: dict,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cryptography")
    from agentteams.vulnscan import executor, ledger
    from tests.test_vulnscan_executor import GIT
    if not GIT:
        pytest.skip("git not available")
    repo = _executor_world(env, monkeypatch, _gh()[1])
    res = executor.act("stage-major", str(repo), env["cfg"])
    assert res["status"] == "done", res
    staged = json.loads(executor.staged_file(repo.resolve()).read_text())
    assert staged["packages"] == ["gunicorn"] and staged["left_to_prs"] == {"Flask-Cors": [31]}
    assert "held by #31" in staged["plan"]
    assert [r["name"] for r in ledger.read() if r.get("kind") == writer_prs.CLOCK_KIND] == ["flask-cors", "vite"]


def test_a_gh_failure_holds_nothing_and_the_fix_proceeds(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("cryptography")
    from agentteams.vulnscan import executor
    from tests.test_vulnscan_executor import GIT
    if not GIT:
        pytest.skip("git not available")
    repo = _executor_world(env, monkeypatch, _gh(fail="api")[1])
    res = executor.act("stage-major", str(repo), env["cfg"])
    assert res["status"] == "done" and "left_to_prs" not in res, res
    staged = json.loads(executor.staged_file(repo.resolve()).read_text())
    assert staged["packages"] == ["Flask-Cors", "gunicorn"]
