"""Tests for agentteams.hooks_emit (Phase 3)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentteams import hooks_emit as he


def _src_agent(dir_path: Path, slug: str) -> Path:
    body = f"---\nname: {slug}\ndescription: d\n---\n# {slug}\n"
    p = dir_path / f"{slug}.agent.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_build_settings_dict_omits_absent_slugs(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "orchestrator")  # not in _HOOK_MAP
    _src_agent(src, "cleanup")        # in map
    settings = he.build_settings_dict(src)
    hook_cmds = json.dumps(settings["hooks"])
    assert "cleanup" in hook_cmds
    assert "orchestrator" not in hook_cmds


def test_build_settings_dict_maps_known_slugs(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    for slug in ("cleanup", "security", "work-summarizer"):
        _src_agent(src, slug)
    settings = he.build_settings_dict(src)
    events = settings["hooks"]
    # cleanup → PostToolUse Write|Edit
    post = events.get("PostToolUse", [])
    assert any(
        "cleanup" in h["hooks"][0]["command"] and h.get("matcher") == "Write|Edit"
        for h in post
    )
    # security → PreToolUse Bash|Write|Edit
    pre = events.get("PreToolUse", [])
    assert any(
        "security" in h["hooks"][0]["command"] and h.get("matcher") == "Bash|Write|Edit"
        for h in pre
    )
    # work-summarizer → Stop, no matcher
    stop = events.get("Stop", [])
    assert any("work-summarizer" in h["hooks"][0]["command"] for h in stop)
    assert all("matcher" not in h for h in stop)


def test_emit_writes_example_and_guard(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "cleanup")
    result = he.emit_hooks_artifacts(source_dir=src, output_root=tmp_path)
    assert result.success
    settings_path = tmp_path / ".claude" / "settings.agentteams.example.json"
    guard_path = tmp_path / ".claude" / "hook-guard.sh"
    assert settings_path.exists()
    assert guard_path.exists()
    # Guard must be executable.
    assert os.access(guard_path, os.X_OK)
    parsed = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "_agentteams_managed" in parsed
    assert "hooks" in parsed


def test_emit_dry_run(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "cleanup")
    he.emit_hooks_artifacts(source_dir=src, output_root=tmp_path, dry_run=True)
    assert not (tmp_path / ".claude" / "settings.agentteams.example.json").exists()


def test_guard_script_records_notice_and_respects_depth(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "cleanup")
    he.emit_hooks_artifacts(source_dir=src, output_root=tmp_path)
    guard = tmp_path / ".claude" / "hook-guard.sh"
    # First invocation: should log.
    env = dict(os.environ)
    env.pop("AGENTTEAMS_HOOK_DEPTH", None)
    env.pop("AGENTTEAMS_HOOK_MAX_DEPTH", None)
    r = subprocess.run(
        ["bash", str(guard), "PostToolUse", "cleanup"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0
    notices = list((tmp_path / ".claude" / "hook-notices").glob("*.log"))
    assert notices, "expected a notice log file"
    content = notices[0].read_text(encoding="utf-8")
    assert "event=PostToolUse" in content
    assert "slug=cleanup" in content
    # At max depth: should still exit 0 but not log a new entry.
    line_count_before = content.count("\n")
    env["AGENTTEAMS_HOOK_DEPTH"] = "2"
    r2 = subprocess.run(
        ["bash", str(guard), "PostToolUse", "cleanup"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert r2.returncode == 0
    content2 = notices[0].read_text(encoding="utf-8")
    assert content2.count("\n") == line_count_before


def test_emit_overwrite_false_skips(tmp_path: Path):
    src = tmp_path / ".github" / "agents"
    _src_agent(src, "cleanup")
    he.emit_hooks_artifacts(source_dir=src, output_root=tmp_path)
    r2 = he.emit_hooks_artifacts(source_dir=src, output_root=tmp_path, overwrite=False)
    assert r2.skipped
    assert not r2.written
