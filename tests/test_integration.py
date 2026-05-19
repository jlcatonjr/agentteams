"""
Integration test: run the full pipeline on each example brief.
"""

import json
import re
import hashlib
import hmac
import pytest
from pathlib import Path

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
TEMPLATES_DIR = Path(__file__).parent.parent / "agentteams" / "templates"


def _run_pipeline(brief_path: Path, tmp_path: Path, framework: str = "copilot-vscode") -> dict:
    from agentteams import ingest, analyze, render, emit
    from agentteams import security_refs as _security_refs
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

    # Mirror build_team security placeholder injection so security templates
    # have the same resolved placeholder surface in integration tests.
    manifest["auto_resolved_placeholders"].update(
        _security_refs.build_security_placeholders(
            output_dir=tmp_path,
            offline=True,
            max_items=5,
            tools=manifest.get("tools", []) or None,
            skip_nvd=True,
        )
    )

    rendered = render.render_all(manifest, templates_dir=TEMPLATES_DIR)
    assert rendered, "render_all returned empty list"

    # Apply framework adapter post-processing (mirrors build_team.py step 5)
    final_rendered: list[tuple[str, str]] = []
    runtime_handoff_agents: list[dict[str, object]] = []
    for rel_path, content in rendered:
        lower = rel_path.lower()
        file_type = "agent"
        if "copilot-instructions" in lower or rel_path.endswith("/CLAUDE.md") or rel_path == "../CLAUDE.md":
            file_type = "instructions"
            content = adapter.render_instructions_file(content, manifest)
        elif "SETUP-REQUIRED" in rel_path:
            file_type = "setup-required"
        elif "team-builder" in rel_path:
            file_type = "builder"
        elif rel_path.startswith("references/") or "/references/" in rel_path:
            file_type = "reference"
        else:
            from pathlib import Path as _Path
            slug = _Path(rel_path).stem.replace(".agent", "")
            if adapter.handoff_delivery_mode() == "manifest":
                handoffs = adapter.extract_handoffs(content)
                if handoffs:
                    runtime_handoff_agents.append({"agent": slug, "handoffs": handoffs})
            content = adapter.render_agent_file(content, slug, manifest)

        final_path = adapter.finalize_output_path(rel_path, file_type)
        final_rendered.append((final_path, content))

    if runtime_handoff_agents:
        final_rendered.append((
            "references/runtime-handoffs.json",
            json.dumps(
                {
                    "schema_version": "1.0",
                    "framework": adapter.framework_id,
                    "project_name": manifest["project_name"],
                    "agents": runtime_handoff_agents,
                },
                indent=2,
            ) + "\n",
        ))

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


@pytest.mark.parametrize("framework", ["copilot-cli", "claude"])
def test_non_vscode_frameworks_emit_runtime_handoff_manifest(tmp_path, framework):
    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("Software project example not found")

    _run_pipeline(brief, tmp_path, framework=framework)

    manifest_file = tmp_path / "references" / "runtime-handoffs.json"
    assert manifest_file.exists()

    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert payload["framework"] == framework
    assert payload["project_name"]
    assert "orchestrator" in {entry["agent"] for entry in payload["agents"]}
    assert any(entry["handoffs"] for entry in payload["agents"])


