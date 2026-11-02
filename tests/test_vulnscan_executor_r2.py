"""agentteams.vulnscan executor, round 2 of the test campaign (validate_diff, lockfiles, spool, R8).

Split out of ``test_vulnscan_executor.py`` (CH-07); shares its fixtures and helpers.
"""

from __future__ import annotations

import threading
import subprocess
import os
import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import executor, executor_serve, fix, ledger, osv, review  # noqa: E402
from tests.test_vulnscan_executor import (  # noqa: E402
    _pin_fix,
    GIT,
    _git_repo,
    _http,
    _install,
    _no_begun,
    _repo_with_remote,
    _req,
    _sh,
    env,
)

# ---------------------------------------------------------------- round 2, Area P (X1, X2, X4, M1, K-E*, V31, G7)

def _committed(path: Path, files: dict[str, bytes]) -> tuple[Path, str]:
    """A repository whose base commit holds ``files``; returns (repo, base sha)."""
    repo = _git_repo(path)
    for name, data in files.items():
        (repo / name).write_bytes(data)
    _sh(repo, "add", "-A")
    _sh(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return repo, _sh(repo, "rev-parse", "HEAD").strip()


REQ_BASE = b'# pins\r\nPyJWT[crypto]==2.3.0 ; python_version >= "3.8"  # auth\r\nflask>=3\r\n'
REQ_EDITS = [   # (new bytes, None = accepted, else the refusal it must give)
    (REQ_BASE.replace(b"==2.3.0", b"==2.15.0"), None),                                  # extras/marker kept
    (REQ_BASE.replace(b"[crypto]==2.3.0", b"[evil]==2.15.0"), "only its version"),     # X1: extras changed
    (REQ_BASE.replace(b'.8"  # auth', b'.8" --index-url https://evil.example/  # auth').replace(b"2.3.0", b"2.15.0"),
     "only its version"),                                                               # X1: trailing option
    (REQ_BASE.replace(b"==2.3.0", b"==2.1.0"), "not an upgrade"),                       # X1: downgrade
    (REQ_BASE.replace(b"==2.3.0", b"==2.15.0rc1"), "not an upgrade"),                   # not a final release
    (REQ_BASE.replace(b'auth\r\n', b'auth\n').replace(b"2.3.0", b"2.15.0"), "only its version"),   # line ending
    (REQ_BASE.replace(b"PyJWT[crypto]==2.3.0", b"PyJWX[crypto]==2.15.0"), "only its version"),     # K-E6
    (REQ_BASE.replace(b"flask>=3\r\n", b"flask>=3\r\nevil==1.0\r\n"), "line count changed"),       # K-E5
    (REQ_BASE.replace(b"flask>=3", b"flask>=2"), "non-pin line"),
]


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize(("new", "refusal"), REQ_EDITS)
def test_x1_a_pin_edit_may_change_only_its_version_upwards(env: dict, tmp_path: Path, new: bytes,
                                                           refusal: str | None) -> None:
    repo, base = _committed(tmp_path / "r", {"requirements.txt": REQ_BASE})
    (repo / "requirements.txt").write_bytes(new)
    if refusal is None:
        assert executor.validate_diff(repo, base) == (["requirements.txt"], [])
    else:
        with pytest.raises(executor.ExecError, match=refusal):
            executor.validate_diff(repo, base)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_x1_hash_pinned_requirements_are_never_edited(env: dict, tmp_path: Path) -> None:
    old = b"PyJWT==2.3.0 \\\n    --hash=sha256:" + b"a" * 64 + b"\n"
    repo, base = _committed(tmp_path / "r", {"requirements.txt": old})
    (repo / "requirements.txt").write_bytes(old.replace(b"2.3.0", b"2.15.0"))
    with pytest.raises(executor.ExecError, match="--hash"):
        executor.validate_diff(repo, base)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_a_cargo_lock_change_is_never_staged(env: dict, tmp_path: Path) -> None:
    """Cargo.lock fixes are plan-only (fix.plan_cargo_lock): validate_diff refuses any change to one."""
    old = b'version = 3\n\n[[package]]\nname = "smallvec"\nversion = "1.6.0"\n'
    repo, base = _committed(tmp_path / "r", {"Cargo.lock": old})
    (repo / "Cargo.lock").write_bytes(old.replace(b"1.6.0", b"1.6.1"))
    with pytest.raises(executor.ExecError, match="outside dependency pins/lockfiles: Cargo.lock"):
        executor.validate_diff(repo, base)


def _rename_lock(repo: Path, tmp: Path) -> None:
    _sh(repo, "-c", "diff.renames=true", "mv", "package-lock.json", "npm-shrinkwrap.json")


def _delete_req(repo: Path, tmp: Path) -> None:
    (repo / "requirements.txt").unlink()


def _exec_bit(repo: Path, tmp: Path) -> None:
    (repo / "requirements.txt").write_text("PyJWT==2.15.0\n")
    (repo / "requirements.txt").chmod(0o755)


def _symlink_req(repo: Path, tmp: Path) -> None:   # X2: validate_diff must not read through it
    (tmp / "outside.txt").write_text("PyJWT==2.15.0\n")
    (repo / "requirements.txt").unlink()
    (repo / "requirements.txt").symlink_to(tmp / "outside.txt")


def _untracked_symlink(repo: Path, tmp: Path) -> None:
    (tmp / "outside.txt").write_text("PyJWT==2.15.0\n")
    (repo / "requirements-dev.txt").symlink_to(tmp / "outside.txt")


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize(("change", "refusal"), [(_rename_lock, "deleted"), (_delete_req, "deleted"),
                                                 (_exec_bit, "mode"), (_symlink_req, "mode"),
                                                 (_untracked_symlink, "not a regular file")])
def test_m1_renames_deletions_and_mode_changes_are_refused(env: dict, tmp_path: Path, change: object,
                                                           refusal: str) -> None:
    lock = json.dumps({"lockfileVersion": 3, "packages": {"": {}}}).encode()
    repo, base = _committed(tmp_path / "r", {"requirements.txt": b"PyJWT==2.3.0\n", "package-lock.json": lock})
    _sh(repo, "config", "diff.renames", "true")
    change(repo, tmp_path)  # type: ignore[operator]
    with pytest.raises(executor.ExecError, match=refusal):
        executor.validate_diff(repo, base)


REG = "https://registry.npmjs.org/a/-/a-1.0.0.tgz"


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize("version", ["git+ssh://git@evil.example/a.git#0123abc", "https://evil.example/a.tgz",
                                     "github:evil/a", "file:../a"])
@pytest.mark.parametrize("nested", [False, True])
def test_x4_a_v1_entry_with_a_url_version_and_no_resolved_is_refused(env: dict, tmp_path: Path, version: str,
                                                                     nested: bool) -> None:
    repo, base = _committed(tmp_path / "r", {"package-lock.json": b'{"lockfileVersion": 1}'})
    entry: dict = {"version": version}
    deps = {"a": {"version": "1.0.0", "resolved": REG, "dependencies": {"b": entry}}} if nested else {"b": entry}
    (repo / "package-lock.json").write_text(json.dumps({"lockfileVersion": 1, "dependencies": deps}))
    with pytest.raises(executor.ExecError, match="not a registry version"):
        executor.validate_diff(repo, base)


def _ws_lock(links: dict[str, dict], a_version: str = "1.0.0") -> bytes:
    pkgs = {"": {"workspaces": ["packages/*"]}, "packages/w": {"version": "1.0.0"},
            "node_modules/a": {"version": a_version, "resolved": REG.replace("1.0.0", a_version)}, **links}
    return json.dumps({"lockfileVersion": 3, "packages": pkgs}, indent=2).encode()


W_LINK = {"node_modules/w": {"resolved": "packages/w", "link": True}}


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize(("base_links", "new_links", "ok"), [
    (W_LINK, W_LINK, True),                                                          # unchanged in-repo link: kept
    ({}, W_LINK, False),                                                             # a link the fix introduced
    (W_LINK, {"node_modules/w": {"resolved": "packages/x", "link": True}}, False),   # a link the fix retargeted
    ({"node_modules/w": {"resolved": "../outside", "link": True}},) * 2 + (False,),  # escapes the repository
    ({"node_modules/w": {"resolved": "/abs/w", "link": True}},) * 2 + (False,),
    ({"node_modules/w": {"resolved": "packages/w", "link": "yes"}},) * 2 + (False,),
])
def test_x4_workspace_link_policy(env: dict, tmp_path: Path, base_links: dict, new_links: dict, ok: bool) -> None:
    repo, base = _committed(tmp_path / "r", {"package-lock.json": _ws_lock(base_links)})
    (repo / "package-lock.json").write_bytes(_ws_lock(new_links, a_version="1.0.1"))
    if ok:
        assert executor.validate_diff(repo, base) == (["package-lock.json"], [])
    else:
        with pytest.raises(executor.ExecError, match="registry allowlist"):
            executor.validate_diff(repo, base)


def test_k_e3_a_symlinked_decision_log_is_refused(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host, "log": "x"})
    log = env["gov"] / "references" / "security-decisions.log.csv"
    real = env["tmp"] / "elsewhere.csv"
    real.write_text(log.read_text())                       # a clean log, but reached through a link
    log.unlink()
    log.symlink_to(real)
    res = executor.act("server-audit", "web1", env["cfg"])
    assert res["status"] == "refused" and "symlink" in res["detail"], res
    assert _no_begun(ledger.read())


