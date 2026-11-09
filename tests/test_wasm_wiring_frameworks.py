"""WASM plan P11: per-framework ``agentteams_wasm`` wiring (codex, goose, grok, copilot, agents-md).

* codex: one compute-only ``_session`` table with ``prompt`` approval, spliced without ``codex:mcp``; the
  first-party hook accepts exactly that table (Rule-12 carve-out 3) and refuses to drop an operator's tables when
  ``codex:mcp`` is off (carve-out 4); a widened grant is refused, never downgraded.
* goose: grant mode carries the exact extension per eligible recipe, with the OS sandbox on or off (operator spike
  B: Goose runs MCP servers outside its sandbox); legacy scoping is ``not-wired``; a delegate cannot add it.
* copilot-vscode / copilot-cli / grok: inert examples only (no live ``.vscode/mcp.json`` / ``.github/mcp.json``, no
  server table in ``.grok/config.toml``). agents-md: instructions only.
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


def _with_args(t, edit):
    entry = t["mcp_servers.agentteams_wasm"]
    return {"mcp_servers.agentteams_wasm": {**entry, "args": edit(list(entry["args"]))}}


def _insert_after_session(args, *extra):
    i = args.index(wasm_mcp.SESSION_AGENT) + 1
    return [*args[:i], *extra, *args[i:]]


@pytest.mark.parametrize("mutate", [
    # Row 659: spellings the old args[:6]-prefix + literal-membership check let through.
    lambda t: _with_args(t, lambda a: _insert_after_session(a, "--read=/")),
    lambda t: _with_args(t, lambda a: _insert_after_session(a, "--rea", "/")),
    lambda t: _with_args(t, lambda a: [*a, "--agent", "navigator"]),
    lambda t: _with_args(t, lambda a: _insert_after_session(a, "--scratch=/tmp")),
    lambda t: {"mcp_servers.agentteams_wasm": {**t["mcp_servers.agentteams_wasm"], "command": "/tmp/evil"}},
    lambda t: {"mcp_servers.agentteams_wasm": {**t["mcp_servers.agentteams_wasm"], "command": "python3"}},
], ids=["read-equals", "argparse-prefix", "second-agent", "scratch-equals", "foreign-command", "bare-command"])
def test_codex_first_party_hook_requires_the_exact_session_launch(tmp_path, mutate):
    tables = ww.codex_first_party_tables(_manifest("codex"))
    with pytest.raises(ValueError, match="canonical compute-only"):
        ce.emit_codex_mcp_config(servers=[], features=[], output_root=tmp_path, first_party_tables=mutate(tables))
    assert not (tmp_path / ".codex").exists()


def test_is_session_launch_accepts_the_canonical_table_and_rejects_bad_types():
    entry = ww.codex_first_party_tables(_manifest("codex"))["mcp_servers.agentteams_wasm"]
    assert wasm_mcp.is_session_launch(entry["command"], entry["args"])
    assert not wasm_mcp.is_session_launch(entry["command"], tuple(entry["args"]))
    assert not wasm_mcp.is_session_launch(entry["command"], [*entry["args"][:-1], 1])
    assert not wasm_mcp.is_session_launch(entry["command"], entry["args"][:7])
    assert not wasm_mcp.is_session_launch(None, entry["args"])


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

def test_grok_example_is_inert_and_protected():
    manifest = _manifest("grok")
    agent = FRAMEWORKS["grok"]().render_agent_file(SHELL, "navigator", manifest)
    extras = dict(FRAMEWORKS["grok"]().extra_output_files(manifest))
    example = extras[f"../../{ww.GROK_EXAMPLE}"]
    entry = tomllib.loads(example)["mcp_servers"]["agentteams_wasm"]
    assert set(entry) == {"command", "args"}  # Codex's default_tools_approval_mode is not a Grok key
    assert entry["args"][entry["args"].index("--agent") + 1] == wasm_mcp.SESSION_AGENT
    assert example.startswith("# ") and ".grok/config.toml" in example.splitlines()[0]
    assert not any(p.endswith("config.toml") and ww.LAUNCH_MARKER in text for p, text in extras.items())
    assert "agentteams_wasm" not in agent
    row = manifest[ww.WIRING_KEY]["frameworks"]["grok"]
    assert row["status"] == "example-only" and row["verified"] is True and row["grant_scope"] == "session-compute-only"
    assert ww.GROK_EXAMPLE in manifest[ww.WIRING_KEY]["protected_config_paths"]


def test_grok_example_header_names_the_profile_mode_exposure():
    """Row 648 gap 1 (B2): Grok's profile cannot deny a write without denying the read, so the example says where
    it is protected (inside the launcher) and where it is not (profile mode), and how to merge it safely."""
    manifest = _manifest("grok")
    example = dict(FRAMEWORKS["grok"]().extra_output_files(manifest))[f"../../{ww.GROK_EXAMPLE}"]
    header = "\n".join(line for line in example.splitlines() if line.startswith("# "))
    for needle in ("confined-run.example.sh", "GROK_CONFINE_MODE=profile", ".grok/config.toml",
                   "fresh render", "diff"):
        assert needle in header, needle
    tomllib.loads(example)  # the header stays TOML comments
    reason = manifest[ww.WIRING_KEY]["frameworks"]["grok"]["reason"]
    assert "write-protected only inside the agentteams launcher" in reason


def test_scan_finds_the_grok_example(tmp_path):
    manifest = _manifest("grok")
    example = dict(FRAMEWORKS["grok"]().extra_output_files(manifest))[f"../../{ww.GROK_EXAMPLE}"]
    (tmp_path / ".grok").mkdir()
    (tmp_path / ww.GROK_EXAMPLE).write_text(example, encoding="utf-8")
    assert ww.launching_config_paths(tmp_path) == (ww.GROK_EXAMPLE,)


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
    manifest = _manifest("goose", goose_tool_scoping="legacy")
    ww.print_report(manifest, "goose", effective_sandbox_state(manifest))
    assert "[WASM-WIRING] goose: not-wired [unverified]" in capsys.readouterr().err
    assert manifest["advisories"][-1]["code"] == "wasm-wiring-not-wired"


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
    assert ww.install_binary(_manifest("agents-md"), tmp_path) == []


def test_check_wiring_problems_only_where_the_server_is_installed(tmp_path):
    assert ww.check_wiring_problems(tmp_path, "agents-md", _manifest("agents-md")) == []
    for framework in ("claude", "grok"):
        problems = ww.check_wiring_problems(tmp_path, framework, _manifest(framework))
        assert any(wasm_mcp.PROTECTED_PATH in p for p in problems)  # not installed here: reported, never passed
    assert ww.check_wiring_problems(tmp_path, "claude", analyze.build_manifest(BRIEF, framework="claude")) == []


def _merged_grok_config(tmp_path, manifest, example=None):
    """A config with the managed block plus the operator's merge of the (or a given) example, as the operator does."""
    from agentteams.grok_config_emit import emit_grok_config

    example = example or dict(FRAMEWORKS["grok"]().extra_output_files(manifest))[f"../../{ww.GROK_EXAMPLE}"]
    assert not emit_grok_config(servers=[], features=["grok:sandbox"], output_root=tmp_path).errors
    config = tmp_path / ".grok" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n" + example, encoding="utf-8")
    return config, tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]["agentteams_wasm"]


