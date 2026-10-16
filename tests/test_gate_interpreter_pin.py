"""test_gate_interpreter_pin.py — the constitutional gate runs under an interpreter that can import agentteams.

1.0 must-fix (mathAgents M6 pilot): the hook command is plain ``python3`` and the gate's write path imports
agentteams. When PATH's ``python3`` lacked it, every Write/Edit check crashed — a confined profile denied all,
and the cooperative default silently allowed all. The gate now re-executes under the interpreter pinned beside
it (``agentteams-python``, untracked: Rule S-8), and fails visibly when no interpreter works.

These subprocess the TEMPLATE hook, copied into a throwaway project's ``.claude/hooks/`` as an install would.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks.claude import GATE_PIN_NAME, verify_gate_interpreter

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "agentteams" / "templates" / "universal" / "hooks" / "constitutional-gate.py"
CREDENTIAL = {"tool_name": "Write",
              "tool_input": {"file_path": "cfg.py", "content": 'aws = "AKIA' + "IOSFODNN7EXAMPLE" + '"'}}

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell wrappers stand in for interpreters")


def _wrapper(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture()
def env(tmp_path: Path) -> dict:
    """A project with the template gate installed, and a PATH whose python3 cannot import agentteams."""
    project = tmp_path / "proj"
    hooks = project / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "constitutional-gate.py").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    # -S drops site-packages, so this python3 has no agentteams (installed or editable).
    fake = _wrapper(tmp_path / "fakebin" / "python3", f'exec "{sys.executable}" -S "$@"\n')
    environ = {k: v for k, v in os.environ.items()
               if k not in ("AGENTTEAMS_GATE_REEXEC", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME")}
    environ["CLAUDE_PROJECT_DIR"] = str(project)
    environ["PATH"] = f"{fake.parent}{os.pathsep}/usr/bin{os.pathsep}/bin"
    return {"project": project, "hooks": hooks, "fake": fake, "environ": environ, "tmp": tmp_path}


def _run(env: dict, payload: dict, **extra_env: str) -> tuple[int, dict | None, str]:
    proc = subprocess.run([str(env["fake"]), str(env["hooks"] / "constitutional-gate.py")],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=60,
                          cwd=str(env["project"]), env={**env["environ"], **extra_env})
    decision = json.loads(proc.stdout)["hookSpecificOutput"] if proc.stdout.strip() else None
    return proc.returncode, decision, proc.stderr


def _pin(env: dict, value: str) -> None:
    (env["hooks"] / GATE_PIN_NAME).write_text(value + "\n", encoding="utf-8")


def _fail_closed(env: dict) -> None:
    gate = env["hooks"] / "constitutional-gate.py"
    text = gate.read_text(encoding="utf-8")
    assert text.count("_FAIL_CLOSED_ON_ERROR = False") == 1, "the flip sentinel must occur exactly once"
    gate.write_text(text.replace("_FAIL_CLOSED_ON_ERROR = False", "_FAIL_CLOSED_ON_ERROR = True", 1), encoding="utf-8")


def test_the_fake_python3_really_lacks_agentteams(env: dict) -> None:
    proc = subprocess.run([str(env["fake"]), "-c", "import agentteams"], capture_output=True, cwd=str(env["hooks"]),
                          env=env["environ"], timeout=60)
    assert proc.returncode != 0


def test_reexec_under_a_valid_pin_still_denies_a_credential(env: dict) -> None:
    """(a) The gate re-runs under the pinned interpreter, so the deterministic deny survives a bare python3."""
    _pin(env, sys.executable)
    code, decision, _ = _run(env, CREDENTIAL)
    assert code == 0 and decision is not None and decision["permissionDecision"] == "deny", decision
    assert "credential" in decision["permissionDecisionReason"]


def test_reexec_keeps_ordinary_writes_unobstructed(env: dict) -> None:
    _pin(env, sys.executable)
    code, decision, _ = _run(env, {"tool_name": "Write", "tool_input": {"file_path": "a.md", "content": "docs\n"}})
    assert code == 0 and decision is None


def test_bash_checks_need_no_agentteams_and_no_pin(env: dict) -> None:
    code, decision, _ = _run(env, {"tool_name": "Bash", "tool_input": {"command": "rm -rf build/"}})
    assert code == 0 and decision is not None and decision["permissionDecision"] == "ask"


def test_no_interpreter_cooperative_asks_and_names_the_fix(env: dict) -> None:
    """(b) No pin, no import: cooperative asks (never the old silent allow) and says how to fix it."""
    code, decision, stderr = _run(env, CREDENTIAL)
    assert code == 0 and decision is not None and decision["permissionDecision"] == "ask", decision
    assert "agentteams --check-wiring" in decision["permissionDecisionReason"]
    assert "agentteams" in stderr and "--check-wiring" in stderr


def test_no_interpreter_fail_closed_denies(env: dict) -> None:
    _fail_closed(env)
    code, decision, _ = _run(env, CREDENTIAL)
    assert code == 0 and decision is not None and decision["permissionDecision"] == "deny", decision


@pytest.mark.parametrize("pin", ["none", "inside-project"])
def test_a_planted_project_package_never_decides_the_verdict(env: dict, pin: str) -> None:
    """1.0 must-fix: the gate never imports agentteams from the project tree.

    An older fallback put the hook's repository root (``parents[2]``) on ``sys.path`` after any pin
    failure, so a planted ``agentteams/`` package there could allow a credential write.
    """
    marker = env["tmp"] / "planted-ran"
    pkg = env["project"] / "agentteams"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    (pkg / "integrity.py").write_text("def scan(*a, **k):\n    return []\n", encoding="utf-8")
    if pin == "inside-project":
        _pin(env, str(_wrapper(env["project"] / "tools" / "python3", f'exec "{sys.executable}" -S "$@"\n')))
    code, decision, _ = _run(env, CREDENTIAL)
    assert not marker.exists(), "the gate imported a package planted in the project tree"
    assert code == 0 and decision is not None and decision["permissionDecision"] == "ask", decision


@pytest.mark.parametrize("planted", ["agentteams", "json", "__future__"])
def test_a_module_planted_beside_the_hook_is_never_imported(env: dict, planted: str) -> None:
    """1.0 must-fix: the hook's own directory is dropped from sys.path before any import.

    Run as ``python3 .claude/hooks/constitutional-gate.py``, Python puts the hooks directory first on
    ``sys.path``, so a planted package there would win even where agentteams is installed.
    """
    marker = env["tmp"] / "planted-ran"
    body = f"open({str(marker)!r}, 'w').write('x')\n"
    if planted == "agentteams":
        pkg = env["hooks"] / "agentteams"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(body, encoding="utf-8")
    else:  # json: a stdlib module; __future__: imported before any scrub could run, so never imported at all
        (env["hooks"] / f"{planted}.py").write_text(body, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(env["hooks"] / "constitutional-gate.py")],
                          input=json.dumps(CREDENTIAL), capture_output=True, text=True, timeout=60,
                          cwd=str(env["project"]), env=env["environ"])
    assert not marker.exists(), f"the gate imported a {planted} planted beside it"
    decision = json.loads(proc.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny", decision


def test_a_content_free_write_is_not_obstructed_without_an_interpreter(env: dict) -> None:
    code, decision, _ = _run(env, {"tool_name": "Edit", "tool_input": {"file_path": "a.md", "new_string": ""}})
    assert code == 0 and decision is None


def test_loop_guard_blocks_a_second_reexec(env: dict) -> None:
    """(c) A logging pin proves the re-exec happens once, and never when AGENTTEAMS_GATE_REEXEC is already set."""
    marker = env["tmp"] / "pin-ran"
    pin = _wrapper(env["tmp"] / "pinbin" / "python3", f'echo x >> "{marker}"\nexec "{sys.executable}" "$@"\n')
    _pin(env, str(pin))
    code, decision, _ = _run(env, CREDENTIAL)
    assert decision is not None and decision["permissionDecision"] == "deny"
    assert marker.read_text().count("x") == 1, "the pinned interpreter must run exactly once"
    marker.unlink()
    code, decision, _ = _run(env, CREDENTIAL, AGENTTEAMS_GATE_REEXEC="1")
    assert not marker.exists(), "the loop guard must stop a re-exec"
    assert code == 0 and decision is not None and decision["permissionDecision"] == "ask"
    assert "already re-executed" in decision["permissionDecisionReason"]


def test_a_pin_without_agentteams_degrades_to_ask_not_a_loop(env: dict) -> None:
    pin = _wrapper(env["tmp"] / "pinbin" / "python3", f'exec "{sys.executable}" -S "$@"\n')
    _pin(env, str(pin))
    code, decision, _ = _run(env, CREDENTIAL)
    assert code == 0 and decision is not None and decision["permissionDecision"] == "ask"


@pytest.mark.parametrize("kind", ["relative", "inside-project", "missing", "not-python", "in-project-venv"])
def test_an_invalid_pin_is_refused(env: dict, kind: str) -> None:
    """(d) Relative, in-project, missing or non-python pins are never executed."""
    marker = env["tmp"] / "pin-ran"
    body = f'echo x >> "{marker}"\nexec "{sys.executable}" "$@"\n'
    if kind == "relative":
        value, why = "bin/python3", "not an absolute path"
    elif kind == "inside-project":
        value, why = str(_wrapper(env["project"] / "tools" / "python3", body)), "inside the project"
    elif kind == "missing":
        value, why = str(env["tmp"] / "nowhere" / "python3"), "existing executable"
    elif kind == "not-python":
        value, why = str(_wrapper(env["tmp"] / "pinbin" / "sh-ish", body)), "python interpreter"
    else:  # the link is outside the project but its pyvenv.cfg (what it loads) is agent-writable
        venv = env["project"] / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        outside = _wrapper(env["tmp"] / "pinbin" / "python3", body)
        (venv / "bin" / "python3").symlink_to(outside)
        value, why = str(venv / "bin" / "python3"), "inside the project"
    _pin(env, value)
    code, decision, _ = _run(env, CREDENTIAL)
    assert not marker.exists(), f"a {kind} pin was executed"
    assert code == 0 and decision is not None and decision["permissionDecision"] == "ask", decision
    assert why in decision["permissionDecisionReason"]


def test_the_template_stays_python39_and_stdlib_only() -> None:
    import ast

    tree = ast.parse(TEMPLATE.read_text(encoding="utf-8"), feature_version=(3, 9))
    top = {n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)}
    top |= {n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
    assert top <= {"__future__", "datetime", "hashlib", "hmac", "json", "os", "pathlib", "re", "shlex", "stat",
                   "subprocess", "sys"}, top


# ── Emission: the pin lands in exactly one ignored file (Rule S-8) ────────────────────────────────


def test_rendered_team_carries_the_pin_and_no_other_machine_path(tmp_path: Path) -> None:
    """(e) Render a real Claude team: the pin + its ignore file exist, and no other emitted file holds the path."""
    from tests._fresh_intel import seed_offline_intel

    project = tmp_path / "proj"
    out = project / ".claude" / "agents"
    project.mkdir()
    brief = tmp_path / "brief.json"
    brief.write_text((REPO / "examples/software-project/brief.json").read_text())
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(brief),
                           "--framework", "claude", "--output", str(out), "--project", str(project), "--yes",
                           *seed_offline_intel(out, "claude")], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    hooks = project / ".claude" / "hooks"
    assert (hooks / GATE_PIN_NAME).read_text(encoding="utf-8") == sys.executable + "\n"
    rules = [line for line in (hooks / ".gitignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    assert rules == [f"/{GATE_PIN_NAME}"], "the ignore file must ignore exactly the pin"
    needles = {str(Path.home()), sys.executable, os.path.realpath(sys.executable)}
    needles = {n for n in needles if n and n not in ("/", "/usr/bin/python3")}
    leaks = []
    for path in project.rglob("*"):
        if not path.is_file() or path == hooks / GATE_PIN_NAME:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            text = path.read_bytes().decode("latin-1")
        leaks += [f"{path.relative_to(project)}: {n}" for n in needles if n in text]
    assert leaks == [], leaks


def test_grok_emits_the_pin_beside_its_gate_copy() -> None:
    from agentteams.frameworks._grok_sandbox_emit import grok_sandbox_output_files

    files = dict(grok_sandbox_output_files({"privilege_profile": "confined"}, platform="linux"))
    assert files["../hooks/agentteams-python"] == sys.executable + "\n"
    assert f"/{GATE_PIN_NAME}" in files["../hooks/.gitignore"]
    assert "agentteams-managed" in files["../hooks/.gitignore"].splitlines()[0]


# ── --check-wiring ───────────────────────────────────────────────────────────────────────────────


def test_check_wiring_reports_a_bad_pin_and_the_fix(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """(f) Neither a refused pin nor python3 imports agentteams: fail, show the pin, print the exact fix."""
    monkeypatch.setenv("PATH", env["environ"]["PATH"])
    _pin(env, "relative/python3")
    ok, messages = verify_gate_interpreter(env["project"], "claude")
    text = "\n".join(messages)
    assert not ok
    assert "relative/python3" in text and "REFUSED" in text and "not an absolute path" in text
    assert "ERROR" in text and f"> {env['hooks'] / GATE_PIN_NAME}" in text


@pytest.mark.parametrize("kind", ["relative", "inside-project", "missing", "not-python", "in-project-venv"])
def test_check_wiring_refuses_the_same_pins_as_the_hook(env: dict, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """@code-hygiene: the hook and --check-wiring validate pins separately; keep them in parity."""
    monkeypatch.setenv("PATH", env["environ"]["PATH"])
    body = f'exec "{sys.executable}" "$@"\n'
    if kind == "relative":
        value = "bin/python3"
    elif kind == "inside-project":
        value = str(_wrapper(env["project"] / "tools" / "python3", body))
    elif kind == "missing":
        value = str(env["tmp"] / "nowhere" / "python3")
    elif kind == "not-python":
        value = str(_wrapper(env["tmp"] / "pinbin2" / "sh-ish", body))
    else:
        venv = env["project"] / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        outside = _wrapper(env["tmp"] / "pinbin2" / "python3", body)
        (venv / "bin" / "python3").symlink_to(outside)
        value = str(venv / "bin" / "python3")
    _pin(env, value)
    ok, messages = verify_gate_interpreter(env["project"], "claude")
    assert not ok and "REFUSED" in "\n".join(messages), (kind, messages)


def test_check_wiring_passes_with_a_working_pin(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", env["environ"]["PATH"])
    _pin(env, sys.executable)
    ok, messages = verify_gate_interpreter(env["project"], "claude")
    assert ok, messages
    assert any(sys.executable in m and "imports agentteams" in m for m in messages)


def test_check_wiring_cli_exits_nonzero_on_a_bad_pin(env: dict) -> None:
    out = env["project"] / ".claude" / "agents"
    out.mkdir()
    _pin(env, str(env["tmp"] / "nowhere" / "python3"))
    brief = env["tmp"] / "brief.json"
    brief.write_text((REPO / "examples/software-project/brief.json").read_text())
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(brief),
                           "--framework", "claude", "--output", str(out), "--check-wiring"],
                          capture_output=True, text=True, timeout=120, cwd=str(REPO),
                          env={**env["environ"], "PATH": env["environ"]["PATH"]})
    assert proc.returncode == 1, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "gate interpreter pin" in proc.stdout and "existing executable" in proc.stdout


# ── @security implementation-audit mitigations M1-M3 ─────────────────────────────────────────────


def test_m1_isolated_parent_reexecs_an_isolated_child(env: dict) -> None:
    """M1: the Grok shim runs the gate with -I; the re-executed child must stay isolated, so a json.py planted
    beside the hook cannot shadow the stdlib there — while agentteams still imports from the pin's site-packages."""
    marker = env["tmp"] / "planted-json-ran"
    (env["hooks"] / "json.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nraise ImportError('planted')\n")
    _pin(env, sys.executable)
    proc = subprocess.run([str(env["fake"]), "-I", str(env["hooks"] / "constitutional-gate.py")],
                          input=json.dumps(CREDENTIAL), capture_output=True, text=True, timeout=60,
                          cwd=str(env["project"]), env=env["environ"])
    assert not marker.exists(), "the planted json.py ran in the re-executed child"
    decision = json.loads(proc.stdout)["hookSpecificOutput"]
    assert proc.returncode == 0 and decision["permissionDecision"] == "deny", decision