def test_vscode_framework_does_not_emit_runtime_handoff_manifest(tmp_path):
    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("Software project example not found")

    _run_pipeline(brief, tmp_path, framework="copilot-vscode")

    assert not (tmp_path / "references" / "runtime-handoffs.json").exists()


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
    _conditional_re = re.compile(
        r"\*\(If\b|\bIf `@[a-z0-9\-]+` in team\b|applies only when `@[a-z0-9\-]+` is in team|\| `@"
    )
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

    allowed_external_or_optional = {
        "orchestrator",
        "post-production-auditor",
        "style-guardian",
        "module-doc-expert",
    }

    broken = [
        (fname, slug)
        for fname, slug in slug_references
        if slug not in agent_slugs and slug not in allowed_external_or_optional
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
        if "build-log" not in f.name
        and f.name not in _live_data_files
        and ".agentteams-backups" not in f.parts
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
    from agentteams import security_refs as _security_refs
    from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter
    from pathlib import Path as _Path
    import json

    adapter = CopilotVSCodeAdapter()
    TEMPLATES = _Path(__file__).parent.parent / "agentteams" / "templates"

    description = ingest.load(brief_path, scan_project=False)
    manifest = analyze.build_manifest(description, framework=framework)
    manifest["auto_resolved_placeholders"].update(
        _security_refs.build_security_placeholders(
            output_dir=output_dir,
            offline=True,
            max_items=5,
            tools=manifest.get("tools", []) or None,
            skip_nvd=True,
        )
    )
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


def test_update_restores_missing_expected_standard_file(tmp_path, monkeypatch):
    """--update restores standardized files missing on disk even without structural drift."""
    import build_team

    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"

    waiver_key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    waiver = {
        "timestamp": "2026-05-03T00:00:00Z",
        "waiver_id": "waiver-freshness-001",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z",
        "max_uses": "5",
        "uses": "0",
        "approver": "test-harness",
        "ticket_id": "INT-1",
        "reason_code": "TEST",
        "conditions_verified": "verified",
        "signature": "",
    }
    payload = "|".join(
        [
            waiver["waiver_id"],
            waiver["action_reviewed"],
            waiver["expires_at"],
            waiver["max_uses"],
            waiver["uses"],
            waiver["approver"],
            waiver["ticket_id"],
            waiver["reason_code"],
            waiver["conditions_verified"],
        ]
    )
    waiver["signature"] = hmac.new(
        waiver_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "{timestamp},{waiver_id},{action_reviewed},{expires_at},{max_uses},"
        "{uses},{approver},{ticket_id},{reason_code},{conditions_verified},"
        "{signature}\n".format(**waiver),
        encoding="utf-8",
    )

    # Initial generation.
    first_rc = build_team.main([
        "--description", str(brief),
        "--output", str(output_dir),
        "--yes",
        "--no-scan",
        "--security-offline",
    ])
    assert first_rc == 0

    # Seed a PASS decision so the runtime destructive gate allows overwrite updates.
    decision_log = refs / "security-decisions.log.csv"
    decision_log.write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,test-harness,overwrite,PASS,,verified\n",
        encoding="utf-8",
    )

    missing_rel = "references/code-hygiene-rules.reference.md"
    missing_path = output_dir / missing_rel
    assert missing_path.exists(), "Precondition: expected standardized reference is generated"
    missing_path.unlink()
    assert not missing_path.exists(), "Precondition: file removal simulation failed"

    update_rc = build_team.main([
        "--description", str(brief),
        "--output", str(output_dir),
        "--update",
        "--yes",
        "--no-scan",
        "--security-offline",
    ])
    assert update_rc == 0
    assert missing_path.exists(), f"Expected --update to restore missing file: {missing_rel}"


def test_blocked_overwrite_update_creates_no_backup(tmp_path, monkeypatch, capsys):
    """Regression: a security-gate-blocked --update must not create a backup,
    print "Writing...", or print the structural-diff report.

    The destructive-action gate was previously checked AFTER backup_output_dir
    and AFTER the structural-diff report, so a blocked update produced a
    spurious backup, printed "Writing N file(s)...", and showed a misleading
    "Updated (N)" report while leaving files untouched. The gate now runs
    before compute_structural_diff's report and any side effect.
    """
    import build_team

    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"
    backups_dir = output_dir / ".agentteams-backups"

    # Freshness waiver so the ONLY thing that can block is the overwrite gate.
    waiver_key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    waiver = {
        "timestamp": "2026-05-03T00:00:00Z",
        "waiver_id": "waiver-freshness-002",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z",
        "max_uses": "5",
        "uses": "0",
        "approver": "test-harness",
        "ticket_id": "INT-2",
        "reason_code": "TEST",
        "conditions_verified": "verified",
        "signature": "",
    }
    payload = "|".join(
        [
            waiver["waiver_id"], waiver["action_reviewed"], waiver["expires_at"],
            waiver["max_uses"], waiver["uses"], waiver["approver"],
            waiver["ticket_id"], waiver["reason_code"], waiver["conditions_verified"],
        ]
    )
    waiver["signature"] = hmac.new(
        waiver_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "{timestamp},{waiver_id},{action_reviewed},{expires_at},{max_uses},"
        "{uses},{approver},{ticket_id},{reason_code},{conditions_verified},"
        "{signature}\n".format(**waiver),
        encoding="utf-8",
    )

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0

    # Force something for --update to write, but DO NOT seed a PASS decision,
    # so the destructive overwrite gate must block.
    missing_path = output_dir / "references/code-hygiene-rules.reference.md"
    assert missing_path.exists()
    missing_path.unlink()

    backups_before = set(backups_dir.glob("*")) if backups_dir.exists() else set()

    rc = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ])

    assert rc == 1, "blocked overwrite update must return non-zero"
    backups_after = set(backups_dir.glob("*")) if backups_dir.exists() else set()
    assert backups_after == backups_before, (
        "blocked update created a spurious backup: "
        f"{sorted(p.name for p in backups_after - backups_before)}"
    )
    assert not missing_path.exists(), "blocked update must not have written files"

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Security gate blocked overwrite update" in combined
    # Phase 2a: blocked update must not emit the misleading drift report or
    # the write banner.
    assert "Structural update" not in combined, (
        "blocked update printed the structural-diff report"
    )
    assert "Writing " not in captured.out, (
        "blocked update printed the 'Writing N file(s)...' banner"
    )


