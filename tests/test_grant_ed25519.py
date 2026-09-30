"""PR-E: only an operator Ed25519-signed grant widens a sandbox's allowWrite.

Covers the ``hmac-grant-widens-allowwrite`` route (red-team row 34, HIGH): an in-sandbox agent
inherits ``AGENTTEAMS_GRANT_SIGNING_KEY`` and can plant a project-root approver roster, so an
HMAC-signed ``write`` grant must never widen. Tests: Ed25519 sign/verify roundtrip, purpose-tag
domain separation (grant vs decision), HMAC write grants refused with a migration message,
project-root roster ignored (with a warning), missing key/roster errors, tamper detection, and
the ``--sign-grant`` / ``--verify-grants`` / ``--issue-grant`` CLI surface.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.cli import grants
from agentteams.cli.artifacts import apply_held_grants_to_write_roots
from agentteams.cli.signed_ledger import ed25519_sign, ed25519_verify, hmac_sign

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

_HMAC_KEY = "inherited-by-the-sandbox"
_KEY_ID = "op-2026"
_FAR = "2099-01-01T00:00:00Z"


def _keypair() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    return (
        priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8"),
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8"),
    )


_PRIV, _PUB = _keypair()


def _roster(root: Path, *names: str) -> None:
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / "references" / "security-approvers.txt").write_text("\n".join(names) + "\n", "utf-8")


def _store(team: Path, key_id: str = _KEY_ID, pub: str = _PUB) -> None:
    store = team / "references" / "authorized-verify-keys"
    store.mkdir(parents=True, exist_ok=True)
    (store / f"{key_id}.pub.pem").write_text(pub, encoding="utf-8")


@pytest.fixture()
def layout(tmp_path: Path) -> tuple[Path, Path]:
    """A holder project root (ledger) and its separate Claude TEAM dir (roster + key store)."""
    project = tmp_path / "holder"
    team = project / ".claude" / "agents"
    _roster(team, "alice")
    _store(team)
    return project, team


def _spec(**over) -> dict:
    base = dict(
        issuer_team="team-b", holder_team="team-a", target_path="/abs/b/shared",
        permitted_ops="write", expires_at=_FAR, max_uses=1, approver="alice",
        ticket_id="T-1", reason_code="collab", grant_id="g-1",
        timestamp="2026-09-30T00:00:00Z",
    )
    base.update(over)
    return base


def _sign(project: Path, team: Path, **over) -> dict[str, str]:
    return grants.sign_ed25519_grant(
        project, team_dir=team, private_pem=_PRIV, key_id=_KEY_ID, **_spec(**over)
    )


def _manifest() -> dict:
    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "Team A", "privilege_profile": "confined"},
        framework="claude",
    )
    m["team_id"] = "team-a"
    return m


def _plant_hmac_write_grant(project: Path, grant_id: str = "evil") -> dict[str, str]:
    """What an in-sandbox agent can do: HMAC-sign a write row with the inherited key."""
    rec = {c: "" for c in grants.GRANT_COLUMNS}
    rec.update(_spec(grant_id=grant_id, target_path="/home/op/.ssh"))
    rec["max_uses"], rec["uses"] = "1", "0"
    rec["signature"] = grants.sign_grant(rec, key=_HMAC_KEY)
    grants._append_grant_row(project, rec)
    return rec


# --------------------------------------------------------------------------------------------
# E1: Ed25519 roundtrip + widening
# --------------------------------------------------------------------------------------------

def test_sign_verify_roundtrip_and_widening(layout):
    project, team = layout
    rec = _sign(project, team)
    assert rec["sig_scheme"] == "ed25519" and rec["key_id"] == _KEY_ID
    assert len(rec["signature"]) == 128
    assert grants.verify_grant_signature(rec, team_dir=team)
    grants.validate_grant(rec, team_dir=team)  # must not raise
    added = apply_held_grants_to_write_roots(_manifest(), project, team_dir=team)
    assert added == ["/abs/b/shared"]


def test_ed25519_grant_needs_no_hmac_key(layout, monkeypatch):
    monkeypatch.delenv(grants.GRANT_KEY_ENV, raising=False)
    project, team = layout
    _sign(project, team)
    assert grants.granted_write_roots(project, holder_team="team-a", team_dir=team) == [
        "/abs/b/shared"
    ]


def test_no_team_dir_applies_nothing(layout, capsys):
    project, team = layout
    _sign(project, team)
    assert apply_held_grants_to_write_roots(_manifest(), project) == []
    assert "not applied" in capsys.readouterr().err


def test_legacy_ledger_header_gains_scheme_columns(layout):
    project, team = layout
    # A pre-PR-E ledger: old header, one HMAC read grant.
    legacy = {c: "" for c in grants.GRANT_COLUMNS}
    legacy.update(_spec(grant_id="old", permitted_ops="read"))
    legacy["max_uses"], legacy["uses"] = "1", "0"
    legacy["signature"] = grants.sign_grant(legacy, key=_HMAC_KEY)
    log = project / grants.GRANT_LOG_REL
    log.parent.mkdir(parents=True)
    with log.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(grants.GRANT_COLUMNS))
        w.writeheader()
        w.writerow(legacy)
    _sign(project, team, grant_id="new")
    with log.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert list(reader.fieldnames or []) == list(grants.GRANT_LEDGER_COLUMNS)
        rows = list(reader)
    assert grants.verify_grant_signature(rows[0], key=_HMAC_KEY)  # old signature unchanged
    assert grants.verify_grants(project, team_dir=team, key=_HMAC_KEY) == []


# --------------------------------------------------------------------------------------------
# Domain separation (purpose tag)
# --------------------------------------------------------------------------------------------

def test_payload_carries_purpose_tag_first(layout):
    project, team = layout
    rec = _sign(project, team)
    values = grants._ed25519_signed_values(rec)
    assert values[0] == grants.GRANT_PURPOSE_TAG == "agentteams-grant-v1"
    # The signature does not verify over the untagged field list.
    assert ed25519_verify(_PUB, values, rec["signature"])
    assert not ed25519_verify(_PUB, grants._signed_values(rec), rec["signature"])


def test_signature_over_untagged_bytes_is_not_a_grant(layout):
    """A signature the operator made over the same field bytes in another domain (no tag), e.g.
    a decision, must not be accepted as a grant."""
    project, team = layout
    rec = _sign(project, team)
    rec["signature"] = ed25519_sign(_PRIV, grants._signed_values(rec))
    assert not grants.verify_grant_signature(rec, team_dir=team)
    with pytest.raises(grants.GrantError, match="invalid signature"):
        grants.validate_grant(rec, team_dir=team)


def test_grant_signature_replayed_as_decision_is_refused(tmp_path):
    """Vice versa: a decision row crafted so its payload equals a grant payload (a replayed grant
    signature) is refused by the decision verifier."""
    from agentteams.cli import decision_log as dl

    _store(tmp_path)
    grant_values = ["agentteams-grant-v1", "g-1", "team-b"]
    sig = ed25519_sign(_PRIV, grant_values)
    row = {"date": "agentteams-grant-v1|g-1|team-b", "sig_scheme": "ed25519",
           "key_id": _KEY_ID, "signature": sig}
    assert grants.payload_claims_grant_purpose(dl._decision_signature_values(row))
    with pytest.raises(RuntimeError, match="grant signing domain"):
        dl._assert_ed25519_sufficient(row, output_dir=tmp_path, action="x")


def test_payload_claims_grant_purpose():
    assert grants.payload_claims_grant_purpose(["agentteams-grant-v1", "x"])
    assert grants.payload_claims_grant_purpose(["agentteams-grant-v1|x"])
    assert not grants.payload_claims_grant_purpose(["2026-09-30", "agentteams-grant-v1"])
    assert not grants.payload_claims_grant_purpose(["agentteams-grant-v10", "x"])


# --------------------------------------------------------------------------------------------
# HMAC write grants refused (no transitional flag)
# --------------------------------------------------------------------------------------------

def test_hmac_write_grant_does_not_widen(layout, monkeypatch, capsys):
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _HMAC_KEY)
    project, team = layout
    _plant_hmac_write_grant(project)
    assert apply_held_grants_to_write_roots(_manifest(), project, team_dir=team) == []
    err = capsys.readouterr().err
    assert "REFUSED" in err and "--sign-grant" in err


def test_hmac_write_grant_reported_by_verify_with_migration(layout, monkeypatch):
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _HMAC_KEY)
    project, team = layout
    _plant_hmac_write_grant(project)
    problems = grants.verify_grants(project, team_dir=team)
    assert len(problems) == 1 and "--sign-grant" in problems[0]


def test_issue_grant_refuses_write(layout):
    project, team = layout
    with pytest.raises(grants.GrantError, match="--sign-grant"):
        grants.issue_grant(project, team_dir=team, key=_HMAC_KEY, **_spec())
    assert not (project / grants.GRANT_LOG_REL).exists()


def test_hmac_read_grant_still_valid(layout):
    project, team = layout
    rec = grants.issue_grant(project, team_dir=team, key=_HMAC_KEY, **_spec(permitted_ops="read"))
    grants.validate_grant(rec, team_dir=team, key=_HMAC_KEY)
    assert grants.granted_write_roots(project, holder_team="team-a", team_dir=team,
                                      key=_HMAC_KEY) == []


def test_red_team_planted_root_roster_plus_hmac_grant_no_widening(tmp_path, monkeypatch, capsys):
    """The row-34 regression: the agent plants <project>/references/security-approvers.txt and
    HMAC-signs a write grant with the inherited key. The next --update must not widen."""
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _HMAC_KEY)
    project = tmp_path / "holder"
    team = project / ".claude" / "agents"
    team.mkdir(parents=True)
    _roster(project, "alice")  # planted at the project root
    _plant_hmac_write_grant(project)
    assert apply_held_grants_to_write_roots(_manifest(), project, team_dir=team) == []
    err = capsys.readouterr().err
    assert "IGNORED" in err and "REFUSED" in err


# --------------------------------------------------------------------------------------------
# E3: team-dir roster only
# --------------------------------------------------------------------------------------------

def test_root_roster_ignored_with_warning(tmp_path, capsys):
    project = tmp_path / "holder"
    team = project / ".claude" / "agents"
    _store(team)
    _roster(project, "alice")  # only a project-root roster
    with pytest.raises(grants.GrantError, match="explicit approver roster"):
        _sign(project, team)
    err = capsys.readouterr().err
    assert "IGNORED" in err and str(team / "references" / "security-approvers.txt") in err


def test_root_roster_does_not_rescue_widening(layout, capsys):
    project, team = layout
    _sign(project, team)
    (team / "references" / "security-approvers.txt").unlink()
    _roster(project, "alice")
    assert grants.granted_write_roots(project, holder_team="team-a", team_dir=team) == []
    assert "IGNORED" in capsys.readouterr().err


def test_off_roster_approver_refused(layout):
    project, team = layout
    with pytest.raises(grants.GrantError, match="not on the security-approver roster"):
        _sign(project, team, approver="mallory")


# --------------------------------------------------------------------------------------------
# Missing / wrong key
# --------------------------------------------------------------------------------------------

def test_missing_verify_key_refused_before_append(layout):
    project, team = layout
    (team / "references" / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").unlink()
    with pytest.raises(grants.GrantError, match="refusing to append"):
        _sign(project, team)
    assert not (project / grants.GRANT_LOG_REL).exists()


def test_wrong_verify_key_refused_before_append(layout):
    project, team = layout
    _store(team, pub=_keypair()[1])
    with pytest.raises(grants.GrantError, match="refusing to append"):
        _sign(project, team)


def test_key_removed_after_signing_stops_widening(layout, capsys):
    project, team = layout
    _sign(project, team)
    (team / "references" / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").unlink()
    assert grants.granted_write_roots(project, holder_team="team-a", team_dir=team) == []
    assert "cannot be Ed25519-verified" in capsys.readouterr().err


def test_ed25519_row_without_team_dir_fails_closed(layout):
    project, team = layout
    rec = _sign(project, team)
    with pytest.raises(grants.GrantError, match="no holder team dir"):
        grants.verify_grant_signature(rec)


# --------------------------------------------------------------------------------------------
# Tamper detection
# --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field,value,match", [
    ("target_path", "/abs/b/EVERYTHING", "invalid signature"),
    ("expires_at", "2199-01-01T00:00:00Z", "invalid signature"),
    ("approver", "bob", "invalid signature"),
    ("key_id", "other-key", "cannot be Ed25519-verified"),
    ("sig_scheme", "hmac", "REFUSED"),
    ("sig_scheme", "", "REFUSED"),
])
def test_tampered_row_refused(layout, field, value, match):
    project, team = layout
    _roster(team, "alice", "bob")
    rec = _sign(project, team)
    rec[field] = value
    with pytest.raises(grants.GrantError, match=match):
        grants.validate_grant(rec, team_dir=team, key=_HMAC_KEY)


def test_tampered_ledger_does_not_widen(layout):
    project, team = layout
    _sign(project, team)
    log = project / grants.GRANT_LOG_REL
    log.write_text(log.read_text("utf-8").replace("/abs/b/shared", "/abs/b/EVERYTHING"), "utf-8")
    assert grants.granted_write_roots(project, holder_team="team-a", team_dir=team) == []


def test_relabelled_hmac_signature_cannot_pass_as_ed25519(layout):
    """An HMAC signature with sig_scheme relabelled to ed25519 fails Ed25519 verification."""
    project, team = layout
    rec = {c: "" for c in grants.GRANT_LEDGER_COLUMNS}
    rec.update(_spec(), sig_scheme="ed25519", key_id=_KEY_ID)
    rec["max_uses"], rec["uses"] = "1", "0"
    rec["signature"] = hmac_sign(_HMAC_KEY, grants._ed25519_signed_values(rec))
    with pytest.raises(grants.GrantError, match="invalid signature"):
        grants.validate_grant(rec, team_dir=team)


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------

def _main(argv: list[str]) -> int:
    from agentteams.cli.app import main

    return main(argv)


def _write_spec(tmp_path: Path, **over) -> Path:
    spec = {k: v for k, v in _spec(**over).items() if k not in ("grant_id", "timestamp")}
    spec["key_id"] = _KEY_ID
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _keyfile(tmp_path: Path) -> Path:
    path = tmp_path / "op.pem"
    path.write_text(_PRIV, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_cli_sign_grant_roundtrip(tmp_path, monkeypatch, capsys):
    team = tmp_path / "team"
    _roster(team, "alice")
    _store(team)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(_keyfile(tmp_path)))
    spec = _write_spec(tmp_path)
    rc = _main(["--sign-grant", str(spec), "--framework", "claude", "--output", str(team)])
    out = capsys.readouterr().out
    assert rc == 0 and "(ed25519)" in out
    assert _main(["--verify-grants", "--framework", "claude", "--output", str(team)]) == 0
    assert grants.granted_write_roots(team, holder_team="team-a", team_dir=team) == [
        "/abs/b/shared"
    ]


def test_cli_team_dir_derived_from_project(tmp_path, monkeypatch):
    project = tmp_path / "holder"
    team = project / ".claude" / "agents"
    _roster(team, "alice")
    _store(team)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(_keyfile(tmp_path)))
    spec = _write_spec(tmp_path)
    assert _main(["--sign-grant", str(spec), "--framework", "claude",
                  "--project", str(project)]) == 0
    assert (project / grants.GRANT_LOG_REL).exists()  # ledger at the workspace root
    assert grants.granted_write_roots(project, holder_team="team-a", team_dir=team)


def test_cli_sign_grant_requires_keyfile_env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", raising=False)
    spec = _write_spec(tmp_path)
    assert _main(["--sign-grant", str(spec), "--framework", "claude",
                  "--output", str(tmp_path)]) == 1
    assert "AGENTTEAMS_DECISION_ED25519_KEYFILE is not set" in capsys.readouterr().err


def test_cli_sign_grant_requires_key_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(_keyfile(tmp_path)))
    spec = _write_spec(tmp_path)
    data = json.loads(spec.read_text("utf-8"))
    del data["key_id"]
    spec.write_text(json.dumps(data), "utf-8")
    assert _main(["--sign-grant", str(spec), "--framework", "claude",
                  "--output", str(tmp_path)]) == 1
    assert "key_id" in capsys.readouterr().err


def test_cli_sign_grant_missing_roster(tmp_path, monkeypatch, capsys):
    team = tmp_path / "team"
    _store(team)
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", str(_keyfile(tmp_path)))
    spec = _write_spec(tmp_path)
    assert _main(["--sign-grant", str(spec), "--framework", "claude",
                  "--output", str(team)]) == 1
    assert "explicit approver roster" in capsys.readouterr().err


def test_cli_issue_grant_refuses_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _HMAC_KEY)
    _roster(tmp_path, "alice")
    spec = _write_spec(tmp_path)
    assert _main(["--issue-grant", str(spec), "--framework", "claude",
                  "--output", str(tmp_path)]) == 1
    assert "--sign-grant" in capsys.readouterr().err


def test_cli_verify_grants_flags_hmac_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(grants.GRANT_KEY_ENV, _HMAC_KEY)
    _roster(tmp_path, "alice")
    _plant_hmac_write_grant(tmp_path)
    assert _main(["--verify-grants", "--framework", "claude", "--output", str(tmp_path)]) == 1
    assert "--sign-grant" in capsys.readouterr().err


def test_sign_grant_is_standalone_exclusive_with_package_team(tmp_path):
    with pytest.raises(SystemExit):
        _main(["--package-team", str(tmp_path), "--sign-grant", "x.json"])
