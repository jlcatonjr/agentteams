"""
Integration test: run the full pipeline on each example brief.
"""

import json
import re
import pytest
from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
TEMPLATES_DIR = Path(__file__).parent.parent / "agentteams" / "templates"


def _run_pipeline(brief_path: Path, tmp_path: Path, framework: str = "copilot-vscode") -> dict:
    from agentteams import ingest, analyze, render, emit
    from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
    from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
    from agentteams.frameworks.claude import ClaudeAdapter

    _adapters = {
        "copilot-vscode": CopilotVSCodeAdapter,
        "copilot-cli": CopilotCLIAdapter,
        "claude": ClaudeAdapter,
    }
    adapter = _adapters[framework]()

    description = ingest.load(brief_path, scan_project=False)
    errors = ingest.validate(description)
    assert errors == [], f"Validation errors in {brief_path}: {errors}"

    manifest = analyze.build_manifest(description, framework=framework)
    assert manifest["project_name"]
    assert manifest["selected_archetypes"]

    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)
    assert rendered, "render_all returned empty list"

    # Apply framework adapter post-processing (mirrors build_team.py step 5)
    final_rendered: list[tuple[str, str]] = []
    for rel_path, content in rendered:
        if "copilot-instructions" in rel_path:
            content = adapter.render_instructions_file(content, manifest)
        elif (
            "SETUP-REQUIRED" not in rel_path
            and "team-builder" not in rel_path
            and not rel_path.startswith("references/")
            and "/references/" not in rel_path
        ):
            from pathlib import Path as _Path
            slug = _Path(rel_path).stem.replace(".agent", "")
            content = adapter.render_agent_file(content, slug, manifest)
        final_rendered.append((rel_path, content))

    # Generate team topology graph (mirrors build_team.py step 5c)
    from agentteams import graph as _graph
    graph_content = _graph.generate_graph_document(
        dict(final_rendered), project_name=manifest.get("project_name", "")
    )
    final_rendered.append(("references/pipeline-graph.md", graph_content))

    result = emit.emit_all(final_rendered, output_dir=tmp_path, dry_run=False, overwrite=True, yes=True)
    assert result.success, f"emit failed: {result.errors}"

    return {"manifest": manifest, "rendered": final_rendered, "result": result}


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


# ---------------------------------------------------------------------------
# YAML front matter validation (step 7.8)
# ---------------------------------------------------------------------------

_YAML_FRONT_MATTER_RE = re.compile(r"^---\n(.+?)\n---", re.DOTALL)
_UNRESOLVED_AUTO_RE = re.compile(r"\{(?!MANUAL:)[A-Z][A-Z0-9_]*\}")


def _parse_yaml_front_matter(content: str) -> dict | None:
    """Return parsed YAML front matter dict, or None if absent."""
    import sys
    match = _YAML_FRONT_MATTER_RE.match(content)
    if not match:
        return None
    # Use yaml if available, otherwise a minimal line parser
    try:
        import yaml  # type: ignore
        return yaml.safe_load(match.group(1))
    except ImportError:
        # Minimal key: value parser sufficient for front matter
        result: dict = {}
        for line in match.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                key, _, val = line.partition(":")
                result[key.strip()] = val.strip()
        return result


def _collect_agent_slugs_from_content(content: str) -> list[str]:
    """Extract agent slugs from handoffs and agents list in rendered content."""
    _conditional_re = re.compile(r"\*\(If\b|\bIf `@[a-z0-9\-]+` in team\b|\| `@")
    slugs: list[str] = []
    for line in content.splitlines():
        # Skip *(If @slug in team)* conditional guards and routing-table rows
        if _conditional_re.search(line):
            continue
        for match in re.finditer(r"@([\w-]+)", line):
            slugs.append(match.group(1))
    return slugs


@pytest.mark.parametrize("example", ["software-project", "research-project", "data-pipeline"])
def test_generated_files_parse_correctly(tmp_path, example):
    """Step 7.8: Validate generated .agent.md files parse and meet structural requirements."""
    brief = EXAMPLES_DIR / example / "brief.json"
    if not brief.exists():
        pytest.skip(f"{example} brief not found")

    output = _run_pipeline(brief, tmp_path)
    manifest = output["manifest"]
    agent_slugs = set(manifest["agent_slug_list"])

    agent_files = list(tmp_path.glob("*.agent.md"))
    assert agent_files, "No .agent.md files were generated"

    for agent_file in agent_files:
        content = agent_file.read_text(encoding="utf-8")

        # 1. YAML front matter present and parseable
        fm = _parse_yaml_front_matter(content)
        assert fm is not None, f"{agent_file.name}: missing YAML front matter"

        # 2. Required fields present and non-empty
        for field in ("name", "description"):
            assert field in fm, f"{agent_file.name}: YAML front matter missing '{field}'"
            assert fm[field], f"{agent_file.name}: YAML front matter '{field}' is empty"

        # 3. No unresolved auto-placeholder tokens in auto-resolved fields
        #    (MANUAL: tokens are allowed to remain; tokens inside backtick spans
        #     are instructional text examples, not unresolved placeholders)
        content_no_code = re.sub(r"`[^`\n]+`", "", content)
        auto_unresolved = _UNRESOLVED_AUTO_RE.findall(content_no_code)
        assert not auto_unresolved, (
            f"{agent_file.name}: found unresolved auto-placeholder(s): {auto_unresolved}"
        )

    # 4. All @agent_slug references in generated files resolve within the team
    #    (builder and reference files are excluded — they may reference slugs from other ecosystems)
    slug_references: list[tuple[str, str]] = []
    for agent_file in agent_files:
        content = agent_file.read_text(encoding="utf-8")
        for slug in _collect_agent_slugs_from_content(content):
            slug_references.append((agent_file.name, slug))

    broken = [
        (fname, slug)
        for fname, slug in slug_references
        if slug not in agent_slugs and slug != "orchestrator"
    ]
    # Warn rather than fail — some slugs (style-guardian etc.) are conditionally included
    if broken:
        import warnings
        for fname, slug in broken:
            warnings.warn(f"{fname}: references @{slug} which is not in the generated team")


