"""
Tests for src/ingest.py
"""

import json
import textwrap
import pytest
from pathlib import Path
from agentteams.ingest import (
    load,
    validate,
    _slugify,
    _load_markdown,
    _detect_primary_output_dir,
    parse_dependency_manifests,
    _parse_requirements_txt,
    _parse_package_json,
    _parse_cargo_toml,
    _parse_go_mod,
    _parse_pyproject_toml,
)


def _md(tmp_path, text):
    p = tmp_path / "desc.md"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


# --- project_goal derivation fallback (feat/ingest-goal-fallback) -----------

def test_explicit_goal_heading_wins_over_overview(tmp_path):
    desc = _load_markdown(_md(tmp_path, """\
        # Title
        ## Project Goal
        The real goal.
        ## Project Overview
        Overview prose that should be ignored.
        """))
    assert desc["project_goal"] == "The real goal."


def test_overview_heading_used_as_goal_fallback(tmp_path):
    desc = _load_markdown(_md(tmp_path, """\
        # Title
        ## Project Overview
        A website for economics research.
        ## Architecture
        details
        """))
    assert desc["project_goal"] == "A website for economics research."


def test_lead_paragraph_fallback_stops_at_next_heading(tmp_path):
    desc = _load_markdown(_md(tmp_path, """\
        # Commodity Money Working Paper
        Dual-system project: an app and a manuscript.
        ## Layout
        | a | b |
        """))
    assert desc["project_goal"] == "Dual-system project: an app and a manuscript."


def test_no_prose_leaves_goal_unset_and_fails_validation(tmp_path):
    # Headings + only a table, no prose paragraph -> no goal derivable.
    p = _md(tmp_path, """\
        # Title
        ## Layout
        | a | b |
        | - | - |
        """)
    with pytest.raises(ValueError):
        load(p, scan_project=False)


def test_fallback_does_not_override_explicit_goal_heading(tmp_path):
    desc = _load_markdown(_md(tmp_path, """\
        ## Overview
        overview text
        ## Goal
        explicit goal text
        """))
    assert desc["project_goal"] == "explicit goal text"


def test_setext_heading_not_treated_as_prose(tmp_path):
    # "My Project Title" underlined with === is a heading, not the goal.
    desc = _load_markdown(_md(tmp_path, """\
        My Project Title
        ================

        The actual project description paragraph.

        ## Details
        x
        """))
    assert desc["project_goal"] == "The actual project description paragraph."


def test_overview_priority_overview_beats_about_regardless_of_order(tmp_path):
    desc = _load_markdown(_md(tmp_path, """\
        # Title
        ## About the Authors
        Jane and John wrote this.
        ## Project Overview
        The real project overview.
        """))
    assert desc["project_goal"] == "The real project overview."


def test_list_first_content_not_used_as_goal(tmp_path):
    # A leading bullet list is not prose; with no prose/overview, goal stays unset.
    p = _md(tmp_path, """\
        # Title
        - feature one
        - feature two
        """)
    with pytest.raises(ValueError):
        load(p, scan_project=False)


def test_goal_fallback_capped_and_collapsed(tmp_path):
    long = "word " * 400  # ~2000 chars, multi-space
    desc = _load_markdown(_md(tmp_path, f"# T\n## Project Overview\n{long}\n"))
    assert len(desc["project_goal"]) <= 500
    assert "  " not in desc["project_goal"]  # whitespace collapsed


def test_too_short_fallback_is_ignored(tmp_path):
    p = _md(tmp_path, "# T\n## Project Overview\nHi\n")  # <10 chars
    with pytest.raises(ValueError):
        load(p, scan_project=False)


def test_nested_fence_code_not_leaked_into_goal(tmp_path):
    desc = _load_markdown(_md(tmp_path, """\
        # T
        ```
        ~~~
        secret_code_here()
        ```
        Real project goal prose.
        """))
    assert "secret_code_here" not in desc["project_goal"]
    assert desc["project_goal"] == "Real project goal prose."


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


def test_parse_dependency_manifests_subpackage_requirements_txt(tmp_path):
    """Monorepo sub-package requirements.txt at depth 2 must be parsed."""
    pkg_dir = tmp_path / "packages" / "foo"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "requirements.txt").write_text("django==4.2.0\n", encoding="utf-8")

    tools = parse_dependency_manifests(tmp_path)
    names = [t["name"] for t in tools]
    assert "django" in names


def test_parse_dependency_manifests_subpackage_package_json(tmp_path):
    """Monorepo sub-package package.json at depth 2 must be parsed."""
    ui_dir = tmp_path / "packages" / "ui"
    ui_dir.mkdir(parents=True)
    (ui_dir / "package.json").write_text(
        json.dumps({"dependencies": {"vue": "^3.0.0"}}), encoding="utf-8"
    )

    tools = parse_dependency_manifests(tmp_path)
    names = [t["name"] for t in tools]
    assert "vue" in names