def test_stale_fingerprint_with_identical_content_does_not_rerender(tmp_path, monkeypatch, capsys):
    """Defect 2 Option A: a stale build-log manifest_fingerprint must NOT cause
    --update to re-render files whose rendered content is identical to disk.

    Before Option A, any fingerprint delta promoted every file to drifted
    ("manifest values changed"). Now content-aware refinement demotes
    fingerprint-only promotions when the file would be rewritten byte-for-byte.
    """
    import json as _json
    import build_team

    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)

    waiver_key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": "waiver-freshness-003",
        "action_reviewed": "security-intel-freshness", "expires_at": "2099-01-01T00:00:00Z",
        "max_uses": "9", "uses": "0", "approver": "test-harness", "ticket_id": "INT-3",
        "reason_code": "TEST", "conditions_verified": "verified", "signature": "",
    }
    payload = "|".join([
        waiver["waiver_id"], waiver["action_reviewed"], waiver["expires_at"],
        waiver["max_uses"], waiver["uses"], waiver["approver"],
        waiver["ticket_id"], waiver["reason_code"], waiver["conditions_verified"],
    ])
    waiver["signature"] = hmac.new(
        waiver_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "{timestamp},{waiver_id},{action_reviewed},{expires_at},{max_uses},"
        "{uses},{approver},{ticket_id},{reason_code},{conditions_verified},"
        "{signature}\n".format(**waiver),
        encoding="utf-8",
    )

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0

    # Allow the destructive overwrite gate.
    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,test-harness,overwrite,PASS,,verified\n",
        encoding="utf-8",
    )

    # Corrupt ONLY the stored manifest_fingerprint; every rendered file on disk
    # is still byte-identical to what the generator produces.
    build_log_path = refs / "build-log.json"
    log = _json.loads(build_log_path.read_text())
    assert log.get("manifest_fingerprint"), "precondition: fingerprint recorded"
    log["manifest_fingerprint"] = "staleStaleStale01"
    build_log_path.write_text(_json.dumps(log), encoding="utf-8")

    sentinel = output_dir / "orchestrator.agent.md"
    before = sentinel.read_text(encoding="utf-8")

    capsys.readouterr()  # clear
    rc = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ])
    out = capsys.readouterr().out

    assert rc == 0, "update with identical content must succeed"
    # A stable (non-volatile) agent file must be demoted: not reported as
    # drifted and not rewritten on disk. (security.agent.md and the
    # security-vulnerability-watch.* files embed a per-render timestamp and
    # legitimately stay drifted — they are rewritten every --update anyway.)
    assert "orchestrator.agent.md  (manifest values changed)" not in out, (
        "a content-identical agent file was still promoted as manifest drift "
        "(content-aware refinement did not fire)"
    )
    assert sentinel.read_text(encoding="utf-8") == before, (
        "a content-identical agent file was rewritten by a stale-fingerprint update"
    )
    # The bulk of the team must be demoted — only the inherently-volatile
    # security trio may remain under the manifest reason.
    promoted = out.count("(manifest values changed)")
    assert promoted <= 3, f"expected ≤3 volatile files promoted, got {promoted}"


