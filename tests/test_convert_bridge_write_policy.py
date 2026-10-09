"""``--convert-from`` and bridge subagent stubs refuse a team under ``write_policy: "orchestrator-only"``.

Neither path narrows tools, adds the write-policy section or wires the runner: a converted agent keeps its source
tools, and a bridge stub copies them. Under the switch they would write agents that can write, so both refuse and
point to ``--interop-from --description`` (which applies the policy, #174). The switch is read from the target
team's build-log (native generation records ``write_policy`` there) or from ``--description``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.bridge import run_bridge
from agentteams.convert import convert_team
from agentteams.interop_write_policy import refuse_bridge_stubs, target_under_switch

REPO = Path(__file__).resolve().parents[1]
BRIEF = {"write_policy": "orchestrator-only", "privilege_profile": "confined"}
AGENTS_DIRS = {"claude": (".claude", "agents"), "goose": (".goose", "recipes")}


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src" / ".github" / "agents"
    src.mkdir(parents=True)
    for slug in ("orchestrator", "planner"):
        (src / f"{slug}.agent.md").write_text(
            f"---\nname: {slug}\ndescription: \"{slug}\"\ntools: ['read', 'edit', 'execute']\n---\n\n# {slug}\n\nWork.\n")
    return src


def _switched(project: Path, framework: str, value: str | None = "orchestrator-only") -> Path:
    agents = project.joinpath(*AGENTS_DIRS[framework])
    (agents / "references").mkdir(parents=True)
    log = {"schema_version": "1.5", "framework": framework, **({"write_policy": value} if value else {})}
    (agents / "references" / "build-log.json").write_text(json.dumps(log))
    return agents


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def test_the_build_log_records_the_switch(tmp_path):
    assert target_under_switch(_switched(tmp_path / "on", "claude"))
    assert not target_under_switch(_switched(tmp_path / "off", "claude", None))
    assert not target_under_switch(tmp_path / "missing")
    bad = tmp_path / "bad" / "references"
    bad.mkdir(parents=True)
    (bad / "build-log.json").write_text("{not json")
    assert target_under_switch(bad.parent), "a present but malformed build-log fails closed (@security cond. 1)"
    (bad / "build-log.json").write_text("[1, 2]")
    assert target_under_switch(bad.parent)


def test_a_codex_refusal_points_to_native_regeneration(tmp_path):
    """interop refuses codex under the switch, so the message must not point there (@security cond. 3)."""
    agents = tmp_path / "project" / ".codex" / "agents"
    (agents / "references").mkdir(parents=True)
    (agents / "references" / "build-log.json").write_text(json.dumps({"write_policy": "orchestrator-only"}))
    with pytest.raises(ValueError, match="Regenerate the codex team natively"):
        convert_team(_source(tmp_path), agents, "codex", overwrite=True)


def test_the_app_module_entry_point_reaches_the_refusal(tmp_path):
    """`python -m agentteams.cli.app` defines every helper before main() runs (@security cond. 2)."""
    src = _source(tmp_path)
    agents = _switched(tmp_path / "project", "claude")
    proc = subprocess.run([sys.executable, "-m", "agentteams.cli.app", "--convert-from", str(src), "--framework",
                           "claude", "--output", str(agents), "--description", str(tmp_path / "missing.json")],
                          capture_output=True, text=True, timeout=120, cwd=str(REPO))
    assert "NameError" not in proc.stderr, proc.stderr[-1500:]
    assert proc.returncode == 1


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_convert_refuses_a_team_under_the_switch(tmp_path, framework):
    agents = _switched(tmp_path / "project", framework)
    before = _files(tmp_path / "project")
    with pytest.raises(ValueError, match="--interop-from .* --description"):
        convert_team(_source(tmp_path), agents, framework, overwrite=True)
    assert _files(tmp_path / "project") == before


def test_convert_refuses_when_the_brief_turns_the_switch_on(tmp_path):
    target = tmp_path / "project" / ".claude" / "agents"
    with pytest.raises(ValueError, match="orchestrator-only"):
        convert_team(_source(tmp_path), target, "claude", description=BRIEF)
    assert not target.exists()


def test_convert_is_unchanged_without_the_switch(tmp_path):
    src = _source(tmp_path)
    plain = convert_team(src, tmp_path / "a" / ".claude" / "agents", "claude")
    briefed = convert_team(src, tmp_path / "b" / ".claude" / "agents", "claude", description={"project_name": "x"})
    assert plain.success and briefed.success
    read = lambda d: {p.name: p.read_text() for p in d.glob("*.md")}  # noqa: E731
    assert read(tmp_path / "a" / ".claude" / "agents") == read(tmp_path / "b" / ".claude" / "agents")


def test_a_scoped_out_framework_is_not_refused(tmp_path):
    scoped = {**BRIEF, "write_policy_frameworks": ["goose"]}
    result = convert_team(_source(tmp_path), tmp_path / "p" / ".claude" / "agents", "claude", description=scoped)
    assert result.success


@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_bridge_subagent_stubs_refuse_a_team_under_the_switch(tmp_path, framework):
    project = tmp_path / "project"
    _switched(project, framework)
    before = _files(project)
    feature = ("bridge:copilot-vscode-to-claude:subagents" if framework == "claude"
               else "bridge:copilot-vscode-to-goose:subagents")
    with pytest.raises(ValueError, match="subagent stubs"):
        run_bridge(source_dir=_source(tmp_path), target_framework=framework, output_root=project,
                   source_framework="copilot-vscode", host_features=[feature])
    assert _files(project) == before


def test_bridge_without_stubs_is_unaffected(tmp_path):
    project = tmp_path / "project"
    _switched(project, "claude")
    result = run_bridge(source_dir=_source(tmp_path), target_framework="claude", output_root=project,
                        source_framework="copilot-vscode", host_features=[])
    assert not result.errors


def test_bridge_check_only_is_unaffected(tmp_path):
    project = tmp_path / "project"
    _switched(project, "claude")
    run_bridge(source_dir=_source(tmp_path), target_framework="claude", output_root=project,
               source_framework="copilot-vscode", check_only=True,
               host_features=["bridge:copilot-vscode-to-claude:subagents"])


def test_bridge_stubs_refuse_when_the_brief_turns_the_switch_on(tmp_path):
    with pytest.raises(ValueError, match="orchestrator-only"):
        refuse_bridge_stubs("copilot-vscode", "claude", tmp_path, ["bridge:copilot-vscode-to-claude:subagents"],
                            BRIEF)


def test_the_cli_refuses_and_names_the_interop_command(tmp_path):
    src = _source(tmp_path)
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps({**json.loads((REPO / "examples/software-project/brief.json").read_text()), **BRIEF}))
    proc = subprocess.run([sys.executable, str(REPO / "build_team.py"), "--convert-from", str(src),
                           "--framework", "claude", "--output", str(tmp_path / "p" / ".claude" / "agents"),
                           "--description", str(brief), "--dry-run"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stdout[-1500:] + proc.stderr[-1500:]
    assert "--interop-from" in proc.stdout + proc.stderr
