"""Branch lifecycle: --branch-inventory states/holds, --branch-cleanup, --branch-post-merge.

Fixture repositories: a bare "origin" plus a working clone, built per test. The GitHub API is a
stub (:class:`FakeAPI`); ``refs/pull/<n>/head`` is created in the bare remote with update-ref.
Covers the acceptance list of tmp/by-week/2026-W40/branch-lifecycle-standardization.plan.md §4.
"""

from __future__ import annotations

import csv
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

import pytest

from agentteams import branch_cleanup as bc
from agentteams import branch_inventory as bi
from agentteams.cli import grants

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
OLD = "2026-08-01T12:00:00+00:00"
RECENT = "2026-10-03T12:00:00+00:00"
ME = "op@example.com"
REPO = "acme/widget"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def run(cwd: Path, *args: str, date: str = RECENT, email: str = ME) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
           "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": "A",
           "GIT_COMMITTER_EMAIL": email, "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo: Path, name: str, text: str, *, date: str = RECENT, email: str = ME) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    run(repo, "add", name, date=date, email=email)
    run(repo, "commit", "-q", "-m", f"edit {name}", date=date, email=email)
    return run(repo, "rev-parse", "HEAD")


class Repo:
    """A working clone plus its bare origin."""

    def __init__(self, tmp: Path) -> None:
        self.origin = tmp / "origin.git"
        self.work = tmp / "work"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.work.mkdir()
        run(self.work, "init", "-q", "-b", "main")
        run(self.work, "config", "user.email", ME)
        run(self.work, "config", "user.name", "A")
        run(self.work, "remote", "add", "origin", str(self.origin))
        commit(self.work, "base.txt", "base\n", date=OLD)
        run(self.work, "push", "-q", "-u", "origin", "main")
        run(self.work, "remote", "set-head", "origin", "main")

    def git(self, *args: str, **kw) -> str:
        return run(self.work, *args, **kw)

    def branch(self, name: str, files: dict[str, str], *, date: str = RECENT,
               email: str = ME, push: bool = True) -> str:
        self.git("checkout", "-q", "-b", name, "main")
        tip = ""
        for path, text in files.items():
            tip = commit(self.work, path, text, date=date, email=email)
        if push:
            self.git("push", "-q", "-u", "origin", name)
        self.git("checkout", "-q", "main")
        return tip

    def merge_no_ff(self, name: str, *, push: bool = True) -> str:
        self.git("merge", "-q", "--no-ff", "-m", f"Merge branch '{name}'", name)
        if push:
            self.git("push", "-q", "origin", "main")
        return self.git("rev-parse", "HEAD")

    def squash(self, name: str, *, push: bool = True) -> str:
        self.git("merge", "-q", "--squash", name)
        self.git("commit", "-q", "-m", f"{name} (#1)")
        if push:
            self.git("push", "-q", "origin", "main")
        return self.git("rev-parse", "HEAD")

    def pull_ref(self, number: int, sha: str) -> None:
        subprocess.run(["git", "--git-dir", str(self.origin), "update-ref",
                        f"refs/pull/{number}/head", sha], check=True)

    def refs(self) -> str:
        return self.git("for-each-ref", "--format=%(refname) %(objectname)")


class FakeAPI:
    """Stub of :class:`agentteams.branch_inventory.GitHubAPI`."""

    def __init__(self, *, open_prs=(), closed=None, protected=(), fail_open=False) -> None:
        self.repo = REPO
        self.open_prs = list(open_prs)
        self.closed = closed or {}
        self.protected = set(protected)
        self.fail_open = fail_open

    def get(self, path: str):
        if path.startswith("branches/"):
            return "ok", {"protected": unquote(path.split("/", 1)[1]) in self.protected}
        if path.startswith("rules/branches/"):
            return "ok", []
        if path.startswith("pulls/"):
            number = int(path.split("/")[1])
            for prs in self.closed.values():
                for pr in prs:
                    if pr["number"] == number:
                        return "ok", pr
            return bi.API_NOT_FOUND, None
        return bi.API_UNKNOWN, None

    def get_all(self, path: str):
        if path.startswith("pulls?state=open"):
            return (bi.API_UNKNOWN, []) if self.fail_open else ("ok", self.open_prs)
        if path.startswith("pulls?state=closed&head="):
            head = path.split("head=", 1)[1].replace("%3A", ":").replace("%2F", "/")
            return "ok", self.closed.get(head.split(":", 1)[1], [])
        return bi.API_UNKNOWN, []


