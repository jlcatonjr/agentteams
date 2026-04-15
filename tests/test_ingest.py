"""
Tests for src/ingest.py
"""

import json
import textwrap
import pytest
from pathlib import Path
from src.ingest import (
    load,
    validate,
    _slugify,
    parse_dependency_manifests,
    _parse_requirements_txt,
    _parse_package_json,
    _parse_cargo_toml,
    _parse_go_mod,
    _parse_pyproject_toml,
)


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


# ---------------------------------------------------------------------------
# Dependency manifest parsing (R-3)
# ---------------------------------------------------------------------------

def test_parse_requirements_txt_basic():
    text = "flask==2.3.0\nrequests>=2.28\n# comment\npandas\n"
    deps = _parse_requirements_txt(text)
    names = [d["name"] for d in deps]
    assert "flask" in names
    assert "requests" in names
    assert "pandas" in names
    flask_dep = next(d for d in deps if d["name"] == "flask")
    assert flask_dep["version"] == "2.3.0"
    assert flask_dep["category"] == "library"


def test_parse_requirements_txt_skips_flags():
    text = "-r base.txt\n-e .\nflask\n"
    deps = _parse_requirements_txt(text)
    assert len(deps) == 1
    assert deps[0]["name"] == "flask"


def test_parse_package_json_deps():
    text = json.dumps({
        "dependencies": {"react": "^18.2.0", "axios": "~1.4.0"},
        "devDependencies": {"jest": "^29.0.0"}
    })
    deps = _parse_package_json(text)
    names = [d["name"] for d in deps]
    assert "react" in names
    assert "axios" in names
    assert "jest" in names


def test_parse_package_json_invalid():
    deps = _parse_package_json("not valid json")
    assert deps == []


def test_parse_cargo_toml():
    text = textwrap.dedent("""\
        [package]
        name = "myapp"

        [dependencies]
        serde = "1.0"
        tokio = { version = "1", features = ["full"] }

        [dev-dependencies]
        criterion = "0.5"
    """)
    deps = _parse_cargo_toml(text)
    names = [d["name"] for d in deps]
    assert "serde" in names
    assert "tokio" in names
    # dev-dependencies is a different section, parser only reads [dependencies]
    assert "criterion" not in names


def test_parse_go_mod():
    text = textwrap.dedent("""\
        module github.com/example/myapp

        go 1.21

        require (
            github.com/gin-gonic/gin v1.9.1
            github.com/lib/pq v1.10.9
        )
    """)
    deps = _parse_go_mod(text)
    names = [d["name"] for d in deps]
    assert "gin" in names
    assert "pq" in names


def test_parse_pyproject_toml_dependencies():
    text = textwrap.dedent("""\
        [project]
        name = "myapp"

        [project.dependencies]
        "flask>=2.3"
        "requests"

        [tool.pytest.ini_options]
        testpaths = ["tests"]
    """)
    # Note: this tests the basic list-item parsing
    deps = _parse_pyproject_toml(text)
    names = [d["name"] for d in deps]
    assert "flask" in names


def test_parse_dependency_manifests_integration(tmp_path):
    """Integration test: parse_dependency_manifests discovers tools from files on disk."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("flask==2.3.0\npandas>=1.5\n", encoding="utf-8")

    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8")

    tools = parse_dependency_manifests(tmp_path)
    names = [t["name"] for t in tools]
    assert "flask" in names
    assert "pandas" in names
    assert "react" in names


def test_parse_dependency_manifests_dedup(tmp_path):
    """Same package name from multiple manifests should not duplicate."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("flask\n", encoding="utf-8")

    # pyproject.toml also mentions flask
    pyp_file = tmp_path / "pyproject.toml"
    pyp_file.write_text('[project.dependencies]\n"flask>=2.0"\n', encoding="utf-8")

    tools = parse_dependency_manifests(tmp_path)
    flask_count = sum(1 for t in tools if t["name"].lower() == "flask")
    assert flask_count == 1


# ---------------------------------------------------------------------------
# Expanded tool signatures (R-2)
# ---------------------------------------------------------------------------

def test_detect_tools_typescript(tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "TypeScript" in tool_names


def test_detect_tools_terraform(tmp_path):
    (tmp_path / "main.tf").write_text("", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Terraform" in tool_names


def test_detect_tools_docker_compose_yaml(tmp_path):
    (tmp_path / "docker-compose.yaml").write_text("", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Docker Compose" in tool_names


def _write_brief(tmp_path, desc):
    """Helper to write a brief.json and return the path."""
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(desc), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Dependency supplement via directory scan (R-3)
# ---------------------------------------------------------------------------

def test_supplement_from_directory_adds_deps(tmp_path):
    """Directory scan should add dependency manifest contents to tools."""
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("flask==2.3.0\npandas\n", encoding="utf-8")

    desc = {
        "project_goal": "Test project with existing code.",
        "existing_project_path": str(tmp_path),
        "tools": [{"name": "Python", "category": "language"}],
    }
    brief_path = _write_brief(tmp_path, desc)
    result = load(brief_path, scan_project=True)
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Python" in tool_names
    assert "flask" in tool_names
    assert "pandas" in tool_names
