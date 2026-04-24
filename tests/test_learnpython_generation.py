"""
test_learnpython_generation.py — Integration tests for both agent-team generation paths
demonstrated with the LearnPythonStatsEcon project brief.

Path A — Fresh Generation:
    Run the full ingest→analyze→render→emit pipeline from brief.json without
    referencing any existing agent files. Validates that all expected agent files
    are written with correct framework-specific format.

Path B — Format Migration (convert_team):
    Read an existing copilot-vscode agent team, apply the ClaudeAdapter
    transformation, and write to a new .claude/agents/ directory. Validates that
    prose body is preserved, VS Code YAML is replaced with Claude front matter,
    and the output directory structure is correct.

Both tests use pytest's tmp_path fixture and do NOT write to the real
Learn-Python-for-Stats-and-Econ repository.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"
TEMPLATES_DIR = REPO_ROOT / "agentteams" / "templates"
LEARN_PYTHON_BRIEF = EXAMPLES_DIR / "learn-python-for-stats-and-econ" / "brief.json"

# External live repo (may not be present on all machines / CI)
LIVE_REPO = Path("/Users/jamescaton/githubrepositories/Learn-Python-for-Stats-and-Econ")
LIVE_AGENTS = LIVE_REPO / ".github" / "agents"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ADAPTERS_MAP: dict[str, type] = {}


def _get_adapters() -> dict[str, type]:
    global _ADAPTERS_MAP
    if not _ADAPTERS_MAP:
        from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
        from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
        from agentteams.frameworks.claude import ClaudeAdapter

        _ADAPTERS_MAP = {
            "copilot-vscode": CopilotVSCodeAdapter,
            "copilot-cli": CopilotCLIAdapter,
            "claude": ClaudeAdapter,
        }
    return _ADAPTERS_MAP


def _run_fresh_pipeline(
    brief_path: Path, tmp_path: Path, framework: str = "copilot-vscode"
) -> dict:
    """Run the full agentteams pipeline from a brief.json and return result context."""
    from agentteams import ingest, analyze, render, emit
    from agentteams import graph as _graph

    adapter = _get_adapters()[framework]()

    description = ingest.load(brief_path, scan_project=False)
    errors = ingest.validate(description)
    assert errors == [], f"Validation errors in {brief_path}: {errors}"

    manifest = analyze.build_manifest(description, framework=framework)

    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)
    assert rendered, "render_all returned empty list"

    final_rendered: list[tuple[str, str]] = []
    for rel_path, content in rendered:
        lower = rel_path.lower()
        if "copilot-instructions" in lower or rel_path.endswith("/CLAUDE.md") or rel_path == "../CLAUDE.md":
            file_type = "instructions"
            content = adapter.render_instructions_file(content, manifest)
        elif "SETUP-REQUIRED" in rel_path:
            file_type = "setup-required"
        elif "team-builder" in rel_path:
            file_type = "builder"
            slug = Path(rel_path).stem.replace(".agent", "")
            content = adapter.render_agent_file(content, slug, manifest)
        elif rel_path.startswith("references/") or "/references/" in rel_path:
            file_type = "reference"
        else:
            file_type = "agent"
            slug = Path(rel_path).stem.replace(".agent", "")
            content = adapter.render_agent_file(content, slug, manifest)

        final_path = adapter.finalize_output_path(rel_path, file_type)
        final_rendered.append((final_path, content))

    graph_content = _graph.generate_graph_document(
        dict(final_rendered), project_name=manifest.get("project_name", "")
    )
    final_rendered.append(("references/pipeline-graph.md", graph_content))

    result = emit.emit_all(final_rendered, output_dir=tmp_path, dry_run=False, overwrite=True, yes=True)
    assert result.success, f"emit failed: {result.errors}"

    return {"manifest": manifest, "rendered": final_rendered, "result": result}


# ---------------------------------------------------------------------------
# PATH A — Fresh Generation (from brief.json, copilot-vscode)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LEARN_PYTHON_BRIEF.exists(), reason="learn-python brief.json not found")
def test_path_a_fresh_generation_vscode(tmp_path):
    """Path A: Fresh pipeline from brief.json produces correct copilot-vscode agents."""
    ctx = _run_fresh_pipeline(LEARN_PYTHON_BRIEF, tmp_path, framework="copilot-vscode")
    manifest = ctx["manifest"]

    assert manifest["project_name"] == "LearnPythonStatsEcon"
    assert len(manifest["components"]) == 9  # ch1–ch9

    # Orchestrator must exist
    orch = tmp_path / "orchestrator.agent.md"
    assert orch.exists(), "orchestrator.agent.md not found"
    orch_content = orch.read_text(encoding="utf-8")
    assert "LearnPythonStatsEcon" in orch_content
    # Must have VS Code YAML front matter
    assert orch_content.startswith("---")
    assert "user-invokable: true" in orch_content

    # All 9 chapter expert agents must exist
    for i in range(1, 10):
        slugs = {
            1: "ch1-essentials-expert",
            2: "ch2-lists-expert",
            3: "ch3-numpy-pandas-expert",
            4: "ch4-functional-expert",
            5: "ch5-probability-expert",
            6: "ch6-hypothesis-expert",
            7: "ch7-ols-expert",
            8: "ch8-advanced-expert",
            9: "ch9-abm-expert",
        }
        agent_file = tmp_path / f"{slugs[i]}.agent.md"
        assert agent_file.exists(), f"{slugs[i]}.agent.md not written"
        content = agent_file.read_text(encoding="utf-8")
        assert "LearnPythonStatsEcon" in content

    # team-builder must use .agent.md extension (not .md)
    team_builder = tmp_path / "team-builder.agent.md"
    assert team_builder.exists(), "team-builder.agent.md not found"

    # copilot-instructions.md must be written at parent level
    instructions = tmp_path.parent / "copilot-instructions.md"
    assert instructions.exists(), "copilot-instructions.md not found"

    # No unresolved auto-placeholders in any agent file
    for agent_file in tmp_path.glob("*.agent.md"):
        content = agent_file.read_text(encoding="utf-8")
        unresolved = re.findall(r"\{AUTO:[^}]+\}", content)
        assert not unresolved, f"Unresolved {{AUTO:*}} placeholders in {agent_file.name}: {unresolved}"


@pytest.mark.skipif(not LEARN_PYTHON_BRIEF.exists(), reason="learn-python brief.json not found")
def test_path_a_fresh_generation_claude(tmp_path):
    """Path A: Fresh pipeline from brief.json produces correct claude format agents."""
    # Use a subdirectory as output_dir so its parent is also within tmp_path
    # (avoids cross-test contamination via shared pytest tmp parents).
    agents_dir = tmp_path / "agents"
    ctx = _run_fresh_pipeline(LEARN_PYTHON_BRIEF, agents_dir, framework="claude")
    manifest = ctx["manifest"]

    assert manifest["project_name"] == "LearnPythonStatsEcon"

    # Claude agents use .md extension, not .agent.md
    orch = agents_dir / "orchestrator.md"
    assert orch.exists(), "orchestrator.md not found (claude format)"
    orch_content = orch.read_text(encoding="utf-8")
    assert "LearnPythonStatsEcon" in orch_content

    # Claude front matter: must have name and allowed-tools, no user-invokable
    assert "allowed-tools:" in orch_content
    assert "user-invokable:" not in orch_content
    assert "handoffs:" not in orch_content

    # CLAUDE.md instructions file at parent of agents_dir (i.e. tmp_path)
    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists(), "CLAUDE.md not found"
    copilot_instructions = tmp_path / "copilot-instructions.md"
    assert not copilot_instructions.exists(), "copilot-instructions.md should NOT exist for claude framework"


@pytest.mark.skipif(not LEARN_PYTHON_BRIEF.exists(), reason="learn-python brief.json not found")
def test_path_a_manifest_has_expected_archetypes_and_components(tmp_path):
    """Path A: Manifest for learn-python project selects expected archetypes."""
    from agentteams import ingest, analyze

    description = ingest.load(LEARN_PYTHON_BRIEF, scan_project=False)
    manifest = analyze.build_manifest(description, framework="copilot-vscode")

    # All 9 components present
    component_slugs = {c["slug"] for c in manifest["components"]}
    expected_slugs = {
        "ch1-essentials", "ch2-lists", "ch3-numpy-pandas", "ch4-functional",
        "ch5-probability", "ch6-hypothesis", "ch7-ols", "ch8-advanced", "ch9-abm",
    }
    assert expected_slugs == component_slugs

    # Governance agents always present
    for slug in ("adversarial", "conflict-auditor", "security", "navigator"):
        assert slug in manifest["agent_slug_list"], f"Missing governance agent: {slug}"

    # Domain agents always present
    for slug in ("primary-producer", "quality-auditor", "technical-validator"):
        assert slug in manifest["agent_slug_list"], f"Missing domain agent: {slug}"



@pytest.mark.skipif(not LEARN_PYTHON_BRIEF.exists(), reason="learn-python brief.json not found")
def test_path_a_fresh_generation_cli(tmp_path):
    """Path A: Fresh pipeline from brief.json produces correct copilot-cli format agents."""
    # Use a subdirectory so its parent is also within tmp_path.
    agents_dir = tmp_path / "agents"
    ctx = _run_fresh_pipeline(LEARN_PYTHON_BRIEF, agents_dir, framework="copilot-cli")
    manifest = ctx["manifest"]

    assert manifest["project_name"] == "LearnPythonStatsEcon"

    # CLI agents use .md extension, not .agent.md
    orch = agents_dir / "orchestrator.md"
    assert orch.exists(), "orchestrator.md not found (copilot-cli format)"
    orch_content = orch.read_text(encoding="utf-8")
    assert "LearnPythonStatsEcon" in orch_content

    # No YAML front matter — CLI agents are plain Markdown
    assert not orch_content.startswith("---"), "copilot-cli agent must not have YAML front matter"

    # No handoff sections
    assert "## Handoffs" not in orch_content

    # team-builder uses .md extension for CLI
    team_builder = agents_dir / "team-builder.md"
    assert team_builder.exists(), "team-builder.md not found (copilot-cli format)"

    # copilot-instructions.md at parent of agents_dir
    instructions = tmp_path / "copilot-instructions.md"
    assert instructions.exists(), "copilot-instructions.md not found for copilot-cli framework"


# ---------------------------------------------------------------------------
# PATH B — Format Migration (convert_team)
# ---------------------------------------------------------------------------


def _make_minimal_vscode_agent(slug: str, project_name: str = "TestProject") -> str:
    """Return a minimal copilot-vscode format agent file for testing."""
    return (
        f"---\n"
        f"name: {slug} — {project_name}\n"
        f'description: "Test agent for {slug}"\n'
        f"user-invokable: false\n"
        f"tools: ['read', 'search']\n"
        f"model: [\"Claude Sonnet 4.6 (copilot)\"]\n"
        f"handoffs:\n"
        f"  - label: Example Handoff\n"
        f"    agent: orchestrator\n"
        f"    send: false\n"
        f"---\n"
        f"\n"
        f"# {slug} — {project_name}\n"
        f"\n"
        f"This is the **{slug}** agent body prose.\n"
        f"\n"
        f"## Responsibilities\n"
        f"\n"
        f"- Responsibility one\n"
        f"- Responsibility two\n"
        f"\n"
        f"## Handoffs\n"
        f"\n"
        f"Some handoff guidance here.\n"
    )


def _make_minimal_claude_agent(slug: str, project_name: str = "TestProject") -> str:
    """Return a minimal claude format agent file for testing."""
    return (
        f"---\n"
        f"name: {slug} — {project_name}\n"
        f'description: "Test agent for {slug}"\n'
        f"allowed-tools: Bash, Read, Write, Edit\n"
        f"---\n"
        f"\n"
        f"# {slug} — {project_name}\n"
        f"\n"
        f"This is the **{slug}** agent body prose.\n"
        f"\n"
        f"## Responsibilities\n"
        f"\n"
        f"- Responsibility one\n"
        f"- Responsibility two\n"
    )


def _make_minimal_cli_agent(slug: str, project_name: str = "TestProject") -> str:
    """Return a minimal copilot-cli format agent file (plain Markdown, no front matter)."""
    return (
        f"# {slug} — {project_name}\n"
        f"\n"
        f"This is the **{slug}** agent body prose.\n"
        f"\n"
        f"## Responsibilities\n"
        f"\n"
        f"- Responsibility one\n"
        f"- Responsibility two\n"
    )


def _build_test_vscode_source(source_dir: Path) -> None:
    """Populate source_dir with a minimal copilot-vscode format agent team."""
    source_dir.mkdir(parents=True, exist_ok=True)
    refs_dir = source_dir / "references"
    refs_dir.mkdir(exist_ok=True)

    # Core agents
    for slug in ("orchestrator", "adversarial", "primary-producer"):
        (source_dir / f"{slug}.agent.md").write_text(
            _make_minimal_vscode_agent(slug), encoding="utf-8"
        )

    # Team builder (special file type)
    (source_dir / "team-builder.agent.md").write_text(
        _make_minimal_vscode_agent("team-builder"), encoding="utf-8"
    )

    # Instructions file
    (source_dir.parent / "copilot-instructions.md").write_text(
        "# Project Instructions\n\nThis is the project copilot instructions.\n",
        encoding="utf-8",
    )

    # SETUP-REQUIRED.md (passthrough)
    (source_dir / "SETUP-REQUIRED.md").write_text(
        "# Setup Required\n\nComplete these manual steps.\n", encoding="utf-8"
    )

    # references/build-log.json (passthrough dir)
    (refs_dir / "build-log.json").write_text(
        json.dumps({"schema_version": "1.2", "project_name": "TestProject", "framework": "copilot-vscode"}),
        encoding="utf-8",
    )


def test_path_b_convert_vscode_to_claude(tmp_path):
    """Path B: convert_team() converts copilot-vscode agents to claude format."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source" / ".github" / "agents"
    target_dir = tmp_path / "target" / ".claude" / "agents"

    _build_test_vscode_source(source_dir)

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"convert_team errors: {result.errors}"
    assert len(result.converted) > 0

    # Claude format: .md extension, not .agent.md
    orch = target_dir / "orchestrator.md"
    assert orch.exists(), "orchestrator.md not found in target"

    orch_content = orch.read_text(encoding="utf-8")

    # Must have Claude front matter
    assert orch_content.startswith("---")
    assert "name:" in orch_content
    assert "allowed-tools:" in orch_content

    # Must NOT have VS Code keys
    assert "user-invokable:" not in orch_content
    assert "handoffs:" not in orch_content

    # Prose body must be preserved
    assert "orchestrator — TestProject agent body prose." in orch_content or \
           "orchestrator — TestProject" in orch_content
    assert "Responsibility one" in orch_content
    assert "Responsibility two" in orch_content