def pr(number: int, branch: str, sha: str, merge_sha: str, *, repo: str = REPO,
       base: str = "main", merged: bool = True) -> dict:
    return {"number": number, "head": {"ref": branch, "sha": sha, "repo": {"full_name": repo}},
            "base": {"ref": base}, "merged_at": "2026-10-03T00:00:00Z" if merged else None,
            "merge_commit_sha": merge_sha}


def open_pr(head: str, base: str = "main", repo: str = REPO) -> dict:
    return {"head": {"ref": head, "repo": {"full_name": repo}}, "base": {"ref": base}}


@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def inventory(r: Repo, api=None, **kw) -> dict:
    cfg = bi.InventoryConfig(repo=r.work, now=NOW, fetch=True, **kw)
    return bi.build_inventory(cfg, api=api if api is not None else FakeAPI())


def rec(inv: dict, branch: str, ref_type: str = "remote") -> dict:
    return next(x for x in inv["refs"] if x["branch"] == branch and x["ref_type"] == ref_type)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


def test_no_ff_merged_branch_is_merged_by_ancestry(repo):
    repo.branch("feat/a", {"a.txt": "a\n"})
    repo.merge_no_ff("feat/a")
    inv = inventory(repo)
    for ref_type in ("local", "remote"):
        r = rec(inv, "feat/a", ref_type)
        assert (r["state"], r["action"], r["via"]) == ("merged", "delete", "ancestor")
        assert r["own_commits"] == 1
    assert rec(inv, "main", "local")["state"] == "default"


def test_merged_release_branch_is_release_finished(repo):
    repo.branch("release/1.0", {"r.txt": "r\n"})
    repo.merge_no_ff("release/1.0")
    r = rec(inventory(repo), "release/1.0")
    assert (r["state"], r["action"]) == ("release-finished", "tag-then-delete")


def test_empty_fresh_branch_is_active_not_merged(repo):
    repo.git("branch", "feat/new", "main")
    r = rec(inventory(repo), "feat/new", "local")
    assert r["ancestor"] and r["own_commits"] == 0
    assert (r["state"], r["action"]) == ("active", "keep")


def test_local_with_unpushed_work_is_classified_separately(repo):
    repo.branch("feat/b", {"b.txt": "b\n"})
    repo.merge_no_ff("feat/b")
    repo.git("checkout", "-q", "feat/b")
    commit(repo.work, "b2.txt", "more\n")
    repo.git("checkout", "-q", "main")
    inv = inventory(repo)
    assert rec(inv, "feat/b", "remote")["state"] == "merged"
    assert rec(inv, "feat/b", "local")["state"] == "active"
    assert rec(inv, "feat/b", "local")["action"] == "keep"


def test_single_commit_squash_patch_equivalent_without_api_merged_by_pr_with_it(repo):
    tip = repo.branch("feat/s1", {"s.txt": "s\n"})
    merge = repo.squash("feat/s1")
    repo.pull_ref(7, tip)
    no_api = bi.build_inventory(bi.InventoryConfig(repo=repo.work, now=NOW, use_api=False))
    assert rec(no_api, "feat/s1")["state"] == "patch-equivalent"
    assert rec(no_api, "feat/s1")["action"] == "ask-operator"
    api = FakeAPI(closed={"feat/s1": [pr(7, "feat/s1", tip, merge)]})
    r = rec(inventory(repo, api), "feat/s1")
    assert (r["state"], r["action"], r["via"]) == ("merged-by-pr", "delete", "pr#7")


