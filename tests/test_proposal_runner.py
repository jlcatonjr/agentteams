"""P4a of the orchestrator-only-writes pilot: the out-of-session runner, its queue, and key custody.

Live probes on macOS (2026-10-06) showed why the runner exists:

* a sandboxed child reads a same-user parent's exec-time environment via ``KERN_PROCARGS2``, so the key must
  come from a file;
* ``sandbox-exec`` can't apply a profile from inside an already-sandboxed session.

The runner, started outside every session, alone holds the key and serves the orchestrator's queue.
"""

from __future__ import annotations

import hashlib
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


def test_results_carry_queue_wait_and_serve_time(runner):
    result = _roundtrip(runner, {"kind": "verify-ledger"})
    assert isinstance(result["queue_wait_ms"], int) and isinstance(result["serve_ms"], int)


# --- R1 (mcp-mediated-agent-writes): heartbeat thread, MCP command cap, non-blocking poll -------------------


def test_heartbeat_thread_keeps_the_runner_alive_while_it_is_busy(project, key_file, monkeypatch):
    """A long command no longer makes the runner look dead: the beat comes from its own thread."""
    root, policy = project
    monkeypatch.setattr(R, "HEARTBEAT_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(R, "HEARTBEAT_STALE_SECONDS", 1)
    r = R.Runner(root, root / "brief.json", policy)
    try:
        (root / R.HEARTBEAT_REL).unlink()
        time.sleep(0.5)  # no serve_once call: only the thread can have written it
        assert R.runner_alive(root)
    finally:
        r.close()
    assert not (root / R.HEARTBEAT_REL).exists()
    time.sleep(0.3)
    assert not (root / R.HEARTBEAT_REL).exists(), "the heartbeat thread outlived close()"


def _roundtrip_on(runner, request, channel):
    request_id = R.enqueue(runner.root, request, channel=channel)
    runner.serve_once()
    return R.wait_result(runner.root, request_id, timeout=5)


def test_mcp_channel_requests_get_the_shorter_command_cap(runner, monkeypatch):
    """R2: the channel comes from the queue directory; a sender-written field is ignored."""
    seen = []
    monkeypatch.setattr(P, "run_request", lambda artifact, **kw: seen.append(kw.get("timeout_cap")) or {"ran": False})
    nonce = P.issue_dispatch(runner.root, "producer")
    art = {"dispatch": nonce}
    assert _roundtrip_on(runner, {"kind": "run-request", "artifact": art, "via_agent": "producer"}, "mcp")["ok"]
    assert _roundtrip_on(runner, {"kind": "run-request", "artifact": art, "channel": "mcp"}, "orchestrator")["ok"]
    assert seen == [R.MCP_COMMAND_TIMEOUT, None]


def test_poll_result_never_blocks(runner):
    request_id = R.enqueue(runner.root, {"kind": "verify-ledger"})
    assert R.poll_result(runner.root, request_id) is None
    runner.serve_once()
    result = R.poll_result(runner.root, request_id)
    assert result is not None and result["ok"]
    with pytest.raises(R.RunnerError, match="bad request id"):
        R.poll_result(runner.root, "../x")


def test_results_always_fit_the_read_back_limit():
    """@security R1 condition 4: worst-case escaping (6 bytes per output byte) plus long lists still fit."""
    worst = "\x01" * P.MAX_OUTPUT_BYTES
    result = {"id": "a" * 32, "kind": "run-request", "ok": False, "error": "e" * 100000,
              "result": {"stdout": worst, "stderr": worst, "undeclared_writes": [f"f{i}" for i in range(50000)]}}
    data = R._fit_result(result)
    assert len(data) <= R.RESULT_MAX_BYTES
    back = json.loads(data)
    assert back["result_cut"] is True and back["id"] == "a" * 32


def test_close_leaves_no_late_heartbeat(project, key_file, monkeypatch):
    """@security R1 condition 3."""
    root, policy = project
    monkeypatch.setattr(R, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    r = R.Runner(root, root / "brief.json", policy)
    time.sleep(0.1)
    r.close()
    time.sleep(0.2)
    assert not (root / R.HEARTBEAT_REL).exists()


def test_a_stalled_serve_loop_stops_the_heartbeat(project, key_file, monkeypatch):
    """@security R1 condition 2: a stuck main thread can't hide behind a live heartbeat."""
    root, policy = project
    monkeypatch.setattr(R, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(R, "STALL_SECONDS", 0.2)
    monkeypatch.setattr(R, "HEARTBEAT_STALE_SECONDS", 0.3)
    r = R.Runner(root, root / "brief.json", policy)
    try:
        time.sleep(0.8)  # no serve_once: no progress
        assert not R.runner_alive(root) and "no progress" in (r.heartbeat_error or "")
    finally:
        r.close()


# --- R2 (mcp-mediated-agent-writes): channel routing and identity binding ------------------------------------


def test_mcp_channel_refuses_orchestrator_kinds(runner):
    for kind in ("issue-dispatch", "verify-ledger"):
        result = _roundtrip_on(runner, {"kind": kind, "agent": "producer"}, "mcp")
        assert not result["ok"] and "MCP channel can't queue" in result["error"]


def test_mcp_request_must_come_from_the_nonces_own_agent(runner):
    """A nonce read off the queue and replayed through another agent's server instance is refused."""
    nonce = P.issue_dispatch(runner.root, "producer")
    art = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py",
           "base_sha256": hashlib.sha256(b"x = 1\n").hexdigest(), "content": "x = 2\n", "rationale": "r"}
    stolen = _roundtrip_on(runner, {"kind": "stage-proposal", "artifact": art, "via_agent": "reviewer"}, "mcp")
    assert not stolen["ok"] and stolen["error"] == P._CHANNEL_REFUSAL
    own = _roundtrip_on(runner, {"kind": "stage-proposal", "artifact": art, "via_agent": "producer"}, "mcp")
    assert own["ok"] and own["result"]["staged"], own
    assert (runner.root / "src/a.py").read_text() == "x = 1\n"  # staged, not written


def test_an_mcp_agent_cannot_act_on_the_orchestrators_queue(runner):
    runner.policy.mcp_agents = frozenset({"producer"})
    nonce = P.issue_dispatch(runner.root, "producer")
    art = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py",
           "base_sha256": hashlib.sha256(b"x = 1\n").hexdigest(), "content": "x = 2\n", "rationale": "r"}
    result = _roundtrip_on(runner, {"kind": "apply-proposal", "artifact": art}, "orchestrator")
    assert not result["ok"] and "only through the agentteams_runner" in result["error"]
    assert (runner.root / "src/a.py").read_text() == "x = 1\n"


def test_unknown_channel_is_refused(runner):
    with pytest.raises(R.RunnerError, match="unknown channel"):
        R.enqueue(runner.root, {"kind": "verify-ledger"}, channel="side-door")


def test_the_mcp_channel_gives_no_nonce_oracle(runner):
    """@security R2 condition 2: unknown, expired or foreign nonces all get the same answer on the MCP channel."""
    live = P.issue_dispatch(runner.root, "producer")
    answers = set()
    for nonce, via in (("0" * 32, "producer"), ("not-a-nonce", "producer"), (live, "reviewer")):
        art = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py", "base_sha256": "absent",
               "content": "x\n", "rationale": "r"}
        result = _roundtrip_on(runner, {"kind": "stage-proposal", "artifact": art, "via_agent": via}, "mcp")
        answers.add(result["error"])
    assert answers == {P._CHANNEL_REFUSAL}


