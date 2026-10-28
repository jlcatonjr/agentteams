"""Codex as an ``--interop-from`` target under ``write_policy: "orchestrator-only"`` (mathAgents handoff item 2).

Codex ignores a spawned agent's ``sandbox_mode`` and tool list, so the switch holds on Codex only through agentteams'
launcher and the role gate it runs. Interop emits neither: a Codex import is admitted only into a native Codex team
already rendered under the switch, relies on its gate, renders every imported agent read-only, and never replaces a
native agent or the native ``AGENTS.md``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams import write_policy
from agentteams.audit_agent_contract import _check_write_policy
from agentteams.interop import run_interop
from agentteams.interop_write_policy import manifest_fields

pytestmark = pytest.mark.skipif(not (sys.platform.startswith("linux") or sys.platform.startswith("darwin")),
                                reason="the Codex launcher is emitted only on Linux and macOS")

REPO = Path(__file__).resolve().parents[1]
GRANT = {"tools": ["read_file_hashed", "write_file"]}
# mathAgents' shape: grants (honoured on claude) for bespoke agents the native roster doesn't have.
BRIEF = {"write_policy": "orchestrator-only", "privilege_profile": "confined",
         "write_policy_frameworks": ["claude", "codex"], "mcp_grants": {"proof-strategist": GRANT}}
BESPOKE = {"proof-strategist": "['read', 'edit', 'execute', 'search']", "tactic-planner": "['read', 'search']"}


def _render_native(project: Path) -> None:
    brief = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text())
    brief.update(BRIEF)
    (project / "brief.json").write_text(json.dumps(brief))
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--description", str(project / "brief.json"),
                           "--project", str(project), "--framework", "codex", "--output",
                           str(project / ".codex" / "agents"), "--no-scan", "--yes"],
                          cwd=project, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-1500:]


@pytest.fixture(scope="module")
def native(tmp_path_factory) -> Path:
    project = tmp_path_factory.mktemp("codex") / "project"
    project.mkdir()
    _render_native(project)
    return project


@pytest.fixture
def project(native, tmp_path) -> Path:
    copy = tmp_path / "project"
    shutil.copytree(native, copy, symlinks=True)
    return copy


def _source(tmp_path: Path, agents: dict[str, str] = BESPOKE) -> Path:
    src = tmp_path / "src" / ".github" / "agents"
    src.mkdir(parents=True)
    manifest = {"write_policy": "orchestrator-only", "mcp_grants": BRIEF["mcp_grants"]}
    for slug, tools in agents.items():
        content = (f"---\nname: {slug}\ndescription: \"The {slug} agent\"\ntools: {tools}\n---\n\n"
                   f"# {slug}\n\nResearches and returns text.\n")
        (src / f"{slug}.agent.md").write_text(write_policy.apply(content, slug, manifest))
    (src.parent / "copilot-instructions.md").write_text("# Bespoke instructions\n")
    return src


def _import(src: Path, project: Path, **kw):
    return run_interop(src, "codex", project / ".codex" / "agents", source_framework="copilot-vscode",
                       write_policy_fields=manifest_fields(BRIEF, "codex"), overwrite=True, **kw)


def test_bespoke_agents_import_read_only_beside_the_native_team(project, tmp_path):
    agents_md = (project / "AGENTS.md").read_bytes()
    orchestrator = (project / ".codex" / "agents" / "orchestrator.toml").read_bytes()
    result = _import(_source(tmp_path), project)
    assert result.success, result.errors
    target = project / ".codex" / "agents"
    for slug in BESPOKE:
        text = (target / f"{slug}.toml").read_text()
        findings = _check_write_policy({f"{slug}.toml": text}, agent_ext=".toml", framework="codex", enabled=True)
        assert not [f for f in findings if f.severity == "error"], findings
        assert 'sandbox_mode = "read-only"' in text
        assert "role gate" in text                       # the reading instructions match the gate that enforces them
    assert (project / "AGENTS.md").read_bytes() == agents_md          # the native instructions file is kept
    assert (target / "orchestrator.toml").read_bytes() == orchestrator


def test_a_bespoke_agent_named_like_a_native_one_is_refused(project, tmp_path):
    before = (project / ".codex" / "agents" / "orchestrator.toml").read_bytes()
    result = _import(_source(tmp_path, {**BESPOKE, "orchestrator": "['read', 'edit', 'execute', 'agent']"}), project)
    assert not result.success
    assert any("belongs to the native Codex team" in e for e in result.errors)
    assert (project / ".codex" / "agents" / "orchestrator.toml").read_bytes() == before


@pytest.mark.parametrize("break_it,needle", [
    (lambda p: (p / ".agentteams/bin/codex-role-gate.py").write_text("# tampered\n"), "pinned hash"),
    (lambda p: (p / ".codex/hooks.json").unlink(), "hooks.json"),
    (lambda p: (p / ".codex/confined-run.example.sh").unlink(), "launcher"),
    (lambda p: (p / ".codex/agents/references/build-log.json").unlink(), "build-log.json"),
])
def test_a_missing_or_altered_native_gate_refuses_before_writing(project, tmp_path, break_it, needle):
    break_it(project)
    before = sorted(p.name for p in (project / ".codex" / "agents").glob("*.toml"))
    with pytest.raises(ValueError, match=needle):
        _import(_source(tmp_path), project)
    assert sorted(p.name for p in (project / ".codex" / "agents").glob("*.toml")) == before


def test_a_codex_import_outside_the_canonical_dir_is_refused(project, tmp_path):
    with pytest.raises(ValueError, match=".codex/agents"):
        run_interop(_source(tmp_path), "codex", tmp_path / "elsewhere", source_framework="copilot-vscode",
                    write_policy_fields=manifest_fields(BRIEF, "codex"), overwrite=True)


def test_grants_are_kept_as_natively_and_reported_not_honoured(project, tmp_path):
    fields = manifest_fields(BRIEF, "codex")
    assert fields == {"write_policy": "orchestrator-only", "mcp_grants": BRIEF["mcp_grants"],
                      "privilege_profile": "confined"}
    result = _import(_source(tmp_path), project)
    assert result.success, result.errors
    assert any("mcp_grants for proof-strategist are not honoured on codex" in n for n in result.notices)
    assert any("--adopt-orphans" in n for n in result.notices)
    text = (project / ".codex" / "agents" / "proof-strategist.toml").read_text()
    assert 'sandbox_mode = "read-only"' in text and "mcp_servers" not in text


def test_a_differently_cased_native_name_is_refused(project, tmp_path):
    before = (project / ".codex" / "agents" / "orchestrator.toml").read_bytes()
    result = _import(_source(tmp_path, {**BESPOKE, "Orchestrator": "['read', 'search']"}), project)
    assert any("belongs to the native Codex team" in e for e in result.errors)
    assert (project / ".codex" / "agents" / "orchestrator.toml").read_bytes() == before


def test_a_missing_agents_md_is_not_filled_by_the_import(project, tmp_path):
    (project / "AGENTS.md").unlink()
    result = _import(_source(tmp_path), project)
    assert result.success, result.errors
    assert not (project / "AGENTS.md").exists()


def test_a_build_log_without_a_roster_is_refused(project, tmp_path):
    log = project / ".codex" / "agents" / "references" / "build-log.json"
    data = json.loads(log.read_text())
    data["agent_slug_list"] = []
    log.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="agent_slug_list"):
        _import(_source(tmp_path), project)
