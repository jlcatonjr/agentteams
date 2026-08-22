"""Tests for P2 cross-workspace capability grants.

Covers signed_ledger primitives, the grant lifecycle (issue/sign/verify/validate),
scope matching (holder/op/path), tamper/expiry/use-counter rejection, team identity,
and the generation-time sandbox-widening integration.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.cli import grants
from agentteams.cli.artifacts import (
    apply_held_grants_to_write_roots,
    resolve_host_features_and_advise,
)
from agentteams.cli.signed_ledger import (
    hmac_sign,
    hmac_verify,
    is_expired,
    path_within,
)
from agentteams.frameworks.claude import ClaudeAdapter

_KEY = "test-grant-key"
_FAR_FUTURE = "2099-01-01T00:00:00Z"


def _write_roster(root: Path, *approvers: str) -> None:
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "security-approvers.txt").write_text("\n".join(approvers) + "\n", encoding="utf-8")


def _issue(root: Path, **over):
    base = dict(
        issuer_team="team-b", holder_team="team-a", target_path="/abs/b/shared",
        permitted_ops="write", expires_at=_FAR_FUTURE, max_uses=5, approver="alice",
        ticket_id="T-1", reason_code="collab", grant_id="g-1",
        timestamp="2026-08-21T00:00:00Z", key=_KEY,
    )
    base.update(over)
    # issue_grant now enforces the approver roster at issue time — ensure it's present.
    approver = base["approver"]
    roster = root / "references" / "security-approvers.txt"
    existing = roster.read_text(encoding="utf-8").split() if roster.exists() else []
    if approver not in existing:
        _write_roster(root, *(existing + [approver]))
    return grants.issue_grant(root, **base)


# --------------------------------------------------------------------------
# signed_ledger primitives
# --------------------------------------------------------------------------

def test_hmac_sign_verify_roundtrip_and_tamper():
    sig = hmac_sign(_KEY, ["a", "b", "c"])
    assert hmac_verify(_KEY, ["a", "b", "c"], sig)
    assert not hmac_verify(_KEY, ["a", "b", "X"], sig)
    assert not hmac_verify("wrong-key", ["a", "b", "c"], sig)


def test_is_expired():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert is_expired("2025-01-01T00:00:00Z", now=now)
    assert not is_expired("2027-01-01T00:00:00Z", now=now)


def test_path_within_containment_and_escape(tmp_path):
    assert path_within("/root/sub/f.txt", "/root")
    assert path_within("/root", "/root")
    assert not path_within("/other/f.txt", "/root")
    # `..` escape is canonicalized away and rejected
    assert not path_within("/root/../etc/passwd", "/root")


# --------------------------------------------------------------------------
# grant lifecycle
# --------------------------------------------------------------------------

def test_issue_and_verify_signature(tmp_path):
    rec = _issue(tmp_path)
    assert len(rec["signature"]) == 64
    assert grants.verify_grant_signature(rec, key=_KEY)


def test_validate_passes_for_fresh_grant(tmp_path):
    grants.validate_grant(_issue(tmp_path), key=_KEY)  # must not raise


def test_validate_rejects_tampered_target(tmp_path):
    rec = _issue(tmp_path)
    rec["target_path"] = "/abs/b/EVERYTHING"  # not re-signed
    assert not grants.verify_grant_signature(rec, key=_KEY)
    with pytest.raises(grants.GrantError):
        grants.validate_grant(rec, key=_KEY)


def test_validate_rejects_expired(tmp_path):
    rec = _issue(tmp_path, expires_at="2000-01-01T00:00:00Z")
    with pytest.raises(grants.GrantError, match="expired"):
        grants.validate_grant(rec, key=_KEY)


def test_validate_rejects_exhausted_uses(tmp_path):
    rec = _issue(tmp_path, max_uses=1)
    rec["uses"] = "1"
    rec["signature"] = grants.sign_grant(rec, key=_KEY)
    with pytest.raises(grants.GrantError, match="exhausted"):
        grants.validate_grant(rec, key=_KEY)


def test_missing_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(grants.GRANT_KEY_ENV, raising=False)
    rec = _issue(tmp_path)  # issued with explicit key
    with pytest.raises(grants.GrantError):
        grants.verify_grant_signature(rec)  # no key arg, env unset → fail closed


# --------------------------------------------------------------------------
# scope matching
# --------------------------------------------------------------------------

def test_grant_covers_holder_op_and_path(tmp_path):
    rec = _issue(tmp_path)
    assert grants.grant_covers(rec, holder_team="team-a", target_path="/abs/b/shared/f", op="write")
    assert not grants.grant_covers(rec, holder_team="team-a", target_path="/abs/b/other", op="write")
    assert not grants.grant_covers(rec, holder_team="team-c", target_path="/abs/b/shared/f", op="write")
    assert not grants.grant_covers(rec, holder_team="team-a", target_path="/abs/b/shared/f", op="read")


# --------------------------------------------------------------------------
# ledger + held_grants + verify_grants
# --------------------------------------------------------------------------

def test_held_grants_filters_by_holder(tmp_path):
    _issue(tmp_path, grant_id="g-1", holder_team="team-a")
    _issue(tmp_path, grant_id="g-2", holder_team="team-c")
    held = grants.held_grants(tmp_path, holder_team="team-a", key=_KEY)
    assert [g["grant_id"] for g in held] == ["g-1"]


def test_held_grants_skips_invalid(tmp_path, capsys):
    _issue(tmp_path, grant_id="ok", holder_team="team-a")
    _issue(tmp_path, grant_id="stale", holder_team="team-a", expires_at="2000-01-01T00:00:00Z")
    held = grants.held_grants(tmp_path, holder_team="team-a", key=_KEY)
    assert [g["grant_id"] for g in held] == ["ok"]
    assert "skipping invalid capability grant" in capsys.readouterr().err


def test_verify_grants_reports_problems(tmp_path):
    _issue(tmp_path, grant_id="ok", holder_team="team-a")
    _issue(tmp_path, grant_id="stale", holder_team="team-a", expires_at="2000-01-01T00:00:00Z")
    problems = grants.verify_grants(tmp_path, key=_KEY)
    assert len(problems) == 1 and "stale" in problems[0]


def test_issue_rejects_offroster_approver(tmp_path):
    # D2: an off-roster approver cannot even enter the ledger.
    _write_roster(tmp_path, "bob")  # alice is NOT on the roster
    with pytest.raises(grants.GrantError, match="roster"):
        grants.issue_grant(
            tmp_path, issuer_team="team-b", holder_team="team-a", target_path="/abs/b/x",
            permitted_ops="write", expires_at=_FAR_FUTURE, max_uses=1, approver="alice",
            ticket_id="T", reason_code="c", grant_id="g", timestamp="2026-08-21T00:00:00Z",
            key=_KEY,
        )


def test_issue_rejects_absent_roster(tmp_path):
    # P2-2: with no roster file at all, a grant naming @security would otherwise
    # self-clear via the built-in default. Refuse to issue instead (fail-closed).
    with pytest.raises(grants.GrantError, match="explicit approver roster"):
        grants.issue_grant(
            tmp_path, issuer_team="team-b", holder_team="team-a", target_path="/abs/b/x",
            permitted_ops="write", expires_at=_FAR_FUTURE, max_uses=1, approver="@security",
            ticket_id="T", reason_code="c", grant_id="g", timestamp="2026-08-21T00:00:00Z",
            key=_KEY,
        )


def test_issue_rejects_empty_roster(tmp_path):
    # P2-2: a roster of only comments/blanks is the same absent-approver case.
    (tmp_path / "references").mkdir(parents=True, exist_ok=True)
    (tmp_path / "references" / "security-approvers.txt").write_text(
        "# no approvers yet\n\n", encoding="utf-8"
    )
    with pytest.raises(grants.GrantError, match="explicit approver roster"):
        grants.issue_grant(
            tmp_path, issuer_team="team-b", holder_team="team-a", target_path="/abs/b/x",
            permitted_ops="write", expires_at=_FAR_FUTURE, max_uses=1, approver="@security",
            ticket_id="T", reason_code="c", grant_id="g", timestamp="2026-08-21T00:00:00Z",
            key=_KEY,
        )


def test_validate_rejects_absent_roster(tmp_path):
    # P2-2: the fail-closed also fires at validate-time (--verify-grants) and, via
    # held_grants, at generation-time widening — not only at issue.
    _write_roster(tmp_path, "alice")
    rec = _issue(tmp_path)
    (tmp_path / "references" / "security-approvers.txt").unlink()
    with pytest.raises(grants.GrantError, match="explicit approver roster"):
        grants.validate_grant(rec, output_dir=tmp_path, key=_KEY)


@pytest.mark.parametrize("bad", ["", "   ", "/", "~", "~/secrets", "../escape", "../../x"])
def test_issue_rejects_unsafe_target_path(tmp_path, bad):
    # P2-4: a target_path that would widen too far is refused before it enters the ledger.
    _write_roster(tmp_path, "alice")
    with pytest.raises(grants.GrantError):
        grants.issue_grant(
            tmp_path, issuer_team="team-b", holder_team="team-a", target_path=bad,
            permitted_ops="write", expires_at=_FAR_FUTURE, max_uses=1, approver="alice",
            ticket_id="T", reason_code="c", grant_id="g", timestamp="2026-08-21T00:00:00Z",
            key=_KEY,
        )


def test_issue_rejects_malformed_expires_at(tmp_path):
    # P2-8: a non-ISO expires_at is caught at issue, not left to crash is_expired later.
    _write_roster(tmp_path, "alice")
    with pytest.raises(grants.GrantError, match="expires_at"):
        _issue(tmp_path, expires_at="not-a-date")


def test_validate_rejects_malformed_expires_at(tmp_path):
    # P2-8: a malformed expires_at that is nonetheless validly signed (a hand-crafted
    # ledger row) surfaces as a GrantError, not a bare ValueError that crashes the caller.
    _write_roster(tmp_path, "alice")
    rec = _issue(tmp_path)
    rec = dict(rec, expires_at="garbage")
    rec["signature"] = grants.sign_grant(rec, key=_KEY)  # re-sign so signature passes
    with pytest.raises(grants.GrantError, match="malformed expires_at"):
        grants.validate_grant(rec, key=_KEY)


def test_granted_write_roots_dedupes(tmp_path):
    _issue(tmp_path, grant_id="g-1", target_path="/abs/b/shared")
    _issue(tmp_path, grant_id="g-2", target_path="/abs/b/shared")  # dup path
    _issue(tmp_path, grant_id="g-3", target_path="/abs/b/other")
    roots = grants.granted_write_roots(tmp_path, holder_team="team-a", key=_KEY)
    assert roots == ["/abs/b/shared", "/abs/b/other"]


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def test_team_id_defaults_to_slug_and_overrides():
    m = analyze.build_manifest({"project_goal": "x", "project_name": "My Cool Team"}, framework="claude")
    assert m["team_id"] == "my-cool-team"
    m2 = analyze.build_manifest({"project_goal": "x", "team_id": "custom-id"}, framework="claude")
    assert m2["team_id"] == "custom-id"


# --------------------------------------------------------------------------
# generation-time sandbox widening (the enforcement integration)
# --------------------------------------------------------------------------

def test_held_grant_widens_sandbox_allowwrite(tmp_path, monkeypatch):
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _KEY)
    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "Team A", "privilege_profile": "confined"},
        framework="claude",
    )
    resolve_host_features_and_advise(m, [], "claude")  # turns on claude:sandbox
    _issue(tmp_path, holder_team=m["team_id"], target_path="/abs/b/shared")
    added = apply_held_grants_to_write_roots(m, tmp_path)
    assert added == ["/abs/b/shared"]
    aw = json.loads(dict(ClaudeAdapter().extra_output_files(m))["../settings.hooks.example.json"])[
        "sandbox"
    ]["filesystem"]["allowWrite"]
    assert aw == [".", "/abs/b/shared"]


def test_no_widening_when_sandbox_off(tmp_path, monkeypatch):
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _KEY)
    # cooperative profile → no sandbox → grants must not widen anything
    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "Team A"}, framework="claude"
    )
    _issue(tmp_path, holder_team=m["team_id"], target_path="/abs/b/shared")
    assert apply_held_grants_to_write_roots(m, tmp_path) == []


def test_readonly_grant_does_not_widen(tmp_path):
    # D1: a grant permitting only `read` must NOT hand the holder OS write.
    _issue(tmp_path, grant_id="ro", holder_team="team-a", permitted_ops="read",
           target_path="/abs/b/readonly")
    assert grants.granted_write_roots(tmp_path, holder_team="team-a", key=_KEY) == []


def test_widening_enforces_roster(tmp_path, monkeypatch):
    # D2: a grant whose approver later drops off the roster stops widening.
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _KEY)
    _issue(tmp_path, holder_team="team-a", target_path="/abs/b/shared", approver="alice")
    # roster now no longer contains alice → the grant must not apply at widening
    _write_roster(tmp_path, "bob")
    assert grants.granted_write_roots(tmp_path, holder_team="team-a", key=_KEY) == []


# --------------------------------------------------------------------------
# P2-3: ledger prev_digest chain (deletion / reorder detection)
# --------------------------------------------------------------------------

def _ledger_path(root: Path) -> Path:
    return root / grants.GRANT_LOG_REL


def _read_raw_ledger(root: Path) -> tuple[list[str], list[dict[str, str]]]:
    with _ledger_path(root).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), [dict(r) for r in reader]


def _write_raw_ledger(root: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    with _ledger_path(root).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def test_chain_valid_across_multiple_issues(tmp_path):
    # A normally-issued multi-row ledger has an intact chain: genesis prev_digest is empty,
    # each subsequent row chains to its predecessor.
    _issue(tmp_path, grant_id="g-1", target_path="/abs/b/one")
    _issue(tmp_path, grant_id="g-2", target_path="/abs/b/two")
    _issue(tmp_path, grant_id="g-3", target_path="/abs/b/three")
    header, rows = _read_raw_ledger(tmp_path)
    assert "prev_digest" in header
    assert rows[0]["prev_digest"] == ""  # genesis
    assert rows[1]["prev_digest"] != "" and rows[2]["prev_digest"] != ""
    # reading validates the chain and does not raise
    assert len(grants._read_grant_rows(tmp_path)) == 3


def test_chain_detects_deleted_row(tmp_path):
    # Deleting a signed row leaves the remaining signatures valid but breaks the chain.
    for i in range(3):
        _issue(tmp_path, grant_id=f"g-{i}", target_path=f"/abs/b/{i}")
    header, rows = _read_raw_ledger(tmp_path)
    _write_raw_ledger(tmp_path, header, [rows[0], rows[2]])  # drop the middle row
    with pytest.raises(grants.GrantError, match="chain broken"):
        grants._read_grant_rows(tmp_path)


def test_chain_detects_reorder(tmp_path):
    for i in range(3):
        _issue(tmp_path, grant_id=f"g-{i}", target_path=f"/abs/b/{i}")
    header, rows = _read_raw_ledger(tmp_path)
    _write_raw_ledger(tmp_path, header, [rows[0], rows[2], rows[1]])  # swap last two
    with pytest.raises(grants.GrantError, match="chain broken"):
        grants._read_grant_rows(tmp_path)


def test_chain_break_stops_widening_fail_closed(tmp_path, monkeypatch):
    # A tampered ledger must not silently widen — the broken chain propagates as GrantError
    # that the widening path treats as "no grants applied".
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _KEY)
    for i in range(3):
        _issue(tmp_path, grant_id=f"g-{i}", holder_team="team-a", target_path=f"/abs/b/{i}")
    header, rows = _read_raw_ledger(tmp_path)
    _write_raw_ledger(tmp_path, header, [rows[0], rows[2]])  # delete middle
    with pytest.raises(grants.GrantError, match="chain broken"):
        grants.granted_write_roots(tmp_path, holder_team="team-a", key=_KEY)


def test_verify_grants_reports_chain_break(tmp_path):
    for i in range(2):
        _issue(tmp_path, grant_id=f"g-{i}", target_path=f"/abs/b/{i}")
    header, rows = _read_raw_ledger(tmp_path)
    _write_raw_ledger(tmp_path, header, [rows[1]])  # drop genesis → chain break at row 1
    problems = grants.verify_grants(tmp_path, key=_KEY)
    assert problems and any("chain broken" in p for p in problems)
