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

from agentteams import analyze, write_policy
from agentteams.audit import run_post_audit

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
ON = {"write_policy": "orchestrator-only"}


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
    brief.update(ON)
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    manifest = analyze.build_manifest(brief, framework="claude")

    proc = _cli(*base, "--update", "--merge", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert "Return Proposals" in (out / "primary-producer.md").read_text(encoding="utf-8")
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


def test_switching_on_an_existing_goose_team_is_flagged_until_regenerated(tmp_path):
    """Reconcile reads only markdown, so recipe/TOML grants stay wide after --update; the audit must say so."""
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
            assert "regenerate them" in out
            errors = [f for f in run_post_audit(tmp_path / fw / "agents", analyze.build_manifest(brief, framework=fw),
                                                ai_audit=False).agent_refactor_findings
                      if f.code == "AR_WRITE_POLICY" and f.severity == "error"]
            assert errors, f"{fw}: wide grants after --update must stay visible to the audit"
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                proc.kill()
