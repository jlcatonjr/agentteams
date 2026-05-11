"""Tests for runtime security gates in build_team.py."""

import hashlib
import hmac
import os
from pathlib import Path

import pytest

from build_team import (
    _action_matches,
    _assert_destructive_action_allowed,
    _assert_security_intelligence_fresh,
)


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


def _waiver_signature(row: dict[str, str], key: str) -> str:
    payload = "|".join(
        [
            row["waiver_id"],
            row["action_reviewed"],
            row["expires_at"],
            row["max_uses"],
            row["uses"],
            row["approver"],
            row["ticket_id"],
            row["reason_code"],
            row["conditions_verified"],
        ]
    )
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _write_security_waiver_log(output_dir: Path, rows: list[dict[str, str]]) -> None:
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    log_path = refs / "security-waivers.log.csv"
    header = (
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
    )
    body = "\n".join(
        ",".join(
            [
                row["timestamp"],
                row["waiver_id"],
                row["action_reviewed"],
                row["expires_at"],
                row["max_uses"],
                row["uses"],
                row["approver"],
                row["ticket_id"],
                row["reason_code"],
                row["conditions_verified"],
                row["signature"],
            ]
        )
        for row in rows
    )
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


def test_gate_prefers_halt_over_waiver(tmp_path, monkeypatch):
    waiver_key = "test-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    _write_security_log(
        tmp_path,
        [["2026-04-22T10:00:00Z", "security", "prune-001", "HALT", "", "pending"]],
    )

    waiver = {
        "timestamp": "2026-05-10T10:00:00Z",
        "waiver_id": "waiver-prune-004",
        "action_reviewed": "prune",
        "expires_at": "2026-05-11T10:00:00Z",
        "max_uses": "1",
        "uses": "0",
        "approver": "security",
        "ticket_id": "SEC-1239",
        "reason_code": "maintenance",
        "conditions_verified": "verified",
        "signature": "",
    }
    waiver["signature"] = _waiver_signature(waiver, waiver_key)
    _write_security_waiver_log(tmp_path, [waiver])

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


def test_gate_allows_valid_signed_waiver(tmp_path, monkeypatch):
    waiver_key = "test-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-10T10:00:00Z",
        "waiver_id": "waiver-prune-001",
        "action_reviewed": "prune",
        "expires_at": "2026-05-11T10:00:00Z",
        "max_uses": "1",
        "uses": "0",
        "approver": "security",
        "ticket_id": "SEC-1234",
        "reason_code": "maintenance",
        "conditions_verified": "verified",
        "signature": "",
    }
    waiver["signature"] = _waiver_signature(waiver, waiver_key)
    _write_security_waiver_log(tmp_path, [waiver])

    _assert_destructive_action_allowed(tmp_path, action="prune")

    updated = (tmp_path / "references" / "security-waivers.log.csv").read_text(encoding="utf-8")
    assert ",1," in updated
    assert ",0," not in updated


