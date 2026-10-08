"""The agentteams_runner MCP server (R4): the only path by which a granted agent writes or executes under the switch.

Driven as a real subprocess over JSON-RPC, against a real runner serving in a background thread.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agentteams import proposal_policy, proposal_runner as R, proposals as P, runner_mcp as M

SERVER = Path(M.__file__).parent / "data" / "agentteams-runner-mcp.py"


# --- the pin and the launch ----------------------------------------------------------------------------------


def test_pinned_hash_matches_the_shipped_server():
    assert hashlib.sha256(SERVER.read_bytes()).hexdigest() == M.SHA256
    assert M.server_content() == SERVER.read_text(encoding="utf-8")


def test_tool_list_matches_the_policy_loader():
    assert M.TOOLS == proposal_policy.MCP_RUNNER_TOOLS


def test_launch_args_are_canonical_and_validated():
    assert M.launch_args("a", ["run_command", "write_file"]) == [
        "-I", "-S", M.PROTECTED_PATH, "--agent", "a", "--approval", "staged", "--tools", "write_file,run_command"]
    for bad in ([], ["shell"], ["*"]):
        with pytest.raises(ValueError):
            M.launch_args("a", bad)
    with pytest.raises(ValueError):
        M.launch_args("a", ["write_file"], approval="auto")


def test_the_server_never_writes_the_project_or_spawns():
    """Tripwire (not a proof): no process, socket or project-write primitives in the source."""
    source = SERVER.read_text(encoding="utf-8")
    for needle in ("subprocess", "socket", "os.system", "os.exec", "os.spawn", "shutil", "os.remove", "unlink",
                   "write_text", "open(path, \"w"):
        assert needle not in source, needle
    # its only writes: exclusive-create request temp files and empty acks, then a rename into the queue dir
    assert source.count("os.O_CREAT") == 2 and source.count("os.rename(") == 1


# --- end to end against a real runner ------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch):
    for name in P.KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(P, "_FILE_KEY", None)


@pytest.fixture
def served(tmp_path, monkeypatch):
    keydir = tmp_path / "keys"
    keydir.mkdir(mode=0o700)
    key = keydir / "proposal-ledger.key"
    key.write_text("k" * 64)
    key.chmod(0o600)
    monkeypatch.setattr(P, "KEY_DIR", str(keydir))
    monkeypatch.setenv(P.KEY_FILE_ENV, str(key))
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / ".env").write_text("SECRET=1\n")
    brief = {"project_name": "P", "project_goal": "Runner MCP tests.", "write_policy": "orchestrator-only",
             "agent_policies": {"producer": {"write_scopes": ["src/"]}},
             "mcp_grants": {"producer": {"tools": list(M.TOOLS)}}}
    (root / "brief.json").write_text(json.dumps(brief))
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    runner = R.Runner(root, root / "brief.json", P.load_policy(brief, brief_rel="brief.json"))
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            runner.serve_once()
            time.sleep(0.05)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    yield root, runner
    stop.set()
    thread.join(timeout=5)
    runner.close()


class Client:
    def __init__(self, root: Path, tools: list[str], agent: str = "producer", approval: str = "staged"):
        self.proc = subprocess.Popen([sys.executable, "-I", "-S", str(SERVER), "--root", str(root), "--agent", agent,
                                      "--approval", approval, "--tools", ",".join(tools), "--wait", "10"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.n = 0

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def call(self, tool: str, **args) -> tuple[bool, object]:
        res = self.rpc("tools/call", {"name": tool, "arguments": args})["result"]
        text = res["content"][0]["text"]
        if res.get("isError"):
            return False, text
        return True, json.loads(text)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def test_only_granted_tools_are_listed_or_callable(served):
    root, _ = served
    c = Client(root, ["read_file_hashed", "write_file"])
    try:
        names = [t["name"] for t in c.rpc("tools/list")["result"]["tools"]]
        assert names == ["write_file", "read_file_hashed"]
        ok, text = c.call("run_command", dispatch="x", argv=["true"], purpose="p")
        assert not ok and "not granted" in text
    finally:
        c.close()


def test_read_file_hashed_reads_inside_and_refuses_the_rest(served):
    root, _ = served
    c = Client(root, ["read_file_hashed"])
    try:
        ok, out = c.call("read_file_hashed", path="src/a.py")
        assert ok and out["content"] == "x = 1\n" and out["base_sha256"] == hashlib.sha256(b"x = 1\n").hexdigest()
        ok, out = c.call("read_file_hashed", path="src/new.py")
        assert ok and out == {"path": "src/new.py", "exists": False, "base_sha256": "absent"}
        for bad in (".env", ".git/config", ".agentteams/dispatches.jsonl", ".agentteams-queue/requests/x",
                    "../outside", "/etc/passwd"):
            ok, text = c.call("read_file_hashed", path=bad)
            assert not ok, bad
    finally:
        c.close()


def test_write_is_staged_then_applied_by_the_orchestrator(served):
    root, _ = served
    nonce = P.issue_dispatch(root, "producer")
    c = Client(root, ["read_file_hashed", "write_file"])
    try:
        _ok, read = c.call("read_file_hashed", path="src/a.py")
        ok, receipt = c.call("write_file", dispatch=nonce, path="src/a.py", content="x = 2\n",
                             base_sha256=read["base_sha256"], rationale="bump")
        assert ok and receipt["ok"] and receipt["result"]["staged"], receipt
        assert (root / "src/a.py").read_text() == "x = 1\n"
    finally:
        c.close()
    sid = receipt["result"]["sid"]
    result = R.wait_result(root, R.enqueue(root, {"kind": "apply-staged", "sid": sid}), timeout=10)
    assert result["ok"] and (root / "src/a.py").read_text() == "x = 2\n"


def test_delete_is_always_staged_even_for_a_direct_instance(served):
    root, _ = served
    nonce = P.issue_dispatch(root, "producer")
    c = Client(root, ["delete_file"], approval="direct")
    try:
        ok, receipt = c.call("delete_file", dispatch=nonce, path="src/a.py",
                             base_sha256=hashlib.sha256(b"x = 1\n").hexdigest(), rationale="gone")
        assert ok and receipt["ok"] and receipt["result"]["staged"], receipt
        assert (root / "src/a.py").exists()
    finally:
        c.close()


def test_without_a_verified_grant_writes_are_staged_whatever_the_instance_says(served):
    """@security C1: the runner is the single decision point; an instance launched 'direct' still stages."""
    root, _ = served
    nonce = P.issue_dispatch(root, "producer")
    c = Client(root, ["write_file"], approval="direct")
    try:
        ok, receipt = c.call("write_file", dispatch=nonce, path="src/a.py", content="y\n",
                             base_sha256=hashlib.sha256(b"x = 1\n").hexdigest(), rationale="r")
        assert ok and receipt["ok"] and receipt["result"]["staged"], receipt
        assert (root / "src/a.py").read_text() == "x = 1\n"
    finally:
        c.close()


def test_another_agents_instance_cannot_use_the_nonce(served):
    root, _ = served
    nonce = P.issue_dispatch(root, "producer")
    c = Client(root, ["write_file"], agent="reviewer")
    try:
        ok, receipt = c.call("write_file", dispatch=nonce, path="src/a.py", content="y\n",
                             base_sha256=hashlib.sha256(b"x = 1\n").hexdigest(), rationale="r")
        assert ok and not receipt["ok"] and receipt["error"] == P._CHANNEL_REFUSAL
    finally:
        c.close()


def test_run_command_goes_to_the_runner(served):
    root, _ = served
    nonce = P.issue_dispatch(root, "producer")
    c = Client(root, ["run_command"])
    try:
        ok, receipt = c.call("run_command", dispatch=nonce, argv=["true"], purpose="check")
        assert ok and not receipt["ok"] and "allowlist" in receipt["error"]  # the runner's own refusal
    finally:
        c.close()


def test_request_status_answers_only_for_own_ids(served):
    root, _ = served
    c = Client(root, ["request_status"])
    try:
        ok, text = c.call("request_status", request_id="a" * 32)
        assert not ok and "unknown request_id" in text
    finally:
        c.close()


def test_no_runner_means_a_clear_refusal(tmp_path):
    (tmp_path / ".agentteams-queue" / "mcp-requests").mkdir(parents=True)
    c = Client(tmp_path, ["write_file"])
    try:
        ok, text = c.call("write_file", dispatch="0" * 32, path="a", content="", base_sha256="absent",
                          rationale="r")
        assert not ok and "no runner" in text
    finally:
        c.close()


def test_runner_refuses_a_swapped_installed_server(tmp_path):
    installed = tmp_path / M.PROTECTED_PATH
    installed.parent.mkdir(parents=True)
    installed.write_text(SERVER.read_text(encoding="utf-8"))
    R.check_installed_runner_mcp(tmp_path)  # the shipped copy passes
    installed.write_text(SERVER.read_text(encoding="utf-8") + "\n# edited\n")
    with pytest.raises(R.RunnerError, match="doesn't match"):
        R.check_installed_runner_mcp(tmp_path)


def test_server_rejects_unknown_tools_at_launch():
    proc = subprocess.run([sys.executable, str(SERVER), "--root", ".", "--agent", "a", "--tools", "shell"],
                          capture_output=True, text=True)
    assert proc.returncode != 0 and re.search("tools must be", proc.stderr)


def test_in_project_links_to_denied_files_are_refused(served):
    """@security R4 condition 1: the deny list applies to the resolved path, not only the requested one."""
    root, _ = served
    (root / "notes.txt").symlink_to(root / ".env")
    (root / "q").symlink_to(root / ".agentteams-queue", target_is_directory=True)
    (root / "app.env").write_text("K=1\n")
    (root / "state.tfstate.backup").write_text("{}")
    c = Client(root, ["read_file_hashed"])
    try:
        for bad in ("notes.txt", "q/mcp-requests", "app.env", "state.tfstate.backup"):
            ok, _text = c.call("read_file_hashed", path=bad)
            assert not ok, bad
        ok, _out = c.call("read_file_hashed", path="src/a.py")
        assert ok
    finally:
        c.close()


def test_a_symlinked_queue_dir_is_refused(served, tmp_path):
    """@security R4 condition 3: a linked-in queue directory can't redirect a request file."""
    root, _ = served
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    queue = root / ".agentteams-queue" / "mcp-requests"
    for entry in queue.iterdir():
        entry.unlink()
    queue.rmdir()
    queue.symlink_to(elsewhere, target_is_directory=True)
    c = Client(root, ["write_file"])
    try:
        ok, text = c.call("write_file", dispatch="0" * 32, path="src/a.py", content="y\n", base_sha256="absent",
                          rationale="r")
        assert not ok and "not a plain directory" in text
        assert list(elsewhere.iterdir()) == []
    finally:
        c.close()