def test_multi_commit_squash_has_unique_patches_but_is_merged_by_pr(repo):
    tip = repo.branch("feat/s3", {"x.txt": "1\n", "y.txt": "2\n", "z.txt": "3\n"})
    merge = repo.squash("feat/s3")
    repo.pull_ref(8, tip)
    no_api = bi.build_inventory(bi.InventoryConfig(repo=repo.work, now=NOW, use_api=False))
    assert rec(no_api, "feat/s3")["unique_patches"] == 3
    assert rec(no_api, "feat/s3")["state"] == "active"
    api = FakeAPI(closed={"feat/s3": [pr(8, "feat/s3", tip, merge)]})
    assert rec(inventory(repo, api), "feat/s3")["state"] == "merged-by-pr"


def test_whitespace_only_equivalent_patch_is_never_merged(repo):
    repo.branch("feat/ws", {"w.txt": "a  b\n"})
    commit(repo.work, "w.txt", "a b\n")  # main gets the same change modulo whitespace
    repo.git("push", "-q", "origin", "main")
    r = rec(inventory(repo), "feat/ws")
    assert not r["ancestor"]
    assert r["state"] == "patch-equivalent" and r["action"] == "ask-operator"
    assert not inventory(repo)["plan"]["items"]


def test_stale_unmerged_is_reported_never_deleted(repo):
    repo.branch("feat/old", {"o.txt": "o\n"}, date=OLD)
    r = rec(inventory(repo), "feat/old")
    assert (r["state"], r["action"]) == ("stale-unmerged", "ask-operator")


def test_protected_and_evergreen_are_kept(repo):
    (repo.work / ".github" / "workflows").mkdir(parents=True)
    (repo.work / ".github" / "workflows" / "bot.yml").write_text(
        "jobs:\n  x:\n    steps:\n      - with:\n          branch: auto/update-${{ x }}\n", "utf-8")
    run(repo.work, "add", "-A")
    run(repo.work, "commit", "-q", "-m", "wf")
    repo.git("push", "-q", "origin", "main")
    repo.branch("auto/update-123", {"u.txt": "u\n"})
    repo.branch("keep/me", {"k.txt": "k\n"})
    repo.merge_no_ff("keep/me")
    inv = inventory(repo, FakeAPI(protected={"keep/me"}))
    assert rec(inv, "auto/update-123")["state"] == "evergreen"
    assert rec(inv, "keep/me")["state"] == "protected"
    assert rec(inv, "keep/me", "local")["state"] == "merged"  # protection is a remote property


# ---------------------------------------------------------------------------
# Holds
# ---------------------------------------------------------------------------


def test_open_pr_head_and_base_hold(repo):
    for name in ("feat/h", "feat/base"):
        repo.branch(name, {f"{name[-1]}.txt": "x\n"})
        repo.merge_no_ff(name)
    api = FakeAPI(open_prs=[open_pr("feat/h"), open_pr("other", base="feat/base")])
    inv = inventory(repo, api)
    assert "open-pr:head" in rec(inv, "feat/h")["holds"]
    assert "open-pr:base" in rec(inv, "feat/base")["holds"]
    assert {i["branch"] for i in inv["plan"]["items"]} == set()


def test_link_hold(repo):
    repo.branch("feat/l", {"l.txt": "l\n"})
    repo.merge_no_ff("feat/l")
    commit(repo.work, "letter.md", "see https://github.com/acme/widget/tree/feat/l for it\n")
    repo.git("push", "-q", "origin", "main")
    r = rec(inventory(repo), "feat/l")
    assert "link" in r["holds"] and r["action"] == "keep"
    assert "letter.md" in r["cited_by"]


def test_collaborator_author_holds_and_operator_email_releases(repo):
    repo.branch("feat/k", {"k.txt": "k\n"}, email="kevin@example.com")
    repo.merge_no_ff("feat/k")
    r = rec(inventory(repo), "feat/k")
    assert r["holds"] == ["owner:kevin@example.com"] and r["action"] == "keep"
    r = rec(inventory(repo, operator_emails=("kevin@example.com",)), "feat/k")
    assert r["action"] == "delete"


