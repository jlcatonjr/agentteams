"""``server-apply`` through the executor (vfr-s S1/S2, @security Phase 2 design conditions), against a fake kit.

No host is contacted: ``server_apply.run_kit`` and ``server_apply.audit`` are replaced by fakes that answer like
``run-host.sh`` / the L1 audit. Real Ed25519 grants, the real ledger, the real decision-log gate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import escalations, executor, executor_serve, grant, grant_v2, ledger, server_apply  # noqa: E402
from tests._vulnscan_helpers import operator_home, sign_grant  # noqa: E402

ANACONDA = "/root/anaconda3.disabled-20261101"
APPS = "jlc-prod | api.service | /srv/api | /srv/api/.venv/bin/python | | - | idna==3.10\n"
NOON = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


class _Clock(datetime):
    now_value = NOON

    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        return cls.now_value


class FakeKit:
    """Answers run_kit/audit calls; records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.plan = "PLAN\tos\tapt\tlibssl3\t3.0.2-1\t3.0.2-2\nPRESTATE\tdpkg\tlibssl3 3.0.2-1\n"
        self.apply_rc = 0
        self.apply_out = "applied\n"
        self.dry_rc = 0
        self.printed_digest: str | None = None
        self.audits: list[tuple[str, int, int]] = []

    def digest(self) -> str:
        return server_apply.plan_sha256(self.plan)

    def run_kit(self, script: str, host: str, args: list[str], conf: Path) -> subprocess.CompletedProcess:
        self.calls.append((script, list(args)))
        level = args[args.index("--level") + 1]
        if "--apply" not in args:
            out = self.plan + f"PLANDIGEST\t{level}\t{self.printed_digest or self.digest()}\n"
            return subprocess.CompletedProcess(args, self.dry_rc, out, "")
        return subprocess.CompletedProcess(args, self.apply_rc, self.apply_out, "")

    def audit(self, host: str, conf: Path) -> tuple[str, int, int]:
        self.calls.append(("audit", [host]))
        return self.audits.pop(0) if self.audits else ("RESULT clean\n", 0, 0)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    world = operator_home(tmp_path, monkeypatch)
    monkeypatch.delenv("AGENTTEAMS_DECISION_SIGNING_KEY", raising=False)
    kitconf = tmp_path / "kit"
    kitconf.mkdir()
    (kitconf / "hosts.conf").write_text("jlc-prod | root@jlc.example.com\nvk-api | admin@api.example.com\n")
    (kitconf / "apps.conf").write_text(APPS)
    fake = FakeKit()
    monkeypatch.setattr(server_apply, "run_kit", fake.run_kit)
    monkeypatch.setattr(server_apply, "audit", fake.audit)
    monkeypatch.setattr(server_apply, "datetime", _Clock)
    _Clock.now_value = NOON
    return {**world, "cfg": {"governance_dir": str(world["gov"])}, "kit": fake}


def install_v2(env: dict[str, Any], **kw: Any) -> dict[str, Any]:
    old = sign_grant(env["pem"], grant.build(ops=["server-audit"], repo_roots=[], hosts=["jlc-prod", "vk-api"],
                                            days=30, max_uses=50, key_id="op", governance_dir=str(env["gov"])))
    grant.install(old)
    grant.revoke(old["grant_id"])
    args: dict[str, Any] = dict(
        ops=["server-audit", "server-dryrun", "server-apply"], repo_roots=[], hosts=["jlc-prod", "vk-api"], days=30,
        max_uses=20, key_id="op", governance_dir=str(env["gov"]), ancestor=old["grant_id"],
        server_levels=[f"jlc-prod:{lv}" for lv in grant_v2.LEVELS] + ["vk-api:l2-security"],
        remove_paths=[f"jlc-prod:{ANACONDA}"], restart_units=["jlc-prod:nginx.service"],
        restart_window="10:00-14:00",
        apps_conf_sha256=hashlib.sha256(APPS.encode()).hexdigest(), major_allowed=["jlc-prod:api.service"])
    args.update(kw)
    g = sign_grant(env["pem"], grant.build(**args))
    grant.install(g)
    return g


def act(env: dict[str, Any], target: str, digest: str | None = None, op: str = "server-apply") -> dict[str, Any]:
    payload = {"confirm_sha256": digest if digest is not None else env["kit"].digest()}
    return executor.act(op, target, env["cfg"], payload=payload)


def kinds() -> list[str]:
    return [r["kind"] for r in ledger.read()]


