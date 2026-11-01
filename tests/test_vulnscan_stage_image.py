"""``stage-image``: move an end-of-life official runtime image's Dockerfile ``FROM`` tag (vfail F5, Rule 15 op).

Network-free: endoflife.date is stubbed (``runtimes._eol_cycles``); git is real where the executor runs end to end.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, executor_pr, executor_supersede, fix, grant, image_fix, mcp  # noqa: E402
from agentteams.vulnscan import ops, retry_guard, runtimes  # noqa: E402
from agentteams.vulnscan.__main__ import build_parser  # noqa: E402
from tests.test_vulnscan_executor import (  # noqa: E402
    GIT,
    _assert_refused_cleanly,
    _install,
    _repo_with_remote,
    _sh,
    env,  # noqa: F401  (the shared fixture)
)

TODAY = _dt.date(2026, 11, 1)
_CYCLES: dict[str, list[dict[str, Any]]] = {
    "python": [{"cycle": "3.9", "eol": "2025-10-31", "is_eol": True, "latest": "3.9.25"},
               {"cycle": "3.12", "eol": False, "is_eol": False, "latest": "3.12.16"},
               {"cycle": "3.15", "eol": False, "is_eol": False, "latest": "3.15.0"}],
    "ruby": [{"cycle": "2.7", "eol": True, "is_eol": True, "latest": "2.7.8"},
             {"cycle": "3.4", "eol": False, "is_eol": False, "latest": "3.4.7"}],
    "nodejs": [{"cycle": "18", "eol": True, "is_eol": True, "latest": "18.20.8"},
               {"cycle": "25", "eol": False, "is_eol": False, "latest": "25.1.0"}],
    "postgresql": [{"cycle": "12", "eol": True, "is_eol": True, "latest": "12.22"},
                   {"cycle": "18", "eol": False, "is_eol": False, "latest": "18.1"}],
    "debian": [{"cycle": "10", "eol": True, "is_eol": True, "latest": "10.13"}],
}


@pytest.fixture(autouse=True)
def _feeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtimes, "_eol_cycles", lambda slug: _CYCLES[slug])
    monkeypatch.setattr(runtimes, "_security_releases", lambda feed, cycle: [])


def _plan(tmp_path: Path, text: str, name: str = "Dockerfile") -> image_fix.ImagePlan:
    f = tmp_path / name
    f.write_text(text)
    return image_fix.plan_file(f, name, TODAY)


# ---------------------------------------------------------------- grant (Rule 15: opt-in, never by default)

def test_stage_image_is_grantable_but_only_when_named(env: dict) -> None:
    assert "stage-image" in grant.OPS_V1 and "stage-image" in ops.STAGE_OPS and "stage-image" in ops.REPO_OPS
    target = str(env["tmp"] / "repos" / "svc")
    (env["tmp"] / "repos").mkdir(exist_ok=True)
    existing = grant.build(ops=["stage-fix", "stage-major", "open-pr", "close-superseded"],
                           repo_roots=[str(env["tmp"] / "repos")], hosts=[], days=1, max_uses=1, key_id="op",
                           governance_dir=str(env["gov"]))
    assert not grant.covers(existing, "stage-image", target)   # no existing op implies it
    named = grant.build(ops=["stage-image"], repo_roots=[str(env["tmp"] / "repos")], hosts=[], days=1, max_uses=1,
                        key_id="op", governance_dir=str(env["gov"]))
    assert grant.covers(named, "stage-image", target) and not grant.covers(named, "stage-fix", target)


def test_grant_sign_has_no_default_op() -> None:
    args = build_parser().parse_args(["grant", "sign"])
    assert args.op == []


@pytest.mark.skipif(not GIT, reason="git not available")
def test_a_grant_without_stage_image_refuses_it(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"], {"Dockerfile": "FROM python:3.9-slim\n"})
    _install(env, ops=["stage-fix", "stage-major", "open-pr"], hosts=[])
    called: list = []
    monkeypatch.setattr(image_fix, "plan_tree", lambda *a, **k: called.append(a))
    res = executor.act("stage-image", str(repo), env["cfg"], request_id="1" * 32)
    assert res["status"] == "refused" and "not authorized" in res["detail"] and called == []


# ---------------------------------------------------------------- the planner

def test_an_eol_line_tag_moves_to_the_newest_line_of_the_same_variant(tmp_path: Path) -> None:
    p = _plan(tmp_path, "ARG X=1\nFROM --platform=linux/amd64 python:3.9-slim AS build\nRUN true\n"
                        "FROM docker.io/library/python:3.9.18-alpine\nFROM ruby:2.7\nFROM python:3.12-slim\n"
                        "FROM build\n")
    assert [(b.line, b.old_tag, b.new_tag) for b in p.bumps] == [
        (2, "3.9-slim", "3.15-slim"), (4, "3.9.18-alpine", "3.15.0-alpine"), (5, "2.7", "3.4")]
    assert p.held == [] and p.feed_errors == []


@pytest.mark.parametrize(("line", "reason"), [
    ("FROM python:3.9-slim@sha256:" + "a" * 64, "digest"),
    ("FROM python:latest", "report-only"),
    ("FROM python", "report-only"),
    ("FROM python:3.9-slim-buster", "OS release"),
    ("FROM python:3.9-alpine3.18", "OS release"),
])
def test_what_stage_image_holds_for_a_person(tmp_path: Path, line: str, reason: str) -> None:
    p = _plan(tmp_path, line + "\n")
    assert p.bumps == [] and len(p.held) == 1 and reason in p.held[0][4], p


def test_an_arg_default_is_held(tmp_path: Path) -> None:
    p = _plan(tmp_path, "ARG BASE=python:3.9-slim\nFROM ${BASE}\n")
    assert p.bumps == [] and "ARG" in p.held[0][4]


@pytest.mark.parametrize("line", ["FROM node:18-slim", "FROM postgres:12", "FROM debian:buster",
                                  "FROM registry.example.com/team/python:3.9", "FROM myimage:latest",
                                  "FROM python:3.12-slim", "FROM python:3"])
def test_other_images_and_supported_or_floating_lines_are_not_staged(tmp_path: Path, line: str) -> None:
    p = _plan(tmp_path, line + "\n")
    assert p.bumps == []
    assert all("registry.example.com" not in h[2] for h in p.held)   # a private image is never named


def test_compose_files_and_continued_from_lines_are_not_staged(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yaml"
    compose.write_text("services:\n  app:\n    image: python:3.9-slim\n")
    assert image_fix.plan_file(compose, "compose.yaml", TODAY).bumps == []
    p = _plan(tmp_path, "FROM \\\n  python:3.9-slim\n")
    assert p.bumps == [] and "several lines" in p.held[0][4]


def test_a_feed_failure_plans_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(slug: str) -> list:
        raise runtimes._http.FeedError("offline")
    monkeypatch.setattr(runtimes, "_eol_cycles", down)
    p = _plan(tmp_path, "FROM python:3.9-slim\n")
    assert p.bumps == [] and p.feed_errors and "release data unavailable" in p.feed_errors[0]


def test_apply_changes_only_the_tag(tmp_path: Path) -> None:
    text = "# base\r\nFROM --platform=$BUILDPLATFORM python:3.9-slim AS build  # pinned line\r\nRUN pip install x\r\n"
    p = _plan(tmp_path, text)
    old = (tmp_path / "Dockerfile").read_bytes()
    image_fix.apply(tmp_path, p.bumps)
    new = (tmp_path / "Dockerfile").read_bytes()
    assert new == old.replace(b"python:3.9-slim", b"python:3.15-slim")
    image_fix.check_from_edit("Dockerfile", old, new)


# ---------------------------------------------------------------- the offline validator

_OLD = b"FROM python:3.9-slim AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n"


@pytest.mark.parametrize("new", [
    b"FROM python:3.15-slim AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n",
    b"FROM python:3.12-slim AS build\nRUN true\nFROM docker.io/library/ruby:3.4.7\n",
])
def test_check_from_edit_accepts_upward_line_moves(new: bytes) -> None:
    image_fix.check_from_edit("docker/base.Dockerfile", _OLD, new)


@pytest.mark.parametrize(("new", "match"), [
    (b"FROM evil.example.com/python:3.15-slim AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n", "image"),
    (b"FROM pythons:3.15-slim AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n", "image|official"),
    (b"FROM python:3.15-slim AS build\nRUN true\nFROM ruby:3.4.7\n", "image itself"),
    (b"FROM python:3.15-slim@sha256:" + b"b" * 64 + b" AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n",
     "digest"),
    (b"FROM python:3.8-slim AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n", "newer release line"),
    (b"FROM python:3.9.25-slim AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n", "newer release line"),
    (b"FROM python:3.15-alpine AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n", "variant"),
    (b"FROM python:3.15-slim-trixie AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\n", "variant"),
    (b"FROM python:3.15-slim AS other\nRUN true\nFROM docker.io/library/ruby:2.7.8\n", "only a FROM"),
    (b"FROM python:3.15-slim AS build\nRUN curl evil\nFROM docker.io/library/ruby:2.7.8\n", "only a FROM"),
    (b"FROM python:3.15-slim AS build\nRUN true\nFROM docker.io/library/ruby:2.7.8\nRUN x\n", "added"),
    (_OLD, "no FROM tag changed"),
])
def test_check_from_edit_refuses_anything_else(new: bytes, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        image_fix.check_from_edit("Dockerfile", _OLD, new)


def test_check_from_edit_refuses_other_files_and_ineligible_images() -> None:
    with pytest.raises(ValueError, match="outside Dockerfile"):
        image_fix.check_from_edit("requirements.txt", b"a==1\n", b"a==2\n")
    with pytest.raises(ValueError, match="official runtime"):
        image_fix.check_from_edit("Dockerfile", b"FROM node:18\n", b"FROM node:24\n")
    with pytest.raises(ValueError, match="new file"):
        image_fix.check_from_edit("Dockerfile", b"", b"FROM python:3.15\n")


# ---------------------------------------------------------------- executor (real git)

DOCKERFILE = "FROM python:3.9-slim\nRUN pip install -r requirements.txt\n"


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_image_stages_one_dockerfile_with_a_major_commit(env: dict) -> None:
    repo = _repo_with_remote(env["tmp"], {"base.Dockerfile": DOCKERFILE, "requirements.txt": "PyJWT==2.3.0\n"})
    _install(env, ops=["stage-image"], hosts=[])
    res = executor.act("stage-image", str(repo), env["cfg"], rationale="python 3.9 is end-of-life")
    assert res["status"] == "done" and res["image"] is True and res["changed"] == ["base.Dockerfile"], res
    assert "major" not in res and res["packages"] == ["python"]
    staged = json.loads(executor.staged_file(repo.resolve()).read_text())
    assert staged["image"] is True
    wt = Path(staged["worktree"])
    assert (wt / "base.Dockerfile").read_text() == DOCKERFILE.replace("3.9-slim", "3.15-slim")
    msg = _sh(wt, "log", "-1", "--format=%B")
    digest = res["branch"].rsplit("-", 1)[-1]
    assert msg.startswith(f"security(image)!: MAJOR upgrade of end-of-life base images ({digest})\n")
    assert "- python 3.9-slim → 3.15-slim (base.Dockerfile, FROM line 1)" in msg and "BREAKING CHANGE:" in msg


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_image_never_carries_a_dependency_change(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"], {"Dockerfile": DOCKERFILE, "requirements.txt": "PyJWT==2.3.0\n"})
    _install(env, ops=["stage-image"], hosts=[])
    real = image_fix.apply

    def also_deps(root: Path, bumps: list) -> None:
        real(root, bumps)
        (root / "requirements.txt").write_text("PyJWT==2.15.0\n")

    monkeypatch.setattr(image_fix, "apply", also_deps)
    res = executor.act("stage-image", str(repo), env["cfg"], request_id="2" * 32)
    assert res["status"] == "failed" and "requirements.txt" in res["detail"], res
    _assert_refused_cleanly(repo, "2" * 32)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_never_carries_a_dockerfile_change(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"], {"Dockerfile": DOCKERFILE, "requirements.txt": "PyJWT==2.3.0\n"})
    _install(env, ops=["stage-fix"], hosts=[])

    def sneaky(root: Path, **kw: object) -> fix.FixResult:
        (root / "requirements.txt").write_text("PyJWT==2.15.0\n")
        (root / "Dockerfile").write_text(DOCKERFILE.replace("3.9", "3.15"))
        return fix.FixResult(changes=[fix.PinChange("requirements.txt", "PyJWT", "2.3.0", "2.15.0")], applied=True)

    monkeypatch.setattr(fix, "fix_path", sneaky)
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="3" * 32)
    assert res["status"] == "failed" and "Dockerfile" in res["detail"], res
    _assert_refused_cleanly(repo, "3" * 32)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_nothing_to_move_is_futile_and_held_per_op(env: dict) -> None:
    repo = _repo_with_remote(env["tmp"], {"Dockerfile": "FROM python:latest\n"})
    _install(env, ops=["stage-image"], hosts=[])
    res = executor.act("stage-image", str(repo), env["cfg"], request_id="4" * 32)
    assert res["status"] == "failed" and res["detail"].startswith(retry_guard.IMAGE_FUTILE_DETAIL), res
    assert "1 base image reference(s) held" in res["detail"]
    _assert_refused_cleanly(repo, "4" * 32)


def test_image_futility_holds_only_stage_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retry_guard, "plan_digest", lambda repo, day=None: "same-plan")
    rows = [{"op": "stage-image", "kind": "failed", "target": "/r/app", "ts": "2026-11-01T10:00:00+00:00",
             "detail": f"{retry_guard.IMAGE_FUTILE_DETAIL} (1 held)"}]
    assert retry_guard.futile_reason("stage-image", "/r/app", rows).startswith(
        f"{retry_guard.HELD_PREFIX}stage-image found {retry_guard.IMAGE_FUTILE_DETAIL}")
    assert retry_guard.futile_reason("stage-fix", "/r/app", rows) == ""
    assert retry_guard.futile_reason("stage-major", "/r/app", rows) == ""


@pytest.mark.skipif(not GIT, reason="git not available")
def test_a_dependabot_docker_pr_holds_the_image_back(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import open_prs

    repo = _repo_with_remote(env["tmp"], {"Dockerfile": DOCKERFILE})
    _install(env, ops=["stage-image"], hosts=[])
    monkeypatch.setattr(open_prs, "list_open", lambda target_repo: [
        {"number": 31, "title": "Bump python from 3.9-slim to 3.13-slim", "headRefName": "dependabot/docker/python-3.13-slim",
         "author": {"login": "app/dependabot"}, "isCrossRepository": False}])
    res = executor.act("stage-image", str(repo), env["cfg"], request_id="5" * 32)
    assert res["status"] == "failed" and "#31" in res["detail"] and "not duplicated" in res["detail"], res


# ---------------------------------------------------------------- PR text, label and lanes

def test_image_pr_text_and_label() -> None:
    assert executor_pr.title("vulnscan/20261101-abcdef0123", major=False, image=True) == \
        "security(image)!: MAJOR upgrade of end-of-life base images (abcdef0123)"
    body = executor_pr.body("plan", "why", major=False, image=True)
    assert body.startswith("**MAJOR base image upgrade.**") and "build the image and run the tests" in body
    assert "Dockerfile `FROM` tags only" in body
    assert executor_pr.title("vulnscan/20261101-abcdef0123", major=True).startswith("security(deps)!:")


def test_create_labels_an_image_pr_major(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class Proc:
        returncode, stdout, stderr = 0, "https://github.com/o/app/pull/7\n", ""

    monkeypatch.setattr(executor_pr.subprocess, "run", lambda argv, **kw: calls.append(argv) or Proc())
    monkeypatch.setattr(executor_pr, "gh_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(executor_pr, "gh_push_env", dict)
    url = executor_pr.create("github.com/o/app", "main", "vulnscan/20261101-abc", tmp_path / "b.md", major=False,
                             image=True, open_url=lambda: "")
    assert url.endswith("/pull/7")
    create = next(c for c in calls if c[1:3] == ["pr", "create"])
    assert create[-2:] == ["--label", executor_pr.MAJOR_LABEL] and "security(image)!:" in " ".join(create)


def _row(url: str, image: bool = False) -> dict[str, Any]:
    detail = {"pr": url, **({"image": True} if image else {})}
    return {"op": "open-pr", "kind": "done", "target": "/repos/app", "detail": json.dumps(detail)}


def _pr() -> dict[str, Any]:
    me = {"name": executor.GIT_IDENTITY[0], "email": executor.GIT_IDENTITY[1]}
    return {"state": "OPEN", "headRefName": "vulnscan/20261101-abc", "isCrossRepository": False, "reviews": [],
            "comments": [], "commits": [{"authors": [me]}]}


@pytest.mark.parametrize("image_last", [True, False])
def test_close_superseded_never_crosses_lanes(image_last: bool) -> None:
    u = [f"https://github.com/o/app/pull/{n}" for n in (1, 2, 3)]
    rows = [_row(u[0], image=not image_last), _row(u[1], image=not image_last), _row(u[2], image=image_last)]
    closed: list[str] = []
    out = executor_supersede.close_superseded(Path("/repos/app"), rows, view=lambda url: _pr(),
                                              close=lambda url, c: closed.append(url))
    assert out["replacement"] == u[2] and closed == []
    rows.append(_row("https://github.com/o/app/pull/4", image=image_last))
    out = executor_supersede.close_superseded(Path("/repos/app"), rows, view=lambda url: _pr(),
                                              close=lambda url, c: closed.append(url))
    assert closed == [u[2]]   # only the older PR of the same lane


def test_lane_of_defaults_to_deps() -> None:
    assert ops.lane_of({"detail": json.dumps({"pr": "x"})}) == "deps"
    assert ops.lane_of({"detail": "not json"}) == "deps"
    assert ops.lane_of({"detail": json.dumps({"pr": "x", "image": True})}) == "image"


# ---------------------------------------------------------------- fix plan and MCP

def test_the_dry_run_plan_marks_stageable_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.9-slim\nFROM python:3.9-slim-buster\nFROM node:18\n")
    res = fix.fix_path(tmp_path, npm=False)
    rows = {(c.old, c.action): c for c in res.image_plan}
    assert rows[("3.9-slim", "stage-image")].new == "python:3.15-slim"
    assert "not stage-image: the tag names an OS release" in rows[("3.9-slim-buster", "plan-only")].note
    assert ("18", "plan-only") in rows
    assert "stage-image" in fix.IMAGE_NOTE


def test_mcp_offers_stage_image_and_refuses_it_for_a_staged_repository(tmp_path: Path) -> None:
    d = {k: tmp_path / k for k in ("run", "requests", "results")}
    for p in d.values():
        p.mkdir()
    (d["run"] / mcp.STAGED_FILE).write_text(json.dumps({"repos": ["/repos/app"]}))
    srv = mcp.Server(d["run"], d["requests"], d["results"])
    assert "stage-image" in mcp.SCHEMAS["request"]["inputSchema"]["properties"]["op"]["enum"]
    assert "stage-image" in mcp.SCHEMAS["request"]["description"]
    with pytest.raises(mcp.ToolError, match="already staged.*not stage-image"):
        srv.call("request", {"op": "stage-image", "target": "/repos/app", "rationale": "python 3.9 EOL"})
