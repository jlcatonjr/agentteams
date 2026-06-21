"""Tests for opt-in MCP-server extension wiring in Goose recipes.

Covers the gate (goose:mcp), the stdio/streamable_http mapping, least-privilege
scope filtering, the fail-closed activation boundary (first-party read-only only),
credential-by-reference (env_keys), the forbidden-shape validator guards, and an
end-to-end `goose recipe validate` (skipped if the binary is absent).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import host_features
from agentteams.frameworks import goose as G
from agentteams.frameworks.goose import GooseAdapter

REPO_ROOT = Path(__file__).resolve().parent.parent

_AGENT_MD = """---
name: Data Analyst Expert
description: Analyzes warehouse data
tools: ['read', 'edit', 'search']
---
You analyze data for the team.
"""


def _server(**kw):
    base = {
        "artifact_type": "mcp-server", "mcp_server_schema_version": "1.0",
        "server_id": "vk-postgres", "domain": "data-access",
        "trust_tier": "first-party", "transport": "stdio",
        "command": "uvx", "args": ["mcp-server-postgres"],
        "auth": {"mechanism": "env", "credential_ref": "VK_PG_DSN"},
        "tools": [{"name": "run_query", "side_effects": "read"}],
        "scope": ["data-analyst-expert"], "progressive_disclosure": "lazy",
        "security_review": {"required": False},
    }
    base.update(kw)
    return base


def _manifest(servers, *, token=True):
    return {
        "project_name": "VK",
        "host_features": ["goose:mcp"] if token else [],
        "mcp_servers": servers,
        "output_files": [{"path": ".goose/recipes/data-analyst-expert.agent.md"}],
    }


def _render(servers, *, slug="data-analyst-expert", token=True):
    return GooseAdapter().render_agent_file(_AGENT_MD, slug, _manifest(servers, token=token))


# --- host-feature token ------------------------------------------------------

def test_goose_mcp_token_is_valid():
    assert host_features.parse_tokens("goose:mcp") == ["goose:mcp"]


def test_goose_namespace_rejects_unknown_feature():
    with pytest.raises(host_features.HostFeatureError):
        host_features.parse_tokens("goose:bogus")


# --- opt-in / default-off ----------------------------------------------------

def test_default_off_is_byte_identical_to_baseline():
    with_token_off = _render([_server()], token=False)
    no_mcp_at_all = GooseAdapter().render_agent_file(
        _AGENT_MD, "data-analyst-expert",
        {"project_name": "VK", "output_files": [{"path": ".goose/recipes/data-analyst-expert.agent.md"}]},
    )
    assert with_token_off == no_mcp_at_all


def test_no_extension_without_token():
    recipe = _render([_server()], token=False)
    assert "stdio" not in recipe
    assert "agentteams MCP" not in recipe


# --- stdio + http mapping ----------------------------------------------------

def test_stdio_server_wired():
    recipe = _render([_server()])
    assert "  - type: stdio" in recipe
    assert 'name: "vk_postgres"' in recipe          # identifier-normalized
    assert 'cmd: "uvx"' in recipe
    assert '- "mcp-server-postgres"' in recipe
    assert 'env_keys:' in recipe and '- "VK_PG_DSN"' in recipe
    assert "    timeout: 300" in recipe


def test_http_server_wired_as_streamable_http():
    s = _server(server_id="remote", transport="http", command=None, args=None,
                auth={"mechanism": "env", "credential_ref": "TOK", "url": "https://m.example/api"},
                tools=[{"name": "q", "side_effects": "read"}])
    recipe = _render([s])
    assert "  - type: streamable_http" in recipe
    assert 'uri: "https://m.example/api"' in recipe
    assert "type: sse" not in recipe                # deprecated transport never emitted


# --- least privilege ---------------------------------------------------------

def test_scope_filters_to_agent():
    s = _server(scope=["some-other-agent"])
    recipe = _render([s])
    assert "stdio" not in recipe                    # not in scope -> not wired
    assert "agentteams MCP" not in recipe           # not in scope -> not even a note


# --- fail-closed activation boundary -----------------------------------------

@pytest.mark.parametrize("kw,reason", [
    (dict(trust_tier="third-party-vetted", security_review={"required": True}), "authorization"),
    (dict(tools=[{"name": "w", "side_effects": "write"}]), "authorization"),
    (dict(tools=[{"name": "d", "side_effects": "destructive"}],
          security_review={"required": True}), "authorization"),
    (dict(security_review={"required": True}), "authorization"),
])
def test_non_read_only_servers_skipped_and_surfaced(kw, reason):
    recipe = _render([_server(**kw)])
    assert "  - type: stdio" not in recipe          # not wired
    assert "# agentteams MCP: vk-postgres not wired" in recipe
    assert reason in recipe


def test_stdio_without_command_skipped_not_silent():
    recipe = _render([_server(command=None)])
    assert "  - type: stdio" not in recipe
    assert "no 'command'" in recipe


def test_http_without_url_skipped_not_silent():
    s = _server(transport="http", command=None,
                auth={"mechanism": "none"}, tools=[{"name": "q", "side_effects": "read"}])
    recipe = _render([s])
    assert "streamable_http" not in recipe
    assert "no auth.url" in recipe


# --- credential mechanisms ---------------------------------------------------

def test_mechanism_none_wires_without_env_keys():
    s = _server(auth={"mechanism": "none"})
    recipe = _render([s])
    assert "  - type: stdio" in recipe
    assert "env_keys:" not in recipe


@pytest.mark.parametrize("mech", ["secret-store", "oauth"])
def test_unexpressible_credential_mechanisms_skipped(mech):
    s = _server(auth={"mechanism": mech, "credential_ref": "K"})
    recipe = _render([s])
    assert "  - type: stdio" not in recipe
    assert mech in recipe                            # surfaced in the skip note


# --- YAML quoting ------------------------------------------------------------

def test_special_chars_are_quoted_and_validate():
    s = _server(command="my cmd", args=["--flag: value", "a#b", "- dash"])
    recipe = _render([s])
    assert G._validate_recipe_yaml(recipe) == []     # internal validator clean
    # every emitted arg is double-quoted
    assert '- "--flag: value"' in recipe
    assert '- "a#b"' in recipe
    assert '- "- dash"' in recipe


# --- validator guards --------------------------------------------------------

def test_rendered_recipe_passes_internal_validator():
    assert G._validate_recipe_yaml(_render([_server()])) == []


def test_validator_guards_catch_forbidden_shapes():
    base = 'version: "1.0.0"\ntitle: "x"\ninstructions: |\n  y\nextensions:\n'
    assert G._validate_recipe_yaml(base + "  - type: sse\n")
    assert G._validate_recipe_yaml(base + "  - type: stdio\n    envs: {}\n")
    assert G._validate_recipe_yaml(
        'version: "1.0.0"\ntitle: "x"\ninstructions: |\n  y\ncontext: z\nextensions:\n')
    # a uri ending in /sse must NOT false-positive
    assert G._validate_recipe_yaml(
        base + '  - type: streamable_http\n    uri: "https://h/sse"\n') == []


# --- schema round-trip (Claude inert artifact still accepts command/args) -----

def test_server_with_command_args_validates_and_round_trips_claude_artifact(tmp_path):
    from agentteams import mcp_emit
    s = _server()  # carries command + args
    r = mcp_emit.emit_mcp_artifact(servers=[s], features=["claude:mcp"], output_root=tmp_path)
    assert r.success and r.written
    payload = json.loads((tmp_path / ".claude" / "mcp-servers.agentteams.json").read_text())
    assert payload["servers"][0]["command"] == "uvx"
    assert payload["servers"][0]["args"] == ["mcp-server-postgres"]


# --- end-to-end goose recipe validate (local-only) ---------------------------

@pytest.mark.skipif(shutil.which("goose") is None, reason="goose CLI not installed")
def test_goose_recipe_validate_accepts_wired_recipe(tmp_path):
    recipe = _render([
        _server(),
        _server(server_id="remote", transport="http", command=None, args=None,
                auth={"mechanism": "env", "credential_ref": "TOK", "url": "https://m.example/api"},
                tools=[{"name": "q", "side_effects": "read"}]),
    ])
    path = tmp_path / "data-analyst-expert.yaml"
    path.write_text(recipe, encoding="utf-8")
    proc = subprocess.run(["goose", "recipe", "validate", str(path)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"goose recipe validate failed:\n{proc.stdout}\n{proc.stderr}"
