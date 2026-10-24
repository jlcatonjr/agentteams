"""agentteams.vulnscan.executor_guard: protected repositories by origin, the registry level, plan marking, and
PR-body scrubbing. Network-free (local git repositories, a fake ``gh``); reuses the executor test fixtures.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, executor_guard, fix, ledger, mcp, publish, registry  # noqa: E402
from tests.test_vulnscan_executor import (  # noqa: E402
    _fake_gh,
    _gh_calls,
    _git_repo,
    _install,
    _pin_fix,
    _repo_with_remote,
    env,  # noqa: F401  (the shared fixture)
)

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(not GIT, reason="git not available")


@pytest.fixture(autouse=True)
def _not_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)


def _set_origin(repo: Path, url: str) -> Path:
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True, capture_output=True)
    return repo


def _enrol(repo: Path, level: str) -> None:
    ident = registry.identity(repo)
    registry.save([registry.Entry(identity=ident, fix_checkout=str(repo.resolve()), checkouts=[str(repo.resolve())],
                                  level=level, owner=registry.owner_of(ident), enrolled="2026-10-01T00:00:00+00:00")])


# ---------------------------------------------------------------- protected by origin

def test_a_separate_clone_of_agentteams_is_refused_by_origin(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentteams
    tmp = env["tmp"]
    pkg_repo = _set_origin(_git_repo(tmp / "repos" / "agentteams-checkout"), "https://github.example.com/acme/agentteams.git")
    (pkg_repo / "agentteams").mkdir()
    monkeypatch.setattr(agentteams, "__file__", str(pkg_repo / "agentteams" / "__init__.py"))
    clone = _set_origin(_git_repo(tmp / "repos" / "agentteams-copy"), "git@github.example.com:acme/agentteams")
    other = _set_origin(_git_repo(tmp / "repos" / "svc"), "https://github.example.com/acme/svc")
    no_origin = _git_repo(tmp / "repos" / "plain")
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    with ledger.locked():
        with pytest.raises(executor.ExecError, match="is a clone of github.example.com/acme/agentteams"):
            executor.authorize("stage-fix", str(clone), "x", env["cfg"])
        for target in (other, no_origin):   # a different origin, or none: only the grant decides
            assert executor.authorize("open-pr", str(target), "x", env["cfg"])["grant_id"]
    res = executor.act("open-pr", str(clone), env["cfg"])
    assert res["status"] == "refused" and "is a clone of" in res["detail"]
    assert executor_guard.origin_of(no_origin) is None
    assert executor_guard.origin_of(pkg_repo / "agentteams" / "missing.py") == "github.example.com/acme/agentteams"


def test_a_clone_of_the_team_dir_or_governance_dir_is_refused(env: dict) -> None:
    tmp = env["tmp"]
    team = _set_origin(_git_repo(tmp / "team"), "https://github.example.com/acme/team.git")
    subprocess.run(["git", "init", "-q", str(env["gov"])], check=True, capture_output=True)
    _set_origin(env["gov"], "https://github.example.com/acme/gov.git")
    team_copy = _set_origin(_git_repo(tmp / "repos" / "team-copy"), "https://github.example.com/acme/team")
    gov_copy = _set_origin(_git_repo(tmp / "repos" / "gov-copy"), "ssh://git@github.example.com/acme/gov.git")
    cfg = {**env["cfg"], "agent": {"team_dir": str(team)}}
    _install(env, ops=["stage-fix"], hosts=[])
    for target in (team_copy, gov_copy):
        with ledger.locked():
            with pytest.raises(executor.ExecError, match="is a clone of"):
                executor.authorize("stage-fix", str(target), "x", cfg)
    assert set(executor_guard.protected_identities(cfg)) >= {"github.example.com/acme/team",
                                                             "github.example.com/acme/gov"}


# ---------------------------------------------------------------- registry level

@pytest.mark.parametrize("level", ["scan", "off", "unanswered"])
def test_a_repository_enrolled_below_remediate_is_scan_only(env: dict, level: str) -> None:
    repo = _set_origin(_git_repo(env["tmp"] / "repos" / "svc"), "https://github.example.com/acme/svc")
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    _enrol(repo, level)
    for op in ("stage-fix", "open-pr"):
        res = executor.act(op, str(repo), env["cfg"])
        assert res["status"] == "refused" and f"enrolled at {level}: scanning only" in res["detail"], res
    assert not any(r["kind"] == "begun" for r in ledger.read())


def test_remediate_and_unregistered_repositories_keep_the_grant_as_scope(env: dict) -> None:
    repo = _set_origin(_git_repo(env["tmp"] / "repos" / "svc"), "https://github.example.com/acme/svc")
    other = _git_repo(env["tmp"] / "repos" / "explicit")
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    _enrol(repo, "remediate")
    for target in (repo, other):
        with ledger.locked():
            assert executor.authorize("stage-fix", str(target), "x", env["cfg"])["grant_id"]


def test_an_unreadable_registry_refuses_repo_ops(env: dict) -> None:
    repo = _git_repo(env["tmp"] / "repos" / "svc")
    _install(env, ops=["stage-fix"], hosts=[])
    registry.registry_path().parent.mkdir(parents=True, exist_ok=True)
    registry.registry_path().write_text("{not json")
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "refused" and "cannot read the project registry" in res["detail"]


def test_scan_only_plans_are_marked_for_the_agent(env: dict) -> None:
    scan = _set_origin(_git_repo(env["tmp"] / "repos" / "scanned"), "https://github.example.com/acme/scanned")
    fixable = _git_repo(env["tmp"] / "repos" / "fixable")
    _enrol(scan, "scan")
    run_dir = env["tmp"] / "run"
    run_dir.mkdir()
    for name, target in (("fixplan-scanned.md", scan), ("fixplan-fixable.md", fixable)):
        (run_dir / name).write_text(f"# Fix plan\n\nRepository (the `stage-fix` target): `{target}`\n\n- PyJWT\n")
    path = executor_guard.write_plan_levels(run_dir)
    assert json.loads(path.read_text())["scan_only"] == {str(scan): "scan"}
    server = mcp.Server(run_dir, env["tmp"] / "spool", env["tmp"] / "results")
    text = server.call("get_fix_plans", {})
    assert "## scan only — do not request stage-fix (enrolled at scan) (fixplan-scanned.md)" in text
    assert f"## stage-fix target `{fixable}` (fixplan-fixable.md)" in text
    registry.registry_path().write_text("{not json")
    executor_guard.write_plan_levels(run_dir)
    assert "stage-fix target" not in server.call("get_fix_plans", {})   # unreadable registry: every plan is held
    (run_dir / mcp.PLAN_LEVELS_FILE).write_text("[not an object")      # a bad file is only a missing convenience
    assert f"stage-fix target `{fixable}`" in server.call("get_fix_plans", {})


# ---------------------------------------------------------------- PR body scrubbing

def test_open_pr_body_passes_the_publish_scrubber(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    bindir = _fake_gh(env, monkeypatch)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    monkeypatch.setattr(publish, "_host_names", lambda: ["build-box"])
    assert executor.act("stage-fix", str(repo), env["cfg"])["status"] == "done"
    rationale = (f"fixes {Path.home()}/proj and /home/example/code on build-box via 10.1.2.3 and "
                 f"/Users/example/x")
    res = executor.act("open-pr", str(repo), env["cfg"], rationale=rationale)
    assert res["status"] == "done", res
    bodies = list(executor._worktrees_dir().glob("*.pr.md"))
    assert len(bodies) == 1 and any("pr create" in c for c in _gh_calls(bindir))
    body = bodies[0].read_text()
    for leak in (str(Path.home()), "/home/example", "/Users/example", "build-box", "10.1.2.3"):
        assert leak not in body, leak
    assert "~/proj" in body and "<this-host>" in body and "<private-ip>" in body


# ---------------------------------------------------------------- review findings (S2, S3, S7)

def _protect_pkg(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import agentteams
    pkg_repo = _set_origin(_git_repo(tmp / "repos" / "agentteams-checkout"), "https://github.example.com/acme/agentteams")
    (pkg_repo / "agentteams").mkdir()
    monkeypatch.setattr(agentteams, "__file__", str(pkg_repo / "agentteams" / "__init__.py"))
    return pkg_repo


@pytest.mark.parametrize("how", ["pushurl", "pushInsteadOf"])
def test_the_push_url_git_would_use_is_checked_too(env: dict, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    _protect_pkg(env["tmp"], monkeypatch)
    target = _set_origin(_git_repo(env["tmp"] / "repos" / "svc"), "https://github.example.com/acme/svc")
    git = ["git", "-C", str(target)]
    if how == "pushurl":   # the text parser reads only url; git pushes to pushurl
        subprocess.run([*git, "remote", "set-url", "--push", "origin", "git@github.example.com:acme/agentteams.git"],
                       check=True, capture_output=True)
    else:
        subprocess.run([*git, "config", "url.https://github.example.com/acme/agentteams.pushInsteadOf",
                        "https://github.example.com/acme/svc"], check=True, capture_output=True)
    assert registry.identity(target) == "github.example.com/acme/svc"
    assert executor_guard.target_origins(str(target)) == {"github.example.com/acme/svc",
                                                          "github.example.com/acme/agentteams"}
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    res = executor.act("stage-fix", str(target), env["cfg"])
    assert res["status"] == "refused" and "is a clone of github.example.com/acme/agentteams" in res["detail"]


def test_an_unreadable_identity_is_refused_not_read_as_no_origin(env: dict) -> None:
    target = _set_origin(_git_repo(env["tmp"] / "repos" / "svc"), "https://github.example.com/acme/svc")
    with (target / ".git" / "config").open("a") as fh:   # git still reads it; identity() refuses the size
        fh.write("# " + "x" * (registry.MAX_GIT_FILE + 10) + "\n")
    _install(env, ops=["stage-fix"], hosts=[])
    res = executor.act("stage-fix", str(target), env["cfg"])
    assert res["status"] == "refused" and "cannot read the identity" in res["detail"], res


def test_an_enrolled_checkout_whose_origin_changed_is_refused(env: dict) -> None:
    repo = _set_origin(_git_repo(env["tmp"] / "repos" / "svc"), "https://github.example.com/acme/svc")
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    _enrol(repo, "remediate")
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", "https://github.example.com/other/svc"],
                   check=True, capture_output=True)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "refused" and "origin changed since enrolment" in res["detail"]
    assert "re-run" in res["detail"] and "enroll" in res["detail"]
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", "https://github.example.com/acme/svc"],
                   check=True, capture_output=True)
    nested = _set_origin(_git_repo(repo / "vendor" / "lib"), "https://github.example.com/third/lib")
    res = executor.act("stage-fix", str(nested), env["cfg"])
    assert res["status"] == "refused" and "inside the enrolled checkout" in res["detail"], res


def _brief(repo: Path, block: dict) -> None:
    (repo / ".agentteams").mkdir(exist_ok=True)
    (repo / ".agentteams" / "brief.json").write_text(json.dumps({"vulnerability_scanning": block}))


@pytest.mark.parametrize("level", ["scan", "off"])
def test_a_brief_below_remediate_refuses_repo_ops(env: dict, level: str) -> None:
    repo = _git_repo(env["tmp"] / "repos" / "svc")
    _brief(repo, {"level": level})
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    for op in ("stage-fix", "open-pr"):
        res = executor.act(op, str(repo), env["cfg"])
        assert res["status"] == "refused" and f"level {level}, not remediate" in res["detail"], res


def test_delivery_plan_refuses_open_pr_only(env: dict) -> None:
    repo = _git_repo(env["tmp"] / "repos" / "svc")
    _brief(repo, {"level": "remediate", "delivery": "plan"})
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "refused" and "project asks for plans only" in res["detail"]
    with ledger.locked():
        assert executor.authorize("stage-fix", str(repo), "x", env["cfg"])["grant_id"]


def test_base_branch_is_the_pr_base_and_must_exist_on_origin(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main:release/2.x"], check=True, capture_output=True)
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    bindir = _fake_gh(env, monkeypatch)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    _brief(repo, {"level": "remediate", "base_branch": "release/2.x"})
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "done" and res["base_ref"] == "origin/release/2.x", res
    assert executor.act("open-pr", str(repo), env["cfg"])["status"] == "done"
    assert any("--base release/2.x" in c for c in _gh_calls(bindir))
    _brief(repo, {"level": "remediate", "base_branch": "no-such-branch"})
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "failed" and "base_branch 'no-such-branch' does not exist on origin" in res["detail"]


def test_open_pr_refuses_when_the_base_branch_left_origin(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main:release"], check=True, capture_output=True)
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    bindir = _fake_gh(env, monkeypatch)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    _brief(repo, {"level": "remediate", "base_branch": "release"})
    assert executor.act("stage-fix", str(repo), env["cfg"])["status"] == "done"
    subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", ":release"], check=True, capture_output=True)
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "failed" and "base branch 'release' does not exist on origin" in res["detail"]
    assert not any("pr create" in c for c in _gh_calls(bindir))
