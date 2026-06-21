"""Tests for MCP integration in goose-TARGET bridges.

The goose bridge now emits a `.goose/recipes/bridge-orchestrator.yaml` entry recipe
that always declares the `developer` (CLI) extension and, opt-in via a
`bridge:<src>-to-goose:mcp` token, wires the operator-selected (first-party,
read-only, orchestrator-scoped) MCP servers read from the SOURCE project's inert
`.claude/mcp-servers.agentteams.json`.

`run_bridge` is called directly (the security-intel gate lives in the CLI
`_run_bridge`, not here), so no waiver seeding is needed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agentteams import host_features
from agentteams.bridge import run_bridge
from agentteams.frameworks import goose as G

_RECIPE_REL = Path(".goose") / "recipes" / "bridge-orchestrator.yaml"


def _server(**kw):
    base = {
        "artifact_type": "mcp-server", "mcp_server_schema_version": "1.0",
        "server_id": "vk-pg", "domain": "data", "trust_tier": "first-party",
        "transport": "stdio", "command": "uvx", "args": ["mcp-server-postgres"],
        "auth": {"mechanism": "env", "credential_ref": "VK_PG_DSN"},
        "tools": [{"name": "q", "side_effects": "read"}],
        "scope": ["orchestrator"], "progressive_disclosure": "lazy",
        "security_review": {"required": False},
    }
    base.update(kw)
    return base


def _make_source(root: Path, *, servers=None):
    """Create a minimal copilot-vscode source project, optionally with the artifact."""
    agents = root / ".github" / "agents"
    agents.mkdir(parents=True)
    (agents / "orchestrator.agent.md").write_text(
        "---\nname: Orchestrator\ndescription: Routes work\n---\nYou coordinate.\n",
        encoding="utf-8",
    )
    if servers is not None:
        claude = root / ".claude"
        claude.mkdir(parents=True)
        (claude / "mcp-servers.agentteams.json").write_text(
            json.dumps({"servers": servers, "activation_status": {}}), encoding="utf-8"
        )
    return agents


def _bridge(root, *, host_features=None, overwrite=False, merge_only=False):
    return run_bridge(
        source_dir=root / ".github" / "agents",
        target_framework="goose",
        output_root=root,
        source_framework="copilot-vscode",
        overwrite=overwrite,
        merge_only=merge_only,
        host_features=host_features or [],
    )


# --- token validity ----------------------------------------------------------

@pytest.mark.parametrize("src", ["copilot-vscode", "claude", "copilot-cli"])
def test_bridge_to_goose_mcp_tokens_valid(src):
    assert host_features.parse_tokens(f"bridge:{src}-to-goose:mcp") == [f"bridge:{src}-to-goose:mcp"]


# --- req 1: developer (CLI) by default ---------------------------------------

def test_bridge_emits_recipe_with_developer_by_default(tmp_path):
    _make_source(tmp_path)
    _bridge(tmp_path)  # no token
    recipe = (tmp_path / _RECIPE_REL).read_text()
    assert "name: developer" in recipe
    assert "type: stdio" not in recipe        # no MCP without token
    assert G._validate_recipe_yaml(recipe) == []


# --- req 2: selected MCP servers wired (opt-in) ------------------------------

def test_token_plus_artifact_wires_orchestrator_server(tmp_path):
    _make_source(tmp_path, servers=[_server()])
    _bridge(tmp_path, host_features=["bridge:copilot-vscode-to-goose:mcp"])
    recipe = (tmp_path / _RECIPE_REL).read_text()
    assert "  - type: stdio" in recipe
    assert 'name: "vk_pg"' in recipe
    assert 'env_keys:' in recipe and '- "VK_PG_DSN"' in recipe


def test_specialist_and_unsafe_servers_surfaced_not_wired(tmp_path):
    specialist = _server(server_id="spec", scope=["data-analyst-expert"])
    third = _server(server_id="ext", trust_tier="third-party-vetted",
                    security_review={"required": True})
    writer = _server(server_id="wr", tools=[{"name": "w", "side_effects": "write"}])
    _make_source(tmp_path, servers=[specialist, third, writer])
    _bridge(tmp_path, host_features=["bridge:copilot-vscode-to-goose:mcp"])
    recipe = (tmp_path / _RECIPE_REL).read_text()
    assert "  - type: stdio" not in recipe          # none wired
    assert "# agentteams MCP: spec not wired" in recipe
    assert "# agentteams MCP: ext not wired" in recipe
    assert "# agentteams MCP: wr not wired" in recipe


def test_token_without_artifact_is_graceful(tmp_path):
    _make_source(tmp_path)  # no artifact
    res = _bridge(tmp_path, host_features=["bridge:copilot-vscode-to-goose:mcp"])
    recipe = (tmp_path / _RECIPE_REL).read_text()
    assert "name: developer" in recipe              # still CLI by default
    assert "type: stdio" not in recipe
    assert any("not found" in n for n in res.notices)


def test_no_token_does_not_read_artifact(tmp_path):
    _make_source(tmp_path, servers=[_server()])
    _bridge(tmp_path)  # artifact present but no token
    recipe = (tmp_path / _RECIPE_REL).read_text()
    assert "type: stdio" not in recipe


# --- merge / refresh mechanics -----------------------------------------------

def test_merge_preserves_edited_recipe(tmp_path):
    _make_source(tmp_path)
    _bridge(tmp_path)
    recipe_path = tmp_path / _RECIPE_REL
    recipe_path.write_text("version: \"1.0.0\"\ntitle: \"edited\"\ninstructions: |\n  mine\n", encoding="utf-8")
    _bridge(tmp_path, merge_only=True)              # merge must NOT clobber
    assert "edited" in recipe_path.read_text()


def test_refresh_overwrites_recipe(tmp_path):
    _make_source(tmp_path)
    _bridge(tmp_path)
    recipe_path = tmp_path / _RECIPE_REL
    recipe_path.write_text("stale\n", encoding="utf-8")
    _bridge(tmp_path, overwrite=True)               # refresh regenerates
    assert "name: developer" in recipe_path.read_text()


# --- pointer files still emitted (bridge unchanged otherwise) -----------------

def test_pointer_files_still_emitted(tmp_path):
    _make_source(tmp_path)
    _bridge(tmp_path)
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / ".goosehints").is_file()
    assert (tmp_path / ".goose" / "README.md").is_file()


# --- e2e goose recipe validate (local-only) ----------------------------------

@pytest.mark.skipif(shutil.which("goose") is None, reason="goose CLI not installed")
def test_emitted_bridge_recipe_passes_goose_validate(tmp_path):
    _make_source(tmp_path, servers=[_server()])
    _bridge(tmp_path, host_features=["bridge:copilot-vscode-to-goose:mcp"])
    proc = subprocess.run(
        ["goose", "recipe", "validate", str(tmp_path / _RECIPE_REL)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
