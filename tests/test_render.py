"""
Tests for src/render.py
"""

import pytest
from pathlib import Path
from agentteams.render import (
    resolve_placeholders,
    collect_unresolved_manual,
    validate_cross_refs,
    compute_template_hashes,
    _tool_placeholder_map,
    _reference_tool_placeholder_map,
)


# ---------------------------------------------------------------------------
# resolve_placeholders
# ---------------------------------------------------------------------------

def test_resolve_simple():
    template = "Hello, {PROJECT_NAME}!"
    mapping = {"PROJECT_NAME": "TestProject"}
    result = resolve_placeholders(template, mapping)
    assert result == "Hello, TestProject!"


def test_resolve_multiple():
    template = "{PROJECT_NAME} — {PROJECT_GOAL}"
    mapping = {"PROJECT_NAME": "Alpha", "PROJECT_GOAL": "Build the thing."}
    result = resolve_placeholders(template, mapping)
    assert result == "Alpha — Build the thing."


def test_resolve_leaves_unmatched():
    template = "Hello, {UNKNOWN_TOKEN}!"
    mapping = {"PROJECT_NAME": "TestProject"}
    result = resolve_placeholders(template, mapping)
    assert "{UNKNOWN_TOKEN}" in result


def test_resolve_leaves_manual_tokens():
    template = "Path: {MANUAL:REFERENCE_DB_PATH}"
    mapping = {}
    result = resolve_placeholders(template, mapping)
    assert "{MANUAL:REFERENCE_DB_PATH}" in result


def test_resolve_manual_with_override():
    template = "Path: {MANUAL:REFERENCE_DB_PATH}"
    mapping = {"MANUAL:REFERENCE_DB_PATH": "references/bibliography.bib"}
    # Manual tokens are NOT replaced by default auto-resolver (they need explicit removal)
    result = resolve_placeholders(template, mapping)
    assert "{MANUAL:" in result


def test_resolve_multiline():
    template = (
        "# {PROJECT_NAME}\n\n"
        "Goal: {PROJECT_GOAL}\n\n"
        "Output: {PRIMARY_OUTPUT_DIR}"
    )
    mapping = {
        "PROJECT_NAME": "MyProj",
        "PROJECT_GOAL": "Do stuff.",
        "PRIMARY_OUTPUT_DIR": "src/",
    }
    result = resolve_placeholders(template, mapping)
    assert "# MyProj" in result
    assert "Goal: Do stuff." in result
    assert "Output: src/" in result


# ---------------------------------------------------------------------------
# collect_unresolved_manual
# ---------------------------------------------------------------------------

def test_collect_unresolved_manual_empty():
    text = "No manual tokens here."
    result = collect_unresolved_manual(text)
    assert result == []


def test_collect_unresolved_manual_found():
    text = "Path: {MANUAL:REFERENCE_DB_PATH}\nStyle: {MANUAL:STYLE_REFERENCE_PATH}"
    result = collect_unresolved_manual(text)
    assert len(result) == 2
    assert "{MANUAL:REFERENCE_DB_PATH}" in result
    assert "{MANUAL:STYLE_REFERENCE_PATH}" in result


def test_collect_unresolved_manual_deduplication():
    text = "{MANUAL:FOO} and {MANUAL:FOO}"
    result = collect_unresolved_manual(text)
    # Returns all instances (not deduplicated)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# validate_cross_refs
# ---------------------------------------------------------------------------

def test_validate_cross_refs_no_warnings():
    rendered = [
        ("orchestrator.agent.md", "See `@navigator` for file locations."),
        ("navigator.agent.md", "Navigate the project."),
    ]
    warnings = validate_cross_refs(rendered)
    assert warnings == []


def test_validate_cross_refs_with_warning():
    rendered = [
        ("orchestrator.agent.md", "Invoke `@nonexistent-agent` for help."),
    ]
    warnings = validate_cross_refs(rendered)
    assert any("nonexistent-agent" in w for w in warnings)


def test_validate_cross_refs_orchestrator_not_flagged():
    # @orchestrator should never be flagged even at the top of the stack
    rendered = [
        ("navigator.agent.md", "Return to `@orchestrator` when done."),
    ]
    warnings = validate_cross_refs(rendered)
    assert warnings == []


# ---------------------------------------------------------------------------
# compute_template_hashes
# ---------------------------------------------------------------------------

