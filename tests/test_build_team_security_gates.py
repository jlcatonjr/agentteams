"""Tests for runtime security gates in build_team.py."""

from pathlib import Path

import pytest

from build_team import _action_matches, _assert_destructive_action_allowed


def _write_security_log(output_dir: Path, rows: list[list[str]]) -> None:
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    log_path = refs / "security-decisions.log.csv"
    header = (
        "timestamp,requesting_agent,action_reviewed,verdict,"
        "conditions,conditions_verified\n"
    )
    body = "\n".join([",".join(r) for r in rows])
    if body:
        body += "\n"
    log_path.write_text(header + body, encoding="utf-8")


def test_gate_blocks_when_log_missing(tmp_path):
    with pytest.raises(RuntimeError, match="no matching PASS"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_blocks_halt_verdict(tmp_path):
    _write_security_log(
        tmp_path,
        [["2026-04-22T10:00:00Z", "security", "prune-001", "HALT", "", "pending"]],
    )
    with pytest.raises(RuntimeError, match="HALT"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_blocks_unverified_conditional_pass(tmp_path):
    _write_security_log(
        tmp_path,
        [["2026-04-22T10:00:00Z", "security", "prune-001", "CONDITIONAL PASS", "backup", "pending"]],
    )
    with pytest.raises(RuntimeError, match="unverified conditions"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_allows_verified_conditional_pass(tmp_path):
    _write_security_log(
        tmp_path,
        [["2026-04-22T10:00:00Z", "security", "prune-001", "CONDITIONAL PASS", "backup", "verified"]],
    )
    _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_uses_latest_matching_decision(tmp_path):
    _write_security_log(
        tmp_path,
        [
            ["2026-04-22T09:00:00Z", "security", "prune-001", "PASS", "", "verified"],
            ["2026-04-22T11:00:00Z", "security", "prune-002", "HALT", "", "pending"],
        ],
    )
    with pytest.raises(RuntimeError, match="HALT"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_rejects_substring_confusion(tmp_path):
    _write_security_log(
        tmp_path,
        [["2026-04-22T09:00:00Z", "security", "unprune-001", "PASS", "", "verified"]],
    )
    with pytest.raises(RuntimeError, match="no matching PASS"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_blocks_malformed_log_header(tmp_path):
    refs = tmp_path / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed\n"
        "2026-04-22T10:00:00Z,security,prune-001\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="malformed"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_action_matches_tokenized_action_names():
    assert _action_matches("prune-001", "prune")
    assert _action_matches("restore-backup-001", "restore-backup")
    assert _action_matches("revert-migration-001", "revert-migration")
    assert not _action_matches("unprune-001", "prune")
    assert not _action_matches("do-not-prune", "prune")


def test_gate_blocks_missing_required_header_columns(tmp_path):
    refs = tmp_path / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "security-decisions.log.csv").write_text(
        "action_reviewed,verdict,conditions_verified\n"
        "prune-001,PASS,verified\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="expected exact header"):
        _assert_destructive_action_allowed(tmp_path, action="prune")