@pytest.mark.parametrize("payload", [{"markdown": 5}, {"markdown": "x", "extra": "y"}, {}, ["# r"], "# r", None])
def test_k_e16_a_malformed_report_payload_is_refused(env: dict, payload: object) -> None:
    with pytest.raises(executor.ExecError, match="payload"):
        executor_serve._read_request(_req(env, "1" * 32, op="report", target="run", payload=payload))
    run = env["tmp"] / "state" / "2026-10-22"
    run.mkdir(parents=True)
    assert executor.serve_requests(env["cfg"], run_dir=run) == []
    assert (executor.spool_dir() / "processed" / f"rejected-{'1' * 32}.json").exists()
    assert [(r["kind"], r["op"]) for r in ledger.read()] == [("refused", "(invalid)")]


class _OsvFeed:
    """OSV and PyPI replies in their real shapes: querybatch answers ``{}`` for a package with no advisory."""

    def __init__(self, vulnerable: dict[tuple[str, str], list[str]], releases: dict[str, dict[str, str]]) -> None:
        self.vulnerable, self.releases = vulnerable, releases
        self.asked: list[tuple[str, str]] = []

    def __call__(self, url: str, payload: dict | None = None, **_: object) -> object:
        if url == f"{osv.OSV_API}/querybatch" and payload is not None:
            out: list[dict] = []
            for q in payload["queries"]:
                self.asked.append((q["package"]["name"], q["version"]))
                ids = self.vulnerable.get((q["package"]["name"], q["version"]), [])
                out.append({"vulns": [{"id": i, "modified": "2026-01-01T00:00:00Z"} for i in ids]} if ids else {})
            return {"results": out}
        name = url.removeprefix(f"{fix.PYPI}/").removesuffix("/json")
        if url.startswith(f"{fix.PYPI}/") and name in self.releases:
            return {"info": {"name": name}, "releases": {
                v: [{"filename": f"{name}-{v}.tar.gz", "upload_time_iso_8601": t, "yanked": False}]
                for v, t in self.releases[name].items()}}
        raise _http.FeedError(f"unexpected URL {url}")


