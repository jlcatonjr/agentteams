"""readfs launch integrity (references/plans/readfs-launch-integrity.design.md, R1–R3).

The Goose read-only file server ships as package data with a pinned sha256; under the switch it is installed in
the session-write-denied control plane and launched with ``python3 -I -S``; the runner refuses a mismatched copy.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import proposal_runner as R
from agentteams.cli import standalone_modes
from agentteams.frameworks import goose as goose_mod
from agentteams.frameworks import goose_tool_scoping as scoping
from agentteams.frameworks.goose import GooseAdapter

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "agentteams" / "data" / "goose-readfs-mcp.py"


def _manifest(*, switch: bool) -> dict:
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}],
            "goose_tool_scoping": "grant"}
    if switch:
        desc.update({"write_policy": "orchestrator-only", "privilege_profile": "confined"})
    return analyze.build_manifest(desc, framework="goose")


def test_pinned_hash_matches_the_shipped_server():
    assert hashlib.sha256(SERVER.read_bytes()).hexdigest() == scoping.READFS_SHA256


def test_server_is_package_data():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"data/**"' in pyproject and not (REPO / "scripts" / "goose-readfs-mcp.py").exists()


def test_launch_uses_isolated_python_and_the_right_path():
    assert scoping.readfs_extension()["args"] == ["-I", "-S", scoping.READFS_SCRIPT, "--root", "."]
    assert scoping.readfs_extension(protected=True)["args"] == [
        "-I", "-S", scoping.READFS_PROTECTED_PATH, "--root", "."]


def test_install_location_follows_the_switch():
    def shipped(switch):
        return dict(GooseAdapter().extra_output_files(_manifest(switch=switch)))
    off, on = shipped(False), shipped(True)
    assert f"../../{scoping.READFS_SCRIPT}" in off and f"../../{scoping.READFS_PROTECTED_PATH}" not in off
    assert f"../../{scoping.READFS_PROTECTED_PATH}" in on and f"../../{scoping.READFS_SCRIPT}" not in on
    assert hashlib.sha256(on[f"../../{scoping.READFS_PROTECTED_PATH}"].encode()).hexdigest() == scoping.READFS_SHA256


def test_render_fails_closed_on_a_mismatched_server(monkeypatch):
    monkeypatch.setattr(goose_mod, "_readfs_mcp_content", lambda: "# not the shipped server\n")
    with pytest.raises(ValueError, match="pinned hash"):
        GooseAdapter().extra_output_files(_manifest(switch=True))
    GooseAdapter().extra_output_files(_manifest(switch=False))          # without the switch: unchanged behaviour


def test_runner_checks_the_installed_copy(tmp_path):
    R.check_installed_readfs(tmp_path)                                  # absent: no Goose readers, passes
    installed = tmp_path / scoping.READFS_PROTECTED_PATH
    installed.parent.mkdir(parents=True)
    installed.write_bytes(SERVER.read_bytes())
    R.check_installed_readfs(tmp_path)                                  # the shipped copy passes
    installed.write_text(SERVER.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    with pytest.raises(R.RunnerError, match="doesn't match"):
        R.check_installed_readfs(tmp_path)
    installed.unlink()
    installed.symlink_to(SERVER)
    with pytest.raises(R.RunnerError, match="not a regular file"):
        R.check_installed_readfs(tmp_path)
    installed.unlink()
    installed.parent.rmdir()
    elsewhere = tmp_path / "writable"
    elsewhere.mkdir()
    (elsewhere / Path(scoping.READFS_PROTECTED_PATH).name).write_bytes(SERVER.read_bytes())
    installed.parent.symlink_to(elsewhere)                              # .agentteams/bin -> agent-writable folder
    with pytest.raises(R.RunnerError, match="is a symlink"):
        R.check_installed_readfs(tmp_path)


def test_python3_inside_the_workspace_is_flagged(tmp_path, monkeypatch):
    import shutil

    system = tmp_path / "system" / "python3"
    system.parent.mkdir()
    system.write_text("")
    project = tmp_path / "project"
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(system)          # a normal virtualenv: a symlink OUT of the project
    (project / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    monkeypatch.setattr(shutil, "which", lambda name: str(venv_bin / "python3"))
    assert "inside the project" in standalone_modes.python3_in_workspace_note(project)
    # a venv whose interpreter path is outside but whose pyvenv.cfg sits in the project tree is caught too
    elsewhere = project / "tools" / "env"
    (elsewhere / "bin").mkdir(parents=True)
    (elsewhere / "pyvenv.cfg").write_text("home = /usr/bin\n")
    monkeypatch.setattr(shutil, "which", lambda name: str(elsewhere / "bin" / "python3"))
    assert standalone_modes.python3_in_workspace_note(project)
    monkeypatch.setattr(shutil, "which", lambda name: str(system))
    assert standalone_modes.python3_in_workspace_note(project) is None


def test_stale_scripts_copy_is_reported(tmp_path):
    assert standalone_modes.stale_readfs_copy_note(tmp_path) is None
    (tmp_path / scoping.READFS_SCRIPT).parent.mkdir(parents=True)
    (tmp_path / scoping.READFS_SCRIPT).write_text("x")
    assert "delete it" in standalone_modes.stale_readfs_copy_note(tmp_path)


def test_claude_edit_tools_are_denied_the_control_plane_under_the_switch():
    import json

    from agentteams.frameworks import _sandbox_emit as se

    example = json.dumps({"hooks": {}, "_comment": []})
    on = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=True))["permissions"]["deny"]
    off = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=False))["permissions"]["deny"]
    assert "Edit(/.agentteams/**)" in on and "Edit(/.agentteams/**)" not in off


def test_server_denies_the_control_plane(tmp_path):
    spec = importlib.util.spec_from_file_location("readfs_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = mod.Workspace(tmp_path)
    for rel in (".agentteams/queue-results/x.json", ".agentteams-queue/requests/y.json",
                ".agentteams/proposal-ledger.jsonl", ".agentteams/bin/goose-readfs-mcp.py"):
        assert ws.denied(rel), rel
    assert not ws.denied("src/app.py")


def test_claude_read_tools_are_denied_the_queue_and_control_plane_under_the_switch():
    """R1 (@security C4): the queue and results briefly hold raw dispatch nonces."""
    import json

    from agentteams.frameworks import _sandbox_emit as se

    example = json.dumps({"hooks": {}, "_comment": []})
    on = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=True))["permissions"]["deny"]
    off = json.loads(se._inject_sandbox_block(example, ["."], protect_ledger=False))["permissions"]["deny"]
    for rule in ("Read(/.agentteams/**)", "Read(/.agentteams-queue/**)"):
        assert rule in on and rule not in off
