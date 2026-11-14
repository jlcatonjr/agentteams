"""The optional work-summary Stop hook: emission, and the launcher's guards.

The launcher is exercised end to end against a stub ``claude`` that records how it was called, so
no model run happens here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentteams.frameworks._work_summary_hook_emit import (
    EXAMPLE_REL,
    LAUNCHER_REL,
    work_summary_hook_files,
)
from agentteams.frameworks.claude import ClaudeAdapter

_REPO = Path(__file__).resolve().parents[1]
_HOOKS = _REPO / "agentteams" / "templates" / "universal" / "hooks"
_LAUNCHER = _HOOKS / "work-summary-stop.py"

_STUB = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
rec = Path(os.environ["STUB_RECORD"])
calls = json.loads(rec.read_text()) if rec.exists() else []
calls.append({"argv": sys.argv[1:], "marker": os.environ.get("AGENTTEAMS_WS_HOOK_CHILD"),
              "entrypoint": os.environ.get("CLAUDE_CODE_ENTRYPOINT"), "stdin": sys.stdin.read()})
rec.write_text(json.dumps(calls))
if os.environ.get("STUB_WRITE") == "1":
    import datetime
    out = Path("workSummaries/daily") / (datetime.date.today().isoformat() + ".md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# day\\n\\n## Plans Implemented\\n" + os.environ.get("STUB_BODY", "") + "\\n")
print('{"type":"result"}')
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("work_summary_stop", _LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode, prior = True, sys.dont_write_bytecode
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = prior
    return module


def _git(root: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    stub = tmp_path / "claude-stub"
    stub.write_text(_STUB)
    stub.chmod(0o755)
    record = tmp_path / "calls.json"
    monkeypatch.setenv("AGENTTEAMS_WS_CLAUDE_BIN", str(stub))
    monkeypatch.setenv("AGENTTEAMS_WS_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("STUB_RECORD", str(record))
    monkeypatch.setenv("STUB_WRITE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-vscode")
    for name in ("AGENTTEAMS_WS_HOOK_CHILD", "AGENTTEAMS_WS_DISABLE", "AGENTTEAMS_WS_KEEP_DAYS",
                 "AGENTTEAMS_WS_ALLOWED_TOOLS", "AGENTTEAMS_WS_PERMISSION_MODE", "STUB_EXIT"):
        monkeypatch.delenv(name, raising=False)

    def calls() -> list[dict]:
        return json.loads(record.read_text()) if record.exists() else []

    return root, calls, tmp_path / "run"


def _plan(root: Path, text: str = "plan") -> Path:
    path = root / "tmp" / "by-week" / "2026-W46" / "x.plan.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _commit(root: Path, name: str = "a.txt") -> str:
    (root / name).write_text(name)
    _git(root, "add", name)
    _git(root, "commit", "-q", "-m", f"add {name}")
    return _git(root, "log", "-1", "--format=%h")


def _run(hook, root: Path) -> int:
    return hook.main(["--foreground", "--project", str(root)])


# ── emission ──────────────────────────────────────────────────────────────────────────────────


def test_emitted_only_for_a_team_with_a_work_summarizer() -> None:
    assert work_summary_hook_files({"agent_slug_list": ["orchestrator"]}) == []
    files = dict(work_summary_hook_files({"agent_slug_list": ["orchestrator", "work-summarizer"]}))
    assert set(files) == {LAUNCHER_REL, EXAMPLE_REL}
    assert files[LAUNCHER_REL] == _LAUNCHER.read_text(encoding="utf-8")


def test_claude_adapter_ships_both_files_and_leaves_the_gate_example_alone() -> None:
    base = {"framework": "claude", "privilege_profile": "cooperative"}
    without = dict(ClaudeAdapter().extra_output_files(dict(base, agent_slug_list=["orchestrator"])))
    with_ = dict(ClaudeAdapter().extra_output_files(
        dict(base, agent_slug_list=["orchestrator", "work-summarizer"])))
    assert LAUNCHER_REL not in without and EXAMPLE_REL not in without
    assert LAUNCHER_REL in with_ and EXAMPLE_REL in with_
    # Wiring the gate must never switch the summary hook on: the gate example carries no Stop entry.
    gate_example = json.loads(with_["../settings.hooks.example.json"])
    assert "Stop" not in gate_example.get("hooks", {})
    assert with_["../settings.hooks.example.json"] == without["../settings.hooks.example.json"]


def test_example_wires_the_launcher_through_python_on_stop_only() -> None:
    example = json.loads((_HOOKS / "settings.worksummary.example.json").read_text(encoding="utf-8"))
    assert list(example["hooks"]) == ["Stop"]
    command = example["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert command == 'python3 "${CLAUDE_PROJECT_DIR}/.claude/hooks/work-summary-stop.py"'


def test_launcher_is_stdlib_only() -> None:
    import ast

    stdlib = sys.stdlib_module_names
    tree = ast.parse(_LAUNCHER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        for name in names:
            assert name.split(".")[0] in stdlib | {"__future__"}, name


# ── the invocation ────────────────────────────────────────────────────────────────────────────


def test_child_is_unpersisted_marked_and_cut_off_from_stdin_and_the_editor_entrypoint(
        hook, project, monkeypatch: pytest.MonkeyPatch) -> None:
    root, calls, rdir = project
    _commit(root)
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"last_assistant_message": "SECRET-REPORT"}'))
    assert _run(hook, root) == 0
    (call,) = calls()
    argv = call["argv"]
    assert argv[0] == "--print" and "--no-session-persistence" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert call["marker"] == "1" and call["entrypoint"] is None and call["stdin"] == ""
    assert "SECRET-REPORT" not in " ".join(argv)
    archived = list((rdir / "runs").rglob("*.jsonl"))
    assert len(archived) == 1 and '"result"' in archived[0].read_text()


def test_default_grant_is_narrow_and_overridable(hook, project, monkeypatch) -> None:
    root, calls, _ = project
    _commit(root)
    _run(hook, root)
    grant = calls()[0]["argv"][calls()[0]["argv"].index("--allowedTools") + 1].split(",")
    assert "Bash" not in grant and "Write" not in grant and "Edit" not in grant
    assert "Edit(workSummaries/**)" in grant and "Bash(git log:*)" in grant
    assert not any(rule.startswith("Bash(git diff") for rule in grant)
    monkeypatch.setenv("AGENTTEAMS_WS_ALLOWED_TOOLS", "Read")
    cmd = hook.child_command("claude", "p")
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"


def test_deny_rules_no_mcp_and_no_bypass(hook, project, monkeypatch) -> None:
    root, calls, _ = project
    _commit(root)
    _run(hook, root)
    argv = calls()[0]["argv"]
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    # The options that turn a read-only git command into a write, an outside read or a launch.
    for rule in ("Bash(*--output*)", "Bash(*--no-index*)", "Bash(*--ext-diff*)", "Bash(*>*)",
                 "WebFetch", "Read(~/.ssh/**)", "Read(**/.env*)", "Edit(.claude/**)"):
        assert rule in denied
    assert "--strict-mcp-config" in argv and "--mcp-config" not in argv
    # No setting loosens the permission mode.
    for value in ("bypassPermissions", "acceptEdits", ""):
        monkeypatch.setenv("AGENTTEAMS_WS_PERMISSION_MODE", value)
        cmd = hook.child_command("claude", "p")
        assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"


def test_hook_child_and_disable_switch_never_launch(hook, project, monkeypatch) -> None:
    root, calls, _ = project
    _commit(root)
    monkeypatch.setenv("AGENTTEAMS_WS_HOOK_CHILD", "1")
    assert _run(hook, root) == 0 and calls() == []
    monkeypatch.delenv("AGENTTEAMS_WS_HOOK_CHILD")
    monkeypatch.setenv("AGENTTEAMS_WS_DISABLE", "1")
    assert _run(hook, root) == 0 and calls() == []


# ── freshness gates ───────────────────────────────────────────────────────────────────────────


def test_nothing_today_launches_nothing(hook, project) -> None:
    root, calls, _ = project
    _run(hook, root)
    assert calls() == []


def test_plan_only_day_runs_once_then_skips_until_a_plan_changes(hook, project) -> None:
    root, calls, _ = project
    plan = _plan(root)
    for _ in range(3):
        _run(hook, root)
    assert len(calls()) == 1
    assert "Do NOT run Workflow D" in calls()[0]["argv"][1]
    plan.write_text("plan, revised and longer")
    _run(hook, root)
    assert len(calls()) == 2
    # The audit chain is requested once a day; the second run is told not to repeat it.
    assert "Run @adversarial and @conflict-auditor" in calls()[0]["argv"][1]
    assert "Do NOT re-run them" in calls()[1]["argv"][1]


def test_commit_after_a_plan_only_run_is_still_caught(hook, project) -> None:
    root, calls, _ = project
    _plan(root)
    _run(hook, root)
    sha = _commit(root)
    _run(hook, root)
    assert len(calls()) == 2
    assert "this hook counted 1 commit(s) today" in calls()[1]["argv"][1]
    _run(hook, root)  # the stub never names the hash; the recorded state still marks it covered
    assert len(calls()) == 2
    _commit(root, "b.txt")
    _run(hook, root)
    assert len(calls()) == 3 and sha


def test_summary_that_already_names_the_latest_commit_is_skipped(hook, project) -> None:
    root, calls, _ = project
    sha = _commit(root)
    daily = root / "workSummaries" / "daily" / f"{time.strftime('%Y-%m-%d')}.md"
    daily.parent.mkdir(parents=True)
    daily.write_text(f"## Plans Implemented\n- {sha} did things\n")
    _run(hook, root)
    assert calls() == []


def test_failed_run_backs_off_then_retries(hook, project, monkeypatch) -> None:
    root, calls, rdir = project
    _plan(root)
    monkeypatch.setenv("STUB_EXIT", "3")
    _run(hook, root)
    _run(hook, root)
    assert len(calls()) == 1  # second Stop is inside the back-off
    assert "retry after the back-off" in (rdir / "hook.log").read_text()
    monkeypatch.setenv("AGENTTEAMS_WS_RETRY_S", "0")
    monkeypatch.setenv("STUB_EXIT", "0")
    _run(hook, root)
    _run(hook, root)
    assert len(calls()) == 2  # retried once it may, then current


def test_run_that_leaves_no_summary_is_not_recorded_as_current(hook, project, monkeypatch) -> None:
    root, calls, _ = project
    _plan(root)
    monkeypatch.setenv("STUB_WRITE", "0")
    monkeypatch.setenv("AGENTTEAMS_WS_RETRY_S", "0")
    _run(hook, root)
    _run(hook, root)
    assert len(calls()) == 2


# ── lock, archive, extras ─────────────────────────────────────────────────────────────────────


def test_fresh_lock_blocks_and_stale_lock_is_cleared(hook, project) -> None:
    root, calls, rdir = project
    _plan(root)
    rdir.mkdir(parents=True)
    (rdir / "lock").write_text("1")
    _run(hook, root)
    assert calls() == []
    old = time.time() - 2000
    os.utime(rdir / "lock", (old, old))
    _run(hook, root)
    assert len(calls()) == 1 and not (rdir / "lock").exists()


def test_fresh_lock_of_a_dead_worker_is_taken_over(hook, project) -> None:
    root, calls, rdir = project
    _plan(root)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    rdir.mkdir(parents=True)
    (rdir / "lock").write_text(str(gone.pid))
    _run(hook, root)
    assert len(calls()) == 1 and not list(rdir.glob("lock*"))


def test_takeover_yields_to_a_claim_and_to_a_lock_that_came_back_to_life(hook, project) -> None:
    _, _, rdir = project
    rdir.mkdir(parents=True)
    old = time.time() - 5000
    (rdir / "lock").write_text("1")
    os.utime(rdir / "lock", (old, old))
    (rdir / "lock.claim").write_text("1")  # another worker is mid-takeover
    assert hook._acquire_lock(rdir, 900) is False and (rdir / "lock").read_text() == "1"
    (rdir / "lock.claim").unlink()
    assert hook._acquire_lock(rdir, 900) is True
    assert (rdir / "lock").read_text() == str(os.getpid()) and not (rdir / "lock.claim").exists()
    # Holding it now, a second attempt (same or another worker) cannot take it.
    assert hook._acquire_lock(rdir, 900) is False
    hook._release_lock(rdir)
    assert not (rdir / "lock").exists()


def test_another_workers_lock_is_never_released(hook, project) -> None:
    _, _, rdir = project
    rdir.mkdir(parents=True)
    (rdir / "lock").write_text("1")
    hook._release_lock(rdir)
    assert (rdir / "lock").exists()


def test_run_directory_files_are_owner_only(hook, project) -> None:
    root, _, rdir = project
    _plan(root)
    prior = os.umask(0o022)
    try:
        _run(hook, root)
    finally:
        os.umask(prior)
    (archive,) = (rdir / "runs").rglob("*.jsonl")
    for path in (archive, rdir / "hook.log", rdir / "state.json"):
        assert path.stat().st_mode & 0o077 == 0, path


def test_archive_retention(hook, project, monkeypatch) -> None:
    root, calls, rdir = project
    stale = rdir / "runs" / "2020-01-01"
    stale.mkdir(parents=True)
    (stale / "000000.jsonl").write_text("{}")
    _plan(root)
    _run(hook, root)
    assert not stale.exists() and len(list((rdir / "runs").rglob("*.jsonl"))) == 1
    monkeypatch.setenv("AGENTTEAMS_WS_KEEP_DAYS", "0")
    _plan(root, "changed plan text")
    _run(hook, root)
    assert len(calls()) == 2 and len(list((rdir / "runs").rglob("*.jsonl"))) == 1


def test_operator_extras_come_from_the_run_dir_never_from_the_repository(hook, project) -> None:
    root, calls, rdir = project
    _plan(root)
    # A file a branch or pull request could plant must not reach the prompt.
    (root / ".claude").mkdir()
    (root / ".claude" / "work-summary-stop.extra.md").write_text("PLANTED")
    (root / "extra.md").write_text("PLANTED")
    rdir.mkdir(parents=True)
    (rdir / "extra.md").write_text("FOLD-NOTE " + "x" * 9000)
    _run(hook, root)
    prompt = calls()[0]["argv"][1]
    assert "FOLD-NOTE" in prompt and "PLANTED" not in prompt and len(prompt) < 8000


def test_default_run_dir_is_outside_the_project(hook, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AGENTTEAMS_WS_RUN_DIR", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    root = tmp_path / "proj"
    rdir = hook.run_dir(root)
    assert rdir.parent == tmp_path / "cfg" / "logs" / "agentteams-work-summary"
    assert rdir.name.startswith("proj-") and root not in rdir.parents


def test_detached_entry_returns_at_once_and_prints_nothing(project) -> None:
    root, calls, rdir = project
    _plan(root)
    done = subprocess.run([sys.executable, "-B", str(_LAUNCHER), "--project", str(root)],
                          input='{"hook_event_name": "Stop"}', capture_output=True, text=True,
                          timeout=20)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    deadline = time.time() + 15
    while time.time() < deadline and not (calls() and not (rdir / "lock").exists()):
        time.sleep(0.1)
    assert len(calls()) == 1