@pytest.mark.skipif(not GIT, reason="git not available")
def test_v31_stage_fix_with_the_real_fix_path_and_a_fake_osv_feed(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    feed = _OsvFeed({("PyJWT", "2.3.0"): ["GHSA-0000-0000-0001"], ("PyJWT", "2.4.0"): ["GHSA-0000-0000-0002"]},
                    {"PyJWT": {"2.3.0": "2021-12-01T00:00:00Z", "2.4.0": "2022-05-01T00:00:00Z",
                               "2.10.1": "2024-11-01T00:00:00Z", "3.0.0": "2025-01-01T00:00:00Z"}})
    monkeypatch.setattr(osv._http, "fetch_json", feed)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "done" and res["changed"] == ["requirements.txt"], res
    assert _sh(repo, "show", f"{res['branch']}:requirements.txt") == "PyJWT==2.10.1\nflask>=3\n"
    assert ("PyJWT", "2.10.1") in feed.asked and ("PyJWT", "3.0.0") in feed.asked     # clean ({}) and checked


def test_g7_a_begun_row_without_done_is_reported_and_serving_continues(env: dict,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    g = _install(env)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host, "log": "x"})
    with ledger.locked():                                    # a run killed between `begun` and `done`
        ledger.append("begun", grant_id=g["grant_id"], op="server-audit", target="web1", request_id="7" * 32)
    assert f"action begun but never finished: {'7' * 32}" in review.build()
    _req(env, "a" * 32, op="server-audit", target="web1", rationale="next run")
    assert [r["status"] for r in executor.serve_requests(env["cfg"])] == ["done"]
    assert f"action begun but never finished: {'7' * 32}" in review.build()


