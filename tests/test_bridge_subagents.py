"""Tests for agentteams.bridge_subagents (Phase 2)."""

from __future__ import annotations

from pathlib import Path

from agentteams import bridge_subagents as bs


def _src_agent(dir_path: Path, slug: str, description: str = "") -> Path:
    desc = description or f"description for {slug}"
    body = (
        "---\n"
        f"name: {slug}\n"
        f"description: {desc}\n"
        "user-invokable: false\n"
        "---\n\n"
        f"# {slug}\n\nCanonical {slug} agent body.\n"
    )
    p = dir_path / f"{slug}.agent.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_collect_source_agents_returns_only_agent_md(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    _src_agent(src, "cleanup")
    (src / "README.md").write_text("not an agent\n", encoding="utf-8")
    found = bs.collect_source_agents(src)
    names = sorted(p.name for p in found)
    assert names == ["cleanup.agent.md", "orchestrator.agent.md"]


def test_emit_stubs_writes_one_per_source(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    _src_agent(src, "cleanup")
    _src_agent(src, "security")
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert result.success
    assert len(result.written) == 3
    stub_dir = tmp_path / ".claude" / "agents"
    for slug in ("orchestrator", "cleanup", "security"):
        stub = stub_dir / f"{slug}.md"
        assert stub.exists(), f"missing stub {stub}"
        text = stub.read_text(encoding="utf-8")
        assert f"name: {slug}" in text
        assert "bridge: copilot-vscode-to-claude" in text
        assert "source_sha256:" in text
        assert ".github/agents/" in text


def test_emit_stubs_collapses_experts(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    _src_agent(src, "auth-module-expert")
    _src_agent(src, "tasks-api-expert")
    _src_agent(src, "billing-expert")
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub_dir = tmp_path / ".claude" / "agents"
    expert_stubs = list(stub_dir.glob("*-expert.md"))
    # No individual per-component expert stubs.
    assert [p.name for p in expert_stubs] == ["workstream-expert.md"]
    text = (stub_dir / "workstream-expert.md").read_text(encoding="utf-8")
    assert "name: workstream-expert" in text
    assert "collapsed_experts: 3" in text
    assert sorted(result.experts_collapsed) == [
        "auth-module-expert", "billing-expert", "tasks-api-expert"
    ]


def test_emit_stubs_dry_run_writes_nothing(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path, dry_run=True)
    assert len(result.written) == 1  # reported
    assert not (tmp_path / ".claude" / "agents" / "orchestrator.md").exists()


def test_emit_stubs_overwrite_false_skips_existing(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path, overwrite=False)
    assert result.skipped
    assert not result.written


def test_detect_stub_drift_returns_empty_when_fresh(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert bs.detect_stub_drift(source_dir=src, output_root=tmp_path) == []


def test_detect_stub_drift_flags_modified_source(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    p = _src_agent(src, "orchestrator")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    # Mutate the source after stub emission.
    p.write_text(p.read_text(encoding="utf-8") + "\n## Update\n", encoding="utf-8")
    drift = bs.detect_stub_drift(source_dir=src, output_root=tmp_path)
    assert len(drift) == 1
    assert drift[0]["slug"] == "orchestrator"
    assert drift[0]["recorded_sha"] != drift[0]["current_sha"]


def test_detect_stub_drift_flags_missing_source(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    p = _src_agent(src, "orchestrator")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    p.unlink()
    drift = bs.detect_stub_drift(source_dir=src, output_root=tmp_path)
    assert len(drift) == 1
    assert drift[0]["current_sha"] == "<missing>"


def test_emit_stubs_empty_source_dir_noop(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    src.mkdir(parents=True)
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert result.written == []
    assert result.skipped == []


# ---------------------------------------------------------------------------
# Goose subagent-stub recipes (plan P3) — opt-in parity with the claude path.
# ---------------------------------------------------------------------------

from agentteams import bridge_subagents_goose as bsg  # noqa: E402
from agentteams import host_features as hf  # noqa: E402
from agentteams.frameworks.goose import _validate_recipe_yaml  # noqa: E402


def test_goose_subagents_token_registered():
    # Must not raise HostFeatureError for any goose-bridge namespace.
    for ns in ("copilot-vscode", "claude", "copilot-cli"):
        hf.validate(f"bridge:{ns}-to-goose:subagents")


def test_goose_stubs_one_valid_recipe_per_agent_reserved_skipped(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")        # reserved -> skipped
    _src_agent(src, "team-builder")        # reserved -> skipped
    _src_agent(src, "cleanup")
    _src_agent(src, "security")
    (src / "_build-description.json").write_text("{}", encoding="utf-8")  # not an agent

    result = bsg.emit_goose_subagent_stubs(source_dir=src, output_root=tmp_path)
    recipes = tmp_path / ".goose" / "recipes"
    written = sorted(p.name for p in recipes.glob("*.yaml"))
    assert written == ["cleanup.yaml", "security.yaml"]   # reserved + non-agent excluded
    # every emitted stub is a structurally valid Goose recipe
    for p in recipes.glob("*.yaml"):
        assert _validate_recipe_yaml(p.read_text(encoding="utf-8")) == [], p.name
    assert any("orchestrator" in s for s in result.skipped)


def test_goose_stubs_never_overwrite_existing_recipe(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "cleanup")
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    sentinel = "version: \"1.0.0\"\ntitle: \"hand authored\"\n# DO NOT TOUCH\n"
    (recipes / "cleanup.yaml").write_text(sentinel, encoding="utf-8")

    result = bsg.emit_goose_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert (recipes / "cleanup.yaml").read_text(encoding="utf-8") == sentinel  # untouched
    assert any("cleanup.yaml" in s for s in result.skipped)


def test_goose_bridge_stubs_opt_in_default_off(tmp_path: Path):
    from agentteams.bridge import run_bridge
    src = tmp_path / "proj" / ".github" / "agents"
    _src_agent(src, "orchestrator")
    _src_agent(src, "cleanup")
    (src.parent / "copilot-instructions.md").write_text("# Instructions\n", encoding="utf-8")

    # Default: no token -> no per-agent stub recipes (only the bridge-owned one).
    run_bridge(source_dir=src, source_framework="copilot-vscode", target_framework="goose",
               output_root=tmp_path / "off", overwrite=True)
    off = sorted(p.name for p in (tmp_path / "off" / ".goose" / "recipes").glob("*.yaml"))
    assert off == ["bridge-orchestrator.yaml"]

    # Opt-in token -> a stub for the non-reserved agent appears.
    run_bridge(source_dir=src, source_framework="copilot-vscode", target_framework="goose",
               output_root=tmp_path / "on", overwrite=True,
               host_features=["bridge:copilot-vscode-to-goose:subagents"])
    on = sorted(p.name for p in (tmp_path / "on" / ".goose" / "recipes").glob("*.yaml"))
    assert on == ["bridge-orchestrator.yaml", "cleanup.yaml"]
