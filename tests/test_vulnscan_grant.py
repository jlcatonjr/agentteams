"""agentteams.vulnscan.grant: validity window, revocation, aggregate cap, directory/file trust, scope (E12).

Real Ed25519 keys (``cryptography``, the ``[signing]`` extra), signed directly with
``signed_ledger.ed25519_sign``: the operator signing flow itself is covered by
``test_vulnscan_signing_digest.py``. HOME and the vulnscan config dir point into ``tmp_path``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")

from agentteams.vulnscan import grant
from tests._vulnscan_helpers import operator_home, sign_grant


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Build an isolated HOME with an operator verify key, a governance dir and a code root.

    Args:
        tmp_path: Pytest tmp dir.
        monkeypatch: Pytest monkeypatch.

    Returns:
        ``{"tmp", "home", "pem", "gov", "code"}``.
    """
    world = operator_home(tmp_path, monkeypatch)
    code = tmp_path / "code"
    code.mkdir()
    return {**world, "code": code}


def _signed(env: dict[str, Any], *, now: datetime | None = None, **over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"ops": ["stage-fix"], "repo_roots": [str(env["code"])], "hosts": [], "days": 1,
                            "max_uses": 5, "key_id": "op", "governance_dir": str(env["gov"]), "now": now}
    args.update(over)
    rec = grant.build(**args)
    return sign_grant(env["pem"], rec)


# ---------------------------------------------------------------- validity window

def test_expiry_boundary(env: dict[str, Any]) -> None:
    issued = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rec = _signed(env, now=issued)
    expires = datetime.fromisoformat(rec["expires"])
    grant.verify(rec, now=issued)
    grant.verify(rec, now=expires - timedelta(seconds=1))
    for at in (expires, expires + timedelta(seconds=1)):
        with pytest.raises(grant.GrantError, match="not valid now"):
            grant.verify(rec, now=at)


def test_a_grant_issued_in_the_future_is_refused(env: dict[str, Any]) -> None:
    """A grant has no future skew allowance: ``issued`` must not be after now (R4 concerns setting the clock back)."""
    issued = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rec = _signed(env, now=issued)
    for before in (timedelta(seconds=1), timedelta(minutes=10), timedelta(days=1)):
        with pytest.raises(grant.GrantError, match="not valid now"):
            grant.verify(rec, now=issued - before)


def test_a_naive_issued_time_is_refused_by_load_active(env: dict[str, Any]) -> None:
    control = _signed(env)
    grant.install(control)
    rec = {k: v for k, v in _signed(env).items() if k != "signature"}
    rec["issued"] = rec["issued"].replace("+00:00", "")       # validly signed, but naive
    rec = sign_grant(env["pem"], rec)
    grant.install(rec)
    valid, problems = grant.load_active()
    assert [v["grant_id"] for v in valid] == [control["grant_id"]]          # the aware one still loads
    assert len(problems) == 1 and problems[0].startswith(rec["grant_id"]) and "offset-naive" in problems[0]


def _plant(record: dict[str, Any]) -> Path:
    """Write ``record`` as a mode-600 grant file the way ``install`` would, but without its id check."""
    d = grant.grants_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f"vg-{os.urandom(8).hex()}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return path


def _resigned(env: dict[str, Any], **change: Any) -> dict[str, Any]:
    """Return a fresh grant with ``change`` applied to its fields, then validly signed by the operator key."""
    body = {k: v for k, v in _signed(env).items() if k != "signature"}
    body.update(change)
    return sign_grant(env["pem"], body)


def test_extra_grant_fields_are_refused(env: dict[str, Any]) -> None:
    """K-G4: a field outside FIELDS is not covered by the signature, so its mere presence refuses the grant."""
    rec = {**_signed(env), "allow_merge": True}               # the signature itself still verifies
    with pytest.raises(grant.GrantError, match="unexpected or missing fields"):
        grant.verify(rec)
    grant.install(rec)
    valid, problems = grant.load_active()
    assert valid == [] and len(problems) == 1 and "unexpected or missing fields" in problems[0]


