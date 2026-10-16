"""rc.12 (masterTeacher 4.1): an HMAC signing key must be strong; placeholders are refused.

A placeholder key such as ``...`` once signed a decision that read as verified. Anyone who knows the
placeholder can forge that signature, so signing AND verifying refuse a weak key, for decisions and
for waivers alike.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import pytest

from agentteams.cli import decision_log as dl
from agentteams.cli import signed_ledger as sl
from agentteams.cli.security_gate import _assert_destructive_action_allowed, verify_waivers

_FIELDS = ["a", "b", "c"]


def _decision_row() -> dict[str, str]:
    return {
        "date": "2026-10-16", "action_reviewed": "draft-weekly-report", "verdict": "PASS",
        "conditions_verified": "verified", "owner": "security", "prev_digest": "", "signature": "",
    }


@pytest.mark.parametrize("weak", [
    "...", "changeme", "secret", "password", "placeholder",
    "short-key", "rc11-hmac-key", "k",          # under 32 bytes
    "a" * 64, "." * 40,                          # one repeated character, however long
    " " * 32,                                    # whitespace only
])
def test_weak_keys_are_refused(weak):
    with pytest.raises(RuntimeError, match="too weak"):
        sl.require_strong_hmac_key(weak)
    with pytest.raises(RuntimeError, match="too weak"):
        sl.hmac_sign(weak, _FIELDS)
    with pytest.raises(RuntimeError, match="too weak"):
        sl.hmac_verify(weak, _FIELDS, "00" * 32)


def test_the_placeholder_denylist_is_case_and_space_insensitive():
    for denied in sorted(sl._PLACEHOLDER_KEYS):
        padded = denied.upper().center(sl.MIN_HMAC_KEY_BYTES + 2)  # long enough by length alone
        with pytest.raises(RuntimeError, match="too weak"):
            sl.require_strong_hmac_key(padded)


def test_a_32_byte_random_key_signs_and_verifies():
    key = secrets.token_hex(16)  # 32 ASCII bytes: exactly the floor
    assert len(key.encode()) == sl.MIN_HMAC_KEY_BYTES
    assert sl.require_strong_hmac_key(key) == key
    sig = sl.hmac_sign(key, _FIELDS)
    assert sl.hmac_verify(key, _FIELDS, sig)
    assert not sl.hmac_verify(key, ["a", "b", "X"], sig)
    assert not sl.hmac_verify(secrets.token_hex(32), _FIELDS, sig)


def test_signing_a_decision_with_a_placeholder_raises_and_writes_nothing(tmp_path, monkeypatch):
    row = _decision_row()
    with pytest.raises(RuntimeError, match="decision signing key is too weak"):
        row["signature"] = dl.sign_decision_row(row, key="...")
        dl.append_signed_decision_row(tmp_path, row)
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", "...")
    with pytest.raises(RuntimeError, match="decision signing key is too weak"):
        row["signature"] = dl.sign_decision_row(row)  # the env key
        dl.append_signed_decision_row(tmp_path, row)
    assert row["signature"] == ""
    assert not (tmp_path / "references" / "security-decisions.log.csv").exists()


def test_a_row_signed_with_a_short_key_fails_verification(tmp_path, monkeypatch):
    """A legacy row HMAC'd under a placeholder must not verify, even with the matching key."""
    weak = "..."
    row = _decision_row()
    row["signature"] = hmac.new(  # what the pre-rc.12 signer produced
        weak.encode(), dl._decision_signature_payload(row).encode(), hashlib.sha256
    ).hexdigest()
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", weak)
    with pytest.raises(RuntimeError, match="too weak"):
        dl._assert_authorizing_row_is_authentic(
            row, output_dir=tmp_path, signing_active=True, action="draft-weekly-report"
        )
    # Re-signed under a strong key, the same row verifies.
    strong = secrets.token_hex(32)
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", strong)
    row["signature"] = dl.sign_decision_row(dict(row, signature=""))
    dl._assert_authorizing_row_is_authentic(
        row, output_dir=tmp_path, signing_active=True, action="draft-weekly-report"
    )


_WAIVER_FIELDS = ["waiver_id", "action_reviewed", "expires_at", "max_uses", "uses", "approver",
                  "ticket_id", "reason_code", "conditions_verified"]
_WAIVER_COLUMNS = ["timestamp", *_WAIVER_FIELDS, "signature"]


def _write_waiver(root, key: str) -> None:
    now = datetime.now(UTC)
    w = {
        "timestamp": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "waiver_id": "w-weak", "action_reviewed": "prune",
        "expires_at": (now + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        "max_uses": "1", "uses": "0", "approver": "security", "ticket_id": "SEC-1",
        "reason_code": "maintenance", "conditions_verified": "verified",
    }
    payload = "|".join(w[f] for f in _WAIVER_FIELDS)
    w["signature"] = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "security-waivers.log.csv").write_text(
        ",".join(_WAIVER_COLUMNS) + "\n" + ",".join(w[c] for c in _WAIVER_COLUMNS) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("weak", ["...", "secret", "test-waiver-key", "x" * 48])
def test_the_waiver_path_refuses_a_weak_waiver_signing_key(tmp_path, monkeypatch, weak):
    _write_waiver(tmp_path, weak)  # validly HMAC'd under the weak key itself
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", weak)
    (report,) = verify_waivers(tmp_path)
    assert report["status"] == "invalid" and "waiver signing key is too weak" in report["detail"]
    with pytest.raises(RuntimeError):
        _assert_destructive_action_allowed(tmp_path, action="prune")


def test_the_waiver_path_accepts_a_strong_waiver_signing_key(tmp_path, monkeypatch):
    strong = secrets.token_hex(32)
    _write_waiver(tmp_path, strong)
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", strong)
    (report,) = verify_waivers(tmp_path)
    assert report["status"] == "valid", report
