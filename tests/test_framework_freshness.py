"""Tests for agentteams/framework_freshness.py — cross-framework render-staleness scan.

Covers the durable fix for the silent-staleness failure class (a provider render
lagging the current templates while a sibling was updated, with no signal). See
references/plans/ or tmp/by-week/2026-W37/agentteams-fence-version-staleness.plan.md.
"""

import hashlib
import json
from pathlib import Path

import pytest

from agentteams import framework_freshness as ff


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_render(project_root: Path, rel_agents: str, framework: str,
                  template_rel: str, template_hash: str, *,
                  generated_at: str | None = None,
                  agentteams_version: str | None = None) -> Path:
    """Create a render dir with a references/build-log.json recording one template hash."""
    agents_dir = project_root / rel_agents
    (agents_dir / "references").mkdir(parents=True, exist_ok=True)
    log = {
        "schema_version": "1.5",
        "framework": framework,
        "template_hashes": {template_rel: template_hash},
        "output_files_map": [],
    }
    if generated_at is not None:
        log["generated_at"] = generated_at
    if agentteams_version is not None:
        log["agentteams_version"] = agentteams_version
    (agents_dir / "references" / "build-log.json").write_text(
        json.dumps(log), encoding="utf-8"
    )
    return agents_dir


@pytest.fixture
def project(tmp_path):
    """A project with a template plus a current render and a stale render.

    Returns (project_root, templates_dir, current_hash, stale_hash).
    """
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    tpl = templates_dir / "universal" / "orchestrator.template.md"
    tpl.write_text("# {PROJECT_NAME}\n\ncurrent contract v2.", encoding="utf-8")
    current_hash = hashlib.sha256(tpl.read_bytes()).hexdigest()[:16]
    stale_hash = "0" * 16  # deliberately not the current hash

    project_root = tmp_path / "proj"
    # current render (hash matches templates)
    _write_render(project_root, ".github/agents", "copilot-vscode",
                  "universal/orchestrator.template.md", current_hash,
                  generated_at="2026-09-08T10:00:00+00:00", agentteams_version="1.0.0")
    # stale render (recorded hash predates the template change)
    _write_render(project_root, ".claude/agents", "claude",
                  "universal/orchestrator.template.md", stale_hash,
                  generated_at="2026-08-19T00:40:00+00:00", agentteams_version="1.0.0")
    return project_root, templates_dir, current_hash, stale_hash


# ---------------------------------------------------------------------------
# scan / staleness classification
# ---------------------------------------------------------------------------

def test_scan_flags_stale_render_and_marks_current(project):
    project_root, templates_dir, _, _ = project
    report = ff.scan(project_root, templates_dir)
    by_fw = {r.framework: r for r in report.renders}
    assert set(by_fw) == {"copilot-vscode", "claude"}
    assert by_fw["copilot-vscode"].is_stale is False
    assert by_fw["claude"].is_stale is True
    assert "universal/orchestrator.template.md" in by_fw["claude"].changed_templates


def test_report_has_stale_and_exit_code(project):
    project_root, templates_dir, _, _ = project
    report = ff.scan(project_root, templates_dir)
    assert report.has_stale is True
    assert report.exit_code() == 1
    assert len(report.stale) == 1


def test_all_current_gives_exit_zero(tmp_path):
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    tpl = templates_dir / "universal" / "orchestrator.template.md"
    tpl.write_text("stable", encoding="utf-8")
    h = hashlib.sha256(tpl.read_bytes()).hexdigest()[:16]
    project_root = tmp_path / "proj"
    _write_render(project_root, ".github/agents", "copilot-vscode",
                  "universal/orchestrator.template.md", h)
    _write_render(project_root, ".claude/agents", "claude",
                  "universal/orchestrator.template.md", h)
    report = ff.scan(project_root, templates_dir)
    assert report.has_stale is False
    assert report.exit_code() == 0


def test_freshest_prefers_newest_stamp(project):
    project_root, templates_dir, _, _ = project
    report = ff.scan(project_root, templates_dir)
    assert report.freshest is not None
    assert report.freshest.framework == "copilot-vscode"  # 2026-09-08 > 2026-08-19


def test_provenance_fields_surfaced(project):
    project_root, templates_dir, _, _ = project
    report = ff.scan(project_root, templates_dir)
    claude = next(r for r in report.renders if r.framework == "claude")
    assert claude.generated_at == "2026-08-19T00:40:00+00:00"
    assert claude.agentteams_version == "1.0.0"


