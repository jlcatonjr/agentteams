"""Codex Phase 1b: the role gate (write_policy orchestrator-only) and the runner's in-launcher self-probe.

Live evidence for the hook contract these tests encode (codex-cli 0.160.1): a spawned agent's PreToolUse input
carries ``agent_type``; the top-level session's doesn't; exit 2 blocks and exit 1 lets the call through; untrusted
project hooks are skipped unless ``--dangerously-bypass-hook-trust``. See
``references/plans/codex-enforced-sandbox.design.md``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.frameworks import _codex_role_gate_emit as gate_emit
from agentteams.frameworks import _codex_sandbox_emit as emit
from agentteams.frameworks.codex import CodexAdapter
from agentteams.frameworks.goose_tool_scoping import READFS_PROTECTED_PATH, READFS_SHA256

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "agentteams" / "data" / "codex-role-gate.py"
_POLICY = {"write_policy": "orchestrator-only", "privilege_profile": "confined", "workspace_write_roots": ["."]}


def _gate_module():
    spec = importlib.util.spec_from_file_location("codex_role_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the gate itself -------------------------------------------------------------------------------------------

def test_pinned_hash_matches_the_shipped_gate() -> None:
    assert hashlib.sha256(GATE.read_bytes()).hexdigest() == gate_emit.CODEX_ROLE_GATE_SHA256


def test_gate_is_package_data_and_integrity_pinned() -> None:
    from agentteams.integrity import ENFORCEMENT_MODULES

    assert '"data/**"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "agentteams/data/codex-role-gate.py" in ENFORCEMENT_MODULES
    assert "agentteams/frameworks/_codex_sandbox_emit.py" in ENFORCEMENT_MODULES
    assert "agentteams/frameworks/_codex_role_gate_emit.py" in ENFORCEMENT_MODULES


@pytest.mark.parametrize("event, allowed", [
    ({"hook_event_name": "PreToolUse", "tool_name": "Bash"}, True),                       # top-level session
    ({"hook_event_name": "PreToolUse", "agent_type": "", "tool_name": "Bash"}, True),
    ({"hook_event_name": "PreToolUse", "agent_type": "navigator",
      "tool_name": "mcp__agentteams_readfs__read_file"}, True),
    ({"hook_event_name": "PreToolUse", "agent_type": "navigator", "tool_name": "Bash"}, False),
    ({"hook_event_name": "PreToolUse", "agent_type": "navigator", "tool_name": "apply_patch"}, False),
    ({"hook_event_name": "PreToolUse", "agent_type": "navigator", "tool_name": "collaborationspawn_agent"}, False),
    ({"hook_event_name": "PreToolUse", "agent_type": "orchestrator", "tool_name": "Bash"}, False),  # a spawned one
    ({"hook_event_name": "PreToolUse", "agent_type": "navigator",
      "tool_name": "mcp__agentteams_readfs__write_file"}, False),
    ({"hook_event_name": "PreToolUse", "agent_type": "navigator", "tool_name": "mcp__github__create_issue"}, False),
    ({"hook_event_name": "PreToolUse", "agent_type": "navigator", "tool_name": None}, False),
    ({"hook_event_name": "PreToolUse", "agent_type": 7, "tool_name": "Bash"}, False),
    ({"hook_event_name": "PreToolUse", "agent_type": "default", "tool_name": "Bash"}, False),  # generic subagent
    ({"hook_event_name": "PreToolUse", "agent_id": "01a1", "tool_name": "Bash"}, False),      # id without a type
    ({"agent_type": "qa", "tool_name": "Bash"}, False),                                      # no event name
    ({"hook_event_name": "SessionStart"}, False),                                            # only PreToolUse wired
    (["not", "a", "dict"], False),
])
def test_decide(event: object, allowed: bool) -> None:
    assert (_gate_module().decide(event, confined=True) is None) is allowed


def test_decide_denies_everything_outside_the_runner() -> None:
    assert _gate_module().decide({"hook_event_name": "PreToolUse", "tool_name": "Bash"}, confined=False)


def _run_gate(stdin: bytes, confined: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "AGENTTEAMS_CODEX_CONFINED"}
    if confined:
        env["AGENTTEAMS_CODEX_CONFINED"] = "1"
    return subprocess.run([sys.executable, "-I", "-S", str(GATE)], input=stdin, env=env, capture_output=True)


def test_gate_exit_codes() -> None:
    allow = _run_gate(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash"}).encode())
    assert allow.returncode == 0 and allow.stderr == b""
    deny = _run_gate(json.dumps({"hook_event_name": "PreToolUse", "agent_type": "qa", "tool_name": "Bash"}).encode())
    assert deny.returncode == 2 and b"agentteams role gate" in deny.stderr
    for bad in (b"", b"{not json", b"\xff\xfe"):                       # malformed input never allows
        assert _run_gate(bad).returncode == 2
    assert _run_gate(b" " * (5 * 1024 * 1024)).returncode == 2              # oversized input never allows
    unconfined = _run_gate(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash"}).encode(), False)
    assert unconfined.returncode == 2 and b"confined-run.example.sh" in unconfined.stderr


def test_hooks_json_runs_the_gate_and_blocks_on_any_failure() -> None:
    data = json.loads(gate_emit.codex_hooks_json())
    [group] = data["hooks"]["PreToolUse"]
    assert group["matcher"] == ".*"
    [hook] = group["hooks"]
    assert hook["command"] == ('"$AGENTTEAMS_PYTHON" -I -S '
                               f'"$AGENTTEAMS_ROOT/{gate_emit.CODEX_ROLE_GATE_PROJECT_PATH}" || exit 2')
    assert hook["timeout"] == gate_emit.HOOK_TIMEOUT_SECONDS


# --- emission and the ownership guard -------------------------------------------------------------------------

def test_policy_emits_the_gate_files_and_plain_confinement_does_not() -> None:
    files = dict(emit.codex_sandbox_output_files(dict(_POLICY), platform="darwin"))
    assert set(files) == {emit.CODEX_RUNNER_REL, f"../../{gate_emit.CODEX_ROLE_GATE_PROJECT_PATH}",
                          f"../../{READFS_PROTECTED_PATH}", "../hooks.json"}
    assert files[f"../../{gate_emit.CODEX_ROLE_GATE_PROJECT_PATH}"] == GATE.read_text(encoding="utf-8")
    assert hashlib.sha256(files[f"../../{READFS_PROTECTED_PATH}"].encode()).hexdigest() == READFS_SHA256
    assert [r for r, _ in emit.codex_sandbox_output_files({"privilege_profile": "confined"}, platform="darwin")] \
        == [emit.CODEX_RUNNER_REL]


def test_a_gate_that_does_not_match_its_pin_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(gate_emit, "CODEX_ROLE_GATE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="pinned hash"):
        emit.codex_sandbox_output_files(dict(_POLICY), platform="linux")


def test_hooks_json_ownership(tmp_path: Path) -> None:
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.parent.mkdir()
    assert gate_emit.codex_hooks_json_is_ours(hooks)                                   # absent
    hooks.write_text(gate_emit.codex_hooks_json(), encoding="utf-8")
    assert gate_emit.codex_hooks_json_is_ours(hooks)
    for foreign in ('{"hooks": {"Stop": []}}', "not json", '{"hooks": {"PreToolUse": [{"hooks": '
                    '[{"type": "command", "command": "my-linter"}]}]}}'):
        hooks.write_text(foreign, encoding="utf-8")
        assert not gate_emit.codex_hooks_json_is_ours(hooks), foreign
    hooks.unlink()
    (tmp_path / "elsewhere.json").write_text(gate_emit.codex_hooks_json(), encoding="utf-8")
    hooks.symlink_to(tmp_path / "elsewhere.json")
    assert not gate_emit.codex_hooks_json_is_ours(hooks)


def test_guard_keeps_an_operators_hooks_json(tmp_path: Path) -> None:
    agents = tmp_path / ".codex" / "agents"
    agents.mkdir(parents=True)
    (tmp_path / ".codex" / "hooks.json").write_text('{"hooks": {"Stop": []}}', encoding="utf-8")
    kept, notices = CodexAdapter().guard_rendered_files([("../hooks.json", "{}"), ("x.toml", "")], agents)
    assert kept == [("x.toml", "")]
    assert any("not generated by agentteams" in n and "refuse to start" in n for n in notices)


def test_non_orchestrators_get_the_role_gate_reading_section() -> None:
    agent = "---\nname: Navigator\ndescription: d\ntools: [read, search, todo]\n---\n# Navigator\n\nBody.\n"
    gated = CodexAdapter().render_agent_file(agent, "navigator", dict(_POLICY))
    plain = CodexAdapter().render_agent_file(agent, "navigator", {"privilege_profile": "confined"})
    assert "Reading on Codex (role gate)" in gated and "`sed -n`" not in gated
    assert "Reading on Codex (role gate)" not in plain and "`sed -n`" in plain


# --- the runner -----------------------------------------------------------------------------------------------

def _project(tmp_path: Path, manifest: dict, launcher: str) -> tuple[Path, dict]:
    proj, home, bindir = tmp_path / "proj", tmp_path / "home", tmp_path / "bin"
    for d in (proj / ".codex" / "agents", proj / "sandbox", home, bindir):
        d.mkdir(parents=True, exist_ok=True)
    for rel, text in emit.codex_sandbox_output_files(manifest, platform="linux"):
        out = Path(os.path.normpath(proj / ".codex" / "agents" / rel))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    stub = proj / "sandbox" / "confine-run.sh"
    stub.write_text(launcher, encoding="utf-8")
    stub.chmod(0o755)
    (bindir / "codex").write_text('#!/bin/sh\necho "CODEX STARTED $*"\n', encoding="utf-8")
    (bindir / "codex").chmod(0o755)
    env = {"HOME": str(home), "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}", "TMPDIR": str(tmp_path),
           "AGENTTEAMS_CODEX_HOME": str(home / ".config" / "agentteams" / "codex-home" / "proj")}
    return proj, env


_ARGV_LAUNCHER = '#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n'
#: Honours --setenv and runs the command, confining nothing: what a broken or bypassed boundary looks like.
_UNCONFINED_LAUNCHER = (
    "#!/usr/bin/env bash\n"
    'while [ $# -gt 0 ]; do case "$1" in --) shift; break ;; --setenv) export "$2"; shift 2 ;;\n'
    "  --protect-prompt-roots) shift ;; *) shift 2 ;; esac; done\n"
    'exec "$@"\n'
)


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    return bash


def test_runner_checks_pins_writes_the_server_entry_and_trusts_the_hook(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, dict(_POLICY), _ARGV_LAUNCHER)
    out = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH), "exec", "hi"], env=env,
                         capture_output=True, text=True, check=True)
    argv = out.stdout.splitlines()
    assert argv[argv.index("--") + 1:argv.index("--") + 3] == ["bash", "-c"]      # the probe text is multi-line
    assert argv[argv.index("codex-confined"):] == ["codex-confined", "--sandbox", "danger-full-access",
                                                   "--dangerously-bypass-hook-trust", "exec", "hi"]
    for setenv in ("AGENTTEAMS_CODEX_CONFINED=1", "AGENTTEAMS_PROBE_GATE=1", f"AGENTTEAMS_ROOT={proj.resolve()}"):
        assert setenv in argv, setenv
    [py] = [a.split("=", 1)[1] for a in argv if a.startswith("AGENTTEAMS_PYTHON=")]
    assert py.startswith("/")                                                   # absolute, resolved outside
    config = (Path(env["AGENTTEAMS_CODEX_HOME"]) / "config.toml").read_text(encoding="utf-8")
    assert config.startswith(f'[mcp_servers.agentteams_readfs]\ncommand = "{py}"\n')
    assert f'"{proj.resolve()}/{READFS_PROTECTED_PATH}", "--root", "{proj.resolve()}"]' in config
    assert "security-relevant keys" not in out.stderr                       # its own entry doesn't trip the warning


def test_runner_without_the_policy_has_no_trust_bypass(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, {"privilege_profile": "confined"}, _ARGV_LAUNCHER)
    argv = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True,
                          text=True, check=True).stdout.splitlines()
    assert "--dangerously-bypass-hook-trust" not in argv and "AGENTTEAMS_PROBE_GATE=0" in argv


@pytest.mark.parametrize("tamper", [gate_emit.CODEX_ROLE_GATE_PROJECT_PATH, READFS_PROTECTED_PATH,
                                    gate_emit.CODEX_HOOKS_PROJECT_PATH])
def test_runner_refuses_a_changed_or_missing_gate_file(tmp_path: Path, tamper: str) -> None:
    proj, env = _project(tmp_path, dict(_POLICY), _ARGV_LAUNCHER)
    with open(proj / tamper, "a", encoding="utf-8") as fh:
        fh.write("\n# changed\n")
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "not the pinned copy" in bad.stderr
    (proj / tamper).unlink()
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "missing" in bad.stderr


def test_runner_requires_the_server_entry_in_an_operator_config(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, dict(_POLICY), _ARGV_LAUNCHER)
    home = Path(env["AGENTTEAMS_CODEX_HOME"])
    home.mkdir(parents=True)
    (home / "config.toml").write_text('model = "x"\n', encoding="utf-8")
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "lacks the read-only server" in bad.stderr
    assert (home / "config.toml").read_text(encoding="utf-8") == 'model = "x"\n'      # never rewritten


def test_runner_refuses_a_duplicated_server_block(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, dict(_POLICY), _ARGV_LAUNCHER)
    subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, check=True)
    config = Path(env["AGENTTEAMS_CODEX_HOME"]) / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") * 2, encoding="utf-8")
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "exactly once" in bad.stderr


def test_runner_refuses_a_project_config_that_redefines_the_server(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, dict(_POLICY), _ARGV_LAUNCHER)
    for form in ('[mcp_servers.agentteams_readfs]\ncommand = "evil"\n',
                 'mcp_servers.agentteams_readfs.command = "evil"\n',
                 '[mcp_servers]\nagentteams_readfs = { command = "evil" }\n'):
        (proj / ".codex" / "config.toml").write_text(form, encoding="utf-8")
        bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True,
                             text=True)
        assert bad.returncode == 2 and "only the runner may define that server" in bad.stderr, form


def test_runner_refuses_a_python_agents_can_write(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, dict(_POLICY), _ARGV_LAUNCHER)
    venv = proj / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python3").write_text("#!/bin/sh\n", encoding="utf-8")
    env["AGENTTEAMS_CODEX_PYTHON"] = str(venv / "python3")
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "inside a path agents can write" in bad.stderr
    link_dir = tmp_path / "outside"
    link_dir.mkdir()
    (link_dir / "python3").symlink_to(venv / "python3")                       # a link outside, pointing in
    env["AGENTTEAMS_CODEX_PYTHON"] = str(link_dir / "python3")
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 2 and "inside a path agents can write" in bad.stderr


def test_self_probe_refuses_to_start_codex_when_nothing_is_confined(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, dict(_POLICY), _UNCONFINED_LAUNCHER)
    (proj / ".agentteams").mkdir(exist_ok=True)
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 3 and "SELF-PROBE FAILED" in bad.stderr and "CODEX STARTED" not in bad.stdout
    assert not list((proj / ".agentteams").glob(".self-probe*"))                     # its own probe dir is removed


def test_self_probe_checks_the_key_dir_first(tmp_path: Path) -> None:
    proj, env = _project(tmp_path, {"privilege_profile": "confined"}, _UNCONFINED_LAUNCHER)
    keys = Path(env["HOME"]) / ".config" / "agentteams" / "keys"
    keys.mkdir(parents=True)
    (keys / "ledger.key").write_text("k", encoding="utf-8")
    bad = subprocess.run([_bash(), str(proj / emit.CODEX_RUNNER_PROJECT_PATH)], env=env, capture_output=True, text=True)
    assert bad.returncode == 3 and "signing-key dir is readable" in bad.stderr