def test_pr_head_sha_mismatch_fork_and_non_default_base_are_not_merged_by_pr(repo):
    tip = repo.branch("feat/p", {"p1.txt": "1\n", "p2.txt": "2\n"})
    merge = repo.squash("feat/p")
    repo.pull_ref(9, tip)
    for bad in (pr(9, "feat/p", "0" * 40, merge), pr(9, "feat/p", tip, merge, repo="evil/fork"),
                pr(9, "feat/p", tip, merge, base="develop")):
        r = rec(inventory(repo, FakeAPI(closed={"feat/p": [bad]})), "feat/p")
        assert r["state"] != "merged-by-pr" and r["action"] != "delete"


def test_api_unknown_blocks_every_remote_deletion(repo):
    repo.branch("feat/u", {"u.txt": "u\n"})
    repo.merge_no_ff("feat/u")
    inv = inventory(repo, FakeAPI(fail_open=True))
    assert inv["meta"]["api_status"] == bi.API_UNKNOWN
    assert "api-unknown" in rec(inv, "feat/u")["holds"]
    assert [i["ref_type"] for i in inv["plan"]["items"]] == ["local"]


def test_missing_pull_ref_refuses_merged_by_pr_deletion(repo):
    tip = repo.branch("feat/m", {"m1.txt": "1\n", "m2.txt": "2\n"})
    merge = repo.squash("feat/m")
    r = rec(inventory(repo, FakeAPI(closed={"feat/m": [pr(5, "feat/m", tip, merge)]})), "feat/m")
    assert r["state"] == "merged-by-pr" and "pr-ref-missing" in r["holds"]
    assert r["action"] == "keep"


def test_inventory_is_read_only(repo):
    repo.branch("feat/r", {"r.txt": "r\n"})
    repo.merge_no_ff("feat/r")
    before = (repo.refs(), repo.git("reflog", "--all"))
    inventory(repo)
    assert (repo.refs(), repo.git("reflog", "--all")) == before


# ---------------------------------------------------------------------------
# Cleanup (clearance) and the ledger
# ---------------------------------------------------------------------------


def _team(repo: Repo, *rows: str, roster: tuple[str, ...] = ()) -> Path:
    team = repo.work / ".claude" / "agents"
    (team / "references").mkdir(parents=True, exist_ok=True)
    log = team / "references" / "security-decisions.log.csv"
    log.write_text("timestamp,requesting_agent,action_reviewed,verdict,conditions,"
                   "conditions_verified\n" + "".join(r + "\n" for r in rows), encoding="utf-8")
    if roster:
        (team / "references" / "security-approvers.txt").write_text("\n".join(roster) + "\n")
    return team


def _plan(repo: Repo, tmp_path: Path, api=None) -> tuple[Path, dict]:
    inv = inventory(repo, api)
    paths = bi.write_reports(inv, tmp_path / "report")
    return paths[2], inv["plan"]


def _cfg(repo: Repo) -> bi.InventoryConfig:
    return bi.InventoryConfig(repo=repo.work, now=NOW)


def test_cleanup_dry_run_changes_nothing_and_apply_needs_clearance(repo, tmp_path):
    repo.branch("feat/c", {"c.txt": "c\n"})
    repo.merge_no_ff("feat/c")
    plan_path, plan = _plan(repo, tmp_path)
    team = _team(repo)
    before = repo.refs()
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=False, api=FakeAPI())
    assert code == bc.EXIT_OK and repo.refs() == before
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert code == bc.EXIT_UNAUTHORIZED and repo.refs() == before


