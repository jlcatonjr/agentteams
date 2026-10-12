"""Backups must never be loadable as agents (accuracy-test harness finding, 2026-10-12).

Claude Code loads every ``*.md`` under ``.claude/agents/`` recursively, dot-directories included. agentteams keeps
pre-update backups in ``<agents dir>/.agentteams-backups/<ts>/``, so a backed-up pre-switch agent (with its original
Write tool) shadowed the narrowed one and defeated the write policy. Backed-up ``.md``/``.yaml``/``.yml`` files are
now stored inert (``*.agentteams-bak``), legacy backups are neutralized on the next update, and ``--check-wiring``
refuses a loadable backup under the switch.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentteams import backup as B
from agentteams import runner_mcp


def _team(tmp_path: Path) -> Path:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "producer.md").write_text("---\nname: Producer\ntools: Read, Edit, Write\n---\nbody\n")
    (agents / "recipe.yaml").write_text("version: 1.0.0\n")
    return agents


def test_new_backups_store_agent_files_inert_and_restore_them(tmp_path):
    agents = _team(tmp_path)
    res = B.backup_output_dir(agents)
    names = {p.name for p in res.backup_path.rglob("*") if p.is_file()}
    assert "producer.md.agentteams-bak" in names and "recipe.yaml.agentteams-bak" in names
    assert not B.loadable_backup_files(agents)
    assert all(e["status"] == "PASS" for e in B.verify_backup(res.backup_path))
    (agents / "producer.md").write_text("changed\n")
    B.restore_backup(res.backup_path, agents)
    assert "tools: Read, Edit, Write" in (agents / "producer.md").read_text()
    assert not list(agents.glob("*.agentteams-bak"))


def test_legacy_loadable_backups_are_neutralized_with_their_manifest(tmp_path):
    agents = _team(tmp_path)
    legacy = agents / ".agentteams-backups" / "20260101-000000"
    legacy.mkdir(parents=True)
    (legacy / "producer.md").write_text("---\nname: Producer\ntools: Read, Edit, Write\n---\nold\n")
    import hashlib
    sha = hashlib.sha256((legacy / "producer.md").read_bytes()).hexdigest()
    (legacy / B.BACKUP_MANIFEST_NAME).write_text(json.dumps({"files": [
        {"source_path": "producer.md", "backup_path": "producer.md", "source_sha256": sha}]}))
    assert [p.name for p in B.loadable_backup_files(agents)] == ["producer.md"]
    renamed = B.neutralize_legacy_backups(agents)
    assert renamed == ["20260101-000000/producer.md"]
    assert not B.loadable_backup_files(agents)
    assert [e["status"] for e in B.verify_backup(legacy)] == ["PASS"]


def test_every_backup_run_neutralizes_legacy_files_first(tmp_path):
    agents = _team(tmp_path)
    legacy = agents / ".agentteams-backups" / "20260101-000000"
    legacy.mkdir(parents=True)
    (legacy / "producer.md").write_text("old\n")
    B.backup_output_dir(agents)
    assert not B.loadable_backup_files(agents)


def test_wiring_check_refuses_a_loadable_backup_under_the_switch(tmp_path, monkeypatch):
    import platform

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    agents = _team(tmp_path)
    legacy = agents / ".agentteams-backups" / "20260101-000000"
    legacy.mkdir(parents=True)
    (legacy / "producer.md").write_text("old\n")
    problems = runner_mcp.wiring_problems(tmp_path, "claude", {"write_policy": "orchestrator-only"}, home=tmp_path)
    assert any("shadow the narrowed ones" in p for p in problems)
