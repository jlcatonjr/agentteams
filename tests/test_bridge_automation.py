"""Automation coverage for bridge maintenance script and workflows."""

from pathlib import Path


def test_daily_bridge_maintenance_script_contains_required_phases():
    script = Path("scripts/run_daily_bridge_maintenance.sh")
    assert script.exists()

    text = script.read_text(encoding="utf-8")
    assert "Refusing to run outside agentteams repository root" in text
    assert "targets=(\"copilot-cli\" \"claude\")" in text
    assert "--bridge-refresh" in text
    assert "--bridge-check" in text
    assert "Primary source bridge directory missing; using fallback" in text
    assert "examples/project-repositories/expected" in text
    assert "tmp/bridge-maintenance" in text
    assert "non-critical warning" in text


def test_bridge_maintenance_workflow_invokes_script_and_uploads_summary():
    workflow = Path(".github/workflows/bridge-maintenance.yml")
    assert workflow.exists()

    text = workflow.read_text(encoding="utf-8")
    assert 'name: Daily AgentTeams Bridge Maintenance' in text
    assert 'workflow_dispatch:' in text
    assert 'cron: "41 5 * * *"' in text
    assert 'bash scripts/run_daily_bridge_maintenance.sh' in text
    assert 'uses: actions/upload-artifact@v4' in text
    assert 'path: tmp/bridge-maintenance/' in text


def test_bridge_watchdog_workflow_checks_staleness_and_dedupes_issue():
    workflow = Path(".github/workflows/bridge-watchdog.yml")
    assert workflow.exists()

    text = workflow.read_text(encoding="utf-8")
    assert 'name: AgentTeams Bridge Watchdog' in text
    assert 'issues: write' in text
    assert 'actions: read' in text
    assert 'workflow_id: workflowId' in text
    assert 'const staleHours = 48;' in text
    assert 'const label = "bridge-watchdog";' in text
    assert 'const existingIssues = openIssues.data.filter' in text
    assert 'state: "closed"' in text
    assert 'Closing this stale alert issue.' in text
    assert 'title: "Bridge Maintenance Stale"' in text