def test_mcp_requests_must_name_their_agent(runner):
    result = _roundtrip_on(runner, {"kind": "run-request", "artifact": {}}, "mcp")
    assert not result["ok"] and "via_agent" in result["error"]


# --- R3 (mcp-mediated-agent-writes): staged and direct writes ------------------------------------------------


def _stage_via_mcp(runner, content="x = 2\n", kind="change-proposal", agent="producer"):
    nonce = P.issue_dispatch(runner.root, agent)
    art = {"kind": kind, "dispatch": nonce, "path": "src/a.py", "rationale": "r",
           "base_sha256": hashlib.sha256((runner.root / "src/a.py").read_bytes()).hexdigest()}
    if kind == "change-proposal":
        art["content"] = content
    return _roundtrip_on(runner, {"kind": "stage-proposal", "artifact": art, "via_agent": agent}, "mcp")


def test_staged_write_lands_only_when_the_orchestrator_approves(runner):
    receipt = _stage_via_mcp(runner)["result"]
    sid = receipt["sid"]
    assert (runner.root / "src/a.py").read_text() == "x = 1\n"
    listed = _roundtrip_on(runner, {"kind": "list-staged"}, "orchestrator")["result"]["staged"]
    assert [e["sid"] for e in listed] == [sid] and "artifact" not in listed[0]
    shown = _roundtrip_on(runner, {"kind": "show-staged", "sid": sid}, "orchestrator")["result"]
    assert shown["artifact"]["content"] == "x = 2\n" and "dispatch" not in shown["artifact"]
    applied = _roundtrip_on(runner, {"kind": "apply-staged", "sid": sid}, "orchestrator")
    assert applied["ok"] and applied["result"]["agent"] == "producer"
    assert (runner.root / "src/a.py").read_text() == "x = 2\n"
    ledger = (runner.root / P.LEDGER_REL).read_text()
    assert "stage-proposal" in ledger and "apply-staged" in ledger and P.verify_ledger(runner.root) == []


