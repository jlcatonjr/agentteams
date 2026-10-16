"""test_gate_decision_log.py — the constitutional gate's opt-in per-case decision log.

The injection evaluation codes a block only from the blocking layer's own record, and a pass only
from positive evidence that the layer checked the case. These tests pin what that record may and
may not contain: one line per invocation, no written content or free-text reason, a hash chain,
and no effect on the decision when the log cannot be written.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "agentteams" / "templates" / "universal" / "hooks" / "constitutional-gate.py"
SENTINEL_USER = "zqsentineluser"


def _run(payload: object, env_extra: dict[str, str] | None = None, hook: Path = TEMPLATE,
         ) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTTEAMS_GATE_LOG")}
    env.update(env_extra or {})
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(hook)], input=raw, capture_output=True, text=True,
                          cwd=str(REPO), env=env, timeout=30, check=False)


def _lines(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def _bash(command: str, tool_use_id: str = "toolu_1") -> dict:
    return {"session_id": "s1", "tool_use_id": tool_use_id, "tool_name": "Bash",
            "tool_input": {"command": command}}


def test_logging_never_changes_the_output_and_unset_logs_nothing(tmp_path: Path) -> None:
    payload = _bash("rm -rf build")
    plain = _run(payload)
    logged = _run(payload, {"AGENTTEAMS_GATE_LOG": str(tmp_path / "gate.jsonl")})
    assert plain.stdout == logged.stdout and plain.returncode == logged.returncode
    assert list(tmp_path.iterdir()) == [tmp_path / "gate.jsonl"]
    assert _run(payload, {}).stdout == plain.stdout


@pytest.mark.parametrize(
    ("payload", "decision", "reason_id", "checked"),
    [
        (_bash("ls -la"), "pass", None, "bash-triggers"),
        (_bash("rm -rf build"), "ask", "bash-trigger", "bash-triggers"),
        ({"tool_use_id": "t", "tool_name": "Read", "tool_input": {"file_path": "/x"}}, "pass", None, "none"),
        ({"tool_use_id": "t", "tool_name": "Write", "tool_input": {"file_path": "/r/.git/config", "content": ""}},
         "ask", "git-config", "git-config"),
    ],
)
def test_one_record_per_invocation(tmp_path: Path, payload: dict, decision: str, reason_id: str | None,
                                   checked: str) -> None:
    log = tmp_path / "gate.jsonl"
    proc = _run(payload, {"AGENTTEAMS_GATE_LOG": str(log)})
    assert proc.returncode == 0
    (record,) = _lines(log)
    assert (record["decision"], record["reason_id"], record["checked"]) == (decision, reason_id, checked)
    assert record["tool_use_id"] == payload["tool_use_id"]
    assert record["gate_sha256"] == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()


def test_deny_record_carries_categories_but_never_content(tmp_path: Path) -> None:
    """The scanner's message embeds the matched text (a username path); the log must not."""
    log = tmp_path / "gate.jsonl"
    content = f"path: /Users/{SENTINEL_USER}/notes.txt\n"
    proc = _run({"tool_use_id": "t", "tool_name": "Write", "tool_input": {"file_path": "/tmp/a.md", "content": content}},
                {"AGENTTEAMS_GATE_LOG": str(log)})
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    text = log.read_text()
    assert SENTINEL_USER not in text and "notes.txt" not in text
    (record,) = _lines(log)
    assert record["decision"] == "deny" and record["reason_id"] == "scan-high" and record["checked"] == "scan"
    assert record["categories"] and record["lines"] == [1]


def test_crash_record_never_carries_the_exception_text(tmp_path: Path) -> None:
    log = tmp_path / "gate.jsonl"
    proc = _run(f'{{"tool_name": "{SENTINEL_USER}"', {"AGENTTEAMS_GATE_LOG": str(log)})
    assert proc.returncode != 0  # fail-open default: the crash propagates and the harness allows
    assert SENTINEL_USER not in log.read_text()
    (record,) = _lines(log)
    assert (record["decision"], record["reason_id"], record["fail_closed"]) == ("crash-open", "crash", False)


def test_fail_closed_crash_logs_once_and_denies(tmp_path: Path) -> None:
    hook = tmp_path / "hooks" / "constitutional-gate.py"
    hook.parent.mkdir()
    hook.write_text(TEMPLATE.read_text().replace("_FAIL_CLOSED_ON_ERROR = False", "_FAIL_CLOSED_ON_ERROR = True", 1))
    log = tmp_path / "gate.jsonl"
    proc = _run("not json", {"AGENTTEAMS_GATE_LOG": str(log)}, hook=hook)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    (record,) = _lines(log)
    assert (record["decision"], record["fail_closed"]) == ("crash-deny", True)


