"""--retire-bridge: the enumerated removal set, the clearance it needs, and what it never touches.

Each project is a real ``copilot-vscode -> claude`` bridge (``bridge.run_bridge`` with the subagent and
todo-projection features), plus hand-placed files the command must leave alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import bridge_retire as br
from agentteams.bridge import run_bridge

PAIR = "copilot-vscode-to-claude"


def _src_agent(src: Path, slug: str, tools: str = "['read', 'edit']") -> None:
    src.mkdir(parents=True, exist_ok=True)
    (src / f"{slug}.agent.md").write_text(
        f"---\nname: {slug}\ndescription: {slug} agent\ntools: {tools}\nuser-invocable: false\n---\n\n# {slug}\n",
        encoding="utf-8")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    src = root / ".github" / "agents"
    for slug in ("orchestrator", "cleanup", "navigator"):
        _src_agent(src, slug)
    (src.parent / "copilot-instructions.md").write_text("# Instructions\n", encoding="utf-8")
    result = run_bridge(source_dir=src, source_framework="copilot-vscode", target_framework="claude",
                        output_root=root, overwrite=True,
                        host_features=[f"bridge:{PAIR}:subagents", f"bridge:{PAIR}:todo-projection"])
    assert not result.errors
    # Hand-placed content the command must keep.
    (root / "references" / "bridges" / PAIR / "operator-notes.md").write_text("mine\n", encoding="utf-8")
    (root / ".claude" / "agents" / "bespoke.md").write_text("---\nname: bespoke\n---\nmine\n", encoding="utf-8")
    (root / ".claude" / "agents" / "other-pair.md").write_text(
        "---\nname: other\nbridge: goose-to-claude\n---\n", encoding="utf-8")
    (src.parent / "agents" / "references").mkdir(exist_ok=True)
    return root


def _team(project: Path, *rows: str) -> Path:
    team = project / ".github" / "agents"
    log = team / "references" / "security-decisions.log.csv"
    log.write_text("timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
                   + "".join(r + "\n" for r in rows), encoding="utf-8")
    return team


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_plan_enumerates_exactly_what_the_bridge_generated(project):
    plan = br.build_plan(project, PAIR)
    removed = {rel for rel, _ in plan.removals}
    assert f"references/bridges/{PAIR}/bridge-manifest.json" in removed
    assert {".claude/agents/cleanup.md", ".claude/agents/navigator.md",
            ".claude/skills/todo-from-plan/SKILL.md"} <= removed
    assert not any(r.endswith(("bespoke.md", "other-pair.md", "operator-notes.md", "CLAUDE.md")) for r in removed)
    assert not any("/recall/" in r or "code-recall" in r for r in removed)  # unmarked skills stay
    assert plan.kept == [f"references/bridges/{PAIR}/operator-notes.md"]
    assert any(r.startswith("CLAUDE.md") for r in plan.reported)


def test_dry_run_changes_nothing(project):
    team = _team(project)
    before = _snapshot(project)
    code, lines = br.run_retire(project, PAIR, team_dir=team, apply=False)
    assert code == br.EXIT_OK and "Dry run" in lines[-1]
    assert _snapshot(project) == before


def test_apply_without_clearance_is_unauthorized(project):
    team = _team(project)
    before = _snapshot(project)
    code, lines = br.run_retire(project, PAIR, team_dir=team, apply=True)
    assert code == br.EXIT_UNAUTHORIZED, lines
    assert _snapshot(project) == before


def test_bare_family_pass_does_not_authorize(project):
    team = _team(project, "2026-10-27,security,retire-bridge,PASS,,")
    code, _ = br.run_retire(project, PAIR, team_dir=team, apply=True)
    assert code == br.EXIT_UNAUTHORIZED


def test_clearance_removes_exactly_the_set_backs_up_and_is_consumed(project):
    plan = br.build_plan(project, PAIR)
    team = _team(project, f"2026-10-27,security,{plan.action},PASS,,")
    code, lines = br.run_retire(project, PAIR, team_dir=team, apply=True)
    assert code == br.EXIT_OK, lines
    for rel, _ in plan.removals:
        assert not (project / rel).exists(), rel
    assert not (project / ".claude" / "skills" / "todo-from-plan").exists()  # emptied skill dir goes
    for kept in ("references/bridges/" + PAIR + "/operator-notes.md", ".claude/agents/bespoke.md",
                 ".claude/agents/other-pair.md", "CLAUDE.md", ".claude/skills/recall/SKILL.md"):
        assert (project / kept).exists(), kept
    backups = list((project / ".agentteams-backups").rglob("cleanup.md.agentteams-bak"))
    assert backups, "removed files are backed up first"
    assert "--materialize-native" in lines[-1]
    with pytest.raises(br.RetireError, match="nothing to retire"):
        br.run_retire(project, PAIR, team_dir=team, apply=True)


def test_pair_dir_goes_when_only_known_artifacts_were_in_it(project):
    (project / "references" / "bridges" / PAIR / "operator-notes.md").unlink()
    plan = br.build_plan(project, PAIR)
    team = _team(project, f"2026-10-27,security,{plan.action},PASS,,")
    assert br.run_retire(project, PAIR, team_dir=team, apply=True)[0] == br.EXIT_OK
    assert not (project / "references" / "bridges" / PAIR).exists()


def test_a_file_edited_after_the_dry_run_voids_the_clearance(project):
    plan = br.build_plan(project, PAIR)
    team = _team(project, f"2026-10-27,security,{plan.action},PASS,,")
    stub = project / ".claude" / "agents" / "cleanup.md"
    stub.write_text(stub.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    code, _ = br.run_retire(project, PAIR, team_dir=team, apply=True)
    assert code == br.EXIT_UNAUTHORIZED
    assert stub.exists()


@pytest.mark.parametrize("halt", ["retire-bridge", f"retire-bridge:{PAIR}"])
def test_halt_blocks(project, halt):
    plan = br.build_plan(project, PAIR)
    team = _team(project, f"2026-10-27,security,{plan.action},PASS,,", f"2026-10-27,security,{halt},HALT,,")
    before = _snapshot(project)
    code, lines = br.run_retire(project, PAIR, team_dir=team, apply=True)
    assert code == br.EXIT_FAIL and "HALT" in lines[-1]
    assert _snapshot(project) == before


def test_retracting_another_pair_does_not_clear_this_pairs_halt(project):
    """A retraction of any retire-bridge:... id clears a family-level HALT; the pair-level check holds."""
    plan = br.build_plan(project, PAIR)
    team = _team(project, f"2026-10-27,security,{plan.action},PASS,,",
                 f"2026-10-27,security,retire-bridge:{PAIR},HALT,,",
                 "2026-10-27,security,retire-bridge:goose-to-claude,HALT-RETRACTED,resolved,verified")
    code, lines = br.run_retire(project, PAIR, team_dir=team, apply=True)
    assert code == br.EXIT_FAIL and "HALT" in lines[-1]


def test_a_symlinked_skills_dir_cannot_reach_another_tree(project, tmp_path):
    """--project at a scratch clone whose .claude/skills links into the real project removes nothing there."""
    real = project / ".claude" / "skills"
    clone = tmp_path / "clone"
    (clone / ".claude").mkdir(parents=True)
    (clone / ".claude" / "skills").symlink_to(real, target_is_directory=True)
    with pytest.raises(br.RetireError, match="nothing to retire"):
        br.build_plan(clone, PAIR)
    assert (real / "todo-from-plan" / "SKILL.md").exists()


def test_a_symlinked_pair_dir_is_kept_not_followed(project, tmp_path):
    pair_dir = project / "references" / "bridges" / PAIR
    elsewhere = tmp_path / "elsewhere"
    pair_dir.rename(elsewhere)
    pair_dir.symlink_to(elsewhere, target_is_directory=True)
    plan = br.build_plan(project, PAIR)
    assert not any(rel.startswith("references/") for rel, _ in plan.removals)
    assert f"references/bridges/{PAIR}/bridge-manifest.json" in plan.kept


def test_a_file_swapped_after_the_gate_stops_the_run(project, monkeypatch):
    from agentteams import backup

    plan = br.build_plan(project, PAIR)
    team = _team(project, f"2026-10-27,security,{plan.action},PASS,,")
    last = project / plan.removals[-1][0]
    real_backup = backup.backup_output_dir

    def backup_then_tamper(*args, **kwargs):
        result = real_backup(*args, **kwargs)
        last.write_text("swapped\n", encoding="utf-8")
        return result

    monkeypatch.setattr(backup, "backup_output_dir", backup_then_tamper)
    code, lines = br.run_retire(project, PAIR, team_dir=team, apply=True)
    assert code == br.EXIT_FAIL and "changed or moved" in lines[-1]
    assert last.read_text(encoding="utf-8") == "swapped\n"


def test_entry_file_bridge_regions_are_reported(project):
    plan = br.build_plan(project, PAIR)
    claude_md = next(r for r in plan.reported if r.startswith("CLAUDE.md"))
    assert "AGENTTEAMS-BRIDGE regions to remove by hand: claude-bridge-entry" in claude_md
    assert any(r.startswith(".claude/skills/recall/SKILL.md") for r in plan.reported)


def test_bridge_marker_reads_only_front_matter(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("---\nname: a\nbridge: 'copilot-vscode-to-claude'\n---\n", encoding="utf-8")
    assert br.bridge_marker(f) == PAIR
    f.write_text("---\nname: a\nbridge:\n---\n", encoding="utf-8")
    assert br.bridge_marker(f) == ""
    f.write_text("---\nname: a\n---\nbridge: copilot-vscode-to-claude\n", encoding="utf-8")
    assert br.bridge_marker(f) is None
    assert br.bridge_marker(tmp_path / "missing.md") is None


def test_unsupported_pair_and_nothing_to_retire_are_refused(project, tmp_path):
    with pytest.raises(br.RetireError, match="supports"):
        br.build_plan(project, "copilot-vscode-to-goose")
    with pytest.raises(br.RetireError, match="nothing to retire"):
        br.build_plan(tmp_path / "empty", PAIR)


def _main(argv: list[str]) -> int:
    from agentteams.cli.app import main

    return main(argv)


def test_cli_dry_run_and_standalone_check(project, capsys):
    _team(project)
    assert _main(["--retire-bridge", PAIR, "--project", str(project), "--team-dir",
                  str(project / ".github" / "agents")]) == 0
    assert "Dry run" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        _main(["--retire-bridge", PAIR, "--update"])
    assert exc.value.code == 2 and "standalone" in capsys.readouterr().err


def test_cli_apply_is_accepted_with_retire_bridge(project, capsys):
    _team(project)
    rc = _main(["--retire-bridge", PAIR, "--apply", "--project", str(project), "--team-dir",
                str(project / ".github" / "agents")])
    assert rc == br.EXIT_UNAUTHORIZED
    assert "no usable clearance" in capsys.readouterr().out