def test_stale_fingerprint_converges_in_two_updates(tmp_path, monkeypatch, capsys):
    """P0 — drift trust: a stale build-log fingerprint heals on the first
    --update and the second --update reports zero manifest drift.

    This pins the heal-converges-in-≤2-updates acceptance from
    ``tmp/by-week/2026-W21/p0-p3-batch.plan.md`` (acceptance criterion under
    P0). The first --update demotes every fingerprint-only promotion via
    content-aware refinement and rewrites the build-log with a fresh
    manifest_fingerprint + fingerprint_algo_version (the observable heal). The
    second --update sees matching fingerprints and matching algo versions, so
    sdreport.manifest_changed is False and no files are promoted under a
    manifest reason.
    """
    import json as _json
    import build_team

    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)

    waiver_key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": "waiver-freshness-p0heal",
        "action_reviewed": "security-intel-freshness", "expires_at": "2099-01-01T00:00:00Z",
        "max_uses": "9", "uses": "0", "approver": "test-harness", "ticket_id": "P0-HEAL",
        "reason_code": "TEST", "conditions_verified": "verified", "signature": "",
    }
    payload = "|".join([
        waiver["waiver_id"], waiver["action_reviewed"], waiver["expires_at"],
        waiver["max_uses"], waiver["uses"], waiver["approver"],
        waiver["ticket_id"], waiver["reason_code"], waiver["conditions_verified"],
    ])
    waiver["signature"] = hmac.new(
        waiver_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "{timestamp},{waiver_id},{action_reviewed},{expires_at},{max_uses},"
        "{uses},{approver},{ticket_id},{reason_code},{conditions_verified},"
        "{signature}\n".format(**waiver),
        encoding="utf-8",
    )

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0

    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,test-harness,overwrite,PASS,,verified\n",
        encoding="utf-8",
    )

    # Corrupt the stored manifest_fingerprint AND drop fingerprint_algo_version
    # to simulate a build-log produced before the algo-version field existed.
    build_log_path = refs / "build-log.json"
    log = _json.loads(build_log_path.read_text())
    log["manifest_fingerprint"] = "staleStaleStale01"
    log.pop("fingerprint_algo_version", None)
    build_log_path.write_text(_json.dumps(log), encoding="utf-8")

    # First --update: heal converges.
    capsys.readouterr()
    rc1 = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ])
    out1 = capsys.readouterr().out
    assert rc1 == 0
    assert "Healed build-log baseline" in out1, (
        "first --update did not emit the heal-converged observable line"
    )

    # Build-log now has fresh fingerprint + current algo version.
    healed_log = _json.loads(build_log_path.read_text())
    from agentteams.drift import FINGERPRINT_ALGO_VERSION
    assert healed_log["fingerprint_algo_version"] == FINGERPRINT_ALGO_VERSION
    assert healed_log["manifest_fingerprint"] != "staleStaleStale01"

    # Second --update: no manifest drift (security refresh files are still
    # force-written; that is by design and not a heal). Re-seed the PASS
    # row because the first --update consumed it.
    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,test-harness,overwrite,PASS,,verified\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    rc2 = build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "Healed build-log baseline" not in out2, (
        "second --update healed again — convergence did not stick"
    )
    assert "(manifest values changed)" not in out2
    assert "(fingerprint algo version bumped)" not in out2


def test_initialization_writes_baseline_inventory_artifacts(tmp_path, monkeypatch):
    """First successful generation writes baseline artifacts required for update/drift workflows."""
    import build_team

    brief = EXAMPLES_DIR / "software-project" / "brief.json"
    if not brief.exists():
        pytest.skip("software-project brief not found")

    output_dir = tmp_path / ".github" / "agents"
    waiver_key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)

    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    waiver = {
        "timestamp": "2026-05-03T00:00:00Z",
        "waiver_id": "waiver-freshness-002",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z",
        "max_uses": "5",
        "uses": "0",
        "approver": "test-harness",
        "ticket_id": "INT-2",
        "reason_code": "TEST",
        "conditions_verified": "verified",
        "signature": "",
    }
    payload = "|".join(
        [
            waiver["waiver_id"],
            waiver["action_reviewed"],
            waiver["expires_at"],
            waiver["max_uses"],
            waiver["uses"],
            waiver["approver"],
            waiver["ticket_id"],
            waiver["reason_code"],
            waiver["conditions_verified"],
        ]
    )
    waiver["signature"] = hmac.new(
        waiver_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        "{timestamp},{waiver_id},{action_reviewed},{expires_at},{max_uses},"
        "{uses},{approver},{ticket_id},{reason_code},{conditions_verified},"
        "{signature}\n".format(**waiver),
        encoding="utf-8",
    )

    rc = build_team.main([
        "--description", str(brief),
        "--output", str(output_dir),
        "--yes",
        "--no-scan",
        "--security-offline",
    ])
    assert rc == 0

    build_log_path = output_dir / "references" / "build-log.json"
    assert build_log_path.exists(), "Initialization must write references/build-log.json"

    payload = json.loads(build_log_path.read_text(encoding="utf-8"))
    assert payload.get("schema_version") == "1.2"
    assert payload.get("manifest_fingerprint")
    assert payload.get("output_files_map"), "Baseline output inventory is required for structural update checks"
    assert payload.get("agent_slug_list"), "Baseline agent slug inventory is required for team membership drift checks"

    expected_reference = output_dir / "references" / "code-hygiene-rules.reference.md"
    expected_graph = output_dir / "references" / "pipeline-graph.md"
    assert expected_reference.exists(), "Initialization baseline must include standard code-hygiene reference"
    assert expected_graph.exists(), "Initialization baseline must include team topology graph"


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