def _guarded(adapter, project: Path, agents_rel: str, files: list[tuple[str, str]]):
    out = project / agents_rel
    out.mkdir(parents=True, exist_ok=True)
    kept, notices = adapter.guard_rendered_files(files, out)
    return dict(kept), "\n".join(notices)


def test_m2_grok_refused_ignore_file_means_no_pin(tmp_path: Path) -> None:
    """M2: an operator's unmarked .grok/hooks/.gitignore is left alone, so the pin is not written either."""
    from agentteams.frameworks._grok_sandbox_emit import grok_sandbox_output_files
    from agentteams.frameworks.grok import GrokAdapter

    files = grok_sandbox_output_files({"privilege_profile": "confined"}, platform="linux")
    (tmp_path / ".grok/hooks").mkdir(parents=True)
    (tmp_path / ".grok/hooks/.gitignore").write_text("*.log\n")
    kept, notices = _guarded(GrokAdapter(), tmp_path, ".grok/agents", files)
    assert "../hooks/.gitignore" not in kept and f"../hooks/{GATE_PIN_NAME}" not in kept
    assert "interpreter pin NOT written" in notices
    assert "../hooks/constitutional-gate.py" in kept, "the gate itself is still emitted"
    (tmp_path / ".grok/hooks/.gitignore").write_text("# agentteams-managed: old\n/agentteams-python\n")
    kept, _ = _guarded(GrokAdapter(), tmp_path, ".grok/agents", files)
    assert f"../hooks/{GATE_PIN_NAME}" in kept and "../hooks/.gitignore" in kept


