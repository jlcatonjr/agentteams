"""WASM plan P11 review conditions (@security C1/C2 + lows, @code-hygiene H1/H2): the launching-config disk scan.

* C1: ``--check-wiring`` / ``wasm_mcp.control_plane_advisory`` / ``claude.verify_sandbox_wiring`` scan the disk
  whatever the WASM switch; with WASM off any launching config is reported as not protected (a team rendered with
  ``--wasm-sandbox on`` and checked from a brief without it, end to end).
* C2 / H2: the scan is not fooled. Symlinked files and directories, unparseable and non-regular files, and trees past
  the walk bounds are reported, never skipped; JSON (JSONC too) and TOML are parsed and matched by their launch keys,
  YAML is decoded, so ``\\u`` / ``\\x`` escapes and escaped line breaks cannot hide the server; Goose ``*.yml`` /
  ``*.json`` recipes in nested dirs are covered.
* Lows: the Goose profile check parses the deny rules (comments and allow rules never count); Codex's approval mode
  is worded as a configured default; with WASM on a non-canonical agents dir is refused.
* H1: the CLI binary install downgrades only an ``OSError`` to a warning.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams import analyze, wasm_mcp
from agentteams.frameworks import _wasm_wiring as ww
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY

REPO = Path(__file__).resolve().parent.parent
BRIEF_PATH = REPO / "examples" / "software-project" / "brief.json"
BRIEF = json.loads(BRIEF_PATH.read_text(encoding="utf-8"))
SERVER = wasm_mcp.PROTECTED_PATH


def _manifest(framework: str = "claude", *, wasm: bool = True, **brief: object) -> dict:
    manifest = analyze.build_manifest({**BRIEF, **brief}, framework=framework)
    if wasm:
        manifest[WASM_SANDBOX_CLI_KEY] = "on"
    return manifest


def _write(root: Path, rel: str, text: str) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return rel


def _entry(arg: str) -> str:
    return json.dumps({"mcpServers": {"x": {"command": "python3", "args": ["-I", arg]}}})


# --- C1: the scan runs whatever the WASM switch -------------------------------------------------------------------


def test_wasm_off_reports_every_launching_config(tmp_path):
    rel = _write(tmp_path, ".mcp.json", _entry(SERVER))
    off = _manifest(wasm=False)
    for problems in (ww.check_wiring_problems(tmp_path, "claude", off),
                     wasm_mcp.wiring_problems(tmp_path, "claude", off),
                     ww.check_wiring_problems(tmp_path, "goose", _manifest("goose", wasm=False))):
        assert len(problems) == 1 and problems[0].startswith(rel) and "WASM is off for this team" in problems[0]
    advisory = wasm_mcp.control_plane_advisory(tmp_path, "claude", off)
    assert advisory and rel in advisory and "WASM is off" in advisory
    assert ww.check_wiring_problems(tmp_path / "empty", "claude", off) == []   # nothing on disk: nothing to say


def test_wasm_on_but_nothing_installed_here_reports_launching_configs(tmp_path):
    rel = _write(tmp_path, ".mcp.json", _entry(SERVER))
    problems = ww.check_wiring_problems(tmp_path, "grok", _manifest("grok"))
    assert len(problems) == 1 and problems[0].startswith(rel) and "grok does not wire agentteams_wasm" in problems[0]


def test_verify_sandbox_wiring_reports_a_launching_config_with_wasm_off(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring

    front = f"---\nmcpServers:\n  - a:\n      args: [\"{SERVER}\"]\n---\n"
    rel = _write(tmp_path, ".claude/agents/navigator.md", front)
    ok, msgs = verify_sandbox_wiring(tmp_path, _manifest(wasm=False))
    assert not ok and any(m.startswith(f"WASM LAUNCH CONFIG NOT PROTECTED: {rel}") for m in msgs)
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "confined", "os_sandbox": "off"})  # drift path
    assert not ok and any("WASM LAUNCH CONFIG NOT PROTECTED" in m for m in msgs)


def test_rendered_with_wasm_on_then_checked_from_a_brief_without_it(tmp_path, capsys):
    import build_team
    from tests._fresh_intel import seed_offline_intel

    root = tmp_path / "proj"
    root.mkdir()
    # Ungoverned, so the WASM on -> off relaxation gate admits the check and the disk scan is what reports.
    (root / "brief.json").write_text(json.dumps({**BRIEF, "enforce_decision_signing": False}), encoding="utf-8")
    out = root / ".claude" / "agents"
    extra = seed_offline_intel(out, "claude")
    base = ["--description", str(root / "brief.json"), "--framework", "claude", "--project", str(root)]
    assert build_team.main([*base, "--yes", "--no-scan", "--wasm-sandbox", "on", *extra]) == 0
    assert ww.scan_launching_configs(root).launching   # the render wrote launching configs
    capsys.readouterr()
    assert build_team.main([*base, "--check-wiring"]) == 1
    out_text = capsys.readouterr().out
    assert "WASM is off for this team, so nothing write-protects it" in out_text
    assert "WASM LAUNCH CONFIG NOT PROTECTED" in out_text


# --- C2 / H2: the scan is not fooled ------------------------------------------------------------------------------


def test_escapes_cannot_hide_the_server(tmp_path):
    _write(tmp_path, ".mcp.json", _entry(SERVER).replace(".py", "\\u002epy"))
    assert SERVER not in (tmp_path / ".mcp.json").read_text()
    _write(tmp_path, ".vscode/mcp.json", '{\n  // JSONC comment\n  "servers": {"a": {"command": "py", '
           f'"args": ["{SERVER}",],}},}},\n}}\n')
    _write(tmp_path, ".codex/config.toml", '[mcp_servers.a]\ncommand = "py"\nargs = ["'
           + SERVER.replace("mcp", "\\u006dcp") + '"]\n')
    _write(tmp_path, ".goose/recipes/hex.yaml", 'extensions:\n  - name: a\n    cmd: py\n    args: ["'
           + SERVER.replace("agentteams-wasm", "\\x61gentteams-wasm") + '"]\n')
    _write(tmp_path, ".goose/recipes/fold.yaml", 'extensions:\n  - name: a\n    args: ["'
           + SERVER.replace("wasm-mcp", "wasm-\\\n      mcp") + '"]\n')
    _write(tmp_path, ".claude/agents/esc.md", '---\nmcpServers:\n  - a:\n      args: ["'
           + SERVER.replace("w", "\\U00000077") + '"]\n---\n')
    _write(tmp_path, ".claude/settings.json", json.dumps({"permissions": {"deny": [f"Edit(/{SERVER})"]}}))
    scan = ww.scan_launching_configs(tmp_path)
    assert scan.launching == (".claude/agents/esc.md", ".codex/config.toml", ".goose/recipes/fold.yaml",
                              ".goose/recipes/hex.yaml", ".mcp.json", ".vscode/mcp.json")
    assert scan.unsafe == ()   # a deny rule naming the server is not a launch


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks")
def test_symlinked_files_and_dirs_are_reported_not_skipped(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "x.md").write_text("plain\n")
    (target / "mcp.json").write_text(_entry(SERVER))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "agents").symlink_to(target, target_is_directory=True)
    (tmp_path / ".vscode").mkdir()
    (tmp_path / ".vscode" / "mcp.json").symlink_to(target / "mcp.json")
    (tmp_path / ".goose" / "recipes").mkdir(parents=True)
    (tmp_path / ".goose" / "recipes" / "linked").symlink_to(target, target_is_directory=True)
    unsafe = dict(ww.scan_launching_configs(tmp_path).unsafe)
    assert unsafe == {".claude/agents": "is a symlinked directory", ".vscode/mcp.json": "is a symlink",
                      ".goose/recipes/linked": "is a symlinked directory"}
    assert set(unsafe) <= set(ww.launching_config_paths(tmp_path))   # H2: returned, so callers must protect them
    problems = ww.unprotected_config_problems(tmp_path)
    assert len(problems) == 3 and all("not protected" in p for p in problems)
    # Even with WASM on and the live denies covering every path, an uncheckable config is never protected.
    advisory = wasm_mcp.control_plane_advisory(tmp_path, "claude", _manifest(privilege_profile="confined"))
    assert advisory and ".vscode/mcp.json is a symlink" in advisory


def test_unparseable_and_non_regular_files_are_reported(tmp_path):
    _write(tmp_path, ".vscode/settings.json", "{not json")
    _write(tmp_path, ".codex/config.toml", "[unterminated\n")
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "bad.md").write_bytes(b"\xff\xfe not utf-8")
    if hasattr(os, "mkfifo"):
        os.mkfifo(tmp_path / ".mcp.json")   # never opened blocking, never read as empty
    unsafe = dict(ww.scan_launching_configs(tmp_path).unsafe)
    assert {".vscode/settings.json", ".codex/config.toml", ".claude/agents/bad.md"} <= set(unsafe)
    assert all("unreadable or does not parse" in why for why in unsafe.values())
    if hasattr(os, "mkfifo"):
        assert ".mcp.json" in unsafe


def test_goose_yml_json_and_nested_recipes_are_scanned_within_bounds(tmp_path, monkeypatch):
    _write(tmp_path, ".goose/recipes/a.yml", f"extensions:\n  - cmd: py\n    args: [{SERVER}]\n")
    _write(tmp_path, ".goose/recipes/team/b.json", json.dumps({"extensions": [{"cmd": "py", "args": [SERVER]}]}))
    _write(tmp_path, ".goose/recipes/team/sub/c.yaml", f"args: [{SERVER}]\n")
    _write(tmp_path, ".goose/recipes/references/build-log.json", json.dumps({"files": [SERVER]}))  # not a launch
    scan = ww.scan_launching_configs(tmp_path)
    assert scan.launching == (".goose/recipes/a.yml", ".goose/recipes/team/b.json", ".goose/recipes/team/sub/c.yaml")
    assert scan.unsafe == ()
    deep = ".goose/recipes/" + "/".join(f"d{i}" for i in range(ww.SCAN_MAX_DEPTH)) + "/x.yaml"
    _write(tmp_path, deep, "plain\n")
    unsafe = dict(ww.scan_launching_configs(tmp_path).unsafe)
    assert any("scan bound" in why for why in unsafe.values()), unsafe
    monkeypatch.setattr(ww, "SCAN_MAX_FILES", 2)
    assert dict(ww.scan_launching_configs(tmp_path).unsafe).get(".goose/recipes", "").startswith("holds more than 2")


# --- lows ---------------------------------------------------------------------------------------------------------


def _goose_profile(root: Path, body: str) -> None:
    _write(root, ".goose/sandbox.sb", "(version 1)\n(allow default)\n(deny file-write*)\n"
           '(allow file-write* (subpath (param "WORKSPACE_ROOT")))\n' + body)


def test_goose_profile_check_parses_the_deny_rules(tmp_path):
    from agentteams.frameworks import _control_plane_protect as cpp

    manifest = _manifest("goose", privilege_profile="confined")
    denies = cpp.seatbelt_denies(False, cpp.control_plane_for(manifest))
    exprs = "\n".join(f'    (subpath (string-append (param "WORKSPACE_ROOT") "/{p}"))' for p in denies)
    _goose_profile(tmp_path, f"(deny file-write*\n{exprs})\n")
    assert wasm_mcp.control_plane_advisory(tmp_path, "goose", manifest) is None
    commented = "\n".join(f';; (deny file-write* (subpath "/r/{p}"))' for p in denies)
    _goose_profile(tmp_path, commented + "\n")
    assert "does not deny writes to" in (wasm_mcp.control_plane_advisory(tmp_path, "goose", manifest) or "")
    _goose_profile(tmp_path, f"(allow file-write*\n{exprs})\n")   # an allow rule is not a deny
    assert "does not deny writes to" in (wasm_mcp.control_plane_advisory(tmp_path, "goose", manifest) or "")
    _goose_profile(tmp_path, f"(deny file-write*\n{exprs})\n(allow file-write* (regex #\"^/\"))\n")  # re-opened later
    assert "does not deny writes to" in (wasm_mcp.control_plane_advisory(tmp_path, "goose", manifest) or "")
    _goose_profile(tmp_path, "(deny file-write* (subpath \"/r/.agentteams\")")
    assert "does not parse" in (wasm_mcp.control_plane_advisory(tmp_path, "goose", manifest) or "")


def test_codex_approval_mode_is_a_configured_default_not_enforcement():
    reason = ww.wiring_row(_manifest("codex"), "codex")["reason"]
    assert "every call prompts" not in reason
    assert 'default_tools_approval_mode = "prompt"' in reason and "not enforcement" in reason


def test_wasm_on_refuses_a_non_canonical_agents_dir(tmp_path):
    from agentteams.cli.wasm_sandbox_switch import report_wasm_wiring
    from agentteams.sandbox_state import effective_sandbox_state

    for framework, canonical in (("claude", ".claude/agents"), ("goose", ".goose/recipes"),
                                 ("codex", ".codex/agents")):
        manifest = _manifest(framework)
        assert ww.agents_dir_problem(manifest, framework, tmp_path / canonical) is None
        problem = ww.agents_dir_problem(manifest, framework, tmp_path / "custom")
        assert problem and canonical in problem
        assert ww.agents_dir_problem(_manifest(framework, wasm=False), framework, tmp_path / "custom") is None
    manifest = _manifest()
    with pytest.raises(ValueError, match=r"\.claude/agents"):
        report_wasm_wiring(manifest, effective_sandbox_state(manifest), "claude", team_dir=tmp_path / "custom")


def test_cli_render_into_a_custom_output_with_wasm_on_is_refused(tmp_path, capsys):
    import build_team

    out = tmp_path / "custom-agents"
    rc = build_team.main(["--description", str(BRIEF_PATH), "--framework", "claude", "--output", str(out),
                          "--yes", "--no-scan", "--wasm-sandbox", "on"])
    assert rc == 1 and ".claude/agents" in capsys.readouterr().err
    assert not (out / ".." / ".agentteams").exists()


# --- H1 -----------------------------------------------------------------------------------------------------------


def test_cli_binary_install_raises_value_errors_and_warns_only_on_os_errors(tmp_path, monkeypatch, capsys):
    from agentteams.cli import wasm_sandbox_switch as sw

    manifest = _manifest()
    with pytest.raises(ValueError, match="not found under"):
        sw.install_wasm_binary(manifest, tmp_path)   # server-not-found is never a warning

    def pin_mismatch(*_a, **_k):
        raise ValueError("pin mismatch")

    def disk_full(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(ww, "install_binary", pin_mismatch)
    with pytest.raises(ValueError, match="pin mismatch"):
        sw.install_wasm_binary(manifest, tmp_path)
    monkeypatch.setattr(ww, "install_binary", disk_full)
    sw.install_wasm_binary(manifest, tmp_path)
    assert "disk full" in capsys.readouterr().err
