"""agentteams.vulnscan.known / known_cli: signed, expiring known-finding entries (vfr-k K1, K3; @security K2 conditions).

Real Ed25519 keys (``cryptography``, the ``[signing]`` extra) generated in ``tmp_path`` and signed directly with
``signed_ledger.ed25519_sign``, as ``test_vulnscan_grant.py`` does. HOME, the vulnscan config and state dirs point
into ``tmp_path``; the governance dir is a throwaway git repository whose committed index the loader reads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")

from agentteams.cli.signed_ledger import ed25519_sign, hmac_sign
from agentteams.vulnscan import engine, grant, known, known_cli, known_report
from agentteams.vulnscan.report import EXIT_CLEAN, EXIT_FINDINGS, Finding, Report
from tests._vulnscan_helpers import operator_home

PLAN, STEP = "vfr-k-known", "K4 entry review"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
DOCROOT = "secret-looking files under web root /var/www/email_api — confirm the server denies them " \
          "(request them from outside; expect 403/404)"


def _git(gov: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(gov), "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """An operator home with verify key ``op`` and a git governance dir holding a cleared decision row."""
    world = operator_home(tmp_path, monkeypatch)
    gov: Path = world["gov"]
    with (gov / "references" / "security-decisions.log.csv").open("a", encoding="utf-8") as fh:
        fh.write(f"2026-11-02,{PLAN},{STEP},CLEARED,closed,,,per-entry review,security\n")
    (gov / known.INDEX_REL).write_text(",".join(known.INDEX_HEADER) + "\n", encoding="utf-8")
    _git(gov, "init", "-q")
    _git(gov, "add", "-A")
    _git(gov, "commit", "-q", "-m", "init")
    return world


def pkg(**over: Any) -> Finding:
    """A HIGH npm package finding with a fix (the shape a run's summary carries)."""
    args: dict[str, Any] = {"kind": "package", "target": "webapp", "name": "xlsx", "version": "0.18.5",
                            "ecosystem": "npm", "severity": 3, "fix": "", "unfixed": 1, "ids": ["GHSA-aaaa"],
                            "locations": ["package-lock.json"]}
    args.update(over)
    return Finding(**args)


def host(evidence: str) -> Finding:
    return Finding(kind="host", target="jlc-prod", name="host", severity=3, detail=DOCROOT, evidence=evidence)


def _audit(files: dict[str, bytes], rules: list[str]) -> str:
    """Audit-log text as L1-audit.sh prints it for one web-root hit."""
    lines = [f"HOSTCHECK\tHIGH\t{DOCROOT}", f"ACTION      {DOCROOT}"]
    lines += [f"HOSTEVIDENCE\tfile\t{hashlib.sha256(b).hexdigest()}\t{p}" for p, b in files.items()]
    lines += [f"HOSTEVIDENCE\tdeny\t{hashlib.sha256(r.encode()).hexdigest()}\t{r}" for r in rules]
    return "\n".join(lines) + "\n"


EMAIL_FILES = {"/var/www/email_api/token.json": b"{\"t\": 1}", "/var/www/email_api/client_secret_x.json": b"{}"}
DENY = ["location ~ ^/email_api/ { => deny all;"]


def _evidence(files: dict[str, bytes] = EMAIL_FILES, rules: list[str] = DENY) -> str:
    rep = engine.scan_inventory(_audit(files, rules), "jlc-prod")
    return next(f for f in rep.findings if f.kind == "host").evidence


def make(env: dict[str, Any], findings: list[Finding], *, now: datetime = NOW, days: int = 30, index: bool = True,
         commit: bool = True, renews: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    """Build, sign (test key), install and index one entry."""
    rec = known.build(findings, reason=over.pop("reason", "accepted: behind nginx deny"),
                      decision_ref={"plan_slug": PLAN, "step": STEP}, days=days, key_id="op",
                      governance_dir=str(env["gov"]), renews=renews, now=now)
    rec.update(over)
    signed = {**rec, "signature": ed25519_sign(env["pem"], known.signed_values(rec))}
    known.install(signed)
    if index:
        known.append_index_row(signed)
        if commit:
            _git(env["gov"], "commit", "-q", "-am", f"index {signed['entry_id']}")
    return signed


def applied(findings: list[Finding], *, now: datetime = NOW + timedelta(hours=1)) -> tuple[Report, list[dict]]:
    rep = Report(target="daily", findings=findings)
    rows = known_report.apply(rep, now=now)
    return rep, rows


def _void(rows: list[dict], text: str) -> dict:
    hits = [r for r in rows if r["status"] == "void" and text in r["void_reason"]]
    assert hits, rows
    return hits[0]


def _integrity(rep: Report) -> list[Finding]:
    return [f for f in rep.findings if f.kind == "integrity"]


# ---------------------------------------------------------------- in force: graded known, never hidden

def test_a_valid_entry_grades_known_and_stays_in_the_report(env: dict[str, Any]) -> None:
    rec = make(env, [pkg()])
    rep, rows = applied([pkg()])
    f = rep.findings[0]
    assert f.known == rec["entry_id"] and f.known_until == rec["expires"][:10]
    assert len(rep.findings) == 1 and not _integrity(rep)
    assert rep.exit_code("high") == EXIT_CLEAN
    assert rows[0]["status"] == "known" and rows[0]["covered"] == 1
    md = rep.markdown()
    assert "known until" in md and "xlsx" in md
    section = known_report.markdown(rows)
    for shown in (rec["entry_id"], "op", f"{PLAN}/{STEP}", "accepted: behind nginx deny", rec["expires"][:10]):
        assert shown in section
    assert Report.from_dict(rep.to_dict()).findings[0].known == rec["entry_id"]   # summary.json keeps it


def test_a_known_row_never_covers_another_target_or_package(env: dict[str, Any]) -> None:
    make(env, [pkg()])
    rep, _ = applied([pkg(target="other"), pkg(name="lodash")])
    assert not any(f.known for f in rep.findings)
    assert rep.exit_code("high") == EXIT_FINDINGS


def test_reason_is_fenced_and_capped_in_the_summary(env: dict[str, Any]) -> None:
    nasty = "| `x` {{ system }} \x1b[31m" + "A" * 400
    make(env, [pkg()], reason=nasty)
    _rep, rows = applied([pkg()])
    section = known_report.markdown(rows)
    line = next(ln for ln in section.splitlines() if ln.startswith("| `vk-"))
    assert "{{" not in line and "\x1b" not in line and "`x`" not in line
    assert "A" * known.SHOWN_REASON_CHARS not in line and "…" in line


# ---------------------------------------------------------------- void triggers: graded normally + HIGH integrity

@pytest.mark.parametrize("now_finding, why", [
    (pkg(version="0.18.6"), "installed version changed"),
    (pkg(ids=["GHSA-aaaa", "GHSA-bbbb"]), "new advisory id"),
    (pkg(fix="0.20.0"), "fixed release is available"),
    (pkg(severity=4), "severity rose"),
    (pkg(kev=True), "CISA KEV"),
    (pkg(ids=["MAL-2026-1"]), "malicious"),
    (pkg(malicious=True), "malicious"),
    (pkg(locations=["~/.config/agentteams/keys/x"]), "trust root"),
])
def test_each_finding_change_voids(env: dict[str, Any], now_finding: Finding, why: str) -> None:
    make(env, [pkg()])
    rep, rows = applied([now_finding])
    assert not now_finding.known
    _void(rows, why)
    notes = _integrity(rep)
    assert len(notes) == 1 and notes[0].severity == 3 and why.split()[0] in notes[0].detail
    assert now_finding in rep.findings


def test_recorded_fix_state_holds_while_unchanged(env: dict[str, Any]) -> None:
    """An entry signed while a fix existed records it; only a *new* fix voids."""
    make(env, [pkg(fix="0.19.3")])
    rep, _ = applied([pkg(fix="0.19.3")])
    assert rep.findings[0].known
    rep, rows = applied([pkg(fix="0.20.0")])
    _void(rows, "fixed release is available (0.20.0) that the entry did not record (0.19.3)")


def test_expiry_voids_and_says_so_when_the_finding_is_still_there(env: dict[str, Any]) -> None:
    rec = make(env, [pkg()], days=10)
    later = datetime.fromisoformat(rec["expires"]) + timedelta(seconds=1)
    rep, rows = applied([pkg()], now=later)
    assert not rep.findings[0].known
    _void(rows, "expired")
    assert len(_integrity(rep)) == 1
    rep, rows = applied([], now=later)   # expired and gone: listed, no HIGH note
    _void(rows, "expired")
    assert not _integrity(rep)


@pytest.mark.parametrize("decision,status", [("CLEARED", "HALT"), ("RETRACTED", "closed"), ("REVOKED", "closed")])
def test_decision_ref_halted_or_retracted_voids(env: dict[str, Any], decision: str, status: str) -> None:
    make(env, [pkg()])
    with (env["gov"] / known.DECISIONS_REL).open("a", encoding="utf-8") as fh:
        fh.write(f"2026-11-03,{PLAN},{STEP},{decision},{status},,,withdrawn,security\n")
    rep, rows = applied([pkg()])
    assert not rep.findings[0].known
    _void(rows, "decision ref")


def test_decision_ref_must_name_a_cleared_row(env: dict[str, Any]) -> None:
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": "nope", "step": "none"}, days=5, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    signed = {**rec, "signature": ed25519_sign(env["pem"], known.signed_values(rec))}
    known.install(signed)
    known.append_index_row(signed)
    _git(env["gov"], "commit", "-q", "-am", "x")
    _rep, rows = applied([pkg()])
    _void(rows, "names no cleared decision-log row")


def test_halt_on_the_action_voids_every_entry(env: dict[str, Any]) -> None:
    make(env, [pkg()])
    make(env, [pkg(name="lodash")])
    with (env["gov"] / known.DECISIONS_REL).open("a", encoding="utf-8") as fh:
        fh.write(f"2026-11-03,vulnscan,s1,{known.ACTION_ID},HALT,,,stop,security\n")
    rep, rows = applied([pkg(), pkg(name="lodash")])
    assert not any(f.known for f in rep.findings)
    assert [r["status"] for r in rows] == ["void", "void"] and all("HALT" in r["void_reason"] for r in rows)
    assert rep.exit_code("high") == EXIT_FINDINGS


def test_missing_uncommitted_or_mismatched_index_row_voids(env: dict[str, Any]) -> None:
    make(env, [pkg()], index=False)
    _rep, rows = applied([pkg()])
    _void(rows, "no row for")
    known.remove(rows[0]["entry_id"])
    make(env, [pkg(name="b")], commit=False)   # appended to the working tree, never committed
    _rep, rows = applied([pkg(name="b")])
    _void(rows, "no row for")


def test_index_row_for_another_body_voids(env: dict[str, Any]) -> None:
    rec = make(env, [pkg()], index=False)
    index = env["gov"] / known.INDEX_REL
    row = known.index_row(rec)
    row["body_sha256"] = "0" * 64
    index.write_text(index.read_text() + ",".join(row[c] for c in known.INDEX_HEADER) + "\n")
    _git(env["gov"], "commit", "-q", "-am", "bad row")
    _rep, rows = applied([pkg()])
    _void(rows, "does not match")


# ---------------------------------------------------------------- signature: tamper, purpose, symmetric

def test_tampered_entry_voids(env: dict[str, Any]) -> None:
    rec = make(env, [pkg()])
    path = known.store_dir() / f"{rec['entry_id']}.json"
    body = json.loads(path.read_text())
    body["members"][0]["version"] = "0.19.0"
    path.write_text(json.dumps(body))
    rep, rows = applied([pkg(version="0.19.0")])
    assert not rep.findings[0].known
    _void(rows, "signature does not verify")
    assert _integrity(rep)


def test_wrong_purpose_signature_voids(env: dict[str, Any]) -> None:
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    as_grant = [grant.PURPOSE_TAG] + known.signed_values(rec)[1:]
    signed = {**rec, "signature": ed25519_sign(env["pem"], as_grant)}
    with pytest.raises(known.KnownError, match="signature does not verify"):
        known.verify(signed, now=NOW)


def test_symmetric_signature_is_refused(env: dict[str, Any]) -> None:
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    signed = {**rec, "signature": hmac_sign(secrets.token_hex(32), known.signed_values(rec))}
    with pytest.raises(known.KnownError, match="symmetric/HMAC"):
        known.verify(signed, now=NOW)


def test_lifetime_over_90_days_is_refused(env: dict[str, Any]) -> None:
    with pytest.raises(known.KnownError, match="days must be"):
        known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=91, key_id="op",
                    governance_dir=str(env["gov"]), now=NOW)
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=90, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    rec["expires"] = (NOW + timedelta(days=91)).isoformat()
    signed = {**rec, "signature": ed25519_sign(env["pem"], known.signed_values(rec))}
    with pytest.raises(known.KnownError, match="at most 90 days"):
        known.verify(signed, now=NOW)


def test_effect_class_is_derived_and_a_self_declared_field_is_refused(env: dict[str, Any]) -> None:
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    assert known.derived_class(rec) == "relaxing"
    for extra in ("effect_class", "prefix"):
        with pytest.raises(known.KnownError, match="unexpected or missing fields"):
            known.check_unsigned({**rec, extra: "non-relaxing"})


# ---------------------------------------------------------------- refusals (even when signed)

@pytest.mark.parametrize("target", [
    "~/.config/agentteams/keys", "references/authorized-verify-keys", "references/security-approvers.txt",
    "~/.config/agentteams-secrets", "references/enforcement-integrity.json", "CLAUDE.md",
    "~/.config/agentteams/vulnscan-grants", "state/ledger.jsonl", "references/vulnscan-known-index.csv",
    "references/security-decisions.log.csv", "site-packages/agentteams/vulnscan",
])
def test_trust_root_targets_are_refused_even_signed(env: dict[str, Any], target: str) -> None:
    with pytest.raises(known.KnownError, match="trust root"):
        known.build([pkg(target=target)], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5,
                    key_id="op", governance_dir=str(env["gov"]), now=NOW)
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    rec["target"] = target
    signed = {**rec, "signature": ed25519_sign(env["pem"], known.signed_values(rec))}
    with pytest.raises(known.KnownError, match="trust root"):
        known.verify(signed, now=NOW)


@pytest.mark.parametrize("finding, why", [
    (pkg(ids=["MAL-2026-9"]), "MAL-"),
    (pkg(malicious=True), "malicious"),
    (pkg(name="agentteams", ecosystem="PyPI"), "install itself"),
    (Finding(kind="ioc", target="web1", name="compromise indicator", severity=4, detail="x"), "ioc"),
    (Finding(kind="range", target="webapp", name="x", ecosystem="PyPI", spec=">=1"), "range"),
    (Finding(kind="integrity", target="known-entries", name="known-entry", severity=3, detail="x"), "integrity"),
    (Finding(kind="host", target="web1", name="host", severity=3, detail="coverage gap: unattended upgrades off"),
     "only the web-root"),
    (host(""), "keys on its evidence"),
])
def test_never_known_findings_are_refused(env: dict[str, Any], finding: Finding, why: str) -> None:
    with pytest.raises(known.KnownError, match=why):
        known.build([finding], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5, key_id="op",
                    governance_dir=str(env["gov"]), now=NOW)


# ---------------------------------------------------------------- groups: the full member list, never a prefix

def test_group_signs_every_member_and_a_new_package_under_the_same_path_is_graded(env: dict[str, Any]) -> None:
    a = pkg(name="libssl3", ecosystem="Ubuntu", distro=True, locations=["/usr/lib"])
    b = pkg(name="libc6", ecosystem="Ubuntu", distro=True, locations=["/usr/lib"])
    rec = make(env, [a, b])
    assert rec["kind"] == "group" and [m["name"] for m in rec["members"]] == ["libc6", "libssl3"]
    newcomer = pkg(name="libxml2", ecosystem="Ubuntu", distro=True, locations=["/usr/lib"])
    rep, rows = applied([a, b, newcomer])
    assert a.known and b.known and not newcomer.known
    assert rows[0]["covered"] == 2 and rows[0]["members"] == 2


def test_one_changed_member_voids_the_whole_group(env: dict[str, Any]) -> None:
    a, b = pkg(name="libssl3", ecosystem="Ubuntu"), pkg(name="libc6", ecosystem="Ubuntu")
    make(env, [a, b])
    rep, rows = applied([pkg(name="libssl3", ecosystem="Ubuntu"), pkg(name="libc6", ecosystem="Ubuntu", version="2")])
    assert not any(f.known for f in rep.findings)
    _void(rows, "installed version changed")


def test_group_size_is_bounded(env: dict[str, Any]) -> None:
    many = [pkg(name=f"p{i}") for i in range(known.MAX_GROUP_MEMBERS + 1)]
    with pytest.raises(known.KnownError, match="2..40 members"):
        known.build(many, reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5, key_id="op",
                    governance_dir=str(env["gov"]), now=NOW)


# ---------------------------------------------------------------- email_api: file content sha256 + nginx deny rule

def test_host_evidence_from_the_audit() -> None:
    ev = _evidence()
    assert ev.startswith("sha256:") and ev.endswith(";files=2;deny=1")
    assert _evidence() == ev
    assert _evidence({**EMAIL_FILES, "/var/www/email_api/token.json": b"changed"}) != ev
    assert _evidence(rules=["location ~ ^/email_api/ { => allow all;", *DENY]) != ev
    assert _evidence(rules=[]) == "" and _evidence(files={}) == ""
    forged = _audit(EMAIL_FILES, []) + f"HOSTEVIDENCE\tdeny\t{'0' * 64}\tdeny all;\n"   # hash is not of the text
    assert next(f for f in engine.scan_inventory(forged, "h").findings if f.kind == "host").evidence == ""


def test_email_api_entry_voids_when_a_file_or_the_deny_rule_changes(env: dict[str, Any]) -> None:
    make(env, [host(_evidence())], days=90)
    rep, rows = applied([host(_evidence())])
    assert rep.findings[0].known and rows[0]["status"] == "known"
    for changed in (_evidence({**EMAIL_FILES, "/var/www/email_api/new.env": b"X=1"}),
                    _evidence(rules=["location /other { => deny all;"])):
        rep, rows = applied([host(changed)])
        assert not rep.findings[0].known
        _void(rows, "evidence changed")


def test_audit_script_emits_host_evidence() -> None:
    script = (Path(known.__file__).parents[1] / "data" / "server_kit" / "L1-audit.sh").read_text()
    assert 'docroot_evidence "$hits"' in script and "HOSTEVIDENCE\\tfile" in script and "HOSTEVIDENCE\\tdeny" in script


# ---------------------------------------------------------------- renewal and the cap

def test_renewal_has_a_new_id_lineage_and_count_and_inherits_an_ancestor_halt(env: dict[str, Any]) -> None:
    old = make(env, [pkg()])
    new = make(env, [pkg()], renews=old)
    assert new["entry_id"] != old["entry_id"] and new["lineage"] == [old["entry_id"]] and new["renewal_count"] == 1
    rep, rows = applied([pkg()])
    assert rep.findings[0].known == new["entry_id"]
    assert {r["entry_id"]: r["status"] for r in rows} == {old["entry_id"]: "renewed", new["entry_id"]: "known"}
    assert "1 (renews " in known_report.markdown(rows)
    with (env["gov"] / known.DECISIONS_REL).open("a", encoding="utf-8") as fh:
        fh.write(f"2026-11-03,vulnscan,s1,{old['entry_id']},HALT,,,bad call,security\n")
    rep, rows = applied([pkg()])
    assert not rep.findings[0].known
    _void(rows, "an ancestor")


def test_cap_refuses_new_entries_only(env: dict[str, Any]) -> None:
    for i in range(known.MAX_ACTIVE + 1):
        make(env, [pkg(name=f"p{i:02d}")], now=NOW + timedelta(minutes=i), commit=False)
    _git(env["gov"], "commit", "-q", "-am", "index")
    findings = [pkg(name=f"p{i:02d}") for i in range(known.MAX_ACTIVE + 1)]
    rep, rows = applied(findings, now=NOW + timedelta(hours=2))
    assert sum(1 for f in rep.findings if f.known) == known.MAX_ACTIVE
    assert findings[-1].known == "" and findings[0].known   # the newest is refused, the oldest stays
    _void(rows, "over the cap")
    assert known_report.active_count(now=NOW + timedelta(hours=2)) == known.MAX_ACTIVE


# ---------------------------------------------------------------- K3: propose never signs; the operator signs

def _summary_run(env: dict[str, Any], findings: list[Finding], day: str = "2026-11-02") -> None:
    run = env["tmp"] / "state" / day
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps(Report(target="daily", findings=findings).to_dict()))