# ---------------------------------------------------------------- round 2, R8 @security findings

def test_r8_1_a_fifo_in_the_spool_never_stalls_serving(env: dict) -> None:
    """The agent can create a FIFO named <id>.json in its rw spool: serving must not block on it."""
    req_dir = executor.spool_dir() / "requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    fifo = req_dir / ("a" * 32 + ".json")
    os.mkfifo(fifo)
    done: list[object] = []
    t = threading.Thread(target=lambda: done.append(executor.serve_requests(env["cfg"])), daemon=True)
    t.start()
    t.join(15)
    if t.is_alive():   # a writer unblocks the reader's open, so the thread can finish; then fail
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        t.join(15)
        pytest.fail("serve_requests blocked on a FIFO in the spool")
    assert done == [[]] and not fifo.exists()                     # served promptly; the FIFO was set aside
    assert (executor.spool_dir() / "processed" / f"rejected-{fifo.name}").exists()
    assert any("not a regular file" in r.get("detail", "") for r in ledger.read() if r["kind"] == "refused")


@pytest.mark.skipif(not GIT, reason="git not available")
def test_r8_3_the_digest_diff_runs_no_repository_diff_driver(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``diff.external`` / textconv in the repo's own config must not run during stage-fix."""
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    marker = env["tmp"] / "diff-driver-ran"
    hook = env["tmp"] / "ext.sh"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    hook.chmod(0o755)
    _sh(repo, "config", "diff.external", str(hook))
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    res = executor.act("stage-fix", str(repo), env["cfg"], rationale="r8-3")
    assert res["status"] == "done", res
    assert not marker.exists(), "stage-fix ran the repository's diff.external"


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize("kind", ["smudge", "clean", "process"])
def test_r8_stage_fix_refuses_a_repo_whose_config_defines_a_filter_driver(env: dict,
                                                                            monkeypatch: pytest.MonkeyPatch,
                                                                            kind: str) -> None:
    """Checkout (worktree add) would run a smudge filter: refuse before any git operation on the repo."""
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    marker = env["tmp"] / "filter-ran"
    hook = env["tmp"] / "filter.sh"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\ncat\n')
    hook.chmod(0o755)
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* filter=x\n")
    _sh(repo, "config", f"filter.x.{kind}", str(hook))
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    res = executor.act("stage-fix", str(repo), env["cfg"], rationale="r8")
    assert res["status"] == "failed" and "filter driver" in res["detail"], res
    assert not marker.exists()
    wts = executor._worktrees_dir()
    assert not wts.exists() or not list(wts.iterdir())             # refused before any worktree was made


def test_r8_5_git_refusing_the_repository_is_a_refusal_not_clean(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository git will not open (e.g. safe.directory) must not read as 'no uncommitted changes'."""
    def refuse(directory: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: detected dubious ownership in repository")

    monkeypatch.setattr(executor, "query_git", refuse)
    assert "git refuses to read this repository" in fix.git_config_problem(tmp_path)

    def not_repo(directory: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: not a git repository (or any parent)")

    monkeypatch.setattr(executor, "query_git", not_repo)
    assert fix.git_config_problem(tmp_path) == ""


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize("base_kind", ["symlink", "executable"])
def test_m1_a_change_from_a_special_base_mode_is_refused(env: dict, tmp_path: Path, base_kind: str) -> None:
    """Only the raw old mode shows a tracked symlink (or exec-bit file) turned into a plain file: refuse it."""
    repo = _git_repo(tmp_path / "r")
    (repo / "pins.txt").write_text("PyJWT==2.3.0\n")
    if base_kind == "symlink":
        (repo / "requirements.txt").symlink_to("pins.txt")
    else:
        (repo / "requirements.txt").write_text("PyJWT==2.3.0\n")
        (repo / "requirements.txt").chmod(0o755)
    _sh(repo, "add", "-A")
    _sh(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    base = _sh(repo, "rev-parse", "HEAD").strip()
    (repo / "requirements.txt").unlink()
    (repo / "requirements.txt").write_text("PyJWT==2.15.0\n")          # a regular 0644 file now
    with pytest.raises(executor.ExecError, match="mode"):
        executor.validate_diff(repo, base)


# ---------------------------------------------------------------- item 3: config problems before fetch / root check

_DUBIOUS = "fatal: detected dubious ownership in repository at '/x'"


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_on_a_repo_git_refuses_reports_the_safe_directory_reason(env: dict,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """safe.directory: the clear 'git refuses to read' reason, not 'is not the top of a git repository'."""
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])

    def refuse(directory: Path, *args: str, **kw: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(["git", *args], 128, "", _DUBIOUS)

    monkeypatch.setattr(executor, "git_proc", refuse)
    monkeypatch.setattr(executor, "query_git", refuse)
    res = executor.act("stage-fix", str(repo), env["cfg"], rationale="item3")
    assert res["status"] == "refused", res
    assert "git refuses to read this repository" in res["detail"] and "fix it by hand" in res["detail"], res
    assert "not the top" not in res["detail"]


@pytest.mark.skipif(not GIT, reason="git not available")
def test_repo_root_still_refuses_a_path_that_is_not_a_repository_top(env: dict, tmp_path: Path) -> None:
    """A plain directory, and a subdirectory of a repository, keep the 'not the top' refusal."""
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(executor.ExecError, match="is not the top of a git repository"):
        executor._repo_root(str(plain))
    repo = _git_repo(tmp_path / "r")
    (repo / "sub").mkdir()
    with pytest.raises(executor.ExecError, match="is not the top of a git repository"):
        executor._repo_root(str(repo / "sub"))


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_checks_for_a_filter_driver_before_fetching(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter-driver refusal comes first: no fetch (or any other network git) runs on such a repository."""
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* filter=x\n")
    _sh(repo, "config", "filter.x.smudge", "cat")
    calls: list[tuple[str, ...]] = []
    real = executor.git_proc

    def spy(r: Path, *args: str, **kw: object) -> subprocess.CompletedProcess:
        calls.append(args)
        return real(r, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(executor, "git_proc", spy)
    res = executor.act("stage-fix", str(repo), env["cfg"], rationale="item3")
    assert res["status"] == "failed" and "defines a filter driver" in res["detail"], res
    assert not any(a and a[0] == "fetch" for a in calls), calls