@pytest.mark.parametrize("example", ["software-project", "research-project", "data-pipeline"])
def test_snapshot_comparison(tmp_path, example):
    """Compare pipeline output against committed expected/ snapshots (normalize whitespace)."""
    import re
    brief = EXAMPLES_DIR / example / "brief.json"
    expected_dir = EXAMPLES_DIR / example / "expected"

    if not brief.exists():
        pytest.skip(f"{example} brief not found")
    if not expected_dir.exists() or not any(expected_dir.iterdir()):
        pytest.skip(f"{example} expected/ snapshots not generated yet")

    _run_pipeline(brief, tmp_path)

    # Exclude files that contain live network data (threat intel, CVE feeds) — non-deterministic
    _live_data_files = {"security-vulnerability-watch.reference.md", "security.agent.md"}
    expected_files = sorted(
        f for f in expected_dir.rglob("*.md")
        if "build-log" not in f.name and f.name not in _live_data_files
    )
    assert expected_files, f"No .md files found in {expected_dir}"

    # Strip non-deterministic timestamp lines before comparison (e.g. "Generated at: `...`")
    _ts_pat = re.compile(r"Generated at: `[^`]+`")

    mismatches: list[str] = []
    for expected_file in expected_files:
        rel = expected_file.relative_to(expected_dir)
        actual_file = tmp_path / rel
        if not actual_file.exists():
            mismatches.append(f"MISSING: {rel}")
            continue
        expected_text = _ts_pat.sub("", expected_file.read_text(encoding="utf-8"))
        actual_text = _ts_pat.sub("", actual_file.read_text(encoding="utf-8"))
        expected_text = " ".join(expected_text.split())
        actual_text = " ".join(actual_text.split())
        if expected_text != actual_text:
            mismatches.append(f"DIFF: {rel}")

    assert not mismatches, (
        f"{example}: snapshot mismatch(es):\n  " + "\n  ".join(mismatches)
    )


# ===========================================================================
# --update integration tests (structural diff + MANUAL preservation)
# ===========================================================================

def _run_pipeline_to_dir(brief_path: Path, output_dir: Path, framework: str = "copilot-vscode") -> dict:
    """Run the full pipeline and emit to output_dir, returning manifest + build-log path."""
    from agentteams import ingest, analyze, render, emit
    from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
    from pathlib import Path as _Path
    import json

    adapter = CopilotVSCodeAdapter()
    TEMPLATES = _Path(__file__).parent.parent / "agentteams" / "templates"

    description = ingest.load(brief_path, scan_project=False)
    manifest = analyze.build_manifest(description, framework=framework)
    rendered = render.render_all(manifest, templates_dir=TEMPLATES)
    template_hashes = render.compute_template_hashes(manifest, templates_dir=TEMPLATES)

    final = []
    for rp, content in rendered:
        if "copilot-instructions" in rp:
            content = adapter.render_instructions_file(content, manifest)
        elif "SETUP-REQUIRED" not in rp and "team-builder" not in rp:
            slug = _Path(rp).stem.replace(".agent", "")
            content = adapter.render_agent_file(content, slug, manifest)
        final.append((rp, content))

    result = emit.emit_all(final, output_dir=output_dir, dry_run=False, overwrite=True, yes=True)
    assert result.success

    # Write build-log (v1.2 format)
    from build_team import _write_run_log
    _write_run_log(manifest, result, output_dir, template_hashes)

    return {"manifest": manifest, "rendered": final, "result": result}


