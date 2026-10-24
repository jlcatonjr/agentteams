"""End to end: an agent run with a scripted agent behind a fake ``docker`` (vulnscan test campaign I3).

Real except the container runtime, the model, GitHub and the dependency resolver (``fix.fix_path`` is
faked; the real one is covered with a fake OSV feed in the executor tests): ``agent_run.run_agent`` starts the
"gateway" and the "agent" through a fake ``docker`` on PATH. The fake agent speaks MCP over stdio to the real
``python -m agentteams.vulnscan.mcp`` with the host directories the real ``docker run -v`` flags name, and asserts
the mount modes the real container enforces (run and results read-only, request spool read-write); the host
shares those directories with the executor, so the fake cannot enforce ``:ro`` itself. Its own log goes to a side directory
(``E2E_OUT``), never into a mount. It lists findings, requests stage-fix and open-pr, waits for the
host executor (real git, a real Ed25519 grant, the real MAC'd ledger) and submits a report. A fake ``gh`` opens
the pull request. Network-free.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import agent_run, executor, fix, grant, ledger, report_store, review  # noqa: E402
from tests._vulnscan_helpers import operator_home, sign_grant  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git not available")

#: The scripted agent: maps the container paths from the ``-v`` flags back to host paths and drives the MCP.
FAKE_AGENT = r'''
import json, os, subprocess, sys, time
argv = sys.argv[1:]
mounts, modes = {}, {}
for i, a in enumerate(argv[:-1]):
    if a == "-v":
        host, inner, mode = (argv[i + 1].split(":") + ["rw"])[:3]
        mounts[inner], modes[inner] = host, mode
assert modes["/work/run"] == "ro" and modes["/work/spool/results"] == "ro", modes
assert modes["/work/spool/requests"] == "rw", modes
out = os.environ["E2E_OUT"]
task = next(a.split("=", 1)[1] for a in argv if a.startswith("task="))
open(os.path.join(out, "agent-task.txt"), "w").write(task)
srv = subprocess.Popen([sys.executable, "-m", "agentteams.vulnscan.mcp", "--run-dir", mounts["/work/run"],
                        "--spool", mounts["/work/spool/requests"], "--results", mounts["/work/spool/results"]],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
n = 0
def rpc(method, params=None):
    global n
    n += 1
    srv.stdin.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params or {}}) + "\n")
    srv.stdin.flush()
    return json.loads(srv.stdout.readline())
def tool(name, **args):
    res = rpc("tools/call", {"name": name, "arguments": args})["result"]
    text = res["content"][0]["text"]
    return text if res.get("isError") else (json.loads(text) if text[:1] in "{[" else text)
rpc("initialize", {"protocolVersion": "2025-06-18"})
findings = tool("list_findings")
target = os.environ["E2E_TARGET"]
log = {"findings_total": findings["total"]}
for op in ("stage-fix", "open-pr"):
    rid = tool("request", op=op, target=target, rationale="critical PyJWT advisory")["id"]
    for _ in range(600):
        st = tool("request_status", id=rid)
        if st["status"] != "pending":
            break
        time.sleep(0.05)
    log[op] = st
tool("submit_report", markdown="# Run report\n\nStaged PyJWT 2.15.0 and opened " + str(log["open-pr"].get("pr")))
srv.stdin.close()
srv.wait(timeout=30)
open(os.path.join(out, "agent-log.json"), "w").write(json.dumps(log))
'''

#: The fake docker: records every call; ``run --rm`` with the agent name runs the scripted agent.
FAKE_DOCKER = r'''#!{python}
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(here, "docker.calls"), "a") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:2] == ["image", "inspect"]:
    print("sha256:e2e")
elif args[:1] == ["run"] and "-d" in args:          # the gateway: record spend as a metered run would
    spend = next(a.split(":")[0] for a in args if a.endswith(":/work/out:rw"))
    with open(os.path.join(spend, "spend.json"), "w") as fh:
        json.dump({"calls": 3, "prompt_tokens": 900, "completion_tokens": 120, "unmetered": 0}, fh)
elif args[:1] == ["run"]:                          # the agent container
    sys.exit(subprocess.run([sys.executable, os.path.join(here, "fake_agent.py"), *args]).returncode)
'''


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """An operator home, a signed grant, a repo with a GitHub-style origin, fake docker/gh, and today's scan."""
    world = operator_home(tmp_path, monkeypatch)
    gov = world["gov"]

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "repos" / "svc"
    repo.mkdir(parents=True)

    def git(*a: str) -> None:
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (repo / "requirements.txt").write_text("PyJWT==2.3.0\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("remote", "add", "origin", "https://github.com/o/svc.git")
    git("config", f"url.{remote}.insteadOf", "https://github.com/o/svc.git")
    git("push", "-q", "origin", "main")
    git("remote", "set-head", "origin", "main")

    rec = grant.build(ops=["stage-fix", "open-pr"], repo_roots=[str(tmp_path / "repos")], hosts=[], days=7,
                      max_uses=5, key_id="op", governance_dir=str(gov))
    grant.install(sign_grant(world["pem"], rec))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "fake_agent.py").write_text(FAKE_AGENT)
    (bindir / "docker").write_text(FAKE_DOCKER.replace("{python}", sys.executable))
    (bindir / "gh").write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/gh.calls"\n'
                               'case "$*" in *"pr list"*) echo "[]";; *) echo https://github.example.com/o/svc/pull/9;; esac\n')
    for f in ("docker", "gh"):
        (bindir / f).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    monkeypatch.setenv("E2E_TARGET", str(repo))
    (tmp_path / "e2e-out").mkdir()
    monkeypatch.setenv("E2E_OUT", str(tmp_path / "e2e-out"))

    run_dir = tmp_path / "state" / "2026-10-22"
    run_dir.mkdir(parents=True)
    finding = {"kind": "package", "target": "svc", "name": "PyJWT", "version": "2.3.0", "ecosystem": "PyPI",
               "severity": 4, "fix": "2.15.0", "ids": ["GHSA-ffqj-6fqr-9h24"], "locations": ["requirements.txt"]}
    (run_dir / "summary.json").write_text(json.dumps({"findings": [finding], "feed_errors": []}))
    (run_dir / "summary.md").write_text("# Summary\n\nPyJWT 2.3.0 -> 2.15.0\n")

    team = tmp_path / "ops-team"
    team.mkdir()
    keyfile = tmp_path / "secrets" / "openrouter.env"
    keyfile.parent.mkdir()
    keyfile.write_text("OPENROUTER_API_KEY=sk-test\n")
    cfg = {"governance_dir": str(gov), "cooldown_days": 14, "notify": "none",
           "agent": {"enabled": True, "team_dir": str(team), "key_file": str(keyfile), "max_minutes": 5}}

    def fake_fix(root: Path, **kw: object) -> fix.FixResult:
        req = root / "requirements.txt"
        req.write_text(req.read_text().replace("PyJWT==2.3.0", "PyJWT==2.15.0"))
        return fix.FixResult(changes=[fix.PinChange("requirements.txt", "PyJWT", "2.3.0", "2.15.0")], applied=True)

    monkeypatch.setattr(fix, "fix_path", fake_fix)
    monkeypatch.setattr(agent_run, "selftest_ok", lambda cfg: (True, "e2e"))
    fast = types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda s: time.sleep(min(s, 0.1)))
    monkeypatch.setattr(agent_run, "time", fast)
    return {"cfg": cfg, "run_dir": run_dir, "repo": repo, "remote": remote, "bin": bindir, "tmp": tmp_path}