def test_cleanup_with_clearance_deletes_records_and_consumes(repo, tmp_path):
    tip = repo.branch("feat/c", {"c.txt": "c\n"})
    repo.merge_no_ff("feat/c")
    plan_path, plan = _plan(repo, tmp_path)
    action = bc.clearance_action(plan)
    team = _team(repo, f"2026-10-04,security,{action},PASS,,")
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert code == bc.EXIT_OK, lines
    assert "feat/c" not in repo.refs()
    assert not run(repo.origin.parent, "--git-dir", str(repo.origin), "branch", "--list", "feat/c")
    ledger = bc.read_ledger(repo.work)
    assert [(r["ref_type"], r["event"]) for r in ledger] == [
        ("local", "attempt"), ("local", "deleted"), ("remote", "attempt"), ("remote", "deleted")]
    assert all(r["old_sha"] == tip for r in ledger)
    code, _ = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert code == bc.EXIT_UNAUTHORIZED  # the clearance was consumed


def test_cleanup_tags_release_then_deletes(repo, tmp_path):
    tip = repo.branch("release/2.0", {"r.txt": "r\n"})
    repo.merge_no_ff("release/2.0")
    plan_path, plan = _plan(repo, tmp_path)
    team = _team(repo, f"2026-10-04,security,{bc.clearance_action(plan)},PASS,,")
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert code == bc.EXIT_OK, lines
    assert run(repo.work, "rev-parse", "archive/release/2.0^{commit}") == tip
    assert "release/2.0" not in repo.refs().replace("refs/tags/archive/release/2.0", "")


def test_cleanup_merged_by_pr_local_uses_compare_and_delete(repo, tmp_path):
    tip = repo.branch("feat/q", {"q1.txt": "1\n", "q2.txt": "2\n"})
    merge = repo.squash("feat/q")
    repo.pull_ref(3, tip)
    repo.git("push", "-q", "origin", "--delete", "feat/q")  # GitHub's delete-on-merge
    api = FakeAPI(closed={"feat/q": [pr(3, "feat/q", tip, merge)]})
    plan_path, plan = _plan(repo, tmp_path, api)
    assert plan["items"][0]["run"] == [f"git update-ref -d refs/heads/feat/q {tip}"]
    team = _team(repo, f"2026-10-04,security,{bc.clearance_action(plan)},PASS,,")
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=api)
    assert code == bc.EXIT_OK, lines
    assert "refs/heads/feat/q" not in repo.refs()


def test_moved_tip_is_skipped_and_lease_fails_closed(repo, tmp_path):
    repo.branch("feat/t", {"t.txt": "t\n"})
    repo.merge_no_ff("feat/t")
    plan_path, plan = _plan(repo, tmp_path)
    # Someone pushes to the branch after the audit.
    repo.git("checkout", "-q", "feat/t")
    commit(repo.work, "t2.txt", "late\n")
    repo.git("push", "-q", "origin", "feat/t")
    repo.git("checkout", "-q", "main")
    team = _team(repo, f"2026-10-04,security,{bc.clearance_action(plan)},PASS,,")
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert any("tip moved" in ln for ln in lines)
    assert "refs/heads/feat/t" in repo.refs()
    # The lease itself: force a stale item straight into the executor.
    stale = dict(plan["items"][-1])
    assert stale["ref_type"] == "remote"
    code, lines = bc.execute_items(repo.work, plan, [stale], mode="cleanup",
                                   authorization="test", api=None, run_id="r")
    assert code == bc.EXIT_FAIL and "refs/remotes/origin/feat/t" in repo.refs()
    assert bc.read_ledger(repo.work)[-1]["event"] in ("failed", "skipped")


def test_edited_plan_is_refused(repo, tmp_path):
    repo.branch("feat/e", {"e.txt": "e\n"})
    repo.merge_no_ff("feat/e")
    plan_path, _ = _plan(repo, tmp_path)
    plan_path.write_text(plan_path.read_text().replace("feat/e", "feat/x"))
    with pytest.raises(bc.CleanupError, match="modified"):
        bc.load_plan(plan_path)


