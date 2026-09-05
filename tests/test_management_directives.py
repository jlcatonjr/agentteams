"""Tests for the management-repository endowment crypto core (management_directives.py).

Covers the directive lifecycle (issue/sign/verify/validate), tamper/expiry/use-counter/roster
rejection, the EXACT-scope security boundary and the signature-non-overridable denylist, the
missing-key fail-closed path, and hash-chain deletion/reorder detection.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from agentteams.cli import management_directives as md

_KEY = "test-management-key"
_FAR_FUTURE = "2099-01-01T00:00:00Z"


def _write_roster(root: Path, *managers: str) -> None:
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "authorized-managers.txt").write_text("\n".join(managers) + "\n", encoding="utf-8")


def _issue(root: Path, **over) -> dict[str, str]:
    base = dict(
        manager_team="team-manager", managed_team="team-managed",
        task_scope="draft-weekly-report", expires_at=_FAR_FUTURE, max_uses=5,
        approver="alice", directive_id="d-1", timestamp="2026-09-01T00:00:00Z", key=_KEY,
    )
    base.update(over)
    # issue_directive enforces the manager roster at issue time — ensure it's present.
    roster = root / "references" / "authorized-managers.txt"
    existing = roster.read_text(encoding="utf-8").split() if roster.exists() else []
    if base["manager_team"] not in existing:
        _write_roster(root, *(existing + [base["manager_team"]]))
    return md.issue_directive(root, **base)


# --------------------------------------------------------------------------
# lifecycle: sign / verify / validate
# --------------------------------------------------------------------------

def test_issue_and_verify_signature(tmp_path):
    rec = _issue(tmp_path)
    assert len(rec["signature"]) == 64
    assert md.verify_directive_signature(rec, key=_KEY)


def test_valid_directive_validates(tmp_path):
    ok, reason = md.validate_directive(_issue(tmp_path), tmp_path, key=_KEY)
    assert ok, reason


def test_sign_verify_roundtrip(tmp_path):
    rec = _issue(tmp_path)
    rec["signature"] = md.sign_directive(rec, key=_KEY)
    assert md.verify_directive_signature(rec, key=_KEY)


def test_tampered_field_invalid(tmp_path):
    rec = _issue(tmp_path)
    rec["managed_team"] = "team-EVERYONE"  # not re-signed
    assert not md.verify_directive_signature(rec, key=_KEY)
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "invalid signature" in reason


def test_expired_invalid(tmp_path):
    rec = _issue(tmp_path, expires_at="2000-01-01T00:00:00Z")
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "expired" in reason


def test_malformed_expiry_fails_closed(tmp_path):
    rec = _issue(tmp_path)
    rec["expires_at"] = "not-a-date"
    rec["signature"] = md.sign_directive(rec, key=_KEY)  # re-sign so signature isn't the failure
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "malformed expires_at" in reason


def test_uses_equal_max_uses_invalid(tmp_path):
    rec = _issue(tmp_path, max_uses=1)
    rec["uses"] = "1"
    rec["signature"] = md.sign_directive(rec, key=_KEY)
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "exhausted" in reason


# --------------------------------------------------------------------------
# roster
# --------------------------------------------------------------------------

def test_off_roster_manager_invalid(tmp_path):
    rec = _issue(tmp_path)  # roster has "team-manager"
    rec["manager_team"] = "team-intruder"
    rec["signature"] = md.sign_directive(rec, key=_KEY)  # signature valid for the tampered id
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "not on the authorized-managers roster" in reason


def test_empty_roster_accepts_nothing(tmp_path):
    rec = _issue(tmp_path)  # writes a roster + a valid directive
    # Blank the roster: an empty roster must accept nothing (fail-closed).
    (tmp_path / "references" / "authorized-managers.txt").write_text("\n", encoding="utf-8")
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "authorized-managers roster" in reason


def test_issue_off_roster_refused(tmp_path):
    _write_roster(tmp_path, "team-manager")
    with pytest.raises(md.MgmtDirectiveError, match="not on the authorized-managers roster"):
        md.issue_directive(
            tmp_path, manager_team="team-intruder", managed_team="team-managed",
            task_scope="draft-weekly-report", expires_at=_FAR_FUTURE, max_uses=5,
            approver="alice", directive_id="d-x", timestamp="2026-09-01T00:00:00Z", key=_KEY,
        )


# --------------------------------------------------------------------------
# scope: EXACT match + signature-non-overridable denylist
# --------------------------------------------------------------------------

def test_scope_denylist_refuses_overwrite_variant(tmp_path):
    # "overwrite-single-readme-only" reads as narrow but names a denylisted token => REFUSED.
    assert not md.scope_is_allowed("overwrite-single-readme-only")


def test_exact_scope_does_not_suffix_match(tmp_path):
    # A directive for the variant must NOT authorize the bare "overwrite" action (exact only).
    rec = {"task_scope": "overwrite-single-readme-only"}
    assert not md.directive_authorizes(rec, "overwrite")
    assert md.directive_authorizes(rec, "overwrite-single-readme-only")


def test_benign_scope_allowed_and_exact(tmp_path):
    assert md.scope_is_allowed("draft-weekly-report")
    rec = {"task_scope": "draft-weekly-report"}
    assert md.directive_authorizes(rec, "draft-weekly-report")
    assert not md.directive_authorizes(rec, "draft")  # exact, not prefix


def test_empty_scope_refused(tmp_path):
    assert not md.scope_is_allowed("")
    assert not md.scope_is_allowed("   ")


@pytest.mark.parametrize(
    "scope",
    ["delete-old-drafts", "bulk-rename", "cross-repo-sync", "push-to-main",
     "edit-constitution", "rotate-signing-key", "update-roster", "grant-write"],
)
def test_denylist_tokens_refused(scope):
    assert not md.scope_is_allowed(scope)


def test_valid_signature_cannot_override_denylisted_scope(tmp_path):
    # A directive whose task_scope is denylisted, but with a genuinely VALID signature, must be
    # REFUSED — the signature cannot override the mechanical denylist. (issue_directive would
    # refuse to mint it, so construct + sign the row directly.)
    _write_roster(tmp_path, "team-manager")
    rec = {
        "timestamp": "2026-09-01T00:00:00Z", "directive_id": "d-evil",
        "manager_team": "team-manager", "managed_team": "team-managed",
        "task_scope": "overwrite-production-config", "expires_at": _FAR_FUTURE,
        "max_uses": "5", "uses": "0", "approver": "alice", "prev_digest": "", "signature": "",
    }
    rec["signature"] = md.sign_directive(rec, key=_KEY)
    assert md.verify_directive_signature(rec, key=_KEY)  # signature genuinely valid
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "refused task_scope" in reason


def test_issue_refuses_denylisted_scope(tmp_path):
    _write_roster(tmp_path, "team-manager")
    with pytest.raises(md.MgmtDirectiveError, match="destructive"):
        md.issue_directive(
            tmp_path, manager_team="team-manager", managed_team="team-managed",
            task_scope="delete-stale-branches", expires_at=_FAR_FUTURE, max_uses=5,
            approver="alice", directive_id="d-bad", timestamp="2026-09-01T00:00:00Z", key=_KEY,
        )


# --------------------------------------------------------------------------
# key handling
# --------------------------------------------------------------------------

def test_missing_key_sign_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(md.MGMT_KEY_ENV, raising=False)
    with pytest.raises(md.MgmtDirectiveError):
        md.sign_directive({"directive_id": "d", "task_scope": "x"})


def test_verify_missing_key_fails_closed(tmp_path, monkeypatch):
    rec = _issue(tmp_path)  # issued with explicit key
    monkeypatch.delenv(md.MGMT_KEY_ENV, raising=False)
    with pytest.raises(md.MgmtDirectiveError):
        md.verify_directive_signature(rec)  # no key arg, env unset => fail closed


def test_verify_unsigned_row_invalid(tmp_path):
    rec = _issue(tmp_path)
    rec["signature"] = ""  # unsigned
    assert not md.verify_directive_signature(rec, key=_KEY)
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok
    assert "invalid signature" in reason


# --------------------------------------------------------------------------
# ledger + verify_directives + hash chain
# --------------------------------------------------------------------------

def test_verify_directives_clean_and_problem(tmp_path):
    _issue(tmp_path, directive_id="ok")
    assert md.verify_directives(tmp_path, key=_KEY) == []
    _issue(tmp_path, directive_id="stale", expires_at="2000-01-01T00:00:00Z")
    problems = md.verify_directives(tmp_path, key=_KEY)
    assert any("expired" in p for p in problems)


def test_hash_chain_detects_deleted_row(tmp_path):
    _issue(tmp_path, directive_id="d-1")
    _issue(tmp_path, directive_id="d-2")
    _issue(tmp_path, directive_id="d-3")
    log = tmp_path / md.MGMT_DIRECTIVES_LOG_REL
    with log.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = list(reader)
    # Delete the middle row and rewrite; the chain must break (prev_digest mismatch).
    kept = [rows[0], rows[2]]
    with log.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(kept)
    problems = md.verify_directives(tmp_path, key=_KEY)
    assert any("chain broken" in p for p in problems)


def test_hash_chain_detects_reordered_rows(tmp_path):
    _issue(tmp_path, directive_id="d-1")
    _issue(tmp_path, directive_id="d-2")
    log = tmp_path / md.MGMT_DIRECTIVES_LOG_REL
    with log.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        rows = list(reader)
    with log.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows([rows[1], rows[0]])  # reordered
    with pytest.raises(md.MgmtDirectiveError, match="chain broken"):
        md._read_directive_rows(tmp_path)


def test_directive_cannot_authorize_its_own_ledger_or_roster():
    """@security 2c: a directive can never widen its own trust base (ledger/roster/key)."""
    from agentteams.cli import management_directives as md
    for scope in ("append-management-directives-log", "edit-directives-ledger",
                  "enroll-authorized-manager", "rotate-signing-key",
                  "edit-management-authority-json"):
        assert md.scope_is_allowed(scope) is False, scope
    assert md.scope_is_allowed("draft-weekly-report") is True


def test_scope_denylist_uses_word_boundaries_not_substrings():
    """Benign scopes containing a denyword as a substring (transform->rm) are NOT refused."""
    from agentteams.cli import management_directives as md
    for benign in ("ingest-transform-data", "confirm-order", "format-report", "transform-load"):
        assert md.scope_is_allowed(benign) is True, benign
    for bad in ("rm-tmp", "delete-logs", "cross-repo-sync", "force-push"):
        assert md.scope_is_allowed(bad) is False, bad

