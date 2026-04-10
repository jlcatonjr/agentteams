"""
Tests for src/render.py
"""

import pytest
from pathlib import Path
from src.render import resolve_placeholders, collect_unresolved_manual, validate_cross_refs


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