@pytest.mark.parametrize("wires_a_server", [False, True])
def test_a_merged_grok_example_survives_a_re_render(tmp_path, wires_a_server):
    """Row wasm-hosts gap 2: a grok:mcp render that wires a server keeps an exact merged copy instead of stripping it."""
    from agentteams.grok_config_emit import emit_grok_config
    from tests.test_grok_config_emit import _server

    manifest = _manifest("grok")
    example = dict(FRAMEWORKS["grok"]().extra_output_files(manifest))[f"../../{ww.GROK_EXAMPLE}"]
    assert "grok:mcp" in example and "equals this example exactly" in example and "fresh render" in example
    config, merged = _merged_grok_config(tmp_path, manifest, example)
    features = ["grok:mcp", "grok:sandbox"] if wires_a_server else ["grok:sandbox"]
    res = emit_grok_config(servers=[_server()], features=features, output_root=tmp_path,
                           keep_if_exact=ww.grok_kept_tables(manifest))
    after = tomllib.loads(config.read_text(encoding="utf-8")).get("mcp_servers", {})
    assert not res.errors and after.get("agentteams_wasm") == merged and not res.dropped_unmanaged
    assert res.kept_unmanaged == (["agentteams_wasm"] if wires_a_server else [])
    if wires_a_server:
        assert "pg" in after and config.read_text(encoding="utf-8").count("[mcp_servers.agentteams_wasm]") == 1
        again = emit_grok_config(servers=[_server()], features=features, output_root=tmp_path,
                                 keep_if_exact=ww.grok_kept_tables(manifest))  # idempotent: still one copy
        assert again.kept_unmanaged == ["agentteams_wasm"]
        assert config.read_text(encoding="utf-8").count("[mcp_servers.agentteams_wasm]") == 1