def clearance(env: dict[str, Any], action: str, *, status: str = "CONDITIONAL PASS", verified: str = "verified") -> None:
    log = env["gov"] / "references" / "security-decisions.log.csv"
    with log.open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["2026-11-02", "vfr-s", "E1b", action, status, "quarantine only", verified,
                                 "@security review", "security"])


# ---------------------------------------------------------------- pre-checks: refused before any use is reserved

@pytest.mark.parametrize("target,match", [
    ("jlc-prod", "HOST:LEVEL"), ("jlc-prod:l5-containers", "unknown level"), ("all:l2-security", "HOST:LEVEL"),
    ("jlc-prod:remove-path", "exactly one path"), ("jlc-prod:l2-security:/x", "exactly one path"),
    ("jlc-prod:remove-path:/etc", "top-level"), ("web9:l2-security", "not a hosts.conf entry")])
def test_precheck_refuses_malformed_targets(env: dict[str, Any], target: str, match: str) -> None:
    install_v2(env)
    res = act(env, target)
    assert res["status"] == "refused" and match in res["detail"], res
    assert "begun" not in kinds() and env["kit"].calls == []


def test_no_confirmed_digest_no_run(env: dict[str, Any]) -> None:
    install_v2(env)
    res = executor.act("server-apply", "jlc-prod:l2-security", env["cfg"])
    assert res["status"] == "refused" and "confirmed" in res["detail"] and "begun" not in kinds()


def test_spooled_agent_request_for_a_host_change_is_refused() -> None:
    for op in ("server-apply", "runtime-patch"):
        with pytest.raises(executor.ExecError, match="operator-run only"):
            executor_serve._check_payload({"op": op, "target": "jlc-prod:l2-security"})


def test_v1_grant_never_covers_server_apply(env: dict[str, Any]) -> None:
    grant.install(sign_grant(env["pem"], grant.build(ops=["server-audit"], repo_roots=[], hosts=["jlc-prod"], days=5,
                                                     max_uses=5, key_id="op", governance_dir=str(env["gov"]))))
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "refused" and "not authorized" in res["detail"]


# ---------------------------------------------------------------- the happy path

def test_l2_security_runs_plan_then_exact_apply_then_post_audit(env: dict[str, Any]) -> None:
    g = install_v2(env)
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "done", res
    calls = env["kit"].calls
    d = env["kit"].digest()
    assert calls == [("audit", ["jlc-prod"]),
                     ("run-host.sh", ["--level", "l2-security", "--restart-unit", "nginx.service"]),
                     ("run-host.sh", ["--level", "l2-security", "--restart-unit", "nginx.service", "--apply",
                                      "--expect-digest", d]),
                     ("audit", ["jlc-prod"])], calls
    rows = ledger.read()
    assert [r["kind"] for r in rows] == ["begun", "note", "done"]
    assert rows[0]["grant_id"] == g["grant_id"] and rows[0]["target"] == "jlc-prod:l2-security"
    note = json.loads(rows[1]["detail"])
    pre = Path(note["prestate"])
    assert pre.read_text() == "PRESTATE\tdpkg\tlibssl3 3.0.2-1\n" and oct(pre.stat().st_mode & 0o777) == "0o600"
    assert note["prestate_sha256"] == hashlib.sha256(pre.read_bytes()).hexdigest() and note["plan_sha256"] == d
    for _, args in calls:
        assert not set(args) & set(server_apply.FORBIDDEN_FLAGS) and args.count("--level") <= 1


def test_restart_units_are_per_host(env: dict[str, Any]) -> None:
    install_v2(env)
    assert act(env, "vk-api:l2-security")["status"] == "done"
    assert env["kit"].calls[1] == ("run-host.sh", ["--level", "l2-security"])   # nginx is signed for jlc-prod only


def test_major_allowed_comes_from_the_grant(env: dict[str, Any]) -> None:
    install_v2(env)
    assert act(env, "jlc-prod:l4-pins")["status"] == "done"
    assert env["kit"].calls[1] == ("run-host.sh", ["--level", "l4-pins", "--major-allowed", "api.service"])
    env["kit"].calls.clear()
    for f in grant.grants_dir().glob("*.json"):
        grant.revoke(f.stem)
    install_v2(env, major_allowed=[])
    assert act(env, "jlc-prod:l4-pins")["status"] == "done"
    assert env["kit"].calls[1] == ("run-host.sh", ["--level", "l4-pins"])


# ---------------------------------------------------------------- digests

def test_plan_changed_since_confirmation_changes_nothing(env: dict[str, Any]) -> None:
    install_v2(env)
    res = act(env, "jlc-prod:l2-security", digest="f" * 64)
    assert res["status"] == "failed" and "changed since you confirmed" in res["detail"]
    assert [c for c in env["kit"].calls if "--apply" in c[1]] == [] and kinds() == ["begun", "failed"]


