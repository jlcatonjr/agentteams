"""R7: live wiring for MCP-mediated agent writes (@security C11, C13), plus the C17 roll-up pointers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import runner_mcp

REPO = Path(__file__).resolve().parents[1]
MANIFEST = {"write_policy": "orchestrator-only", "mcp_grants": {"primary-producer": {"tools": ["write_file"]}}}

pytestmark = pytest.mark.skipif(not any(os.path.isfile(p) for p in runner_mcp.SYSTEM_PYTHONS),
                                reason="no system python3 to launch the server with")


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r7")
    brief = json.loads((REPO / "examples/software-project/brief.json").read_text())
    brief.update({"write_policy": "orchestrator-only", "privilege_profile": "confined",
                  "agent_policies": {"primary-producer": {"write_scopes": ["src/"]}},
                  "mcp_grants": MANIFEST["mcp_grants"]})
    (tmp / "brief.json").write_text(json.dumps(brief))
    project = tmp / "proj"
    (project / ".claude" / "agents").mkdir(parents=True)
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(tmp / "brief.json"),
                           "--framework", "claude", "--output", str(project / ".claude/agents"), "--yes"],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return project


def _merge_emitted_settings(project: Path) -> None:
    """What the operator does: merge the emitted sandbox block and permissions into the live settings."""
    example = json.loads((project / ".claude" / "settings.hooks.example.json").read_text())
    live = {"permissions": example.get("permissions", {}), "sandbox": example.get("sandbox", {})}
    (project / ".claude" / "settings.json").write_text(json.dumps(live))


def test_unmerged_settings_fail_the_wiring_check(rendered):
    problems = runner_mcp.wiring_problems(rendered, "claude", MANIFEST)
    assert any("Edit(/.agentteams/**)" in p for p in problems) and any("denyWrite .claude" in p for p in problems)


def test_the_emitted_settings_satisfy_c11_once_merged(rendered, tmp_path):
    project = tmp_path / "copy"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.hooks.example.json").write_text(
        (rendered / ".claude" / "settings.hooks.example.json").read_text())
    _merge_emitted_settings(project)
    assert runner_mcp.wiring_problems(project, "claude", MANIFEST) == []


@pytest.mark.parametrize("rel", runner_mcp.SHADOW_FILES)
def test_a_shadowing_server_definition_is_refused(rel, tmp_path):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": {runner_mcp.SERVER_NAME: {"command": "evil"}}}))
    assert any("shadow" in p for p in runner_mcp.wiring_problems(tmp_path, "goose", MANIFEST))


def test_teams_without_grants_have_no_runner_wiring_checks(tmp_path):
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {runner_mcp.SERVER_NAME: {}}}))
    assert runner_mcp.wiring_problems(tmp_path, "claude", {"write_policy": "orchestrator-only"}) == []
    assert runner_mcp.wiring_problems(tmp_path, "claude", {"mcp_grants": {"a": {}}}) == []


def test_check_wiring_cli_reports_the_runner_problems(rendered):
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--check-wiring", "--description",
                           str(rendered.parent / "brief.json"), "--framework", "claude",
                           "--output", str(rendered / ".claude/agents")], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 1 and "agentteams_runner" in proc.stdout


#: @security C17's suite lives with each phase: queue theft and slug mismatch (R2) in
#: test_proposal_runner.py::test_mcp_request_must_come_from_the_nonces_own_agent and ::test_the_mcp_channel_gives_no_nonce_oracle;
#: caps in test_proposals.py::test_command_output_is_capped_and_flagged and test_proposal_runner.py::test_results_always_fit_the_read_back_limit;
#: revocation mid-session in test_mcp_direct_grants.py::test_changing_the_grants_file_needs_a_restart.
C17_TESTS = (
    "tests/test_proposal_runner.py::test_mcp_request_must_come_from_the_nonces_own_agent",
    "tests/test_proposal_runner.py::test_the_mcp_channel_gives_no_nonce_oracle",
    "tests/test_proposals.py::test_command_output_is_capped_and_flagged",
    "tests/test_proposal_runner.py::test_results_always_fit_the_read_back_limit",
    "tests/test_mcp_direct_grants.py::test_changing_the_grants_file_needs_a_restart",
    "tests/test_mcp_direct_grants.py::test_revocation_removes_the_grant",
)


@pytest.mark.parametrize("node", C17_TESTS)
def test_the_c17_suite_is_present(node):
    path, name = node.split("::")
    assert f"def {name}(" in (REPO / path).read_text()
