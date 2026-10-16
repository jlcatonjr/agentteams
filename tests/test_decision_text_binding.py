"""A signed decision row binds its free-text ``conditions`` and ``evidence`` (``text_digest``).

Before this, ``conditions`` and ``evidence`` sat outside the signed payload, so a keyless writer
could rewrite a signed row's stated limits ("only these repos, expires 2027-01-16") while its HMAC
or Ed25519 signature and the hash chain still verified. ``text_digest`` is the last optional signed
axis: signed only when non-empty (every legacy signature still verifies byte-for-byte), and a row
whose digest no longer matches its text fails exactly like a bad signature.

Each verifier is tested in pairs: a sensitivity test (tampered text is refused) and a negative
control (an untampered row, or a legacy row with no digest, is accepted).
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

import build_team
from agentteams.cli import decision_log as dl
from agentteams.cli import decision_log_migrate as dlm
from agentteams.cli.decision_chain import row_chain_digest, verify_chain
from agentteams.cli.decision_text import (
    TEXT_DIGEST_FIELD,
    assert_text_bound,
    describe_text_binding,
    text_digest_for,
    text_digest_mismatch,
)
from agentteams.cli.security_gate import check_clearance

_KEY = "decision-text-binding-test-key"
_COLUMNS = ["timestamp", "requesting_agent", "date", "action_reviewed", "verdict", "scope", "conditions", "conditions_verified",
            "evidence", "author", "prev_digest", "signature"]
_LOG = "references/security-decisions.log.csv"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_DECISION_SIGNING_KEY", _KEY)


def _row(*, bind: bool, **over: str) -> dict[str, str]:
    """A current-schema HMAC-signed PASS for ``prune``; with ``bind`` it carries a text_digest."""
    row = {"timestamp": "2026-10-16T00:00:00Z", "requesting_agent": "security", "date": "2026-10-16",
           "action_reviewed": "prune", "verdict": "PASS", "scope": "",
           "conditions": "only repos a and b; expires 2027-01-16", "conditions_verified": "verified",
           "evidence": "ticket T-1", "author": "security", "prev_digest": "", "signature": ""}
    row.update(over)
    if bind:
        row[TEXT_DIGEST_FIELD] = text_digest_for(row)
    row["signature"] = dl.sign_decision_row(row, key=_KEY)
    return row


def _write_log(root: Path, rows: list[dict[str, str]], *, text_column: bool = True) -> None:
    columns = _COLUMNS + ([TEXT_DIGEST_FIELD] if text_column else [])
    path = root / _LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _authentic(row: dict[str, str], root: Path) -> None:
    dl._assert_authorizing_row_is_authentic(row, output_dir=root, signing_active=True, action="prune")


# --- the digest ----------------------------------------------------------------------------------


def test_text_digest_is_sha256_of_the_stripped_texts_joined_by_a_unit_separator() -> None:
    row = {"conditions": "  only a  ", "evidence": " T-1 "}
    expected = hashlib.sha256("only a\x1fT-1".encode("utf-8")).hexdigest()
    assert text_digest_for(row) == f"sha256:{expected}"
    # Moving text from one column to the other is a different binding.
    assert text_digest_for({"conditions": "only aT-1", "evidence": ""}) != text_digest_for(row)
    # A legacy row has no evidence column; it reads as empty.
    assert text_digest_for({"conditions": "x"}) == text_digest_for({"conditions": "x", "evidence": ""})


def test_text_digest_is_the_last_optional_signed_axis() -> None:
    assert dl._OPTIONAL_SIGNED_AXES[-1] == TEXT_DIGEST_FIELD
    assert TEXT_DIGEST_FIELD in dl.SIGNED_DECISION_COLUMNS


# --- HMAC rows -----------------------------------------------------------------------------------


def test_legacy_signed_row_without_text_digest_still_verifies(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: a row with no text_digest signs and verifies exactly as before."""
    row = _row(bind=False)
    assert not any(v.startswith(f"{TEXT_DIGEST_FIELD}=") for v in dl._decision_signature_values(row))
    _authentic(row, tmp_path)
    # Its text is unbound, as before: editing it does not break the signature.
    _authentic({**row, "conditions": "every repo, never expires"}, tmp_path)


