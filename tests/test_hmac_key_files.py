"""HMAC keys from key files (hmac-key-autoresolve): the @security ruling's five conditions, one test each.

Design and ruling: references/plans/hmac-key-autoresolve.plan.md (local). The variable wins; otherwise
``~/.config/agentteams/keys/<kind>-hmac.key`` is read under proposals.use_key_file's custody rules, and any
failure refuses.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import agentteams.cli.signed_ledger as sl
from agentteams.cli import decision_log, grants, management_directives, security_gate

KEY = "k" * 16 + "0123456789abcdef0123456789abcdef"   # 48 bytes, not one repeated character
WAIVER = "AGENTTEAMS_WAIVER_SIGNING_KEY"


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty key dir the helper reads, the install-location check passing, and no HMAC env keys."""
    d = tmp_path / "keys"
    d.mkdir(mode=0o700)
    monkeypatch.setattr(sl, "hmac_key_dir", lambda: d)
    monkeypatch.setattr(sl, "_file_key_location_refusals", lambda: [])
    for name in sl.HMAC_KEY_FILES:
        monkeypatch.delenv(name, raising=False)
    return d


def _put(keys: Path, env: str, text: str = KEY, mode: int = 0o600) -> Path:
    p = keys / sl.HMAC_KEY_FILES[env]
    p.write_text(text + "\n", encoding="utf-8")
    p.chmod(mode)
    return p


def test_no_env_no_file_is_unchanged(keys):
    assert sl.load_hmac_key(WAIVER) == ""


def test_the_file_is_read_when_the_env_is_unset(keys):
    _put(keys, WAIVER)
    assert sl.load_hmac_key(WAIVER) == KEY


def test_env_wins_and_both_present_warns_once_naming_the_variable(keys, monkeypatch, capsys):
    """Condition 2: with the file present, a set variable still wins but is named, so it can be unset."""
    _put(keys, WAIVER, "f" * 8 + KEY)
    monkeypatch.setenv(WAIVER, KEY)
    assert sl.load_hmac_key(WAIVER) == KEY
    assert sl.load_hmac_key(WAIVER) == KEY
    err = capsys.readouterr().err
    assert err.count(f"Warning: {WAIVER} is set and") == 1 and "unset it" in err


def test_env_alone_prints_nothing(keys, monkeypatch, capsys):
    monkeypatch.setenv(WAIVER, KEY)
    assert sl.load_hmac_key(WAIVER) == KEY
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_file_others_can_read_is_refused(keys, mode):
    _put(keys, WAIVER, mode=mode)
    with pytest.raises(RuntimeError, match="mode 0600"):
        sl.load_hmac_key(WAIVER)


def test_a_symlinked_key_file_is_refused(keys, tmp_path):
    real = tmp_path / "elsewhere.key"
    real.write_text(KEY, encoding="utf-8")
    real.chmod(0o600)
    (keys / sl.HMAC_KEY_FILES[WAIVER]).symlink_to(real)
    with pytest.raises(RuntimeError, match="cannot be read"):
        sl.load_hmac_key(WAIVER)


def test_a_symlinked_key_dir_is_refused(tmp_path, monkeypatch):
    """Condition 1: the file must sit in the key directory itself, not behind a link to somewhere else."""
    real = tmp_path / "agent-writable"
    real.mkdir()
    link = tmp_path / "keys"
    link.symlink_to(real)
    monkeypatch.setattr(sl, "hmac_key_dir", lambda: link)
    monkeypatch.setattr(sl, "_file_key_location_refusals", lambda: [])
    monkeypatch.delenv(WAIVER, raising=False)
    _put(link, WAIVER)
    with pytest.raises(RuntimeError, match="not behind a symlink"):
        sl.load_hmac_key(WAIVER)


def test_an_empty_file_is_refused(keys):
    _put(keys, WAIVER, text="")
    with pytest.raises(RuntimeError, match="is empty"):
        sl.load_hmac_key(WAIVER)


def test_a_file_key_is_refused_from_an_install_an_agent_could_edit(keys, monkeypatch):
    """Q3: refuse, not warn, as the Ed25519 signers do; the variable stays usable."""
    _put(keys, WAIVER)
    monkeypatch.setattr(sl, "_file_key_location_refusals", lambda: ["the agentteams package (/x) is inside a git work tree"])
    with pytest.raises(RuntimeError, match="install an agent may be able to edit"):
        sl.load_hmac_key(WAIVER)
    monkeypatch.setenv(WAIVER, KEY)
    assert sl.load_hmac_key(WAIVER) == KEY


def test_the_error_type_is_the_callers(keys):
    _put(keys, "AGENTTEAMS_GRANT_SIGNING_KEY", mode=0o644)
    with pytest.raises(grants.GrantError):
        grants._signing_key()


def test_grant_and_management_keys_come_from_their_files(keys):
    _put(keys, "AGENTTEAMS_GRANT_SIGNING_KEY")
    _put(keys, "AGENTTEAMS_MANAGEMENT_SIGNING_KEY", "m" * 8 + KEY)
    assert grants._signing_key() == KEY
    assert management_directives._signing_key() == "m" * 8 + KEY


def test_the_decision_key_file_signs_but_does_not_switch_enforcement_on(keys):
    """Condition 3: _decision_signing_active stays tied to the variable and the log header, never the file."""
    _put(keys, "AGENTTEAMS_DECISION_SIGNING_KEY")
    assert decision_log._decision_signing_active(["date", "action_reviewed", "verdict"]) is False
    row = {"date": "2026-11-14", "action_reviewed": "x", "verdict": "PASS", "conditions": "", "author": "security"}
    assert decision_log._hmac_decision_row(dict(row)) == decision_log._hmac_decision_row(dict(row), key=KEY)


def _waiver() -> dict[str, str]:
    return {"action_reviewed": "x", "conditions_verified": "verified", "approver": "alice", "ticket_id": "T-1",
            "reason_code": "r", "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "max_uses": "5", "uses": "0", "signature": "0" * 64}


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_waiver_with_an_unreadable_key_file_and_no_env_fails_closed(keys):
    """Condition 5: inside a session sandbox the key file is read-denied; a waiver check must refuse, not pass."""
    _put(keys, WAIVER, mode=0o000)
    with pytest.raises(RuntimeError, match="waiver|cannot be read|mode 0600"):
        security_gate._validate_security_waiver(_waiver(), action="x")


def test_a_waiver_with_no_key_anywhere_is_not_configured(keys):
    with pytest.raises(RuntimeError, match="not configured.*waiver-hmac.key"):
        security_gate._validate_security_waiver(_waiver(), action="x")
