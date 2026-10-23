"""agentteams.vulnscan.escalations (R6 WS-2). Network- and Docker-free.

Real Ed25519 keys (``cryptography``, the ``[signing]`` extra). HOME and the vulnscan config/state
dirs point into tmp_path; the canonicaliser is a fake.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from agentteams.cli.signed_ledger import ed25519_sign
from agentteams.vulnscan import escalations as E
from agentteams.vulnscan import ledger

REPO = "/srv/repos/alice-app"


def _canon(target: str) -> str | None:
    if target.startswith(REPO):
        return REPO
    return "web1" if target == "web1" else None


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(home / ".config" / "agentteams"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    key = Ed25519PrivateKey.generate()
    vk = home / ".config" / "agentteams" / "verify-keys"
    vk.mkdir(parents=True)
    os.chmod(vk, 0o700)
    (vk / "op.pub.pem").write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return {"pem": pem, "home": home, "tmp": tmp_path, "vk": vk}


def _rec(kind: str, target: str = REPO, evidence: str = "seen", *, run_id: str = "2026-10-22-aaaaaa",
         n: int = 0) -> E.Escalation:
    with ledger.locked():
        return E.record(kind, target, evidence, request_id=f"{n:032x}", run_id=run_id, canonical=_canon)


def _signed(env: dict, esc_id: str, evidence: str = "checked: false positive") -> dict:
    rec = E.build_resolution(esc_id, evidence, "op")
    return {**rec, "signature": ed25519_sign(env["pem"], E.resolution_signed_values(rec))}


# ---------------------------------------------------------------- scope and recording

def test_scope_rules() -> None:
    for kind in ("malicious", "ioc", "integrity", E.FLOOD_KIND):
        assert E.scope_for(kind, REPO, _canon) == "*"
    assert E.scope_for("injection", REPO + "/sub/dir", _canon) == REPO
    assert E.scope_for("injection", "/nowhere", _canon) == "*"
    assert E.scope_for("other", "web1", _canon) == "web1"
    assert E.scope_for("other", "", _canon) == "*"
    with pytest.raises(E.EscalationError):
        E.scope_for("bogus", REPO, _canon)


def test_record_writes_row_and_refuses_internal_kind(env: dict) -> None:
    esc = _rec("injection", REPO + "/lib")
    rows = ledger.read()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "escalation"
    assert {k: row[k] for k in ("esc_id", "esc_kind", "scope", "evidence", "request_id", "run_id")} == {
        "esc_id": esc.id, "esc_kind": "injection", "scope": REPO, "evidence": "seen",
        "request_id": "0" * 32, "run_id": "2026-10-22-aaaaaa"}
    assert esc.opened_ts == row["ts"]
    with pytest.raises(E.EscalationError):
        _rec(E.FLOOD_KIND)
    with pytest.raises(E.EscalationError):
        _rec("bogus")
    with pytest.raises(E.EscalationError), ledger.locked():
        E.record("other", REPO, "x", request_id="r", run_id="", canonical=_canon)


def test_dedupe_open_scope_kind(env: dict) -> None:
    a = _rec("injection", REPO, n=1)
    b = _rec("injection", REPO + "/other/file.py", n=2)
    assert a == b and len(ledger.read()) == 1
    c = _rec("other", REPO, n=3)
    d = _rec("injection", "web1", n=4)
    assert len({a.id, c.id, d.id}) == 3 and len(ledger.read()) == 3


def test_uncanonical_targets_collapse_to_star(env: dict) -> None:
    for i in range(E.MAX_PER_RUN + 3):
        _rec("other", f"unknown-host{i}", n=i)       # none canonicalise: all scope "*", deduped
    assert len(ledger.read()) == 1 and E.blocking("web1") is not None


def test_flood_triggers_once_per_run(env: dict) -> None:
    def canon(t: str) -> str | None:
        return t if t.startswith("/r/") else None

    def rec(i: int, run_id: str = "run-a") -> E.Escalation:
        with ledger.locked():
            return E.record("other", f"/r/{i}", f"e{i}", request_id=f"{i:032x}", run_id=run_id, canonical=canon)

    for i in range(E.MAX_PER_RUN):
        rec(i)
    assert len(ledger.read()) == E.MAX_PER_RUN and E.blocking("/r/none") is None
    flood = rec(100)
    assert flood.kind == E.FLOOD_KIND and flood.scope == "*" and "more than" in flood.evidence
    assert rec(101) == flood and rec(102) == flood
    assert len(ledger.read()) == E.MAX_PER_RUN + 1
    assert E.blocking("/r/none") == flood
    # another run is unaffected by this run's count (but still blocked by the open flood)
    other = rec(200, run_id="run-b")
    assert other.kind == "other" and other.run_id == "run-b"


def test_blocking_star_and_target(env: dict) -> None:
    esc = _rec("injection", REPO)
    assert E.blocking(REPO) == esc
    assert E.blocking("web1") is None
    star = _rec("ioc", "anything")
    assert E.blocking("web1") == star and E.blocking(REPO) == star
    rows = ledger.read()
    assert E.blocking(REPO, rows) == star
    assert [e.id for e in E.open_escalations(rows)] == [esc.id, star.id]


def test_evidence_sanitisation(env: dict) -> None:
    raw = "\x1b[31mred\x1b[0m line1\nline2\x00\x07\u202etail\x1b]0;title\x07end\t!" + "x" * 5000
    esc = _rec("other", REPO, raw)
    assert "\x1b" not in esc.evidence and "\x00" not in esc.evidence and "\u202e" not in esc.evidence
    assert esc.evidence.startswith("red line1\nline2tailend\t!")
    assert len(esc.evidence) == E.EVIDENCE_MAX
    line = E.describe(esc)
    assert "\n" not in line and "\t" not in line and "\x1b" not in line
    assert line.startswith(f"{esc.id} [other] scope={REPO} run=2026-10-22-aaaaaa") and line.endswith("...")


# ---------------------------------------------------------------- resolution

def test_resolve_other_and_refuses_serious(env: dict) -> None:
    other = _rec("other", REPO)
    serious = _rec("malicious", REPO)
    with pytest.raises(E.EscalationError, match="signed"):
        E.resolve_other(serious.id, "looked fine")
    with pytest.raises(E.EscalationError, match="non-empty"):
        E.resolve_other(other.id, " \x1b[0m ")
    row = E.resolve_other(other.id, "benign")
    assert row["kind"] == "resolved" and row["esc_id"] == other.id and row["method"] == "operator-mac"
    assert [e.id for e in E.open_escalations()] == [serious.id]
    with pytest.raises(E.EscalationError, match="not an open"):
        E.resolve_other(other.id, "again")
    with pytest.raises(E.EscalationError, match="resolve_other"):
        E.build_resolution(_rec("other", "web1").id, "x", "op")


def test_signed_resolution_clears(env: dict) -> None:
    esc = _rec("integrity", REPO)
    assert E.blocking(REPO) == esc
    row = E.apply_signed_resolution(_signed(env, esc.id))
    assert row["kind"] == "resolved" and row["method"] == "operator-ed25519"
    assert row["signature"] and row["key_id"] == "op" and row["scope"] == "*"
    assert E.blocking(REPO) is None and E.open_escalations() == []


def test_tampered_signature_refused(env: dict) -> None:
    esc = _rec("malicious", REPO)
    signed = _signed(env, esc.id)
    with pytest.raises(E.EscalationError, match="does not verify"):
        E.apply_signed_resolution({**signed, "evidence": "something else"})
    other_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    forged = {**signed, "signature": ed25519_sign(other_key, E.resolution_signed_values(signed))}
    with pytest.raises(E.EscalationError, match="does not verify"):
        E.apply_signed_resolution(forged)
    with pytest.raises(E.EscalationError, match="fields"):
        E.apply_signed_resolution({**signed, "extra": 1})
    assert E.blocking(REPO) == esc


def test_verify_key_mode_checked(env: dict) -> None:
    esc = _rec("malicious", REPO)
    signed = _signed(env, esc.id)
    os.chmod(env["vk"] / "op.pub.pem", 0o666)
    with pytest.raises(E.EscalationError, match="writable"):
        E.apply_signed_resolution(signed)


def test_unknown_or_resolved_id_refused(env: dict) -> None:
    esc = _rec("ioc", REPO)
    signed = _signed(env, esc.id)
    E.apply_signed_resolution(signed)
    with pytest.raises(E.EscalationError, match="not an open"):
        E.apply_signed_resolution(signed)            # replay
    unknown = {"esc_id": "ve-" + "0" * 16, "evidence": "x", "issued": signed["issued"], "key_id": "op"}
    unknown["signature"] = ed25519_sign(env["pem"], E.resolution_signed_values(unknown))
    with pytest.raises(E.EscalationError, match="not an open"):
        E.apply_signed_resolution(unknown)
    with pytest.raises(E.EscalationError, match="not an open"):
        E.build_resolution("ve-" + "1" * 16, "x", "op")


def test_signed_resolution_refuses_other_kind(env: dict) -> None:
    esc = _rec("other", REPO)
    rec = {"esc_id": esc.id, "evidence": "x", "issued": "2026-10-22T00:00:00+00:00", "key_id": "op"}
    rec["signature"] = ed25519_sign(env["pem"], E.resolution_signed_values(rec))
    with pytest.raises(E.EscalationError, match="resolve_other"):
        E.apply_signed_resolution(rec)


def test_sign_resolution_operator_flow(env: dict, monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    from agentteams.cli import operator_signing as ops_

    keyfile = env["tmp"] / "op.pem"
    keyfile.write_text(env["pem"])
    monkeypatch.setattr(ops_, "_signing_preflight", lambda roots, allow: (str(keyfile), False))
    esc = _rec("injection", REPO)
    rec = E.build_resolution(esc.id, "reviewed the diff", "op")
    assert E.sign_resolution(rec, confirm_sha256="0" * 64) is None
    from agentteams.vulnscan import paths   # the digest covers the operator's choices; its base is the config dir

    digest = ops_.review_digest("vulnscan-escalation-resolution", ops_.review_choices(rec), paths.config_dir(), "op")
    signed = E.sign_resolution(rec, confirm_sha256=digest)
    assert signed is not None and "RESOLUTION" in capsys.readouterr().out
    E.apply_signed_resolution(signed)
    assert E.open_escalations() == []


# ---------------------------------------------------------------- ledger integrity

def test_tail_truncation_raises(env: dict) -> None:
    _rec("other", REPO, n=1)
    _rec("malicious", REPO, n=2)
    p = ledger.ledger_path()
    lines = p.read_text().splitlines()
    p.write_text(lines[0] + "\n")                   # drop the malicious escalation
    with pytest.raises(ValueError, match="removed"):
        E.open_escalations()
    with pytest.raises(ValueError):
        E.blocking(REPO)
    p.unlink()
    with pytest.raises(ValueError, match="missing"):
        E.open_escalations()


def test_tampered_row_raises(env: dict) -> None:
    _rec("malicious", REPO)
    p = ledger.ledger_path()
    row = json.loads(p.read_text())
    row["esc_kind"] = "other"
    p.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="altered"):
        E.open_escalations()