def _args(**over: Any) -> argparse.Namespace:
    base = {"action": "propose", "proposal": None, "run": None, "finding": [], "reason": "operator declined removal",
            "from_note": None, "decision_plan": PLAN, "decision_step": STEP, "days": 90, "key_id": "op",
            "governance_dir": "", "renews": "", "out": None, "confirm_review_sha256": None,
            "allow_checkout_signing": False}
    base.update(over)
    return argparse.Namespace(**base)


def test_propose_writes_an_unsigned_proposal_and_never_touches_key_material(
        env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agentteams.cli import operator_signing, signed_ledger

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("propose must never sign or read key material")

    for mod, name in ((operator_signing, "_read_operator_private_key"), (signed_ledger, "ed25519_sign"),
                      (grant, "operator_sign"), (known, "sign")):
        monkeypatch.setattr(mod, name, boom)
    _summary_run(env, [pkg()])
    out = env["tmp"] / "proposal.json"
    rc = known_cli.dispatch(_args(finding=[pkg().key()], governance_dir=str(env["gov"]), out=out))
    assert rc == 0
    rec = json.loads(out.read_text())
    assert "signature" not in rec and rec["members"][0]["name"] == "xlsx"
    assert f"known sign {out}" in capsys.readouterr().out
    assert not known.store_dir().exists()   # nothing installed, nothing in force


def test_propose_refuses_an_unknown_key_and_at_the_cap(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _summary_run(env, [pkg()])
    assert known_cli.dispatch(_args(finding=["nope"], governance_dir=str(env["gov"]))) == 2
    monkeypatch.setattr(known_report, "active_count", lambda **_k: known.MAX_ACTIVE)
    assert known_cli.dispatch(_args(finding=[pkg().key()], governance_dir=str(env["gov"]))) == 2


def test_operator_sign_installs_and_appends_the_index_row(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """``known sign`` goes through grant.operator_sign (replaced here by the test key, as the operator would sign)."""
    _summary_run(env, [pkg()])
    out = env["tmp"] / "proposal.json"
    assert known_cli.dispatch(_args(finding=[pkg().key()], governance_dir=str(env["gov"]), out=out)) == 0
    seen: dict[str, Any] = {}

    def operator(record: dict[str, Any], **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {**record, "signature": ed25519_sign(env["pem"], kw["values"](record))}

    monkeypatch.setattr(grant, "operator_sign", operator)
    assert known_cli.dispatch(_args(action="sign", proposal=str(out))) == 0
    assert seen["digest_kind"] == "vulnscan-known-entry" and seen["values"] is known.signed_values
    rec = json.loads(out.read_text())
    index = (env["gov"] / known.INDEX_REL).read_text()
    assert rec["entry_id"] in index
    rep, rows = applied([pkg()], now=datetime.now(UTC))
    _void(rows, "no row for")   # in force only once the index row is committed
    _git(env["gov"], "commit", "-q", "-am", "index")
    rep, rows = applied([pkg()], now=datetime.now(UTC))
    assert rep.findings[0].known == rec["entry_id"]


def test_propose_from_a_note_migrates_its_text(env: dict[str, Any]) -> None:
    from agentteams.vulnscan import notes

    note = notes.add("zstd 0.13.3 stays: no fixed crate for the fallback", target="baseAgent", days=90)
    _summary_run(env, [pkg()])
    out = env["tmp"] / "p.json"
    assert known_cli.dispatch(_args(finding=[pkg().key()], governance_dir=str(env["gov"]), out=out,
                                    from_note=note["id"], reason="")) == 0
    assert json.loads(out.read_text())["reason"].startswith(f"(from operator note {note['id']}) zstd 0.13.3")