@pytest.mark.parametrize("existing", ["unmarked", "symlink"])
def test_m3_claude_leaves_an_operator_gitignore_and_drops_the_pin(tmp_path: Path, existing: str) -> None:
    """M3: Claude no longer overwrites an operator-owned .claude/hooks/.gitignore, and then emits no pin."""
    from agentteams.frameworks.claude import ClaudeAdapter

    hooks = tmp_path / ".claude/hooks"
    hooks.mkdir(parents=True)
    if existing == "unmarked":
        (hooks / ".gitignore").write_text("*.log\n")
    else:
        (tmp_path / "elsewhere").write_text("# agentteams-managed\n")
        (hooks / ".gitignore").symlink_to(tmp_path / "elsewhere")
    files = ClaudeAdapter().extra_output_files({"privilege_profile": "cooperative"})
    kept, notices = _guarded(ClaudeAdapter(), tmp_path, ".claude/agents", files)
    assert "../hooks/.gitignore" not in kept and f"../hooks/{GATE_PIN_NAME}" not in kept
    assert "not agentteams-managed" in notices and "interpreter pin NOT written" in notices
    assert "../hooks/constitutional-gate.py" in kept
    if existing == "unmarked":
        assert (hooks / ".gitignore").read_text() == "*.log\n"


def test_m3_claude_regenerates_its_own_marked_gitignore(tmp_path: Path) -> None:
    from agentteams.frameworks.claude import GATE_PIN_GITIGNORE, ClaudeAdapter

    (tmp_path / ".claude/hooks").mkdir(parents=True)
    (tmp_path / ".claude/hooks/.gitignore").write_text(GATE_PIN_GITIGNORE)
    files = ClaudeAdapter().extra_output_files({"privilege_profile": "cooperative"})
    kept, notices = _guarded(ClaudeAdapter(), tmp_path, ".claude/agents", files)
    assert kept[f"../hooks/{GATE_PIN_NAME}"] == sys.executable + "\n" and "../hooks/.gitignore" in kept
    assert "interpreter pin NOT written" not in notices