def test_path_b_handoffs_section_stripped(tmp_path):
    """Path B: Handoff sections are removed during conversion to claude format."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "adversarial.agent.md").write_text(
        _make_minimal_vscode_agent("adversarial"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success
    converted = target_dir / "adversarial.md"
    assert converted.exists()

    content = converted.read_text(encoding="utf-8")
    # Handoffs heading section should be stripped
    assert "## Handoffs" not in content
    assert "Some handoff guidance here." not in content
    # Body prose outside handoffs should remain
    assert "Responsibility one" in content


def test_path_b_convert_vscode_to_cli(tmp_path):
    """Path B: convert_team() converts copilot-vscode agents to copilot-cli format."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "primary-producer.agent.md").write_text(
        _make_minimal_vscode_agent("primary-producer"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="copilot-cli",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success
    converted = target_dir / "primary-producer.md"
    assert converted.exists()

    content = converted.read_text(encoding="utf-8")
    # No YAML front matter
    assert not content.startswith("---")
    # Body prose preserved
    assert "Responsibility one" in content


def test_path_b_passthrough_files_copied(tmp_path):
    """Path B: SETUP-REQUIRED.md and references/ dir are copied verbatim."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    _build_test_vscode_source(source_dir)

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success
    assert (target_dir / "SETUP-REQUIRED.md").exists()
    assert (target_dir / "references" / "build-log.json").exists()


def test_path_b_instructions_placed_at_parent(tmp_path):
    """Path B: Instructions file is placed at parent of target_dir (project root level)."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source" / ".github" / "agents"
    target_dir = tmp_path / "target" / ".claude" / "agents"
    _build_test_vscode_source(source_dir)

    convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    # copilot-instructions.md from source parent becomes CLAUDE.md at target parent
    claude_md = target_dir.parent / "CLAUDE.md"
    assert claude_md.exists(), f"CLAUDE.md not found at {claude_md}"
    content = claude_md.read_text(encoding="utf-8")
    assert "Project Instructions" in content


def test_path_b_dry_run_writes_nothing(tmp_path):
    """Path B: dry_run=True reports conversions but writes no files."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()

    (source_dir / "adversarial.agent.md").write_text(
        _make_minimal_vscode_agent("adversarial"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        dry_run=True,
        overwrite=False,
    )

    assert result.dry_run is True
    assert len(result.converted) == 1  # Would convert adversarial.md
    assert not target_dir.exists()     # Nothing written


def test_path_b_overwrite_false_skips_existing(tmp_path):
    """Path B: overwrite=False skips files that already exist at target."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "navigator.agent.md").write_text(
        _make_minimal_vscode_agent("navigator"), encoding="utf-8"
    )

    # Pre-create the target file
    existing = target_dir / "navigator.md"
    existing.write_text("EXISTING CONTENT", encoding="utf-8")

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        dry_run=False,
        overwrite=False,
    )

    assert result.success
    assert len(result.skipped) == 1
    # Existing content must be unchanged
    assert existing.read_text(encoding="utf-8") == "EXISTING CONTENT"