def test_row_with_text_digest_verifies(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: a bound, untampered row is accepted."""
    row = _row(bind=True)
    assert f"{TEXT_DIGEST_FIELD}={row[TEXT_DIGEST_FIELD]}" in dl._decision_signature_values(row)
    _authentic(row, tmp_path)
    assert_text_bound(row, action="prune")


@pytest.mark.parametrize("field,value", [("conditions", "every repo, never expires"),
                                         ("evidence", "forged ticket")])
def test_editing_bound_text_after_signing_fails_verification(tmp_path: Path, field: str,
                                                             value: str) -> None:
    """SENSITIVITY: the signature still verifies over the payload, but the text no longer matches."""
    tampered = {**_row(bind=True), field: value}
    assert tampered["signature"] == dl.sign_decision_row(tampered, key=_KEY)  # payload unchanged
    assert text_digest_mismatch(tampered)
    with pytest.raises(RuntimeError, match="failed signature verification"):
        _authentic(tampered, tmp_path)
    with pytest.raises(RuntimeError, match="edited after signing"):
        assert_text_bound(tampered, action="prune")


def test_clearing_or_rewriting_the_digest_breaks_the_signature(tmp_path: Path) -> None:
    row = _row(bind=True)
    for forged in ("", text_digest_for({"conditions": "every repo"})):
        tampered = {**row, TEXT_DIGEST_FIELD: forged, "conditions": "every repo"}
        with pytest.raises(RuntimeError, match="failed signature verification"):
            _authentic(tampered, tmp_path)


def test_a_tampered_bound_row_no_longer_clears(tmp_path: Path) -> None:
    row = _row(bind=True)
    _write_log(tmp_path, [row])
    allowed, reason = check_clearance(tmp_path, action="prune")
    assert allowed, reason
    _write_log(tmp_path, [{**row, "conditions": "every repo, never expires"}])
    allowed, reason = check_clearance(tmp_path, action="prune")
    assert not allowed and "edited after signing" in reason


def test_a_pipe_in_the_text_digest_is_refused() -> None:
    row = {**_row(bind=False), TEXT_DIGEST_FIELD: "sha256:x|scope=prune"}
    with pytest.raises(RuntimeError, match="contains '\\|'"):
        dl._decision_signature_values(row)


# --- the hash chain ------------------------------------------------------------------------------


def test_the_chain_digest_covers_text_digest() -> None:
    first = _row(bind=True)
    assert row_chain_digest(first) != row_chain_digest({**first, TEXT_DIGEST_FIELD: ""})
    second = _row(bind=True, prev_digest=row_chain_digest(first))
    verify_chain([first, second], _COLUMNS + [TEXT_DIGEST_FIELD], signing_active=True)
    rewritten = {**first, "conditions": "every repo", TEXT_DIGEST_FIELD: text_digest_for(
        {"conditions": "every repo", "evidence": first["evidence"]})}
    with pytest.raises(RuntimeError, match="chain broken"):
        verify_chain([rewritten, second], _COLUMNS + [TEXT_DIGEST_FIELD], signing_active=True)


# --- Ed25519 rows --------------------------------------------------------------------------------


def _governed(root: Path) -> str:
    """Govern ``root``, record the lineage ancestor and install a verify key; return the private PEM."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    refs = root / "references"
    (refs / "authorized-verify-keys").mkdir(parents=True, exist_ok=True)
    (refs / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": True}),
                                               encoding="utf-8")
    (refs / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-09-01T00:00:00Z,security,approved-cleanup,PASS,,verified\n", encoding="utf-8")
    priv = Ed25519PrivateKey.generate()
    (refs / "authorized-verify-keys" / "op-1.pub.pem").write_bytes(priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode("utf-8")


def test_ed25519_relaxing_row_binds_its_text(tmp_path: Path) -> None:
    pytest.importorskip("cryptography")
    from agentteams.cli import signed_ledger as sl

    private_pem = _governed(tmp_path)
    row = {"date": "2026-10-16", "action_reviewed": "grant-cross-repo-write", "verdict": "PASS",
           "conditions": "only repo b; expires 2027-01-16", "conditions_verified": "verified",
           "evidence": "ticket T-2", "owner": "security", "prev_digest": "",
           "derives_from": "approved-cleanup", "effect_grants": "write:cross-repo",
           "sig_scheme": "ed25519", "key_id": "op-1"}
    row[TEXT_DIGEST_FIELD] = text_digest_for(row)
    row["signature"] = sl.ed25519_sign(private_pem, dl._decision_signature_values(row))
    action = "grant-cross-repo-write"
    dl._assert_authorizing_row_is_authentic(row, output_dir=tmp_path, signing_active=True, action=action)
    tampered = {**row, "conditions": "every repo, never expires"}
    with pytest.raises(RuntimeError, match="failed signature verification"):
        dl._assert_authorizing_row_is_authentic(tampered, output_dir=tmp_path, signing_active=True,
                                                action=action)


# --- migration and signing -----------------------------------------------------------------------


def _migrate(out: Path) -> int:
    return build_team.main(["--migrate-decision-log", "--framework", "claude", "--output", str(out), "--yes"])


def test_migrate_adds_an_empty_text_digest_column_to_a_signed_log(tmp_path: Path) -> None:
    out = tmp_path / "proj" / ".claude" / "agents"
    # A log already migrated to the signing schema before text_digest existed.
    columns = ["timestamp", "requesting_agent",
               *(c for c in dl.SIGNED_DECISION_COLUMNS if c != TEXT_DIGEST_FIELD)]
    path = out / _LOG
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerow(_row(bind=False))
    assert dlm.missing_signing_columns(columns) == [TEXT_DIGEST_FIELD]
    assert _migrate(out) == 0
    rows, fieldnames = dl._read_decision_rows(out)
    assert fieldnames == columns + [TEXT_DIGEST_FIELD]
    assert rows[0][TEXT_DIGEST_FIELD] == ""                       # never back-filled
    assert rows[0]["signature"] == dl.sign_decision_row(rows[0], key=_KEY)  # still verifies
    assert check_clearance(out, action="prune")[0]


def test_sign_decision_fills_text_digest_and_the_row_verifies(tmp_path: Path, monkeypatch, capsys,
                                                               signing_preapproved) -> None:
    pytest.importorskip("cryptography")
    from tests.test_operator_signing import _decision, _sd

    spec = _decision(conditions="only repo b", evidence="ticket T-3",
                     text_digest="sha256:" + "0" * 64)  # a spec cannot choose its own digest
    assert build_team.main(_sd(tmp_path, monkeypatch, spec=spec)) == 0
    assert "not bound" not in capsys.readouterr().err
    rows, _ = dl._read_decision_rows(tmp_path / "team")
    assert rows[-1][TEXT_DIGEST_FIELD] == text_digest_for(rows[-1])
    assert not text_digest_mismatch(rows[-1])


def test_sign_decision_into_a_log_without_the_column_says_so(tmp_path: Path, monkeypatch, capsys,
                                                             signing_preapproved) -> None:
    pytest.importorskip("cryptography")
    from tests.test_operator_signing import _decision, _sd

    team = tmp_path / "team"
    columns = ["timestamp", "requesting_agent",
               *(c for c in dl.SIGNED_DECISION_COLUMNS if c != TEXT_DIGEST_FIELD)]
    (team / "references").mkdir(parents=True)
    (team / _LOG).write_text(",".join(columns) + "\n", encoding="utf-8")
    argv = _sd(tmp_path, monkeypatch, spec=_decision(conditions="only repo b"))
    assert build_team.main(argv) == 0
    err = capsys.readouterr().err
    assert "not bound by its signature" in err and "--migrate-decision-log" in err
    rows, fieldnames = dl._read_decision_rows(team)
    assert TEXT_DIGEST_FIELD not in fieldnames and len(rows) == 1


# --- reporting -----------------------------------------------------------------------------------


def test_describe_text_binding_counts_bound_and_unbound_rows(tmp_path: Path) -> None:
    unsigned = {**_row(bind=False), "signature": ""}
    _write_log(tmp_path, [_row(bind=True), _row(bind=False), unsigned])
    lines = describe_text_binding(tmp_path)
    assert lines == [f"Decision log text binding: 1 of 2 signed row(s) bind their conditions/evidence "
                     f"({TEXT_DIGEST_FIELD}); 1 do not"]
    _write_log(tmp_path, [{**_row(bind=True), "evidence": "forged"}])
    assert "MISMATCH on row(s) [1]" in describe_text_binding(tmp_path)[-1]
    _write_log(tmp_path, [_row(bind=False)], text_column=False)
    assert "no text_digest column" in describe_text_binding(tmp_path)[-1]
    assert describe_text_binding(tmp_path / "absent") == []


def test_verify_integrity_reports_text_binding_without_changing_its_exit_code(tmp_path: Path,
                                                                               capsys) -> None:
    import argparse

    from agentteams.cli.commands import _run_verify_integrity

    args = argparse.Namespace(output=str(tmp_path), project=None, description=None)
    setattr(args, "self", False)
    _write_log(tmp_path, [_row(bind=True)])
    clean_rc = _run_verify_integrity(args)
    _write_log(tmp_path, [{**_row(bind=True), "conditions": "every repo"}])
    capsys.readouterr()
    assert _run_verify_integrity(args) == clean_rc  # advisory only
    out = capsys.readouterr().out
    assert "Decision log text binding: 1 of 1 signed row(s)" in out and "MISMATCH on row(s) [1]" in out
