"""
Tests for src/analyze.py
"""

import pytest
from agentteams.analyze import (
    build_manifest,
    classify_project_type,
    select_archetypes,
    detect_tool_agents,
    detect_reference_tools,
    classify_tool_importance,
    build_authority_hierarchy,
    _has_unknown_tool_metadata,
    _format_unresolved_tool_list,
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

def test_archetypes_always_include_primary_and_quality():
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


def test_collect_manual_required_no_false_positives_from_composed_values():
    """Composed auto-resolved values containing {MANUAL:*} text should not be flagged.

    AUTHORITY_HIERARCHY, AUTHORITY_SOURCES_LIST, and STYLE_RULES_SUMMARY are
    composed strings that may embed MANUAL tokens (e.g., an authority source path
    of {MANUAL:REFERENCE_BOOK_PROJECT_PATH}) or documentation text (e.g.,
    style rules that mention {MANUAL:UPPER_SNAKE_CASE} as an example).
    Neither should create a false MANUAL entry for the composed placeholder itself.
    """
    desc = {
        "project_goal": "Test project.",
        "deliverables": ["src/"],
        "authority_sources": [
            {"name": "Source A", "path": "src/", "scope": "primary"},
            {"name": "Ref book", "path": "{MANUAL:REFERENCE_BOOK_PROJECT_PATH}", "scope": "reference"},
        ],
        "style_rules": [
            "Use {UPPER_SNAKE_CASE} for constants and {MANUAL:UPPER_SNAKE_CASE} for manual placeholders."
        ],
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    manual_keys = {item["placeholder"] for item in manifest["manual_required_placeholders"]}

    # Composed placeholders should NOT appear as manual-required
    assert "AUTHORITY_HIERARCHY" not in manual_keys
    assert "AUTHORITY_SOURCES_LIST" not in manual_keys
    assert "STYLE_RULES_SUMMARY" not in manual_keys

    # The embedded authority-path MANUAL token SHOULD appear
    assert "REFERENCE_BOOK_PROJECT_PATH" in manual_keys

    # Incidental documentation text like UPPER_SNAKE_CASE should NOT be flagged
    assert "UPPER_SNAKE_CASE" not in manual_keys


# ---------------------------------------------------------------------------
# Tool importance classification (R-1)
# ---------------------------------------------------------------------------

def test_classify_tool_importance_explicit_specialist():
    tool = {"name": "CustomDB", "category": "other", "needs_specialist_agent": True}
    assert classify_tool_importance(tool) == "specialist"


def test_classify_tool_importance_explicit_false_overrides_category():
    """Explicit False still allows auto-classification by category."""
    tool = {"name": "PostgreSQL", "category": "database", "needs_specialist_agent": False}
    assert classify_tool_importance(tool) == "specialist"


def test_classify_tool_importance_database_category():
    tool = {"name": "MySQL", "category": "database"}
    assert classify_tool_importance(tool) == "specialist"


def test_classify_tool_importance_cli_category():
    tool = {"name": "Docker", "category": "cli"}
    assert classify_tool_importance(tool) == "specialist"


def test_classify_tool_importance_build_system_category():
    tool = {"name": "Maven", "category": "build-system"}
    assert classify_tool_importance(tool) == "specialist"


def test_classify_tool_importance_known_specialist_name():
    tool = {"name": "Terraform", "category": "other"}
    assert classify_tool_importance(tool) == "specialist"


def test_classify_tool_importance_framework_category():
    tool = {"name": "FastAPI", "category": "framework"}
    assert classify_tool_importance(tool) == "reference"


def test_classify_tool_importance_library_category():
    tool = {"name": "SQLAlchemy", "category": "library"}
    assert classify_tool_importance(tool) == "reference"


def test_classify_tool_importance_known_reference_name():
    tool = {"name": "pandas", "category": "other"}
    assert classify_tool_importance(tool) == "reference"


def test_classify_tool_importance_passive():
    tool = {"name": "Python", "category": "language"}
    assert classify_tool_importance(tool) == "passive"


def test_classify_tool_importance_unknown_passive():
    tool = {"name": "SomeObscureTool", "category": "other"}
    assert classify_tool_importance(tool) == "passive"


# ---------------------------------------------------------------------------
# Auto-promoted tool agents (R-1)
# ---------------------------------------------------------------------------

def test_detect_tool_agents_auto_promotion():
    """Database tools should get specialist agents even without needs_specialist_agent."""
    tools = [
        {"name": "PostgreSQL", "version": "15", "category": "database"},
        {"name": "Docker", "category": "cli"},
        {"name": "FastAPI", "category": "framework"},
        {"name": "Python", "category": "language"},
    ]
    result = detect_tool_agents(tools)
    slugs = [a["slug"] for a in result]
    assert "tool-postgresql" in slugs
    assert "tool-docker" in slugs
    # framework and language should NOT get specialist agents
    assert not any("fastapi" in s for s in slugs)
    assert not any("python" in s for s in slugs)


def test_detect_tool_agents_includes_category():
    """Tool agents should carry tool_category."""
    tools = [{"name": "PostgreSQL", "version": "15", "category": "database"}]
    result = detect_tool_agents(tools)
    assert result[0]["tool_category"] == "database"


# ---------------------------------------------------------------------------
# Reference tool detection (R-1/R-4)
# ---------------------------------------------------------------------------

def test_detect_reference_tools():
    tools = [
        {"name": "PostgreSQL", "category": "database"},
        {"name": "FastAPI", "category": "framework"},
        {"name": "pandas", "category": "library"},
        {"name": "Python", "category": "language"},
    ]
    refs = detect_reference_tools(tools)
    ref_names = [r["tool_name"] for r in refs]
    assert "FastAPI" in ref_names
    assert "pandas" in ref_names
    assert "PostgreSQL" not in ref_names
    assert "Python" not in ref_names


def test_detect_reference_tools_slugs():
    tools = [{"name": "FastAPI", "category": "framework"}]
    refs = detect_reference_tools(tools)
    assert refs[0]["slug"] == "ref-fastapi"


# ---------------------------------------------------------------------------
# Manifest with auto-promoted tools (R-1/R-4/R-5)
# ---------------------------------------------------------------------------

def test_build_manifest_auto_promotes_database():
    """A database tool without explicit needs_specialist_agent gets a specialist agent."""
    desc = {
        "project_goal": "Build a Python API with a PostgreSQL database.",
        "tools": [
            {"name": "PostgreSQL", "version": "15", "category": "database"},
            {"name": "FastAPI", "category": "framework"},
        ],
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    assert "tool-postgresql" in manifest["domain_agent_slugs"]
    # FastAPI should be in reference_tools not tool_agents
    ref_names = [r["tool_name"] for r in manifest["reference_tools"]]
    assert "FastAPI" in ref_names


def test_build_manifest_reference_files_planned():
    """Reference-tier tools should produce reference output files."""
    desc = {
        "project_goal": "Build a Python API with frameworks.",
        "tools": [
            {"name": "FastAPI", "category": "framework"},
            {"name": "SQLAlchemy", "category": "library"},
        ],
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    ref_files = [f for f in manifest["output_files"]
                 if f["type"] == "reference" and f["template"] == "domain/tool-reference.template.md"]
    assert len(ref_files) == 2
    ref_paths = [f["path"] for f in ref_files]
    assert any("fastapi" in p for p in ref_paths)
    assert any("sqlalchemy" in p for p in ref_paths)


def test_build_manifest_category_template_for_database():
    """Database tools should use tool-database template with fallback."""
    desc = {
        "project_goal": "Build an ETL pipeline with PostgreSQL database.",
        "tools": [{"name": "PostgreSQL", "version": "15", "category": "database"}],
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    tool_files = [f for f in manifest["output_files"] if "postgresql" in f["path"]]
    assert len(tool_files) == 1
    assert "tool-database" in tool_files[0]["template"]
    assert tool_files[0]["fallback_template"] == "domain/tool-specific.template.md"


# ---------------------------------------------------------------------------
# Component tool mapping (R-7)
# ---------------------------------------------------------------------------

def test_build_manifest_component_tools_preserved():
    """Component tools field should be preserved in normalized output."""
    desc = {
        "project_goal": "Build a data pipeline.",
        "components": [
            {"slug": "ingest", "name": "Ingest Module", "tools": ["PostgreSQL", "pandas"]},
        ],
        "tools": [
            {"name": "PostgreSQL", "category": "database"},
            {"name": "pandas", "category": "library"},
        ],
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    assert manifest["components"][0]["tools"] == ["PostgreSQL", "pandas"]


# ---------------------------------------------------------------------------
# Phase 5: api_surface and common_patterns flow through pipeline (P5.6-P5.7)
# ---------------------------------------------------------------------------

def test_detect_tool_agents_includes_api_surface():
    """api_surface from brief.json tools[] is carried into tool agent specs."""
    tools = [
        {
            "name": "PostgreSQL",
            "category": "database",
            "api_surface": "SELECT, INSERT, UPDATE, DELETE",
        }
    ]
    result = detect_tool_agents(tools)
    assert len(result) == 1
    assert result[0]["api_surface"] == "SELECT, INSERT, UPDATE, DELETE"


def test_detect_tool_agents_api_surface_defaults_to_empty():
    """api_surface defaults to empty string when absent from tool dict."""
    tools = [{"name": "PostgreSQL", "category": "database"}]
    result = detect_tool_agents(tools)
    assert result[0]["api_surface"] == ""


def test_detect_tool_agents_includes_common_patterns():
    """common_patterns from brief.json tools[] is carried into tool agent specs."""
    tools = [
        {
            "name": "PostgreSQL",
            "category": "database",
            "common_patterns": "Use connection pooling.",
        }
    ]
    result = detect_tool_agents(tools)
    assert result[0]["common_patterns"] == "Use connection pooling."


def test_detect_reference_tools_includes_api_surface():
    """api_surface is carried into reference tool specs."""
    tools = [
        {
            "name": "pandas",
            "category": "library",
            "api_surface": "DataFrame, Series, read_csv",
        }
    ]
    result = detect_reference_tools(tools)
    assert len(result) == 1
    assert result[0]["api_surface"] == "DataFrame, Series, read_csv"


def test_detect_reference_tools_includes_common_patterns():
    """common_patterns is carried into reference tool specs."""
    tools = [
        {
            "name": "pandas",
            "category": "library",
            "common_patterns": "Use vectorised operations.",
        }
    ]
    result = detect_reference_tools(tools)
    assert result[0]["common_patterns"] == "Use vectorised operations."


def test_detect_reference_tools_api_surface_defaults_to_empty():
    """api_surface defaults to empty string when absent from tool dict."""
    tools = [{"name": "pandas", "category": "library"}]
    result = detect_reference_tools(tools)
    assert result[0]["api_surface"] == "DataFrame, Series, read_csv, merge, groupby, pivot_table"


def test_detect_tool_agents_includes_docs_url():
    """docs_url is carried into tool agent specs."""
    tools = [
        {
            "name": "PostgreSQL",
            "category": "database",
            "docs_url": "https://www.postgresql.org/docs/",
        }
    ]
    result = detect_tool_agents(tools)
    assert result[0]["docs_url"] == "https://www.postgresql.org/docs/"


def test_detect_reference_tools_includes_docs_url():
    """docs_url is carried into reference tool specs."""
    tools = [
        {
            "name": "pandas",
            "category": "library",
            "docs_url": "https://pandas.pydata.org/docs/",
        }
    ]
    result = detect_reference_tools(tools)
    assert result[0]["docs_url"] == "https://pandas.pydata.org/docs/"


# ---------------------------------------------------------------------------
# _has_unknown_tool_metadata
# ---------------------------------------------------------------------------

def test_has_unknown_tool_metadata_all_filled():
    tool_agents = [
        {
            "slug": "tool-postgresql", "tool_name": "PostgreSQL",
            "docs_url": "https://postgresql.org/docs/",
            "api_surface": "SELECT, INSERT",
            "common_patterns": "Connection pooling.",
        }
    ]
    reference_tools = [
        {
            "slug": "ref-fastapi", "tool_name": "FastAPI",
            "docs_url": "https://fastapi.tiangolo.com/",
            "api_surface": "FastAPI, APIRouter, Depends",
            "common_patterns": "Use dependency injection.",
        }
    ]
    assert _has_unknown_tool_metadata(tool_agents, reference_tools) is False


def test_has_unknown_tool_metadata_missing_docs_url():
    tool_agents = [
        {
            "slug": "tool-customdb", "tool_name": "CustomDB",
            "docs_url": "",
            "api_surface": "SELECT, INSERT",
            "common_patterns": "Use indexes.",
        }
    ]
    assert _has_unknown_tool_metadata(tool_agents, []) is True


def test_has_unknown_tool_metadata_missing_api_surface():
    reference_tools = [
        {
            "slug": "ref-somelib", "tool_name": "SomeLib",
            "docs_url": "https://somelib.example.com/",
            "api_surface": "",
            "common_patterns": "Some patterns.",
        }
    ]
    assert _has_unknown_tool_metadata([], reference_tools) is True


def test_has_unknown_tool_metadata_missing_common_patterns():
    tool_agents = [
        {
            "slug": "tool-customdb", "tool_name": "CustomDB",
            "docs_url": "https://example.com/",
            "api_surface": "SELECT",
            "common_patterns": "",
        }
    ]
    assert _has_unknown_tool_metadata(tool_agents, []) is True


def test_has_unknown_tool_metadata_empty_lists():
    assert _has_unknown_tool_metadata([], []) is False


# ---------------------------------------------------------------------------
# _format_unresolved_tool_list
# ---------------------------------------------------------------------------

def test_format_unresolved_tool_list_no_tools():
    result = _format_unresolved_tool_list([], [])
    assert result == "No tools with missing metadata."


def test_format_unresolved_tool_list_all_filled():
    tool_agents = [
        {
            "slug": "tool-postgresql", "tool_name": "PostgreSQL",
            "docs_url": "https://postgresql.org/docs/",
            "api_surface": "SELECT, INSERT",
            "common_patterns": "Use connection pooling.",
        }
    ]
    result = _format_unresolved_tool_list(tool_agents, [])
    assert result == "No tools with missing metadata."


def test_format_unresolved_tool_list_specialist_missing_docs():
    tool_agents = [
        {
            "slug": "tool-customdb", "tool_name": "CustomDB",
            "docs_url": "",
            "api_surface": "SELECT",
            "common_patterns": "Use indexes.",
        }
    ]
    result = _format_unresolved_tool_list(tool_agents, [])
    assert "CustomDB" in result
    assert "tool-customdb.agent.md" in result
    assert "docs URL" in result


def test_format_unresolved_tool_list_reference_missing_api_surface():
    reference_tools = [
        {
            "slug": "ref-somelib", "tool_name": "SomeLib",
            "docs_url": "https://example.com/",
            "api_surface": "",
            "common_patterns": "Some patterns.",
        }
    ]
    result = _format_unresolved_tool_list([], reference_tools)
    assert "SomeLib" in result
    assert "references/ref-somelib-reference.md" in result
    assert "API surface" in result


def test_format_unresolved_tool_list_multiple_entries():
    tool_agents = [
        {
            "slug": "tool-customdb", "tool_name": "CustomDB",
            "docs_url": "", "api_surface": "", "common_patterns": "",
        }
    ]
    reference_tools = [
        {
            "slug": "ref-somelib", "tool_name": "SomeLib",
            "docs_url": "", "api_surface": "", "common_patterns": "",
        }
    ]
    result = _format_unresolved_tool_list(tool_agents, reference_tools)
    lines = result.splitlines()
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# select_archetypes — pip/pypi trigger (module-doc agents)
# ---------------------------------------------------------------------------

def test_select_archetypes_pip_includes_module_doc_author():
    desc = {"project_goal": "Distribute a pip package to PyPI."}
    archetypes = select_archetypes(desc)
    assert "module-doc-author" in archetypes


def test_select_archetypes_pip_includes_module_doc_validator():
    desc = {"project_goal": "Distribute a pip package to PyPI."}
    archetypes = select_archetypes(desc)
    assert "module-doc-validator" in archetypes


def test_select_archetypes_pypi_keyword_triggers_module_doc():
    desc = {"project_goal": "Publish a pypi package with changelog and readthedocs."}
    archetypes = select_archetypes(desc)
    assert "module-doc-author" in archetypes
    assert "module-doc-validator" in archetypes


def test_select_archetypes_module_doc_not_included_for_generic_project():
    # Avoid "pipeline" (contains "pip"), "install", or "package"
    desc = {"project_goal": "Build a simple ETL workflow for CSV data."}
    archetypes = select_archetypes(desc)
    assert "module-doc-author" not in archetypes
    assert "module-doc-validator" not in archetypes


# ---------------------------------------------------------------------------
# build_manifest — tool-doc-researcher auto-include
# ---------------------------------------------------------------------------

def test_build_manifest_tool_doc_researcher_when_tool_missing_metadata():
    """A specialist-tier tool with no docs_url/api_surface/common_patterns triggers tool-doc-researcher."""
    desc = {
        "project_goal": "Build a project with a custom internal database.",
        "tools": [
            # database category → specialist tier; not in _KNOWN_TOOL_METADATA
            {"name": "InternalDB", "category": "database"},
        ],
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    assert "tool-doc-researcher" in manifest["selected_archetypes"]


def test_build_manifest_no_tool_doc_researcher_when_metadata_complete():
    """A tool with all metadata fields filled does NOT trigger tool-doc-researcher."""
    desc = {
        "project_goal": "Build a project with a well-documented tool.",
        "tools": [
            {
                "name": "WellDocumentedTool",
                "category": "other",
                "docs_url": "https://example.com/docs/",
                "api_surface": "api_call(), run()",
                "common_patterns": "Always use context managers.",
            }
        ],
    }
    manifest = build_manifest(desc, framework="copilot-vscode")
    assert "tool-doc-researcher" not in manifest["selected_archetypes"]


def test_detect_reference_tools_enrich_known_tool_metadata():
    """Known reference tools inherit metadata when the brief omits it."""
    tools = [{"name": "plotly", "category": "library"}]
    result = detect_reference_tools(tools)
    assert result[0]["docs_url"] == "https://plotly.com/python/"
    assert "graph_objects.Figure" in result[0]["api_surface"]
    assert "self-contained HTML exports" in result[0]["common_patterns"]


def test_build_manifest_flags_missing_tool_reference_metadata():
    """Unknown reference-tool metadata is surfaced in manual setup items."""
    description = {
        "project_name": "TestProject",
        "project_goal": "Generate a research team.",
        "deliverables": ["docs"],
        "tools": [{"name": "CustomLib", "category": "library"}],
        "components": [],
    }
    manifest = build_manifest(description)
    reference_items = [
        item for item in manifest["manual_required_placeholders"]
        if item["agent_file"] == "references/ref-customlib-reference.md"
    ]
    placeholders = {item["placeholder"] for item in reference_items}
    assert placeholders == {"TOOL_DOCS_URL", "TOOL_API_SURFACE", "TOOL_COMMON_PATTERNS"}


def test_build_manifest_infers_component_sources_from_output_and_authority_paths():
    """Components inherit source paths when the brief omits an explicit sources list."""
    description = {
        "project_name": "TestProject",
        "project_goal": "Generate a research team.",
        "deliverables": ["docs"],
        "authority_sources": [
            {
                "name": "Data library",
                "path": "ResearchProject/datlib/",
                "scope": "Shared analysis code",
                "rank": 1,
            }
        ],
        "components": [
            {
                "slug": "research-project",
                "name": "Research Project",
                "output_file": "ResearchProject/notebook.ipynb",
                "description": "Analyze the project.",
                "sections": ["Overview"],
                "quality_criteria": ["Has references"],
            }
        ],
    }
    manifest = build_manifest(description)
    component = manifest["components"][0]
    assert component["sources"] == [
        "ResearchProject/notebook.ipynb",
        "ResearchProject/datlib/",
    ]
    assert not any(item["placeholder"] == "COMPONENT_SOURCES" for item in manifest["manual_required_placeholders"])


# ---------------------------------------------------------------------------
# Tool documentation researcher archetype
# ---------------------------------------------------------------------------

def test_has_unknown_tool_metadata_returns_true_when_field_missing():
    tool_agents = [{"slug": "tool-custom", "tool_name": "Custom", "docs_url": "", "api_surface": "", "common_patterns": ""}]
    assert _has_unknown_tool_metadata(tool_agents, []) is True


def test_has_unknown_tool_metadata_returns_false_when_all_fields_present():
    tool_agents = [{
        "slug": "tool-custom",
        "tool_name": "Custom",
        "docs_url": "https://example.com",
        "api_surface": "Foo, Bar",
        "common_patterns": "Use Foo for X.",
    }]
    assert _has_unknown_tool_metadata(tool_agents, []) is False


def test_has_unknown_tool_metadata_checks_reference_tools_too():
    reference_tools = [{"slug": "ref-mylib", "tool_name": "MyLib", "docs_url": "", "api_surface": "A", "common_patterns": "B"}]
    assert _has_unknown_tool_metadata([], reference_tools) is True


def test_format_unresolved_tool_list_specialist_with_gaps():
    tool_agents = [{
        "slug": "tool-custom",
        "tool_name": "Custom",
        "docs_url": "",
        "api_surface": "",
        "common_patterns": "",
    }]
    result = _format_unresolved_tool_list(tool_agents, [])
    assert "Custom" in result
    assert "tool-custom.agent.md" in result
    assert "docs URL" in result
    assert "API surface" in result
    assert "usage patterns" in result


def test_format_unresolved_tool_list_reference_with_gaps():
    reference_tools = [{
        "slug": "ref-mylib",
        "tool_name": "MyLib",
        "docs_url": "",
        "api_surface": "Some surface",
        "common_patterns": "",
    }]
    result = _format_unresolved_tool_list([], reference_tools)
    assert "MyLib" in result
    assert "references/ref-mylib-reference.md" in result
    assert "docs URL" in result
    # api_surface is present — should not appear in gaps
    assert "API surface" not in result


def test_format_unresolved_tool_list_no_gaps_returns_none_message():
    tool_agents = [{
        "slug": "tool-sqlite",
        "tool_name": "SQLite",
        "docs_url": "https://www.sqlite.org/docs.html",
        "api_surface": "CREATE TABLE",
        "common_patterns": "Use parameterized queries.",
    }]
    result = _format_unresolved_tool_list(tool_agents, [])
    assert result == "No tools with missing metadata."


def test_build_manifest_includes_tool_doc_researcher_for_unknown_tool():
    """tool-doc-researcher archetype is added when a tool has missing metadata."""
    description = {
        "project_name": "TestProject",
        "project_goal": "Build a research team.",
        "deliverables": ["docs"],
        "tools": [{"name": "UnknownLib", "category": "library"}],
        "components": [],
    }
    manifest = build_manifest(description)
    assert "tool-doc-researcher" in manifest["selected_archetypes"]
    assert "tool-doc-researcher" in manifest["agent_slug_list"]


def test_build_manifest_excludes_tool_doc_researcher_when_all_metadata_known():
    """tool-doc-researcher is NOT added when all tool metadata is auto-resolved."""
    description = {
        "project_name": "TestProject",
        "project_goal": "Build a research team.",
        "deliverables": ["docs"],
        "tools": [{"name": "pandas", "category": "library"}],
        "components": [],
    }
    manifest = build_manifest(description)
    assert "tool-doc-researcher" not in manifest["selected_archetypes"]


def test_build_manifest_tool_doc_researcher_file_is_planned():
    """tool-doc-researcher.agent.md is included in output_files when archetype is selected."""
    description = {
        "project_name": "TestProject",
        "project_goal": "Build a research team.",
        "deliverables": ["docs"],
        "tools": [{"name": "UnknownLib", "category": "library"}],
        "components": [],
    }
    manifest = build_manifest(description)
    planned_paths = [f["path"] for f in manifest["output_files"]]
    assert "tool-doc-researcher.agent.md" in planned_paths


def test_build_manifest_unresolved_tool_list_placeholder_populated():
    """UNRESOLVED_TOOL_LIST placeholder is populated in auto_resolved_placeholders."""
    description = {
        "project_name": "TestProject",
        "project_goal": "Build a research team.",
        "deliverables": ["docs"],
        "tools": [{"name": "UnknownLib", "category": "library"}],
        "components": [],
    }
    manifest = build_manifest(description)
    resolved = manifest["auto_resolved_placeholders"]
    assert "UNRESOLVED_TOOL_LIST" in resolved
    assert "UnknownLib" in resolved["UNRESOLVED_TOOL_LIST"]


def test_build_manifest_unresolved_tool_list_empty_when_all_known():
    """UNRESOLVED_TOOL_LIST reads 'No tools with missing metadata' when all tools are resolved."""
    description = {
        "project_name": "TestProject",
        "project_goal": "Build a research team.",
        "deliverables": ["docs"],
        "tools": [{"name": "pandas", "category": "library"}],
        "components": [],
    }
    manifest = build_manifest(description)
    resolved = manifest["auto_resolved_placeholders"]
    assert resolved["UNRESOLVED_TOOL_LIST"] == "No tools with missing metadata."
