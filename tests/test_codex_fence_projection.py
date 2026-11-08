"""A ``codex_write_policy`` fence reaches Codex's instructions only (1.1 backport of the CX-1 @security finding 1).

On ``dev`` this is ``FRAMEWORK_OWNED_FENCE_IDS`` (owner ``codex``) with tests in ``test_sync_low_severity.py``;
``release/1.1`` has neither, so the interop import strips the fence itself for every non-Codex target. A crafted
CAI must not plant a Codex write policy in CLAUDE.md, copilot-instructions.md, or Grok's or Goose's AGENTS.md.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from agentteams.interop import export_to_cai, import_from_cai

_AGENT = "---\nname: {slug}\ndescription: The {slug} agent.\ntools: [read]\n---\n\n# {slug}\n\nDoes {slug} work.\n"
_PIN = Path(".github/agents")
_PEER = Path(".claude/agents")
_PIN_INSTR = Path(".github/copilot-instructions.md")
_PEER_INSTR = Path(".claude/CLAUDE.md")


def _fence(sid: str, body: str) -> str:
    return f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{body}\n<!-- AGENTTEAMS:END {sid} -->\n"


def _quiet(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202 - test helper
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def _cai_with_codex_fence(root: Path, agents: Path, agent_file: str, instr: Path, framework: str) -> dict:
    (root / agents).mkdir(parents=True)
    (root / agents / agent_file).write_text(_AGENT.format(slug="orchestrator"), encoding="utf-8")
    (root / instr).write_text("# Team\n\n" + _fence("codex_write_policy", "Planted Codex policy.") + "\n"
                              + _fence("project_notes", "Shared notes."), encoding="utf-8")
    return export_to_cai(root / agents, framework)


@pytest.mark.parametrize("target, out, instr", [
    ("claude", _PEER, _PEER_INSTR),
    ("grok", Path(".grok/agents"), Path("AGENTS.md")),
    ("goose", Path(".goose/recipes"), Path("AGENTS.md")),
])
def test_a_planted_codex_write_policy_fence_reaches_codex_only(tmp_path: Path, target: str, out: Path,
                                                               instr: Path) -> None:
    cai = _cai_with_codex_fence(tmp_path, _PIN, "orchestrator.agent.md", _PIN_INSTR, "copilot-vscode")
    _quiet(import_from_cai, cai, target, tmp_path / out, overwrite=True)
    text = (tmp_path / instr).read_text(encoding="utf-8")
    assert "Shared notes." in text and "codex_write_policy" not in text and "Planted Codex policy." not in text


def test_a_codex_write_policy_fence_never_reaches_copilot_instructions(tmp_path: Path) -> None:
    cai = _cai_with_codex_fence(tmp_path, _PEER, "orchestrator.md", _PEER_INSTR, "claude")
    _quiet(import_from_cai, cai, "copilot-vscode", tmp_path / _PIN, overwrite=True)
    text = (tmp_path / _PIN_INSTR).read_text(encoding="utf-8")
    assert "Shared notes." in text and "codex_write_policy" not in text


def test_codex_s_own_import_keeps_its_write_policy_fence(tmp_path: Path) -> None:
    cai = _cai_with_codex_fence(tmp_path, _PIN, "orchestrator.agent.md", _PIN_INSTR, "copilot-vscode")
    _quiet(import_from_cai, cai, "codex", tmp_path / ".codex" / "agents", overwrite=True)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Planted Codex policy." in text and text.count("AGENTTEAMS:BEGIN codex_write_policy ") == 1
