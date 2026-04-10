"""
Tests for src/ingest.py
"""

import json
import textwrap
import pytest
from pathlib import Path
from src.ingest import load, validate, _slugify


# ---------------------------------------------------------------------------
# JSON loading
# ---------------------------------------------------------------------------

def test_load_json_minimal(tmp_path):
    desc_file = tmp_path / "brief.json"
    desc_file.write_text(json.dumps({"project_goal": "Build a test project."}), encoding="utf-8")

    result = load(desc_file, scan_project=False)
    assert result["project_goal"] == "Build a test project."


def test_load_json_full(tmp_path):
    brief = {
        "project_name": "TestProject",
        "project_goal": "Generate reports from CSV data.",
        "deliverables": ["Python modules", "CSV reports"],
        "output_format": "CSV",
        "primary_output_dir": "src/",
        "components": [{"slug": "ingest", "name": "Ingest Module"}],
    }
    desc_file = tmp_path / "brief.json"
    desc_file.write_text(json.dumps(brief), encoding="utf-8")

    result = load(desc_file, scan_project=False)
    assert result["project_name"] == "TestProject"
    assert len(result["deliverables"]) == 2
    assert result["components"][0]["slug"] == "ingest"


def test_load_json_invalid_json(tmp_path):
    desc_file = tmp_path / "brief.json"
    desc_file.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        load(desc_file, scan_project=False)


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nonexistent.json", scan_project=False)


def test_load_unsupported_extension(tmp_path):
    f = tmp_path / "brief.toml"
    f.write_text("project_goal = 'test'", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported file type"):
        load(f, scan_project=False)


# ---------------------------------------------------------------------------
# Markdown loading
# ---------------------------------------------------------------------------

def test_load_markdown_structured(tmp_path):
    md = textwrap.dedent("""\
        ## Project Name
        MyMdProject

        ## Project Goal
        Build a data pipeline for daily sales data.

        ## Deliverables
        - Python modules
        - SQL scripts

        ## Output Format
        Python 3.11

        ## Components
        - ingest: Ingest Module
        - transform: Transform Module
    """)
    desc_file = tmp_path / "brief.md"
    desc_file.write_text(md, encoding="utf-8")

    result = load(desc_file, scan_project=False)
    assert result["project_name"] == "MyMdProject"
    assert "data pipeline" in result["project_goal"].lower()
    assert len(result["deliverables"]) == 2
    assert result["output_format"] == "Python 3.11"
    assert len(result["components"]) == 2
    assert result["components"][0]["slug"] == "ingest"


def test_load_markdown_unstructured_fallback(tmp_path):
    md = "Build a project that does useful things with data."
    desc_file = tmp_path / "brief.md"
    desc_file.write_text(md, encoding="utf-8")

    result = load(desc_file, scan_project=False)
    assert "project_goal" in result
    assert "data" in result["project_goal"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_missing_project_goal():
    errors = validate({})
    assert any("project_goal" in e for e in errors)


def test_validate_valid():
    errors = validate({"project_goal": "Build something useful."})
    assert errors == []


def test_validate_bad_slug():
    desc = {
        "project_goal": "Test project",
        "components": [{"slug": "Bad Slug!", "name": "Bad Component"}],
    }
    errors = validate(desc)
    assert any("slug" in e for e in errors)


def test_validate_good_slug():
    desc = {
        "project_goal": "Test project",
        "components": [{"slug": "good-slug-01", "name": "Good Component"}],
    }
    errors = validate(desc)
    assert errors == []


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert _slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    assert _slugify("Chapter 1: Introduction") == "chapter-1-introduction"


def test_slugify_already_slug():
    assert _slugify("already-a-slug") == "already-a-slug"
