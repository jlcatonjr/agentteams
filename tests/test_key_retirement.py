"""Retiring an Ed25519 verify key without revoking what it already signed (``cli/key_retirement.py``).

A retirement record ``<key_id>.retired.json`` lists the sha256 of every signature the old key made, signed by a
different key that is not retired. Afterwards a signature under the old key verifies only when it is listed. Every
key here is a throwaway generated under ``tmp_path``; no operator key is read.
"""

from __future__ import annotations

import csv
import itertools
import json
import os
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams import carveouts  # noqa: E402
from agentteams.cli import decision_log as dl  # noqa: E402
from agentteams.cli import dev_merge  # noqa: E402
from agentteams.cli import grants  # noqa: E402
from agentteams.cli import key_retirement as kr  # noqa: E402
from agentteams.cli import operator_signing as ops  # noqa: E402
from agentteams.cli import signed_ledger as sl  # noqa: E402
from agentteams.cli import toolchain_group as tg  # noqa: E402
from agentteams.cli.app import main  # noqa: E402

OLD, NEW, THIRD = "op-old", "op-new", "op-third"
_KEY_ENV = "AGENTTEAMS_DECISION_ED25519_KEYFILE"
_RETIRED = "is retired"
_UNTRUSTED = "cannot be trusted"
_SEQ = itertools.count(1)  # Ed25519 is deterministic: each document differs, so each signature does


def _pair() -> tuple[str, str]:
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


@pytest.fixture
def keys(tmp_path) -> dict[str, str]:
    """Private PEMs by key id; each public half is installed in the team store of ``tmp_path/proj/.claude/agents``."""
    store = _team(tmp_path) / kr.STORE_REL
    store.mkdir(parents=True, exist_ok=True)
    privates = {}
    for key_id in (OLD, NEW, THIRD):
        privates[key_id], public = _pair()
        (store / f"{key_id}.pub.pem").write_text(public, encoding="utf-8")
    return privates


@pytest.fixture(autouse=True)
def _signing_env(monkeypatch):
    """Let the signers run here: no agent-session marker, integrity and install location pre-approved."""
    monkeypatch.setattr(dev_merge, "agent_session", lambda: None)
    monkeypatch.setattr(ops, "presign_integrity_check", lambda: True)
    monkeypatch.setattr(ops, "_location_gate", lambda roots, allow: False)


def _team(tmp_path: Path) -> Path:
    team = tmp_path / "proj" / ".claude" / "agents"
    (team / "references").mkdir(parents=True, exist_ok=True)
    (team / "references" / "agent-privilege.json").write_text("{}\n", encoding="utf-8")
    (team / "references" / "security-approvers.txt").write_text("security\n", encoding="utf-8")
    return team


def _record(team: Path, keys: dict[str, str], signatures: list[str], *, signer: str = NEW,
            sign_with: str | None = None, key_id: str = OLD) -> dict:
    """Write a retirement record for ``key_id`` listing ``signatures``, signed by ``sign_with`` (default: signer)."""
    doc = {"key_id": key_id, "not_after": "2026-12-05", "signed_by": signer,
           "signature_sha256": sorted({kr.signature_digest(s) for s in signatures})}
    doc["signature"] = sl.ed25519_sign(keys[sign_with or signer], kr.signed_values(doc))
    kr.record_path(team, key_id).write_text(json.dumps(doc), encoding="utf-8")
    return doc


# ------------------------------------------------------------------------------------------- the seven verify sites

def _decision_site(team: Path, keys: dict[str, str], key_id: str = OLD):
    row = {"date": "2026-11-01", "action_reviewed": "cleanup-x", "verdict": "PASS", "conditions_verified": f"c{next(_SEQ)}",
           "author": "security", "prev_digest": "", "sig_scheme": "ed25519", "key_id": key_id}
    row["signature"] = sl.ed25519_sign(keys[key_id], dl._decision_signature_values(row))

    def verify() -> None:
        dl._assert_ed25519_sufficient(row, output_dir=team, action="cleanup-x")
    return row["signature"], verify