def test_approval_rechecks_the_base(runner):
    sid = _stage_via_mcp(runner)["result"]["sid"]
    (runner.root / "src/a.py").write_text("x = 99\n")  # changed after staging
    applied = _roundtrip_on(runner, {"kind": "apply-staged", "sid": sid}, "orchestrator")
    assert not applied["ok"] and "stale base" in applied["error"]
    assert (runner.root / "src/a.py").read_text() == "x = 99\n"


def test_reject_drops_the_record(runner):
    sid = _stage_via_mcp(runner)["result"]["sid"]
    rejected = _roundtrip_on(runner, {"kind": "reject-staged", "sid": sid, "reason": "no"}, "orchestrator")
    assert rejected["ok"] and rejected["result"]["rejected"]
    again = _roundtrip_on(runner, {"kind": "apply-staged", "sid": sid}, "orchestrator")
    assert not again["ok"] and "no staged proposal" in again["error"]


def test_a_staged_deletion_waits_for_approval(runner):
    sid = _stage_via_mcp(runner, kind="delete-proposal")["result"]["sid"]
    assert (runner.root / "src/a.py").exists()
    assert _roundtrip_on(runner, {"kind": "apply-staged", "sid": sid}, "orchestrator")["ok"]
    assert not (runner.root / "src/a.py").exists()


def test_approvals_are_the_orchestrators_alone(runner):
    sid = _stage_via_mcp(runner)["result"]["sid"]
    for kind in ("apply-staged", "reject-staged", "list-staged", "show-staged"):
        result = _roundtrip_on(runner, {"kind": kind, "sid": sid, "via_agent": "producer"}, "mcp")
        assert not result["ok"] and "MCP channel can't queue" in result["error"]
    assert (runner.root / "src/a.py").read_text() == "x = 1\n"


def test_stage_and_direct_come_only_from_the_mcp_channel(runner):
    nonce = P.issue_dispatch(runner.root, "producer")
    art = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py", "rationale": "r", "content": "y\n",
           "base_sha256": hashlib.sha256(b"x = 1\n").hexdigest()}
    for kind in ("stage-proposal", "apply-direct"):
        result = _roundtrip_on(runner, {"kind": kind, "artifact": art}, "orchestrator")
        assert not result["ok"] and "agentteams_runner server" in result["error"]


