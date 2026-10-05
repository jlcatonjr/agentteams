"""Pinned-sync projection fidelity (baseAgent handoff item 3) and the sync-init incident of
2026-09-29 (``--output`` silently ignored, no backup).

* the pin framework's own files are byte-identical after ``sync_init`` — bespoke front matter
  (``runCommands`` in ``tools:``, no ``user-invocable``) survives;
* an existing instruction file keeps its USER-EDITABLE regions (fence-merge, not replace);
* every projected framework is backed up first;
* ``--sync-init``/``--sync`` refuse ``--output``;
* a backup of a file two levels above the agents dir restores to the right place.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agentteams.backup import backup_output_dir, restore_backup
from agentteams.cli.sync_switch import run_sync_cli
from agentteams.multi_sync import sync_init

_BESPOKE = (
    "---\n"
    "name: Wasm Expert\n"
    'description: "Bespoke adopted agent"\n'
    "tools: ['read', 'search', 'edit', 'runCommands']\n"
    'model: ["auto"]\n'
    "handoffs:\n"
    "  - label: Verify\n"
    "    agent: orchestrator\n"
    '    prompt: "Check it."\n'
    "    send: false\n"
    "---\n\n"
    "# Wasm Expert\n\nVerbatim bespoke body.\n"
)
_ORCH = (
    "---\n"
    "name: Orchestrator\n"
    'description: "Routes work"\n'
    "tools: ['read', 'edit', 'agent']\n"
    "user-invocable: true\n"
    "---\n\n"
    "# Orchestrator\n\nRoutes.\n"
)
_INSTR = (
    "# Team\n\n"
    "<!-- AGENTTEAMS:BEGIN project_overview v=1 -->\n## Project Overview\n\nPIN OVERVIEW\n"
    "<!-- AGENTTEAMS:END project_overview -->\n"
)
_CLAUDE_INSTR = (
    "# Team\n\n"
    "<!-- AGENTTEAMS:BEGIN project_overview v=1 -->\n## Project Overview\n\nSTALE OVERVIEW\n"
    "<!-- AGENTTEAMS:END project_overview -->\n\n"
    "## Project-Specific Rules\n\n- KEEP_THIS_CLAUDE_ONLY_RULE\n"
)


def _project(root: Path) -> None:
    gh = root / ".github" / "agents"
    gh.mkdir(parents=True)
    (gh / "wasm-wat-expert.agent.md").write_text(_BESPOKE, encoding="utf-8")
    (gh / "orchestrator.agent.md").write_text(_ORCH, encoding="utf-8")
    (root / ".github" / "copilot-instructions.md").write_text(_INSTR, encoding="utf-8")
    (root / ".claude" / "agents").mkdir(parents=True)
    (root / ".claude" / "CLAUDE.md").write_text(_CLAUDE_INSTR, encoding="utf-8")


def test_sync_init_leaves_pin_files_byte_identical(tmp_path: Path) -> None:
    _project(tmp_path)
    gh = tmp_path / ".github" / "agents"
    before = {p.name: p.read_bytes() for p in gh.glob("*.agent.md")}
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    assert {p.name: p.read_bytes() for p in gh.glob("*.agent.md")} == before
    assert (tmp_path / ".github" / "copilot-instructions.md").read_text(encoding="utf-8") == _INSTR


def test_sync_init_merges_instruction_file_keeping_user_editable(tmp_path: Path) -> None:
    _project(tmp_path)
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    claude_md = (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "KEEP_THIS_CLAUDE_ONLY_RULE" in claude_md  # USER-EDITABLE kept
    assert "PIN OVERVIEW" in claude_md and "STALE OVERVIEW" not in claude_md  # pin wins fences
    assert (tmp_path / ".claude" / "agents" / "wasm-wat-expert.md").is_file()  # projected


def test_sync_init_backs_up_before_projecting(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / ".claude" / "agents" / "orchestrator.md").write_text("# hand-made\n", encoding="utf-8")
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    backups = list((tmp_path / ".claude" / "agents" / ".agentteams-backups").glob("*/orchestrator.md"))
    assert backups and backups[0].read_text(encoding="utf-8") == "# hand-made\n"
    ext = list((tmp_path / ".claude" / "agents" / ".agentteams-backups").glob("*/__external__/CLAUDE.md"))
    assert ext and "KEEP_THIS_CLAUDE_ONLY_RULE" in ext[0].read_text(encoding="utf-8")


def test_sync_cli_refuses_output(tmp_path: Path, capsys) -> None:
    ns = argparse.Namespace(sync_init=True, sync=False, pin="copilot-vscode", sync_frameworks=None,
                            output=str(tmp_path), project=None, dry_run=False)
    assert run_sync_cli(ns) == 2
    assert "--project" in capsys.readouterr().err
    assert not (tmp_path / ".agentteams").exists()


def test_sync_cli_refuses_missing_project(tmp_path: Path) -> None:
    ns = argparse.Namespace(sync_init=True, sync=False, pin="copilot-vscode", sync_frameworks=None,
                            output=None, project=str(tmp_path / "nope"), dry_run=False)
    assert run_sync_cli(ns) == 2


def test_backup_restores_depth_two_file_to_its_place(tmp_path: Path) -> None:
    """codex/goose teams live two levels deep; their repo-root AGENTS.md must restore to the root."""
    agents = tmp_path / ".codex" / "agents"
    agents.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("original\n", encoding="utf-8")
    res = backup_output_dir(agents, files_to_backup=["../../AGENTS.md"])
    (tmp_path / "AGENTS.md").write_text("changed\n", encoding="utf-8")
    restore_backup(res.backup_path, agents)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "original\n"
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()


# --- review findings, 2026-09-29 (@security C1/C4, @adversarial #1/#2/#5) ---------------------

def test_hand_widened_grant_in_a_target_is_reset_by_the_pin(tmp_path: Path) -> None:
    """C-3: a tampered target file that widens its tools must not survive re-projection."""
    _project(tmp_path)
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    target = tmp_path / ".claude" / "agents" / "orchestrator.md"
    text = target.read_text(encoding="utf-8")
    tampered_line = next(l for l in text.splitlines() if l.startswith("tools:"))
    target.write_text(text.replace(tampered_line, tampered_line + ", Bash, WebFetch"), encoding="utf-8")
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    restored = target.read_text(encoding="utf-8")
    assert "WebFetch" not in restored and ", Bash" not in restored


def test_stale_raw_tools_never_widen_canonical_scopes(tmp_path: Path) -> None:
    from agentteams.interop import import_from_cai

    cai = {"schema_version": "2.0", "source_framework": "copilot-vscode", "instructions_binding": {},
           "agents": [{"slug": "a", "name": "A", "description": "d", "body_markdown": "# A\n",
                       "handoffs": [], "capabilities": {"tool_scopes": ["read"],
                                                        "raw": {"copilot-vscode": "['read', 'edit', 'execute']"}}}]}
    out = tmp_path / ".github" / "agents"
    import_from_cai(cai, "copilot-vscode", out)
    text = (out / "a.agent.md").read_text(encoding="utf-8")
    assert "'edit'" not in text and "'execute'" not in text


def test_sidecar_keeps_handoffs_of_unchanged_agents(tmp_path: Path) -> None:
    import json

    from agentteams.interop import export_to_cai, import_from_cai

    _project(tmp_path)
    cai = export_to_cai(tmp_path / ".github" / "agents", "copilot-vscode")
    claude_dir = tmp_path / ".claude" / "agents"
    import_from_cai(cai, "claude", claude_dir, overwrite=True)
    # Change ONE agent; the unchanged one must keep its sidecar routing.
    for a in cai["agents"]:
        if a["slug"] == "orchestrator":
            a["body_markdown"] += "\nchanged\n"
    import_from_cai(cai, "claude", claude_dir, overwrite=True, preserve_existing=True)
    sidecar = json.loads((tmp_path / ".claude" / "references" / "runtime-handoffs.json").read_text())
    assert "wasm-wat-expert" in {e["agent"] for e in sidecar["agents"]}


def test_unsafe_slug_is_refused(tmp_path: Path) -> None:
    import pytest

    from agentteams.interop import import_from_cai

    cai = {"schema_version": "2.0", "agents": [{"slug": "../escape", "name": "x", "body_markdown": "b"}]}
    with pytest.raises(ValueError, match="unsafe CAI slug"):
        import_from_cai(cai, "codex", tmp_path / ".codex" / "agents")
    assert not (tmp_path / ".codex" / "escape.toml").exists()


def test_projection_backup_includes_skills(tmp_path: Path) -> None:
    _project(tmp_path)
    skill = tmp_path / ".claude" / "skills" / "recall"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: recall\n---\nkeep\n", encoding="utf-8")
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "claude"])
    backed = list((tmp_path / ".claude" / "agents" / ".agentteams-backups").glob("*/__external__/skills/recall/SKILL.md"))
    assert backed


def test_hand_added_codex_keys_are_reset_by_projection(tmp_path: Path) -> None:
    """@security C1 residual: codex TOML exports a key subset, so it is always re-rendered."""
    import tomllib

    _project(tmp_path)
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "codex"])
    toml_path = tmp_path / ".codex" / "agents" / "orchestrator.toml"
    text = toml_path.read_text(encoding="utf-8")
    toml_path.write_text(text.replace('name = "orchestrator"',
                                      'sandbox_mode = "danger-full-access"\nname = "orchestrator"'),
                         encoding="utf-8")
    sync_init(tmp_path, pin="copilot-vscode", frameworks=["copilot-vscode", "codex"])
    # the hand-added value is replaced by the projected one (the orchestrator declares edit)
    assert tomllib.loads(toml_path.read_text(encoding="utf-8"))["sandbox_mode"] == "workspace-write"
