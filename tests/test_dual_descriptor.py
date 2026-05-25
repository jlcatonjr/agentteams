"""Tests for build_team._check_dual_descriptor."""

from __future__ import annotations

import json
from types import SimpleNamespace

import build_team


def _write_descriptor(path, **overrides):
    body = {
        "project_name": "Demo",
        "primary_output_dir": "reports/",
        "reference_db_path": "references/bibliography.bib",
        "deliverables": ["a", "b"],
    }
    body.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def test_no_sibling_is_silent(tmp_path, capsys):
    desc = tmp_path / "brief.json"
    _write_descriptor(desc)
    args = SimpleNamespace(description=str(desc), output=str(tmp_path), self_update=False)
    build_team._check_dual_descriptor(args)
    captured = capsys.readouterr()
    assert "Dual descriptor" not in captured.err


def test_divergent_sibling_warns(tmp_path, capsys, monkeypatch):
    # Redirect the daily-pipeline log to the test tmp tree so the test
    # never writes into the real agentteams source tree.
    monkeypatch.setattr(build_team, "__file__", str(tmp_path / "_fake_build_team.py"))
    (tmp_path / "_fake_build_team.py").write_text("", encoding="utf-8")

    desc = tmp_path / "brief.json"
    sibling = tmp_path / ".github" / "agents" / "_build-description.json"
    _write_descriptor(desc, primary_output_dir="reports/")
    _write_descriptor(sibling, primary_output_dir="Projects/", deliverables=["a"])

    args = SimpleNamespace(description=str(desc), output=str(tmp_path), self_update=False)
    build_team._check_dual_descriptor(args)

    captured = capsys.readouterr()
    assert "Dual descriptor detected" in captured.err
    assert "primary_output_dir" in captured.err
    assert "deliverables" in captured.err

    log_dir = tmp_path / "tmp" / "daily-pipeline" / "dual-descriptor-events"
    files = list(log_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "Demo" in content
    assert "primary_output_dir" in content


def test_self_update_skips(tmp_path, capsys):
    desc = tmp_path / "brief.json"
    sibling = tmp_path / ".github" / "agents" / "_build-description.json"
    _write_descriptor(desc)
    _write_descriptor(sibling, primary_output_dir="Different/")
    args = SimpleNamespace(description=str(desc), output=str(tmp_path), self_update=True)
    build_team._check_dual_descriptor(args)
    captured = capsys.readouterr()
    assert "Dual descriptor" not in captured.err