def _carveout_site(team: Path, keys: dict[str, str], key_id: str = OLD):
    root = team.parent.parent
    registry = {"decision_log": ".claude/agents", "sequence": next(_SEQ), "carveouts": []}
    value = sl.ed25519_sign(keys[key_id], carveouts.signed_fields(registry, key_id))
    signed = {**registry, "signature": {"scheme": "ed25519", "key_id": key_id, "value": value}}

    def verify() -> None:
        problem = carveouts.signature_problem(root, signed)
        if problem:
            raise RuntimeError(problem.detail)
    return value, verify


def _group_site(team: Path, keys: dict[str, str], key_id: str = OLD):
    doc = {"version": tg.FORMAT_VERSION, "scope": tg.SCOPE, "group": ["acme/tool"], "purpose": tg.PURPOSE,
           "workspaces": [{"repo": "acme/app", "team": "app"}], "sequence": next(_SEQ), "key_id": key_id,
           "derives_from": ""}
    doc["signature"] = sl.ed25519_sign(keys[key_id], tg.signed_values(doc))
    return doc["signature"], lambda: tg.verify_signature(doc, team)


def _dev_merge_site(team: Path, keys: dict[str, str], key_id: str = OLD):
    doc = {"version": dev_merge.FORMAT_VERSION, "scope": dev_merge.SCOPE, "branch": "dev", "required_checks": ["ci"],
           "workspaces": [{"repo": "acme/app", "team": "app"}], "purpose": dev_merge.PURPOSE, "sequence": next(_SEQ),
           "key_id": key_id, "derives_from": ""}
    doc["signature"] = sl.ed25519_sign(keys[key_id], dev_merge.signed_values(doc))
    return doc["signature"], lambda: dev_merge.verify_signature(doc, team)


def _grant_site(team: Path, keys: dict[str, str], key_id: str = OLD):
    record = {c: "" for c in grants.GRANT_COLUMNS}
    record.update({"grant_id": f"g-{next(_SEQ)}", "issuer_team": "b", "holder_team": "a", "target_path": "/x",
                   "permitted_ops": "read", "expires_at": "2099-01-01T00:00:00Z", "max_uses": "1",
                   "sig_scheme": "ed25519", "key_id": key_id})
    record["signature"] = sl.ed25519_sign(keys[key_id], grants._ed25519_signed_values(record))

    def verify() -> None:
        if not grants.verify_grant_signature(record, team_dir=team):
            raise RuntimeError("grant signature does not verify")
    return record["signature"], verify


def _direct_load_site(team: Path, keys: dict[str, str], key_id: str = OLD):
    """The chokepoint itself, as every caller (including the signers' verify-before-write) reaches it."""
    signature = sl.ed25519_sign(keys[key_id], [f"anything-{next(_SEQ)}"])
    return signature, lambda: dl._load_verify_key(team, key_id, signature=signature)


_SITES = {"decision-row": _decision_site, "carve-out-registry": _carveout_site, "toolchain-group": _group_site,
          "dev-merge": _dev_merge_site, "grant": _grant_site, "load-verify-key": _direct_load_site}


@pytest.mark.parametrize("site", sorted(_SITES))
def test_retired_key_accepts_snapshotted_signature_and_refuses_others(site, tmp_path, keys):
    team = _team(tmp_path)
    kept, verify_kept = _SITES[site](team, keys)
    fresh, verify_fresh = _SITES[site](team, keys)
    verify_kept()  # negative control: no record, today's behaviour, both verify
    verify_fresh()
    _record(team, keys, [kept])
    verify_kept()
    with pytest.raises(RuntimeError, match=_RETIRED):
        verify_fresh()


@pytest.mark.parametrize("site", sorted(_SITES))
def test_tampered_snapshot_refuses_every_signature_at_every_site(site, tmp_path, keys):
    team = _team(tmp_path)
    kept, verify_kept = _SITES[site](team, keys)
    doc = _record(team, keys, [kept])
    verify_kept()  # negative control: the untampered record keeps it
    doc["signature_sha256"] = sorted([*doc["signature_sha256"], "0" * 64])  # one digest added after signing
    kr.record_path(team, OLD).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not verify"):
        verify_kept()