def test_compute_template_hashes(tmp_path):
    tpl_dir = tmp_path / "templates" / "universal"
    tpl_dir.mkdir(parents=True)
    tpl_file = tpl_dir / "orchestrator.template.md"
    tpl_file.write_text("# Template content", encoding="utf-8")

    manifest = {
        "output_files": [
            {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
            {"path": "SETUP-REQUIRED.md", "template": "", "type": "setup-required"},
        ]
    }

    hashes = compute_template_hashes(manifest, templates_dir=tmp_path / "templates")
    assert "universal/orchestrator.template.md" in hashes
    assert len(hashes["universal/orchestrator.template.md"]) == 16


def test_compute_template_hashes_missing_template(tmp_path):
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()

    manifest = {
        "output_files": [
            {"path": "x.agent.md", "template": "nonexistent.template.md", "type": "agent"},
        ]
    }

    hashes = compute_template_hashes(manifest, templates_dir=tpl_dir)
    assert hashes == {}


# ---------------------------------------------------------------------------
# Fallback template handling (R-5)
# ---------------------------------------------------------------------------

def test_compute_template_hashes_fallback(tmp_path):
    """When category template is missing, fallback template hash is used."""
    tpl_dir = tmp_path / "templates" / "domain"
    tpl_dir.mkdir(parents=True)
    fallback = tpl_dir / "tool-specific.template.md"
    fallback.write_text("# Generic tool template", encoding="utf-8")

    manifest = {
        "output_files": [
            {
                "path": "tool-postgresql.agent.md",
                "template": "domain/tool-database.template.md",
                "fallback_template": "domain/tool-specific.template.md",
                "type": "agent",
            },
        ]
    }

    hashes = compute_template_hashes(manifest, templates_dir=tmp_path / "templates")
    assert "domain/tool-specific.template.md" in hashes


# ---------------------------------------------------------------------------
# Phase 5: _tool_placeholder_map and _reference_tool_placeholder_map (P5.1-P5.5)
# ---------------------------------------------------------------------------

def test_tool_placeholder_map_docs_url_resolves():
    """TOOL_DOCS_URL resolves to the provided URL in tool agent placeholder map."""
    tool_agent = {
        "tool_name": "PostgreSQL",
        "tool_version": "15",
        "tool_category": "database",
        "docs_url": "https://www.postgresql.org/docs/",
        "api_surface": "",
        "common_patterns": "",
        "config_files": [],
        "invocation_command": "",
        "invocation_target": "",
    }
    mapping = _tool_placeholder_map(tool_agent)
    assert mapping["TOOL_DOCS_URL"] == "https://www.postgresql.org/docs/"


def test_tool_placeholder_map_docs_url_falls_back_to_manual():
    """TOOL_DOCS_URL falls back to {MANUAL:TOOL_DOCS_URL} when docs_url is absent."""
    tool_agent = {
        "tool_name": "PostgreSQL",
        "tool_version": "15",
        "tool_category": "database",
        "docs_url": "",
        "api_surface": "",
        "common_patterns": "",
        "config_files": [],
        "invocation_command": "",
        "invocation_target": "",
    }
    mapping = _tool_placeholder_map(tool_agent)
    assert mapping["TOOL_DOCS_URL"] == "{MANUAL:TOOL_DOCS_URL}"


def test_tool_placeholder_map_api_surface_resolves():
    """TOOL_API_SURFACE resolves from the tool agent dict."""
    tool_agent = {
        "tool_name": "PostgreSQL",
        "tool_version": "15",
        "tool_category": "database",
        "docs_url": "",
        "api_surface": "SELECT, INSERT, UPDATE, DELETE",
        "common_patterns": "",
        "config_files": [],
        "invocation_command": "",
        "invocation_target": "",
    }
    mapping = _tool_placeholder_map(tool_agent)
    assert mapping["TOOL_API_SURFACE"] == "SELECT, INSERT, UPDATE, DELETE"


def test_tool_placeholder_map_api_surface_falls_back_to_manual():
    """TOOL_API_SURFACE falls back to {MANUAL:TOOL_API_SURFACE} when empty."""
    tool_agent = {
        "tool_name": "PostgreSQL",
        "tool_version": "15",
        "tool_category": "database",
        "docs_url": "",
        "api_surface": "",
        "common_patterns": "",
        "config_files": [],
        "invocation_command": "",
        "invocation_target": "",
    }
    mapping = _tool_placeholder_map(tool_agent)
    assert mapping["TOOL_API_SURFACE"] == "{MANUAL:TOOL_API_SURFACE}"


def test_tool_placeholder_map_common_patterns_resolves():
    """TOOL_COMMON_PATTERNS resolves from the tool agent dict."""
    tool_agent = {
        "tool_name": "PostgreSQL",
        "tool_version": "15",
        "tool_category": "database",
        "docs_url": "",
        "api_surface": "",
        "common_patterns": "Use connection pooling.",
        "config_files": [],
        "invocation_command": "",
        "invocation_target": "",
    }
    mapping = _tool_placeholder_map(tool_agent)
    assert mapping["TOOL_COMMON_PATTERNS"] == "Use connection pooling."


def test_tool_placeholder_map_common_patterns_falls_back_to_manual():
    """TOOL_COMMON_PATTERNS falls back to {MANUAL:TOOL_COMMON_PATTERNS} when empty."""
    tool_agent = {
        "tool_name": "PostgreSQL",
        "tool_version": "15",
        "tool_category": "database",
        "docs_url": "",
        "api_surface": "",
        "common_patterns": "",
        "config_files": [],
        "invocation_command": "",
        "invocation_target": "",
    }
    mapping = _tool_placeholder_map(tool_agent)
    assert mapping["TOOL_COMMON_PATTERNS"] == "{MANUAL:TOOL_COMMON_PATTERNS}"


def test_tool_placeholder_map_config_files_defaults_to_na():
    """TOOL_CONFIG_FILES defaults to N/A when no config files are defined."""
    tool_agent = {
        "tool_name": "SQLite",
        "tool_version": "",
        "tool_category": "database",
        "docs_url": "https://www.sqlite.org/docs.html",
        "api_surface": "sqlite3 CLI",
        "common_patterns": "Use parameterized queries.",
        "config_files": [],
        "invocation_command": "",
        "invocation_target": "",
    }
    mapping = _tool_placeholder_map(tool_agent)
    assert mapping["TOOL_CONFIG_FILES"] == "N/A"


def test_reference_tool_placeholder_map_docs_url_resolves():
    """TOOL_DOCS_URL resolves in reference tool placeholder map."""
    ref_tool = {
        "tool_name": "pandas",
        "tool_version": "2.0",
        "tool_category": "library",
        "docs_url": "https://pandas.pydata.org/docs/",
        "api_surface": "",
        "common_patterns": "",
        "config_files": [],
    }
    mapping = _reference_tool_placeholder_map(ref_tool)
    assert mapping["TOOL_DOCS_URL"] == "https://pandas.pydata.org/docs/"


def test_reference_tool_placeholder_map_docs_url_falls_back_to_manual():
    """TOOL_DOCS_URL falls back to {MANUAL:TOOL_DOCS_URL} in reference tool map when empty."""
    ref_tool = {
        "tool_name": "pandas",
        "tool_version": "2.0",
        "tool_category": "library",
        "docs_url": "",
        "api_surface": "",
        "common_patterns": "",
        "config_files": [],
    }
    mapping = _reference_tool_placeholder_map(ref_tool)
    assert mapping["TOOL_DOCS_URL"] == "{MANUAL:TOOL_DOCS_URL}"


def test_reference_tool_placeholder_map_api_surface_resolves():
    """TOOL_API_SURFACE resolves in reference tool placeholder map."""
    ref_tool = {
        "tool_name": "pandas",
        "tool_version": "2.0",
        "tool_category": "library",
        "docs_url": "",
        "api_surface": "DataFrame, Series, read_csv",
        "common_patterns": "",
        "config_files": [],
    }
    mapping = _reference_tool_placeholder_map(ref_tool)
    assert mapping["TOOL_API_SURFACE"] == "DataFrame, Series, read_csv"


def test_reference_tool_placeholder_map_common_patterns_resolves():
    """TOOL_COMMON_PATTERNS resolves in reference tool placeholder map."""
    ref_tool = {
        "tool_name": "pandas",
        "tool_version": "2.0",
        "tool_category": "library",
        "docs_url": "",
        "api_surface": "",
        "common_patterns": "Prefer vectorised operations over apply().",
        "config_files": [],
    }
    mapping = _reference_tool_placeholder_map(ref_tool)
    assert mapping["TOOL_COMMON_PATTERNS"] == "Prefer vectorised operations over apply()."


# ---------------------------------------------------------------------------
# Phase 5: template content assertions (P5.4-P5.5)
# ---------------------------------------------------------------------------

def test_tool_reference_template_has_no_yaml_frontmatter():
    """tool-reference.template.md must not begin with YAML front matter (--- block)."""
    import os
    templates_dir = Path(__file__).parent.parent / "agentteams" / "templates"
    ref_template = templates_dir / "domain" / "tool-reference.template.md"
    assert ref_template.exists(), "tool-reference.template.md not found"
    content = ref_template.read_text(encoding="utf-8")
    assert not content.startswith("---"), (
        "tool-reference.template.md must not have YAML front matter"
    )


def test_tool_cli_template_uses_auto_resolved_api_surface():
    """tool-cli.template.md must use {TOOL_API_SURFACE} not {MANUAL:TOOL_API_SURFACE}."""
    templates_dir = Path(__file__).parent.parent / "agentteams" / "templates"
    cli_template = templates_dir / "domain" / "tool-cli.template.md"
    assert cli_template.exists(), "tool-cli.template.md not found"
    content = cli_template.read_text(encoding="utf-8")
    assert "{TOOL_API_SURFACE}" in content, (
        "tool-cli.template.md must contain {TOOL_API_SURFACE} placeholder"
    )
    assert "{MANUAL:TOOL_API_SURFACE}" not in content, (
        "tool-cli.template.md must not contain literal {MANUAL:TOOL_API_SURFACE}"
    )
