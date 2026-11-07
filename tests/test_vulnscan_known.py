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


EMAIL_FILES = {"token.json": b"{\"t\": 1}", "client_secret_x.json": b"{}"}
DENY = ["deny all;"]
KIT_L1 = Path(known.__file__).parents[1] / "data" / "server_kit" / "L1-audit.sh"
BASH = "/bin/bash" if Path("/bin/bash").is_file() else "bash"


class Audit:
    """Runs L1-audit.sh's real evidence functions (key, HMAC, docroot_evidence) on files in ``tmp_path``.

    ``AGENTTEAMS_L1_ROOT`` points the key at ``<tmp>/root/var/lib/agentteams-server-kit``; a fake ``nginx -T``
    prints one ``location`` block holding the given deny rules.
    """

    def __init__(self, tmp: Path) -> None:
        self.tmp, self.root, self.www, self.bin = tmp, tmp / "root", tmp / "www", tmp / "bin"
        for d in (self.root, self.www, self.bin):
            d.mkdir(parents=True, exist_ok=True)
        text = KIT_L1.read_text()
        self.funcs = text[text.index("L1_KEY_DIR="): text.index("docroot_section(){")]
        # the one `nginx -T` dump docroot_evidence reads (main() runs nginx_dump first), with the kit's `tmo`
        tmo = next(ln for ln in text.splitlines() if ln.startswith("tmo(){"))
        dump = text[text.index('NGINX_DUMP=""; NGINX_RC=1'): text.index("main(){")]
        self.funcs += "\n" + tmo + "\n" + dump + "nginx_dump\n"
        self.key_file = self.root / "var/lib/agentteams-server-kit/l1-evidence.key"

    def run(self, files: dict[str, bytes] = EMAIL_FILES, rules: list[str] | None = DENY, *,
            xtrace: bool = False) -> subprocess.CompletedProcess:
        for old in self.www.iterdir():
            old.unlink()
        for name, body in files.items():
            (self.www / name).write_bytes(body)
        nginx = self.bin / "nginx"
        nginx.unlink(missing_ok=True)
        if rules is not None:
            conf = "server {\n  location ~ ^/email_api/ {\n" + "".join(f"    {r}\n" for r in rules) + "  }\n}\n"
            nginx.write_text(f"#!/bin/sh\ncat <<'EOF'\n{conf}EOF\n")
            nginx.chmod(0o755)
        hits = "\n".join(str(self.www / n) for n in sorted(files))
        probe = f"set -uo pipefail{' -x' if xtrace else ''}\ninfo(){{ printf 'INFO        %s\\n' \"$*\"; }}\n{self.funcs}\ndocroot_evidence \"$1\"\n"
        env = {"PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin", "AGENTTEAMS_L1_ROOT": str(self.root)}
        return subprocess.run([BASH, "-c", probe, "x", hits], capture_output=True, text=True, env=env, check=False)

    def evidence(self, files: dict[str, bytes] = EMAIL_FILES, rules: list[str] | None = DENY) -> str:
        out = self.run(files, rules).stdout
        audit = f"HOSTCHECK\tHIGH\t{DOCROOT}\nACTION      {DOCROOT}\n" + out
        return next(f for f in engine.scan_inventory(audit, "jlc-prod").findings if f.kind == "host").evidence


@pytest.fixture
def audit(tmp_path: Path) -> Audit:
    return Audit(tmp_path / "audit")


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


# ---------------------------------------------------------------- email_api: file HMACs + nginx deny rule (@security R1)

def test_audit_prints_one_hmac_digest_and_never_a_per_file_value(audit: Audit) -> None:
    import hmac

    proc = audit.run()
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("HOSTEVIDENCE")]
    assert proc.returncode == 0 and len(lines) == 1, (proc.stdout, proc.stderr)
    _tag, digest, files, deny = lines[0].split("\t")
    assert (files, deny) == ("2", "1")
    key = audit.key_file.read_text()
    assert len(key) == 64 and (audit.key_file.stat().st_mode & 0o777) == 0o600
    assert (audit.key_file.parent.stat().st_mode & 0o777) == 0o700
    want = []
    for name in sorted(EMAIL_FILES):
        h = hmac.new(bytes.fromhex(key), EMAIL_FILES[name], hashlib.sha256).hexdigest()   # RFC 2104, as the shell
        want.append(f"file {h} {audit.www / name}\n")
        assert h not in proc.stdout and hashlib.sha256(EMAIL_FILES[name]).hexdigest() not in proc.stdout
    want.append("deny location ~ ^/email_api/ { => deny all;\n")
    assert digest == hashlib.sha256("".join(sorted(want)).encode()).hexdigest()
    assert key not in proc.stdout and key not in proc.stderr