def test_other_keys_are_unaffected(tmp_path, keys):
    team = _team(tmp_path)
    _record(team, keys, [])
    signature, verify = _decision_site(team, keys, key_id=NEW)
    verify()  # the signer key is not retired
    with pytest.raises(RuntimeError, match=_RETIRED):
        _decision_site(team, keys, key_id=OLD)[1]()


# ------------------------------------------------------------------------------------------- who may sign the record

def test_record_signed_by_the_retired_key_is_refused(tmp_path, keys):
    team = _team(tmp_path)
    kept, verify = _direct_load_site(team, keys)
    _record(team, keys, [kept], signer=OLD)  # self-signed: the holder of OLD could widen its own snapshot
    with pytest.raises(RuntimeError, match="cannot sign its own retirement"):
        verify()
    _record(team, keys, [kept], signer=NEW, sign_with=OLD)  # claims NEW, signed by OLD
    with pytest.raises(RuntimeError, match="does not verify"):
        verify()
    _record(team, keys, [kept])  # negative control: really signed by NEW
    verify()


def test_record_whose_signer_is_retired_or_absent_is_refused(tmp_path, keys):
    team = _team(tmp_path)
    kept, verify = _direct_load_site(team, keys)
    _record(team, keys, [kept])
    verify()  # negative control
    _record(team, keys, [], key_id=NEW, signer=THIRD)  # the signer NEW is retired in turn
    with pytest.raises(RuntimeError, match="itself retired"):
        verify()
    kr.record_path(team, NEW).unlink()
    (team / kr.STORE_REL / f"{NEW}.pub.pem").unlink()  # the signer key is absent
    with pytest.raises(RuntimeError, match=_UNTRUSTED):
        verify()


@pytest.mark.parametrize("damage", ["symlink", "malformed", "unsorted", "wrong-key-id", "bad-date"])
def test_untrustworthy_record_refuses_everything(damage, tmp_path, keys):
    team = _team(tmp_path)
    kept, verify = _direct_load_site(team, keys)
    doc = _record(team, keys, [kept])
    verify()  # negative control
    path = kr.record_path(team, OLD)
    if damage == "symlink":
        real = tmp_path / "elsewhere.json"
        real.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.unlink()
        path.symlink_to(real)
    elif damage == "malformed":
        path.write_text("{not json", encoding="utf-8")
    else:
        bad = dict(doc)
        if damage == "unsorted":
            bad["signature_sha256"] = ["f" * 64, "0" * 64]
        elif damage == "wrong-key-id":
            bad["key_id"] = THIRD
        else:
            bad["not_after"] = "2026-13-40"
        path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(RuntimeError, match=_UNTRUSTED):
        verify()


# --------------------------------------------------------------------------------------------- domain separation

def test_retirement_payload_does_not_verify_as_a_decision_row(tmp_path, keys):
    team = _team(tmp_path)
    row = {"date": kr.PURPOSE, "action_reviewed": OLD, "verdict": "2026-12-05", "conditions_verified": NEW,
           "author": "security", "prev_digest": "", "sig_scheme": "ed25519", "key_id": NEW}
    row["signature"] = sl.ed25519_sign(keys[NEW], dl._decision_signature_values(row))
    with pytest.raises(RuntimeError, match="key-retirement signing domain"):
        dl._assert_ed25519_sufficient(row, output_dir=team, action=OLD)
    row["date"] = "2026-12-05"  # negative control: the same row outside the domain verifies
    row["signature"] = sl.ed25519_sign(keys[NEW], dl._decision_signature_values(row))
    dl._assert_ed25519_sufficient(row, output_dir=team, action=OLD)


