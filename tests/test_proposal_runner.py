"""P4a of the orchestrator-only-writes pilot: the out-of-session runner, its queue, and key custody.

Live probes on macOS (2026-10-06) showed why the runner exists:

* a sandboxed child reads a same-user parent's exec-time environment via ``KERN_PROCARGS2``, so the key must
  come from a file;
* ``sandbox-exec`` can't apply a profile from inside an already-sandboxed session.

The runner, started outside every session, alone holds the key and serves the orchestrator's queue.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentteams import proposal_runner as R
from agentteams import proposals as P

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    for name in P.KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(P, "_FILE_KEY", None)


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    path = tmp_path / "keys" / "proposal-ledger.key"
    path.parent.mkdir()
    monkeypatch.setattr(P, "KEY_DIR", str(path.parent))
    path.write_text("runner-secret\n")
    path.chmod(0o600)
    monkeypatch.setenv(P.KEY_FILE_ENV, str(path))
    return path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n")
    brief = {"project_name": "P", "project_goal": "A small project for runner tests.",
             "write_policy": "orchestrator-only", "agent_policies": {"producer": {"write_scopes": ["src/"]}}}
    (root / "brief.json").write_text(json.dumps(brief))
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root, P.load_policy(brief, brief_rel="brief.json")


@pytest.fixture
def runner(project, key_file):
    root, policy = project
    r = R.Runner(root, root / "brief.json", policy)
    yield r
    r.close()


def _roundtrip(runner, request):
    request_id = R.enqueue(runner.root, request)
    runner.serve_once()
    return R.wait_result(runner.root, request_id, timeout=5)


# --- key custody --------------------------------------------------------------------------------


def test_key_file_must_be_private_regular_and_not_a_link(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "KEY_DIR", str(tmp_path))
    loose = tmp_path / "loose.key"
    loose.write_text("k")
    loose.chmod(0o644)
    with pytest.raises(P.ProposalError, match="0600"):
        P.use_key_file(str(loose))
    link = tmp_path / "link.key"
    link.symlink_to(loose)
    with pytest.raises(P.ProposalError, match="symlink"):
        P.use_key_file(str(link))
    with pytest.raises(P.ProposalError, match="cannot be opened"):
        P.use_key_file(str(tmp_path / "missing.key"))


def test_file_custody_ignores_environment_keys(key_file, monkeypatch):
    monkeypatch.setenv(P.KEY_ENV[0], "env-secret")
    P.use_key_file()
    assert P._key() == b"runner-secret"


# --- the queue ----------------------------------------------------------------------------------


def test_dispatch_and_proposal_round_trip(runner):
    nonce = _roundtrip(runner, {"kind": "issue-dispatch", "agent": "producer"})["result"]["nonce"]
    target = runner.root / "src" / "a.py"
    import hashlib
    artifact = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py",
                "base_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "content": "x = 2\n",
                "rationale": "bump"}
    result = _roundtrip(runner, {"kind": "apply-proposal", "artifact": artifact})
    assert result["ok"], result
    assert target.read_text() == "x = 2\n"
    assert _roundtrip(runner, {"kind": "verify-ledger"})["result"]["problems"] == []


def test_run_request_needs_a_confined_entry(runner):
    nonce = _roundtrip(runner, {"kind": "issue-dispatch", "agent": "producer"})["result"]["nonce"]
    runner.policy = P.load_policy({"agent_policies": {"producer": {"commands": [
        {"prefix": [PY, "-c", "pass"], "args": []}]}}})
    artifact = {"kind": "command-request", "dispatch": nonce, "argv": [PY, "-c", "pass"], "purpose": "t"}
    result = _roundtrip(runner, {"kind": "run-request", "artifact": artifact})
    assert not result["ok"] and "no confined_programs entry" in result["error"]


def test_refusal_is_a_result_not_a_crash(runner):
    result = _roundtrip(runner, {"kind": "apply-proposal", "artifact": {"kind": "change-proposal"}})
    assert not result["ok"] and result["exit"] == 1


def test_symlinked_request_is_dropped_and_never_echoed(runner, key_file):
    requests = runner.root / R.REQUESTS_REL
    (requests / ("a" * 32 + ".json")).symlink_to(key_file)
    assert runner.serve_once() == 0
    assert not list((runner.root / R.RESULTS_REL).glob("*.json"))
    assert not (requests / ("a" * 32 + ".json")).exists()


def test_stray_names_are_ignored(runner):
    requests = runner.root / R.REQUESTS_REL
    (requests / "not-an-id.json").write_text("{}")
    assert runner.serve_once() == 0
    assert (requests / "not-an-id.json").exists()


def test_result_deleted_after_ack(runner):
    request_id = R.enqueue(runner.root, {"kind": "verify-ledger"})
    runner.serve_once()
    R.wait_result(runner.root, request_id, timeout=5)
    runner.serve_once()
    assert not (runner.root / R.RESULTS_REL / f"{request_id}.json").exists()


def test_brief_change_stops_the_runner(runner):
    (runner.root / "brief.json").write_text(json.dumps({"write_policy": "orchestrator-only",
                                                        "agent_policies": {"producer": {"write_scopes": ["."]}}}))
    with pytest.raises(R.RunnerError, match="brief changed"):
        runner.serve_once()


def test_second_runner_refused(runner, project):
    root, policy = project
    with pytest.raises(R.RunnerError, match="already serving"):
        R.Runner(root, root / "brief.json", policy)


def test_enqueue_without_a_runner_refused(project):
    root, _ = project
    with pytest.raises(R.RunnerError, match="no runner"):
        R.enqueue(root, {"kind": "verify-ledger"})


# --- the CLI ------------------------------------------------------------------------------------


def _cli(*args, cwd, env=None):
    return subprocess.run([PY, str(REPO / "build_team.py"), *args], cwd=cwd, capture_output=True, text=True,
                          timeout=120, env=env)


def test_cli_queues_through_a_live_runner(project, tmp_path):
    root, _ = project
    home = tmp_path / "home"   # the runner's key must sit in ~/.config/agentteams/keys: give it its own HOME
    keys = home / ".config" / "agentteams" / "keys"
    keys.mkdir(parents=True)
    (keys / "proposal-ledger.key").write_text("runner-secret\n")
    (keys / "proposal-ledger.key").chmod(0o600)
    env = {k: v for k, v in os.environ.items() if k not in (*P.KEY_ENV, P.KEY_FILE_ENV)}
    env["HOME"] = str(home)
    server = subprocess.Popen([PY, str(REPO / "build_team.py"), "--serve-requests", "--project", str(root),
                               "--description", str(root / "brief.json")], cwd=root, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            if R.runner_alive(root):
                break
            time.sleep(0.1)
        assert R.runner_alive(root), "runner never started"
        client_env = dict(env)  # the session has no key at all (env or file custody)
        out = _cli("--issue-dispatch", "--agent", "producer", "--project", str(root), cwd=root, env=client_env)
        assert out.returncode == 0, out.stderr
        assert len(out.stdout.strip()) == 32
        out = _cli("--verify-proposal-ledger", "--project", str(root), cwd=root, env=client_env)
        assert out.returncode == 0 and "OK" in out.stdout, out.stdout + out.stderr
    finally:
        server.terminate()
        server.wait(timeout=10)


# --- session profiles under the switch -----------------------------------------------------------


def test_session_profiles_write_deny_the_ledger_only_under_the_switch():
    from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile
    from agentteams.frameworks._sandbox_emit import _build_sandbox_block

    on = _build_sandbox_block(["."], protect_ledger=True)["filesystem"]["denyWrite"]
    off = _build_sandbox_block(["."])["filesystem"]["denyWrite"]
    assert ".agentteams" in on and ".agentteams" not in off
    assert ".agentteams" in _build_seatbelt_profile(["."], protect_ledger=True)
    assert ".agentteams\"" not in _build_seatbelt_profile(["."])


def test_key_file_outside_the_read_denied_dir_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "KEY_DIR", str(tmp_path / "keys"))
    elsewhere = tmp_path / "elsewhere.key"
    elsewhere.write_text("k")
    elsewhere.chmod(0o600)
    with pytest.raises(P.ProposalError, match="must sit in"):
        P.use_key_file(str(elsewhere))


def test_runner_refuses_a_brief_without_the_switch(project, key_file):
    root, policy = project
    (root / "brief.json").write_text(json.dumps({"agent_policies": {}}))
    with pytest.raises(R.RunnerError, match="write_policy"):
        R.Runner(root, root / "brief.json", policy)


def test_gates_run_confined_in_the_runner(runner, project):
    import hashlib

    from agentteams import confinement as C
    toolchain = os.path.dirname(os.path.dirname(os.path.realpath(PY)))
    gated = P.load_policy({"agent_policies": {"producer": {"write_scopes": ["src/"]}},
                           "proposal_gates": {"g": {"glob": "src/*.py", "argv": [PY, "-c", "pass", "{file}"],
                                                    "exec": [toolchain, os.path.dirname(PY)]}}})
    runner.policy = gated
    nonce = _roundtrip(runner, {"kind": "issue-dispatch", "agent": "producer"})["result"]["nonce"]
    target = runner.root / "src" / "a.py"
    artifact = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py",
                "base_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "content": "x = 3\n",
                "rationale": "gated"}
    result = _roundtrip(runner, {"kind": "apply-proposal", "artifact": artifact})
    if C.available():
        assert result["ok"], result
        assert target.read_text() == "x = 3\n"
    else:  # no usable sandbox and no opt-out: refused, nothing written
        assert not result["ok"] and "no usable OS sandbox" in result["error"]
        assert target.read_text() == "x = 1\n"


def test_symlinked_queue_dir_refused(runner, tmp_path):
    requests = runner.root / R.REQUESTS_REL
    for p in requests.iterdir():
        p.unlink()
    requests.rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    requests.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(R.RunnerError, match="not a plain directory"):
        runner.serve_once()


def test_cli_stays_in_queue_mode_after_the_runner_stops(project, key_file):
    root, policy = project
    R.Runner(root, root / "brief.json", policy).close()  # leaves the lock, removes the heartbeat
    env = {k: v for k, v in os.environ.items() if k not in (P.KEY_FILE_ENV,)}
    env[P.KEY_ENV[0]] = "session-env-key"   # a key in the session must not be used directly
    out = _cli("--verify-proposal-ledger", "--project", str(root), cwd=root, env=env)
    assert out.returncode == 1 and "no runner" in out.stderr, out.stdout + out.stderr
