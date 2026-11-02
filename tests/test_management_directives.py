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

_KEY = "test-management-key-0123456789ab"
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


@pytest.mark.parametrize(
    "scope",
    [
        # Governance/trust-ROOT scopes the verb denylist alone missed (audit Q1); now refused via
        # the single shared governance-target vocabulary. These are the exact gaps the WS-D audit
        # enumerated: the roster, the strict-signing switch, the governed marker, the authority
        # reference, and (R1) the Ed25519 verify-key store — plus underscore/camelCase spellings.
        "update-security-approvers",
        "write-agent-privilege",
        "touch-signing-governed",
        "edit-instruction-authority",
        "rotate-verify-key",
        "write-authorized_verify_keys",
        "write-authorizedVerifyKeys",
    ],
)
def test_governance_root_scopes_refused(scope):
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



def test_denylist_refuses_inflected_destructive_and_governance_scopes():
    """Fail-closed: stem matching must catch inflections (deletes/deleting/reference-templates),
    not just the bare verb — the Rule-8 close-out gap (2026-W36). Benign words stay allowed."""
    from agentteams.cli import management_directives as md
    for bad in ("deletes-stale-branches", "deleted-records", "deleting-logs", "deletion-run",
                "overwrites-config", "overwriting-file", "removes-files", "destroys-index",
                "destruction-run", "purges-cache", "pruning-backups", "resetting-state",
                "drops-table", "dropping-rows", "pushes-remote", "deploying-svc", "merges-branch",
                "merging", "wipe-db", "truncate-table", "revert-migration", "granting-widen",
                "edit-reference-templates", "enforcing-gate", "edit-integrity-manifest"):
        assert md.scope_is_allowed(bad) is False, bad
    for benign in ("ingest-transform-data", "confirm-order", "format-report", "transform-load",
                   "migrate-schema", "generate-invoice", "render-chart", "validate-input",
                   "normalize-records", "draft-weekly-report"):
        assert md.scope_is_allowed(benign) is True, benign


# --------------------------------------------------------------------------
# verify_handoff_directive: one current row against the whole ledger (vfr X1)
# --------------------------------------------------------------------------

_PAST = "2000-01-01T00:00:00Z"
_SCOPE = "review-vulnscan-handoff-20261102010101"


def _ledger(root: Path) -> Path:
    return root / md.MGMT_DIRECTIVES_LOG_REL


def _rewrite(root: Path, rows: list[dict[str, str]], columns=md.MGMT_DIRECTIVE_COLUMNS) -> None:
    with _ledger(root).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _rows(root: Path) -> list[dict[str, str]]:
    with _ledger(root).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _history_then_current(root: Path, **history) -> None:
    """An expired earlier-day directive, then today's directive for _SCOPE (id d-now)."""
    _issue(root, directive_id="d-old", task_scope="review-vulnscan-handoff-20261101010101",
           expires_at=_PAST, **history)
    _issue(root, directive_id="d-now", task_scope=_SCOPE)


def _verdict(root: Path, **over) -> md.HandoffDirectiveVerdict:
    kw = dict(task_scope=_SCOPE, directive_id="d-now", key=_KEY)
    kw.update(over)
    return md.verify_handoff_directive(root, **kw)


def test_handoff_verdict_tolerates_expired_history(tmp_path):
    """The 2026-11-02 false negative: an earlier day's expired directive no longer fails today's."""
    _history_then_current(tmp_path)
    assert md.verify_directives(tmp_path, key=_KEY)  # the whole-ledger check still reports it
    v = _verdict(tmp_path)
    assert v.verified and v.reason == md.VERDICT_VERIFIED, v.detail
    assert v.directive_id == "d-now" and v.expired_history == 1
    assert "1 earlier directive(s) expired (normal)" in v.detail
    assert [(d, c) for d, c, _ in v.problems] == [("d-old", md.ROW_EXPIRED)]


def test_handoff_verdict_without_directive_id_finds_the_scope_row(tmp_path):
    """A resume does not know the id: the single row carrying the exact scope is the current row."""
    _history_then_current(tmp_path)
    v = _verdict(tmp_path, directive_id="")
    assert v.verified and v.directive_id == "d-now"


