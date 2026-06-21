"""Tests for the specified-server automation pipeline (report §5.4/§6).

Covers the two new seams that wire operator-DECLARED MCP servers through the
build:

1. ``analyze.build_manifest`` copies ``description['mcp_servers']`` into
   ``manifest['mcp_servers']`` (Phase 1, inert).
2. ``generate._emit_mcp_servers_if_enabled`` emits the inert
   ``.claude/mcp-servers.agentteams.json`` at the project root when an MCP
   host-feature token is on and the manifest carries servers (Phase 2), reusing
   ``mcp_emit.emit_mcp_artifact`` (validation, fail-closed activation status, and
   the never-clobber default all come from there).

These stay strictly inert: no ``.mcp.json``, no secrets, nothing provisioned.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import build_team
from agentteams import analyze
from agentteams.cli import generate

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"

_SERVER = {
    "artifact_type": "mcp-server",
    "mcp_server_schema_version": "1.0",
    "server_id": "vk-pg",
    "domain": "data-access",
    "trust_tier": "first-party",
    "transport": "stdio",
    "tools": [{"name": "run_query", "side_effects": "read"}],
    "scope": ["data-analyst-expert"],
    "progressive_disclosure": "lazy",
    "security_review": {"required": False},
}


def _desc(**kw):
    base = {"project_goal": "g", "project_name": "p"}
    base.update(kw)
    return base


def _artifact(root: Path) -> Path:
    return root / ".claude" / "mcp-servers.agentteams.json"


# --- Phase 1: specification -> manifest --------------------------------------

def test_build_manifest_copies_declared_servers():
    m = analyze.build_manifest(_desc(mcp_servers=[_SERVER]), framework="claude")
    assert m["mcp_servers"] == [_SERVER]


def test_build_manifest_without_servers_is_unchanged():
    m = analyze.build_manifest(_desc(), framework="claude")
    assert "mcp_servers" not in m


def test_build_manifest_ignores_empty_or_non_list_servers():
    assert "mcp_servers" not in analyze.build_manifest(_desc(mcp_servers=[]), framework="claude")
    assert "mcp_servers" not in analyze.build_manifest(_desc(mcp_servers="nope"), framework="claude")


# --- Phase 2: emission gate --------------------------------------------------

def test_emits_inert_artifact_when_enabled(tmp_path):
    manifest = {"host_features": ["claude:mcp"], "mcp_servers": [_SERVER]}
    generate._emit_mcp_servers_if_enabled(manifest, tmp_path)
    out = _artifact(tmp_path)
    assert out.is_file()
    payload = json.loads(out.read_text())
    assert "_agentteams_managed" in payload          # inert managed notice
    assert payload["activation_status"] == {"vk-pg": False}  # first-party read -> not blocked
    assert payload["servers"] == [_SERVER]


def test_bridge_token_also_enables(tmp_path):
    manifest = {"host_features": ["bridge:copilot-vscode-to-claude:mcp"], "mcp_servers": [_SERVER]}
    generate._emit_mcp_servers_if_enabled(manifest, tmp_path)
    assert _artifact(tmp_path).is_file()


def test_no_token_emits_nothing(tmp_path):
    generate._emit_mcp_servers_if_enabled({"host_features": [], "mcp_servers": [_SERVER]}, tmp_path)
    assert not _artifact(tmp_path).exists()


def test_no_servers_emits_nothing(tmp_path):
    generate._emit_mcp_servers_if_enabled({"host_features": ["claude:mcp"], "mcp_servers": []}, tmp_path)
    assert not _artifact(tmp_path).exists()


def test_third_party_server_is_activation_blocked(tmp_path):
    third = dict(_SERVER, server_id="ext", trust_tier="third-party-vetted",
                 security_review={"required": True})
    manifest = {"host_features": ["claude:mcp"], "mcp_servers": [third]}
    generate._emit_mcp_servers_if_enabled(manifest, tmp_path)
    payload = json.loads(_artifact(tmp_path).read_text())
    assert payload["activation_status"] == {"ext": True}  # fail-closed


def test_never_clobbers_existing_artifact(tmp_path):
    """overwrite=False default protects operator authorization records on re-run."""
    manifest = {"host_features": ["claude:mcp"], "mcp_servers": [_SERVER]}
    generate._emit_mcp_servers_if_enabled(manifest, tmp_path)
    out = _artifact(tmp_path)
    out.write_text('{"sentinel": "operator-authorized"}\n', encoding="utf-8")
    generate._emit_mcp_servers_if_enabled(manifest, tmp_path)  # re-run
    assert json.loads(out.read_text()) == {"sentinel": "operator-authorized"}


# --- end-to-end: description -> manifest -> artifact -------------------------

def test_declared_server_flows_description_to_artifact(tmp_path):
    manifest = analyze.build_manifest(_desc(mcp_servers=[_SERVER]), framework="claude")
    manifest["host_features"] = ["claude:mcp"]
    generate._emit_mcp_servers_if_enabled(manifest, tmp_path)
    payload = json.loads(_artifact(tmp_path).read_text())
    assert payload["servers"][0]["server_id"] == "vk-pg"


# --- end-to-end through build_team.main: emit + drift-exclusion --------------

def _seed_gates(refs: Path, monkeypatch, *, ticket: str) -> None:
    """Seed a signed security-intel-freshness waiver (mirrors test_model_routing)."""
    refs.mkdir(parents=True, exist_ok=True)
    key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", key)
    cols = ["waiver_id", "action_reviewed", "expires_at", "max_uses", "uses",
            "approver", "ticket_id", "reason_code", "conditions_verified"]
    w = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": f"wf-{ticket}",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z", "max_uses": "9", "uses": "0",
        "approver": "t", "ticket_id": ticket, "reason_code": "T",
        "conditions_verified": "verified",
    }
    w["signature"] = hmac.new(
        key.encode(), "|".join(w[c] for c in cols).encode(), hashlib.sha256
    ).hexdigest()
    header = ("timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
              "ticket_id,reason_code,conditions_verified,signature\n")
    row = (f'{w["timestamp"]},{w["waiver_id"]},{w["action_reviewed"]},{w["expires_at"]},'
           f'{w["max_uses"]},{w["uses"]},{w["approver"]},{w["ticket_id"]},'
           f'{w["reason_code"]},{w["conditions_verified"]},{w["signature"]}\n')
    (refs / "security-waivers.log.csv").write_text(header + row, encoding="utf-8")


def test_end_to_end_emits_and_is_drift_excluded(tmp_path, monkeypatch):
    """A real build (build_team.main) with claude:mcp + declared servers emits the
    inert artifact AND keeps it out of the build-log (parity with model-routing)."""
    brief_src = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief_src.exists():
        pytest.skip("data-pipeline brief not found")
    brief = json.loads(brief_src.read_text(encoding="utf-8"))
    brief["mcp_servers"] = [_SERVER]
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")

    out = tmp_path / "proj"          # project root == output for the claude adapter
    _seed_gates(out / "references", monkeypatch, ticket="MCPE2E")
    rc = build_team.main([
        "--description", str(brief_path), "--output", str(out),
        "--framework", "claude", "--target-host-features", "claude:mcp",
        "--yes", "--no-scan", "--security-offline",
    ])
    assert rc == 0, f"build failed: rc={rc}"

    # 1. inert artifact emitted at project-root .claude/
    artifact = out / ".claude" / "mcp-servers.agentteams.json"
    assert artifact.is_file()
    payload = json.loads(artifact.read_text())
    assert payload["servers"][0]["server_id"] == "vk-pg"
    assert "_agentteams_managed" in payload

    # 2. drift-excluded: never registered in any build-log map
    log = json.loads((out / "references" / "build-log.json").read_text())
    rel = ".claude/mcp-servers.agentteams.json"
    assert rel not in log.get("template_hashes", {})
    assert rel not in log.get("file_hashes", {})
    omap = log.get("output_files_map", [])
    paths = {f.get("path") for f in omap} if omap and isinstance(omap[0], dict) else set(omap)
    assert rel not in paths
    assert not any("mcp-servers" in str(p) for p in paths)
