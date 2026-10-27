"""close-superseded (only the executor's own, untouched, older PRs) and the operator-run merge digest.

No network: PR state comes from an injected lookup and closing is recorded, not performed.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, executor_supersede, grant, mcp, merge_digest  # noqa: E402

REPO = "/repos/app"
ME = {"name": executor.GIT_IDENTITY[0], "email": executor.GIT_IDENTITY[1]}
SHA = "0123456789abcdef0123456789abcdef01234567"


def _url(n: int, repo: str = "o/app") -> str:
    return f"https://github.com/{repo}/pull/{n}"


def _row(url: str, target: str = REPO, kind: str = "done") -> dict[str, Any]:
    return {"op": "open-pr", "kind": kind, "target": target, "detail": json.dumps({"pr": url})}


def _pr(**over: Any) -> dict[str, Any]:
    base = {"state": "OPEN", "headRefName": "vulnscan/20261027-abc", "isCrossRepository": False, "reviews": [],
            "comments": [], "commits": [{"authors": [ME]}]}
    return {**base, **over}


def _run(rows: list[dict[str, Any]], views: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    closed: list[str] = []
    out = executor_supersede.close_superseded(Path(REPO), rows, view=views.__getitem__,
                                              close=lambda url, comment: closed.append(url))
    return out, closed


# ---------------------------------------------------------------- close-superseded

def test_older_untouched_executor_prs_are_closed_once_the_replacement_is_open() -> None:
    rows = [_row(_url(1)), _row(_url(2)), _row(_url(3))]
    out, closed = _run(rows, {_url(1): _pr(), _url(2): _pr(), _url(3): _pr()})
    assert out["replacement"] == _url(3) and closed == [_url(1), _url(2)] and out["kept"] == []


def test_touched_or_foreign_prs_are_kept_with_a_reason() -> None:
    rows = [_row(_url(1)), _row(_url(2)), _row(_url(3)), _row(_url(4)), _row(_url(5, "o/other")), _row(_url(6)),
            _row(_url(9))]
    views = {_url(1): _pr(reviews=[{"state": "APPROVED"}]), _url(2): _pr(comments=[{"body": "wait"}]),
             _url(3): _pr(commits=[{"authors": [ME]}, {"authors": [{"name": "dev", "email": "dev@x"}]}]),
             _url(4): _pr(isCrossRepository=True), _url(5, "o/other"): _pr(), _url(6): _pr(state="MERGED"),
             _url(9): _pr()}
    out, closed = _run(rows, views)
    assert closed == []
    reasons = {k["pr"]: k["reason"] for k in out["kept"]}
    assert "reviews" in reasons[_url(1)] and "comments" in reasons[_url(2)] and "not made by" in reasons[_url(3)]
    assert "vulnscan/*" in reasons[_url(4)] and "another repository" in reasons[_url(5, "o/other")]
    assert _url(6) not in reasons                                     # already merged: nothing to do


def test_nothing_is_closed_without_an_open_replacement() -> None:
    with pytest.raises(executor.ExecError, match="nothing supersedes"):
        _run([], {})
    with pytest.raises(executor.ExecError, match="closing nothing"):
        _run([_row(_url(1)), _row(_url(2))], {_url(1): _pr(), _url(2): _pr(state="CLOSED")})
    with pytest.raises(executor.ExecError, match="closing nothing"):
        _run([_row(_url(1)), _row(_url(2))], {_url(1): _pr(), _url(2): _pr(headRefName="feature/x")})


def test_only_this_repositorys_successful_open_pr_rows_are_candidates() -> None:
    rows = [_row(_url(1), target="/repos/elsewhere"), _row(_url(2), kind="failed"), _row(_url(3)),
            {"op": "open-pr", "kind": "done", "target": REPO, "detail": "not json"}, _row(_url(4)), _row(_url(3))]
    assert executor_supersede.ledger_prs(REPO, rows) == [_url(4), _url(3)]   # a re-claimed PR moves to newest


def test_the_op_is_wired_but_never_implied() -> None:
    assert "close-superseded" in grant.OPS_V1 and "close-superseded" in mcp.OPS
    assert "close-superseded" in executor.REPO_OPS and "merge" in grant.OPS_RESERVED


# ---------------------------------------------------------------- merge digest

def test_green_needs_open_mergeable_and_every_check_passed() -> None:
    ok = {"state": "OPEN", "mergeable": "MERGEABLE", "headRefOid": SHA,
          "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}]}
    assert merge_digest.green("github.com/o/app", 7, ok) == merge_digest.GreenPr("github.com/o/app", 7, SHA)
    assert merge_digest.green("github.com/o/app", 7, {**ok, "statusCheckRollup": []}) is None
    assert merge_digest.green("github.com/o/app", 7, {**ok, "statusCheckRollup": [{"conclusion": "FAILURE"}]}) is None
    assert merge_digest.green("github.com/o/app", 7, {**ok, "mergeable": "CONFLICTING"}) is None
    assert merge_digest.green("github.com/o/app", 7, {**ok, "headRefOid": "abc; rm -rf ~"}) is None
    assert merge_digest.green("github.com/o/app;x", 7, ok) is None


def test_the_digest_asks_per_pr_and_pins_the_head(tmp_path: Path) -> None:
    prs = [merge_digest.GreenPr("github.com/o/app", 7, SHA)]
    path = merge_digest.write(tmp_path, prs)
    assert path is not None and stat.S_IMODE(os.stat(path).st_mode) == 0o700
    text = path.read_text()
    assert "ask " in text and "</dev/tty" in text
    merge = next(ln for ln in text.splitlines() if "gh pr merge" in ln)
    argv = shlex.split(merge.split("||")[0])
    assert argv == ["gh", "pr", "merge", "7", "--repo", "github.com/o/app", "--merge", "--match-head-commit", SHA]
    assert "No green remediation PRs" in merge_digest.render_script([], "2026-10-27")


def test_a_digest_identifier_that_is_not_validated_is_refused() -> None:
    for bad in (("github.com/o/app", 0, SHA), ("github.com/o/app", 7, "HEAD"), ("o/$(x)/y", 7, SHA)):
        with pytest.raises(ValueError):
            merge_digest.GreenPr(*bad)


def test_a_pr_touched_between_the_first_look_and_the_close_is_kept() -> None:
    """@security B1: the candidate is looked at again right before it is closed."""
    rows = [_row(_url(1)), _row(_url(2))]
    seen = {"n": 0}

    def view(url: str) -> dict[str, Any]:
        if url == _url(1):
            seen["n"] += 1
            return _pr() if seen["n"] == 1 else _pr(comments=[{"body": "hold on"}])
        return _pr()

    closed: list[str] = []
    out = executor_supersede.close_superseded(Path(REPO), rows, view=view, close=lambda u, c: closed.append(u))
    assert closed == [] and seen["n"] == 2 and "comments" in out["kept"][0]["reason"]


def test_a_check_entry_that_is_not_an_object_never_passes() -> None:
    """@security B3."""
    data = {"state": "OPEN", "mergeable": "MERGEABLE", "headRefOid": SHA,
            "statusCheckRollup": [{"conclusion": "SUCCESS"}, "SUCCESS"]}
    assert merge_digest.green("github.com/o/app", 7, data) is None
    assert merge_digest.green("github.com/o/app", 7, {**data, "statusCheckRollup": "SUCCESS"}) is None


def test_the_digest_comment_names_only_a_dated_run() -> None:
    """@security B2."""
    assert "for run 2026-10-27:" in merge_digest.render_script([], "2026-10-27")
    text = merge_digest.render_script([], "x$(touch /tmp/p)\nrm -rf ~")
    assert "(unknown)" in text and "touch" not in text and "rm -rf" not in text


def test_close_superseded_alone_still_needs_a_repo_root(tmp_path: Path) -> None:
    """@conflict-auditor 3: a repo op grant without roots could never be used."""
    gov = tmp_path / "gov" / "references"
    gov.mkdir(parents=True)
    (gov / "security-decisions.log.csv").write_text("date\n")
    with pytest.raises(grant.GrantError, match="close-superseded need at least one repo root"):
        grant.build(ops=["close-superseded"], repo_roots=[], hosts=[], days=1, max_uses=1, key_id="op",
                    governance_dir=str(tmp_path / "gov"))
