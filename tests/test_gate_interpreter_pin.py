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
    assert top <= {"__future__", "json", "os", "re", "shlex", "subprocess", "sys", "pathlib"}, top


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