def test_halt_blocks_cleanup(repo, tmp_path):
    repo.branch("feat/h", {"h.txt": "h\n"})
    repo.merge_no_ff("feat/h")
    plan_path, plan = _plan(repo, tmp_path)
    team = _team(repo, "2026-10-04,security,branch-delete,HALT,stop,",
                 f"2026-10-04,security,{bc.clearance_action(plan)},PASS,,")
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert code == bc.EXIT_FAIL and "HALT" in lines[-1]
    assert "refs/heads/feat/h" in repo.refs()


def test_ledger_tamper_is_detected(repo):
    bc.append_ledger(repo.work, {"branch": "a", "event": "attempt"})
    bc.append_ledger(repo.work, {"branch": "b", "event": "attempt"})
    path = repo.work / bc.LEDGER_REL
    path.write_text(path.read_text().replace(",a,", ",z,"))
    with pytest.raises(bc.CleanupError, match="chain broken"):
        bc.read_ledger(repo.work)


# ---------------------------------------------------------------------------
# Post-merge (grant)
# ---------------------------------------------------------------------------

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

_KEY = Ed25519PrivateKey.generate()
_PRIV = _KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()
_PUB = _KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _grant(repo: Repo, team: Path, *, expires: str = "2099-01-01T00:00:00Z", max_uses: int = 1,
           gid: str = "g-1") -> None:
    store = team / "references" / "authorized-verify-keys"
    store.mkdir(parents=True, exist_ok=True)
    (store / "op.pub.pem").write_text(_PUB)
    grants.sign_ed25519_grant(
        repo.work, team_dir=team, private_pem=_PRIV, key_id="op", issuer_team="widget",
        holder_team="widget", target_path=str(repo.work.resolve()),
        permitted_ops=grants.BRANCH_DELETE_OP, expires_at=expires, max_uses=max_uses,
        approver="jim", ticket_id="T-1", reason_code="post-merge", grant_id=gid,
        timestamp="2026-10-04T00:00:00Z")


def _merged(repo: Repo, name: str) -> None:
    repo.branch(name, {f"{name.replace('/', '_')}.txt": "x\n"})
    repo.merge_no_ff(name)


def test_post_merge_without_grant_exits_unauthorized(repo):
    _merged(repo, "feat/pm")
    team = _team(repo)
    code, lines = bc.run_post_merge("feat/pm", _cfg(repo), team_dir=team, team_id="widget",
                                    apply=True, api=FakeAPI())
    assert code == bc.EXIT_UNAUTHORIZED and "refs/heads/feat/pm" in repo.refs()


def test_post_merge_with_grant_deletes_and_counts_one_use(repo):
    _merged(repo, "feat/pm")
    team = _team(repo, roster=("security", "jim"))
    _grant(repo, team)
    code, lines = bc.run_post_merge("feat/pm", _cfg(repo), team_dir=team, team_id="widget",
                                    apply=True, api=FakeAPI())
    assert code == bc.EXIT_OK, lines
    assert "feat/pm" not in repo.refs()
    assert bc.grant_uses(repo.work, "g-1") == 1
    _merged(repo, "feat/pm2")
    code, lines = bc.run_post_merge("feat/pm2", _cfg(repo), team_dir=team, team_id="widget",
                                    apply=True, api=FakeAPI())
    assert code == bc.EXIT_UNAUTHORIZED and "used up" in lines[0]


def test_post_merge_refuses_expired_grant_and_non_latest_merge(repo):
    _merged(repo, "feat/x1")
    team = _team(repo, roster=("security", "jim"))
    _grant(repo, team, expires="2026-01-01T00:00:00Z")
    code, lines = bc.run_post_merge("feat/x1", _cfg(repo), team_dir=team, team_id="widget",
                                    apply=True, api=FakeAPI())
    assert code == bc.EXIT_UNAUTHORIZED and "expired" in lines[0]
    _merged(repo, "feat/x2")
    code, lines = bc.run_post_merge("feat/x1", _cfg(repo), team_dir=team, team_id="widget",
                                    apply=True, api=FakeAPI())
    assert code == bc.EXIT_FAIL and "second parent" in lines[0]


