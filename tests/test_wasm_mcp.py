"""wasm_mcp (WASM P10): the agentteams_wasm server's pins, bundle, launch flags, entry builders, install and wiring.

Unit tests: no wasmtime needed. The server itself is exercised over stdio in ``tests/test_wasm_mcp_server.py``.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

from agentteams import integrity, runner_mcp, wasm_exec as W, wasm_mcp as M, wasm_runtime
from agentteams.sandbox_state import SandboxState
from tests._wasm_mcp_helpers import SERVER

PKG = Path(M.__file__).parent
_HEX = "ab" * 32


def _server_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("_wasm_mcp_server_under_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _grant(*mounts: W.Mount, limits: W.Limits = W.DEFAULT_LIMITS) -> W.Grant:
    return W.Grant(tuple(mounts), limits)


ROOT = "/srv/proj"
WIDE = _grant(W.Mount(f"{ROOT}/src", "/project/src", "ro"), W.Mount(f"{ROOT}/data", "/project/data", "ro"),
              W.Mount(f"{ROOT}/.agentteams/wasm/scratch/analyst", "/scratch", "rw"))


# --- pins ----------------------------------------------------------------------------------------------------

def test_pinned_hash_matches_the_shipped_server() -> None:
    assert hashlib.sha256(SERVER.read_bytes()).hexdigest() == M.SHA256
    assert M.server_content() == SERVER.read_text(encoding="utf-8")


def test_bundle_pins_match_the_packaged_files_and_the_server_constants() -> None:
    sources = dict(M.BUNDLE)
    for installed, sha in M.BUNDLE_SHA256.items():
        assert hashlib.sha256((PKG / sources[installed]).read_bytes()).hexdigest() == sha, installed
    assert set(M.BUNDLE_SHA256) | {M.PINS_INSTALLED} == set(sources)
    srv = _server_module()
    carried = {f"{M.BIN_DIR}/{f}": s for _, f, s in srv.BUNDLE_MODULES} | \
        {f"{M.BIN_DIR}/{f}": s for f, s in srv.BUNDLE_DATA}
    assert carried == dict(M.BUNDLE_SHA256)
    assert f"{M.BIN_DIR}/{srv.PINS_FILE}" == M.PINS_INSTALLED and M.pins_sha256() == wasm_runtime.PINS_SHA256
    # the server loads the modules in BUNDLE order
    assert [f"{M.BIN_DIR}/{f}" for _, f, _ in srv.BUNDLE_MODULES] == [p for p, _ in M.BUNDLE if p.endswith(".py")
                                                                      and "probe" not in p]


def test_bundle_files_are_byte_identical_to_package_modules() -> None:
    for installed, data in M.bundle_files():
        src = SERVER if installed == M.PROTECTED_PATH else PKG / dict(M.BUNDLE)[installed]
        assert data == src.read_bytes(), installed


@pytest.mark.parametrize("name", ["_sandbox_profiles.py", "wasm_pins.py", "wasm_exec.py", "wasm_guests.py",
                                  "data/agentteams-wasm-mcp.py"])
def test_bundle_modules_import_nothing_from_agentteams(name: str) -> None:
    tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0 and not (node.module or "").startswith("agentteams"), (name, node.module)
        elif isinstance(node, ast.Import):
            assert not any(a.name.startswith("agentteams") for a in node.names), name
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "__import__", name


def test_bundle_modules_run_exec_loaded_without_a_file() -> None:
    """What the server does: compile + exec each file into a fresh module with no ``__file__``."""
    loaded = {}
    for installed, data in M.bundle_files():
        if installed.endswith(".py") and "probe" not in installed and installed != M.PROTECTED_PATH:
            name = "_p10_" + Path(installed).stem
            mod = types.ModuleType(name)
            sys.modules[name] = mod
            try:
                exec(compile(data, f"<{name}>", "exec", dont_inherit=True), mod.__dict__)  # noqa: S102
            finally:
                sys.modules.pop(name, None)
            assert "__file__" not in mod.__dict__
            loaded[Path(installed).stem] = mod
    assert loaded["wasm_exec"].COMPUTE_ONLY.mounts == ()
    assert loaded["wasm_guests"].LANGUAGES == ("module", "python", "javascript")


def test_module_is_integrity_pinned() -> None:
    for rel in ("agentteams/wasm_mcp.py", "agentteams/data/agentteams-wasm-mcp.py"):
        assert rel in integrity.ENFORCEMENT_MODULES, rel


# --- launch ---------------------------------------------------------------------------------------------------

def test_launch_flags_are_I_S_B() -> None:
    args = M.launch_args("analyst", W.COMPUTE_ONLY, ("module",), _HEX)
    assert args[:4] == ["-I", "-S", "-B", M.PROTECTED_PATH] and M.PYTHON_FLAGS == ("-I", "-S", "-B")


def test_launch_args_are_canonical() -> None:
    d = W.DEFAULT_LIMITS
    assert M.launch_args("analyst", WIDE, ("python", "module"), _HEX) == [
        "-I", "-S", "-B", M.PROTECTED_PATH, "--agent", "analyst", "--languages", "module,python",
        "--read", "data", "--read", "src", "--scratch", "--fuel", str(d.fuel), "--max-memory-bytes",
        str(d.max_memory_bytes), "--timeout-s", str(d.timeout_s), "--pins-sha256", _HEX,
        "--grant-sha256", M.grant_digest(WIDE)]
    assert M.launch_args("a", W.COMPUTE_ONLY, ("module",))[-3] == wasm_runtime.PINS_SHA256


def test_compute_only_grant_has_no_read_or_scratch_flags() -> None:
    args = M.launch_args(M.SESSION_AGENT, W.COMPUTE_ONLY, ("module", "python", "javascript"), _HEX)
    assert "--read" not in args and "--scratch" not in args and args[5] == "_session"


@pytest.mark.parametrize("agent, grant, langs", [
    ("Bad", W.COMPUTE_ONLY, ("module",)), ("", W.COMPUTE_ONLY, ("module",)), ("a b", W.COMPUTE_ONLY, ("module",)),
    ("a", W.COMPUTE_ONLY, ()), ("a", W.COMPUTE_ONLY, ("module", "module")), ("a", W.COMPUTE_ONLY, ("rust",)),
    (M.SESSION_AGENT, WIDE, ("module",)),
    ("a", _grant(limits=W.Limits(W.DEFAULT_LIMITS.fuel, W.DEFAULT_LIMITS.max_memory_bytes, 241)), ("module",)),
])
def test_launch_args_are_validated(agent: str, grant: W.Grant, langs: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        M.launch_args(agent, grant, langs, _HEX)


def test_grant_digest_matches_the_servers() -> None:
    srv = _server_module()
    lim = WIDE.limits
    assert M.grant_digest(WIDE) == srv.grant_digest(["src", "data"], True, lim.fuel, lim.max_memory_bytes,
                                                    lim.timeout_s)
    assert M.grant_digest(W.COMPUTE_ONLY) != M.grant_digest(WIDE)
    assert M.grant_parts(WIDE) == (["data", "src"], True)


def test_state_launch_helpers() -> None:
    off = SandboxState(os=False, os_reason="unknown", wasm=False, wasm_reason="default-off", wasm_languages=())
    assert M.agent_launch_args(off, "analyst", _HEX) is None and M.session_launch_args(off, _HEX) is None
    on = SandboxState(os=True, os_reason="cli", wasm=True, wasm_reason="cli", wasm_languages=("module", "python"),
                      wasm_grants={"analyst": WIDE})
    assert M.agent_launch_args(on, "analyst", _HEX) == M.launch_args("analyst", WIDE, ("module", "python"), _HEX)
    assert M.agent_launch_args(on, "other", _HEX) == M.launch_args("other", W.COMPUTE_ONLY, ("module", "python"),
                                                                   _HEX)
    assert "--read" not in M.session_launch_args(on, _HEX)


def test_claude_entry_goose_extension_table_entry_shapes() -> None:
    args = M.launch_args("analyst", W.COMPUTE_ONLY, ("module",), _HEX)
    assert M.claude_entry(args, "/usr/bin/python3") == (
        "  - agentteams_wasm:\n      type: stdio\n      command: \"/usr/bin/python3\"\n"
        f"      args: [{', '.join(json.dumps(a) for a in args)}]\n")
    assert M.goose_extension(args, "/usr/bin/python3") == {
        "type": "stdio", "name": "agentteams_wasm", "cmd": "/usr/bin/python3", "args": args, "env_keys": [],
        "timeout": 300, "available_tools": ["run_wasm"]}
    assert M.table_entry(args, "/usr/bin/python3") == {"command": "/usr/bin/python3", "args": args}
    assert M.tool_names() == ["mcp__agentteams_wasm__run_wasm"] and M.GOOSE_TIMEOUT > M.MAX_TIMEOUT_S + 15


# --- install --------------------------------------------------------------------------------------------------

def test_install_files_empty_when_wasm_off() -> None:
    assert M.install_files({}) == [] and M.install_binary_files({}) == []
    assert M.install_files({"_wasm_sandbox_cli": "off"}) == []


def test_install_files_is_server_plus_bundle_no_nest_json() -> None:
    files = M.install_files({"_wasm_sandbox_cli": "on"})
    paths = [p for p, _ in files]
    assert paths == [f"../../{M.PROTECTED_PATH}", *(f"../../{p}" for p, _ in M.BUNDLE if p not in M.BINARY_FILES)]
    assert not any("nest" in p for p in paths)
    assert files[0][1] == M.server_content()
    assert M.install_binary_files({"_wasm_sandbox_cli": "on"}) == [
        (f"../../{M.BIN_DIR}/wasm_deny_probe.wasm", (PKG / "data" / "wasm_deny_probe.wasm").read_bytes())]


def test_install_refuses_a_mismatched_packaged_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(M, "SHA256", "0" * 64)
    with pytest.raises(ValueError, match="pinned hash"):
        M.install_files({"_wasm_sandbox_cli": "on"})


@pytest.mark.parametrize("installed", sorted(M.BUNDLE_SHA256))
def test_install_refuses_a_mismatched_bundle_file(monkeypatch: pytest.MonkeyPatch, installed: str) -> None:
    monkeypatch.setattr(M, "BUNDLE_SHA256", {**M.BUNDLE_SHA256, installed: "0" * 64})
    with pytest.raises(ValueError, match="bundle pin"):
        M.bundle_files()


def test_install_refuses_mismatched_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(M, "pins_sha256", lambda: "0" * 64)
    with pytest.raises(ValueError, match="bundle pin"):
        M.install_files({"_wasm_sandbox_cli": "on"})


def test_write_bundle_is_byte_exact_and_refuses_symlinks(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path)) / "p"
    root.mkdir()
    written = M.write_bundle(root)
    assert written == [M.PROTECTED_PATH, *(p for p, _ in M.BUNDLE)]
    for rel, data in M.bundle_files():
        assert (root / rel).read_bytes() == data
    assert M.write_bundle(root) == written   # idempotent replace
    other = Path(os.path.realpath(tmp_path)) / "q"
    (other / "elsewhere").mkdir(parents=True)
    os.symlink(other / "elsewhere", other / ".agentteams")
    with pytest.raises(ValueError, match="symlink"):
        M.write_bundle(other)
    with pytest.raises(ValueError, match="directly under"):
        M.write_bundle(root, [(".agentteams/x.py", b"")])


# --- wiring ---------------------------------------------------------------------------------------------------

_ON = {"_wasm_sandbox_cli": "on", "agent_slug_list": ["analyst"]}


def _claude_settings(root: Path, manifest: dict) -> None:
    from agentteams.frameworks import _control_plane_protect as cpp

    control = cpp.control_plane_for(manifest)
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"deny": cpp.permission_rules(False, control)},
        "sandbox": {"enabled": True, "filesystem": {"denyWrite": list(cpp.write_denies(False, control))}}}))


def test_wiring_problems_off_is_empty(tmp_path: Path) -> None:
    assert M.wiring_problems(tmp_path, "claude", {}, home=tmp_path) == []


def test_wiring_problems_reports_install_shadowing_and_unprotected_control_plane(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    probs = M.wiring_problems(root, "claude", _ON, home=root)
    assert any(M.PROTECTED_PATH in p and "missing" in p for p in probs)
    assert any("WASM runtime wasmtime" in p for p in probs)
    assert "WASM grant integrity: advisory (control plane is agent-writable: the OS sandbox is off)" in probs
    M.write_bundle(root)
    (root / M.BIN_DIR / "__pycache__").mkdir()
    (root / M.BIN_DIR / "wasm_exec.py").write_bytes(b"# tampered\n")
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentteams_wasm": {"command": "x"}}}))
    probs = M.wiring_problems(root, "claude", _ON, home=root)
    assert any("wasm_exec.py does not match" in p for p in probs)
    assert any("__pycache__ is bytecode" in p for p in probs)
    assert any(".mcp.json defines an MCP server named agentteams_wasm" in p for p in probs)
    assert not any(M.PROTECTED_PATH in p for p in probs)
    (root / M.PROTECTED_PATH).write_bytes(b"# edited server\n")
    probs = M.wiring_problems(root, "claude", _ON, home=root)
    edited = [p for p in probs if M.PROTECTED_PATH in p]
    # only this check catches an edited server: it would still start, so the message must not claim it refuses
    assert len(edited) == 1 and "cannot detect this itself" in edited[0] and "refuses to start" not in edited[0]


def test_control_plane_advisory_names_the_reason(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    os_on = {**_ON, "_os_sandbox_cli": "on"}
    assert "the OS sandbox is off" in M.control_plane_advisory(root, "claude", _ON)
    assert "settings.json is missing" in M.control_plane_advisory(root, "claude", os_on)
    _claude_settings(root, os_on)
    assert M.control_plane_advisory(root, "claude", os_on) is None
    data = json.loads((root / ".claude" / "settings.json").read_text())
    data["permissions"]["deny"].remove("Edit(/.claude/agents/**)")
    (root / ".claude" / "settings.json").write_text(json.dumps(data))
    assert "Edit(/.claude/agents/**)" in M.control_plane_advisory(root, "claude", os_on)
    _claude_settings(root, os_on)
    (root / ".claude" / "settings.local.json").write_text(json.dumps({"sandbox": {"enabled": False}}))
    assert "settings.local.json" in M.control_plane_advisory(root, "claude", os_on)
    (root / ".claude" / "settings.local.json").write_text("{not json")   # CH-24: malformed is not "absent"
    assert "settings.local.json (it is unreadable or not JSON" in M.control_plane_advisory(root, "claude", os_on)
    (root / ".claude" / "settings.local.json").unlink()
    assert M.control_plane_advisory(root, "claude", os_on) is None
    assert "grok" in M.control_plane_advisory(root, "grok", os_on)
    assert "codex" in M.control_plane_advisory(root, "codex", os_on)
    assert "sandbox.sb is missing" in M.control_plane_advisory(root, "goose", os_on)


def test_runner_shadowing_default_unchanged(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"agentteams_runner": {}, "agentteams_wasm": {}}}))
    assert runner_mcp._shadowing(tmp_path, tmp_path) == [".mcp.json defines an MCP server named agentteams_runner"]
    assert runner_mcp._shadowing(tmp_path, tmp_path, "agentteams_wasm") == [
        ".mcp.json defines an MCP server named agentteams_wasm"]
