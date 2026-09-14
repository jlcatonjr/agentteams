"""WS-B chokepoint policy: a governed workspace's RELAXING authorization requires Ed25519.

validity != sufficiency — a valid HMAC on a relaxing row is refused; only the operator's Ed25519
signature (verified against the tracked public key) clears it. sig_scheme/key_id are signed, so a
scheme relabel breaks verification. Non-relaxing rows keep the HMAC path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.cli import decision_log as dl

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams.cli import signed_ledger as sl  # noqa: E402

_KEY_ID = "op-2026"


def _govern_with_key(root: Path) -> str:
    """Govern the workspace and install one Ed25519 verify key; return the private PEM."""
    refs = root / "references"
    (refs / "authorized-verify-keys").mkdir(parents=True, exist_ok=True)
    (refs / "agent-privilege.json").write_text(
        json.dumps({"enforce_decision_signing": True}), encoding="utf-8"
    )
    priv = Ed25519PrivateKey.generate()
    private_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    (refs / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").write_text(public_pem, encoding="utf-8")
    return private_pem


def _relaxing_row(**over) -> dict[str, str]:
    # A constraint-relaxing EXCEPTION (grants a capability) — the class that requires operator
    # Ed25519 (a routine destructive action would NOT). Includes resolvable lineage + author.
    base = {
        "date": "2026-09-14",
        "action_reviewed": "grant-cross-repo-write",
        "verdict": "PASS",
        "conditions_verified": "verified",
        "owner": "security",
        "prev_digest": "",
        "derives_from": "approved-cleanup",
        "effect_grants": "write:cross-repo",
        "sig_scheme": "ed25519",
        "key_id": _KEY_ID,
        "signature": "",
    }
    base.update(over)
    return base


def _ed25519_sign(row: dict[str, str], private_pem: str) -> str:
    return sl.ed25519_sign(private_pem, dl._decision_signature_values(row))


def _log_with_ancestor(root: Path) -> None:
    # A recorded ancestor so the lineage traceability resolves (legacy schema header).
    (root / "references" / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-09-01T00:00:00Z,security,approved-cleanup,PASS,,verified\n",
        encoding="utf-8",
    )


def test_relaxing_row_accepted_with_valid_ed25519(tmp_path):
    private_pem = _govern_with_key(tmp_path)
    _log_with_ancestor(tmp_path)
    row = _relaxing_row()
    row["signature"] = _ed25519_sign(row, private_pem)
    dl._assert_authorizing_row_is_authentic(
        row, output_dir=tmp_path, signing_active=True, action="grant-cross-repo-write"
    )


def test_relaxing_row_with_hmac_is_insufficient(tmp_path):
    _govern_with_key(tmp_path)
    _log_with_ancestor(tmp_path)
    row = _relaxing_row(sig_scheme="hmac")
    # even a genuinely valid HMAC is refused for a relaxing row
    row["signature"] = dl.sign_decision_row(row, key="k")
    with pytest.raises(RuntimeError, match="REQUIRES ed25519|valid-but-INSUFFICIENT"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=True, action="grant-cross-repo-write"
        )


def test_relaxing_row_wrong_ed25519_signature_refused(tmp_path):
    _govern_with_key(tmp_path)
    _log_with_ancestor(tmp_path)
    other = Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    row = _relaxing_row()
    row["signature"] = _ed25519_sign(row, other)  # signed by the WRONG key
    with pytest.raises(RuntimeError, match="failed Ed25519 verification"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=True, action="grant-cross-repo-write"
        )


def test_scheme_relabel_breaks_verification(tmp_path):
    private_pem = _govern_with_key(tmp_path)
    _log_with_ancestor(tmp_path)
    row = _relaxing_row()
    row["signature"] = _ed25519_sign(row, private_pem)
    # Attacker relabels the (signed) scheme marker — payload changes → verification fails.
    row["key_id"] = "attacker-key"
    with pytest.raises(RuntimeError):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=True, action="grant-cross-repo-write"
        )


def test_missing_verify_key_makes_relaxing_path_unavailable(tmp_path):
    private_pem = _govern_with_key(tmp_path)
    _log_with_ancestor(tmp_path)
    row = _relaxing_row(key_id="no-such-key")
    row["signature"] = _ed25519_sign(row, private_pem)
    with pytest.raises(RuntimeError, match="no Ed25519 verify key|unavailable"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=True, action="grant-cross-repo-write"
        )


def test_nonrelaxing_row_keeps_hmac_path_when_governed(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", "k")
    _govern_with_key(tmp_path)
    # a benign action stays on the HMAC path even in a governed workspace
    row = {
        "date": "2026-09-14", "action_reviewed": "draft-weekly-report", "verdict": "PASS",
        "conditions_verified": "verified", "owner": "security", "prev_digest": "", "signature": "",
    }
    row["signature"] = dl.sign_decision_row(row)  # env key
    dl._assert_authorizing_row_is_authentic(
        row, output_dir=tmp_path, signing_active=True, action="draft-weekly-report"
    )


def test_governance_target_refused_even_with_valid_ed25519(tmp_path):
    # WS-E: a row targeting a governance/trust root is categorically NON-ELIGIBLE — refused even
    # with a valid operator Ed25519 signature (the non-eligibility check runs before signing).
    private_pem = _govern_with_key(tmp_path)
    _log_with_ancestor(tmp_path)
    row = _relaxing_row(
        action_reviewed="grant-verify-key-write",
        effect_grants="",
        effect_targets="references/authorized-verify-keys/attacker.pub.pem",
    )
    row["signature"] = _ed25519_sign(row, private_pem)  # a genuinely valid operator signature
    with pytest.raises(RuntimeError, match="NON-ELIGIBLE"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=True, action="grant-verify-key-write"
        )


def test_key_id_traversal_refused(tmp_path):
    private_pem = _govern_with_key(tmp_path)
    _log_with_ancestor(tmp_path)
    row = _relaxing_row(key_id="../../etc/passwd")
    row["signature"] = _ed25519_sign(row, private_pem)
    with pytest.raises(RuntimeError, match="missing/invalid key_id|escapes"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=True, action="grant-cross-repo-write"
        )
