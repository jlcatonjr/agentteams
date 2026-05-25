"""Tests for build_team._persist_orphan_events (F5)."""

from __future__ import annotations

from pathlib import Path

import build_team


def _redirect_build_team_root(tmp_path: Path, monkeypatch):
    """Make Path(__file__) point inside tmp_path so the log writes there."""
    fake = tmp_path / "_fake_build_team.py"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(build_team, "__file__", str(fake))


def test_no_orphans_writes_nothing(tmp_path, monkeypatch):
    _redirect_build_team_root(tmp_path, monkeypatch)
    build_team._persist_orphan_events([], {"project_name": "Demo"}, tmp_path / "out")
    assert not (tmp_path / "tmp" / "daily-pipeline" / "orphan-events").exists()


def test_orphans_persisted_first_time(tmp_path, monkeypatch):
    _redirect_build_team_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    orphans = ["stale-a.agent.md", "stale-b.agent.md"]
    build_team._persist_orphan_events(orphans, {"project_name": "Demo"}, out_dir)
    log_dir = tmp_path / "tmp" / "daily-pipeline" / "orphan-events"
    files = list(log_dir.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "Demo" in body
    assert "stale-a.agent.md" in body
    assert "stale-b.agent.md" in body
    assert "@cleanup" in body


def test_same_signature_is_silent(tmp_path, monkeypatch):
    _redirect_build_team_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    orphans = ["stale-a.agent.md"]
    build_team._persist_orphan_events(orphans, {"project_name": "Demo"}, out_dir)
    log_path = next((tmp_path / "tmp" / "daily-pipeline" / "orphan-events").glob("*.md"))
    first_len = len(log_path.read_text(encoding="utf-8"))
    # Second call with identical orphans → no new section appended.
    build_team._persist_orphan_events(orphans, {"project_name": "Demo"}, out_dir)
    assert len(log_path.read_text(encoding="utf-8")) == first_len


def test_different_signature_appends(tmp_path, monkeypatch):
    _redirect_build_team_root(tmp_path, monkeypatch)
    out_dir = tmp_path / "out"
    build_team._persist_orphan_events(["a.agent.md"], {"project_name": "Demo"}, out_dir)
    build_team._persist_orphan_events(["a.agent.md", "b.agent.md"], {"project_name": "Demo"}, out_dir)
    log_path = next((tmp_path / "tmp" / "daily-pipeline" / "orphan-events").glob("*.md"))
    body = log_path.read_text(encoding="utf-8")
    # Two distinct signatures recorded.
    assert body.count("## Demo @") == 2