# ---------------------------------------------------------------------------
# discovery / exclusions — the fix must not flag backups, snapshots, worktrees
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("excluded_rel", [
    "tmp/by-week/2026-W21/snapshot/agents",
    ".agentteams-backups/20260101/agents",
    ".claude/worktrees/agent-x/.claude/agents",
    ".agentteams/canonical/agents",
    "tmp/by-week/2026-W27/backups/agents.bak",
    ".venv/x/agents",
])
def test_discover_excludes_non_live_trees(tmp_path, excluded_rel):
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    tpl = templates_dir / "universal" / "orchestrator.template.md"
    tpl.write_text("x", encoding="utf-8")
    h = hashlib.sha256(tpl.read_bytes()).hexdigest()[:16]
    project_root = tmp_path / "proj"
    # one live render + one excluded render
    _write_render(project_root, ".github/agents", "copilot-vscode",
                  "universal/orchestrator.template.md", h)
    _write_render(project_root, excluded_rel, "claude",
                  "universal/orchestrator.template.md", h)
    dirs = ff.discover_render_dirs(project_root)
    assert (project_root / ".github/agents") in dirs
    assert (project_root / excluded_rel) not in dirs


def test_project_under_excluded_named_prefix_still_scanned(tmp_path):
    """Regression for the absolute-parts bug: a project checked out under a directory
    named like an excluded segment (e.g. 'build') must still have its renders found —
    exclusion applies only to parts RELATIVE to project_root."""
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    tpl = templates_dir / "universal" / "orchestrator.template.md"
    tpl.write_text("x", encoding="utf-8")
    h = hashlib.sha256(tpl.read_bytes()).hexdigest()[:16]
    # project_root itself lives under a segment named "build" (an excluded name)
    project_root = tmp_path / "build" / "proj"
    _write_render(project_root, ".github/agents", "copilot-vscode",
                  "universal/orchestrator.template.md", h)
    dirs = ff.discover_render_dirs(project_root)
    assert (project_root / ".github/agents") in dirs
    report = ff.scan(project_root, templates_dir)
    assert len(report.renders) == 1


def test_legacy_build_log_is_unverifiable_not_stale(tmp_path):
    """A build-log with no template_hashes cannot be compared — report unverifiable,
    not STALE (no cry-wolf), and do not fail the exit code on it alone."""
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    (templates_dir / "universal" / "orchestrator.template.md").write_text("x", encoding="utf-8")
    project_root = tmp_path / "proj"
    agents_dir = project_root / ".claude" / "agents"
    (agents_dir / "references").mkdir(parents=True)
    (agents_dir / "references" / "build-log.json").write_text(
        json.dumps({"schema_version": "1.1", "framework": "claude"}), encoding="utf-8"
    )
    report = ff.scan(project_root, templates_dir)
    assert len(report.renders) == 1
    r = report.renders[0]
    assert r.unverifiable is True
    assert r.is_stale is False
    assert report.has_stale is False
    assert report.exit_code() == 0
    assert report.unverifiable == [r]


def test_freshest_ignores_stale_renders(tmp_path):
    """freshest must not name a stale render even if it has the newest timestamp."""
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    tpl = templates_dir / "universal" / "orchestrator.template.md"
    tpl.write_text("current", encoding="utf-8")
    h = hashlib.sha256(tpl.read_bytes()).hexdigest()[:16]
    project_root = tmp_path / "proj"
    # newest timestamp but STALE
    _write_render(project_root, ".claude/agents", "claude",
                  "universal/orchestrator.template.md", "0" * 16,
                  generated_at="2026-09-10T00:00:00+00:00")
    # older but CURRENT
    _write_render(project_root, ".github/agents", "copilot-vscode",
                  "universal/orchestrator.template.md", h,
                  generated_at="2026-09-01T00:00:00+00:00")
    report = ff.scan(project_root, templates_dir)
    assert report.freshest is not None
    assert report.freshest.framework == "copilot-vscode"  # current, despite older stamp
    assert report.freshest.is_stale is False


def test_scan_empty_project_no_renders(tmp_path):
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    report = ff.scan(tmp_path / "proj", templates_dir)
    assert report.renders == []
    assert report.has_stale is False
    assert report.exit_code() == 0


def test_malformed_build_log_recorded_as_error_not_crash(tmp_path):
    templates_dir = tmp_path / "templates"
    (templates_dir / "universal").mkdir(parents=True)
    project_root = tmp_path / "proj"
    agents_dir = project_root / ".claude" / "agents"
    (agents_dir / "references").mkdir(parents=True)
    (agents_dir / "references" / "build-log.json").write_text("{not json", encoding="utf-8")
    report = ff.scan(project_root, templates_dir)
    assert report.renders == []
    assert len(report.errors) == 1
