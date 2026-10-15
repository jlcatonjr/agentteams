"""Grok Phase 2b: confinement artifacts, advisory hook gate + shim (plan tmp/by-week/2026-W42 §12.2–12.3).

Spike facts these encode: `read_only` does not write-protect inside the workspace (R3), so the profile carries
no `read_only` and says what it cannot protect; relative deny globs are workspace-only (§12.1); Grok sends a
Claude-shaped envelope with Grok tool names (R5), so the gate needs the input shim.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from agentteams.frameworks import _grok_sandbox_emit as gse
from agentteams.frameworks.grok import GrokAdapter

_REPO = Path(__file__).resolve().parents[1]
_HOOKS = _REPO / "agentteams" / "templates" / "universal" / "hooks"
_CONFINED = {"privilege_profile": "confined", "project_name": "Demo"}
# Assembled at runtime (house pattern, test_constitutional_gate_hook.py) so no credential-shaped string sits in
# this file; it is AWS's published documentation example, not a key.
_SECRET = 'aws = "AKIA' + "IOSFODNN7EXAMPLE" + '"'


def _files(manifest: dict, platform: str = "linux") -> dict[str, str]:
    return dict(gse.grok_sandbox_output_files(manifest, platform=platform))


def test_emitted_only_when_confined_on_linux_or_macos() -> None:
    assert _files({"privilege_profile": "cooperative"}) == {}
    assert _files(_CONFINED, platform="win32") == {}
    assert set(_files(_CONFINED)) == {gse.GROK_SANDBOX_PROFILE_REL, gse.GROK_RUNNER_REL, gse.GROK_GATE_JSON_REL,
                                      gse.GROK_GATE_SHIM_REL, gse.GROK_GATE_REL}
    assert set(_files({"host_features": ["grok:sandbox"]}, platform="darwin")) == set(_files(_CONFINED))


def test_profile_is_honest_about_its_limits() -> None:
    text = _files(_CONFINED)[gse.GROK_SANDBOX_PROFILE_REL]
    profile = tomllib.loads(text)["profiles"]["agentteams"]
    assert profile["extends"] == "workspace" and "read_only" not in profile
    assert "**/.config/agentteams/keys/**" in profile["deny"]
    assert "workspace-only" in text and "does NOT" in text
    assert text.startswith("# " + gse.GROK_OWNED_MARKER)


def test_runner_is_valid_bash_and_runs_grok_inside_the_launcher(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text(_files(_CONFINED)[gse.GROK_RUNNER_REL], encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0
    text = script.read_text(encoding="utf-8")
    assert '"$LAUNCHER"' in text and "--sandbox off" in text and "--protect-prompt-roots" in text
    for f in ("trusted_folders.toml", "requirements.toml", "hooks"):
        assert f in text  # user-level Grok config is protected inside GROK_HOME
    assert "GROK_CLAUDE_HOOKS_ENABLED=false" in text and "--env-allow XAI_API_KEY" in text
    assert "profiles.agentteams" in text and "/etc/grok/requirements.toml" in text  # profile-mode shadow check


def test_gate_json_points_at_the_shim() -> None:
    hooks = json.loads(_files(_CONFINED)[gse.GROK_GATE_JSON_REL])["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hooks == {"type": "command", "command": "./agentteams-gate-shim.py", "timeout": 10}


def test_gate_copy_is_the_template_and_fail_closed_only_when_explicit() -> None:
    template = (_HOOKS / "constitutional-gate.py").read_text(encoding="utf-8")
    assert _files({"host_features": ["grok:sandbox"]})[gse.GROK_GATE_REL] == template
    explicit = _files({"privilege_profile": "confined", "privilege_profile_explicit": True})[gse.GROK_GATE_REL]
    assert explicit != template and "_FAIL_CLOSED_ON_ERROR = True" in explicit


def test_shim_failure_denies_only_when_the_gate_is_fail_closed(hooks_dir: Path) -> None:
    shim = hooks_dir / "agentteams-gate-shim.py"
    for closed, rc in ((False, 0), (True, 2)):
        gate = (_HOOKS / "constitutional-gate.py").read_text(encoding="utf-8")
        if closed:
            gate = gate.replace("_FAIL_CLOSED_ON_ERROR = False", "_FAIL_CLOSED_ON_ERROR = True", 1)
        (hooks_dir / "constitutional-gate.py").write_text(gate, encoding="utf-8")
        proc = subprocess.run([sys.executable, str(shim)], input="not json", text=True, capture_output=True,
                              check=False)
        assert proc.returncode == rc, (closed, proc.stderr)


@pytest.fixture()
def hooks_dir(tmp_path: Path) -> Path:
    d = tmp_path / "proj" / ".grok" / "hooks"
    d.mkdir(parents=True)
    shutil.copy(_HOOKS / "constitutional-gate.py", d)
    shutil.copy(_HOOKS / "grok-gate-shim.py", d / "agentteams-gate-shim.py")
    return d


def _decide(hooks_dir: Path, script: str, payload: dict) -> str | None:
    proc = subprocess.run([sys.executable, str(hooks_dir / script)], input=json.dumps(payload), text=True,
                          capture_output=True, check=False, cwd=hooks_dir.parent.parent)
    out = proc.stdout.strip()
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None


@pytest.mark.parametrize("payload, decision, gate_alone_blind", [
    ({"tool_name": "run_terminal_command", "tool_input": {"command": "sudo rm -rf /x"}}, "ask", True),
    ({"tool_name": "write", "tool_input": {"path": "cfg.py", "contents": _SECRET}}, "deny", True),
    ({"tool_name": "search_replace", "tool_input": {"file_path": "cfg.py", "old_string": "x", "new_string": _SECRET}},
     "deny", False),
    ({"tool_name": "run_terminal_command", "tool_input": {"command": "echo hi"}}, None, False),
])
def test_shim_lets_the_gate_see_grok_payloads(hooks_dir: Path, payload: dict, decision: str | None,
                                              gate_alone_blind: bool) -> None:
    payload = {"hook_event_name": "PreToolUse", **payload}
    if gate_alone_blind:
        assert _decide(hooks_dir, "constitutional-gate.py", payload) is None
    assert _decide(hooks_dir, "agentteams-gate-shim.py", payload) == decision


def test_shim_translation_keeps_other_keys() -> None:
    spec = importlib.util.spec_from_file_location("grok_gate_shim", _HOOKS / "grok-gate-shim.py")
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    out = shim.translate({"tool_name": "read_file", "tool_input": {"target_file": "a"}, "cwd": "/w", "x": 1})
    assert out["tool_name"] == out["toolName"] == "Read"
    assert out["tool_input"] == {"file_path": "a"} and out["cwd"] == "/w" and out["x"] == 1
    assert shim.translate({"tool_name": "mystery", "tool_input": {"k": 1}})["tool_name"] == "mystery"
    # Claude's Grep/Glob take `path` themselves: only the file tools get `path` -> `file_path`.
    assert shim.translate({"tool_name": "grep", "tool_input": {"path": "src"}})["tool_input"] == {"path": "src"}
    assert shim.translate({"tool_name": "write", "tool_input": {"path": "a", "contents": "c"}})["tool_input"] == {
        "file_path": "a", "content": "c"}


def test_operator_gate_copy_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / ".grok" / "hooks").mkdir(parents=True)
    (tmp_path / ".grok" / "hooks" / "constitutional-gate.py").write_text("# my own gate\n", encoding="utf-8")
    kept, notices = _guard(tmp_path, gse.GROK_GATE_REL)
    assert kept == [] and "not agentteams-managed" in notices[0]


# guard: never overwrite an operator's own file, never through a symlink ------------------------------

def _guard(tmp_path: Path, rel: str) -> tuple[list, list]:
    agents = tmp_path / ".grok" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    return GrokAdapter().guard_rendered_files([(rel, "# agentteams-managed: new\n")], agents)


def test_operator_sandbox_toml_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "sandbox.toml").write_text('[profiles.mine]\nextends = "strict"\n', encoding="utf-8")
    kept, notices = _guard(tmp_path, gse.GROK_SANDBOX_PROFILE_REL)
    assert kept == [] and "not agentteams-managed" in notices[0]


def test_managed_sandbox_toml_is_refreshed(tmp_path: Path) -> None:
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "sandbox.toml").write_text("# agentteams-managed: old\n", encoding="utf-8")
    kept, _ = _guard(tmp_path, gse.GROK_SANDBOX_PROFILE_REL)
    assert [p for p, _ in kept] == [gse.GROK_SANDBOX_PROFILE_REL]


def test_symlinked_hooks_dir_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".grok").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / ".grok" / "hooks").symlink_to(elsewhere, target_is_directory=True)
    kept, notices = _guard(tmp_path, gse.GROK_GATE_SHIM_REL)
    assert kept == [] and "symlink" in notices[0]
