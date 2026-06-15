"""Tests for tools-as-documents: stale tool-agent migration + skill/ref planning.

Covers the behavior change where specialist tools become reference docs
(Copilot) or skill docs (Claude), never agents — and the migration that removes
legacy ``tool-*.agent.md`` files left by older generations.
"""

from __future__ import annotations

from pathlib import Path

import build_team
from agentteams import emit


def _manifest_with_tool(slug: str = "tool-postgresql") -> dict:
    return {"project_name": "Demo", "tool_agents": [{"slug": slug, "tool_name": "PostgreSQL"}]}


# ---------------------------------------------------------------------------
# _remove_stale_tool_agents
# ---------------------------------------------------------------------------

def test_stale_removal_overwrite_deletes_and_backs_up(tmp_path):
    out = tmp_path / "agents"
    out.mkdir()
    legacy = out / "tool-postgresql.agent.md"
    legacy.write_text("---\nname: legacy\n---\n# legacy\n", encoding="utf-8")

    removed, notices = build_team._remove_stale_tool_agents(
        _manifest_with_tool(), out, "copilot-vscode", overwrite=True, dry_run=False
    )

    assert not legacy.exists(), "overwrite must delete the legacy tool agent"
    assert removed == [str(legacy)]
    assert notices == []
    # The file was backed up before deletion (recoverable).
    backups = list((out / ".agentteams-backups").rglob("tool-postgresql.agent.md"))
    assert backups, "legacy tool agent must be backed up before removal"


def test_stale_removal_merge_is_notice_only(tmp_path):
    out = tmp_path / "agents"
    out.mkdir()
    legacy = out / "tool-postgresql.agent.md"
    legacy.write_text("# legacy\n", encoding="utf-8")

    removed, notices = build_team._remove_stale_tool_agents(
        _manifest_with_tool(), out, "copilot-vscode", overwrite=False, dry_run=False
    )

    assert legacy.exists(), "merge mode must NOT delete (notice-only)"
    assert removed == []
    assert len(notices) == 1 and "tool-postgresql" in notices[0]


def test_stale_removal_dry_run_reports_without_deleting(tmp_path):
    out = tmp_path / "agents"
    out.mkdir()
    legacy = out / "tool-postgresql.agent.md"
    legacy.write_text("# legacy\n", encoding="utf-8")

    removed, notices = build_team._remove_stale_tool_agents(
        _manifest_with_tool(), out, "copilot-vscode", overwrite=True, dry_run=True
    )

    assert legacy.exists(), "dry-run must not delete"
    assert removed == [str(legacy)]


def test_stale_removal_claude_uses_md_suffix(tmp_path):
    out = tmp_path / "agents"
    out.mkdir()
    legacy = out / "tool-postgresql.md"  # claude agents are .md, not .agent.md
    legacy.write_text("# legacy\n", encoding="utf-8")

    removed, _ = build_team._remove_stale_tool_agents(
        _manifest_with_tool(), out, "claude", overwrite=True, dry_run=False
    )
    assert not legacy.exists()
    assert removed == [str(legacy)]


def test_stale_removal_ignores_unrelated_agents(tmp_path):
    out = tmp_path / "agents"
    out.mkdir()
    keep = out / "orchestrator.agent.md"
    keep.write_text("# orchestrator\n", encoding="utf-8")
    # A tool-prefixed agent NOT in the current team's tool set is left alone.
    other = out / "tool-something-else.agent.md"
    other.write_text("# other\n", encoding="utf-8")

    removed, notices = build_team._remove_stale_tool_agents(
        _manifest_with_tool(), out, "copilot-vscode", overwrite=True, dry_run=False
    )
    assert keep.exists() and other.exists()
    assert removed == [] and notices == []


# ---------------------------------------------------------------------------
# Backup / restore round-trip for out-of-tree files (skills, CLAUDE.md)
# ---------------------------------------------------------------------------

def test_backup_restore_preserves_out_of_tree_skills(tmp_path):
    """A ../skills/<tool>.md file backs up and restores to the right location."""
    project = tmp_path / "proj"
    agents = project / ".claude" / "agents"
    skills = project / ".claude" / "skills"
    agents.mkdir(parents=True)
    skills.mkdir(parents=True)

    (agents / "orchestrator.md").write_text("# orch v1\n", encoding="utf-8")
    skill = skills / "tool-postgresql.md"
    skill.write_text("# pg skill v1\n", encoding="utf-8")

    rels = ["orchestrator.md", "../skills/tool-postgresql.md"]
    result = emit.backup_output_dir(agents, files_to_backup=rels, reason="test", framework="claude")
    assert result.backup_path is not None

    # Mutate both files, then restore.
    (agents / "orchestrator.md").write_text("# orch v2\n", encoding="utf-8")
    skill.write_text("# pg skill v2\n", encoding="utf-8")

    emit.restore_backup(result.backup_path, agents, remove_extra=False)

    assert (agents / "orchestrator.md").read_text() == "# orch v1\n"
    # The skill is restored to .claude/skills/, NOT flattened into the agents dir.
    assert skill.read_text() == "# pg skill v1\n"
    assert not (agents / "tool-postgresql.md").exists()


def test_backup_restore_through_symlinked_root_with_remove_extra(tmp_path):
    """Regression: a symlinked output root must not mis-file in-tree files.

    `backup_output_dir` resolves source paths but compared against the raw
    output_dir, so on a symlinked root (macOS /tmp→/private/tmp, symlinked
    $HOME) every in-tree file fell into the __external__ branch — and
    `restore_backup(remove_extra=True)` then deleted the live files and wrote
    the restored copies one directory too high. `tmp_path` is already
    symlink-resolved, so this test introduces a real symlink component.
    """
    real = tmp_path / "real"
    agents = real / ".claude" / "agents"
    refs = agents / "references"
    refs.mkdir(parents=True)
    (agents / "orchestrator.md").write_text("# orch v1\n", encoding="utf-8")
    (refs / "code-hygiene.reference.md").write_text("# hygiene v1\n", encoding="utf-8")

    link = tmp_path / "link"
    link.symlink_to(real)  # symlinked component in the output path
    agents_via_link = link / ".claude" / "agents"

    rels = ["orchestrator.md", "references/code-hygiene.reference.md"]
    result = emit.backup_output_dir(agents_via_link, files_to_backup=rels, reason="t", framework="claude")
    assert result.backup_path is not None

    (agents / "orchestrator.md").write_text("# orch v2\n", encoding="utf-8")
    emit.restore_backup(result.backup_path, agents_via_link, remove_extra=True)

    # In-tree files survive remove_extra and restore to their real locations —
    # NOT deleted, NOT written to .claude/ one level up.
    assert (agents / "orchestrator.md").read_text() == "# orch v1\n"
    assert (refs / "code-hygiene.reference.md").read_text() == "# hygiene v1\n"
    assert not (real / ".claude" / "orchestrator.md").exists()