@pytest.mark.parametrize("profile", ["fail-open", "fail-closed"])
def test_symlink_loop_in_the_log_path_never_changes_the_decision(tmp_path: Path, profile: str) -> None:
    """resolve() raises RuntimeError on a loop before py3.13; an escape after stdout would exit 1 = allow."""
    (tmp_path / "a").symlink_to(tmp_path / "b")
    (tmp_path / "b").symlink_to(tmp_path / "a")
    hook = TEMPLATE
    if profile == "fail-closed":
        hook = tmp_path / "hooks" / "constitutional-gate.py"
        hook.parent.mkdir()
        hook.write_text(TEMPLATE.read_text().replace("_FAIL_CLOSED_ON_ERROR = False", "_FAIL_CLOSED_ON_ERROR = True", 1))
    payload = _bash("rm -rf build")
    proc = _run(payload, {"AGENTTEAMS_GATE_LOG": str(tmp_path / "a" / "gate.jsonl")}, hook=hook)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


@pytest.mark.parametrize("where", ["relative", "in-project", "in-worktree", "symlink"])
def test_unsafe_log_path_is_refused_without_changing_the_decision(tmp_path: Path, where: str) -> None:
    if where == "relative":
        target = "gate.jsonl"
    elif where == "in-project":
        target = str(REPO / "gate-should-not-exist.jsonl")
    elif where == "in-worktree":
        (tmp_path / "wt" / ".git").mkdir(parents=True)
        target = str(tmp_path / "wt" / "logs" / "gate.jsonl")
        (tmp_path / "wt" / "logs").mkdir()
    else:
        (tmp_path / "real.jsonl").write_text("")
        (tmp_path / "gate.jsonl").symlink_to(tmp_path / "real.jsonl")
        target = str(tmp_path / "gate.jsonl")
    payload = _bash("rm -rf build")
    proc = _run(payload, {"AGENTTEAMS_GATE_LOG": target})
    assert proc.stdout == _run(payload).stdout and proc.returncode == 0
    assert "decision log not written" in proc.stderr
    assert not (REPO / "gate-should-not-exist.jsonl").exists()
    if where == "symlink":
        assert (tmp_path / "real.jsonl").read_text() == ""


def test_chain_links_every_line_even_under_concurrent_writers(tmp_path: Path) -> None:
    log = tmp_path / "gate.jsonl"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTTEAMS_GATE_LOG")}
    env["AGENTTEAMS_GATE_LOG"] = str(log)
    procs = [subprocess.Popen([sys.executable, str(TEMPLATE)], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, cwd=str(REPO), env=env, text=True)
             for _ in range(8)]
    for i, proc in enumerate(procs):
        proc.communicate(json.dumps(_bash("ls", f"toolu_{i}")), timeout=30)
    raw = log.read_bytes().splitlines()
    assert len(raw) == 8
    assert json.loads(raw[0])["prev_sha"] is None
    for previous, line in zip(raw, raw[1:]):
        assert json.loads(line)["prev_sha"] == hashlib.sha256(previous).hexdigest()
    assert {json.loads(line)["tool_use_id"] for line in raw} == {f"toolu_{i}" for i in range(8)}


def test_input_hmac_recomputes_from_the_canonical_tool_input(tmp_path: Path) -> None:
    log = tmp_path / "gate.jsonl"
    payload = _bash("ls -la")
    _run(payload, {"AGENTTEAMS_GATE_LOG": str(log), "AGENTTEAMS_GATE_LOG_KEY": "k3y"})
    canonical = json.dumps(payload["tool_input"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    expected = hmac.new(b"k3y", canonical.encode(), hashlib.sha256).hexdigest()
    assert _lines(log)[0]["input_hmac"] == expected
    _run(payload, {"AGENTTEAMS_GATE_LOG": str(log)})
    assert _lines(log)[1]["input_hmac"] is None


def test_the_profile_flip_sentinel_occurs_exactly_once() -> None:
    """agentteams flips the first occurrence at emit time; a second copy would be flipped instead."""
    assert TEMPLATE.read_text().count("_FAIL_CLOSED_ON_ERROR = False") == 1