def test_post_merge_refuses_squash_merges(repo):
    tip = repo.branch("feat/sq", {"a.txt": "1\n"})
    merge = repo.squash("feat/sq")
    repo.pull_ref(4, tip)
    team = _team(repo, roster=("security", "jim"))
    _grant(repo, team)
    api = FakeAPI(closed={"feat/sq": [pr(4, "feat/sq", tip, merge)]})
    code, lines = bc.run_post_merge("feat/sq", _cfg(repo), team_dir=team, team_id="widget",
                                    apply=True, api=api)
    assert code == bc.EXIT_FAIL and "two-parent merge" in lines[0]


def test_hmac_branch_delete_grant_is_refused(repo):
    team = _team(repo, roster=("security", "jim"))
    with pytest.raises(grants.GrantError, match="operator-only"):
        grants.issue_grant(
            repo.work, team_dir=team, issuer_team="widget", holder_team="widget",
            target_path=str(repo.work), permitted_ops=grants.BRANCH_DELETE_OP,
            expires_at="2099-01-01T00:00:00Z", max_uses=1, approver="jim", ticket_id="T",
            reason_code="r", grant_id="h-1", timestamp="2026-10-04T00:00:00Z", key="k")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: list[str]) -> int:
    from agentteams.cli.app import main

    return main(argv)


@pytest.mark.parametrize("argv,needle", [
    (["--branch-report", "x"], "--branch-report requires --branch-inventory"),
    (["--branch-inventory", "--apply"], "--apply requires"),
    (["--branch-inventory", "--branch-post-merge", "b"], "mutually exclusive"),
    (["--branch-inventory", "--update"], "standalone"),
])
def test_cli_rejects_incoherent_flags(argv, needle, capsys):
    with pytest.raises(SystemExit) as exc:
        _main(argv)
    assert exc.value.code == 2 and needle in capsys.readouterr().err


