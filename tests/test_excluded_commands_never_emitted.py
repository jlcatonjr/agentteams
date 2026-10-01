"""Pinned: agentteams never emits Claude Code ``sandbox.excludedCommands``, and warns on a live one.

Measured on Claude Code 2.1.251 (SECURITY.md advisory): with
``sandbox.excludedCommands = ["/bin/bash /abs/fixed.sh"]``, the command
``PROBE_ENV=1 /bin/bash /abs/fixed.sh`` still runs unsandboxed *with the variable set*, so a
leading ``LD_PRELOAD=`` or ``PYTHONPATH=`` turns any excluded command into an escape. The key is
therefore rejected as a mechanism (self-updating-agents plan, second revision item 8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.cli.generate_helpers import (
    _warn_live_sandbox_excluded_commands,
    live_excluded_commands_warning,
)
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.frameworks.claude import ClaudeAdapter

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("profile", ["cooperative", "confined", "exclusive"])
@pytest.mark.parametrize("protect", [False, True])
def test_no_emitted_settings_example_contains_excluded_commands(tmp_path, profile, protect):
    manifest = {"project_name": "X", "framework": "claude", "privilege_profile": profile,
                PROJECT_ROOT_KEY: tmp_path.resolve()}
    if protect:
        manifest["protect_prompt_roots"] = True
    files = dict(ClaudeAdapter().extra_output_files(manifest))
    assert files, "the claude adapter emitted no extra files"
    for name, content in files.items():
        assert "excludedCommands" not in str(content), name


def test_no_template_or_emitter_source_contains_excluded_commands():
    hits = [str(p.relative_to(REPO)) for p in (REPO / "agentteams").rglob("*")
            if p.is_file() and p.suffix in {".py", ".json", ".md", ".sh"}
            and "excludedCommands" in p.read_text(encoding="utf-8", errors="replace")]
    # Only the warning itself may name the key.
    assert hits == ["agentteams/cli/generate_helpers.py"], hits


def _settings(root: Path, sandbox: dict) -> None:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({"sandbox": sandbox}))


def test_live_excluded_commands_warns_high_without_echoing_entries(tmp_path, capsys):
    _settings(tmp_path, {"enabled": True, "excludedCommands": ["/bin/bash /secret/path.sh"]})
    warning = live_excluded_commands_warning(tmp_path)
    assert warning and "HIGH" in warning and "LD_PRELOAD" in warning
    assert "/secret/path.sh" not in warning
    assert _warn_live_sandbox_excluded_commands(tmp_path) is True
    assert "excludedCommands" in capsys.readouterr().err


@pytest.mark.parametrize("sandbox", [{"enabled": True}, {"enabled": True, "excludedCommands": []}])
def test_no_warning_without_excluded_commands(tmp_path, sandbox):
    _settings(tmp_path, sandbox)
    assert live_excluded_commands_warning(tmp_path) is None
    assert live_excluded_commands_warning(tmp_path / "absent") is None


def test_check_wiring_fails_on_live_excluded_commands(tmp_path, capsys):
    from agentteams.cli.app import main

    brief = REPO / "examples" / "software-project" / "brief.json"
    out = tmp_path / ".claude" / "agents"
    _settings(tmp_path, {"enabled": True, "excludedCommands": ["x"]})
    rc = main(["--description", str(brief), "--framework", "claude", "--output", str(out),
               "--check-wiring"])
    assert rc == 1
    assert "excludedCommands" in capsys.readouterr().out
