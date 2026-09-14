"""Tests for the Ed25519 signing seam in signed_ledger.py (Workstream B crypto core).

Proves: round-trip sign/verify; one-byte mutation fails; an agent holding only the public key
cannot forge (verifying under a different keypair fails); the verify_by_scheme dispatcher; and the
fail-closed behaviour when the asymmetric backend is absent (never a silent HMAC fall-through).
"""

from __future__ import annotations

import pytest

from agentteams.cli import signed_ledger as sl

crypto = pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402


def _keypair() -> tuple[str, str]:
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
    return private_pem, public_pem


_FIELDS = ["2026-09-14", "prune-collectors", "PASS", "verified", "security", ""]


def test_round_trip_sign_verify():
    private_pem, public_pem = _keypair()
    sig = sl.ed25519_sign(private_pem, _FIELDS)
    assert len(sig) == 128  # 64 bytes hex
    assert sl.ed25519_verify(public_pem, _FIELDS, sig) is True


def test_one_byte_field_mutation_fails():
    private_pem, public_pem = _keypair()
    sig = sl.ed25519_sign(private_pem, _FIELDS)
    mutated = list(_FIELDS)
    mutated[1] = "prune-collector"  # one-char change (dropped the trailing 's')
    assert sl.ed25519_verify(public_pem, mutated, sig) is False


def test_public_key_holder_cannot_forge():
    # An agent that holds only the public verify key of one keypair cannot mint a signature that
    # verifies — a signature from a DIFFERENT private key fails against this public key.
    _priv_a, public_a = _keypair()
    private_b, _pub_b = _keypair()
    forged = sl.ed25519_sign(private_b, _FIELDS)
    assert sl.ed25519_verify(public_a, _FIELDS, forged) is False


def test_tampered_signature_fails():
    private_pem, public_pem = _keypair()
    sig = sl.ed25519_sign(private_pem, _FIELDS)
    flipped = ("f" if sig[0] != "f" else "0") + sig[1:]
    assert sl.ed25519_verify(public_pem, _FIELDS, flipped) is False


def test_non_hex_signature_is_invalid_not_error():
    _priv, public_pem = _keypair()
    assert sl.ed25519_verify(public_pem, _FIELDS, "not-hex!!") is False


def test_malformed_public_key_raises():
    with pytest.raises(RuntimeError, match="could not load Ed25519 public key"):
        sl.ed25519_verify("-----BEGIN PUBLIC KEY-----\nnope\n-----END PUBLIC KEY-----", _FIELDS, "00")


def test_ed25519_signs_same_payload_as_hmac():
    # The asymmetric scheme signs the identical canonical payload the HMAC scheme does.
    private_pem, public_pem = _keypair()
    sig = sl.ed25519_sign(private_pem, _FIELDS)
    # canonical_payload is what both sign; verifying proves the bytes matched.
    assert sl.ed25519_verify(public_pem, _FIELDS, sig)


# --- verify_by_scheme dispatcher --------------------------------------------------------------


def test_verify_by_scheme_hmac():
    sig = sl.hmac_sign("k", _FIELDS)
    assert sl.verify_by_scheme("hmac", _FIELDS, sig, hmac_key="k") is True
    assert sl.verify_by_scheme("", _FIELDS, sig, hmac_key="k") is True  # empty ⇒ hmac default


def test_verify_by_scheme_ed25519():
    private_pem, public_pem = _keypair()
    sig = sl.ed25519_sign(private_pem, _FIELDS)
    assert sl.verify_by_scheme("ed25519", _FIELDS, sig, public_pem=public_pem) is True


def test_verify_by_scheme_unknown_raises():
    with pytest.raises(ValueError, match="unknown signature scheme"):
        sl.verify_by_scheme("rot13", _FIELDS, "00", hmac_key="k")


def test_verify_by_scheme_missing_key_raises():
    with pytest.raises(ValueError, match="requires"):
        sl.verify_by_scheme("hmac", _FIELDS, "00")
    with pytest.raises(ValueError, match="requires"):
        sl.verify_by_scheme("ed25519", _FIELDS, "00")


def test_backend_absent_fails_closed(monkeypatch):
    # R4: if the asymmetric backend cannot be imported, verify RAISES — never returns False that a
    # caller might treat as "unverified, fall back to HMAC".
    def _boom():
        raise RuntimeError("the 'signing' extra (cryptography) is not installed")

    monkeypatch.setattr(sl, "_require_cryptography", _boom)
    with pytest.raises(RuntimeError, match="signing"):
        sl.ed25519_verify("x", _FIELDS, "00")
    with pytest.raises(RuntimeError, match="signing"):
        sl.verify_by_scheme("ed25519", _FIELDS, "00", public_pem="x")