def test_agent_run_end_to_end(world: dict[str, Any]) -> None:
    """A full run: the agent's requests are executed under the grant, ledgered, reported and cleaned up."""
    status = agent_run.run_agent(world["cfg"], world["run_dir"], run_id="run-e2e")
    assert status["run_state"] == "completed", status
    assert status["spend"]["prompt_tokens"] == 900

    log = json.loads((world["tmp"] / "e2e-out" / "agent-log.json").read_text())
    assert log["findings_total"] == 1
    assert log["stage-fix"]["status"] == "done", log
    assert log["open-pr"]["status"] == "done" and log["open-pr"]["pr"].endswith("/pull/9"), log

    heads = subprocess.run(["git", "-C", str(world["remote"]), "branch"], capture_output=True, text=True).stdout
    assert log["stage-fix"]["branch"] in heads                       # pushed as a new ref, main untouched
    main = subprocess.run(["git", "-C", str(world["remote"]), "show", "main:requirements.txt"],
                          capture_output=True, text=True).stdout
    assert main == "PyJWT==2.3.0\n"
    assert not Path(log["stage-fix"]["worktree"]).exists()           # removed once the PR exists (D4)
    assert "--repo github.com/o/svc" in (world["bin"] / "gh.calls").read_text()

    rows = ledger.read()                                             # verifies the MAC chain and the mark
    pairs = [(r["op"], r["kind"]) for r in rows]
    assert pairs == [("stage-fix", "begun"), ("stage-fix", "done"), ("open-pr", "begun"), ("open-pr", "done"),
                     ("report", "done")], pairs
    assert [r["seq"] for r in rows] == [1, 2, 3, 4, 5]

    stored = report_store.load(world["run_dir"])                     # read through the verifying store
    assert stored and "Staged PyJWT 2.15.0" in stored and "completed" in stored

    task = (world["tmp"] / "e2e-out" / "agent-task.txt").read_text()
    assert task.strip()

    calls = [json.loads(line) for line in (world["bin"] / "docker.calls").read_text().splitlines()]
    removed = {c[-1] for c in calls if c[:2] == ["rm", "-f"]}
    assert {agent_run.AGENT_CONTAINER, agent_run.GATEWAY} <= removed  # both containers removed at the end
    run_flags = next(c for c in calls if c[:1] == ["run"] and agent_run.AGENT_CONTAINER in c)
    assert "--read-only" in run_flags and "--cap-drop" in run_flags

    md = review.build()
    assert "/pull/9" in md and "stage-fix" in md
    assert not list((executor.spool_dir() / "requests").glob("*.json"))   # every request was consumed