def _over_30_days() -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0)
    return {"issued": now.isoformat(), "expires": (now + timedelta(days=30, minutes=2)).isoformat()}


@pytest.mark.parametrize(("case", "match"), [
    ("reserved-merge", "not honoured in v1"),
    ("reserved-server-apply", "not honoured in v1"),
    ("unknown-op", "not honoured in v1"),
    ("empty-ops", "not honoured in v1"),
    ("lifetime-over-30-days", "bounds exceed"),
    ("max-uses-0", "bounds exceed"),
    ("max-uses-501", "bounds exceed"),
    ("grant-id-uppercase", "malformed grant_id"),
    ("grant-id-short", "malformed grant_id"),
    ("grant-id-path", "malformed grant_id"),
])
def test_verify_bounds_refuse_validly_signed_grants(env: dict[str, Any], case: str, match: str) -> None:
    """V1: every bound holds at verify time, even for a record the operator key really signed."""
    change: dict[str, Any] = {
        "reserved-merge": {"ops": ["merge"]},
        "reserved-server-apply": {"ops": ["stage-fix", "server-apply"]},
        "unknown-op": {"ops": ["rm-rf"]},
        "empty-ops": {"ops": []},
        "lifetime-over-30-days": _over_30_days(),
        "max-uses-0": {"max_uses": 0},
        "max-uses-501": {"max_uses": grant.MAX_USES + 1},
        "grant-id-uppercase": {"grant_id": "vg-ABCDEF0123456789"},
        "grant-id-short": {"grant_id": "vg-0123"},
        "grant-id-path": {"grant_id": "../vg-0123456789abcdef"},
    }[case]
    rec = _resigned(env, **change)
    with pytest.raises(grant.GrantError, match=match):
        grant.verify(rec)
    _plant(rec)
    valid, problems = grant.load_active()
    assert valid == [] and len(problems) == 1 and match in problems[0], problems


def test_verify_bounds_controls_load(env: dict[str, Any]) -> None:
    """V1 control: the boundary values themselves (30 days, 500 uses) are honoured."""
    now = datetime.now(UTC).replace(microsecond=0)
    _plant(_resigned(env, issued=now.isoformat(), expires=(now + timedelta(days=30)).isoformat(),
                     max_uses=grant.MAX_USES))
    valid, problems = grant.load_active()
    assert len(valid) == 1 and problems == []


def _other_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    return Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def test_a_grant_signed_by_the_wrong_key_is_refused(env: dict[str, Any]) -> None:
    """V2: a well-formed grant naming key ``op`` but signed by another key is refused, in verify and load_active."""
    body = {k: v for k, v in _signed(env).items() if k != "signature"}
    forged = sign_grant(_other_pem(), body)
    with pytest.raises(grant.GrantError, match="signature does not verify"):
        grant.verify(forged)
    grant.install(forged)
    valid, problems = grant.load_active()
    assert valid == [] and "signature does not verify" in problems[0]


def test_a_wrong_key_grant_is_refused_by_executor_act_without_a_begun_row(env: dict[str, Any]) -> None:
    """V2 through the executor: refused, ledgered as ``refused`` only — no use is reserved."""
    from agentteams.vulnscan import executor, ledger
    body = {k: v for k, v in _signed(env, ops=["server-audit"], repo_roots=[], hosts=["web1"]).items()
            if k != "signature"}
    grant.install(sign_grant(_other_pem(), body))
    out = executor.act("server-audit", "web1", {}, request_id="r-wrong-key")
    assert out["status"] == "refused" and "signature does not verify" in out["detail"], out
    rows = ledger.read()
    assert [r["kind"] for r in rows] == ["refused"] and not any(r["kind"] == "begun" for r in rows)