def test_parse_dependency_manifests_depth_limit_not_exceeded(tmp_path):
    """Manifests at depth 3 must not be found with the default max_depth=2."""
    deep_dir = tmp_path / "a" / "b" / "c"   # depth 3
    deep_dir.mkdir(parents=True)
    (deep_dir / "requirements.txt").write_text("hidden-dep==1.0.0\n", encoding="utf-8")

    tools = parse_dependency_manifests(tmp_path)  # default max_depth=2
    names = [t["name"] for t in tools]
    assert "hidden-dep" not in names


def test_parse_dependency_manifests_dedup_root_and_subpackage(tmp_path):
    """flask listed in both root and a sub-package manifest must appear once."""
    (tmp_path / "requirements.txt").write_text("flask==2.3.0\n", encoding="utf-8")
    sub_dir = tmp_path / "packages" / "api"
    sub_dir.mkdir(parents=True)
    (sub_dir / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")

    tools = parse_dependency_manifests(tmp_path)
    flask_entries = [t for t in tools if t["name"].lower() == "flask"]
    assert len(flask_entries) == 1


def test_parse_dependency_manifests_depth2_boundary_inclusive(tmp_path):
    """Manifests exactly at max_depth=2 must be found (boundary is inclusive)."""
    pkg_dir = tmp_path / "packages" / "bar"   # depth 2
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "requirements.txt").write_text("celery==5.3.0\n", encoding="utf-8")

    tools = parse_dependency_manifests(tmp_path, max_depth=2)
    names = [t["name"] for t in tools]
    assert "celery" in names


