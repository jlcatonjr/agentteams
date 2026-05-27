"""Tests for agentteams.schedule_emit + model_routing Phase 5 extension."""

from __future__ import annotations

import json
from pathlib import Path

from agentteams import model_routing as mr
from agentteams import schedule_emit as se


def _src_agent(dir_path: Path, slug: str) -> None:
    body = f"---\nname: {slug}\ndescription: d\n---\n# {slug}\n"
    (dir_path / f"{slug}.agent.md").write_text(body, encoding="utf-8")


def test_agent_tier_critic_is_cheap():
    manifest = {"governance_agents": []}
    assert mr.agent_tier("critic", manifest) == "cheap"
    assert mr.agent_tier("retrieval-policy", manifest) == "cheap"
    assert mr.agent_tier("navigator", manifest) == "cheap"
    assert mr.agent_tier("reference-manager", manifest) == "cheap"


def test_agent_tier_governance_still_cheap():
    manifest = {"governance_agents": ["cleanup", "security"]}
    assert mr.agent_tier("cleanup", manifest) == "cheap"
    assert mr.agent_tier("security", manifest) == "cheap"


def test_agent_tier_primary_default():
    manifest = {"governance_agents": []}
    assert mr.agent_tier("orchestrator", manifest) == "primary"
    assert mr.agent_tier("primary-producer", manifest) == "primary"


def test_build_routines_omits_missing_slugs(tmp_path: Path):
    src = tmp_path / "agents"
    src.mkdir()
    _src_agent(src, "work-summarizer")
    routines, omitted = se.build_routines(src)
    assert [r["agent"] for r in routines] == ["work-summarizer"]
    assert set(omitted) == {"drift", "post-production-auditor", "advisory"}


def test_build_routines_full_set(tmp_path: Path):
    src = tmp_path / "agents"
    src.mkdir()
    for slug in ("work-summarizer", "drift", "post-production-auditor", "advisory"):
        _src_agent(src, slug)
    routines, omitted = se.build_routines(src)
    assert len(routines) == 4
    assert omitted == []
    # Each routine carries cron, tier, bridge attribution.
    for r in routines:
        assert r["tier"] == "cheap"
        assert r["bridge"] == "copilot-vscode-to-claude"
        assert len(r["cron"].split()) == 5


def test_emit_writes_json_artifact(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    src.mkdir(parents=True)
    _src_agent(src, "work-summarizer")
    _src_agent(src, "drift")
    result = se.emit_schedule_artifact(source_dir=src, output_root=tmp_path)
    assert result.success
    out_path = tmp_path / ".claude" / "schedules.agentteams.json"
    assert out_path.exists()
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["schema_version"] == "1.0"
    assert len(parsed["routines"]) == 2
    assert "_agentteams_managed" in parsed


def test_emit_dry_run(tmp_path: Path):
    src = tmp_path / "agents"
    src.mkdir()
    _src_agent(src, "work-summarizer")
    se.emit_schedule_artifact(source_dir=src, output_root=tmp_path, dry_run=True)
    assert not (tmp_path / ".claude" / "schedules.agentteams.json").exists()


def test_emit_overwrite_false_skips_existing(tmp_path: Path):
    src = tmp_path / "agents"
    src.mkdir()
    _src_agent(src, "work-summarizer")
    se.emit_schedule_artifact(source_dir=src, output_root=tmp_path)
    r2 = se.emit_schedule_artifact(source_dir=src, output_root=tmp_path, overwrite=False)
    assert r2.skipped
    assert not r2.written
