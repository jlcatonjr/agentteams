"""A render never writes a path a frozen surface owns (baseAgent D63: a goose --update rewrote the AGENTS.md a Codex
freeze owns, because renders ignored ``frozen_surfaces``)."""

from __future__ import annotations

import json
from pathlib import Path

import build_team
from agentteams.cli import security_gate
from agentteams.cli.frozen_render import drop_frozen, frozen_refusal

BRIEF = Path(__file__).resolve().parent.parent / "examples" / "software-project" / "brief.json"


def _brief(tmp_path: Path, frozen: list[str] | None) -> Path:
    data = json.loads(BRIEF.read_text(encoding="utf-8"))
    if frozen is not None:
        data["frozen_surfaces"] = frozen
    out = tmp_path / "brief.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def _args(brief: Path, project: Path, framework: str) -> list[str]:
    return ["--description", str(brief), "--framework", framework, "--output", str(project), "--yes", "--no-scan",
            "--security-offline"]


def test_drop_frozen_drops_the_shared_agents_md_and_keeps_the_rest(tmp_path: Path, capsys) -> None:
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    rendered = [("../../AGENTS.md", "a"), ("orchestrator.yaml", "b"), ("../../.goosehints", "c")]
    kept = drop_frozen({"frozen_surfaces": ["codex"], "existing_project_path": str(tmp_path)}, rendered, recipes)
    assert kept == [("orchestrator.yaml", "b"), ("../../.goosehints", "c")]
    assert "AGENTS.md: skipped (a frozen surface owns it: codex)" in capsys.readouterr().out


def test_drop_frozen_without_a_freeze_keeps_everything(tmp_path: Path) -> None:
    rendered = [("../../AGENTS.md", "a")]
    assert drop_frozen({}, rendered, tmp_path / ".goose" / "recipes") == rendered


def test_a_path_outside_the_project_is_left_to_the_emit_guards(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    out = project / ".goose" / "recipes"
    out.mkdir(parents=True)
    rendered = [("../../../AGENTS.md", "a")]
    manifest = {"frozen_surfaces": ["codex"], "existing_project_path": str(project)}
    assert drop_frozen(manifest, rendered, out) == rendered


def test_a_frozen_framework_and_an_unknown_surface_are_refused(tmp_path: Path) -> None:
    m = {"frozen_surfaces": ["codex"], "existing_project_path": str(tmp_path)}
    assert "'codex' surface is frozen" in frozen_refusal(m, "codex", tmp_path / ".codex" / "agents")
    assert "unknown surface" in frozen_refusal({"frozen_surfaces": ["nope"]}, "goose", tmp_path / ".goose" / "recipes")
    assert frozen_refusal(m, "goose", tmp_path / ".goose" / "recipes") is None


def test_a_render_into_a_directory_another_frozen_surface_owns_is_refused(tmp_path: Path) -> None:
    # copilot-cli and copilot-vscode share .github/agents/: the build log and backups would land in the frozen one.
    m = {"frozen_surfaces": ["copilot-vscode"], "existing_project_path": str(tmp_path)}
    assert "'copilot-vscode' surface is frozen" in frozen_refusal(m, "copilot-cli", tmp_path / ".github" / "agents")
    assert frozen_refusal(m, "claude", tmp_path / ".claude" / "agents") is None


def test_without_a_recorded_root_the_filter_uses_the_emit_boundary(tmp_path: Path) -> None:
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    assert drop_frozen({"frozen_surfaces": ["codex"]}, [("../../AGENTS.md", "a")], recipes) == []


def test_a_symlink_into_a_frozen_path_is_followed(tmp_path: Path) -> None:
    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    (tmp_path / ".codex").mkdir()
    (recipes / "alias").symlink_to(tmp_path / ".codex")
    m = {"frozen_surfaces": ["codex"], "existing_project_path": str(tmp_path)}
    assert drop_frozen(m, [("alias/x.toml", "a"), ("orchestrator.yaml", "b")], recipes) == [("orchestrator.yaml", "b")]


def test_goose_update_leaves_a_codex_frozen_agents_md_byte_identical(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    project = tmp_path / "proj"
    project.mkdir()
    assert build_team.main(_args(_brief(tmp_path, None), project, "goose")) == 0
    agents_md = project / "AGENTS.md"
    before = agents_md.read_bytes()
    edited = before.replace(b"**Name:** WebAppBackend", b"**Name:** FROZEN-EDIT", 1)  # inside a fence
    assert edited != before
    # Control: unfrozen, --update --merge restores the fenced text, so it is the freeze that keeps the file.
    agents_md.write_bytes(edited)
    assert build_team.main([*_args(_brief(tmp_path, None), project, "goose"), "--update", "--merge"]) == 0
    assert b"FROZEN-EDIT" not in agents_md.read_bytes()
    agents_md.write_bytes(edited)
    capsys.readouterr()
    assert build_team.main([*_args(_brief(tmp_path, ["codex"]), project, "goose"), "--update", "--merge"]) == 0
    assert agents_md.read_bytes() == edited
    assert "AGENTS.md: skipped (a frozen surface owns it: codex)" in capsys.readouterr().out
    assert (project / ".goose" / "recipes" / "orchestrator.yaml").is_file()


def test_rendering_a_frozen_framework_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(security_gate, "_assert_security_intelligence_fresh", lambda *_a, **_k: None)
    project = tmp_path / "proj"
    project.mkdir()
    assert build_team.main(_args(_brief(tmp_path, ["codex"]), project, "codex")) == 1
    assert "'codex' surface is frozen" in capsys.readouterr().err
    assert not (project / ".codex").exists() and not (project / "AGENTS.md").exists()


def test_check_leaves_frozen_files_out_of_the_drift_verdict(tmp_path: Path, capsys) -> None:
    from types import SimpleNamespace

    from agentteams.cli.frozen_render import exempt_frozen

    recipes = tmp_path / ".goose" / "recipes"
    recipes.mkdir(parents=True)
    m = {"frozen_surfaces": ["codex"], "existing_project_path": str(tmp_path)}
    dreport = SimpleNamespace(changed_templates=[{"output_file": "../../AGENTS.md"}, {"output_file": "security.yaml"}])
    sdreport = SimpleNamespace(drifted_files=[{"path": "../../AGENTS.md"}], added_files=[{"path": "new.yaml"}])
    exempt_frozen(m, recipes, dreport, sdreport)
    assert dreport.changed_templates == [{"output_file": "security.yaml"}]
    assert sdreport.drifted_files == [] and sdreport.added_files == [{"path": "new.yaml"}]
    assert "../../AGENTS.md: not checked (a frozen surface owns it)" in capsys.readouterr().out
