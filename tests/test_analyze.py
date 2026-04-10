"""
Tests for src/analyze.py
"""

import pytest
from src.analyze import (
    build_manifest,
    classify_project_type,
    select_archetypes,
    detect_tool_agents,
    build_authority_hierarchy,
)


# ---------------------------------------------------------------------------
# Project type classification
# ---------------------------------------------------------------------------

def test_classify_writing():
    desc = {"project_goal": "Write a book with chapters and essays.", "deliverables": ["HTML chapters"]}
    assert classify_project_type(desc) == "writing"


def test_classify_software():
    desc = {"project_goal": "Build a Python API module.", "tools": [{"name": "Python"}]}
    assert classify_project_type(desc) == "software"


def test_classify_data_pipeline():
    desc = {"project_goal": "Build an ETL pipeline for CSV datasets.", "deliverables": ["CSV"]}
    assert classify_project_type(desc) == "data-pipeline"


def test_classify_unknown():
    desc = {"project_goal": "Do something."}
    assert classify_project_type(desc) == "unknown"


# ---------------------------------------------------------------------------
# Archetype selection
# ---------------------------------------------------------------------------

def test_archetypes_always_include_primary_and_quality(mocker):
    desc = {"project_goal": "Do something."}
    archetypes = select_archetypes(desc)
    assert "primary-producer" in archetypes
    assert "quality-auditor" in archetypes


def test_archetypes_writing_includes_cohesion():
    desc = {"project_goal": "Write a book with chapters.", "deliverables": ["HTML chapters"]}
    archetypes = select_archetypes(desc)
    assert "cohesion-repairer" in archetypes
    assert "style-guardian" in archetypes


def test_archetypes_code_includes_technical_validator():
    desc = {"project_goal": "Build a Python module with functions.", "tools": [{"name": "Python"}]}
    archetypes = select_archetypes(desc)
    assert "technical-validator" in archetypes


def test_archetypes_latex_includes_format_converter():
    desc = {"project_goal": "Produce a LaTeX manuscript.", "output_format": "PDF via LaTeX"}
    archetypes = select_archetypes(desc)
    assert "format-converter" in archetypes


def test_archetypes_bibliography_includes_reference_manager():
    desc = {
        "project_goal": "Academic paper with bibliography and citations.",
        "reference_db_path": "references.bib"
    }
    archetypes = select_archetypes(desc)
    assert "reference-manager" in archetypes


# ---------------------------------------------------------------------------
# Tool agent detection
# ---------------------------------------------------------------------------

def test_detect_tool_agents_no_specialists():
    tools = [
        {"name": "Python", "category": "language"},
        {"name": "FastAPI", "category": "framework"},
    ]
    result = detect_tool_agents(tools)
    assert result == []


def test_detect_tool_agents_with_specialist():
    tools = [
        {"name": "PostgreSQL", "version": "15", "category": "database", "needs_specialist_agent": True}
    ]
    result = detect_tool_agents(tools)
    assert len(result) == 1
    assert result[0]["tool_name"] == "PostgreSQL"
    assert result[0]["slug"].startswith("tool-")


# ---------------------------------------------------------------------------
# Authority hierarchy
# ---------------------------------------------------------------------------

def test_build_authority_hierarchy_empty():
    desc = {"project_goal": "Test project."}
    hierarchy = build_authority_hierarchy(desc)
    assert hierarchy == []


def test_build_authority_hierarchy_ordered():
    desc = {
        "project_goal": "Test",
        "authority_sources": [
            {"name": "A", "path": "a/", "rank": 2},
            {"name": "B", "path": "b/", "rank": 1},
        ]
    }
    hierarchy = build_authority_hierarchy(desc)
    assert hierarchy[0]["name"] == "B"
    assert hierarchy[1]["name"] == "A"


# ---------------------------------------------------------------------------
# Full manifest generation
# ---------------------------------------------------------------------------

def test_build_manifest_minimal():
    desc = {"project_goal": "Build the simplest possible project."}
    manifest = build_manifest(desc, framework="copilot-vscode")

    assert manifest["project_name"] == "MyProject"
    assert manifest["framework"] == "copilot-vscode"
    assert "orchestrator" in manifest["agent_slug_list"]
    assert "navigator" in manifest["agent_slug_list"]
    assert "primary-producer" in manifest["selected_archetypes"]
    assert isinstance(manifest["output_files"], list)
    assert len(manifest["output_files"]) > 0


def test_build_manifest_with_components():
    desc = {
        "project_name": "TestProject",
        "project_goal": "Build chapters for a book.",
        "components": [
            {"slug": "ch01-intro", "name": "Chapter 1", "number": 1},
            {"slug": "ch02-body", "name": "Chapter 2", "number": 2},
        ]
    }
    manifest = build_manifest(desc, framework="copilot-vscode")

    assert len(manifest["components"]) == 2
    assert "ch01-intro-expert" in manifest["workstream_expert_slugs"]
    assert "ch02-body-expert" in manifest["workstream_expert_slugs"]

    expert_files = [f for f in manifest["output_files"] if "expert" in f["path"]]
    assert len(expert_files) == 2


def test_build_manifest_includes_builder_agent():
    desc = {"project_goal": "Test project"}
    for fw in ("copilot-vscode", "copilot-cli", "claude"):
        manifest = build_manifest(desc, framework=fw)
        builder_files = [f for f in manifest["output_files"] if f["type"] == "builder"]
        assert len(builder_files) == 1, f"Expected 1 builder file for {fw}"


def test_build_manifest_manual_required_when_no_reference_db():
    desc = {
        "project_goal": "Academic paper with citations and bibliography.",
        "deliverables": ["chapters"]
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    # reference-manager archetype should be selected
    # If no reference_db_path is provided, REFERENCE_DB_PATH should be MANUAL
    ref_db = manifest["auto_resolved_placeholders"].get("REFERENCE_DB_PATH", "")
    assert "{MANUAL:" in ref_db or manifest.get("manual_required_placeholders")