def test_host_evidence_voids_on_a_file_a_deny_rule_or_a_rotated_key(audit: Audit) -> None:
    ev = audit.evidence()
    assert ev.startswith("sha256:") and ev.endswith(";files=2;deny=1") and audit.evidence() == ev
    assert audit.evidence({**EMAIL_FILES, "token.json": b"changed"}) != ev
    assert audit.evidence(rules=["deny 10.0.0.0/8;", *DENY]) != ev and audit.evidence(rules=["deny 10.0.0.1;"]) != ev
    assert audit.evidence(rules=None) == "" and audit.evidence(rules=[]) == ""   # no deny rule: nothing to key on
    audit.key_file.unlink()   # rotating the key changes every digest (documented: it voids host entries)
    assert audit.evidence() not in ("", ev)


@pytest.mark.parametrize("spoil", ["mode", "symlink", "garbage", "fifo", "dir-mode", "dir-symlink"])
def test_an_unsafe_evidence_key_gives_no_evidence(audit: Audit, spoil: str) -> None:
    import os

    audit.run()
    key = audit.key_file
    if spoil == "mode":
        key.chmod(0o644)
    elif spoil == "fifo":   # noclobber would still write into an existing FIFO
        key.unlink()
        os.mkfifo(key, 0o600)
    elif spoil == "dir-mode":
        key.parent.chmod(0o755)
    elif spoil == "dir-symlink":
        real = audit.tmp / "elsewhere-dir"
        key.parent.rename(real)
        key.parent.symlink_to(real)
    elif spoil == "symlink":
        real = audit.tmp / "elsewhere.key"
        real.write_text(key.read_text())
        real.chmod(0o600)
        key.unlink()
        key.symlink_to(real)
    else:
        key.write_text("not hex")
    proc = audit.run()
    assert not any(ln.startswith("HOSTEVIDENCE") for ln in proc.stdout.splitlines())
    assert "no usable evidence key" in proc.stdout


def test_xtrace_does_not_reveal_the_key(audit: Audit) -> None:
    """A `bash -x` run (streamed back by remote.sh) must not trace the key or anything derived from it."""
    import hmac

    proc = audit.run(xtrace=True)
    assert "+ " in proc.stderr   # tracing was on around the evidence functions
    assert any(ln.startswith("HOSTEVIDENCE") for ln in proc.stdout.splitlines())
    key = audit.key_file.read_text()
    secrets_ = [key] + [hmac.new(bytes.fromhex(key), b, hashlib.sha256).hexdigest() for b in EMAIL_FILES.values()]
    secrets_.append("\\x" + format(int(key[:2], 16) ^ 0x36, "02x"))   # the first byte of the inner pad
    for value in secrets_:
        assert value not in proc.stderr and value not in proc.stdout


def test_host_evidence_line_parsing() -> None:
    d = "a" * 64
    assert engine.host_evidence(d, "2", "1") == f"sha256:{d};files=2;deny=1"
    for bad in (("A" * 64, "2", "1"), (d, "0", "1"), (d, "2", "0"), (d[:-1], "2", "1"), (d, "x", "1")):
        assert engine.host_evidence(*bad) == ""


@pytest.mark.parametrize("between, attached", [
    ("ACTION      secret-looking file\nINFO        note", True),   # human lines (no tab) keep the link
    ("SCOPE\t/x\tdisabled\ttree\tev", False),                        # any other record breaks it,
    ("NEWTAG\tsomething", False),                                     # including one added later
    ("HOSTCHECK\tLOW\tanother check", False),                         # and the evidence is the newer check's
])
def test_host_evidence_attaches_only_to_the_check_before_it(between: str, attached: bool) -> None:
    d = "b" * 64
    text = f"HOSTCHECK\tHIGH\tsecret-looking file\n{between}\nHOSTEVIDENCE\t{d}\t2\t1\n"
    first = next(f for f in engine.scan_inventory(text, "jlc-prod").findings if f.kind == "host")
    assert (first.evidence == f"sha256:{d};files=2;deny=1") is attached


def test_email_api_entry_voids_when_a_file_or_the_deny_rule_changes(env: dict[str, Any], audit: Audit) -> None:
    make(env, [host(audit.evidence())], days=90)
    rep, rows = applied([host(audit.evidence())])
    assert rep.findings[0].known and rows[0]["status"] == "known"
    for changed in (audit.evidence({**EMAIL_FILES, "new.env": b"X=1"}), audit.evidence(rules=["deny 10.0.0.1;"])):
        rep, rows = applied([host(changed)])
        assert not rep.findings[0].known
        _void(rows, "evidence changed")


