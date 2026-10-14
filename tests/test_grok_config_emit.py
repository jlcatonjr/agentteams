"""Grok Phase 2: the agentteams-managed `.grok/config.toml` block, grant class, host features, launcher.

Plan tmp/by-week/2026-W42/grok-integration.plan.md §12 (P2-A..E) and the @security step-12b conditions (§12.1).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from agentteams import host_features
from agentteams.grok_config_emit import emit_grok_config, grok_mcp_entry, grok_permission_table
from agentteams.interop_grant_check import DECLARED, enforcement_class

_LAUNCHER = Path(__file__).resolve().parents[1] / "agentteams/templates/universal/sandbox/confine-run.sh"


def _server(**kw):
    base = {"artifact_type": "mcp-server", "mcp_server_schema_version": "1.0", "server_id": "pg",
            "domain": "data-access", "trust_tier": "first-party", "transport": "stdio", "command": "npx",
            "auth": {"mechanism": "none"}, "tools": [{"name": "q", "side_effects": "read"}], "scope": ["x"],
            "progressive_disclosure": "lazy", "security_review": {"required": False}}
    base.update(kw)
    return {k: v for k, v in base.items() if v is not None}


def _cfg(root: Path) -> dict:
    return tomllib.loads((root / ".grok" / "config.toml").read_text(encoding="utf-8"))


# P2-D: MCP entries ---------------------------------------------------------------------------------

def test_stdio_env_is_a_reference_never_a_value() -> None:
    fields, _ = grok_mcp_entry(_server(args=["-y", "pg"], auth={"mechanism": "env", "credential_ref": "PG_URL"}))
    assert fields == {"command": "npx", "args": ["-y", "pg"], "env": {"PG_URL": "${PG_URL}"}}


def test_http_uses_bearer_token_env_var_only() -> None:
    fields, _ = grok_mcp_entry(_server(transport="http", command=None,
                                       auth={"mechanism": "env", "credential_ref": "TOK", "url": "https://x/mcp"}))
    assert fields == {"url": "https://x/mcp", "bearer_token_env_var": "TOK"}


@pytest.mark.parametrize("server, why", [
    (_server(command="run ${HOME}/x"), "command"),
    (_server(args=["${SECRET}"]), "command/args"),
    (_server(auth={"mechanism": "env", "credential_ref": "BAD-NAME"}), "credential_ref"),
    (_server(auth={"mechanism": "env", "credential_ref": "X:-default"}), "credential_ref"),
    (_server(transport="http", command=None, auth={"mechanism": "none", "url": "https://${HOST}/m"}), "url"),
    (_server(trust_tier="third-party"), "authorization"),
    (_server(auth={"mechanism": "oauth"}), "oauth"),
])
def test_refused_entries(server: dict, why: str) -> None:
    fields, reason = grok_mcp_entry(server)
    assert fields is None and why in reason


# P2-E: permission ----------------------------------------------------------------------------------

def test_permission_rules_are_unrooted_and_cover_the_control_plane() -> None:
    table = grok_permission_table(["pg"])
    assert all(re.match(r"^(Edit|Read)\(\*\*/", r) for r in table["deny"]), table["deny"]
    for needed in ("Edit(**/.grok/config.toml)", "Edit(**/.grok/sandbox.toml)", "Edit(**/.grok/hooks/**)",
                   "Edit(**/.claude/settings.json)", "Read(**/.config/agentteams/keys/**)",
                   "Edit(**/.grok/agents/references/agent-privilege.json)"):
        assert needed in table["deny"]
    assert table["ask"] == ["MCPTool(pg__*)"]


def test_emit_writes_one_managed_block_and_keeps_operator_keys(tmp_path: Path) -> None:
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "config.toml").write_text('[plugins]\npaths = ["~/p"]\n', encoding="utf-8")
    res = emit_grok_config(servers=[_server()], features=["grok:mcp", "grok:sandbox"], output_root=tmp_path)
    assert res.errors == [] and res.wired == ["pg"] and res.permission_written
    cfg = _cfg(tmp_path)
    assert cfg["plugins"] == {"paths": ["~/p"]}
    assert cfg["mcp_servers"]["pg"]["command"] == "npx" and "deny" in cfg["permission"]
    again = emit_grok_config(servers=[_server()], features=["grok:mcp", "grok:sandbox"], output_root=tmp_path)
    assert again.errors == [] and _cfg(tmp_path) == cfg  # idempotent: the block is replaced, not stacked


@pytest.mark.parametrize("operator", ["[permission]\nallow = [\"Bash(ls)\"]\n", "permission.allow = [\"Bash(ls)\"]\n",
                                      "[permission.extra]\nx = 1\n"])
def test_operator_permission_table_is_never_touched(tmp_path: Path, operator: str) -> None:
    (tmp_path / ".grok").mkdir()
    (tmp_path / ".grok" / "config.toml").write_text(operator, encoding="utf-8")
    before = tomllib.loads(operator)
    res = emit_grok_config(servers=[], features=["grok:sandbox"], output_root=tmp_path)
    assert not res.permission_written and res.notices
    assert _cfg(tmp_path) == before


def test_symlinked_grok_dir_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / ".grok").symlink_to(real, target_is_directory=True)
    res = emit_grok_config(servers=[], features=["grok:sandbox"], output_root=proj)
    assert res.errors and not (real / "config.toml").exists()


def test_gated_off_without_tokens(tmp_path: Path) -> None:
    res = emit_grok_config(servers=[_server()], features=[], output_root=tmp_path)
    assert not res.written and not (tmp_path / ".grok").exists()


# P2-B / P2-C ---------------------------------------------------------------------------------------

def test_grok_grants_are_declared_not_enforced() -> None:
    assert enforcement_class("grok", {}) == DECLARED


def test_host_feature_tokens() -> None:
    assert host_features._sandbox_token_for("grok") == "grok:sandbox"
    assert {"mcp", "sandbox"} <= host_features._KNOWN_FEATURES["grok"]


# P2-A: launcher ------------------------------------------------------------------------------------

def test_launcher_protects_grok_whole_and_warns() -> None:
    text = _LAUNCHER.read_text(encoding="utf-8")
    dirs = re.search(r"CONTROL_PLANE_DIRS_REL=\(([^)]*)\)", text).group(1).split()
    assert ".grok" in dirs
    assert re.search(r"^grok_config_warn$", text, re.M), "grok_config_warn is defined but never called"