def test_path_b_overwrite_true_replaces_existing(tmp_path):
    """Path B: overwrite=True replaces existing target files."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "navigator.agent.md").write_text(
        _make_minimal_vscode_agent("navigator"), encoding="utf-8"
    )

    existing = target_dir / "navigator.md"
    existing.write_text("EXISTING CONTENT", encoding="utf-8")

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        dry_run=False,
        overwrite=True,
    )

    assert result.success
    assert len(result.converted) == 1
    content = existing.read_text(encoding="utf-8")
    assert "EXISTING CONTENT" not in content
    assert "allowed-tools:" in content


def test_path_b_unknown_framework_raises(tmp_path):
    """Path B: convert_team() raises ValueError for unknown framework."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    source_dir.mkdir()

    with pytest.raises(ValueError, match="Unknown target framework"):
        convert_team(
            source_dir=source_dir,
            target_dir=tmp_path / "target",
            target_framework="not-a-framework",
        )


def test_path_b_missing_source_raises(tmp_path):
    """Path B: convert_team() raises FileNotFoundError if source_dir is missing."""
    from agentteams.convert import convert_team

    with pytest.raises(FileNotFoundError):
        convert_team(
            source_dir=tmp_path / "does_not_exist",
            target_dir=tmp_path / "target",
            target_framework="claude",
        )