def test_m3_other_emitted_gitignores_are_not_this_guards_business(tmp_path: Path) -> None:
    from agentteams.frameworks.claude import guard_gate_pin_files

    (tmp_path / "x").mkdir()
    (tmp_path / "x/.gitignore").write_text("*\n")
    kept, notices = guard_gate_pin_files([("x/.gitignore", "*\n!.gitignore\n")], tmp_path)
    assert kept == [("x/.gitignore", "*\n!.gitignore\n")] and notices == []


def test_m3_render_with_an_operator_gitignore_writes_no_pin(tmp_path: Path) -> None:
    """End to end: a real Claude render over an operator-owned hooks .gitignore leaves it and writes no pin."""
    from tests._fresh_intel import seed_offline_intel

    project = tmp_path / "proj"
    out = project / ".claude" / "agents"
    (project / ".claude/hooks").mkdir(parents=True)
    (project / ".claude/hooks/.gitignore").write_text("*.log\n")
    brief = tmp_path / "brief.json"
    brief.write_text((REPO / "examples/software-project/brief.json").read_text())
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(brief),
                           "--framework", "claude", "--output", str(out), "--project", str(project), "--yes",
                           *seed_offline_intel(out, "claude")], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert (project / ".claude/hooks/.gitignore").read_text() == "*.log\n"
    assert not (project / ".claude/hooks" / GATE_PIN_NAME).exists()
    assert (project / ".claude/hooks/constitutional-gate.py").is_file()
    assert "interpreter pin NOT written" in proc.stdout + proc.stderr