def test_kit_digest_must_match_the_executors_own(env: dict[str, Any]) -> None:
    install_v2(env)
    env["kit"].printed_digest = "0" * 64
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "failed" and "does not match the executor's own" in res["detail"]


def test_host_side_digest_mismatch_fails_without_escalation(env: dict[str, Any]) -> None:
    install_v2(env)
    env["kit"].apply_rc = 5
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "failed" and "changed nothing" in res["detail"]
    assert escalations.open_escalations() == []


# ---------------------------------------------------------------- escalations

@pytest.mark.parametrize("where", ["pre", "dry", "apply", "post"])
def test_exit_4_anywhere_escalates_and_refuses_everything(env: dict[str, Any], where: str) -> None:
    install_v2(env)
    kit = env["kit"]
    if where == "pre":
        kit.audits = [("RESULT COMPROMISE\n", 4, 1)]
    elif where == "dry":
        kit.dry_rc = 4
    elif where == "apply":
        kit.apply_rc = 4
    else:
        kit.audits = [("RESULT clean\n", 0, 0), ("RESULT COMPROMISE\n", 4, 1)]
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "failed" and "stops everything" in res["detail"], res
    (esc,) = escalations.open_escalations()
    assert esc.scope == escalations.ALL_SCOPE and esc.kind == "ioc"
    again = executor.act("server-audit", "vk-api", env["cfg"])
    assert again["status"] == "refused" and esc.id in again["detail"]


@pytest.mark.parametrize("rc,why", [(6, "pre-state was restored"), (7, "NOT restored"), (3, "a step failed")])
def test_failed_health_or_step_escalates_and_stops_the_host(env: dict[str, Any], rc: int, why: str) -> None:
    install_v2(env)
    env["kit"].apply_rc = rc
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "failed" and why in res["detail"] and "stops jlc-prod" in res["detail"], res
    (esc,) = escalations.open_escalations()
    assert esc.scope == "jlc-prod" and esc.kind == "other"
    env["kit"].calls.clear()
    blocked = act(env, "jlc-prod:l4-pins")
    assert blocked["status"] == "refused" and esc.id in blocked["detail"] and env["kit"].calls == []
    env["kit"].apply_rc = 0
    assert act(env, "vk-api:l2-security")["status"] == "done"          # another host is not stopped


def test_worse_post_audit_escalates(env: dict[str, Any]) -> None:
    install_v2(env)
    env["kit"].audits = [("RESULT action-needed\n", 1, 3), ("RESULT action-needed\n", 1, 4)]
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "failed" and "post-audit is worse" in res["detail"]
    assert escalations.open_escalations()[0].scope == "jlc-prod"


def test_better_post_audit_is_done(env: dict[str, Any]) -> None:
    install_v2(env)
    env["kit"].audits = [("RESULT action-needed\n", 1, 3), ("RESULT clean\n", 0, 0)]
    res = act(env, "jlc-prod:l2-security")
    assert res["status"] == "done" and res["post_audit"] == {"exit": 0, "findings": 0}


# ---------------------------------------------------------------- OD4: window, apps.conf

def test_restarting_levels_run_only_inside_the_signed_window(env: dict[str, Any]) -> None:
    install_v2(env)
    _Clock.now_value = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    for target in ("jlc-prod:l4-pins", "jlc-prod:py-venv", "jlc-prod:l2-security"):
        res = act(env, target)
        assert res["status"] == "failed" and "restart window" in res["detail"], (target, res)
    assert act(env, "vk-api:l2-security")["status"] == "done"   # no restart unit signed there: no window needed
    _Clock.now_value = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
    assert act(env, "jlc-prod:l4-pins")["status"] == "done"


def test_apps_conf_must_have_the_signed_sha256(env: dict[str, Any]) -> None:
    install_v2(env)
    (env["tmp"] / "kit" / "apps.conf").write_text(APPS + "jlc-prod | evil.service | /x | /x/bin/python | | - | a==1\n")
    res = act(env, "jlc-prod:l4-pins")
    assert res["status"] == "failed" and "apps.conf sha256" in res["detail"] and env["kit"].calls == []


def test_config_snapshot_refuses_a_symlinked_apps_conf(env: dict[str, Any]) -> None:
    install_v2(env)
    apps = env["tmp"] / "kit" / "apps.conf"
    real = env["tmp"] / "apps.real"
    real.write_text(APPS)
    apps.unlink()
    apps.symlink_to(real)
    res = act(env, "jlc-prod:l4-pins")
    assert res["status"] == "failed" and "symlink" in res["detail"]


