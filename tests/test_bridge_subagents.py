"""Tests for agentteams.bridge_subagents (Phase 2)."""

from __future__ import annotations

from pathlib import Path

from agentteams import bridge_subagents as bs


def _src_agent(dir_path: Path, slug: str, description: str = "", tools: str | None = None) -> Path:
    desc = description or f"description for {slug}"
    tools_line = f"tools: {tools}\n" if tools else ""
    body = (
        "---\n"
        f"name: {slug}\n"
        f"description: {desc}\n"
        f"{tools_line}"
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


def test_emit_stubs_never_leak_absolute_source_path(tmp_path: Path):
    """Security fix: a stub must never embed the operator's absolute filesystem path
    (which would leak the OS username into a file that may end up tracked/published).
    The relative path (already given in front matter + body) is sufficient — Claude
    resolves it against the repo root at read time."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    result = bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert result.success
    stub = tmp_path / ".claude" / "agents" / "orchestrator.md"
    text = stub.read_text(encoding="utf-8")
    assert str(tmp_path) not in text
    assert str(src) not in text
    assert "Source absolute path" not in text


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
# allowed-tools emission from source tools: field.
# ---------------------------------------------------------------------------


def test_parse_tools_list_python_list_syntax():
    assert bs._parse_tools_list("['read', 'edit']") == ["read", "edit"]
    assert bs._parse_tools_list("") == []
    assert bs._parse_tools_list("not-a-list") == []
    assert bs._parse_tools_list("[]") == []


def test_tools_to_allowed_full_work_summarizer_set():
    raw = "['read', 'search', 'execute', 'edit', 'agent']"
    assert bs._tools_to_allowed(raw) == ["Read", "Grep", "Glob", "Bash", "Edit", "Write", "Task"]


def test_tools_to_allowed_deduplicates():
    raw = "['edit', 'edit', 'read']"
    assert bs._tools_to_allowed(raw) == ["Edit", "Write", "Read"]


def test_tools_to_allowed_empty_returns_empty():
    assert bs._tools_to_allowed("[]") == []
    assert bs._tools_to_allowed("") == []


def test_emit_stubs_maps_tools_to_allowed_tools(tmp_path: Path):
    """tools: in source front matter → allowed-tools: in Claude stub."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "work-summarizer", tools="['read', 'search', 'execute', 'edit', 'agent']")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "work-summarizer.md").read_text(encoding="utf-8")
    assert "allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Task" in stub


def test_emit_stubs_no_tools_no_allowed_tools(tmp_path: Path):
    """No tools: in source → no allowed-tools: in stub (backward compat)."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "orchestrator.md").read_text(encoding="utf-8")
    assert "allowed-tools" not in stub


def test_emit_stubs_edit_only_includes_edit_and_write(tmp_path: Path):
    """edit tool maps to both Edit and Write."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "cleanup", tools="['read', 'edit']")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "cleanup.md").read_text(encoding="utf-8")
    assert "allowed-tools: Read, Edit, Write" in stub


# ---------------------------------------------------------------------------
# MAP-05: block-style tools: in source front matter must produce allowed-tools:
# ---------------------------------------------------------------------------

_BLOCK_STYLE_AGENT = """\
---
name: {slug}
description: {slug} agent
tools:
  - read
  - edit
user-invokable: false
---

# {slug}

Canonical {slug} body.
"""


def test_parse_front_matter_block_style_tools_returns_list_literal():
    """_parse_front_matter parses block-style tools: into a Python list literal."""
    text = "---\nname: x\ntools:\n  - read\n  - edit\n---\nbody\n"
    meta, body = bs._parse_front_matter(text)
    assert meta["name"] == "x"
    # The value must be a string that _parse_tools_list can evaluate.
    parsed = bs._parse_tools_list(meta["tools"])
    assert parsed == ["read", "edit"]


def test_parse_front_matter_inline_style_unaffected():
    """Inline flow-sequence tools: continues to work after the block-style fix."""
    text = "---\nname: x\ntools: ['read', 'edit']\n---\nbody\n"
    meta, _ = bs._parse_front_matter(text)
    parsed = bs._parse_tools_list(meta["tools"])
    assert parsed == ["read", "edit"]


def test_parse_front_matter_block_style_mixed_with_scalar_keys():
    """Scalar keys before and after a block-style key are parsed correctly."""
    text = (
        "---\n"
        "name: mixed-agent\n"
        "tools:\n"
        "  - execute\n"
        "  - search\n"
        "user-invokable: false\n"
        "---\nbody\n"
    )
    meta, _ = bs._parse_front_matter(text)
    assert meta["name"] == "mixed-agent"
    assert meta["user-invokable"] == "false"
    assert bs._parse_tools_list(meta["tools"]) == ["execute", "search"]


def test_emit_stubs_block_style_tools_emits_allowed_tools(tmp_path: Path):
    """Agent file with block-style tools: -> allowed-tools: in Claude stub."""
    src = tmp_path / ".github" / "agents"
    src.mkdir(parents=True)
    agent_text = _BLOCK_STYLE_AGENT.format(slug="work-summarizer")
    (src / "work-summarizer.agent.md").write_text(agent_text, encoding="utf-8")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "work-summarizer.md").read_text(
        encoding="utf-8"
    )
    # read -> Read; edit -> Edit, Write
    assert "allowed-tools: Read, Edit, Write" in stub


