"""
Integration test: run the full pipeline on each example brief.
"""

import json
import pytest
from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _run_pipeline(brief_path: Path, tmp_path: Path, framework: str = "copilot-vscode") -> dict:
    from src import ingest, analyze, render, emit

    description = ingest.load(brief_path, scan_project=False)
    errors = ingest.validate(description)
    assert errors == [], f"Validation errors in {brief_path}: {errors}"

    manifest = analyze.build_manifest(description, framework=framework)
    assert manifest["project_name"]
    assert manifest["selected_archetypes"]

    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)
    assert rendered, "render_all returned empty list"

    result = emit.emit_all(rendered, output_dir=tmp_path, dry_run=False, overwrite=True, yes=True)
    assert result.success, f"emit failed: {result.errors}"

    return {"manifest": manifest, "rendered": rendered, "result": result}


# ---------------------------------------------------------------------------
# Research project
# ---------------------------------------------------------------------------

def test_research_project_pipeline(tmp_path):
    brief = EXAMPLES_DIR / "research-project" / "brief.json"
    if not brief.exists():
        pytest.skip("Research project example not found")

    output = _run_pipeline(brief, tmp_path)
    manifest = output["manifest"]

    assert manifest["project_type"] in ("writing", "research", "mixed")
    assert "reference-manager" in manifest["selected_archetypes"]
    assert "format-converter" in manifest["selected_archetypes"]
    assert len(manifest["components"]) >= 1

    # Check orchestrator agent was written
    orchestrator_file = tmp_path / "orchestrator.agent.md"
    assert orchestrator_file.exists()
    content = orchestrator_file.read_text(encoding="utf-8")
    assert "---" in content  # YAML front matter present
    assert manifest["project_name"] in content


# ---------------------------------------------------------------------------
# Software project
# ---------------------------------------------------------------------------

def test_software_project_pipeline(tmp_path):
    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("Software project example not found")

    output = _run_pipeline(brief, tmp_path)
    manifest = output["manifest"]

    assert manifest["project_type"] in ("software", "mixed")
    assert "technical-validator" in manifest["selected_archetypes"]

    # PostgreSQL tool specialist should be generated
    tool_slugs = [ta["slug"] for ta in manifest["tool_agents"]]
    assert any("postgresql" in s.lower() for s in tool_slugs)

    # Workstream experts for each component
    assert len(manifest["components"]) == 2


# ---------------------------------------------------------------------------
# Data pipeline project
# ---------------------------------------------------------------------------

def test_data_pipeline_project_pipeline(tmp_path):
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("Data pipeline example not found")

    output = _run_pipeline(brief, tmp_path)
    manifest = output["manifest"]

    assert manifest["project_type"] in ("data-pipeline", "software", "mixed")
    assert len(manifest["components"]) == 4

    # SETUP-REQUIRED.md should exist
    setup_file = tmp_path / "SETUP-REQUIRED.md"
    assert setup_file.exists()


# ---------------------------------------------------------------------------
# Multi-framework test
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("framework", ["copilot-vscode", "copilot-cli", "claude"])
def test_all_frameworks(tmp_path, framework):
    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("Software project example not found")

    fw_output = tmp_path / framework
    fw_output.mkdir()
    output = _run_pipeline(brief, fw_output, framework=framework)
    manifest = output["manifest"]

    assert manifest["framework"] == framework

    # Builder agent should exist for all frameworks
    builder_files = [f for f in manifest["output_files"] if f["type"] == "builder"]
    assert len(builder_files) == 1