def test_handoff_verdict_expired_history_with_bad_signature_is_unverified(tmp_path):
    """@security X1 (1) + Rule 12: the expiry carve-out never masks a bad signature on that row."""
    _history_then_current(tmp_path)
    rows = _rows(tmp_path)
    rows[0]["signature"] = "0" * 64   # unsigned column: the chain stays intact, only the sig fails
    _rewrite(tmp_path, rows)
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_HISTORY_INVALID
    codes = {c for d, c, _ in v.problems if d == "d-old"}
    assert codes == {md.ROW_BAD_SIGNATURE, md.ROW_EXPIRED}
    assert v.expired_history == 0


def test_handoff_verdict_expired_history_off_roster_is_unverified(tmp_path):
    """Rule 12: expired AND off-roster history is not 'expired (normal)'."""
    _history_then_current(tmp_path, manager_team="team-retired")
    _write_roster(tmp_path, "team-manager")   # team-retired removed from the roster
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_HISTORY_INVALID
    assert {c for d, c, _ in v.problems if d == "d-old"} == {md.ROW_EXPIRED, md.ROW_OFF_ROSTER}


def test_handoff_verdict_unexpired_history_failure_is_unverified(tmp_path):
    """Any non-expiry failure on ANY row (here exhausted history, re-signed so only uses fail)."""
    _issue(tmp_path, directive_id="d-old", task_scope="draft-weekly-report", max_uses=1)
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    rows = _rows(tmp_path)
    rows[0]["uses"] = "1"
    rows[0]["signature"] = md.sign_directive(rows[0], key=_KEY)
    rows[1]["prev_digest"] = md._directive_chain_digest(rows[0])
    rows[1]["signature"] = md.sign_directive(rows[1], key=_KEY)
    _rewrite(tmp_path, rows)
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_HISTORY_INVALID
    assert [c for d, c, _ in v.problems] == [md.ROW_EXHAUSTED]


def test_handoff_verdict_malformed_history_expiry_is_unverified(tmp_path):
    """A malformed date is never read as 'expired'."""
    _history_then_current(tmp_path)
    rows = _rows(tmp_path)
    rows[0]["expires_at"] = "not-a-date"
    rows[0]["signature"] = md.sign_directive(rows[0], key=_KEY)
    rows[1]["prev_digest"] = md._directive_chain_digest(rows[0])
    rows[1]["signature"] = md.sign_directive(rows[1], key=_KEY)
    _rewrite(tmp_path, rows)
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_HISTORY_INVALID
    assert [c for d, c, _ in v.problems] == [md.ROW_MALFORMED_EXPIRY]


def test_handoff_verdict_expired_current_row_is_unverified(tmp_path):
    """@security X1 (2): the carve-out is history-only; an expired CURRENT row never verifies."""
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE, expires_at=_PAST)
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_CURRENT_INVALID
    assert v.expired_history == 0


def test_handoff_verdict_replayed_scope_is_unverified(tmp_path):
    """@security X1 (2): a second validly-signed, chained row for the same scope (a replay)."""
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    _issue(tmp_path, directive_id="d-replay", task_scope=_SCOPE)
    for did in ("d-now", "d-replay", ""):
        v = _verdict(tmp_path, directive_id=did)
        assert not v.verified and v.reason == md.VERDICT_CURRENT_DUPLICATE, did


def test_handoff_verdict_duplicated_directive_id_is_unverified(tmp_path):
    """@security X1 (2): the id just issued occurs on another row too."""
    _issue(tmp_path, directive_id="d-now", task_scope="draft-weekly-report")
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_CURRENT_DUPLICATE


def test_handoff_verdict_missing_or_mismatched_current_row_is_unverified(tmp_path):
    """@security X1 (2): no row for the scope, or the scope's row is not the id just issued."""
    _history_then_current(tmp_path)
    assert _verdict(tmp_path, task_scope=_SCOPE + "9").reason == md.VERDICT_CURRENT_MISSING
    assert _verdict(tmp_path, directive_id="d-other").reason == md.VERDICT_CURRENT_MISSING
    assert _verdict(tmp_path, directive_id="d-old").reason == md.VERDICT_CURRENT_MISSING
    assert _verdict(tmp_path, task_scope="").reason == md.VERDICT_CURRENT_MISSING


def test_handoff_verdict_broken_chain_is_unverified(tmp_path):
    """@security X1 (1): a removed row breaks the chain; the verdict fails before reading rows."""
    _issue(tmp_path, directive_id="d-a", task_scope="draft-weekly-report", expires_at=_PAST)
    _issue(tmp_path, directive_id="d-b", task_scope="draft-monthly-report", expires_at=_PAST)
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    rows = _rows(tmp_path)
    _rewrite(tmp_path, [rows[0], rows[2]])   # d-b deleted
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_CHAIN_BROKEN


