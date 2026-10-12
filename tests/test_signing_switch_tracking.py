"""The agent-privilege switch tracks the description on every render (fleet round 3, 2026-10-11).

Defect: a projected ``.codex/agents/references/agent-privilege.json`` was written only when
absent, so ``"enforce_decision_signing": false`` in the description never reached it. These pin:
owned switches track (native and projected), a relax is loud and a fleet REVIEW reason, an
unclear-ownership switch is kept and reported, and the absent-key-means-true rule is unchanged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentteams import analyze, signing_switch as ss
from agentteams.cli.artifacts import _write_agent_privilege_config
from agentteams.cli.generate import _emit_agent_privilege_config
from agentteams.projection_marker import write_projection_marker


def _team(root: Path, rel: str, *, marker: dict | None, switch: bool | None,
          note: str = ss.NATIVE_NOTE) -> Path:
    team = root / rel
    (team / "references").mkdir(parents=True)
    if marker is not None:
        (team / ss.TEAM_MARKER_REL).write_text(json.dumps(marker), encoding="utf-8")
    if switch is not None:
        (team / ss.SWITCH_REL).write_text(
            json.dumps({"enforce_decision_signing": switch, "note": note}), encoding="utf-8")
    return team


def _value(team: Path) -> bool:
    return json.loads((team / ss.SWITCH_REL).read_text(encoding="utf-8"))["enforce_decision_signing"]


_NATIVE = {"framework": "copilot-vscode"}
_INTEROP = {"origin": "interop", "source_dir": ".github/agents", "framework": "codex"}


def test_absent_description_key_still_means_true():
    m = analyze.build_manifest({"project_goal": "x", "project_name": "T"}, framework="claude")
    assert m["enforce_decision_signing"] is True


def test_owned_native_switch_relaxes_loudly(tmp_path, capsys):
    team = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True)
    _write_agent_privilege_config({"enforce_decision_signing": False}, team)
    assert _value(team) is False
    out = capsys.readouterr().out
    assert ss.RELAXED_PREFIX in out
    assert ss.review_reason(ss.review_lines(out)).startswith("signing switch relaxed")


def test_owned_native_switch_tightens_quietly(tmp_path, capsys):
    team = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=False)
    _write_agent_privilege_config({"enforce_decision_signing": True}, team)
    assert _value(team) is True
    assert ss.review_lines(capsys.readouterr().out) == []


def test_switch_without_team_marker_is_kept_and_reported(tmp_path, capsys):
    team = _team(tmp_path, ".github/agents", marker=None, switch=True)
    _write_agent_privilege_config({"enforce_decision_signing": False}, team)
    assert _value(team) is True
    out = capsys.readouterr().out
    assert ss.KEPT_PREFIX in out and ss.RELAXED_PREFIX not in out


def test_hand_written_switch_is_kept_even_in_a_team(tmp_path, capsys):
    team = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True, note="ours, hands off")
    _write_agent_privilege_config({"enforce_decision_signing": False}, team)
    assert _value(team) is True
    assert ss.KEPT_PREFIX in capsys.readouterr().out


def test_unclear_switch_with_matching_value_is_silent(tmp_path, capsys):
    team = _team(tmp_path, ".github/agents", marker=None, switch=False, note="hand")
    _write_agent_privilege_config({"enforce_decision_signing": False}, team)
    assert ss.review_lines(capsys.readouterr().out) == []


def test_absent_switch_is_created(tmp_path):
    team = _team(tmp_path, ".claude/agents", marker=None, switch=None)
    path = _write_agent_privilege_config({"enforce_decision_signing": True}, team)
    assert path == team / ss.SWITCH_REL and _value(team) is True


def test_unchanged_owned_switch_is_not_rewritten(tmp_path):
    team = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=None)
    (team / ss.SWITCH_REL).write_text(ss.switch_payload(True, ss.NATIVE_NOTE), encoding="utf-8")
    assert ss.apply_switch(team, True, ss.NATIVE_NOTE).written is False


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlinked_switch_is_never_followed(tmp_path, capsys):
    team = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=None)
    victim = tmp_path / "victim.json"
    victim.write_text(ss.switch_payload(True, ss.NATIVE_NOTE), encoding="utf-8")
    (team / ss.SWITCH_REL).symlink_to(victim)
    _write_agent_privilege_config({"enforce_decision_signing": False}, team)
    assert json.loads(victim.read_text())["enforce_decision_signing"] is True
    assert ss.KEPT_PREFIX in capsys.readouterr().out


def test_native_render_carries_the_value_to_its_codex_projection(tmp_path, capsys):
    gh = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True)
    cx = _team(tmp_path, ".codex/agents", marker=_INTEROP, switch=True, note=ss.PROJECTION_NOTE)
    _emit_agent_privilege_config({"enforce_decision_signing": False}, gh)
    assert _value(gh) is False and _value(cx) is False
    out = capsys.readouterr().out
    assert "[.codex/agents]" in out and out.count(ss.RELAXED_PREFIX) == 2


def test_projection_of_another_source_is_left_alone(tmp_path):
    gh = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True)
    other = dict(_INTEROP, source_dir=".claude/agents")
    cx = _team(tmp_path, ".codex/agents", marker=other, switch=True, note=ss.PROJECTION_NOTE)
    _emit_agent_privilege_config({"enforce_decision_signing": False}, gh)
    assert _value(cx) is True


def test_governed_projection_stays_on(tmp_path):
    gh = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True)
    cx = _team(tmp_path, ".codex/agents", marker=_INTEROP, switch=True, note=ss.PROJECTION_NOTE)
    (cx / ss.GOVERNED_MARKER).write_text("", encoding="utf-8")
    _emit_agent_privilege_config({"enforce_decision_signing": False}, gh)
    assert _value(cx) is True


def test_hand_written_projected_switch_is_kept(tmp_path, capsys):
    gh = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True)
    cx = _team(tmp_path, ".codex/agents", marker=_INTEROP, switch=True, note="pinned by hand")
    _emit_agent_privilege_config({"enforce_decision_signing": False}, gh)
    assert _value(cx) is True
    assert "[.codex/agents]" in capsys.readouterr().out


@pytest.mark.parametrize("files", [True, False])
def test_reprojection_tracks_the_source_switch(tmp_path, files):
    gh = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=False)
    cx = _team(tmp_path, ".codex/agents", marker=_INTEROP, switch=True, note=ss.PROJECTION_NOTE)
    written = []
    if files:
        agent = cx / "a.toml"
        agent.write_text("x = 1\n", encoding="utf-8")
        written = [str(agent)]
    res = write_projection_marker(cx, "codex", source_dir=gh, source_framework="copilot-vscode",
                                  files_written=written)
    assert not res.error
    assert _value(cx) is False
    assert any(ss.RELAXED_PREFIX in n for n in res.notices)


def test_first_projection_keeps_a_preexisting_foreign_switch(tmp_path):
    gh = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=False)
    cx = _team(tmp_path, ".codex/agents", marker=None, switch=True, note="hand")
    agent = cx / "a.toml"
    agent.write_text("x = 1\n", encoding="utf-8")
    res = write_projection_marker(cx, "codex", source_dir=gh, source_framework="copilot-vscode",
                                  files_written=[str(agent)])
    assert _value(cx) is True
    assert any(ss.KEPT_PREFIX in n for n in res.notices)


def test_fleet_classifies_a_relax_as_review(tmp_path):
    from agentteams.fleet import _classify

    out = f"  ⚠  {ss.RELAXED_PREFIX}: [.codex/agents] x true -> false\nother line\n"
    cls = _classify(tmp_path, "HEAD", "", out)
    assert len(cls["signing_switch"]) == 1
    assert "signing switch relaxed" in ss.review_reason(cls["signing_switch"])
    assert _classify(tmp_path, "HEAD", "", "  ℹ  switch tightened\n")["signing_switch"] == []


def test_governed_native_team_never_gets_false(tmp_path, capsys):
    team = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True)
    (team / ss.GOVERNED_MARKER).write_text("", encoding="utf-8")
    _write_agent_privilege_config({"enforce_decision_signing": False}, team)
    assert _value(team) is True
    absent = _team(tmp_path / "p2", ".github/agents", marker=_NATIVE, switch=None)
    (absent / ss.GOVERNED_MARKER).write_text("", encoding="utf-8")
    _write_agent_privilege_config({"enforce_decision_signing": False}, absent)
    assert _value(absent) is True
    assert capsys.readouterr().out.count(ss.KEPT_PREFIX) == 2


def test_kept_source_switch_is_what_siblings_follow(tmp_path):
    """A hand-pinned (kept) native switch is propagated, not the bare description value."""
    gh = _team(tmp_path, ".github/agents", marker=_NATIVE, switch=True, note="pinned by hand")
    cx = _team(tmp_path, ".codex/agents", marker=_INTEROP, switch=True, note=ss.PROJECTION_NOTE)
    _emit_agent_privilege_config({"enforce_decision_signing": False}, gh)
    assert _value(gh) is True and _value(cx) is True


def test_source_without_a_switch_never_relaxes_a_projection(tmp_path):
    """multi_sync projects from .agentteams/canonical, which has no switch: keep the target."""
    canonical = tmp_path / ".agentteams" / "canonical"
    canonical.mkdir(parents=True)
    cx = _team(tmp_path, ".codex/agents", marker=dict(_INTEROP, source_dir=".agentteams/canonical"),
               switch=True, note=ss.PROJECTION_NOTE)
    agent = cx / "a.toml"
    agent.write_text("x = 1\n", encoding="utf-8")
    for files in ([str(agent)], []):
        res = write_projection_marker(cx, "codex", source_dir=canonical, source_framework="canonical",
                                      files_written=files)
        assert not res.error and _value(cx) is True
        assert not any(ss.RELAXED_PREFIX in n for n in res.notices)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_sibling_behind_a_symlinked_parent_is_not_written(tmp_path):
    gh = _team(tmp_path / "proj", ".github/agents", marker=_NATIVE, switch=False)
    outside = _team(tmp_path / "elsewhere", "codex/agents", marker=_INTEROP, switch=True,
                    note=ss.PROJECTION_NOTE)
    (tmp_path / "proj" / ".codex").symlink_to(outside.parent)
    assert ss.sync_projected_switches(gh) == []
    assert _value(outside) is True


def test_review_lines_match_prefixed_notices():
    out = f"  [codex] Notice: {ss.RELAXED_PREFIX}: x\n  ✗  {ss.KEPT_PREFIX}: y\nunrelated\n"
    assert len(ss.review_lines(out)) == 2