def test_decision_signature_does_not_verify_as_a_retirement_record(tmp_path, keys):
    team = _team(tmp_path)
    decision_sig, _ = _decision_site(team, keys, key_id=NEW)
    doc = _record(team, keys, [])
    _direct_load_site(team, keys)  # the genuine record loads
    doc["signature"] = decision_sig
    kr.record_path(team, OLD).write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not verify"):
        kr.verified_record(team, OLD)
    assert kr.payload_claims_retirement_purpose(kr.signed_values(doc))
    assert not kr.payload_claims_retirement_purpose(["2026-11-01", "cleanup-x"])


def test_decision_minter_refuses_a_payload_in_the_retirement_domain(tmp_path, keys, monkeypatch, capsys):
    team = _team(tmp_path)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"date": kr.PURPOSE, "action_reviewed": "cleanup-x", "verdict": "PASS",
                                "key_id": NEW, "author": "security"}), encoding="utf-8")
    monkeypatch.setenv(_KEY_ENV, str(tmp_path / "absent.key"))
    assert main(["--sign-decision", str(spec), "--output", str(team)]) == 1
    assert "key-retirement purpose tag" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------- sign-time refusal

def _keyfile(tmp_path: Path, keys: dict[str, str], key_id: str) -> Path:
    path = tmp_path / f"{key_id}.key"
    path.write_text(keys[key_id], encoding="utf-8")
    path.chmod(0o600)
    return path


def test_retired_key_refuses_new_decision_signing(tmp_path, keys, monkeypatch, capsys):
    team = _team(tmp_path)
    monkeypatch.setattr(ops, "_confirm", lambda digest, confirm: True)
    spec = tmp_path / "spec.json"
    log = team / "references" / "security-decisions.log.csv"
    spec.write_text(json.dumps({"date": "2026-11-26", "action_reviewed": "cleanup-x", "verdict": "PASS",
                                "key_id": OLD, "author": "security"}), encoding="utf-8")
    monkeypatch.setenv(_KEY_ENV, str(_keyfile(tmp_path, keys, OLD)))
    _record(team, keys, [])
    assert main(["--sign-decision", str(spec), "--output", str(team)]) == 1
    assert _RETIRED in capsys.readouterr().err and not log.exists()
    kr.record_path(team, OLD).unlink()  # negative control: without the record the same spec signs
    assert main(["--sign-decision", str(spec), "--output", str(team)]) == 0
    assert log.exists()


def test_retired_key_refuses_new_group_signing(tmp_path, keys, monkeypatch, capsys):
    team = _team(tmp_path)
    monkeypatch.setattr(ops, "_confirm", lambda digest, confirm: True)
    doc = {"version": tg.FORMAT_VERSION, "scope": tg.SCOPE, "group": ["acme/tool"], "purpose": tg.PURPOSE,
           "workspaces": [{"repo": "repo:acme/app", "team": "team:app"}], "sequence": 1, "key_id": OLD,
           "derives_from": "approved-policy"}
    path = tmp_path / "group.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(_KEY_ENV, str(_keyfile(tmp_path, keys, OLD)))
    _record(team, keys, [])
    assert tg.sign_group_file(path, team) == 1
    assert _RETIRED in capsys.readouterr().err and "signature" not in json.loads(path.read_text(encoding="utf-8"))
    kr.record_path(team, OLD).unlink()  # negative control
    assert tg.sign_group_file(path, team) == 0


# -------------------------------------------------------------------------------------- the operator verb

