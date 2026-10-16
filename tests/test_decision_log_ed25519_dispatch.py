"""rc.11: an authorizing row is verified by the scheme it SIGNS, not only on the elevated path.

Before rc.11, ``_assert_authorizing_row_is_authentic`` took the Ed25519 path only for a governed,
constraint-relaxing, non-retraction row. Every other row fell through to the HMAC check, which raises
without ``AGENTTEAMS_DECISION_SIGNING_KEY``. So an operator Ed25519-signed ``HALT-RETRACTED`` could
never clear a HALT, an Ed25519 PASS was HMAC-checked when ``enforce_decision_signing`` was off, and
consumers had to put the symmetric secret in every render environment (found by mathAgents at rc.10).

Now a row whose signed ``sig_scheme`` is ``ed25519`` is always verified against the team's
``references/authorized-verify-keys/<key_id>.pub.pem`` and never falls back to HMAC. HMAC rows, the
elevated-path requirement and the retraction evidence rule are unchanged.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agentteams.cli import decision_log as dl
from agentteams.cli.security_gate import check_clearance

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams.cli import signed_ledger as sl  # noqa: E402

_KEY_ID = "op-rc11"
_HMAC_KEY = "rc11-hmac-key"
_COLUMNS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions",
            "conditions_verified", "evidence", "author", "derives_from", "effect_grants", "sig_scheme",
            "key_id", "signature"]
_LOG = "references/security-decisions.log.csv"


@pytest.fixture(autouse=True)
def _no_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTTEAMS_DECISION_SIGNING_KEY", raising=False)


def _team(root: Path, *, governed: bool) -> str:
    """Install one verify key and the signing switch; return the private PEM."""
    refs = root / "references"
    (refs / "authorized-verify-keys").mkdir(parents=True, exist_ok=True)
    (refs / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": governed}),
                                               encoding="utf-8")
    priv = Ed25519PrivateKey.generate()
    (refs / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode("utf-8")


def _row(action: str, verdict: str, **over: str) -> dict[str, str]:
    row = {"timestamp": "2026-10-16T00:00:00Z", "requesting_agent": "security", "action_reviewed": action,
           "verdict": verdict, "conditions": "", "conditions_verified": "verified", "evidence": "",
           "author": "", "derives_from": "", "effect_grants": "", "sig_scheme": "", "key_id": "",
           "signature": ""}
    row.update(over)
    return row


def _ed25519(row: dict[str, str], private_pem: str, key_id: str = _KEY_ID) -> dict[str, str]:
    row = {**row, "sig_scheme": "ed25519", "key_id": key_id}
    row["signature"] = sl.ed25519_sign(private_pem, dl._decision_signature_values(row))
    return row


def _write(root: Path, rows: list[dict[str, str]]) -> None:
    with (root / _LOG).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _authentic(row: dict[str, str], root: Path, action: str) -> None:
    dl._assert_authorizing_row_is_authentic(row, output_dir=root, signing_active=True, action=action)


@pytest.mark.parametrize("governed", [True, False])
def test_an_ed25519_retraction_clears_its_halt_without_an_hmac_key(tmp_path: Path, governed: bool) -> None:
    """SENSITIVITY: before rc.11 this raised "decision signing key is not configured"."""
    private_pem = _team(tmp_path, governed=governed)
    retraction = _ed25519(_row("restore-backup", "HALT-RETRACTED", evidence="backup verified"), private_pem)
    _write(tmp_path, [_row("restore-backup", "HALT"), retraction])
    dl._assert_no_unretracted_halt(tmp_path, action="restore-backup")


def test_an_ed25519_retraction_still_needs_evidence(tmp_path: Path) -> None:
    private_pem = _team(tmp_path, governed=True)
    _write(tmp_path, [_row("restore-backup", "HALT"),
                      _ed25519(_row("restore-backup", "HALT-RETRACTED"), private_pem)])
    with pytest.raises(RuntimeError, match="carries no evidence"):
        dl._assert_no_unretracted_halt(tmp_path, action="restore-backup")


def test_an_ed25519_pass_clears_with_enforcement_off_and_no_hmac_key(tmp_path: Path) -> None:
    private_pem = _team(tmp_path, governed=False)
    row = _ed25519(_row("grant-cross-repo-write", "PASS", effect_grants="write:cross-repo"), private_pem)
    _write(tmp_path, [row])
    allowed, reason = check_clearance(tmp_path, action="grant-cross-repo-write")
    assert allowed, reason


@pytest.mark.parametrize("governed", [True, False])
def test_a_tampered_ed25519_row_fails_with_no_hmac_fallback(tmp_path: Path, monkeypatch, governed: bool) -> None:
    """SENSITIVITY: a key holder's valid HMAC over the same payload is never accepted for an ed25519 row."""
    private_pem = _team(tmp_path, governed=governed)
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", _HMAC_KEY)
    row = _ed25519(_row("restore-backup", "HALT-RETRACTED", evidence="ok"), private_pem)
    with pytest.raises(RuntimeError, match="failed Ed25519 verification"):
        _authentic({**row, "conditions_verified": "forged"}, tmp_path, "restore-backup")
    hmac_signed = {**row, "signature": dl._hmac_decision_row(row, key=_HMAC_KEY)}
    with pytest.raises(RuntimeError, match="Ed25519"):
        _authentic(hmac_signed, tmp_path, "restore-backup")