def test_gate_rejects_replayed_signed_waiver(tmp_path, monkeypatch):
    waiver_key = "test-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-10T10:00:00Z",
        "waiver_id": "waiver-prune-001",
        "action_reviewed": "prune",
        "expires_at": "2026-05-11T10:00:00Z",
        "max_uses": "1",
        "uses": "0",
        "approver": "security",
        "ticket_id": "SEC-1234",
        "reason_code": "maintenance",
        "conditions_verified": "verified",
        "signature": "",
    }
    waiver["signature"] = _waiver_signature(waiver, waiver_key)
    _write_security_waiver_log(tmp_path, [waiver])

    _assert_destructive_action_allowed(tmp_path, action="prune")

    with pytest.raises(RuntimeError, match="use limit"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_blocks_expired_signed_waiver(tmp_path, monkeypatch):
    waiver_key = "test-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-10T10:00:00Z",
        "waiver_id": "waiver-prune-002",
        "action_reviewed": "prune",
        "expires_at": "2026-05-09T10:00:00Z",
        "max_uses": "1",
        "uses": "0",
        "approver": "security",
        "ticket_id": "SEC-1235",
        "reason_code": "maintenance",
        "conditions_verified": "verified",
        "signature": "",
    }
    waiver["signature"] = _waiver_signature(waiver, waiver_key)
    _write_security_waiver_log(tmp_path, [waiver])

    with pytest.raises(RuntimeError, match="expired"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_blocks_unsigned_waiver(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", "test-waiver-key")
    waiver = {
        "timestamp": "2026-05-10T10:00:00Z",
        "waiver_id": "waiver-prune-003",
        "action_reviewed": "prune",
        "expires_at": "2026-05-11T10:00:00Z",
        "max_uses": "1",
        "uses": "0",
        "approver": "security",
        "ticket_id": "SEC-1236",
        "reason_code": "maintenance",
        "conditions_verified": "verified",
        "signature": "deadbeef",
    }
    _write_security_waiver_log(tmp_path, [waiver])

    with pytest.raises(RuntimeError, match="signature"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_rejects_waiver_scope_mismatch(tmp_path, monkeypatch):
    waiver_key = "test-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-10T10:00:00Z",
        "waiver_id": "waiver-overwrite-001",
        "action_reviewed": "overwrite",
        "expires_at": "2026-05-11T10:00:00Z",
        "max_uses": "1",
        "uses": "0",
        "approver": "security",
        "ticket_id": "SEC-prune-1237",
        "reason_code": "maintenance",
        "conditions_verified": "verified",
        "signature": "",
    }
    waiver["signature"] = _waiver_signature(waiver, waiver_key)
    _write_security_waiver_log(tmp_path, [waiver])

    with pytest.raises(RuntimeError, match="no matching PASS"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_security_intelligence_freshness_blocks_stale_payload(tmp_path):
    placeholders = {
        "SECURITY_DATA_GENERATED_AT": "2026-05-08T10:00:00Z",
        "SECURITY_CURRENT_THREATS_SUMMARY": "summary",
        "SECURITY_PREVENTION_PLAYBOOK": "playbook",
    }

    with pytest.raises(RuntimeError, match="stale"):
        _assert_security_intelligence_fresh(placeholders, output_dir=tmp_path)
def test_security_intelligence_freshness_consumes_signed_waiver(tmp_path, monkeypatch):
    waiver_key = "test-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", waiver_key)
    waiver = {
        "timestamp": "2026-05-10T10:00:00Z",
        "waiver_id": "waiver-freshness-001",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2026-05-11T10:00:00Z",
        "max_uses": "1",
        "uses": "0",
        "approver": "security",
        "ticket_id": "SEC-1238",
        "reason_code": "maintenance",
        "conditions_verified": "verified",
        "signature": "",
    }
    waiver["signature"] = _waiver_signature(waiver, waiver_key)
    _write_security_waiver_log(tmp_path, [waiver])

    placeholders = {
        "SECURITY_DATA_GENERATED_AT": "2026-05-08T10:00:00Z",
        "SECURITY_CURRENT_THREATS_SUMMARY": "summary",
        "SECURITY_PREVENTION_PLAYBOOK": "playbook",
    }

    _assert_security_intelligence_fresh(placeholders, output_dir=tmp_path)

    with pytest.raises(RuntimeError, match="use limit"):
        _assert_security_intelligence_fresh(placeholders, output_dir=tmp_path)


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

    with pytest.raises(RuntimeError, match="expected either the legacy header"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_gate_accepts_current_repository_schema(tmp_path):
    refs = tmp_path / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "security-decisions.log.csv").write_text(
        "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner\n"
        "2026-05-04,groupa-formal-exclusion-2026-05-04,2a,documentation-only-exclusion-path,PASS,No writes,verified,plan.md,security\n",
        encoding="utf-8",
    )

    _assert_destructive_action_allowed(tmp_path, action="documentation-only-exclusion-path")


def test_gate_rejects_replayed_pass_decision(tmp_path):
    _write_security_log(
        tmp_path,
        [["2026-04-22T10:00:00Z", "security", "prune-001", "PASS", "", "verified"]],
    )

    _assert_destructive_action_allowed(tmp_path, action="prune")

    with pytest.raises(RuntimeError, match="no matching PASS"):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_security_intelligence_freshness_honors_explicit_stale_status(tmp_path):
    placeholders = {
        "SECURITY_DATA_GENERATED_AT": "2026-05-10T10:00:00Z",
        "SECURITY_DATA_FRESHNESS_STATUS": "stale",
        "SECURITY_CURRENT_THREATS_SUMMARY": "summary",
        "SECURITY_PREVENTION_PLAYBOOK": "playbook",
    }

    with pytest.raises(RuntimeError, match="stale"):
        _assert_security_intelligence_fresh(placeholders, output_dir=tmp_path)


def test_security_intelligence_freshness_rejects_malformed_timestamp(tmp_path):
    placeholders = {
        "SECURITY_DATA_GENERATED_AT": "not-a-timestamp",
        "SECURITY_DATA_FRESHNESS_STATUS": "fresh",
        "SECURITY_CURRENT_THREATS_SUMMARY": "summary",
        "SECURITY_PREVENTION_PLAYBOOK": "playbook",
    }

    freshness = _assert_security_intelligence_fresh
    with pytest.raises(RuntimeError, match="stale"):
        freshness(placeholders, output_dir=tmp_path)


def test_security_intelligence_freshness_rejects_future_timestamp(tmp_path):
    placeholders = {
        "SECURITY_DATA_GENERATED_AT": "2099-01-01T00:00:00Z",
        "SECURITY_DATA_FRESHNESS_STATUS": "fresh",
        "SECURITY_CURRENT_THREATS_SUMMARY": "summary",
        "SECURITY_PREVENTION_PLAYBOOK": "playbook",
    }

    with pytest.raises(RuntimeError, match="stale"):
        _assert_security_intelligence_fresh(placeholders, output_dir=tmp_path)