def test_an_old_format_grant_without_governance_dir_is_refused(env: dict[str, Any]) -> None:
    rec = _signed(env)
    old = {k: v for k, v in rec.items() if k != "governance_dir"}
    with pytest.raises(grant.GrantError, match="fields"):
        grant.verify(old)
    grant.install(old)
    valid, problems = grant.load_active()
    assert valid == [] and "fields" in problems[0]


# ---------------------------------------------------------------- revocation and the aggregate cap

def test_revoke_then_load_active_excludes_it(env: dict[str, Any]) -> None:
    a, b = _signed(env), _signed(env)
    grant.install(a)
    grant.install(b)
    assert {g["grant_id"] for g in grant.load_active()[0]} == {a["grant_id"], b["grant_id"]}
    assert grant.revoke(a["grant_id"]) is True
    assert [g["grant_id"] for g in grant.load_active()[0]] == [b["grant_id"]]
    assert grant.revoke(a["grant_id"]) is False
    assert grant.revoke("../vg-0000000000000000") is False


def test_a_third_valid_grant_refuses_all(env: dict[str, Any]) -> None:
    grants = [_signed(env) for _ in range(grant.MAX_ACTIVE + 1)]
    for g in grants[:-1]:
        grant.install(g)
    assert len(grant.load_active()[0]) == grant.MAX_ACTIVE
    grant.install(grants[-1])
    valid, problems = grant.load_active()
    assert valid == [] and "exceed the aggregate cap" in problems[-1]


def test_invalid_grants_do_not_count_toward_the_cap(env: dict[str, Any]) -> None:
    for _ in range(grant.MAX_ACTIVE):
        grant.install(_signed(env))
    expired = _signed(env, now=datetime.now(UTC) - timedelta(days=3))
    grant.install(expired)
    valid, problems = grant.load_active()
    assert len(valid) == grant.MAX_ACTIVE and len(problems) == 1 and "not valid now" in problems[0]


def test_signing_past_the_cap_is_refused_before_it_disables_the_others(env: dict[str, Any]) -> None:
    """2026-10-25: a third grant was signed while two were valid, and every grant stopped working."""
    assert grant.cap_problem() == ""
    held = [_signed(env) for _ in range(grant.MAX_ACTIVE)]
    grant.install(held[0])
    assert grant.cap_problem() == ""                      # one more still fits
    grant.install(held[1])
    why = grant.cap_problem()
    assert "exceeds the aggregate cap" in why and all(g["grant_id"] in why for g in held) and "grant revoke" in why
    grant.install(_signed(env, now=datetime.now(UTC) - timedelta(days=3)))   # expired grants do not count
    assert grant.cap_problem() == why
    grant.grants_dir().chmod(0o777)                       # an unsafe grants dir is reported, not signed into
    assert "refusing all grants" in grant.cap_problem()
    grant.grants_dir().chmod(0o700)


# ---------------------------------------------------------------- directory and file trust

def test_a_group_writable_grants_dir_refuses_all(env: dict[str, Any]) -> None:
    grant.install(_signed(env))
    os.chmod(grant.grants_dir(), 0o770)
    valid, problems = grant.load_active()
    assert valid == [] and "group/world-writable" in problems[0]
    os.chmod(grant.grants_dir(), 0o700)
    assert len(grant.load_active()[0]) == 1


def test_a_grants_dir_owned_by_someone_else_refuses_all(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    grant.install(_signed(env))
    owner = grant.grants_dir().stat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: owner + 1)
    valid, problems = grant.load_active()
    assert valid == [] and "not owned by you" in problems[0]


def test_a_group_writable_grant_file_is_refused(env: dict[str, Any]) -> None:
    path = grant.install(_signed(env))
    os.chmod(path, 0o666)
    valid, problems = grant.load_active()
    assert valid == [] and "group/world-writable" in problems[0]