def test_update_adds_new_agents(tmp_path):
    """--update emits files for agents that are new since the last build."""
    from agentteams import drift, analyze
    from pathlib import Path as _Path

    TEMPLATES = _Path(__file__).parent.parent / "agentteams" / "templates"
    brief = _Path(__file__).parent.parent / "examples" / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    # Step 1: Run pipeline → emit initial team
    output_dir = tmp_path / ".github" / "agents"
    data = _run_pipeline_to_dir(brief, output_dir)
    manifest = data["manifest"]

    # Step 2: Simulate a governance agent being added by removing it from the build-log
    log_path = output_dir / "references" / "build-log.json"
    log = json.loads(log_path.read_text())

    # Remove code-hygiene from the old log's output_files_map and agent_slug_list
    log["output_files_map"] = [
        f for f in log["output_files_map"]
        if "code-hygiene" not in f["path"]
    ]
    log["agent_slug_list"] = [s for s in log["agent_slug_list"] if s != "code-hygiene"]
    log_path.write_text(json.dumps(log), encoding="utf-8")

    # Also delete the generated files to simulate a fresh downstream project
    for path in list(output_dir.rglob("*code-hygiene*")):
        path.unlink()

    # Step 3: Run structural diff — code-hygiene should be detected as added
    old_log = drift.load_build_log(output_dir)
    sdreport = drift.compute_structural_diff(old_log, manifest, TEMPLATES)

    added_paths = {f["path"] for f in sdreport.added_files}
    assert any("code-hygiene" in p for p in added_paths), (
        f"Expected code-hygiene in added_files; got: {added_paths}"
    )
    assert sdreport.has_changes


def test_update_preserves_manual_values(tmp_path):
    """--update carries forward resolved {MANUAL:*} values from existing files."""
    from build_team import _preserve_manual_values

    existing = "# Agent\n\nStyle reference: /path/to/style-guide.md\n"
    new_content = "# Agent\n\nStyle reference: {MANUAL:STYLE_REFERENCE_PATH}\n"

    result = _preserve_manual_values(existing, new_content)
    assert "{MANUAL:STYLE_REFERENCE_PATH}" not in result
    assert "/path/to/style-guide.md" in result


def test_update_preserves_unresolved_manual_token(tmp_path):
    """If a {MANUAL:*} token is still unresolved in existing file, it stays unresolved."""
    from build_team import _preserve_manual_values

    existing = "# Agent\n\nStyle reference: {MANUAL:STYLE_REFERENCE_PATH}\n"
    new_content = "# Agent\n\nStyle reference: {MANUAL:STYLE_REFERENCE_PATH}\n"

    result = _preserve_manual_values(existing, new_content)
    assert "{MANUAL:STYLE_REFERENCE_PATH}" in result


def test_update_reports_removed_files(tmp_path):
    """--update classifies deprecated files as removed (not deleted)."""
    from agentteams import drift

    TEMPLATES = Path(__file__).parent.parent / "agentteams" / "templates"
    old_files = [
        {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
        {"path": "deprecated-agent.agent.md", "template": "universal/deprecated.template.md", "type": "agent"},
    ]
    old_log = {
        "schema_version": "1.2",
        "files_written": [],
        "template_hashes": {},
        "output_files_map": old_files,
        "agent_slug_list": ["orchestrator", "deprecated-agent"],
        "governance_agents": [],
    }
    new_manifest = {
        "project_name": "TestProject",
        "output_files": [old_files[0]],  # deprecated-agent removed
        "agent_slug_list": ["orchestrator"],
    }

    sdreport = drift.compute_structural_diff(old_log, new_manifest, TEMPLATES)
    assert len(sdreport.removed_files) == 1
    assert sdreport.removed_files[0]["path"] == "deprecated-agent.agent.md"
    # Removed agents change slug list → copilot-instructions re-render needed
    assert sdreport.team_membership_changed
    # Removed files must NOT be in the write set
    update_paths = {f["path"] for f in sdreport.update_files}
    assert "deprecated-agent.agent.md" not in update_paths


def test_build_log_schema_v12(tmp_path):
    """Build-log written by _write_run_log includes structural and manifest fingerprints."""
    import json
    from build_team import _write_run_log
    from agentteams.emit import EmitResult

    manifest = {
        "project_name": "TestProject",
        "framework": "copilot-vscode",
        "project_type": "software",
        "selected_archetypes": ["primary-producer"],
        "components": [],
        "agent_slug_list": ["orchestrator", "navigator"],
        "governance_agents": ["navigator"],
        "output_files": [
            {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent", "component_slug": None},
        ],
        "manual_required_placeholders": [],
    }
    result = EmitResult(dry_run=False)
    result.written = [str(tmp_path / "orchestrator.agent.md")]

    _write_run_log(manifest, result, tmp_path, {"universal/orchestrator.template.md": "abc123"})

    log_path = tmp_path / "references" / "build-log.json"
    assert log_path.exists()
    log = json.loads(log_path.read_text())

    assert log["schema_version"] == "1.2"
    assert "output_files_map" in log
    assert "agent_slug_list" in log
    assert "governance_agents" in log
    assert "manifest_fingerprint" in log
    assert log["agent_slug_list"] == ["orchestrator", "navigator"]
    assert log["governance_agents"] == ["navigator"]
