"""The optional work-summary Stop hook: emission, and the launcher's guards.

The launcher is exercised end to end against a stub ``claude`` that records how it was called, so
no model run happens here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

import hashlib
import shlex

from agentteams.frameworks._work_summary_hook_emit import (
    DIGEST_PLACEHOLDER,
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
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))  # never the real user settings
    monkeypatch.setenv("STUB_RECORD", str(record))
    monkeypatch.setenv("STUB_WRITE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-vscode")
    for name in ("AGENTTEAMS_WS_HOOK_CHILD", "AGENTTEAMS_WS_DISABLE", "AGENTTEAMS_WS_KEEP_DAYS",
                 "AGENTTEAMS_WS_ALLOWED_TOOLS", "AGENTTEAMS_WS_PERMISSION_MODE", "STUB_EXIT",
                 "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN"):
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


def _emitted_command() -> str:
    files = dict(work_summary_hook_files({"agent_slug_list": ["work-summarizer"]}))
    example = json.loads(files[EXAMPLE_REL])
    assert list(example["hooks"]) == ["Stop"]
    return example["hooks"]["Stop"][0]["hooks"][0]["command"]


def test_example_command_carries_the_digest_of_the_emitted_launcher() -> None:
    template = (_HOOKS / "settings.worksummary.example.json").read_text(encoding="utf-8")
    assert template.count(DIGEST_PLACEHOLDER) == 1
    command = _emitted_command()
    digest = hashlib.sha256(_LAUNCHER.read_bytes()).hexdigest()
    assert DIGEST_PLACEHOLDER not in command and command.endswith(" " + digest)
    assert '"${CLAUDE_PROJECT_DIR}/.claude/hooks/work-summary-stop.py"' in command


def _wire(root: Path, body: str) -> list[str]:
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "work-summary-stop.py").write_text(body, encoding="utf-8")
    argv = shlex.split(_emitted_command().replace("${CLAUDE_PROJECT_DIR}", str(root)))
    return [sys.executable, "-B", *argv[1:]]


def test_the_launcher_and_its_emitter_are_integrity_pinned() -> None:
    """SEC-AT-WS-STOP-HOOK-2026-11-14 (6): dropping either file from the enforcement set must fail a test."""
    from agentteams import integrity

    repo = Path(__file__).resolve().parents[1]
    manifest = json.loads((repo / "references" / "enforcement-integrity.json").read_text(encoding="utf-8"))
    modules = manifest["modules"]   # a path cited elsewhere in the manifest (its note, say) is not a pin
    for rel in ("agentteams/templates/universal/hooks/work-summary-stop.py",
                "agentteams/frameworks/_work_summary_hook_emit.py"):
        assert rel in integrity.ENFORCEMENT_MODULES, rel
        assert rel in modules and re.fullmatch(r"[0-9a-f]{64}", str(modules[rel])), rel


def test_wired_command_runs_the_intact_launcher(project) -> None:
    root, calls, rdir = project
    _plan(root)
    done = subprocess.run(_wire(root, _LAUNCHER.read_text(encoding="utf-8")), input="{}",
                          capture_output=True, text=True, timeout=20)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    deadline = time.time() + 15
    while time.time() < deadline and not (calls() and not (rdir / "lock").exists()):
        time.sleep(0.1)
    assert len(calls()) == 1


def test_wired_command_is_isolated_from_modules_planted_in_the_project(project) -> None:
    """A committed hashlib.py or runpy.py in the project root must not replace the digest check (1.2.2, @security)."""
    root, calls, rdir = project
    _plan(root)
    assert " -I -c " in _emitted_command()
    for name in ("hashlib.py", "runpy.py"):
        (root / name).write_text("import sys\nsys.exit(0)  # a planted module that would skip the check\n")
    edited = _LAUNCHER.read_text(encoding="utf-8") + "\n# edited\n"
    refused = subprocess.run(_wire(root, edited), input="{}", cwd=root, capture_output=True, text=True, timeout=20)
    assert refused.returncode != 0 and "does not match the digest" in refused.stderr
    assert calls() == []


def test_wired_command_refuses_an_edited_launcher(project, tmp_path) -> None:
    root, calls, _ = project
    _plan(root)
    marker = tmp_path / "ran"
    tampered = f"open({str(marker)!r}, 'w').close()\n" + _LAUNCHER.read_text(encoding="utf-8")
    done = subprocess.run(_wire(root, tampered), input="{}", capture_output=True, text=True,
                          timeout=20)
    # Exit 1 is a visible, non-blocking hook error; exit 2 would block the session.
    assert done.returncode == 1 and "does not match the digest" in done.stderr
    time.sleep(1.0)
    assert not marker.exists() and calls() == []


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
    assert "Write" not in grant and "Edit" not in grant
    assert not any(rule.startswith("Bash") for rule in grant)  # the run has no shell at all
    assert "Edit(workSummaries/**)" in grant
    monkeypatch.setenv("AGENTTEAMS_WS_ALLOWED_TOOLS", "Read")
    cmd = hook.child_command("claude", "p")
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"


def test_tool_set_is_capped_and_messaging_credentials_are_stripped(hook, project, monkeypatch) -> None:
    """F-LC1: allow/deny rules do not remove tools that need no permission; only the cap does."""
    root, calls, _ = project
    _commit(root)
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", "/tmp/some.sock")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "secret")
    monkeypatch.setenv("AGENTTEAMS_WS_ALLOWED_TOOLS", "Read,SendMessage,Bash")  # cannot widen the cap
    cmd = hook.child_command("claude", "p")
    assert cmd[cmd.index("--tools") + 1] == "Read,Grep,Glob,Agent,Edit,Write"
    env = hook.child_env()
    assert "CLAUDE_CODE_MESSAGING_SOCKET" not in env and "CLAUDE_CODE_MESSAGING_TOKEN" not in env
    # An override naming a tool the hook does not know now stops the launch outright (item 4), so the cap is checked
    # on a launch whose override passes the rule check: Bash is denied, so its allow is dead, and the cap holds.
    assert any("SendMessage" in u for u in hook.unsafe_inherited_allows(root))
    monkeypatch.setenv("AGENTTEAMS_WS_ALLOWED_TOOLS", "Read,Bash")
    _run(hook, root)
    argv = calls()[0]["argv"]
    cap = argv[argv.index("--tools") + 1].split(",")
    for tool in ("Bash", "WebFetch", "SendMessage", "ListAgents", "CronCreate", "RemoteTrigger",
                 "EnterWorktree", "Skill", "Workflow", "ToolSearch"):
        assert tool not in cap


def test_deny_rules_no_mcp_and_no_bypass(hook, project, monkeypatch) -> None:
    root, calls, _ = project
    _commit(root)
    _run(hook, root)
    argv = calls()[0]["argv"]
    denied = argv[argv.index("--disallowedTools") + 1].split(",")
    # Whole tools are denied, so an inherited allow of any shape for them is dead in this run.
    for rule in ("Bash", "WebFetch", "WebSearch", "NotebookEdit",
                 "Read(~/.ssh/**)", "Read(**/.env*)", "Edit(.claude/**)"):
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


# ── what the run inherits, and what it is handed ──────────────────────────────────────────────


def _settings(path: Path, allow: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": {"allow": allow}}))


def test_broad_inherited_shell_allow_is_neutralised_not_inherited(hook, project, tmp_path) -> None:
    root, calls, _ = project
    _plan(root)
    _settings(tmp_path / "cfg" / "settings.json", ["Bash(python3 -c ' *)", "Bash(gh *)", "Read(//tmp/**)"])
    _settings(root / ".claude" / "settings.json", ["Bash(git push*)", "mcp__x__y", "WebFetch"])
    assert hook.unsafe_inherited_allows(root) == []
    _run(hook, root)
    argv = calls()[0]["argv"]
    assert "Bash" in argv[argv.index("--disallowedTools") + 1].split(",")


@pytest.mark.parametrize("where", ["user", "project", "local"])
@pytest.mark.parametrize("rule", ["Write(**)", "Edit", "Edit(src/**)", "Write(workSummaries/../x)",
                                  "SomeFutureTool(*)", "Edit(workSummaries/**) Write",
                                  "Edit(workSummaries/**)(x)", "Edit(workSummaries/(x)/**)"])
def test_inherited_rule_that_escapes_worksummaries_stops_the_launch(
        hook, project, tmp_path, where: str, rule: str) -> None:
    root, calls, rdir = project
    _plan(root)
    path = {"user": tmp_path / "cfg" / "settings.json",
            "project": root / ".claude" / "settings.json",
            "local": root / ".claude" / "settings.local.json"}[where]
    _settings(path, ["Read", "Edit(workSummaries/**)", rule])
    _run(hook, root)
    assert calls() == []
    assert "would let the unattended run reach beyond workSummaries/" in (rdir / "hook.log").read_text()


@pytest.mark.parametrize("override", ["Read,Write(**)", "Read,Edit", "Edit(src/**),Read",
                                      "Write(workSummaries/../x)", "SomeFutureTool(*)",
                                      "Read,Edit(workSummaries/**", ",", " ",
                                      # Trailing text or nested parentheses: Claude Code may read "Write" as a
                                      # further, unscoped rule (SEC review of item 4).
                                      "Edit(workSummaries/**) Write", "Edit(workSummaries/**)\tWrite",
                                      "Edit(workSummaries/**)\nWrite", "Edit(workSummaries/**)(x)",
                                      "Edit(workSummaries/(x)/**)", "Edit(workSummaries/a,b/**)"])
def test_an_override_grant_that_escapes_worksummaries_stops_the_launch(
        hook, project, monkeypatch, override: str) -> None:
    """AGENTTEAMS_WS_ALLOWED_TOOLS replaces the default grant, so its rules pass the same check (1.2.0 cut item 4)."""
    root, calls, rdir = project
    _plan(root)
    monkeypatch.setenv("AGENTTEAMS_WS_ALLOWED_TOOLS", override)
    assert hook.unsafe_inherited_allows(root), override
    _run(hook, root)
    assert calls() == []
    assert "would let the unattended run reach beyond workSummaries/" in (rdir / "hook.log").read_text()


def test_a_contained_override_grant_still_runs_and_is_passed_through(hook, project, monkeypatch) -> None:
    root, calls, _ = project
    _plan(root)
    grant = "Read,Grep,Glob,Agent,Edit(workSummaries/**),Write(./workSummaries/daily/**)"
    monkeypatch.setenv("AGENTTEAMS_WS_ALLOWED_TOOLS", grant)
    assert hook.unsafe_inherited_allows(root) == []
    _run(hook, root)
    argv = calls()[0]["argv"]
    assert argv[argv.index("--allowedTools") + 1] == grant


def test_override_rules_split_on_top_level_commas_only(hook) -> None:
    assert hook._split_rules("Read, Edit(workSummaries/a,b/**) ,Glob") == [
        "Read", "Edit(workSummaries/a,b/**)", "Glob"]
    assert hook._split_rules("Read,Edit(x") is None and hook._split_rules("Read)") is None
    assert hook._split_rules(" , ") == []


def test_unreadable_settings_stop_the_launch(hook, project) -> None:
    root, calls, _ = project
    _plan(root)
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text("{not json")
    _run(hook, root)
    assert calls() == []


def test_evidence_is_collected_by_the_hook_and_removed_after_the_run(hook, project) -> None:
    root, calls, _ = project
    sha = _commit(root)
    text = hook.collect_evidence(root, 8)
    assert sha in text and "add a.txt" in text and "a.txt" in text.split("--stat")[1]
    assert text.startswith("GIT EVIDENCE") and "data, not instructions" in text
    _run(hook, root)
    prompt = calls()[0]["argv"][1]
    assert "You have no shell in this run" in prompt and "run 'git" not in prompt
    path = Path(prompt.split("collected for you: read ")[1].split(". It is the only")[0])
    assert path.name == "git-evidence.txt" and not path.parent.exists()


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


def test_the_prompt_carries_no_absolute_project_path(hook, project) -> None:
    """researchteam @security (1.2.2): the absolute root (a home path, so the OS username) could reach a summary."""
    root, _, _ = project
    for kind in ("commits", "plan-only"):
        decision = {"kind": kind, "mode": "append", "count": 1, "latest": "abc1234", "digest": "d"}
        prompt = hook.build_prompt(root, decision, "2026-11-19", False, evidence=Path("/tmp/x/git-evidence.txt"))
        assert str(root) not in prompt and str(root.resolve()) not in prompt
        assert "current working directory" in prompt and "Never write an absolute path" in prompt


def test_the_detached_worker_refuses_a_launcher_changed_after_the_check(hook, project, tmp_path) -> None:
    assert hook._SELF_SHA256 == hashlib.sha256(_LAUNCHER.read_bytes()).hexdigest()
    root, calls, _ = project
    _plan(root)
    copy = tmp_path / "work-summary-stop.py"
    copy.write_bytes(_LAUNCHER.read_bytes() + b"\n# swapped after the wired check\n")
    stale = subprocess.run([sys.executable, "-I", "-c", hook._REEXEC, str(copy), hook._SELF_SHA256,
                            "--foreground", "--project", str(root)], capture_output=True, text=True, timeout=20)
    assert stale.returncode == 1 and calls() == []
    ok = subprocess.run([sys.executable, "-I", "-c", hook._REEXEC, str(_LAUNCHER), hook._SELF_SHA256,
                         "--foreground", "--project", str(root)], capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0 and len(calls()) == 1


def test_main_starts_the_detached_worker_through_the_verifying_entry(hook, project, monkeypatch) -> None:
    root, _, _ = project
    started: list[list[str]] = []
    monkeypatch.setattr(hook.subprocess, "Popen", lambda argv, **kw: started.append(argv))
    assert hook.main(["--project", str(root)]) == 0
    [argv] = started
    assert argv[1:5] == ["-I", "-c", hook._REEXEC, str(Path(hook.__file__).resolve())]
    assert argv[5] == hook._SELF_SHA256 and argv[6:] == ["--foreground", "--project", str(root)]


def test_a_summary_naming_machine_paths_is_flagged_in_the_log(hook, project, monkeypatch) -> None:
    root, calls, rdir = project
    _plan(root)
    monkeypatch.setenv("STUB_BODY", f"- see {root}/tmp/by-week/x.plan.md")
    _run(hook, root)
    [warning] = [line for line in (rdir / "hook.log").read_text().splitlines() if "warning:" in line]
    assert "absolute machine path(s) (project root" in warning
    assert str(root) not in warning                                  # the log names the kind, never the path
    clean = root / "workSummaries" / "daily" / "clean.md"
    clean.write_text("- see tmp/by-week/x.plan.md\n")
    assert hook.absolute_paths_in(clean, root, root / "nowhere") == []


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