def test_a_symlinked_grant_file_is_refused(env: dict[str, Any]) -> None:
    """A grant reached through a symlink is refused even when its target is a valid grant."""
    path = grant.install(_signed(env))
    target = env["tmp"] / "elsewhere.json"
    path.rename(target)
    path.symlink_to(target)
    valid, problems = grant.load_active()
    assert valid == [] and "not a regular file" in problems[0]


def test_a_group_writable_publish_grant_file_is_refused(env: dict[str, Any]) -> None:
    """publish.load_active applies the same per-file check before reading the file."""
    from agentteams.vulnscan import publish
    d = publish.grants_dir()
    d.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    f = d / "vp-x.json"
    f.write_text("{}")
    os.chmod(f, 0o620)
    valid, problems = publish.load_active()
    assert valid == [] and problems == ["vp-x.json: not owned by you or group/world-writable — refused"]


def _symlink_dir(link: Path, real: Path) -> None:
    """Move the directory ``link`` to ``real`` (keeping its 0700 mode) and put a symlink in its place."""
    link.rename(real)
    link.symlink_to(real)


def test_a_symlinked_grants_dir_refuses_all(env: dict[str, Any]) -> None:
    """X7: the directory is judged by its own final component (lstat), not by the 0700 directory it points at."""
    grant.install(_signed(env))
    _symlink_dir(grant.grants_dir(), env["tmp"] / "real-grants")
    valid, problems = grant.load_active()
    assert valid == [] and len(problems) == 1 and "symlink" in problems[0], problems


def test_a_symlinked_publish_grants_dir_refuses_all(env: dict[str, Any]) -> None:
    """X7: the publish-grant directory gets the same lstat check."""
    from agentteams.vulnscan import publish
    real = env["tmp"] / "real-publish-grants"
    real.mkdir(mode=0o700)
    os.chmod(real, 0o700)
    publish.grants_dir().parent.mkdir(parents=True, exist_ok=True)
    publish.grants_dir().symlink_to(real)
    valid, problems = publish.load_active()
    assert valid == [] and len(problems) == 1 and "symlink" in problems[0], problems


def test_a_symlinked_verify_key_file_is_refused(env: dict[str, Any]) -> None:
    """X7: a 0600 key moved out of the verify-keys dir and linked back is refused (O_NOFOLLOW), not followed."""
    rec = _signed(env)
    grant.verify(rec)                                                   # control: the real key verifies
    key = env["home"] / ".config" / "agentteams" / "verify-keys" / "op.pub.pem"
    moved = env["tmp"] / "op.pub.pem"
    key.rename(moved)
    key.symlink_to(moved)
    with pytest.raises(grant.GrantError, match="symlink"):
        grant.verify(rec)
    grant.install(rec)
    valid, problems = grant.load_active()
    assert valid == [] and "symlink" in problems[0]


@pytest.mark.parametrize("mode", [0o770, 0o720, 0o702, 0o777])
def test_a_group_or_world_writable_verify_keys_dir_is_refused(env: dict[str, Any], mode: int) -> None:
    """X7: anyone who can write the verify-keys directory can swap in their own key; refuse, whatever the file."""
    rec = _signed(env)
    vk = env["home"] / ".config" / "agentteams" / "verify-keys"
    os.chmod(vk, mode)
    try:
        with pytest.raises(grant.GrantError, match="verify-keys|group/world-writable"):
            grant.verify(rec)
    finally:
        os.chmod(vk, 0o700)
    grant.verify(rec)                                                   # control: a 0700 dir is fine
    os.chmod(vk, 0o755)
    grant.verify(rec)                                                   # readable is fine; writable is not


def test_a_symlinked_verify_keys_dir_is_refused(env: dict[str, Any]) -> None:
    """X7: a symlinked verify-keys directory is refused even when its target is a 0700 directory you own."""
    rec = _signed(env)
    _symlink_dir(env["home"] / ".config" / "agentteams" / "verify-keys", env["tmp"] / "real-vk")
    with pytest.raises(grant.GrantError, match="symlink"):
        grant.verify(rec)


