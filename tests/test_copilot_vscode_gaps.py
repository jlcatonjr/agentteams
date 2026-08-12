"""A.2 regression tests: copilot-vscode export/import gaps.

Report section 4.2: user-invokable silently flips to false, model is silently
dropped and replaced by a hardcoded default, and the agents: cross-reference
roster vanishes entirely on a native→canonical→native cycle.

A.3's raw_front_matter escape hatch captures these keys on export. This test
verifies they survive the full round trip — that the import side restores them
into the front matter so _ensure_yaml_front_matter doesn't overwrite them with
hardcoded defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.canonical import load_canonical, materialize_canonical
from agentteams.interop import export_to_cai, import_from_cai


def _round_trip_copilot_vscode(tmp_path: Path):
    """Export → materialize → load → import back to copilot-vscode."""
    source = Path(".github/agents")
    if not source.is_dir():
        pytest.skip("repo .github/agents not found")
    cai = export_to_cai(source, "copilot-vscode")
    canon_dir = tmp_path / "canon"
    materialize_canonical(cai, canon_dir)
    loaded = load_canonical(canon_dir)
    native_dir = tmp_path / "native" / ".github" / "agents"
    result = import_from_cai(loaded, "copilot-vscode", native_dir, overwrite=True)
    assert result.errors == [], f"import errors: {result.errors}"
    return cai, native_dir


def test_user_invokable_survives_round_trip(tmp_path: Path):
    """user-invokable: true must survive native→canonical→native, not flip to false."""
    cai, native_dir = _round_trip_copilot_vscode(tmp_path)
    # Find an agent with user-invokable: true (orchestrator)
    orch_cai = [a for a in cai["agents"] if a["slug"] == "orchestrator"][0]
    rfm = orch_cai.get("raw_front_matter", {})
    assert rfm.get("user-invokable") is True or rfm.get("user-invokable") == "true", (
        f"user-invokable not captured as true: {rfm.get('user-invokable')}"
    )
    # Check the reimported file
    reimported = (native_dir / "orchestrator.agent.md").read_text()
    assert "user-invokable: true" in reimported, (
        "user-invokable: true not found in reimported file — was overwritten with default"
    )


def test_model_survives_round_trip(tmp_path: Path):
    """model: front-matter value must survive, not be replaced by hardcoded default."""
    cai, native_dir = _round_trip_copilot_vscode(tmp_path)
    orch_cai = [a for a in cai["agents"] if a["slug"] == "orchestrator"][0]
    rfm = orch_cai.get("raw_front_matter", {})
    assert "model" in rfm, f"model not captured in raw_front_matter: {rfm}"
    # Check the reimported file has the real model value, not the default
    reimported = (native_dir / "orchestrator.agent.md").read_text()
    model_value = str(rfm["model"])
    # The value should be present (either as a flow list or quoted string)
    assert "Claude Sonnet 4.6" in reimported, (
        f"model value not found in reimported file"
    )


def test_agents_roster_survives_round_trip(tmp_path: Path):
    """agents: cross-reference roster must survive, not vanish."""
    cai, native_dir = _round_trip_copilot_vscode(tmp_path)
    orch_cai = [a for a in cai["agents"] if a["slug"] == "orchestrator"][0]
    rfm = orch_cai.get("raw_front_matter", {})
    assert "agents" in rfm, f"agents roster not captured in raw_front_matter: {rfm}"
    agents_list = rfm["agents"]
    assert isinstance(agents_list, list) and len(agents_list) > 1, (
        f"agents roster should have multiple entries: {agents_list}"
    )
    # Check the reimported file has an agents: block
    reimported = (native_dir / "orchestrator.agent.md").read_text()
    assert "agents:" in reimported, "agents: block missing from reimported file"
    # At least some slugs should be present
    assert "orchestrator" in reimported, "orchestrator slug missing from agents: roster"
