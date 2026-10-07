"""The newest matching clearance decides; a consumed one is never replaced by an older one (C-5, 2026-10-07).

Reported from mathAgents' P5 render: skipping consumed rows promoted an older, unconsumed PASS that a later
clearance had superseded, so that older clearance could be replayed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.cli.security_gate import _assert_destructive_action_allowed, _latest_security_decision


def _log(output_dir: Path, rows: list[tuple[str, str, str, str]]) -> None:
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    header = "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified,consumed\n"
    body = "".join(f"{ts},security,{action},{verdict},,verified,{consumed}\n" for ts, action, verdict, consumed in rows)
    (refs / "security-decisions.log.csv").write_text(header + body, encoding="utf-8")


def test_a_superseded_pass_is_not_replayed_once_its_successor_is_consumed(tmp_path):
    _log(tmp_path, [("2026-10-07T10:00:00Z", "prune-001", "PASS", ""),       # older, never used
                    ("2026-10-07T11:00:00Z", "prune-002", "PASS", "yes")])   # newer, already used
    assert _latest_security_decision(tmp_path, action="prune") is None
    with pytest.raises(RuntimeError, match="newest one is already consumed"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_the_newest_unconsumed_pass_still_clears(tmp_path):
    _log(tmp_path, [("2026-10-07T10:00:00Z", "prune-001", "PASS", "yes"),
                    ("2026-10-07T11:00:00Z", "prune-002", "PASS", "")])
    assert _latest_security_decision(tmp_path, action="prune")["action_reviewed"] == "prune-002"
    _assert_destructive_action_allowed(tmp_path, action="prune")


def test_a_consumed_row_for_another_action_does_not_block(tmp_path):
    _log(tmp_path, [("2026-10-07T10:00:00Z", "prune-001", "PASS", ""),
                    ("2026-10-07T11:00:00Z", "overwrite-001", "PASS", "yes")])
    assert _latest_security_decision(tmp_path, action="prune")["action_reviewed"] == "prune-001"


def test_consuming_the_newest_blocks_a_second_use(tmp_path):
    _log(tmp_path, [("2026-10-07T10:00:00Z", "prune-001", "PASS", ""),
                    ("2026-10-07T11:00:00Z", "prune-002", "PASS", "")])
    _assert_destructive_action_allowed(tmp_path, action="prune", consume=True)   # uses prune-002
    with pytest.raises(RuntimeError, match="already consumed"):
        _assert_destructive_action_allowed(tmp_path, action="prune")             # prune-001 can't stand in
