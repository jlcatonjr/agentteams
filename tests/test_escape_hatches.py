"""A.3 regression tests: CAI schema escape hatches in export_to_cai.

Report section 4.1: the three CAI schema escape-hatch fields
(``agents[].raw_front_matter``, ``agents[].capabilities.raw``/``model_hint``,
top-level ``references[]``) are fully implemented in canonical.py's
materialize/load layer but were never populated by ``export_to_cai`` — so
framework-specific front-matter keys, raw capability strings, model hints,
and non-agent reference content all silently vanished on export.

These tests verify each escape hatch is now captured and survives the
canonical materialize→load round trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.interop import export_to_cai, import_from_cai


# ---------------------------------------------------------------------------
# raw_front_matter
# ---------------------------------------------------------------------------

def test_raw_front_matter_captures_unmodeled_keys(tmp_path: Path):
    """Front-matter keys not in {name, description, handoffs} are captured
    into raw_front_matter — specifically user-invokable, model, and agents:
    for copilot-vscode."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    orch = [a for a in cai["agents"] if a["slug"] == "orchestrator"][0]
    rfm = orch.get("raw_front_matter", {})
    assert "user-invokable" in rfm, f"user-invokable missing from raw_front_matter: {rfm}"
    assert rfm["user-invokable"] is True or rfm["user-invokable"] == "true"
    assert "model" in rfm, f"model missing from raw_front_matter: {rfm}"
    assert "agents" in rfm, f"agents roster missing from raw_front_matter: {rfm}"


def test_raw_front_matter_survives_canonical_round_trip(tmp_path: Path):
    """raw_front_matter survives export → materialize → load."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    canon = tmp_path / "canon"
    materialize_canonical(cai, canon)
    loaded = load_canonical(canon)
    orch = [a for a in loaded["agents"] if a["slug"] == "orchestrator"][0]
    rfm = orch.get("raw_front_matter", {})
    assert "user-invokable" in rfm
    assert "model" in rfm
    assert "agents" in rfm


# ---------------------------------------------------------------------------
# capabilities.raw and capabilities.model_hint
# ---------------------------------------------------------------------------

def test_capabilities_raw_captures_original_tool_string(tmp_path: Path):
    """capabilities.raw captures the original tools: string per framework."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    orch = [a for a in cai["agents"] if a["slug"] == "orchestrator"][0]
    caps = orch.get("capabilities", {})
    assert "raw" in caps, f"capabilities.raw missing: {caps}"
    assert "copilot-vscode" in caps["raw"]
    # The raw value should be the original tools string
    raw_val = str(caps["raw"]["copilot-vscode"])
    assert "read" in raw_val and "edit" in raw_val


def test_model_hint_captured(tmp_path: Path):
    """capabilities.model_hint captures the model: front-matter value."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    orch = [a for a in cai["agents"] if a["slug"] == "orchestrator"][0]
    caps = orch.get("capabilities", {})
    assert "model_hint" in caps, f"model_hint missing: {caps}"
    assert caps["model_hint"] is not None
    assert len(str(caps["model_hint"])) > 0


def test_capabilities_raw_and_model_hint_survive_canonical_round_trip(tmp_path: Path):
    """capabilities.raw and model_hint survive export → materialize → load."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    canon = tmp_path / "canon"
    materialize_canonical(cai, canon)
    loaded = load_canonical(canon)
    orch = [a for a in loaded["agents"] if a["slug"] == "orchestrator"][0]
    caps = orch.get("capabilities", {})
    assert "raw" in caps, f"capabilities.raw lost in round trip: {caps}"
    assert "copilot-vscode" in caps["raw"]
    assert "model_hint" in caps, f"model_hint lost in round trip: {caps}"


# ---------------------------------------------------------------------------
# references[]
# ---------------------------------------------------------------------------

def test_references_captured_from_native_source(tmp_path: Path):
    """references[] captures files from the source team's references/ directory."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    refs = cai.get("references", [])
    assert len(refs) > 0, "no references captured"
    # All entries should have rel_path and content
    for ref in refs:
        assert "rel_path" in ref
        assert "content" in ref
        assert ref["rel_path"].startswith("references/")


def test_references_survive_canonical_round_trip(tmp_path: Path):
    """references[] survive export → materialize → load."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    canon = tmp_path / "canon"
    materialize_canonical(cai, canon)
    loaded = load_canonical(canon)
    refs = loaded.get("references", [])
    assert len(refs) > 0, "references lost in canonical round trip"
    original_refs = cai.get("references", [])
    assert len(refs) == len(original_refs), (
        f"reference count mismatch: {len(original_refs)} → {len(refs)}"
    )