def test_cli_inventory_writes_reports(repo, tmp_path, capsys):
    _merged(repo, "feat/cli")
    out = tmp_path / "rep"
    rc = _main(["--branch-inventory", "--no-api", "--project", str(repo.work),
                "--branch-report", str(out)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "Branch inventory:" in text and (out / "branch-deletion-plan.json").exists()
    rows = list(csv.DictReader((out / "branch-inventory.csv").open()))
    assert {r["branch"] for r in rows} >= {"main", "feat/cli"}


# ---------------------------------------------------------------------------
# Emission: every framework adapter ships the reference and cites it at a resolving path
# ---------------------------------------------------------------------------

from agentteams.frameworks.registry import FRAMEWORK_IDS  # noqa: E402


@pytest.mark.parametrize("framework", FRAMEWORK_IDS)
def test_every_framework_emits_and_cites_branch_lifecycle_reference(framework, tmp_path,
                                                                    monkeypatch):
    import build_team
    from agentteams.cli import security_gate

    # Emission is under test, not intel freshness (that gate has its own tests).
    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh",
                        lambda *a, **k: None)

    out = tmp_path / "team"
    brief = Path("examples/software-project/brief.json").resolve()
    assert build_team.main(["--description", str(brief), "--framework", framework,
                            "--output", str(out), "--yes", "--no-scan",
                            "--security-offline"]) == 0
    refs = list(out.rglob("branch-lifecycle.reference.md"))
    assert refs, f"{framework}: branch-lifecycle.reference.md not emitted"
    body = refs[0].read_text(encoding="utf-8")
    assert "Merged-by-PR" in body and "branch-cleanup:<" in body
    git_ops = [p for p in out.rglob("*git-operations*") if p.is_file()
               and ".agentteams-backups" not in p.parts]
    assert git_ops, f"{framework}: no git-operations agent emitted"
    for path in git_ops:
        text = path.read_text(encoding="utf-8")
        assert "references/branch-lifecycle.reference.md" in text, path
        assert "Branch disposition" in text and "Branch inventory" in text, path
        # The citation resolves: references/ sits beside the agent file or one level up.
        assert any((d / "references" / "branch-lifecycle.reference.md").exists()
                   for d in (path.parent, path.parent.parent)), f"{path}: citation unresolved"


def test_external_team_dir_is_refused(repo, tmp_path, capsys):
    """Security review B1: an agent must not supply its own trust root via --team-dir."""
    _merged(repo, "feat/b1")
    plan_path, _ = _plan(repo, tmp_path)
    rogue = tmp_path / "rogue"
    (rogue / "references").mkdir(parents=True)
    rc = _main(["--branch-cleanup", str(plan_path), "--apply", "--no-api",
                "--project", str(repo.work), "--team-dir", str(rogue)])
    assert rc == bc.EXIT_FAIL
    assert "refusing an external trust root" in capsys.readouterr().err
    assert "refs/heads/feat/b1" in repo.refs()


def test_plan_item_must_match_fresh_inventory_exactly(repo, tmp_path):
    """Security review C4: same tip but a different verdict detail (here: a new link hold
    appears after the audit) is drift, not a green light."""
    _merged(repo, "feat/c4")
    plan_path, plan = _plan(repo, tmp_path)
    commit(repo.work, "note.md", "https://github.com/acme/widget/tree/feat/c4\n")
    repo.git("push", "-q", "origin", "main")
    team = _team(repo, f"2026-10-04,security,{bc.clearance_action(plan)},PASS,,")
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert any("no longer deletable" in ln for ln in lines)
    assert "refs/heads/feat/c4" in repo.refs()


def test_altered_item_with_recomputed_digest_is_refused(repo, tmp_path):
    """Security re-verification condition 2: an item edited (here its restore command) and
    re-hashed so load_plan accepts it must still fail the fresh-inventory comparison."""
    import json

    _merged(repo, "feat/c4b")
    plan_path, plan = _plan(repo, tmp_path)
    plan["items"][0]["restore"] = ["echo pwned"]
    plan["sha256"] = bi.plan_digest(plan)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    team = _team(repo, f"2026-10-04,security,{bc.clearance_action(plan)},PASS,,")
    code, lines = bc.run_cleanup(plan_path, _cfg(repo), team_dir=team, apply=True, api=FakeAPI())
    assert any("differs from the fresh inventory (restore)" in ln for ln in lines), lines
    assert plan["items"][0]["ref_type"] == "local"
    assert "refs/heads/feat/c4b" in repo.refs()  # the altered item never ran


def test_grant_signed_via_cli_with_project_is_found(repo, tmp_path, monkeypatch, signing_preapproved):
    """Technical-validator finding 1: the documented provisioning command must deposit the grant
    where --branch-post-merge reads it (<repo>/references/capability-grants.log.csv)."""
    import json

    _merged(repo, "feat/e2e")
    team = _team(repo, roster=("security", "jim"))
    store = team / "references" / "authorized-verify-keys"
    store.mkdir(parents=True, exist_ok=True)
    (store / "op.pub.pem").write_text(_PUB)
    keyfile = tmp_path / "op.pem"
    keyfile.write_text(_PRIV)
    keyfile.chmod(0o600)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(keyfile))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "issuer_team": "widget", "holder_team": "widget", "target_path": str(repo.work.resolve()),
        "permitted_ops": "branch-delete", "expires_at": "2099-01-01T00:00:00Z", "max_uses": 2,
        "approver": "jim", "ticket_id": "T-9", "reason_code": "post-merge", "key_id": "op",
    }))
    assert _main(["--sign-grant", str(spec), "--framework", "claude",
                  "--project", str(repo.work)]) == 0
    assert (repo.work / "references" / "capability-grants.log.csv").exists()
    grant, reasons = bc.find_branch_grant(repo.work.resolve(), team_dir=team, team_id="widget")
    assert grant is not None, reasons
    code, lines = bc.run_post_merge("feat/e2e", _cfg(repo), team_dir=team, team_id="widget",
                                    apply=True, api=FakeAPI())
    assert code == bc.EXIT_OK, lines
    assert "feat/e2e" not in repo.refs()