# ---------------------------------------------------------------- remove-path: C-5 per path, inertness, quarantine

RP = f"jlc-prod:remove-path:{ANACONDA}"
SCOPE_OK = f"SCOPE\t{ANACONDA}\tdisabled\ttree\tinert since 2026-11-01\nRESULT action-needed\n"


def _scoped(env: dict[str, Any]) -> None:
    env["kit"].plan = f"PLAN\tremove-path\t{ANACONDA}\tquarantine\td\troot:root 755\t9 entries\t4 KiB\n"
    env["kit"].audits = [(SCOPE_OK, 1, 60), ("RESULT action-needed\n", 1, 5)]
    env["kit"].apply_out = f"QUARANTINED\t{ANACONDA}\t/var/backups/agentteams/quarantine/x-1\n"


def test_remove_path_needs_its_own_cleared_and_verified_c5_row(env: dict[str, Any]) -> None:
    install_v2(env)
    _scoped(env)
    action = server_apply.removal_action("jlc-prod", ANACONDA)
    res = act(env, RP)
    assert res["status"] == "failed" and "C-5" in res["detail"] and env["kit"].calls == []
    clearance(env, action, verified="pending")
    res = act(env, RP)
    assert res["status"] == "failed" and "C-5" in res["detail"]
    clearance(env, action + ":other-scope")            # decorated id: action_matches accepts it, exact check does not
    res = act(env, RP)
    assert res["status"] == "failed" and "not exactly" in res["detail"], res
    clearance(env, action)
    _scoped(env)
    res = act(env, RP)
    assert res["status"] == "done" and res["quarantine"].startswith("/var/backups/agentteams/quarantine/"), res
    assert env["kit"].calls[1] == ("run-host.sh", ["--level", "remove-path", "--remove-path", ANACONDA])
    _scoped(env)
    again = act(env, RP)
    assert again["status"] == "failed" and "already quarantined" in again["detail"]


def test_remove_path_halt_on_its_id_refuses(env: dict[str, Any]) -> None:
    install_v2(env)
    _scoped(env)
    action = server_apply.removal_action("jlc-prod", ANACONDA)
    clearance(env, action)
    clearance(env, action, status="HALT", verified="")
    res = act(env, RP)
    assert res["status"] == "failed" and "C-5" in res["detail"] and env["kit"].calls == []


def test_disabled_tree_needs_the_l1_scope_line(env: dict[str, Any]) -> None:
    install_v2(env)
    clearance(env, server_apply.removal_action("jlc-prod", ANACONDA))
    _scoped(env)
    env["kit"].audits = [(f"INFO disabled tree {ANACONDA}: not labelled, referenced by x\nRESULT clean\n", 0, 0)]
    res = act(env, RP)
    assert res["status"] == "failed" and "SCOPE" in res["detail"]
    assert [c for c in env["kit"].calls if c[0] == "run-host.sh"] == []
    other = f"SCOPE\t{ANACONDA}.old\tdisabled\ttree\tinert\nRESULT clean\n"   # another path's label does not count
    assert server_apply.disabled_scope_problem(other, ANACONDA)
    assert server_apply.disabled_scope_problem("RESULT clean\n", "/opt/old-app") == ""   # not a disabled-tree name


# ---------------------------------------------------------------- argv rules

def test_check_argv_refuses_everything_that_widens_a_level() -> None:
    ok = ["--level", "l2-security"]
    server_apply.check_argv(ok, apply=False)
    server_apply.check_argv([*ok, "--apply", "--expect-digest", "a" * 64], apply=True)
    for bad in ([], ["--level", "a", "--level", "b"], [*ok, "--reboot"], [*ok, "--all-updates"],
                [*ok, "--node-major", "24"], [*ok, "--allow-fresh"], [*ok, "--restart-services"], [*ok, "L5-containers.sh"]):
        with pytest.raises(executor.ExecError):
            server_apply.check_argv(bad, apply=False)
    with pytest.raises(executor.ExecError):
        server_apply.check_argv([*ok, "--apply"], apply=True)


def test_preview_changes_nothing_and_reserves_no_use(env: dict[str, Any]) -> None:
    install_v2(env)
    shown = server_apply.preview("jlc-prod:l2-security")
    assert shown["plan_sha256"] == env["kit"].digest() and shown["plan"][0].startswith("PLAN\tos")
    assert all("--apply" not in a for _, a in env["kit"].calls) and not ledger.read()
