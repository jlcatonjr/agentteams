"""WASM plan P11: per-framework ``agentteams_wasm`` wiring (codex, goose, grok, copilot, agents-md).

* codex: one compute-only ``_session`` table with ``prompt`` approval, spliced without ``codex:mcp``; the
  first-party hook accepts exactly that table (Rule-12 carve-out 3) and refuses to drop an operator's tables when
  ``codex:mcp`` is off (carve-out 4); a widened grant is refused, never downgraded.
* goose: grant mode carries the exact extension per eligible recipe, with the OS sandbox on or off (operator spike
  B: Goose runs MCP servers outside its sandbox); legacy scoping is ``not-wired``; a delegate cannot add it.
* grok: ``unverified-format`` and nothing emitted. copilot-vscode / copilot-cli: inert examples only (no live
  ``.vscode/mcp.json`` / ``.github/mcp.json``). agents-md: instructions only.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from agentteams import analyze, wasm_exec, wasm_mcp
from agentteams import codex_mcp_emit as ce
from agentteams.frameworks import _wasm_wiring as ww
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.sandbox_state import OS_SANDBOX_CLI_KEY, SandboxState, effective_sandbox_state
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
SHELL = "---\nname: Nav\ndescription: \"d\"\ntools: ['read', 'search', 'execute']\n---\n\nbody\n"


def _manifest(framework: str, *, os_on: bool | None = None, **brief: object) -> dict:
    manifest = analyze.build_manifest({**BRIEF, **brief}, framework=framework)
    manifest[WASM_SANDBOX_CLI_KEY] = "on"
    if os_on is not None:
        manifest[OS_SANDBOX_CLI_KEY] = "on" if os_on else "off"
    return manifest


# --- codex ---------------------------------------------------------------------------------------------------------

def test_codex_shared_table_is_compute_only_with_prompt_approval(tmp_path):
    manifest = _manifest("codex")
    tables = ww.codex_first_party_tables(manifest)
    assert list(tables) == ["mcp_servers.agentteams_wasm"]
    entry = tables["mcp_servers.agentteams_wasm"]
    assert entry["default_tools_approval_mode"] == "prompt"
    assert entry["args"] == wasm_mcp.session_launch_args(effective_sandbox_state(manifest))
    assert "--read" not in entry["args"] and "--scratch" not in entry["args"]
    res = ce.emit_codex_mcp_config(servers=[], features=[], output_root=tmp_path, first_party_tables=tables)
    assert res.success and res.wired == ["agentteams_wasm"] and not res.gated_off
    data = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert data["mcp_servers"]["agentteams_wasm"] == entry
    assert ww.CODEX_CONFIG in manifest[ww.WIRING_KEY]["protected_config_paths"]


def test_codex_tables_only_for_codex_renders_and_wasm_on():
    assert ww.codex_first_party_tables(_manifest("claude")) is None
    off = analyze.build_manifest(BRIEF, framework="codex")
    assert ww.codex_first_party_tables(off) is None and ww.WIRING_KEY not in off


@pytest.mark.parametrize("mutate", [
    lambda t: {"mcp_servers.other": t["mcp_servers.agentteams_wasm"]},
    lambda t: {"mcp_servers.agentteams_wasm": {**t["mcp_servers.agentteams_wasm"], "env": {"A": "b"}}},
    lambda t: {"mcp_servers.agentteams_wasm": {**t["mcp_servers.agentteams_wasm"],
                                               "default_tools_approval_mode": "auto"}},
    lambda t: {"mcp_servers.agentteams_wasm": {**t["mcp_servers.agentteams_wasm"],
                                               "args": [*t["mcp_servers.agentteams_wasm"]["args"], "--read", "src"]}},
    lambda t: {"mcp_servers.agentteams_wasm": {**t["mcp_servers.agentteams_wasm"], "args": ["evil.py"]}},
])
def test_codex_first_party_hook_admits_nothing_else(tmp_path, mutate):
    tables = ww.codex_first_party_tables(_manifest("codex"))
    with pytest.raises(ValueError):
        ce.emit_codex_mcp_config(servers=[], features=[], output_root=tmp_path, first_party_tables=mutate(tables))
    assert not (tmp_path / ".codex").exists()


def test_codex_without_mcp_feature_refuses_to_drop_operator_tables(tmp_path):
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('model = "x"\n\n[mcp_servers.mine]\ncommand = "mine"\n', encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    tables = ww.codex_first_party_tables(_manifest("codex"))
    res = ce.emit_codex_mcp_config(servers=[], features=[], output_root=tmp_path, first_party_tables=tables)
    assert not res.success and "mine" in res.errors[0] and cfg.read_text(encoding="utf-8") == before


def test_codex_with_mcp_feature_keeps_declared_operator_servers(tmp_path):
    server = {"artifact_type": "mcp-server", "mcp_server_schema_version": "1.0", "server_id": "example-pg",
              "domain": "data-access", "trust_tier": "first-party", "transport": "stdio", "command": "npx",
              "auth": {"mechanism": "none"}, "tools": [{"name": "q", "side_effects": "read"}], "scope": ["x"],
              "progressive_disclosure": "lazy", "security_review": {"required": False}}
    tables = ww.codex_first_party_tables(_manifest("codex"))
    res = ce.emit_codex_mcp_config(servers=[server], features=["codex:mcp"], output_root=tmp_path,
                                   first_party_tables=tables)
    data = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert res.success and set(data["mcp_servers"]) == {"example-pg", "agentteams_wasm"}


def test_codex_without_first_party_tables_is_unchanged(tmp_path):
    res = ce.emit_codex_mcp_config(servers=[], features=[], output_root=tmp_path)
    assert res.gated_off and not (tmp_path / ".codex").exists()


def test_shared_hosts_refuse_a_widened_grant(capsys):
    widened = wasm_exec.Grant(mounts=(wasm_exec.Mount(host="/x", guest="/project/src", mode="ro"),),
                              limits=wasm_exec.DEFAULT_LIMITS) if hasattr(wasm_exec, "Mount") else None
    if widened is None:
        pytest.skip("wasm_exec.Mount is not available")
    state = SandboxState(os=True, os_reason="brief", wasm=True, wasm_reason="cli", wasm_languages=("python",),
                         wasm_grants={"navigator": widened})
    for framework in ("codex", "copilot-vscode", "copilot-cli"):
        with pytest.raises(ValueError, match="compute-only"):
            ww.print_report(_manifest(framework), framework, state)
    ww.print_report(_manifest("claude"), "claude", state)  # per-agent copies honour it
    assert "[WASM-WIRING] claude: wired-per-agent" in capsys.readouterr().err


# --- goose ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("os_on", [False, True])
def test_goose_grant_mode_carries_the_exact_extension(os_on):
    manifest = _manifest("goose", os_on=os_on, goose_tool_scoping="grant")
    recipe = FRAMEWORKS["goose"]().render_agent_file(SHELL, "navigator", manifest)
    args = wasm_mcp.agent_launch_args(effective_sandbox_state(manifest), "navigator")
    assert 'name: "agentteams_wasm"' in recipe and "available_tools: [run_wasm]" in recipe.replace('"', "")
    assert all(json.dumps(a) in recipe for a in args)
    row = manifest[ww.WIRING_KEY]["frameworks"]["goose"]
    assert row["status"] == "wired-per-agent" and row["agents"] == ["navigator"]
    assert "outside its own session sandbox" in row["reason"]  # never an enforcement claim
    assert ".goose/recipes/navigator.yaml" in manifest[ww.WIRING_KEY]["protected_config_paths"]
    extras = dict(FRAMEWORKS["goose"]().extra_output_files(manifest))
    assert f"../../{wasm_mcp.PROTECTED_PATH}" in extras


@pytest.mark.parametrize("os_on", [False, True])
def test_goose_legacy_scoping_is_not_wired(os_on):
    reason = "legacy"
    manifest = _manifest("goose", os_on=os_on)
    recipe = FRAMEWORKS["goose"]().render_agent_file(SHELL, "navigator", manifest)
    row = ww.wiring_row(manifest, "goose")
    assert "agentteams_wasm" not in recipe and row["status"] == "not-wired" and reason in row["reason"]
    assert not row["verified"] and not ww.output_files(manifest, "goose")


def test_goose_delegate_cannot_add_the_wasm_copy():
    from agentteams.frameworks.goose_recipe_grants import recipe_extras

    manifest = _manifest("goose", os_on=False, goose_tool_scoping="grant")
    extras, _ = recipe_extras(manifest, "navigator", frozenset({"read", "search", "execute"}))
    assert [e["name"] for e in extras if e["name"] == "agentteams_wasm"] == ["agentteams_wasm"]
    assert not [e for e in recipe_extras(manifest, "navigator")[0] if e["name"] == "agentteams_wasm"]
    from agentteams.frameworks.goose_tool_scoping import delegate_extensions

    names, withheld = delegate_extensions(frozenset({"read", "search", "execute"}), ("agentteams_wasm",))
    assert "agentteams_wasm" not in names and "agentteams_wasm (delegate cannot add it)" in withheld


# --- grok, copilot, agents-md --------------------------------------------------------------------------------------

def test_grok_is_unverified_and_emits_nothing():
    manifest = _manifest("grok")
    agent = FRAMEWORKS["grok"]().render_agent_file(SHELL, "navigator", manifest)
    extras = FRAMEWORKS["grok"]().extra_output_files(manifest)
    row = manifest[ww.WIRING_KEY]["frameworks"]["grok"]
    assert row["status"] == "unverified-format" and row["verified"] is False and not row["config_paths"]
    assert "agentteams_wasm" not in agent and not any(ww.LAUNCH_MARKER in text for _, text in extras)
    assert "wired" not in ww.report_line(manifest, "grok").split(":")[1].split("(")[0].replace("unverified", "")


@pytest.mark.parametrize("framework, path, key, kind", [
    ("copilot-vscode", f"../../{ww.VSCODE_EXAMPLE}", "servers", "stdio"),
    ("copilot-cli", f"../../{ww.COPILOT_CLI_EXAMPLE}", "mcpServers", "local"),
])
def test_copilot_examples_are_inert_and_protected(framework, path, key, kind):
    manifest = _manifest(framework)
    extras = dict(FRAMEWORKS[framework]().extra_output_files(manifest))
    example = json.loads(extras[path])
    entry = example[key]["agentteams_wasm"]
    assert entry["type"] == kind and "--agent" in entry["args"]
    assert entry["args"][entry["args"].index("--agent") + 1] == wasm_mcp.SESSION_AGENT
    assert "_comment" in example
    assert not any(p.endswith(("/mcp.json", "/.mcp.json")) for p in extras)
    assert path.removeprefix("../../") in manifest[ww.WIRING_KEY]["protected_config_paths"]
    agent = FRAMEWORKS[framework]().render_agent_file(SHELL, "navigator", manifest)
    assert "agentteams_wasm" not in agent  # run_wasm is not added to agent tools (spelling unverified)
    assert manifest[ww.WIRING_KEY]["frameworks"][framework]["status"] == "example-only"


def test_agents_md_is_instructions_only():
    manifest = _manifest("agents-md")
    extras = FRAMEWORKS["agents-md"]().extra_output_files(manifest)
    assert not any(ww.LAUNCH_MARKER in text for _, text in extras)
    assert manifest[ww.WIRING_KEY]["frameworks"]["agents-md"]["status"] == "instructions-only"


def test_unverified_and_not_wired_rows_become_advisories(capsys):
    manifest = _manifest("grok")
    ww.print_report(manifest, "grok", effective_sandbox_state(manifest))
    assert "[WASM-WIRING] grok: unverified-format [unverified]" in capsys.readouterr().err
    assert manifest["advisories"][-1]["code"] == "wasm-wiring-unverified-format"


def test_binary_probe_is_installed_where_the_server_was_emitted(tmp_path):
    manifest = _manifest("claude")
    with pytest.raises(ValueError):
        ww.install_binary(manifest, tmp_path)  # nothing emitted yet: refuse rather than guess
    (tmp_path / ".agentteams" / "bin").mkdir(parents=True)
    (tmp_path / wasm_mcp.PROTECTED_PATH).write_text(wasm_mcp.server_content(), encoding="utf-8")
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    with pytest.raises(ValueError, match="not found under"):
        ww.install_binary(manifest, agents)  # never searches a parent directory (@security low)
    assert ww.install_root("claude", agents) == tmp_path  # --output named the canonical agents dir
    assert ww.install_root("claude", tmp_path) == tmp_path
    assert ww.install_binary(manifest, ww.install_root("claude", agents)) == list(wasm_mcp.BINARY_FILES)
    data = (tmp_path / wasm_mcp.BINARY_FILES[0]).read_bytes()
    assert data == wasm_mcp.bundle_content(wasm_mcp.BINARY_FILES[0]) and data[:4] == b"\0asm"
    assert ww.install_binary(_manifest("grok"), tmp_path) == []


def test_check_wiring_problems_only_where_the_server_is_installed(tmp_path):
    assert ww.check_wiring_problems(tmp_path, "grok", _manifest("grok")) == []
    assert ww.check_wiring_problems(tmp_path, "agents-md", _manifest("agents-md")) == []
    problems = ww.check_wiring_problems(tmp_path, "claude", _manifest("claude"))
    assert any(wasm_mcp.PROTECTED_PATH in p for p in problems)  # not installed here: reported, never passed
    assert ww.check_wiring_problems(tmp_path, "claude", analyze.build_manifest(BRIEF, framework="claude")) == []