def test_handoff_verdict_unchained_ledger_is_unverified(tmp_path):
    """@security X1 (3): a ledger with no prev_digest column is never treated as passed."""
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    rows = _rows(tmp_path)
    _rewrite(tmp_path, rows, columns=[c for c in md.MGMT_DIRECTIVE_COLUMNS if c != "prev_digest"])
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_UNCHAINED


def test_handoff_verdict_no_ledger_or_no_key_is_unverified(tmp_path, monkeypatch):
    assert _verdict(tmp_path).reason == md.VERDICT_NO_LEDGER
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    monkeypatch.delenv(md.MGMT_KEY_ENV, raising=False)
    v = md.verify_handoff_directive(tmp_path, task_scope=_SCOPE, directive_id="d-now")
    assert not v.verified and v.reason == md.VERDICT_CURRENT_INVALID
    assert [c for _, c, _ in v.problems] == [md.ROW_NO_KEY]
    v = _verdict(tmp_path, key="the-wrong-management-key-0123456789")
    assert not v.verified and [c for _, c, _ in v.problems] == [md.ROW_BAD_SIGNATURE]


def test_directive_row_failures_reports_every_check(tmp_path):
    """Every failed check is reported, so callers never infer 'signature passed' from order."""
    rec = _issue(tmp_path, expires_at=_PAST)
    rec["signature"] = "0" * 64
    codes = [c for c, _ in md.directive_row_failures(rec, tmp_path, key=_KEY)]
    assert codes == [md.ROW_BAD_SIGNATURE, md.ROW_EXPIRED]
    ok, reason = md.validate_directive(rec, tmp_path, key=_KEY)
    assert not ok and "invalid signature" in reason   # first failure, unchanged


def test_handoff_verdict_unreadable_ledger_is_unverified(tmp_path):
    """A ledger that is not UTF-8 is reported (VERDICT_UNREADABLE), never an escaping decode error."""
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    _ledger(tmp_path).write_bytes(_ledger(tmp_path).read_bytes() + b"\xff\xfe\x00bad\n")
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_UNREADABLE
    with pytest.raises(md.MgmtDirectiveError, match="unable to read"):
        md._read_directive_rows(tmp_path)


def test_handoff_verdict_malformed_header_is_unverified(tmp_path):
    """A chained ledger missing another required column (here signature) is VERDICT_MALFORMED."""
    _issue(tmp_path, directive_id="d-now", task_scope=_SCOPE)
    rows = _rows(tmp_path)
    _rewrite(tmp_path, rows, columns=[c for c in md.MGMT_DIRECTIVE_COLUMNS if c != "signature"])
    v = _verdict(tmp_path)
    assert not v.verified and v.reason == md.VERDICT_MALFORMED


def test_issue_directive_cli_prints_the_id_line_the_handoff_tooling_parses(tmp_path, capsys, monkeypatch):
    """Pins the ``--issue-directive`` stdout line the operator's handoff tooling reads the new id from.

    The site-local vulnscan-operator-actions.sh extracts the id with
    ``sed -nE 's/^Issued management directive (directive-[0-9a-f]{16}): .*/\\1/p'``; a changed line means every
    handoff reads unverified (fail-closed), so a format change must update that script too.
    """
    import re

    from build_team import main

    _write_roster(tmp_path, "ops-team")
    monkeypatch.setenv(md.MGMT_KEY_ENV, _KEY)
    rc = main(["--issue-directive", "--output", str(tmp_path), "--manager-team", "ops-team",
               "--managed-team", "svc", "--task-scope", _SCOPE, "--expires-at", _FAR_FUTURE,
               "--max-uses", "3", "--approver", "operator:test"])
    assert rc == 0
    lines = capsys.readouterr().out.splitlines()
    ids = [m.group(1) for ln in lines
           if (m := re.match(r"^Issued management directive (directive-[0-9a-f]{16}): ", ln))]
    assert len(ids) == 1, lines
    assert _rows(tmp_path)[-1]["directive_id"] == ids[0]
    assert md.verify_handoff_directive(tmp_path, task_scope=_SCOPE, directive_id=ids[0], key=_KEY).verified