@pytest.mark.parametrize("edit", [
    lambda t: t.replace('"--agent", "_session"', '"--agent", "_session", "--read", "/"'),   # a widened grant
    lambda t: t.replace("[mcp_servers.agentteams_wasm]", '[mcp_servers."agentteams_wasm"]'),  # another spelling
    lambda t: t + 'env = { X = "1" }\n',                                                     # an extra key
])
def test_a_merged_grok_table_that_differs_is_replaced_and_reported(tmp_path, edit):
    from agentteams.grok_config_emit import emit_grok_config
    from tests.test_grok_config_emit import _server

    manifest = _manifest("grok")
    example = dict(FRAMEWORKS["grok"]().extra_output_files(manifest))[f"../../{ww.GROK_EXAMPLE}"]
    config, _ = _merged_grok_config(tmp_path, manifest, edit(example))
    res = emit_grok_config(servers=[_server()], features=["grok:mcp"], output_root=tmp_path,
                           keep_if_exact=ww.grok_kept_tables(manifest))
    assert not res.errors and res.dropped_unmanaged == ["agentteams_wasm"] and not res.kept_unmanaged
    assert "agentteams_wasm" not in tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]


def test_without_keep_if_exact_the_merged_grok_table_is_replaced(tmp_path):
    """WASM off (grok_kept_tables is None): the old behaviour, so a stale copy cannot linger unseen."""
    from agentteams.grok_config_emit import emit_grok_config
    from tests.test_grok_config_emit import _server

    manifest = analyze.build_manifest(BRIEF, framework="grok")
    assert ww.grok_kept_tables(manifest) is None and ww.WIRING_KEY not in manifest
    config, _ = _merged_grok_config(tmp_path, _manifest("grok"))
    res = emit_grok_config(servers=[_server()], features=["grok:mcp"], output_root=tmp_path, keep_if_exact=None)
    assert res.dropped_unmanaged == ["agentteams_wasm"] and not res.kept_unmanaged


def test_grok_kept_tables_is_the_example_table_and_creates_no_wiring_row():
    manifest = _manifest("claude")  # host-scoped: a non-grok render with grok:mcp keeps the merged table too
    tables = ww.grok_kept_tables(manifest)
    example = dict(FRAMEWORKS["grok"]().extra_output_files(_manifest("grok")))[f"../../{ww.GROK_EXAMPLE}"]
    assert tables == {"mcp_servers.agentteams_wasm": tomllib.loads(example)["mcp_servers"]["agentteams_wasm"]}
    assert "grok" not in (manifest.get(ww.WIRING_KEY) or {}).get("frameworks", {})


@pytest.mark.parametrize("tamper", [False, True])
def test_the_cli_names_the_kept_or_replaced_wasm_table_with_the_right_remedy(tmp_path, capsys, tamper):
    """Row wasm-hosts gap 2: the replaced-table warning no longer says "add it to mcp_servers[]" for agentteams_wasm."""
    from agentteams.cli.artifacts import _emit_grok_config_if_enabled
    from tests.test_grok_config_emit import _server

    manifest = _manifest("grok")
    example = dict(FRAMEWORKS["grok"]().extra_output_files(manifest))[f"../../{ww.GROK_EXAMPLE}"]
    if tamper:
        example = example.replace('"--agent", "_session"', '"--agent", "_session", "--read", "/"')
    config, _ = _merged_grok_config(tmp_path, manifest, example + '\n[mcp_servers.mine]\ncommand = "x"\n')
    manifest["host_features"] = ["grok:mcp"]
    manifest["mcp_servers"] = [_server()]
    _emit_grok_config_if_enabled(manifest, tmp_path)
    out = capsys.readouterr().out
    assert "replaced: mine (add them to mcp_servers[] to keep them)" in out  # other tables: unchanged message
    if tamper:
        assert "[mcp_servers.agentteams_wasm] in .grok/config.toml was replaced" in out
        assert "Do not add agentteams_wasm to mcp_servers[]" in out and "Kept the merged" not in out
    else:
        assert f"Kept the merged [mcp_servers.*] agentteams_wasm in .grok/config.toml: it equals this render's " \
               f"{ww.GROK_EXAMPLE}" in out and "was replaced: it does not equal" not in out


def test_the_cli_reports_an_unparseable_managed_grok_block_instead_of_aborting(tmp_path, capsys):
    """A managed block that no longer parses as TOML is reported on stderr; the render carries on."""
    from agentteams.cli.artifacts import _emit_grok_config_if_enabled
    from agentteams.grok_config_emit import _MANAGED_FOOTER, _MANAGED_HEADER
    from tests.test_grok_config_emit import _server

    config = tmp_path / ".grok" / "config.toml"
    config.parent.mkdir(parents=True)
    broken = _MANAGED_HEADER + "[mcp_servers.x\n" + _MANAGED_FOOTER
    config.write_text(broken, encoding="utf-8")
    manifest = _manifest("grok")
    manifest["host_features"] = ["grok:mcp"]
    manifest["mcp_servers"] = [_server()]
    _emit_grok_config_if_enabled(manifest, tmp_path)
    err = capsys.readouterr().err
    assert "Grok config write failed" in err and "does not parse" in err
    assert config.read_text(encoding="utf-8") == broken  # nothing written over the operator's file
