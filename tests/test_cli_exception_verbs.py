"""CLI tests for the exception-registry verbs and the operator Ed25519 sign verb (WS-C/B/F)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import build_team


def _seed_registry(root: Path, exceptions: list[dict], cap: int = 5) -> None:
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / "references" / "exception-registry.json").write_text(
        json.dumps({"cap": {"max_active_relaxing": cap}, "exceptions": exceptions}),
        encoding="utf-8",
    )


def test_list_exceptions_empty(tmp_path, capsys):
    assert build_team.main(["--list-exceptions", "--output", str(tmp_path)]) == 0
    assert "No exceptions recorded" in capsys.readouterr().out


def test_audit_exceptions_healthy(tmp_path):
    _seed_registry(tmp_path, [{"id": "e1", "scope": "grant-x", "status": "active",
                               "expires": "2099-01-01T00:00:00Z"}])
    assert build_team.main(["--audit-exceptions", "--output", str(tmp_path)]) == 0


def test_audit_exceptions_lapsed_exits_nonzero(tmp_path):
    _seed_registry(tmp_path, [{"id": "e1", "scope": "grant-x", "status": "active",
                               "expires": "2000-01-01T00:00:00Z"}])
    assert build_team.main(["--audit-exceptions", "--output", str(tmp_path)]) == 1


def test_list_exceptions_shows_entries(tmp_path, capsys):
    _seed_registry(tmp_path, [{"id": "e1", "scope": "grant-x", "status": "declined",
                               "expires": "2099-01-01T00:00:00Z"}])
    assert build_team.main(["--list-exceptions", "--output", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "e1" in out and "declined" in out


# --- sign-decision (operator Ed25519 minter) --------------------------------------------------

crypto = pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402


def _keypair(root: Path, key_id: str = "op-2026") -> Path:
    (root / "references" / "authorized-verify-keys").mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    keyfile = root / "op.key"
    keyfile.write_bytes(priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (root / "references" / "authorized-verify-keys" / f"{key_id}.pub.pem").write_bytes(
        priv.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return keyfile


def test_sign_decision_requires_keyfile_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", raising=False)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"action_reviewed": "grant-x", "effect_grants": "write:cross-repo"}))
    assert build_team.main(["--sign-decision", str(spec), "--output", str(tmp_path)]) == 1


def test_sign_decision_mints_and_appends(tmp_path, monkeypatch):
    keyfile = _keypair(tmp_path)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(keyfile))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "date": "2026-09-14", "action_reviewed": "grant-cross-repo-write", "verdict": "PASS",
        "effect_grants": "write:cross-repo", "derives_from": "approved-cleanup",
        "key_id": "op-2026", "author": "security",
    }))
    assert build_team.main(["--sign-decision", str(spec), "--output", str(tmp_path)]) == 0
    log = (tmp_path / "references" / "security-decisions.log.csv").read_text()
    assert "ed25519" in log and "grant-cross-repo-write" in log


def test_sign_decision_refuses_non_eligible(tmp_path, monkeypatch):
    keyfile = _keypair(tmp_path)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(keyfile))
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({
        "action_reviewed": "grant-verify-key", "verdict": "PASS",
        "effect_targets": "references/authorized-verify-keys/x.pub.pem", "key_id": "op-2026",
    }))
    assert build_team.main(["--sign-decision", str(spec), "--output", str(tmp_path)]) == 1