def test_parse_dependency_manifests_max_depth_zero_root_only(tmp_path):
    """max_depth=0 must find only the root manifest, not any subdirectory."""
    (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
    sub_dir = tmp_path / "packages" / "foo"
    sub_dir.mkdir(parents=True)
    (sub_dir / "requirements.txt").write_text("django\n", encoding="utf-8")

    tools = parse_dependency_manifests(tmp_path, max_depth=0)
    names = [t["name"] for t in tools]
    assert "flask" in names
    assert "django" not in names


def test_parse_dependency_manifests_node_modules_excluded(tmp_path):
    """package.json files inside node_modules must not be surfaced."""
    nm_dir = tmp_path / "node_modules" / "react"
    nm_dir.mkdir(parents=True)
    (nm_dir / "package.json").write_text(
        json.dumps({"dependencies": {"react": "^18.0.0"}}), encoding="utf-8"
    )

    tools = parse_dependency_manifests(tmp_path)
    names = [t["name"] for t in tools]
    assert "react" not in names


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


def test_detect_tools_uv(tmp_path):
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "uv" in tool_names
    assert any(t["name"] == "uv" and t.get("category") == "build-system" for t in result.get("tools", []))


def test_detect_tools_pyenv_python_version(tmp_path):
    (tmp_path / ".python-version").write_text("3.12.0\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "pyenv" in tool_names
    assert any(t["name"] == "pyenv" and t.get("category") == "language" for t in result.get("tools", []))


def test_detect_tools_yarn(tmp_path):
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Yarn" in tool_names
    assert any(t["name"] == "Yarn" and t.get("category") == "build-system" for t in result.get("tools", []))


def test_detect_tools_pnpm(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "pnpm" in tool_names
    assert any(t["name"] == "pnpm" and t.get("category") == "build-system" for t in result.get("tools", []))


def test_detect_tools_bun(tmp_path):
    (tmp_path / "bun.lockb").write_bytes(b"")  # binary file; empty bytes is fine
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Bun" in tool_names
    assert any(t["name"] == "Bun" and t.get("category") == "build-system" for t in result.get("tools", []))


def test_detect_tools_bun_lock_text(tmp_path):
    (tmp_path / "bun.lock").write_text("", encoding="utf-8")  # text-format lock (Bun >= 1.1)
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Bun" in tool_names
    assert any(t["name"] == "Bun" and t.get("category") == "build-system" for t in result.get("tools", []))


def test_detect_tools_swift(tmp_path):
    (tmp_path / "Package.swift").write_text("// swift-tools-version:5.9\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Swift" in tool_names
    assert any(t["name"] == "Swift" and t.get("category") == "language" for t in result.get("tools", []))


def test_detect_tools_kotlin_gradle(tmp_path):
    (tmp_path / "build.gradle.kts").write_text("plugins { kotlin(\"jvm\") version \"1.9.0\" }\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "Kotlin (Gradle)" in tool_names
    assert any(t["name"] == "Kotlin (Gradle)" and t.get("category") == "build-system" for t in result.get("tools", []))


def test_detect_tools_mise(tmp_path):
    (tmp_path / ".mise.toml").write_text("[tools]\npython = \"3.12\"\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "mise" in tool_names
    assert any(t["name"] == "mise" and t.get("category") == "cli" for t in result.get("tools", []))


def test_detect_tools_mise_toml_nodot(tmp_path):
    (tmp_path / "mise.toml").write_text("[tools]\npython = \"3.12\"\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "mise" in tool_names
    assert any(t["name"] == "mise" and t.get("category") == "cli" for t in result.get("tools", []))


def test_detect_tools_asdf_tool_versions(tmp_path):
    (tmp_path / ".tool-versions").write_text("python 3.12.0\nnode 20.0.0\n", encoding="utf-8")
    result = load(
        _write_brief(tmp_path, {"project_goal": "Test project.", "existing_project_path": str(tmp_path)}),
        scan_project=True,
    )
    tool_names = [t["name"] for t in result.get("tools", [])]
    assert "asdf" in tool_names
    assert any(t["name"] == "asdf" and t.get("category") == "cli" for t in result.get("tools", []))


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


def test_scan_infers_retrieval_integration_relational_metadata(tmp_path):
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / "run_services.py").write_text(
        """
import os
def run():
    mode = os.environ.get('BBB_IDS')
    parser = '--service refresh-mvs'
    sql = 'UPDATE agency_datasets SET retrieval_type = ...'
""",
        encoding="utf-8",
    )
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "refresh.sh").write_text("python services/run_services.py --service refresh-mvs\n", encoding="utf-8")

    brief = {
        "project_goal": "Collector maintenance pipeline.",
        "existing_project_path": str(tmp_path),
    }
    result = load(_write_brief(tmp_path, brief), scan_project=True)
    retrieval = result.get("retrieval_integration", {})

    assert retrieval.get("mode") == "relational-metadata"
    assert "services/run_services.py" in retrieval.get("query_entrypoints", [])
    assert "services/run_services.py" in retrieval.get("maintenance_entrypoints", [])
    assert "cli" in retrieval.get("trigger_sources", [])
    assert "env" in retrieval.get("trigger_sources", [])


def test_scan_skips_retrieval_integration_when_none_detected(tmp_path):
    (tmp_path / "README.md").write_text("Minimal project", encoding="utf-8")
    brief = {
        "project_goal": "Simple project.",
        "existing_project_path": str(tmp_path),
    }
    result = load(_write_brief(tmp_path, brief), scan_project=True)
    assert "retrieval_integration" not in result


# ---------------------------------------------------------------------------
# _detect_primary_output_dir (MAP-07)
# ---------------------------------------------------------------------------

def test_detect_primary_output_dir_excludes_src(tmp_path):
    (tmp_path / "src").mkdir()
    assert _detect_primary_output_dir(tmp_path) is None


def test_detect_primary_output_dir_excludes_lib(tmp_path):
    (tmp_path / "lib").mkdir()
    assert _detect_primary_output_dir(tmp_path) is None


def test_detect_primary_output_dir_excludes_docs(tmp_path):
    (tmp_path / "docs").mkdir()
    assert _detect_primary_output_dir(tmp_path) is None


def test_detect_primary_output_dir_picks_dist(tmp_path):
    (tmp_path / "dist").mkdir()
    assert _detect_primary_output_dir(tmp_path) == "dist/"


def test_detect_primary_output_dir_picks_build(tmp_path):
    (tmp_path / "build").mkdir()
    assert _detect_primary_output_dir(tmp_path) == "build/"


def test_detect_primary_output_dir_picks__site(tmp_path):
    (tmp_path / "_site").mkdir()
    assert _detect_primary_output_dir(tmp_path) == "_site/"


def test_detect_primary_output_dir_picks_site(tmp_path):
    (tmp_path / "site").mkdir()
    assert _detect_primary_output_dir(tmp_path) == "site/"


def test_detect_primary_output_dir_picks_public(tmp_path):
    (tmp_path / "public").mkdir()
    assert _detect_primary_output_dir(tmp_path) == "public/"


def test_detect_primary_output_dir_dist_beats_public(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "public").mkdir()
    assert _detect_primary_output_dir(tmp_path) == "dist/"


def test_detect_primary_output_dir_returns_none_for_empty(tmp_path):
    assert _detect_primary_output_dir(tmp_path) is None


def test_detect_primary_output_dir_src_and_dist_prefers_dist(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "dist").mkdir()
    assert _detect_primary_output_dir(tmp_path) == "dist/"


def test_scan_does_not_set_primary_output_dir_to_src(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "requirements.txt").write_text("flask==3.0.0\n", encoding="utf-8")
    brief = {
        "project_goal": "A Python web application.",
        "existing_project_path": str(tmp_path),
    }
    brief_path = tmp_path / "_brief.json"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    result = load(brief_path, scan_project=True)
    assert result.get("primary_output_dir") != "src/"
    assert result.get("primary_output_dir") is None
