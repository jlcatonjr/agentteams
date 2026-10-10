"""R6: generation and audit for agents granted the agentteams_runner MCP server (mcp-mediated-agent-writes).

Renders real teams through the CLI on claude and goose, then checks the granted agent's front matter / recipe, the
installed server, the audit, and that a team without grants is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import runner_mcp
from agentteams.audit_agent_contract import _check_write_policy

REPO = Path(__file__).resolve().parents[1]
GRANTED = "primary-producer"
GRANT = {"tools": ["read_file_hashed", "write_file", "run_command", "request_status"]}

pytestmark = pytest.mark.skipif(not any(os.path.isfile(p) for p in runner_mcp.SYSTEM_PYTHONS),
                                reason="no system python3 to launch the server with")


def _brief(tmp_path: Path, *, grants: bool) -> Path:
    brief = json.loads((REPO / "examples/software-project/brief.json").read_text())
    brief.update({"write_policy": "orchestrator-only", "privilege_profile": "confined", "goose_tool_scoping": "grant",
                  "agent_policies": {GRANTED: {"write_scopes": ["src/"]}},
                  "proposal_gates": {"syntax": {"glob": "src/**.py", "argv": ["/usr/bin/true", "{file}"]}}})
    if grants:
        brief["mcp_grants"] = {GRANTED: GRANT}
    path = tmp_path / ("granted.json" if grants else "plain.json")
    path.write_text(json.dumps(brief))
    return path


def _render(tmp_path: Path, framework: str, *, grants: bool) -> Path:
    project = tmp_path / f"{framework}-{'g' if grants else 'p'}"
    project.mkdir()
    out = project / ".claude" / "agents" if framework == "claude" else project
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(_brief(tmp_path, grants=grants)),
                           "--framework", framework, "--output", str(out), "--project", str(project), "--yes"],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return project


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("r6")
    return {(fw, g): _render(tmp, fw, grants=g) for fw in ("claude", "goose") for g in (True, False)}


def _agents(project: Path, framework: str) -> tuple[Path, str]:
    return (project / ".claude/agents", ".md") if framework == "claude" else (project / ".goose/recipes", ".yaml")


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_the_granted_agent_gets_exactly_the_canonical_server(renders, framework):
    project = renders[(framework, True)]
    directory, ext = _agents(project, framework)
    text = (directory / f"{GRANTED}{ext}").read_text()
    if framework == "claude":
        assert runner_mcp.claude_block(GRANTED, GRANT["tools"], "staged", runner_mcp.interpreter()) in text
        tools_line = next(line for line in text.splitlines() if line.startswith("tools:"))
        for name in runner_mcp.tool_names(GRANT["tools"]):
            assert name in tools_line
        assert "Bash" not in tools_line and "Write" not in tools_line and "Edit" not in tools_line
    else:
        assert 'name: "agentteams_runner"' in text and f'- "{GRANTED}"' in text
        assert "available_tools: [write_file, run_command, request_status, read_file_hashed]" in text
    assert "agentteams_runner" in text and "Return Proposals, Never Write" in text


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_the_server_is_installed_with_the_pinned_hash(renders, framework):
    installed = renders[(framework, True)] / runner_mcp.PROTECTED_PATH
    assert hashlib.sha256(installed.read_bytes()).hexdigest() == runner_mcp.SHA256
    assert not (renders[(framework, False)] / runner_mcp.PROTECTED_PATH).exists()


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_the_render_passes_the_write_policy_audit(renders, framework):
    directory, ext = _agents(renders[(framework, True)], framework)
    files = {p.name: p.read_text() for p in directory.glob(f"*{ext}")}
    findings = _check_write_policy(files, agent_ext=ext, framework=framework, enabled=True,
                                   mcp_grants={GRANTED: GRANT})
    assert [f.description for f in findings if f.severity == "error"] == []


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_ungranted_agents_are_unchanged(renders, framework):
    """Only the granted agent and the orchestrator differ; every other agent file is byte-identical."""
    granted_dir, ext = _agents(renders[(framework, True)], framework)
    plain_dir, _ = _agents(renders[(framework, False)], framework)
    def body(path: Path) -> str:  # generation timestamps (e.g. the threat-intel snapshot) differ between renders
        return re.sub(r"(Generated at: `[^`]*`|\d+(?:\.\d+)? days old|age_hours=[\d.]+)", "", path.read_text())

    # The security agent embeds live threat intelligence (CVE feeds fetched at render time), so two renders minutes
    # apart differ there for reasons unrelated to grants.
    differ = sorted(p.name for p in granted_dir.glob(f"*{ext}")
                    if (plain_dir / p.name).exists() and p.stem != "security" and body(p) != body(plain_dir / p.name))
    import difflib

    detail = {n: [l for l in difflib.unified_diff(body(plain_dir / n).splitlines(), body(granted_dir / n).splitlines(),
                                                lineterm="") if l.startswith(("+", "-"))][:6]
              for n in differ if n not in (f"{GRANTED}{ext}", f"orchestrator{ext}")}
    assert set(differ) <= {f"{GRANTED}{ext}", f"orchestrator{ext}"}, detail


def test_no_machine_path_is_written_into_the_team(renders):
    """The server finds its root from its install location; no home directory lands in agent files."""
    home = str(Path.home())
    for framework in ("claude", "goose"):
        directory, ext = _agents(renders[(framework, True)], framework)
        assert not [p.name for p in directory.glob(f"*{ext}") if home in p.read_text()]


def test_the_installed_server_finds_its_own_root(renders, tmp_path):
    project = renders[("claude", True)]
    proc = subprocess.run([runner_mcp.interpreter(), "-I", "-S", runner_mcp.PROTECTED_PATH, "--agent", GRANTED,
                           "--tools", "read_file_hashed"], cwd=project, capture_output=True, text=True, timeout=30,
                          input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                              "name": "read_file_hashed", "arguments": {"path": "CLAUDE.md"}}}) + "\n")
    reply = json.loads(proc.stdout.splitlines()[0])
    assert not reply["result"].get("isError"), reply
    copy = tmp_path / "loose.py"
    copy.write_text((project / runner_mcp.PROTECTED_PATH).read_text())
    stray = subprocess.run([runner_mcp.interpreter(), "-I", "-S", str(copy), "--agent", "a", "--tools", "write_file"],
                           capture_output=True, text=True, timeout=30, input="")
    assert stray.returncode != 0 and "not installed under" in stray.stderr


@pytest.mark.parametrize("extra", ["      env: {DYLD_INSERT_LIBRARIES: /tmp/x.dylib}\n", "      cwd: /tmp\n",
                                   "  - other_server:\n      type: stdio\n      command: /bin/sh\n"])
def test_nothing_may_follow_the_canonical_block(renders, extra):
    """@security R6 condition 1: indented leftovers after the block (env, cwd, another server) are refused."""
    directory, ext = _agents(renders[("claude", True)], "claude")
    files = {p.name: p.read_text() for p in directory.glob("*.md")}
    text = files[f"{GRANTED}.md"]
    block = runner_mcp.claude_block(GRANTED, GRANT["tools"], "staged", runner_mcp.interpreter())
    files[f"{GRANTED}.md"] = text.replace(block, block + extra)
    findings = _check_write_policy(files, agent_ext=".md", framework="claude", enabled=True,
                                   mcp_grants={GRANTED: GRANT})
    assert any(f.file == f"{GRANTED}.md" and f.severity == "error" and "extra lines" in f.description
               for f in findings)


def test_rendering_refuses_an_install_outside_the_project(tmp_path):
    """@security R6 condition 3: with grants, the agents dir must sit two levels below the project root."""
    project = tmp_path / "proj"
    out = project / "agents-here"
    out.mkdir(parents=True)
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(_brief(tmp_path, grants=True)),
                           "--framework", "claude", "--output", str(out), "--project", str(project), "--yes"],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 1 and "outside the project" in proc.stderr
    assert not (tmp_path / runner_mcp.PROTECTED_PATH).exists()


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_the_build_log_records_the_switch(renders, framework):
    """--convert-from and bridge subagent stubs read this to refuse a team under the switch."""
    directory, _ = _agents(renders[(framework, True)], framework)
    log = json.loads((directory / "references" / "build-log.json").read_text())
    assert log.get("write_policy") == "orchestrator-only"