def test_direct_needs_a_verified_grant_and_never_deletes(runner):
    nonce = P.issue_dispatch(runner.root, "producer")
    base = hashlib.sha256(b"x = 1\n").hexdigest()
    art = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py", "rationale": "r", "content": "y\n",
           "base_sha256": base}
    refused = _roundtrip_on(runner, {"kind": "apply-direct", "artifact": art, "via_agent": "producer"}, "mcp")
    assert not refused["ok"] and "no verified direct-write grant" in refused["error"]
    runner.policy.direct_agents = frozenset({"producer"})
    runner.direct_grants = {"producer": {"grant_id": "g1", "expires": "2999-01-01T00:00:00+00:00", "max_writes": 5}}
    done = _roundtrip_on(runner, {"kind": "apply-direct", "artifact": art, "via_agent": "producer"}, "mcp")
    assert done["ok"] and (runner.root / "src/a.py").read_text() == "y\n"
    delete = {"kind": "delete-proposal", "dispatch": nonce, "path": "src/a.py", "rationale": "r",
              "base_sha256": hashlib.sha256(b"y\n").hexdigest()}
    refused = _roundtrip_on(runner, {"kind": "apply-direct", "artifact": delete, "via_agent": "producer"}, "mcp")
    assert not refused["ok"] and "never applied directly" in refused["error"]
    assert (runner.root / "src/a.py").exists()


def test_staging_spends_the_use_and_approval_does_not(runner, monkeypatch):
    nonce = P.issue_dispatch(runner.root, "producer", max_uses=1)
    art = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py", "rationale": "r", "content": "y\n",
           "base_sha256": hashlib.sha256(b"x = 1\n").hexdigest()}
    sid = _roundtrip_on(runner, {"kind": "stage-proposal", "artifact": art, "via_agent": "producer"}, "mcp")
    assert sid["ok"]
    again = _roundtrip_on(runner, {"kind": "stage-proposal", "artifact": art, "via_agent": "producer"}, "mcp")
    assert not again["ok"]  # the one use went to staging
    assert _roundtrip_on(runner, {"kind": "apply-staged", "sid": sid["result"]["sid"]}, "orchestrator")["ok"]


def test_staged_cli_queues_the_right_requests(monkeypatch, capsys):
    from agentteams.cli import proposal_commands as PC
    from agentteams.cli.app import _build_parser

    sent = []

    def fake(args, label, request):
        sent.append(request)
        return 0, ({"staged": []} if request["kind"] == "list-staged" else {"path": "src/a.py", "agent": "producer"})

    monkeypatch.setattr(PC, "_queued", fake)
    sid = "a" * 32
    for argv in (["--list-staged"], ["--show-staged", sid], ["--apply-staged", sid],
                 ["--reject-staged", sid, "--reject-reason", "nope"]):
        assert PC.run_staged(_build_parser().parse_args(argv)) == 0
    assert [r["kind"] for r in sent] == ["list-staged", "show-staged", "apply-staged", "reject-staged"]
    assert sent[3]["reason"] == "nope" and all(r.get("sid") in (None, sid) for r in sent)


@pytest.mark.parametrize("tamper", ["content", "agent", "path", "unsigned", "expiry"])
def test_a_tampered_staged_record_is_refused(runner, tamper):
    """R3 review: the record is signed with the runner's key; any swap between staging and approval is refused."""
    from agentteams import proposal_staging as S

    sid = _stage_via_mcp(runner)["result"]["sid"]
    path = runner.root / S.STAGED_REL / f"{sid}.json"
    record = json.loads(path.read_text())
    if tamper == "content":
        record["artifact"]["content"] = "evil\n"
    elif tamper == "agent":
        record["agent"] = "reviewer"
    elif tamper == "path":
        record["path"] = record["artifact"]["path"] = "src/b.py"
    elif tamper == "unsigned":
        record.pop("mac")
    else:
        record["expires"] = "not-a-date"
    path.write_text(json.dumps(record))
    result = _roundtrip_on(runner, {"kind": "apply-staged", "sid": sid}, "orchestrator")
    assert not result["ok"], tamper
    assert (runner.root / "src/a.py").read_text() == "x = 1\n" and not (runner.root / "src/b.py").exists()


def test_expired_records_are_purged_on_listing(runner):
    from agentteams import proposal_staging as S

    sid = _stage_via_mcp(runner)["result"]["sid"]
    path = runner.root / S.STAGED_REL / f"{sid}.json"
    record = json.loads(path.read_text())
    record["expires"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(record))
    assert _roundtrip_on(runner, {"kind": "list-staged"}, "orchestrator")["result"]["staged"] == []
    assert not path.exists() and "expire-staged" in (runner.root / P.LEDGER_REL).read_text()
