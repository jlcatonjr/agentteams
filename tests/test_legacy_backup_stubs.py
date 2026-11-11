"""Legacy `.md` backup stubs that load as live agents are neutralized on every runtime dir (IM item 01, G-14-1).

A Copilot-primary repo with a Claude bridge never backs up `.claude/agents/` itself (the bridge backs up the project
root), so pre-2026-10-12 copies under `.claude/agents/.agentteams-backups/` kept loading with every tool.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import backup, doctor

_STUB = "---\nname: adversarial\ndescription: legacy copy\n---\n# adversarial (no tools: line -> every tool)\n"


def _consumer(root: Path, runtime: str = ".claude/agents") -> Path:
    """A Copilot-primary project with a bridge runtime dir holding a legacy loadable backup copy."""
    (root / ".github" / "agents").mkdir(parents=True)
    (root / ".github" / "agents" / "adversarial.agent.md").write_text("---\nname: a\n---\n")
    stub_dir = root / runtime / ".agentteams-backups" / "20260806-164702"
    stub_dir.mkdir(parents=True)
    stub = stub_dir / ("adversarial.md" if runtime != ".goose/recipes" else "adversarial.yaml")
    stub.write_text(_STUB)
    (stub_dir / backup.BACKUP_MANIFEST_NAME).write_text(json.dumps(
        {"files": [{"path": stub.name, "backup_path": stub.name}]}))
    (root / "CLAUDE.md").write_text("# entry\n")
    return stub


def test_bridge_path_neutralizes_the_runtime_stub(tmp_path):
    """bridge.py backs up the project root (files_to_backup = entry files); the .claude stub must be renamed."""
    stub = _consumer(tmp_path)
    backup.backup_output_dir(tmp_path, files_to_backup=["CLAUDE.md"], reason="bridge-merge", framework="claude")
    assert not stub.exists()
    inert = stub.with_name(backup.inert_backup_name(stub.name))
    assert inert.is_file() and inert.read_text() == _STUB          # renamed, never deleted
    assert backup.runtime_loadable_backups(tmp_path) == []


def test_update_path_on_the_primary_dir_neutralizes_the_other_runtime_dirs(tmp_path):
    stub = _consumer(tmp_path)
    backup.backup_output_dir(tmp_path / ".github" / "agents", files_to_backup=["adversarial.agent.md"],
                             reason="merge-overwrite-fenced", framework="copilot-vscode")
    assert not stub.exists() and backup.runtime_loadable_backups(tmp_path) == []


@pytest.mark.parametrize("runtime", [".goose/recipes", ".codex/agents", ".grok/agents"])
def test_every_runtime_dir_is_covered(tmp_path, runtime):
    stub = _consumer(tmp_path, runtime)
    backup.backup_output_dir(tmp_path, files_to_backup=["CLAUDE.md"], reason="bridge-merge")
    assert not stub.exists()


def test_the_manifest_points_at_the_renamed_file(tmp_path):
    """Item 35 relies on the rename's naming and manifest format staying stable."""
    stub = _consumer(tmp_path)
    backup.backup_output_dir(tmp_path, files_to_backup=["CLAUDE.md"], reason="bridge-merge")
    manifest = json.loads((stub.parent / backup.BACKUP_MANIFEST_NAME).read_text())
    assert manifest["files"][0]["backup_path"] == backup.inert_backup_name(stub.name)
    assert backup.inert_backup_name("x.md") == "x.md.agentteams-bak"


def test_doctor_fails_on_a_loadable_stub(tmp_path, capsys):
    _consumer(tmp_path)
    assert doctor.run_doctor(str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "Backup stubs" in out and "loads as a live agent" in out


def test_check_warns_on_a_loadable_stub(tmp_path, capsys):
    _consumer(tmp_path)
    found = backup.warn_runtime_backups(tmp_path / ".github" / "agents")
    assert len(found) == 1 and "loads as a live agent" in capsys.readouterr().err


def test_a_clean_project_reports_nothing(tmp_path, capsys):
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    assert backup.runtime_loadable_backups(tmp_path) == []
    assert backup.warn_runtime_backups(tmp_path / ".claude" / "agents") == []