# ---------------------------------------------------------------------------
# PATH B — Live repo conversion (skip when real repo not present)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LIVE_AGENTS.is_dir(), reason="Live Learn-Python-for-Stats-and-Econ repo not present")
def test_path_b_live_repo_dry_run_conversion(tmp_path):
    """Path B (live): Dry-run conversion of real copilot-vscode team to claude format."""
    from agentteams.convert import convert_team

    target_dir = tmp_path / ".claude" / "agents"

    result = convert_team(
        source_dir=LIVE_AGENTS,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "LearnPythonStatsEcon"},
        dry_run=True,
        overwrite=True,
    )

    assert result.success, f"Errors: {result.errors}"
    # Should convert all 28+ agent files
    assert len(result.converted) >= 25, f"Expected 25+ conversions, got {len(result.converted)}"
    assert result.dry_run is True
    # Nothing written to disk
    assert not target_dir.exists()

    # Verify all 9 chapter expert agents would be converted
    converted_names = {Path(p).name for p in result.converted}
    for slug in ("ch1-essentials-expert", "ch7-ols-expert", "ch9-abm-expert"):
        assert f"{slug}.md" in converted_names, f"Missing expected conversion for {slug}"


@pytest.mark.skipif(not LIVE_AGENTS.is_dir(), reason="Live Learn-Python-for-Stats-and-Econ repo not present")
def test_path_b_live_repo_actual_conversion(tmp_path):
    """Path B (live): Actual conversion of real copilot-vscode team to claude format in tmp_path."""
    from agentteams.convert import convert_team

    target_dir = tmp_path / ".claude" / "agents"

    result = convert_team(
        source_dir=LIVE_AGENTS,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "LearnPythonStatsEcon"},
        dry_run=False,
        overwrite=True,
    )

    assert result.success, f"Errors: {result.errors}"
    assert target_dir.is_dir()

    # Spot-check orchestrator conversion
    orch = target_dir / "orchestrator.md"
    assert orch.exists(), "orchestrator.md not found after live conversion"
    orch_content = orch.read_text(encoding="utf-8")

    # Claude front matter present
    assert orch_content.startswith("---")
    assert "allowed-tools:" in orch_content

    # No VS Code keys
    assert "user-invokable:" not in orch_content
    assert "handoffs:" not in orch_content

    # Prose body preserved (orchestrator has an Invariant Core section)
    assert "Invariant Core" in orch_content or "Constitutional" in orch_content

    # CLAUDE.md at parent of agents dir (.claude/CLAUDE.md)
    claude_md = target_dir.parent / "CLAUDE.md"
    assert claude_md.exists(), f"CLAUDE.md not found at {claude_md}"

    # All 9 chapter expert agents converted
    for slug in (
        "ch1-essentials-expert", "ch2-lists-expert", "ch3-numpy-pandas-expert",
        "ch4-functional-expert", "ch5-probability-expert", "ch6-hypothesis-expert",
        "ch7-ols-expert", "ch8-advanced-expert", "ch9-abm-expert",
    ):
        agent_file = target_dir / f"{slug}.md"
        assert agent_file.exists(), f"Converted {slug}.md not found"
        content = agent_file.read_text(encoding="utf-8")
        assert "allowed-tools:" in content, f"Missing Claude front matter in {slug}.md"
        assert "user-invokable:" not in content, f"VS Code key found in converted {slug}.md"


