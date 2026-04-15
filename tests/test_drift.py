"""
Tests for src/drift.py — template drift detection.
"""

import json
import hashlib
import pytest
from pathlib import Path
from agentteams.drift import (
    detect_drift,
    load_build_log,
    DriftReport,
    compute_structural_diff,
    compute_manifest_fingerprint,
    print_structural_diff_report,
    StructuralDiffReport,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_dirs(tmp_path):
    """Create agents_dir and templates_dir with a basic template and build log."""
    agents_dir = tmp_path / ".github" / "agents"
    refs_dir = agents_dir / "references"
    refs_dir.mkdir(parents=True)
    templates_dir = tmp_path / "templates" / "universal"
    templates_dir.mkdir(parents=True)

    # Create a template
    template_content = "# {PROJECT_NAME}\n\nTemplate content."
    template_file = templates_dir / "orchestrator.template.md"
    template_file.write_text(template_content, encoding="utf-8")

    template_hash = hashlib.sha256(template_file.read_bytes()).hexdigest()[:16]

    # Create build-log.json with template hashes
    build_log = {
        "schema_version": "1.1",
        "project_name": "TestProject",
        "framework": "copilot-vscode",
        "project_type": "software",
        "archetypes": ["primary-producer"],
        "components": [],
        "files_written": [".github/agents/orchestrator.agent.md"],
        "manual_required": 0,
        "template_hashes": {
            "universal/orchestrator.template.md": template_hash,
        },
    }
    (refs_dir / "build-log.json").write_text(json.dumps(build_log), encoding="utf-8")

    return agents_dir, tmp_path / "templates"


# ---------------------------------------------------------------------------
# load_build_log
# ---------------------------------------------------------------------------

def test_load_build_log(setup_dirs):
    agents_dir, _ = setup_dirs
    log = load_build_log(agents_dir)
    assert log["project_name"] == "TestProject"
    assert "template_hashes" in log


def test_load_build_log_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="No build-log.json"):
        load_build_log(tmp_path)


def test_load_build_log_malformed(tmp_path):
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "build-log.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Malformed"):
        load_build_log(tmp_path)


# ---------------------------------------------------------------------------
# detect_drift — no drift
# ---------------------------------------------------------------------------

def test_no_drift(setup_dirs):
    agents_dir, templates_dir = setup_dirs
    report = detect_drift(agents_dir, templates_dir)
    assert not report.has_drift
    assert len(report.unchanged) == 1
    assert report.changed_templates == []


# ---------------------------------------------------------------------------
# detect_drift — template changed
# ---------------------------------------------------------------------------

def test_drift_detected_on_template_change(setup_dirs):
    agents_dir, templates_dir = setup_dirs

    # Modify the template
    tpl = templates_dir / "universal" / "orchestrator.template.md"
    tpl.write_text("# Modified template\n\nNew content.", encoding="utf-8")

    report = detect_drift(agents_dir, templates_dir)
    assert report.has_drift
    assert len(report.changed_templates) == 1
    assert report.changed_templates[0]["reason"] == "template content changed"


# ---------------------------------------------------------------------------
# detect_drift — template deleted
# ---------------------------------------------------------------------------

def test_drift_missing_template(setup_dirs):
    agents_dir, templates_dir = setup_dirs

    # Delete the template
    (templates_dir / "universal" / "orchestrator.template.md").unlink()

    report = detect_drift(agents_dir, templates_dir)
    assert report.has_drift
    assert len(report.missing_templates) == 1


# ---------------------------------------------------------------------------
# detect_drift — new template
# ---------------------------------------------------------------------------

def test_new_template_detected(setup_dirs):
    agents_dir, templates_dir = setup_dirs

    # Add a new template
    new_tpl = templates_dir / "universal" / "navigator.template.md"
    new_tpl.write_text("# Navigator\n", encoding="utf-8")

    report = detect_drift(agents_dir, templates_dir)
    assert not report.has_drift  # new templates alone aren't "drift"
    assert len(report.new_templates) == 1


