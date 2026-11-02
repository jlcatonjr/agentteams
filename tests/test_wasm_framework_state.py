"""``--framework wasm`` and the sandbox switches (WASM plan P08 §4.6): WASM on by construction, OS -> host confinement.

The framework choice is the WASM opt-in (D6, reason ``framework``); an explicit off refuses because the runtime has no
non-WASM mode. The OS switch decides ``runtime.json.host_os_confine`` (``auto`` on, ``off`` off) and how read-only
mounts are enforced (``read_only_via``). Nothing here reads ``privilege_profile`` directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import _sandbox_profiles as SP
from agentteams import wasm_exec as W
from agentteams.cli.os_sandbox_switch import gate_exceptions
from agentteams.frameworks import _wasm_team_docs as D
from agentteams.frameworks.wasm import wasm_team_state
from agentteams.wasm_agent import team as T
from agentteams.wasm_config import WASM_FRAMEWORK_KEY, WASM_SANDBOX_CLI_KEY, wasm_switch, wasm_switch_set
from tests._wasm_framework_helpers import project, render, write_brief


def test_the_framework_turns_wasm_on_with_its_reason() -> None:
    assert wasm_switch({}) == (False, "default-off")
    assert wasm_switch({WASM_FRAMEWORK_KEY: True}) == (True, "framework") and wasm_switch_set({WASM_FRAMEWORK_KEY: True})
    assert wasm_switch({WASM_FRAMEWORK_KEY: "yes"}) == (False, "default-off")     # only the boolean True counts
    state = wasm_team_state({})
    assert state.wasm and state.wasm_reason == "framework"
    assert wasm_switch({WASM_FRAMEWORK_KEY: True, WASM_SANDBOX_CLI_KEY: "off"}) == (False, "cli")  # cli wins


@pytest.mark.parametrize("manifest", [{WASM_SANDBOX_CLI_KEY: "off"},
                                      {"wasm_sandbox": {"enabled": False, "languages": ["python"], "limits": {
                                          "fuel": W.DEFAULT_LIMITS.fuel,
                                          "max_memory_bytes": W.DEFAULT_LIMITS.max_memory_bytes,
                                          "timeout_s": W.DEFAULT_LIMITS.timeout_s}, "agents": {},
                                          "native_exec": "replace", "native_exec_keep": [], "model_substrate": None}}])
def test_an_explicit_off_refuses_in_the_adapter(manifest: dict) -> None:
    with pytest.raises(ValueError, match="needs WASM confinement"):
        wasm_team_state(manifest)


def test_render_records_the_framework_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    assert render(root, write_brief(root, wasm_sandbox=None)) == 0
    out = capsys.readouterr().out
    assert "--framework wasm turns WASM confinement on for this team (reason: framework)" in out
    assert "WASM confinement: **on** (framework)" in (root / T.README_REL).read_text()
    log = json.loads((root / ".agentteams-wasm/agents/references/build-log.json").read_text())
    assert '"wasm_reason": "framework"' in json.dumps(log)


@pytest.mark.parametrize("extra, brief_over", [
    (("--wasm-sandbox", "off"), {"wasm_sandbox": None}),
    ((), {"wasm_sandbox": {"enabled": False}}),
])
def test_render_refuses_an_explicit_wasm_off(tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple,
                                             brief_over: dict) -> None:
    root = project(tmp_path)
    assert render(root, write_brief(root, **brief_over), *extra) == 1
    assert "--framework wasm needs WASM confinement" in capsys.readouterr().err
    assert not (root / T.LOCK_REL).exists()


def test_os_off_turns_host_confinement_off_and_says_so(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert render(root, write_brief(root, wasm_sandbox=None), "--os-sandbox", "off") == 0
    assert json.loads((root / T.RUNTIME_REL).read_text())["host_os_confine"] == "off"
    readme = (root / T.README_REL).read_text()
    assert "the host is NOT OS-confined" in readme and "OS switch: off (cli)" in readme


def test_os_on_is_auto(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert render(root, write_brief(root, wasm_sandbox=None)) == 0
    assert json.loads((root / T.RUNTIME_REL).read_text())["host_os_confine"] == "auto"


def _ro_grant() -> W.Grant:
    return W.Grant((W.Mount("/x/docs", "/project/docs", "ro"),), W.DEFAULT_LIMITS)


def test_read_only_via(monkeypatch: pytest.MonkeyPatch) -> None:
    assert D.read_only_via(W.COMPUTE_ONLY, "auto") == "none"
    assert D.read_only_via(_ro_grant(), "auto") == "snapshot"       # guests inherit the host profile: snapshot copy
    monkeypatch.setattr(SP, "available", lambda: "seatbelt")
    assert D.read_only_via(_ro_grant(), "off") == "os"              # a fresh nested profile enforces it
    monkeypatch.setattr(SP, "available", lambda: None)
    assert D.read_only_via(_ro_grant(), "off") == "snapshot"


def test_read_only_mode_is_printed_by_the_launcher_and_recorded(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert render(root, write_brief(root)) == 0
    entry = json.loads((root / T.GRANTS_REL).read_text())["agents"]["primary-producer"]
    assert entry["read_only_via"] == "snapshot"
    assert "| snapshot |" in (root / T.README_REL).read_text()


def test_wasm_framework_has_no_native_exec_keep() -> None:
    """The runtime has no native execution, so D12's default keep list does not apply (no gate exception)."""
    manifest = {"framework": "wasm", "agent_slug_list": ["navigator", "git-operations"]}
    state = wasm_team_state(manifest)
    assert gate_exceptions(manifest, state, Path("."), None) is None
    other = {"framework": "claude", "agent_slug_list": ["navigator", "git-operations"], WASM_FRAMEWORK_KEY: True}
    exc = gate_exceptions(other, wasm_team_state(other), Path("."), None)
    assert exc is not None and exc[1].native_exec_keep == frozenset({"navigator", "git-operations"})