def test_emit_stubs_block_style_tools_quoted_items(tmp_path: Path):
    """Block-style tools with quoted item values (YAML string scalars) are parsed correctly."""
    src = tmp_path / ".github" / "agents"
    src.mkdir(parents=True)
    agent_text = (
        "---\n"
        "name: quoted-tools-agent\n"
        "description: agent with quoted block items\n"
        "tools:\n"
        "  - 'read'\n"
        "  - 'execute'\n"
        "user-invokable: false\n"
        "---\n\n"
        "# Agent body\n"
    )
    (src / "quoted-tools-agent.agent.md").write_text(agent_text, encoding="utf-8")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "quoted-tools-agent.md").read_text(
        encoding="utf-8"
    )
    # read -> Read; execute -> Bash
    assert "allowed-tools: Read, Bash" in stub


# ---------------------------------------------------------------------------
# MAP-13: parametric workstream-expert stub must emit allowed-tools.
# ---------------------------------------------------------------------------


def test_expert_stub_emits_union_of_tools(tmp_path: Path):
    """allowed-tools in parametric stub = union of all collapsed expert tools."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "auth-module-expert", tools="['read', 'edit']")
    _src_agent(src, "tasks-api-expert", tools="['read', 'execute', 'search']")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "workstream-expert.md").read_text(
        encoding="utf-8"
    )
    # Union of read+edit and read+execute+search, deduplicated, ordered first-seen.
    # read -> Read; edit -> Edit, Write; execute -> Bash; search -> Grep, Glob
    assert "allowed-tools: Read, Edit, Write, Bash, Grep, Glob" in stub


def test_expert_stub_no_tools_field_emits_no_allowed_tools(tmp_path: Path):
    """Expert source files with no tools: field → no allowed-tools: in stub."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "auth-module-expert")   # no tools= kwarg -> no tools: line
    _src_agent(src, "billing-expert")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "workstream-expert.md").read_text(
        encoding="utf-8"
    )
    assert "allowed-tools" not in stub


def test_expert_stub_deduplicates_overlapping_tools(tmp_path: Path):
    """Overlapping tool declarations across experts produce each Claude tool name once."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "alpha-expert", tools="['read', 'edit']")
    _src_agent(src, "beta-expert",  tools="['edit', 'read']")   # exact overlap
    _src_agent(src, "gamma-expert", tools="['execute']")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "workstream-expert.md").read_text(
        encoding="utf-8"
    )
    line = next(l for l in stub.splitlines() if l.startswith("allowed-tools:"))
    tools = [t.strip() for t in line.split(":", 1)[1].split(",")]
    # No duplicates.
    assert tools == list(dict.fromkeys(tools))
    # All expected tools present.
    assert set(tools) == {"Read", "Edit", "Write", "Bash"}


def test_expert_stub_partial_tools_field_covers_superset(tmp_path: Path):
    """Expert files where only some have a tools: field — stub still covers all declared tools."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "auth-module-expert", tools="['read', 'edit']")
    _src_agent(src, "reporting-expert")   # no tools: field — should be silently skipped
    _src_agent(src, "tasks-api-expert",  tools="['execute']")
    bs.emit_subagent_stubs(source_dir=src, output_root=tmp_path)
    stub = (tmp_path / ".claude" / "agents" / "workstream-expert.md").read_text(
        encoding="utf-8"
    )
    # read+edit -> Read, Edit, Write; execute -> Bash
    # The expert with no tools: field must not suppress the other experts' tools.
    assert "allowed-tools:" in stub
    line = next(l for l in stub.splitlines() if l.startswith("allowed-tools:"))
    tools = {t.strip() for t in line.split(":", 1)[1].split(",")}
    assert tools == {"Read", "Edit", "Write", "Bash"}


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


def test_goose_stubs_never_leak_absolute_source_path(tmp_path: Path):
    """Security fix: same as the Claude stub case — never embed the operator's
    absolute filesystem path in a generated recipe."""
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "cleanup")
    result = bsg.emit_goose_subagent_stubs(source_dir=src, output_root=tmp_path)
    assert result.written
    recipe = (tmp_path / ".goose" / "recipes" / "cleanup.yaml").read_text(encoding="utf-8")
    assert str(tmp_path) not in recipe
    assert str(src) not in recipe
    assert "Source absolute path" not in recipe


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