# ---------------------------------------------------------------------------
# detect_drift — legacy build log (no hashes)
# ---------------------------------------------------------------------------

def test_legacy_build_log_all_drifted(setup_dirs):
    agents_dir, templates_dir = setup_dirs

    # Rewrite build log without template_hashes
    log_path = agents_dir / "references" / "build-log.json"
    log = json.loads(log_path.read_text())
    del log["template_hashes"]
    log["schema_version"] = "1.0"
    log_path.write_text(json.dumps(log), encoding="utf-8")

    report = detect_drift(agents_dir, templates_dir)
    assert report.has_drift
    # All templates treated as potentially drifted
    assert len(report.changed_templates) >= 1


# ---------------------------------------------------------------------------
# affected_output_files
# ---------------------------------------------------------------------------

def test_affected_output_files(setup_dirs):
    agents_dir, templates_dir = setup_dirs

    # Modify template to trigger drift
    tpl = templates_dir / "universal" / "orchestrator.template.md"
    tpl.write_text("# Changed\n", encoding="utf-8")

    report = detect_drift(agents_dir, templates_dir)
    affected = report.affected_output_files
    assert len(affected) == 1
    assert "orchestrator" in affected[0]


# ===========================================================================
# compute_structural_diff
# ===========================================================================

def _make_log_v12(files: list[dict], template_hashes: dict, agent_slugs: list[str], manifest_fingerprint: str | None = None) -> dict:
    """Build a minimal schema v1.2 build-log dict."""
    return {
        "schema_version": "1.2",
        "project_name": "TestProject",
        "framework": "copilot-vscode",
        "project_type": "software",
        "archetypes": ["primary-producer"],
        "components": [],
        "files_written": [f["path"] for f in files],
        "manual_required": 0,
        "template_hashes": template_hashes,
        "output_files_map": files,
        "agent_slug_list": agent_slugs,
        "governance_agents": [],
        "manifest_fingerprint": manifest_fingerprint,
    }


def _make_manifest(files: list[dict], agent_slugs: list[str], **extra: object) -> dict:
    """Build a minimal manifest dict matching analyze.build_manifest() shape."""
    manifest = {
        "project_name": "TestProject",
        "output_files": files,
        "agent_slug_list": agent_slugs,
    }
    manifest.update(extra)
    return manifest


# ---------------------------------------------------------------------------
# Additions
# ---------------------------------------------------------------------------

