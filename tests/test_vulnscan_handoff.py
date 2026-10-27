"""Plan phase H: remediation handoff to the target repository's own orchestrator (agentteams.vulnscan.handoff).

H3: under handoff delivery the executor's ``open-pr`` waits for the repository orchestrator's ``approve-staged``
verdict, matched by the staged fix's content digest; the newest verdict wins. H4: ``handoff status`` and the summary
section show it. The verdicts file is written by an agent session, so it is read defensively.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams.vulnscan import executor, handoff

pytest.importorskip("cryptography")

from tests.test_vulnscan_executor import _git_repo, _install, env  # noqa: E402,F401  (env: the shared fixture)
from tests.test_vulnscan_executor_guard import _brief  # noqa: E402

HEADER = "handoff_id,verdict,branch,notes\n"


def _verdicts(home: Path, *rows: str, mtime: float | None = None) -> Path:
    d = home / handoff.HANDOFF_DIR
    d.mkdir(parents=True, exist_ok=True)
    f = d / handoff.VERDICTS_FILE
    f.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


def test_digest_and_homes(tmp_path: Path) -> None:
    assert handoff.digest_of("vulnscan/20261026-065858d058") == "065858d058"
    assert handoff.digest_of("vulnscan/x-065858d058") == "" and handoff.digest_of("main") == ""
    repo = tmp_path / "svc"
    (repo).mkdir()
    (tmp_path / "svc.worktrees" / "goose-agent-team").mkdir(parents=True)
    assert handoff.homes(repo) == [repo, tmp_path / "svc.worktrees" / "goose-agent-team"]


def test_the_newest_verdict_for_the_fix_digest_wins(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "svc")
    team = _git_repo(tmp_path / "svc.worktrees" / "goose-agent-team")   # homes are git work trees
    _verdicts(team, "20261025381042,approve-staged,vulnscan/20261025-065858d058,pin only", mtime=1000)
    # the same change re-staged the next day under a new branch name: the approval carries over
    found, problems = handoff.latest_verdict(repo, "vulnscan/20261026-065858d058")
    assert found is not None and found.verdict == "approve-staged" and problems == []
    assert handoff.approval_problem(repo, "vulnscan/20261026-065858d058") == ""
    # another change (another digest) is not approved by it
    assert "no verdict" in handoff.approval_problem(repo, "vulnscan/20261026-aaaaaaaaaa")
    # a later reject in another home (modified later) withdraws the approval
    _verdicts(repo, "20261026111111,reject,vulnscan/20261026-065858d058,changed my mind", mtime=2000)
    assert "answered reject" in handoff.approval_problem(repo, "vulnscan/20261026-065858d058")


def test_bad_rows_and_bad_files_are_ignored(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "svc")
    f = _verdicts(repo, "20261025381042,approved,vulnscan/20261025-065858d058,not a verdict word",
                  "x y,approve-staged,vulnscan/20261025-065858d058,bad id",
                  "20261025381042,approve-staged,main,not a vulnscan branch")
    rows, problems = handoff.read_verdicts(f.parent)
    assert rows == [] and len(problems) == 3
    f.unlink()
    (repo / handoff.HANDOFF_DIR / handoff.VERDICTS_FILE).symlink_to(tmp_path / "elsewhere.csv")
    assert "not a regular file" in handoff.read_verdicts(f.parent)[1][0]   # a symlink is never followed
    (repo / handoff.HANDOFF_DIR / handoff.VERDICTS_FILE).unlink()
    os.mkfifo(f)                                                             # nor a FIFO waited on
    assert "not a regular file" in handoff.read_verdicts(f.parent)[1][0]
    f.unlink()
    f.write_text(HEADER + "x" * (handoff.MAX_VERDICT_BYTES + 1), encoding="utf-8")
    assert "larger than" in handoff.read_verdicts(f.parent)[1][0]
    f.write_text(HEADER + "1," + "x" * 140_000 + ",vulnscan/20261026-aaaaaaaaaa,n\n", encoding="utf-8")
    assert "not a readable CSV" in handoff.read_verdicts(f.parent)[1][0]   # csv.Error is reported, never raised


def test_a_committed_verdicts_file_is_ignored(tmp_path: Path) -> None:
    """A verdict committed to the repository (or checked out in a worktree) would approve a fix in advance."""
    repo = _git_repo(tmp_path / "svc")
    f = _verdicts(repo, "20261026333333,approve-staged,vulnscan/20261026-065858d058,planted")
    import subprocess
    subprocess.run(["git", "-C", str(repo), "add", "-f", str(f)], check=True, capture_output=True)
    rows, problems = handoff.read_verdicts(f.parent)
    assert rows == [] and "tracked by git" in problems[0]
    assert "no verdict" in handoff.approval_problem(repo, "vulnscan/20261026-065858d058")


def test_tracking_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only git's "did not match" exit counts as untracked; any failure withholds the approval."""
    import subprocess
    home = tmp_path / "plain-dir-not-a-repo"
    _verdicts(home, "20261026333333,approve-staged,vulnscan/20261026-065858d058,ok")
    assert handoff.read_verdicts(home / handoff.HANDOFF_DIR)[0] == []   # git exits 128 here: ignored

    def timeout(*a: object, **k: object) -> None:
        raise subprocess.TimeoutExpired("git", 1)
    monkeypatch.setattr(executor, "query_git", timeout)
    assert handoff._tracked(home, "x") is True