def test_a_reexec_writes_exactly_one_decision_log_record(env: dict) -> None:
    """The decision log (#217) and the pin re-run (#226) together: the child logs, the parent does not."""
    import json as _json

    _pin(env, sys.executable)
    log = env["tmp"] / "gate-log" / "gate.jsonl"
    log.parent.mkdir()
    code, decision, _ = _run(env, CREDENTIAL, AGENTTEAMS_GATE_LOG=str(log))
    assert code == 0 and decision is not None and decision["permissionDecision"] == "deny"
    records = [_json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) == 1 and records[0]["decision"] == "deny" and records[0]["reason_id"] == "scan-high"
    assert records[0]["reexec"] is True  # written by the re-run child, never by the parent


def test_no_interpreter_is_logged_as_its_own_reason(env: dict) -> None:
    import json as _json

    log = env["tmp"] / "gate-log2" / "gate.jsonl"
    log.parent.mkdir()
    code, decision, _ = _run(env, CREDENTIAL, AGENTTEAMS_GATE_LOG=str(log))
    assert decision is not None and decision["permissionDecision"] == "ask"
    (record,) = [_json.loads(line) for line in log.read_text().splitlines()]
    assert record["reason_id"] == "no-interpreter" and record["decision"] == "ask"


def test_after_a_failed_child_the_parent_record_is_marked_not_reexec(env: dict) -> None:
    """@security: when the re-run child fails (exit other than 0/2), the parent logs the decision it
    actually emitted with reexec false; a child's own records carry reexec true (previous test), so a
    child crash-open record and the parent's record are always distinguishable."""
    import json as _json

    crashing = _wrapper(env["tmp"] / "pinbin3" / "python3",
                        f'exec "{sys.executable}" -c "import json,sys,os; sys.exit(1)" "$@"\n')
    _pin(env, str(crashing))
    log = env["tmp"] / "gate-log3" / "gate.jsonl"
    log.parent.mkdir()
    _run(env, CREDENTIAL, AGENTTEAMS_GATE_LOG=str(log))
    records = [_json.loads(line) for line in log.read_text().splitlines()]
    (record,) = records  # this stub child writes no record; only the parent's remains
    assert record["reexec"] is False and record["reason_id"] == "no-interpreter"