# ---------------------------------------------------------------------------
# PATH B — Missing conversion directions (claude→vscode, claude→cli,
#           cli→vscode, cli→claude)
# ---------------------------------------------------------------------------


def test_path_b_convert_claude_to_vscode(tmp_path):
    """Path B: Convert claude format agents to copilot-vscode format."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "navigator.md").write_text(
        _make_minimal_claude_agent("navigator"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="copilot-vscode",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"convert_team errors: {result.errors}"
    converted = target_dir / "navigator.agent.md"
    assert converted.exists(), "navigator.agent.md not found (claude→vscode)"
    content = converted.read_text(encoding="utf-8")

    # VS Code YAML front matter must be present with required keys
    assert content.startswith("---")
    assert "user-invokable:" in content
    assert "tools:" in content
    assert "model:" in content
    # allowed-tools from claude source may be present (harmless) but VS Code keys injected
    assert "name:" in content
    # Prose body preserved
    assert "Responsibility one" in content
    assert "Responsibility two" in content


def test_path_b_convert_claude_to_cli(tmp_path):
    """Path B: Convert claude format agents to copilot-cli format (plain Markdown)."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "security.md").write_text(
        _make_minimal_claude_agent("security"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="copilot-cli",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"convert_team errors: {result.errors}"
    converted = target_dir / "security.md"
    assert converted.exists(), "security.md not found (claude→cli)"
    content = converted.read_text(encoding="utf-8")

    # CLI: no YAML front matter
    assert not content.startswith("---"), "copilot-cli output must not have YAML front matter"
    # Prose body preserved
    assert "Responsibility one" in content
    assert "Responsibility two" in content


def test_path_b_convert_cli_to_vscode(tmp_path):
    """Path B: Convert copilot-cli format agents (plain Markdown) to copilot-vscode format."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "quality-auditor.md").write_text(
        _make_minimal_cli_agent("quality-auditor"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="copilot-vscode",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"convert_team errors: {result.errors}"
    converted = target_dir / "quality-auditor.agent.md"
    assert converted.exists(), "quality-auditor.agent.md not found (cli→vscode)"
    content = converted.read_text(encoding="utf-8")

    # VS Code YAML front matter injected (source had none)
    assert content.startswith("---")
    assert "user-invokable:" in content
    assert "name:" in content
    # Prose body preserved
    assert "Responsibility one" in content


def test_path_b_convert_cli_to_claude(tmp_path):
    """Path B: Convert copilot-cli format agents (plain Markdown) to claude format."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "conflict-auditor.md").write_text(
        _make_minimal_cli_agent("conflict-auditor"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"convert_team errors: {result.errors}"
    converted = target_dir / "conflict-auditor.md"
    assert converted.exists(), "conflict-auditor.md not found (cli→claude)"
    content = converted.read_text(encoding="utf-8")

    # Claude front matter injected (source had none)
    assert content.startswith("---")
    assert "allowed-tools:" in content
    assert "name:" in content
    # No VS Code keys
    assert "user-invokable:" not in content
    # Prose body preserved
    assert "Responsibility one" in content


def test_path_b_convert_claude_instructions_to_copilot_instructions(tmp_path):
    """Path B: CLAUDE.md in source parent is renamed to copilot-instructions.md for non-claude targets."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source" / ".claude" / "agents"
    target_dir = tmp_path / "target" / ".github" / "agents"
    source_dir.mkdir(parents=True)

    (source_dir / "orchestrator.md").write_text(
        _make_minimal_claude_agent("orchestrator"), encoding="utf-8"
    )
    (source_dir.parent / "CLAUDE.md").write_text(
        "# Claude Instructions\n\nSource instruction content.\n", encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="copilot-vscode",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"convert_team errors: {result.errors}"
    instructions = target_dir.parent / "copilot-instructions.md"
    assert instructions.exists(), "copilot-instructions.md not found for copilot-vscode target"
    assert "Source instruction content." in instructions.read_text(encoding="utf-8")
    assert not (target_dir.parent / "CLAUDE.md").exists()


def test_path_b_nested_subdir_agents_are_converted(tmp_path):
    """Path B: Agent files under nested subdirectories are recursively converted."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    nested = source_dir / "domain"
    nested.mkdir(parents=True)

    (nested / "navigator.agent.md").write_text(
        _make_minimal_vscode_agent("navigator"), encoding="utf-8"
    )

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success, f"convert_team errors: {result.errors}"
    converted = target_dir / "domain" / "navigator.md"
    assert converted.exists(), "Nested navigator.md not written"
    content = converted.read_text(encoding="utf-8")
    assert "allowed-tools:" in content
    assert "Responsibility one" in content


def test_path_b_unknown_file_types_are_skipped(tmp_path):
    """Path B: Non-markdown, non-passthrough files are skipped and not copied."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()

    (source_dir / "navigator.agent.md").write_text(
        _make_minimal_vscode_agent("navigator"), encoding="utf-8"
    )
    (source_dir / "notes.txt").write_text("Should be skipped", encoding="utf-8")

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=False,
    )

    assert result.success
    assert str(source_dir / "notes.txt") in result.skipped
    assert not (target_dir / "notes.txt").exists()
    assert (target_dir / "navigator.md").exists()


def test_path_b_references_overwrite_true_replaces_existing_directory(tmp_path):
    """Path B: overwrite=True replaces an existing references/ directory with source contents."""
    from agentteams.convert import convert_team

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    _build_test_vscode_source(source_dir)

    existing_refs = target_dir / "references"
    existing_refs.mkdir(parents=True)
    (existing_refs / "build-log.json").write_text(
        json.dumps({"project_name": "OldProject"}), encoding="utf-8"
    )
    (existing_refs / "stale.txt").write_text("stale", encoding="utf-8")

    result = convert_team(
        source_dir=source_dir,
        target_dir=target_dir,
        target_framework="claude",
        project_manifest={"project_name": "TestProject"},
        dry_run=False,
        overwrite=True,
    )

    assert result.success
    assert (target_dir / "references" / "build-log.json").exists()
    refs_payload = json.loads((target_dir / "references" / "build-log.json").read_text(encoding="utf-8"))
    assert refs_payload["project_name"] == "TestProject"
    assert not (target_dir / "references" / "stale.txt").exists()
