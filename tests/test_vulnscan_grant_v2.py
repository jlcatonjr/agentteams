"""v2 remediation grants (vfr-s S1-S3, vfr-d.D4): signed host x level pairs, restart units/window, apps.conf sha256,
quarantine paths, runtimes, the re-issued ancestor. Network-free; real Ed25519 keys (the ``[signing]`` extra)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import grant, grant_v2  # noqa: E402
from tests._vulnscan_helpers import operator_home, sign_grant  # noqa: E402

SHA = "a" * 64
ANACONDA = "/root/anaconda3.disabled-20261101"
USER_HOME = "/".join(("", "home", "svc"))          # built, not written: a user's home path in a committed file
USER_SITE = USER_HOME + "/.local"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return operator_home(tmp_path, monkeypatch)


def ancestor(env: dict[str, Any]) -> str:
    """Install and revoke a v1 server grant (the one being re-issued); return its id."""
    old = sign_grant(env["pem"], grant.build(ops=["server-audit", "server-dryrun"], repo_roots=[],
                                            hosts=["jlc-prod", "vk-api", "vk-bastion"], days=30, max_uses=200,
                                            key_id="op", governance_dir=str(env["gov"])))
    grant.install(old)
    assert grant.revoke(old["grant_id"])
    return old["grant_id"]


def server_v2(env: dict[str, Any], **kw: Any) -> dict[str, Any]:
    """Build (unsigned) an OD3-shaped re-issue: four levels on three hosts."""
    hosts = ["jlc-prod", "vk-api", "vk-bastion"]
    args: dict[str, Any] = dict(
        ops=["server-audit", "server-dryrun", "server-apply"], repo_roots=[], hosts=hosts, days=30, max_uses=20,
        key_id="op", governance_dir=str(env["gov"]),
        server_levels=[f"{h}:{lv}" for h in hosts for lv in grant_v2.LEVELS],
        remove_paths=[f"jlc-prod:{ANACONDA}", f"vk-bastion:{USER_SITE}", "vk-api:/opt/old-app"],
        restart_units=["vk-api:nginx.service"], restart_window="07:00-11:00", apps_conf_sha256=SHA,
        major_allowed=["vk-api:api.service"], max_read_uses=500)
    args.update(kw)
    if "ancestor" not in args:
        args["ancestor"] = ancestor(env)
    return grant.build(**args)


def test_v2_grant_signs_verifies_and_covers_exact_pairs(env: dict[str, Any]) -> None:
    rec = server_v2(env)
    assert rec["version"] == 2 and grant.signed_values(rec)[0] == grant_v2.PURPOSE_TAG_V2
    signed = sign_grant(env["pem"], rec)
    grant.install(signed)
    valid, problems = grant.load_active()
    assert [g["grant_id"] for g in valid] == [rec["grant_id"]] and not problems
    assert grant.covers(signed, "server-apply", "jlc-prod:l2-security")
    assert grant.covers(signed, "server-apply", f"jlc-prod:remove-path:{ANACONDA}")
    assert not grant.covers(signed, "server-apply", f"jlc-prod:remove-path:{ANACONDA}/sub")   # exact, never prefix
    assert not grant.covers(signed, "server-apply", f"vk-api:remove-path:{ANACONDA}")          # path is per host
    assert not grant.covers(signed, "server-apply", "jlc-prod:l2-security:/x")                 # no stray suffix
    assert not grant.covers(signed, "server-apply", "web9:l2-security")
    assert grant.covers(signed, "server-audit", "vk-api")


@pytest.mark.parametrize("field,value", [
    ("server_levels", ["jlc-prod:l2-security"]), ("restart_units", []), ("apps_conf_sha256", "b" * 64),
    ("remove_paths", [f"jlc-prod:{ANACONDA}"]), ("restart_window", "00:00-23:59"), ("ancestor", "vg-" + "0" * 16),
    ("max_read_uses", 499)])
def test_every_v2_field_is_signed(env: dict[str, Any], field: str, value: Any) -> None:
    signed = sign_grant(env["pem"], server_v2(env))
    with pytest.raises(grant.GrantError):
        grant.verify({**signed, field: value})


def test_v1_signature_never_verifies_as_v2_and_v1_cannot_carry_v2_ops(env: dict[str, Any]) -> None:
    rec = server_v2(env)
    v1_sig = sign_grant(env["pem"], {k: rec[k] for k in grant.FIELDS})["signature"]
    with pytest.raises(grant.GrantError, match="signature"):
        grant.verify({**rec, "signature": v1_sig})
    with pytest.raises(grant.GrantError, match="need a v2 grant"):
        grant.verify(sign_grant(env["pem"], {k: rec[k] for k in grant.FIELDS}))


@pytest.mark.parametrize("change,match", [
    ({"max_uses": 21}, "at most 20"),
    ({"ops": ["server-apply", "stage-fix"], "repo_roots": ["/tmp/x"]}, "server operations only"),
    ({"days": 31}, "days"),
    ({"server_levels": []}, "host:level"),
    ({"server_levels": ["jlc-prod:l5-containers"]}, "level one of"),
    ({"server_levels": ["all:l2-security"]}, "exact hosts.conf"),
    ({"remove_paths": ["jlc-prod:/root"]}, "top-level"),
    ({"remove_paths": ["vk-bastion:" + USER_HOME]}, "home directory"),
    ({"remove_paths": ["vk-bastion:" + USER_HOME + "/.ssh"]}, "ssh"),
    ({"remove_paths": ["jlc-prod:/etc/nginx"]}, "trust root"),
    ({"remove_paths": ["jlc-prod:/var/backups/agentteams/quarantine/x"]}, "trust root"),
    ({"remove_paths": ["jlc-prod:/var"]}, "top-level"),
    ({"remove_paths": ["jlc-prod:/var/backups"]}, "contains the trust root"),
    ({"remove_paths": ["jlc-prod:/usr/lib/python3"]}, "/usr/local"),
    ({"remove_paths": ["jlc-prod:/opt/a/../b"]}, "normalised"),
    ({"remove_paths": ["jlc-prod:/opt/a b"]}, "letters"),
    ({"remove_paths": ["jlc-prod://opt/x"]}, "normalised"),
    ({"remove_paths": ["jlc-prod:/usr/local/bin/tool"]}, "trust root /usr/local/bin"),
    ({"remove_paths": ["jlc-prod:/usr/local/lib/python3.9"]}, "trust root /usr/local/lib"),
    ({"remove_paths": ["vk-bastion:" + USER_HOME + "/.config/agentteams/x"]}, r"trust root /home/\*/\.config/agentteams"),
    ({"remove_paths": ["vk-bastion:" + USER_HOME + "/.config"]}, "contains the trust root"),
    ({"max_read_uses": 0}, "max_read_uses 1..500"),
    ({"max_read_uses": 501}, "max_read_uses 1..500"),
    ({"server_levels": ["jlc-prod:l2-security"], "remove_paths": [], "restart_units": [], "major_allowed": [],
      "apps_conf_sha256": "", "restart_window": ""}, "restart_window"),
    ({"remove_paths": []}, "exact paths"),
    ({"apps_conf_sha256": ""}, "apps.conf sha256"),
    ({"restart_window": ""}, "restart_window"),
    ({"restart_window": "25:00-26:00"}, "restart_window"),
    ({"restart_units": ["vk-api:nginx"]}, ".service"),
    ({"major_allowed": ["web9:api.service"]}, "major_allowed"),
])
def test_v2_build_bounds(env: dict[str, Any], change: dict[str, Any], match: str) -> None:
    with pytest.raises(grant.GrantError, match=match):
        server_v2(env, **change)


def test_ancestor_must_be_revoked_first_and_archived(env: dict[str, Any]) -> None:
    live = sign_grant(env["pem"], grant.build(ops=["server-audit"], repo_roots=[], hosts=["jlc-prod"], days=5,
                                             max_uses=5, key_id="op", governance_dir=str(env["gov"])))
    grant.install(live)
    with pytest.raises(grant.GrantError, match="revoke it first"):
        server_v2(env, ancestor=live["grant_id"])
    with pytest.raises(grant.GrantError, match="not a revoked"):
        server_v2(env, ancestor="vg-" + "1" * 16)
    with pytest.raises(grant.GrantError, match="ancestor"):
        grant.build(ops=["server-apply"], repo_roots=[], hosts=["jlc-prod"], days=5, max_uses=5, key_id="op",
                    governance_dir=str(env["gov"]), server_levels=["jlc-prod:l2-security"],
                    restart_window="08:00-10:00")
    assert grant.revoke(live["grant_id"])
    assert not (grant.grants_dir() / f"{live['grant_id']}.json").exists()
    archived = json.loads((grant.revoked_dir() / f"{live['grant_id']}.json").read_text())
    assert archived["grant_id"] == live["grant_id"]
    assert grant.load_active() == ([], [])            # an archived grant is never honoured
    assert server_v2(env, ancestor=live["grant_id"])["ancestor"] == live["grant_id"]


def test_forged_archived_ancestor_is_refused(env: dict[str, Any]) -> None:
    grant.revoked_dir().mkdir(parents=True, exist_ok=True)
    fake = "vg-" + "2" * 16
    (grant.revoked_dir() / f"{fake}.json").write_text(json.dumps({"grant_id": fake, "signature": "x"}))
    with pytest.raises(grant.GrantError, match="not a revoked, validly signed"):
        server_v2(env, ancestor=fake)


def test_runtime_patch_grant_bounds(env: dict[str, Any]) -> None:
    anc = ancestor(env)
    (env["tmp"] / "repos").mkdir()
    base: dict[str, Any] = dict(ops=["stage-fix", "runtime-patch"], repo_roots=[str(env["tmp"] / "repos")], hosts=[],
                                days=30, max_uses=100, key_id="op", governance_dir=str(env["gov"]), ancestor=anc)
    g = sign_grant(env["pem"], grant.build(**base, runtimes=["node@22"]))
    grant.verify(g)
    assert grant.covers(g, "runtime-patch", "node@22") and not grant.covers(g, "runtime-patch", "node")
    for bad in ("python@3.12", "python", "git", "gh", "openssh", "gnupg", "gnupg@2.2", "Node!"):
        with pytest.raises(grant.GrantError, match="runtime"):
            grant.build(**base, runtimes=[bad])
    with pytest.raises(grant.GrantError, match="at least one signed runtime"):
        grant.build(**base)
    with pytest.raises(grant.GrantError, match="does not name runtime-patch"):
        grant.build(**{**base, "ops": ["stage-fix"]}, runtimes=["node@22"])


def test_restart_window_wraps_midnight() -> None:
    def at(h: int, m: int) -> datetime:
        return datetime(2026, 11, 2, h, m, tzinfo=UTC)
    assert grant_v2.in_window("07:00-11:00", at(7, 0)) and not grant_v2.in_window("07:00-11:00", at(11, 0))
    assert grant_v2.in_window("22:00-04:00", at(23, 30)) and grant_v2.in_window("22:00-04:00", at(3, 59))
    assert not grant_v2.in_window("22:00-04:00", at(12, 0)) and not grant_v2.in_window("", at(8, 0))


def test_unknown_build_fields_refused(env: dict[str, Any]) -> None:
    with pytest.raises(grant.GrantError, match="unknown grant fields"):
        grant.build(ops=["server-audit"], repo_roots=[], hosts=["h"], days=1, max_uses=1, key_id="op",
                    governance_dir=str(env["gov"]), allow_reboot=True)


def test_merge_stays_reserved_and_server_apply_is_grantable() -> None:
    assert grant.OPS_RESERVED == {"merge"} and {"server-apply", "runtime-patch"} <= grant.OPS_V1


# ---------------------------------------------------------------- Q1: separate apply and read caps

def test_server_apply_uses_and_reads_have_separate_caps(env: dict[str, Any]) -> None:
    g = sign_grant(env["pem"], server_v2(env, max_uses=2, max_read_uses=3))
    rows: list[dict[str, Any]] = []

    def begun(op: str) -> None:
        rows.append({"kind": "begun", "grant_id": g["grant_id"], "op": op})
    for _ in range(2):
        begun("server-apply")
    assert not grant_v2.uses_left(g, "server-apply", rows) and grant_v2.uses_left(g, "server-audit", rows)
    for op in ("server-audit", "server-dryrun", "server-audit"):
        begun(op)
    assert not grant_v2.uses_left(g, "server-audit", rows) and not grant_v2.uses_left(g, "server-dryrun", rows)
    v1 = {"grant_id": "vg-" + "3" * 16, "ops": ["server-audit"], "max_uses": 1}
    assert grant_v2.uses_left(v1, "server-audit", [])
    assert not grant_v2.uses_left(v1, "server-audit", [{"kind": "begun", "grant_id": v1["grant_id"], "op": "x"}])


def test_max_read_uses_only_on_server_apply_grants(env: dict[str, Any]) -> None:
    (env["tmp"] / "repos").mkdir()
    with pytest.raises(grant.GrantError, match="max_read_uses"):
        grant.build(ops=["stage-fix", "runtime-patch"], repo_roots=[str(env["tmp"] / "repos")], hosts=[], days=5,
                    max_uses=5, key_id="op", governance_dir=str(env["gov"]), runtimes=["node@22"],
                    ancestor=ancestor(env), max_read_uses=5)


# ---------------------------------------------------------------- Rule 12: the revoke archive (5a, 5b, 5c)

def test_a_revoked_grant_moved_back_is_never_honoured(env: dict[str, Any]) -> None:
    g = sign_grant(env["pem"], grant.build(ops=["server-audit"], repo_roots=[], hosts=["jlc-prod"], days=5,
                                           max_uses=5, key_id="op", governance_dir=str(env["gov"])))
    grant.install(g)
    grant.revoke(g["grant_id"])
    (grant.revoked_dir() / f"{g['grant_id']}.json").replace(grant.grants_dir() / f"{g['grant_id']}.json")
    shutil_copy = (grant.grants_dir() / f"{g['grant_id']}.json").read_bytes()
    (grant.revoked_dir() / f"{g['grant_id']}.json").write_bytes(shutil_copy)   # un-revoked, archive kept
    valid, problems = grant.load_active()
    assert valid == [] and "was revoked" in problems[0], problems


def test_an_archived_ancestor_must_be_that_grant(env: dict[str, Any]) -> None:
    other = sign_grant(env["pem"], grant.build(ops=["server-audit"], repo_roots=[], hosts=["jlc-prod"], days=5,
                                               max_uses=5, key_id="op", governance_dir=str(env["gov"])))
    grant.revoked_dir().mkdir(parents=True, exist_ok=True)
    fake = "vg-" + "4" * 16
    (grant.revoked_dir() / f"{fake}.json").write_text(json.dumps(other))   # validly signed, but another grant
    with pytest.raises(grant.GrantError, match="not a revoked, validly signed"):
        server_v2(env, ancestor=fake)
    assert not grant.ancestor_ok(grant.revoked_dir() / f"{fake}.json", fake)


def test_the_lineage_is_checked_at_load_too(env: dict[str, Any]) -> None:
    anc = ancestor(env)
    g = sign_grant(env["pem"], server_v2(env, ancestor=anc))
    grant.install(g)
    assert [v["grant_id"] for v in grant.load_active()[0]] == [g["grant_id"]]
    archived = grant.revoked_dir() / f"{anc}.json"
    saved = archived.read_bytes()
    archived.unlink()                                              # the ancestor's archive is gone
    valid, problems = grant.load_active()
    assert valid == [] and "lineage" in problems[0], problems
    archived.write_bytes(saved)
    (grant.grants_dir() / f"{anc}.json").write_bytes(saved)        # the ancestor moved back (un-revoked)
    valid, problems = grant.load_active()
    assert valid == [] and any("lineage" in p for p in problems) and any("was revoked" in p for p in problems)


def test_check_only_validates_the_reissue_while_the_ancestor_is_still_installed(env: dict[str, Any]) -> None:
    old = sign_grant(env["pem"], grant.build(ops=["server-audit", "server-dryrun"], repo_roots=[],
                                            hosts=["jlc-prod", "vk-api", "vk-bastion"], days=30, max_uses=200,
                                            key_id="op", governance_dir=str(env["gov"])))
    grant.install(old)
    rec = server_v2(env, ancestor=old["grant_id"], before_revoke=True)        # nothing revoked yet: still valid
    assert rec["ancestor"] == old["grant_id"]
    with pytest.raises(grant.GrantError, match="revoke it first"):
        server_v2(env, ancestor=old["grant_id"])                              # a real sign still needs the revoke
    with pytest.raises(grant.GrantError, match="max_read_uses"):
        server_v2(env, ancestor=old["grant_id"], before_revoke=True, max_read_uses=0)   # a refusal, old untouched
    assert (grant.grants_dir() / f"{old['grant_id']}.json").is_file()
    with pytest.raises(grant.GrantError, match="installed, validly signed"):
        server_v2(env, ancestor="vg-" + "5" * 16, before_revoke=True)
