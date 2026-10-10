"""P3 of the orchestrator-only-writes pilot: generated agents under ``write_policy: "orchestrator-only"``.

``agentteams.write_policy.apply`` runs before every framework adapter. It narrows each non-orchestrator
agent's canonical ``tools:`` line and appends the proposal section; the orchestrator keeps its tools and
gets the "Applying proposals" workflow. Without the switch it is the identity.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import analyze, liaison_logs, mcp_need, write_policy
from agentteams.audit import run_post_audit

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
ON = {"write_policy": "orchestrator-only", "privilege_profile": "confined"}


def _agent(tools: str, body: str = "# X\nEdit the file.\n") -> str:
    return f"---\nname: X\ndescription: d\ntools: {tools}\n---\n{body}"


@pytest.mark.parametrize("tools, expected", [
    ("['read', 'edit', 'search', 'execute', 'todo', 'agent']", "tools: ['read', 'search', 'todo']"),
    ("['edit', 'execute']", "tools: ['read']"),                     # read is always kept
    ("['read', 'retrieval']", "tools: ['read']"),                   # retrieval maps to a Claude Bash grant
    ("['Read', 'Search']", "tools: ['read', 'search']"),
])
def test_narrow_tools(tools, expected):
    assert expected in write_policy.narrow_tools(_agent(tools))


@pytest.mark.parametrize("tools", ["\n  - read\n  - edit", "'*'", "Read, Edit"])
def test_tools_key_of_another_shape_is_refused(tools):
    with pytest.raises(ValueError, match="one-line flow list"):
        write_policy.narrow_tools(_agent(tools))


def test_legacy_allowed_tools_is_removed():
    # The Claude builder template declares only `allowed-tools`; narrowing must not leave that write grant.
    out = write_policy.narrow_tools("---\nname: X\nallowed-tools: Read, Edit, Write, Bash\n---\nbody\n")
    assert "allowed-tools" not in out and "tools: ['read', 'search']" in out and out.endswith("---\nbody\n")
    out = write_policy.narrow_tools(_agent("['read', 'edit']").replace("tools:", "allowed-tools: Edit\ntools:", 1))
    assert "allowed-tools" not in out and "tools: ['read']" in out


@pytest.mark.parametrize("value", ["\n  - Read\n  - Edit", " [Read,\n  Write]", " Read,\n  Write"])
def test_multiline_allowed_tools_is_refused(value):
    with pytest.raises(ValueError, match="allowed-tools"):
        write_policy.narrow_tools(f"---\nname: X\nallowed-tools:{value}\ndescription: d\n---\nbody\n")


def test_quoted_allowed_tools_is_removed():
    out = write_policy.narrow_tools("---\nname: X\n\"allowed-tools\": Read, Write\n---\nbody\n")
    assert "allowed-tools" not in out


def test_missing_tools_line_gets_read_only():
    assert "tools: ['read', 'search']" in write_policy.narrow_tools("---\nname: X\n---\nbody\n")


def test_identity_without_the_switch():
    content = _agent("['read', 'edit']")
    assert write_policy.apply(content, "primary-producer", {}) == content
    assert write_policy.apply(content, "primary-producer", {"write_policy": "default"}) == content


def test_orchestrator_keeps_its_tools_and_gets_the_workflow():
    out = write_policy.apply(_agent("['read', 'edit', 'agent']"), "orchestrator", ON)
    assert "tools: ['read', 'edit', 'agent']" in out
    assert "Write Policy: Applying Proposals" in out and "Workflow 13" in out


def test_capability_gaps_route_through_the_orchestrator():
    # Non-orchestrator agents can't write a plan, so they attach a gap note; the orchestrator opens plans.
    agent = write_policy.apply(_agent("['read', 'edit']"), "primary-producer", ON)
    assert "gap note" in agent and "references/mcp-need.reference.md" in agent
    orch = write_policy.apply(_agent("['read', 'edit']"), "orchestrator", ON)
    assert "you open every capability-gap plan" in orch and "references/mcp-needs.csv" in orch


def test_mcp_need_reference_and_register_columns():
    doc = mcp_need.reference_doc()
    assert "Ask in order" in doc and "never needs an MCP server" in doc and "wildcard is never allowed" in doc
    for column in mcp_need.MCP_NEEDS_HEADERS:
        assert f"`{column}`" in doc, column


def test_skill_override_fenced_only_when_the_body_already_is():
    plain = mcp_need.apply_skill_override("# Skill Generation\nThe agent that hit the gap opens that plan.\n")
    assert "AGENTTEAMS:BEGIN" not in plain and "The orchestrator opens every" in plain
    fenced = mcp_need.apply_skill_override("<!-- AGENTTEAMS:BEGIN content v=1 -->\nx\n<!-- AGENTTEAMS:END content -->\n")
    assert "<!-- AGENTTEAMS:BEGIN write_policy v=1 -->" in fenced and fenced.endswith("<!-- AGENTTEAMS:END write_policy -->\n")


def test_unmeasured_need_never_proposes_a_server():
    assert "A server is never proposed without a measurement over a set threshold" in mcp_need.reference_doc()


def test_mcp_needs_stub_only_when_asked(tmp_path):
    assert mcp_need.MCP_NEEDS_CSV not in liaison_logs.init_csv_stubs(tmp_path / "a")
    assert not (tmp_path / "a" / mcp_need.MCP_NEEDS_CSV).exists()
    assert mcp_need.MCP_NEEDS_CSV in liaison_logs.init_csv_stubs(tmp_path / "b", mcp_needs=True)
    header = (tmp_path / "b" / mcp_need.MCP_NEEDS_CSV).read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(mcp_need.MCP_NEEDS_HEADERS)


def test_section_fenced_only_when_the_body_already_is():
    plain = write_policy.apply(_agent("['read', 'edit']"), "primary-producer", ON)
    assert "AGENTTEAMS:BEGIN" not in plain and "Return Proposals" in plain   # wrapped later with the body
    fenced_body = "<!-- AGENTTEAMS:BEGIN content v=1 -->\nx\n<!-- AGENTTEAMS:END content -->\n"
    fenced = write_policy.apply(_agent("['read', 'edit']", fenced_body), "primary-producer", ON)
    assert "<!-- AGENTTEAMS:BEGIN write_policy v=1 -->" in fenced and "<!-- AGENTTEAMS:END write_policy -->" in fenced


def test_goose_scoping_forced_to_grant_and_legacy_refused():
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "a", "name": "A"}], **ON}
    assert analyze.build_manifest(dict(desc), framework="goose")["goose_tool_scoping"] == "grant"
    with pytest.raises(ValueError, match="goose_tool_scoping"):
        analyze.build_manifest({**desc, "goose_tool_scoping": "legacy"}, framework="goose")


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REPO / "build_team.py"), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=600)


def test_switching_on_an_existing_team(tmp_path):
    """--update --merge adds the sections; front matter needs --reconcile-front-matter --reconcile-apply."""
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    brief_path, out = tmp_path / "brief.json", tmp_path / "agents"
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    base = ("--description", str(brief_path), "--project", str(tmp_path), "--framework", "claude",
            "--output", str(out), "--no-scan", "--yes")
    assert _cli(*base, cwd=tmp_path).returncode == 0
    # The MCP-need procedure and register ship only under the switch.
    assert not (out / "references" / "mcp-need.reference.md").exists()
    assert not (out / "references" / "mcp-needs.csv").exists()
    assert "orchestrator-only" not in (out / "references" / "skill-generation.reference.md").read_text(encoding="utf-8")
    brief.update(ON)
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    manifest = analyze.build_manifest(brief, framework="claude")

    proc = _cli(*base, "--update", "--merge", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert "Return Proposals" in (out / "primary-producer.md").read_text(encoding="utf-8")
    assert (out / "references" / "mcp-need.reference.md").exists()
    assert (out / "references" / "mcp-needs.csv").read_text(encoding="utf-8").startswith("id,agent,capability,")
    assert "The orchestrator opens every\ncapability-gap plan" in (
        out / "references" / "skill-generation.reference.md").read_text(encoding="utf-8")
    still_wide = [f for f in run_post_audit(out, manifest, ai_audit=False).agent_refactor_findings
                  if f.code == "AR_WRITE_POLICY" and f.severity == "error"]
    assert still_wide, "front matter is not merged, so the audit must still flag the old tools"

    proc = _cli(*base, "--reconcile-front-matter", "--reconcile-apply", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    left = [(f.file, f.description[:100]) for f in run_post_audit(out, manifest, ai_audit=False).agent_refactor_findings
            if f.code == "AR_WRITE_POLICY"]
    assert not left, left


def test_goose_non_orchestrator_withholds_operator_mcp_and_coordination():
    from agentteams.frameworks.goose import GooseAdapter

    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "a", "name": "A"}], **ON}
    manifest = analyze.build_manifest(desc, framework="goose")
    manifest["host_features"] = ["goose:mcp"]
    manifest["mcp_servers"] = [{"server_id": "lean", "transport": "stdio", "command": "lean-mcp",
                                "trust_tier": "first-party", "scope": ["repo-liaison"],
                                "tools": [{"name": "check", "side_effects": "read"}]}]
    manifest["coordination_write_roots"] = ["../other"]
    agent = _agent("['read', 'search', 'edit']")
    # Positive control: without the switch both are wired, so the assertion below is not vacuous.
    off = {k: v for k, v in manifest.items() if k != "write_policy"}
    control = GooseAdapter().render_agent_file(agent, "repo-liaison", off)
    assert "agentteams_coordination" in control and "lean-mcp" in control
    recipe = GooseAdapter().render_agent_file(write_policy.apply(agent, "repo-liaison", manifest), "repo-liaison",
                                              manifest)
    assert "agentteams_coordination" not in recipe and "lean-mcp" not in recipe


def test_switching_on_an_existing_goose_team_narrows_its_recipes_on_update(tmp_path):
    """--update --merge reconciles each recipe's extensions from the render (#189), so switching the policy on for an
    existing Goose team narrows its grants without a regeneration; the audit finds no AR_WRITE_POLICY error."""
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    off, on = tmp_path / "off.json", tmp_path / "on.json"
    off.write_text(json.dumps(brief), encoding="utf-8")
    brief.update(ON)
    on.write_text(json.dumps(brief), encoding="utf-8")
    procs = {}
    for fw in ("goose",):  # codex: the switch is refused (P4a key custody)
        args = ("--project", str(tmp_path / fw), "--framework", fw, "--output", str(tmp_path / fw / "agents"),
                "--no-scan", "--yes")
        procs[fw] = subprocess.Popen(
            f"{sys.executable} {REPO / 'build_team.py'} --description {off} {' '.join(args)} && "
            f"{sys.executable} {REPO / 'build_team.py'} --description {on} {' '.join(args)} --update --merge",
            shell=True, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for fw, proc in procs.items():
            out, _ = proc.communicate(timeout=600)
            assert proc.returncode == 0, out[-1500:]
            assert "Goose recipe extensions" in out and "extensions: aligned with the template" in out
            errors = [f for f in run_post_audit(tmp_path / fw / "agents", analyze.build_manifest(brief, framework=fw),
                                                ai_audit=False).agent_refactor_findings
                      if f.code == "AR_WRITE_POLICY" and f.severity == "error"]
            assert not errors, f"{fw}: --update must narrow recipe grants: {[e.description for e in errors][:3]}"
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