def test_audit_script_emits_host_evidence() -> None:
    script = KIT_L1.read_text()
    assert 'docroot_evidence "$hits"' in script and "HOSTEVIDENCE\\t%s\\t%s\\t%s" in script
    assert "sha256sum -- " not in script   # no plain hash of a secret-looking file


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
    zstd = pkg(target="baseAgent", ecosystem="crates.io", name="zstd", version="0.13.3", ids=["RUSTSEC-x"])
    _summary_run(env, [zstd, pkg()])
    out = env["tmp"] / "p.json"
    assert known_cli.dispatch(_args(finding=[zstd.key()], governance_dir=str(env["gov"]), out=out,
                                    from_note=note["id"], reason="")) == 0
    assert json.loads(out.read_text())["reason"].startswith(f"(from operator note {note['id']}) zstd 0.13.3")


def test_propose_from_a_note_refuses_another_target(env: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    """A note's target must match the findings' target (@code-hygiene 1)."""
    from agentteams.vulnscan import notes

    note = notes.add("zstd stays", target="baseAgent", days=90)
    untargeted = notes.add("applies everywhere", days=90)
    _summary_run(env, [pkg()])
    for n in (note, untargeted):
        out = env["tmp"] / f"{n['id']}.json"
        assert known_cli.dispatch(_args(finding=[pkg().key()], governance_dir=str(env["gov"]), out=out,
                                        from_note=n["id"], reason="")) == 2
        assert not out.exists()
    assert "not the findings' target 'webapp'" in capsys.readouterr().err


# ---------------------------------------------------------------- review round 1 (@security R2, R3; recommended)

def test_a_critical_member_is_refused_until_kev_is_read(env: dict[str, Any]) -> None:
    """R2: vulnscan reads no KEV feed, so a CRITICAL finding cannot be accepted (whether it is exploited is unknown)."""
    with pytest.raises(known.KnownError, match="CRITICAL finding cannot be known"):
        known.build([pkg(severity=4)], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5,
                    key_id="op", governance_dir=str(env["gov"]), now=NOW)
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    rec["members"][0]["severity"] = 4
    signed = {**rec, "signature": ed25519_sign(env["pem"], known.signed_values(rec))}
    with pytest.raises(known.KnownError, match="CRITICAL"):
        known.verify(signed, now=NOW)


@pytest.mark.parametrize("decision, verified, ok", [
    ("CLEARED-WITH-CONDITIONS", "pending", False), ("CONDITIONAL PASS", "", False),
    ("CLEARED-WITH-CONDITIONS", "verified", True), ("CONDITIONAL PASS", "Verified", True),
])
def test_a_conditional_decision_counts_only_once_verified(env: dict[str, Any], decision: str, verified: str,
                                                         ok: bool) -> None:
    """R3: CLEARED-WITH-CONDITIONS / CONDITIONAL count only with conditions_verified=verified; pending blocks."""
    step = "K4 conditional"
    with (env["gov"] / known.DECISIONS_REL).open("a", encoding="utf-8") as fh:
        fh.write(f"2026-11-02,{PLAN},{step},{decision},open,c1,{verified},review,security\n")
    rec = known.build([pkg()], reason="r", decision_ref={"plan_slug": PLAN, "step": step}, days=5, key_id="op",
                      governance_dir=str(env["gov"]), now=NOW)
    signed = {**rec, "signature": ed25519_sign(env["pem"], known.signed_values(rec))}
    known.install(signed)
    known.append_index_row(signed)
    _git(env["gov"], "commit", "-q", "-am", "x")
    rep, rows = applied([pkg()])
    if ok:
        assert rep.findings[0].known and rows[0]["status"] == "known"
    else:
        assert not rep.findings[0].known
        _void(rows, "conditions are not verified")


def test_a_symlinked_target_is_judged_by_where_it_points(env: dict[str, Any]) -> None:
    keys = env["tmp"] / "x" / ".config" / "agentteams" / "keys"
    keys.mkdir(parents=True)
    innocent = env["tmp"] / "webapp-link"
    innocent.symlink_to(keys)
    with pytest.raises(known.KnownError, match="trust root"):
        known.build([pkg(target=str(innocent))], reason="r", decision_ref={"plan_slug": PLAN, "step": STEP}, days=5,
                    key_id="op", governance_dir=str(env["gov"]), now=NOW)
    make(env, [pkg()])
    _rep, rows = applied([pkg(locations=[str(innocent)])])   # a location is resolved too
    _void(rows, "trust root")


def test_a_fifo_in_the_store_does_not_hang_load(env: dict[str, Any]) -> None:
    import os

    make(env, [pkg()])
    os.mkfifo(known.store_dir() / "vk-0000000000000000.json", 0o600)
    loaded = known_report.load(now=NOW + timedelta(hours=1))   # would block forever without O_NONBLOCK
    assert any(name == "vk-0000000000000000.json" and "regular file" in why for _r, name, why in loaded.void)
    assert len(loaded.valid) == 1
