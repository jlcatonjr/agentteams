"""A pinned-sync projection keeps the sibling-team denies a native generate emits (remediation-log 2026-11-03).

``_projected_privilege_manifest`` never set ``SIBLING_DENY_DIRS_KEY``, so a projected boundary treated every
sibling team as absent: goose's ``.goose/sandbox.sb`` lost the ``.github``, ``.github/agents`` and
``.github/agents/references`` F-4 ancestor rename-deny literals whenever a copilot team was present.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentteams import multi_sync as ms
from agentteams.frameworks._sandbox_emit import SIBLING_DENY_DIRS_KEY, team_marker_path

_CONFINED = {"privilege_profile": "confined", "privilege_profile_explicit": True}
_GITHUB_LITERALS = [f'(literal (string-append (param "WORKSPACE_ROOT") "/{p}"))'
                    for p in (".github", ".github/agents", ".github/agents/references")]


@pytest.fixture
def macos(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Seatbelt profile is emitted on darwin only (``goose_sandbox_output_files``)."""
    monkeypatch.setattr(sys, "platform", "darwin")


def _copilot_team(root: Path) -> None:
    marker = root / team_marker_path("copilot")
    marker.parent.mkdir(parents=True)
    marker.write_text("{}", encoding="utf-8")


def _f4_block(sandbox_sb: str) -> str:
    start = sandbox_sb.index(";; F-4: refuse RENAMING")
    return sandbox_sb[start:sandbox_sb.index("\n)\n", start)]  # the block's closing paren sits on its own line


def test_projected_manifest_carries_the_present_sibling_teams(tmp_path: Path) -> None:
    assert ms._projected_privilege_manifest(tmp_path, "goose", _CONFINED)[SIBLING_DENY_DIRS_KEY] == ()
    _copilot_team(tmp_path)
    assert ms._projected_privilege_manifest(tmp_path, "goose", _CONFINED)[SIBLING_DENY_DIRS_KEY] == (".github/agents",)


def test_projected_goose_sandbox_keeps_the_github_f4_literals(tmp_path: Path, macos: None) -> None:
    _copilot_team(tmp_path)
    ms._emit_privilege_artifacts(tmp_path, "goose", _CONFINED, dry_run=False)
    block = _f4_block((tmp_path / ".goose" / "sandbox.sb").read_text(encoding="utf-8"))
    for literal in _GITHUB_LITERALS:
        assert literal in block


def _deny_write(settings: dict) -> list[str]:
    return settings["sandbox"]["filesystem"]["denyWrite"]


def test_projected_claude_example_settings_deny_the_copilot_team(tmp_path: Path) -> None:
    ms._emit_privilege_artifacts(tmp_path / "absent", "claude", _CONFINED, dry_run=False)
    absent = json.loads((tmp_path / "absent" / ".claude" / "settings.hooks.example.json").read_text(encoding="utf-8"))
    assert ".github/agents" not in _deny_write(absent)
    _copilot_team(tmp_path / "present")
    ms._emit_privilege_artifacts(tmp_path / "present", "claude", _CONFINED, dry_run=False)
    present = json.loads((tmp_path / "present" / ".claude" / "settings.hooks.example.json").read_text(encoding="utf-8"))
    assert ".github/agents" in _deny_write(present)


def test_without_a_copilot_team_the_github_literals_stay_out(tmp_path: Path, macos: None) -> None:
    ms._emit_privilege_artifacts(tmp_path, "goose", _CONFINED, dry_run=False)
    block = _f4_block((tmp_path / ".goose" / "sandbox.sb").read_text(encoding="utf-8"))
    assert not any(literal in block for literal in _GITHUB_LITERALS)
