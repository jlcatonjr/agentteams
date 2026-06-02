"""Tests for agentteams.mcp_emit — the gated, inert MCP definition emitter.

Covers gating, inertness self-enforcement, fail-closed activation status, and
the no-clobber default introduced after the step-3 adversarial audit.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import mcp_emit as me

_MCP_SCHEMA = json.loads(
    (Path(__file__).resolve().parent.parent / "schemas" / "mcp-server.schema.json").read_text()
)


def _validator():
    import jsonschema

    return jsonschema.Draft7Validator(_MCP_SCHEMA)


def _server(**kw):
    base = {
        "artifact_type": "mcp-server",
        "mcp_server_schema_version": "1.0",
        "server_id": "vk-pg",
        "domain": "data-access",
        "trust_tier": "first-party",
        "transport": "stdio",
        "tools": [{"name": "q", "side_effects": "read"}],
        "scope": ["x"],
        "progressive_disclosure": "lazy",
        "security_review": {"required": False},
    }
    base.update(kw)
    return base


def _read(out_root: Path) -> dict:
    return json.loads((out_root / ".claude" / "mcp-servers.agentteams.json").read_text())


# --- gating ------------------------------------------------------------------

def test_gated_off_without_feature(tmp_path):
    r = me.emit_mcp_artifact(servers=[_server()], features=[], output_root=tmp_path)
    assert r.gated_off is True
    assert not (tmp_path / ".claude").exists()


def test_both_tokens_enable():
    assert me.mcp_enabled(["claude:mcp"]) is True
    assert me.mcp_enabled(["bridge:copilot-vscode-to-claude:mcp"]) is True
    assert me.mcp_enabled(["claude:hooks"]) is False
    assert me.mcp_enabled([]) is False


def test_empty_servers_is_noop(tmp_path):
    r = me.emit_mcp_artifact(servers=[], features=["claude:mcp"], output_root=tmp_path)
    assert r.written == [] and r.gated_off is False


# --- inertness self-enforcement (step-3 audit F4) ---------------------------

def test_inline_secret_credential_ref_rejected(tmp_path):
    leaky = _server(server_id="leaky", auth={"mechanism": "env", "credential_ref": "postgres://u:p@h/db"})
    r = me.emit_mcp_artifact(servers=[leaky], features=["claude:mcp"], output_root=tmp_path)
    assert r.written == []
    assert any("inline secret" in e for e in r.errors)


def test_schema_invalid_server_rejected(tmp_path):
    bad = _server(server_id="mystery", trust_tier="weird-tier")
    good = _server()
    r = me.emit_mcp_artifact(servers=[bad, good], features=["claude:mcp"], output_root=tmp_path)
    data = _read(tmp_path)
    assert [s["server_id"] for s in data["servers"]] == ["vk-pg"]
    assert any("mystery" in e for e in r.errors)


def test_all_written_servers_are_schema_valid(tmp_path):
    third = _server(server_id="jira", trust_tier="third-party-vetted", security_review={"required": True})
    me.emit_mcp_artifact(servers=[_server(), third], features=["claude:mcp"], output_root=tmp_path)
    data = _read(tmp_path)
    v = _validator()
    assert all(v.is_valid(s) for s in data["servers"])


# --- activation status sibling map, NOT injected into server (F1) -----------

def test_activation_status_is_sibling_not_injected(tmp_path):
    third = _server(server_id="jira", trust_tier="third-party-vetted", security_review={"required": True})
    r = me.emit_mcp_artifact(servers=[_server(), third], features=["claude:mcp"], output_root=tmp_path)
    data = _read(tmp_path)
    assert data["activation_status"] == {"vk-pg": False, "jira": True}
    assert r.activation_blocked == ["jira"]
    # the annotation must NOT leak into the schema-conformant server objects
    for s in data["servers"]:
        assert "activation_requires_authorization" not in s


def test_third_party_blocked_but_first_party_readwrite_not_blocked(tmp_path):
    # third-party -> blocked; first-party with read AND write tools -> NOT blocked.
    # This pins _SAFE_SIDE_EFFECTS = {read, write} and the fail-open-on-safe direction.
    blocked = _server(server_id="api", trust_tier="third-party-untrusted", security_review={"required": True})
    writer = _server(server_id="writer", tools=[{"name": "w", "side_effects": "write"},
                                                {"name": "r", "side_effects": "read"}])
    me.emit_mcp_artifact(servers=[blocked, writer], features=["claude:mcp"], output_root=tmp_path)
    status = _read(tmp_path)["activation_status"]
    assert status["api"] is True
    assert status["writer"] is False


# --- no-clobber default (step-3 audit F5) -----------------------------------

def test_overwrite_false_default_preserves_operator_edits(tmp_path):
    me.emit_mcp_artifact(servers=[_server()], features=["claude:mcp"], output_root=tmp_path)
    out = tmp_path / ".claude" / "mcp-servers.agentteams.json"
    out.write_text('{"operator":"edited"}', encoding="utf-8")
    r = me.emit_mcp_artifact(servers=[_server()], features=["claude:mcp"], output_root=tmp_path)
    assert str(out) in r.skipped
    assert json.loads(out.read_text()) == {"operator": "edited"}


def test_explicit_overwrite_replaces(tmp_path):
    out = tmp_path / ".claude" / "mcp-servers.agentteams.json"
    out.parent.mkdir(parents=True)
    out.write_text('{"old":1}', encoding="utf-8")
    me.emit_mcp_artifact(servers=[_server()], features=["claude:mcp"], output_root=tmp_path, overwrite=True)
    assert "servers" in json.loads(out.read_text())


# --- robustness (step-3 audit F6) -------------------------------------------

def test_non_dict_entry_routed_to_errors_not_crash(tmp_path):
    r = me.emit_mcp_artifact(servers=["not-a-server", None], features=["claude:mcp"], output_root=tmp_path)
    assert r.written == []
    assert len(r.errors) == 2
    assert not (tmp_path / ".claude").exists()  # nothing written when all entries invalid


def test_dry_run_reports_but_writes_nothing(tmp_path):
    r = me.emit_mcp_artifact(servers=[_server()], features=["claude:mcp"], output_root=tmp_path, dry_run=True)
    assert len(r.written) == 1  # reported as would-write
    assert not (tmp_path / ".claude").exists()  # but no file on disk


def test_managed_notice_warns_against_mcp_json_rename(tmp_path):
    me.emit_mcp_artifact(servers=[_server()], features=["claude:mcp"], output_root=tmp_path)
    assert ".mcp.json" in _read(tmp_path)["_agentteams_managed"]