def test_install_writes_mode_600_in_a_0700_dir(env: dict[str, Any]) -> None:
    path = grant.install(_signed(env))
    assert path.stat().st_mode & 0o777 == 0o600
    assert grant.grants_dir().stat().st_mode & 0o777 == 0o700
    with pytest.raises(grant.GrantError, match="unsigned"):
        grant.install({k: v for k, v in _signed(env).items() if k != "signature"})


# ---------------------------------------------------------------- unverified_values

def test_unverified_values_reads_every_json_file_and_ignores_non_strings(env: dict[str, Any]) -> None:
    d = env["tmp"] / "grants"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"governance_dir": "/gov/a"}))
    (d / "b.json").write_text("{not json")
    (d / "c.json").write_text(json.dumps({"governance_dir": 7}))
    (d / "d.json").write_text(json.dumps(["governance_dir", "/gov/d"]))
    (d / "e.json").write_text(json.dumps({"governance_dir": ""}))
    (d / "f.json").write_text(json.dumps({"governance_dir": ["/gov/f"]}))
    (d / "misnamed.json").write_text(json.dumps({"governance_dir": "/gov/m", "signature": "bogus"}))
    (d / "vg-0000000000000001.json").write_text(json.dumps({"governance_dir": "/gov/v"}))   # unsigned
    (d / "note.txt").write_text(json.dumps({"governance_dir": "/gov/txt"}))
    (d / "dir.json").mkdir()                                                               # unreadable
    assert grant.unverified_values(d, "governance_dir") == ["/gov/a", "/gov/m", "/gov/v"]
    assert grant.unverified_values(d, "ops_repo") == []
    assert grant.unverified_values(env["tmp"] / "absent", "governance_dir") == []


# ---------------------------------------------------------------- covers

def test_covers_subpaths_but_not_siblings(env: dict[str, Any]) -> None:
    root = env["tmp"] / "a" / "b"
    (root / "sub").mkdir(parents=True)
    (env["tmp"] / "a" / "bc").mkdir()
    rec = _signed(env, ops=["stage-fix", "server-audit"], repo_roots=[str(root)], hosts=["web1"])
    assert grant.covers(rec, "stage-fix", str(root))
    assert grant.covers(rec, "stage-fix", str(root / "sub"))
    assert not grant.covers(rec, "stage-fix", str(env["tmp"] / "a" / "bc"))
    assert not grant.covers(rec, "stage-fix", str(env["tmp"] / "a"))
    assert not grant.covers(rec, "stage-fix", str(root / ".." / "bc"))
    assert not grant.covers(rec, "open-pr", str(root))                          # op not granted
    assert grant.covers(rec, "server-audit", "web1")
    assert not grant.covers(rec, "server-audit", "web10")
    assert not grant.covers(rec, "server-audit", str(root))


def test_covers_refuses_a_symlink_out_of_the_root(env: dict[str, Any]) -> None:
    root = env["tmp"] / "a" / "b"
    root.mkdir(parents=True)
    outside = env["tmp"] / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    rec = _signed(env, repo_roots=[str(root)])
    assert not grant.covers(rec, "stage-fix", str(root / "link"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode-000 files")
def test_an_unreadable_grant_file_fails_closed(env: dict[str, Any]) -> None:
    """An unreadable file might name a protected repo: the tolerant reader raises instead of skipping it."""
    d = env["tmp"] / "grants-unreadable"
    d.mkdir()
    f = d / "a.json"
    f.write_text(json.dumps({"governance_dir": "/gov/a"}))
    os.chmod(f, 0)
    try:
        with pytest.raises(grant.GrantError, match="cannot read grant file a.json"):
            grant.unverified_values(d, "governance_dir")
    finally:
        os.chmod(f, 0o600)
