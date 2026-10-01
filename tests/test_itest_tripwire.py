"""#6: the Claude Code sandbox itest version tripwire is advisory and safe."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from agentteams.cli import itest_tripwire as tw


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("AGENTTEAMS_NO_ITEST_TRIPWIRE", raising=False)
    return tmp_path


def _fake_claude(tmp_path: Path, monkeypatch, version: str = "2.1.251 (Claude Code)") -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(f"#!/bin/sh\necho '{version}'\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))


@pytest.mark.skipif(sys.platform == "win32", reason="posix fake binary")
def test_matching_record_says_unauthenticated_pass(cache, monkeypatch):
    _fake_claude(cache, monkeypatch)
    path = tw.write_record({"claude_version": "2.1.251 (Claude Code)", "passed_at": "t"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    (note,) = tw.tripwire_notes()
    assert "last recorded sandbox itest pass on this host (unauthenticated)" in note
    assert "verified" not in note.lower()


@pytest.mark.skipif(sys.platform == "win32", reason="posix fake binary")
@pytest.mark.parametrize("content", [None, "not json", "[]", json.dumps({"claude_version": "2.0.0"})])
def test_missing_malformed_or_stale_record_prints_the_rerun_note(cache, monkeypatch, content):
    _fake_claude(cache, monkeypatch)
    if content is not None:
        tw.record_path().parent.mkdir(parents=True)
        tw.record_path().write_text(content)
    (note,) = tw.tripwire_notes()
    assert "has no recorded sandbox itest pass" in note and "RUN_CLAUDE_SANDBOX_ITEST=1" in note


@pytest.mark.skipif(sys.platform == "win32", reason="posix fake binary")
def test_record_fields_are_sanitized(cache, monkeypatch):
    _fake_claude(cache, monkeypatch, version="2.1.251")
    tw.record_path().parent.mkdir(parents=True)
    tw.record_path().write_text(json.dumps({"claude_version": "2.1.251", "passed_at": "\x1b[31mX" + "y" * 500}))
    (note,) = tw.tripwire_notes()
    assert "\x1b" not in note and len(note) < 300


def test_absent_claude_and_disable_switch(cache, monkeypatch):
    monkeypatch.setenv("PATH", str(cache / "empty"))
    assert "not found" in tw.tripwire_notes()[0]
    monkeypatch.setenv("AGENTTEAMS_NO_ITEST_TRIPWIRE", "1")
    assert tw.tripwire_notes() == []


@pytest.mark.skipif(sys.platform == "win32", reason="posix fake binary")
@pytest.mark.parametrize("record", ["forged", "missing", "malformed"])
def test_check_wiring_exit_status_ignores_the_record(cache, monkeypatch, tmp_path, record):
    """The exit status depends on the live wiring only, never on the unauthenticated record."""
    from agentteams.frameworks.claude import verify_sandbox_wiring

    _fake_claude(cache, monkeypatch)
    if record == "forged":
        tw.write_record({"claude_version": "2.1.251 (Claude Code)", "passed_at": "x"})
    elif record == "malformed":
        tw.record_path().parent.mkdir(parents=True)
        tw.record_path().write_text("{")
    project = tmp_path / "proj"
    (project / ".claude" / "agents").mkdir(parents=True)
    ok, _ = verify_sandbox_wiring(project / ".claude" / "agents")
    ok_after = ok  # tripwire notes are appended separately and never change `ok`
    notes = tw.tripwire_notes()
    assert ok_after == ok and isinstance(notes, list)


@pytest.mark.skipif(sys.platform == "win32", reason="posix fake binary")
def test_deeply_nested_record_never_raises(cache, monkeypatch):
    _fake_claude(cache, monkeypatch)
    tw.record_path().parent.mkdir(parents=True)
    tw.record_path().write_text("[" * 100000 + "]" * 100000)
    notes = tw.tripwire_notes()
    assert len(notes) == 1 and notes[0].startswith("NOTE")