def test_structural_diff_additions(tmp_path):
    """Files in new manifest not in old log → classified as added."""
    old_files = [
        {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
    ]
    new_files = old_files + [
        {"path": "code-hygiene.agent.md", "template": "universal/code-hygiene.template.md", "type": "agent"},
        {"path": "references/code-hygiene-rules.reference.md", "template": "domain/code-hygiene-rules-reference.template.md", "type": "reference"},
    ]
    old_log = _make_log_v12(old_files, {}, ["orchestrator"])
    manifest = _make_manifest(new_files, ["orchestrator", "code-hygiene"])

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert len(report.added_files) == 2
    added_paths = {f["path"] for f in report.added_files}
    assert "code-hygiene.agent.md" in added_paths
    assert "references/code-hygiene-rules.reference.md" in added_paths
    assert report.has_changes


# ---------------------------------------------------------------------------
# Removals
# ---------------------------------------------------------------------------

def test_structural_diff_removals(tmp_path):
    """Files in old log not in new manifest → classified as removed."""
    old_files = [
        {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
        {"path": "deprecated-agent.agent.md", "template": "universal/deprecated-agent.template.md", "type": "agent"},
    ]
    new_files = [old_files[0]]  # deprecated removed
    old_log = _make_log_v12(old_files, {}, ["orchestrator", "deprecated-agent"])
    manifest = _make_manifest(new_files, ["orchestrator"])

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert len(report.removed_files) == 1
    assert report.removed_files[0]["path"] == "deprecated-agent.agent.md"
    # Removed agents change the slug list → copilot-instructions needs re-render
    assert report.team_membership_changed
    # Removed files are never in the write set
    update_paths = {f["path"] for f in report.update_files}
    assert "deprecated-agent.agent.md" not in update_paths


# ---------------------------------------------------------------------------
# Drifted (template hash changed)
# ---------------------------------------------------------------------------

def test_structural_diff_drifted(tmp_path):
    """Same path, different template hash → classified as drifted."""
    tpl_dir = tmp_path / "universal"
    tpl_dir.mkdir(parents=True)
    tpl_file = tpl_dir / "orchestrator.template.md"
    tpl_file.write_text("# Updated template", encoding="utf-8")
    current_hash = hashlib.sha256(tpl_file.read_bytes()).hexdigest()[:16]

    files = [{"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"}]
    old_log = _make_log_v12(files, {"universal/orchestrator.template.md": "aabbccdd11223344"}, ["orchestrator"])
    manifest = _make_manifest(files, ["orchestrator"])

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert len(report.drifted_files) == 1
    assert report.drifted_files[0]["_reason"] == "template content changed"
    assert report.has_changes


# ---------------------------------------------------------------------------
# Unchanged
# ---------------------------------------------------------------------------

def test_structural_diff_unchanged(tmp_path):
    """Same path, same template hash → classified as unchanged."""
    tpl_dir = tmp_path / "universal"
    tpl_dir.mkdir(parents=True)
    tpl_file = tpl_dir / "orchestrator.template.md"
    tpl_file.write_text("# Template", encoding="utf-8")
    current_hash = hashlib.sha256(tpl_file.read_bytes()).hexdigest()[:16]

    files = [{"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"}]
    manifest = _make_manifest(files, ["orchestrator"])
    old_log = _make_log_v12(
        files,
        {"universal/orchestrator.template.md": current_hash},
        ["orchestrator"],
        manifest_fingerprint=compute_manifest_fingerprint(manifest),
    )

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert len(report.unchanged_files) == 1
    assert report.added_files == []
    assert report.drifted_files == []
    assert not report.has_changes


def test_structural_diff_manifest_change_rerenders_current_files(tmp_path):
    """A brief-only change should promote unchanged files into the drifted set."""
    tpl_dir = tmp_path / "universal"
    tpl_dir.mkdir(parents=True)
    tpl_file = tpl_dir / "orchestrator.template.md"
    tpl_file.write_text("# Template", encoding="utf-8")
    current_hash = hashlib.sha256(tpl_file.read_bytes()).hexdigest()[:16]

    files = [{"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"}]
    old_manifest = _make_manifest(files, ["orchestrator"], reference_db_path=None)
    new_manifest = _make_manifest(files, ["orchestrator"], reference_db_path=".github/agents/references/project-references.bib")
    old_log = _make_log_v12(
        files,
        {"universal/orchestrator.template.md": current_hash},
        ["orchestrator"],
        manifest_fingerprint=compute_manifest_fingerprint(old_manifest),
    )

    report = compute_structural_diff(old_log, new_manifest, tmp_path)

    assert report.manifest_changed
    assert len(report.drifted_files) == 1
    assert report.drifted_files[0]["_reason"] == "manifest values changed"
    assert report.unchanged_files == []


def test_structural_diff_missing_manifest_fingerprint_promotes_files(tmp_path):
    """Older build logs should trigger a one-time refresh when no fingerprint exists."""
    tpl_dir = tmp_path / "universal"
    tpl_dir.mkdir(parents=True)
    tpl_file = tpl_dir / "orchestrator.template.md"
    tpl_file.write_text("# Template", encoding="utf-8")
    current_hash = hashlib.sha256(tpl_file.read_bytes()).hexdigest()[:16]

    files = [{"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"}]
    manifest = _make_manifest(files, ["orchestrator"])
    old_log = _make_log_v12(
        files,
        {"universal/orchestrator.template.md": current_hash},
        ["orchestrator"],
        manifest_fingerprint=None,
    )

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert report.manifest_changed
    assert len(report.drifted_files) == 1
    assert report.drifted_files[0]["_reason"] == "manifest fingerprint unavailable"


# ---------------------------------------------------------------------------
# Legacy log (no output_files_map)
# ---------------------------------------------------------------------------

def test_structural_diff_no_old_map(tmp_path):
    """Missing output_files_map → legacy fallback, team_membership_changed=True."""
    new_files = [
        {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
        {"path": "code-hygiene.agent.md", "template": "universal/code-hygiene.template.md", "type": "agent"},
    ]
    # Legacy log: has files_written but no output_files_map
    old_log = {
        "schema_version": "1.1",
        "files_written": [".github/agents/orchestrator.agent.md"],
        "template_hashes": {},
        "agent_slug_list": ["orchestrator"],
    }
    manifest = _make_manifest(new_files, ["orchestrator", "code-hygiene"])

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert report.legacy_log
    assert report.team_membership_changed
    # code-hygiene not in files_written → added
    added_paths = {f["path"] for f in report.added_files}
    assert "code-hygiene.agent.md" in added_paths


# ---------------------------------------------------------------------------
# Team membership change forces copilot-instructions re-render
# ---------------------------------------------------------------------------

def test_structural_diff_team_membership_change(tmp_path):
    """Different agent_slug_list → copilot-instructions.md forced into drifted set."""
    files = [
        {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
        {"path": "../copilot-instructions.md", "template": "copilot-instructions.template.md", "type": "instructions"},
    ]
    old_log = _make_log_v12(files, {}, ["orchestrator"])
    new_slugs = ["orchestrator", "code-hygiene"]
    manifest = _make_manifest(files, new_slugs)

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert report.team_membership_changed
    drifted_paths = {f["path"] for f in report.drifted_files}
    assert "../copilot-instructions.md" in drifted_paths


def test_structural_diff_no_membership_change(tmp_path):
    """Same agent_slug_list → team_membership_changed is False."""
    files = [
        {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
    ]
    slugs = ["orchestrator", "navigator"]
    old_log = _make_log_v12(files, {}, slugs)
    manifest = _make_manifest(files, slugs)

    report = compute_structural_diff(old_log, manifest, tmp_path)

    assert not report.team_membership_changed


# ---------------------------------------------------------------------------
# update_files property
# ---------------------------------------------------------------------------

def test_structural_diff_update_files_combines_added_and_drifted(tmp_path):
    """update_files returns added + drifted entries."""
    tpl_dir = tmp_path / "universal"
    tpl_dir.mkdir(parents=True)
    tpl_file = tpl_dir / "orchestrator.template.md"
    tpl_file.write_text("# Updated", encoding="utf-8")

    old_files = [
        {"path": "orchestrator.agent.md", "template": "universal/orchestrator.template.md", "type": "agent"},
    ]
    new_files = old_files + [
        {"path": "code-hygiene.agent.md", "template": "universal/code-hygiene.template.md", "type": "agent"},
    ]
    old_log = _make_log_v12(old_files, {"universal/orchestrator.template.md": "aabbccdd11223344"}, ["orchestrator"])
    manifest = _make_manifest(new_files, ["orchestrator", "code-hygiene"])

    report = compute_structural_diff(old_log, manifest, tmp_path)

    paths = {f["path"] for f in report.update_files}
    assert "orchestrator.agent.md" in paths      # drifted
    assert "code-hygiene.agent.md" in paths      # added


# ---------------------------------------------------------------------------
# print_structural_diff_report (smoke test — no crash)
# ---------------------------------------------------------------------------

def test_print_structural_diff_report_no_changes(capsys):
    report = StructuralDiffReport()
    print_structural_diff_report(report)
    out = capsys.readouterr().out
    assert "No structural changes" in out


def test_print_structural_diff_report_with_changes(capsys):
    report = StructuralDiffReport()
    report.added_files = [{"path": "code-hygiene.agent.md"}]
    report.removed_files = [{"path": "old-agent.agent.md"}]
    print_structural_diff_report(report)
    out = capsys.readouterr().out
    assert "Added" in out
    assert "Removed" in out