def test_an_unknown_key_id_fails(tmp_path: Path) -> None:
    private_pem = _team(tmp_path, governed=False)
    row = _ed25519(_row("restore-backup", "HALT-RETRACTED", evidence="ok"), private_pem, key_id="nobody")
    with pytest.raises(RuntimeError, match="no Ed25519 verify key"):
        _authentic(row, tmp_path, "restore-backup")


def test_the_text_digest_is_still_checked_on_an_ed25519_row(tmp_path: Path) -> None:
    from agentteams.cli.decision_text import bind_text_digest

    private_pem = _team(tmp_path, governed=False)
    row = _ed25519(bind_text_digest(_row("restore-backup", "HALT-RETRACTED", evidence="ok")), private_pem)
    _authentic(row, tmp_path, "restore-backup")
    with pytest.raises(RuntimeError, match="edited after signing"):
        _authentic({**row, "evidence": "forged"}, tmp_path, "restore-backup")


def test_sig_scheme_is_signed_so_a_keyless_relabel_fails_both_ways(tmp_path: Path, monkeypatch) -> None:
    assert "sig_scheme" in dl._OPTIONAL_SIGNED_AXES and "key_id" in dl._OPTIONAL_SIGNED_AXES
    private_pem = _team(tmp_path, governed=False)
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", _HMAC_KEY)
    ed_row = _ed25519(_row("prune", "PASS"), private_pem)
    with pytest.raises(RuntimeError, match="failed signature verification"):
        _authentic({**ed_row, "sig_scheme": "hmac"}, tmp_path, "prune")     # ed25519 -> hmac
    hmac_row = _row("prune", "PASS", sig_scheme="hmac")
    hmac_row["signature"] = dl._hmac_decision_row(hmac_row, key=_HMAC_KEY)
    with pytest.raises(RuntimeError, match="Ed25519"):
        _authentic({**hmac_row, "sig_scheme": "ed25519", "key_id": _KEY_ID}, tmp_path, "prune")


def test_hmac_rows_are_unchanged(tmp_path: Path, monkeypatch) -> None:
    """NEGATIVE CONTROL: an HMAC row verifies with the key, and still needs it."""
    _team(tmp_path, governed=False)
    row = _row("prune", "PASS")
    row["signature"] = dl._hmac_decision_row(row, key=_HMAC_KEY)
    with pytest.raises(RuntimeError, match="signing key is not configured"):
        _authentic(row, tmp_path, "prune")
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", _HMAC_KEY)
    _authentic(row, tmp_path, "prune")
    with pytest.raises(RuntimeError, match="failed signature verification"):
        _authentic({**row, "conditions_verified": "forged"}, tmp_path, "prune")


def test_a_governed_relaxing_row_with_hmac_is_still_refused(tmp_path: Path, monkeypatch) -> None:
    _team(tmp_path, governed=True)
    _write(tmp_path, [_row("approved-cleanup", "PASS")])   # the recorded lineage ancestor
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", _HMAC_KEY)
    row = _row("grant-cross-repo-write", "PASS", effect_grants="write:cross-repo",
               derives_from="approved-cleanup", sig_scheme="hmac")
    row["signature"] = dl._hmac_decision_row(row, key=_HMAC_KEY)
    with pytest.raises(RuntimeError, match="REQUIRES ed25519"):
        _authentic(row, tmp_path, "grant-cross-repo-write")


def test_an_unsigned_historical_retraction_does_not_wedge_a_later_signed_one(tmp_path: Path) -> None:
    """rc.12 (mathAgents, rc.11): an unsigned HALT-RETRACTED written before migration made the scan raise.

    In an append-only signed log it could never be removed, so the action could never be cleared.
    An unauthenticated retraction now retracts nothing and is skipped; a later valid one clears.
    """
    private_pem = _team(tmp_path, governed=False)
    _write(tmp_path, [_row("overwrite-replay-record", "HALT"),
                      _row("overwrite-replay-record", "HALT-RETRACTED", evidence="written before signing"),
                      _ed25519(_row("overwrite-replay-record", "HALT-RETRACTED", evidence="re-retracted"),
                               private_pem)])
    dl._assert_no_unretracted_halt(tmp_path, action="overwrite")


def test_an_unsigned_retraction_alone_leaves_the_halt_open_and_says_why(tmp_path: Path) -> None:
    _team(tmp_path, governed=False)
    _write(tmp_path, [_row("overwrite-replay-record", "HALT"),
                      _row("overwrite-replay-record", "HALT-RETRACTED", evidence="written before signing")])
    with pytest.raises(RuntimeError, match=r"has never been retracted.*row 2 not accepted"):
        dl._assert_no_unretracted_halt(tmp_path, action="overwrite")
