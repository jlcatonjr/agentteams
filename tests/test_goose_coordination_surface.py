"""Phase 2: the file-based cross-repo coordination surface for Goose teams.

Covers the single gate (coordination_write_roots), the stdio coordination MCP extension
wired into coordinator/liaison recipes only, the gated shipping of the server script, the
read-from-disk shipper (no drift / placeholder degrade), and the write-roots merge seam.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.frameworks.goose import GooseAdapter, _coordination_mcp_content, _COORDINATION_MCP_SOURCE
from agentteams.cli.artifacts import apply_coordination_roots_to_write_roots

_AGENT_MD = """---
name: Agent
description: does a thing
tools: ['read', 'edit', 'search']
---
Body.
"""

_COORD_ROOTS = ["/srv/repos/vk-api-utils"]


def _manifest(*, coordination=True, slug="orchestrator"):
    m = {
        "project_name": "Example",
        "output_files": [{"path": f".goose/recipes/{slug}.agent.md"}],
    }
    if coordination:
        m["coordination_write_roots"] = _COORD_ROOTS
    return m


def _render(slug, *, coordination=True):
    return GooseAdapter().render_agent_file(_AGENT_MD, slug, _manifest(coordination=coordination, slug=slug))


# --- recipe wiring, gated + scoped ------------------------------------------------------

@pytest.mark.parametrize("slug", ["orchestrator", "repo-liaison"])
def test_coordination_extension_wired_into_coordinator_recipes(slug):
    out = _render(slug, coordination=True)
    assert "agentteams_coordination" in out
    assert "scripts/goose-coordination-mcp.py" in out


@pytest.mark.parametrize("slug", ["navigator", "security"])
def test_coordination_extension_absent_from_non_coordinator_recipe(slug):
    # PoLA: @security reviews/relays (reads files, signs verdicts) — it is NOT a coordinator and
    # must not receive the record-capable coordination extension.
    out = _render(slug, coordination=True)
    assert "agentteams_coordination" not in out


def test_no_coordination_declared_is_byte_identical_baseline():
    # Same coordinator slug, but no coordination_write_roots -> no extension at all.
    with_decl = _render("orchestrator", coordination=True)
    without = _render("orchestrator", coordination=False)
    assert "agentteams_coordination" in with_decl
    assert "agentteams_coordination" not in without


# --- gated shipping of the server script ------------------------------------------------

def test_server_script_shipped_only_when_coordination_declared():
    on = dict(GooseAdapter().extra_output_files(_manifest(coordination=True)))
    off = dict(GooseAdapter().extra_output_files(_manifest(coordination=False)))
    assert "../../scripts/goose-coordination-mcp.py" in on
    assert "../../scripts/goose-coordination-mcp.py" not in off


def test_shipped_server_matches_disk_source_exactly():
    on = dict(GooseAdapter().extra_output_files(_manifest(coordination=True)))
    assert on["../../scripts/goose-coordination-mcp.py"] == _COORDINATION_MCP_SOURCE.read_text(encoding="utf-8")


def test_coordination_mcp_content_degrades_on_missing_source(monkeypatch, tmp_path):
    monkeypatch.setattr("agentteams.frameworks.goose_docs._COORDINATION_MCP_SOURCE", tmp_path / "gone.py")
    content = _coordination_mcp_content()
    assert "Placeholder" in content


# --- write-roots merge seam -------------------------------------------------------------

def test_coordination_roots_union_when_confined():
    m = {"privilege_profile": "confined", "coordination_write_roots": _COORD_ROOTS}
    added = apply_coordination_roots_to_write_roots(m)
    assert added == _COORD_ROOTS
    assert _COORD_ROOTS[0] in m["workspace_write_roots"]


def test_coordination_roots_noop_when_not_confined():
    m = {"coordination_write_roots": _COORD_ROOTS}  # cooperative (no confinement)
    assert apply_coordination_roots_to_write_roots(m) == []
    assert "workspace_write_roots" not in m  # unchanged -> baseline preserved


def test_coordination_roots_noop_when_none_declared():
    m = {"privilege_profile": "confined"}
    assert apply_coordination_roots_to_write_roots(m) == []