def test_pending_packets_are_packets_only(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "svc")
    d = repo / handoff.HANDOFF_DIR
    d.mkdir(parents=True)
    for name in ("20261026138634.md", "20261025177303.md", "20261026138634-review-notes.md", "review-x.plan.md"):
        (d / name).write_text("x", encoding="utf-8")
    _verdicts(repo, "20261026138634,defer,vulnscan/20261026-10fd455261,cooldown")
    assert handoff.pending_packets(repo) == ["20261025177303"]


def test_required_by_operator_or_brief(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "svc")
    assert not handoff.required({}, repo)
    assert handoff.required({"handoff_required": True}, repo)
    assert not handoff.required({"handoff_required": "yes"}, repo)   # only a real true turns it on
    _brief(repo, {"level": "remediate", "delivery": "handoff"})
    assert handoff.required({}, repo)


def _stage(repo: Path, branch: str) -> None:
    d = executor.spool_dir() / "staged"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{executor.state_slug(repo)}.json").write_text(json.dumps({"repo": str(repo), "branch": branch}),
                                                          encoding="utf-8")


def test_open_pr_waits_for_the_orchestrators_approval(env: dict) -> None:
    repo = _git_repo(env["tmp"] / "repos" / "svc").resolve()
    _brief(repo, {"level": "remediate", "delivery": "handoff"})
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    _stage(repo, "vulnscan/20261026-065858d058")
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "refused" and "no verdict from the repository's orchestrator" in res["detail"], res
    _verdicts(repo, "20261026222222,defer,vulnscan/20261026-065858d058,cooldown")
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "refused" and "answered defer" in res["detail"], res
    _verdicts(repo, "20261026222222,defer,vulnscan/20261026-065858d058,cooldown",
              "20261026333333,approve-staged,vulnscan/20261026-065858d058,ok")
    from agentteams.vulnscan import ledger
    with ledger.locked():
        assert executor.authorize("open-pr", str(repo), "x", env["cfg"])["grant_id"]
        assert executor.authorize("stage-fix", str(repo), "y", env["cfg"])["grant_id"]   # stage-fix never waits


def test_direct_delivery_is_unchanged(env: dict) -> None:
    repo = _git_repo(env["tmp"] / "repos" / "svc").resolve()
    _install(env, ops=["open-pr"], hosts=[])
    _stage(repo, "vulnscan/20261026-065858d058")
    from agentteams.vulnscan import ledger
    with ledger.locked():
        assert executor.authorize("open-pr", str(repo), "x", env["cfg"])["grant_id"]
    with ledger.locked(), pytest.raises(executor.ExecError, match="no verdict"):   # operator switch: every repo
        executor.authorize("open-pr", str(repo), "x", {**env["cfg"], "handoff_required": True})


def test_status_markdown_and_cli(env: dict, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _git_repo(env["tmp"] / "repos" / "svc").resolve()
    _stage(repo, "vulnscan/20261026-4b4656f6a5")
    _verdicts(repo, "20261025171044,approve-staged,vulnscan/20261025-4b4656f6a5,ok")
    assert handoff.repos_with_staged_fixes() == [repo]
    [rec] = handoff.status({}, [repo])
    assert rec.state == "approved" and rec.verdict_handoff == "20261025171044" and not rec.required
    md = handoff.markdown([rec])
    assert "svc (direct delivery): approved; staged vulnscan/20261026-4b4656f6a5" in md
    assert handoff.markdown(handoff.status({}, [env["tmp"] / "nothing"])) == ""
    from agentteams.vulnscan.__main__ import main
    assert main(["handoff", "status", "--json"]) == 0
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert [(o["repo"], o["state"]) for o in out] == [(str(repo), "approved")]


def test_cli_text_output(env: dict, capsys: pytest.CaptureFixture[str]) -> None:
    repo = _git_repo(env["tmp"] / "repos" / "svc").resolve()
    _stage(repo, "vulnscan/20261026-10fd455261")
    d = repo / handoff.HANDOFF_DIR
    _verdicts(repo, "20261026138634,defer,vulnscan/20261026-10fd455261,cooldown")
    (d / "20261025177303.md").write_text("packet", encoding="utf-8")
    from agentteams.vulnscan.__main__ import main
    assert main(["handoff", "status", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert f"{repo}: defer (direct delivery)" in out and "staged: vulnscan/20261026-10fd455261" in out
    assert "verdict: defer (handoff 20261026138634)" in out and "packets awaiting a verdict: 20261025177303" in out
