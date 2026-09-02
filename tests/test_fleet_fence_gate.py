"""test_fleet_fence_gate.py — the fleet update path must flag malformed-fence renders.

The researchteam autosync gate rejects a changed file whose real fence markers do not pair up
(caught the parallelization.reference.md corruption on 2026-09-01). The Layer-A fleet path had NO
equivalent check, so a malformed render could be committed/pushed to a direct-write consumer
undetected. `_fence_imbalances` closes that gap, mirroring the autosync gate:

- counts only REAL HTML-comment markers (a marker quoted in prose or shown as table data is not a
  fence — the instruction-authority.reference.md prose case and the agent-inventory.md data case);
- exempts generated bridge report/inventory artifacts under references/bridges/, which legitimately
  DISPLAY marker syntax as data (agent-inventory.md lists each agent's BEGIN marker — the false
  positive that aborted the new_bid_tool sync on 2026-09-02).
"""
from __future__ import annotations

from pathlib import Path

from agentteams.fleet import _fence_imbalances

_BALANCED = "<!-- AGENTTEAMS:BEGIN content v=1 -->\nbody\n<!-- AGENTTEAMS:END content -->\n"
_ORPHAN_END = (  # the parallelization corruption shape
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\na\n<!-- AGENTTEAMS:END content -->\n"
    "dup\n<!-- AGENTTEAMS:END content -->\n"
)
_ORPHAN_BEGIN = (  # unclosed fence
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\na\n<!-- AGENTTEAMS:END content -->\n"
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\nb\n"
)
_PROSE = (  # instruction-authority style: real 1/1, marker only quoted in prose
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\ncarries no `AGENTTEAMS:BEGIN` fence\n"
    "<!-- AGENTTEAMS:END content -->\n"
)
_INVENTORY = "| agent | <!-- AGENTTEAMS:BEGIN content v=1 --> |\n" * 8  # 8 real BEGIN as data


def _mk(ws: Path, rel: str, text: str) -> str:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return rel


def test_flags_orphan_end(tmp_path):
    f = _mk(tmp_path, ".github/agents/references/bad.reference.md", _ORPHAN_END)
    assert _fence_imbalances(tmp_path, [f]) == [f + " (1/2)"]


def test_flags_orphan_begin(tmp_path):
    f = _mk(tmp_path, ".github/agents/references/bad2.reference.md", _ORPHAN_BEGIN)
    assert _fence_imbalances(tmp_path, [f]) == [f + " (2/1)"]


def test_balanced_not_flagged(tmp_path):
    f = _mk(tmp_path, ".github/agents/orchestrator.agent.md", _BALANCED)
    assert _fence_imbalances(tmp_path, [f]) == []


def test_prose_mention_not_flagged(tmp_path):
    f = _mk(tmp_path, ".github/agents/references/instruction-authority.reference.md", _PROSE)
    assert _fence_imbalances(tmp_path, [f]) == []


def test_bridge_inventory_exempt(tmp_path):
    f = _mk(tmp_path, "references/bridges/copilot-vscode-to-claude/agent-inventory.md", _INVENTORY)
    assert _fence_imbalances(tmp_path, [f]) == []


def test_nested_bridge_inventory_exempt(tmp_path):
    f = _mk(tmp_path, "sub/team/references/bridges/x/agent-inventory.md", _INVENTORY)
    assert _fence_imbalances(tmp_path, [f]) == []


def test_non_markdown_ignored(tmp_path):
    f = _mk(tmp_path, ".github/agents/references/build-log.json", "{ }\n")
    assert _fence_imbalances(tmp_path, [f]) == []


def test_mixed_batch_flags_only_bad(tmp_path):
    files = [
        _mk(tmp_path, ".github/agents/references/ok.reference.md", _BALANCED),
        _mk(tmp_path, ".github/agents/references/bad.reference.md", _ORPHAN_END),
        _mk(tmp_path, "references/bridges/c/agent-inventory.md", _INVENTORY),
    ]
    assert _fence_imbalances(tmp_path, files) == [".github/agents/references/bad.reference.md (1/2)"]
