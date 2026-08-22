"""Post-update notice for the enforce_decision_signing switch (plan step A5 / A6 gap).

The independent verify pass (2026-08-22) found that A6's claimed "notice path" test did not
exist: `_emit_agent_privilege_config` prints the default-on notice + names the opt-out, but
nothing asserted it. A notice a human is meant to read at --update time is behavioral surface;
an untested one silently regresses. These close that gap.
"""

from __future__ import annotations

import json

from agentteams.cli.artifacts import AGENT_PRIVILEGE_REL_PATH
from agentteams.cli.generate import _emit_agent_privilege_config


def test_notice_on_names_the_opt_out_and_warns_about_existing_rows(tmp_path, capsys):
    """Switch ON (the default): the notice must fire, say enforcement is ON, warn that existing
    unsigned rows stop clearing, and name the exact opt-out the brief accepts."""
    _emit_agent_privilege_config({"enforce_decision_signing": True}, tmp_path)
    out = capsys.readouterr().out

    assert "enforce_decision_signing" in out
    assert "ON" in out
    assert "fail-closed" in out
    # names the opt-out precisely (the "notify after, opportunity to switch off" contract):
    assert '"enforce_decision_signing": false' in out
    assert "re-run --update" in out
    # warns that EXISTING unsigned PASS / HALT-RETRACTED rows will stop clearing:
    assert "HALT-RETRACTED" in out or "already in this workspace" in out
    # and the config was actually emitted ON:
    cfg = json.loads((tmp_path / AGENT_PRIVILEGE_REL_PATH).read_text())
    assert cfg["enforce_decision_signing"] is True


def test_notice_off_states_legacy_behavior(tmp_path, capsys):
    """Switch explicitly OFF: the notice states OFF / legacy behavior, does not claim fail-closed."""
    _emit_agent_privilege_config({"enforce_decision_signing": False}, tmp_path)
    out = capsys.readouterr().out

    assert "OFF" in out
    assert "legacy" in out.lower()
    assert "fail-closed" not in out
    cfg = json.loads((tmp_path / AGENT_PRIVILEGE_REL_PATH).read_text())
    assert cfg["enforce_decision_signing"] is False


def test_absent_switch_emits_nothing(tmp_path, capsys):
    """An older manifest with no switch key writes no config and prints no notice — a workspace
    that predates the feature must not be surprised (absent == OFF, silently)."""
    _emit_agent_privilege_config({}, tmp_path)
    out = capsys.readouterr().out

    assert out.strip() == ""
    assert not (tmp_path / AGENT_PRIVILEGE_REL_PATH).exists()
