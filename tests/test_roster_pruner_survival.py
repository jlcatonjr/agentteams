"""R2 — roster survival across the copilot-vscode/goose team-ref pruner (D1 regression).

The 2026-08-21 incident: an `--update --merge` pruned the orchestrator's `agents:` block
roster because `_get_team_slugs` was built only from `manifest["output_files"]` (the
regenerated subset), so deployed teammates the run did not re-emit were dropped — the empty
case emitted `agents: []`. R1a fixes the source: `_get_team_slugs`/`_team_slugs` now union
the full brief roster (`agent_slug_list`) and the on-disk deployed agents
(`existing_agent_slugs`, populated during `--update`). These tests prove the class is closed,
not merely guarded at the emit boundary.
"""

from __future__ import annotations

import io
import sys

from agentteams.frameworks.copilot_vscode import _filter_yaml_team_refs, _get_team_slugs
from agentteams.frameworks.goose import _team_slugs


_ROSTER = [
    "orchestrator", "navigator", "security", "primary-producer",
    # deployed teammates the current brief does NOT regenerate (the D1 orphans):
    "nginx", "vue-expert", "endo-expert", "working-paper",
]


def _block(roster: list[str]) -> str:
    return "agents:\n" + "".join(f"  - {s}\n" for s in roster)


def test_get_team_slugs_unions_on_disk_and_brief_roster():
    # output_files under-covers (only 2 agents re-emitted), but agent_slug_list +
    # existing_agent_slugs cover the rest — the union must include every roster slug.
    manifest = {
        "output_files": [
            {"path": "navigator.agent.md"},
            {"path": "security.agent.md"},
        ],
        "agent_slug_list": ["orchestrator", "navigator", "security", "primary-producer"],
        "existing_agent_slugs": ["nginx", "vue-expert", "endo-expert", "working-paper"],
    }
    slugs = _get_team_slugs(manifest)
    for s in _ROSTER:
        assert s in slugs, f"{s} missing from team_slugs"


def test_pruner_preserves_full_roster_when_output_files_undercover():
    manifest = {
        "output_files": [{"path": "navigator.agent.md"}],  # severe under-coverage
        "agent_slug_list": ["orchestrator", "navigator", "security", "primary-producer"],
        "existing_agent_slugs": ["nginx", "vue-expert", "endo-expert", "working-paper"],
    }
    team_slugs = _get_team_slugs(manifest)
    out = _filter_yaml_team_refs(_block(_ROSTER), team_slugs)
    assert "agents: []" not in out
    for s in _ROSTER:
        assert f"- {s}" in out.replace("'", ""), f"{s} was pruned from the roster"


def test_pruner_never_emits_empty_roster_for_populated_block():
    # Even with a team_slugs that covers NOTHING, refuse to blank a populated roster —
    # an all-drop is a coverage bug, so the original block is returned unchanged.
    out = _filter_yaml_team_refs(_block(_ROSTER), frozenset({"orchestrator"}))
    assert "agents: []" not in out


def test_pruner_still_drops_genuinely_departed_agent_with_warning(capsys):
    # A slug absent from brief, on-disk, and adopted IS legitimately pruned — and warns.
    manifest = {
        "output_files": [{"path": "navigator.agent.md"}],
        "agent_slug_list": ["orchestrator", "navigator", "security", "primary-producer"],
        "existing_agent_slugs": ["nginx", "vue-expert", "endo-expert", "working-paper"],
    }
    team_slugs = _get_team_slugs(manifest)
    roster = _ROSTER + ["deleted-ghost-agent"]
    out = _filter_yaml_team_refs(_block(roster), team_slugs)
    assert "deleted-ghost-agent" not in out  # genuinely departed → pruned
    for s in _ROSTER:
        assert f"- {s}" in out.replace("'", "")  # real teammates survive
    err = capsys.readouterr().err
    assert "deleted-ghost-agent" in err and "pruned" in err.lower()


def test_goose_team_slugs_unions_on_disk_and_brief_roster():
    manifest = {
        "output_files": [{"path": "navigator.agent.md"}],
        "agent_slug_list": ["orchestrator", "navigator", "security", "primary-producer"],
        "existing_agent_slugs": ["nginx", "vue-expert", "endo-expert", "working-paper"],
    }
    slugs = _team_slugs(manifest)
    for s in _ROSTER:
        assert s in slugs, f"{s} missing from goose team_slugs"