def _log_with(team: Path, signatures: list[str]) -> None:
    with (team / "references" / "security-decisions.log.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "action_reviewed", "verdict", "sig_scheme", "key_id", "signature"])
        for n, sig in enumerate(signatures):
            writer.writerow(["2026-11-01", f"act-{n}", "PASS", "ed25519", OLD, sig])
        writer.writerow(["2026-11-02", "other", "PASS", "ed25519", NEW, "ab" * 64])  # another key: not kept


def test_retire_verb_snapshots_the_logs_and_includes(tmp_path, keys, monkeypatch, capsys):
    team = _team(tmp_path)
    monkeypatch.setattr(ops, "_confirm", lambda digest, confirm: True)
    in_log, verify_log = _direct_load_site(team, keys)
    group_sig, verify_group = _group_site(team, keys)
    unlisted, verify_unlisted = _direct_load_site(team, keys)
    _log_with(team, [in_log])
    group_doc = {"key_id": OLD, "signature": group_sig}
    include = tmp_path / "toolchain-group.json"
    include.write_text(json.dumps(group_doc), encoding="utf-8")
    monkeypatch.setenv(_KEY_ENV, str(_keyfile(tmp_path, keys, NEW)))
    rc = main(["--retire-verify-key", OLD, "--key-id", NEW, "--team-dir", str(team), "--include", str(include)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "security-decisions.log.csv line 2: 2026-11-01 act-0 PASS" in out and "toolchain-group.json" in out
    record = json.loads(kr.record_path(team, OLD).read_text(encoding="utf-8"))
    assert record["signed_by"] == NEW and len(record["signature_sha256"]) == 2
    verify_log()
    verify_group()
    with pytest.raises(RuntimeError, match=_RETIRED):
        verify_unlisted()


@pytest.mark.parametrize("case", ["same-key", "signer-retired", "include-without-signature", "agent-session"])
def test_retire_verb_refuses_before_the_key_is_read(case, tmp_path, keys, monkeypatch, capsys):
    team = _team(tmp_path)
    monkeypatch.setenv(_KEY_ENV, str(tmp_path / "absent.key"))
    argv = ["--retire-verify-key", OLD, "--key-id", NEW, "--team-dir", str(team)]
    if case == "same-key":
        argv[3] = OLD
    elif case == "signer-retired":
        _record(team, keys, [], key_id=NEW, signer=THIRD)
    elif case == "include-without-signature":
        empty = tmp_path / "empty.json"
        empty.write_text("{}", encoding="utf-8")
        argv += ["--include", str(empty)]
    else:
        monkeypatch.setattr(dev_merge, "agent_session", lambda: "claude-code")
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "The key was not read." in err and not kr.record_path(team, OLD).exists()


# ---------------------------------------------------------------------------------------- --verify-integrity

def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=/dev/null", "-C", str(cwd), *args], check=True, capture_output=True)


def test_verify_integrity_prints_one_line_per_retired_key(tmp_path, keys, capsys):
    team = _team(tmp_path)
    kept, _ = _direct_load_site(team, keys)
    main(["--verify-integrity", "--output", str(team)])
    assert "Retired verify key" not in capsys.readouterr().out  # negative control: no record, no line
    _record(team, keys, [kept])
    main(["--verify-integrity", "--output", str(team)])
    out = capsys.readouterr().out
    assert (f"Retired verify key {OLD}: not after 2026-12-05, 1 signature(s) kept, signed by {NEW}, verifies"
            in out)
    _record(team, keys, [kept], signer=NEW, sign_with=OLD)
    main(["--verify-integrity", "--output", str(team)])
    assert f"Retired verify key {OLD}: DOES NOT VERIFY" in capsys.readouterr().out


def test_verify_integrity_warns_when_the_snapshot_grew(tmp_path, keys, capsys):
    team = _team(tmp_path)
    kept, _ = _direct_load_site(team, keys)
    later, _ = _direct_load_site(team, keys)
    root = team.parent.parent
    _git(root, "init", "-q")
    _record(team, keys, [kept, later])
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "retire")
    _record(team, keys, [kept])  # shrinking is the expected direction
    main(["--verify-integrity", "--output", str(team)])
    assert "WARNING" not in capsys.readouterr().out
    _git(root, "commit", "-q", "-am", "shrink")
    _record(team, keys, [kept, later])  # re-signed wider than the committed copy
    main(["--verify-integrity", "--output", str(team)])
    assert "1 digest(s) added since the committed copy" in capsys.readouterr().out


def test_signature_digest_is_over_the_decoded_bytes():
    sig = "ab" * 64
    assert kr.signature_digest(sig) == kr.signature_digest(sig.upper()) == kr.signature_digest(f" {sig} ")
    assert kr.signature_digest("not hex") is None and kr.signature_digest("") is None
    assert os.sep not in kr.record_path(Path("t"), OLD).name
