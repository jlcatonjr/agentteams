"""``runtime-patch`` (vfr-d.D4) and the operator's preview-then-confirm ``act`` (:mod:`.host_change`). A fake brew
runner only: no Homebrew command ever runs."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cryptography")
from agentteams.vulnscan import engine, escalations, executor, grant, host_change, ledger, runtime_patch  # noqa: E402
from tests._vulnscan_helpers import operator_home, sign_grant  # noqa: E402

TAG = "arm64_sequoia"
REAL_LATEST = runtime_patch.latest_findings   # before the fixture replaces it
FLAGGED = {"kind": "runtime", "target": engine.LOCAL_TARGET, "name": "node", "version": "22.11.0",
           "detail": "behind-patch: 22.12.0 is out [brew]", "locations": ["/opt/homebrew/Cellar/node@22/22.11.0/bin/node"]}


class FakeBrew:
    def __init__(self) -> None:
        self.info: dict[str, Any] = {"name": "node@22", "tap": "homebrew/core", "installed": [{"version": "22.11.0"}],
                                     "linked_keg": "22.11.0", "pinned": False,
                                     "bottle": {"stable": {"files": {TAG: {}}}}}
        self.dry = "==> Would upgrade 1 outdated package:\nnode@22 22.11.0 -> 22.12.0\n"
        self.upgrade_rc = 0
        self.after = "22.12.0"
        self.poured = True
        self.deps = "openssl@3\nmpdecimal\n"
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        self.calls.append((argv, env))
        if argv[0] == "deps":
            return subprocess.CompletedProcess(argv, 0, self.deps, "")
        if argv[0] == "info":
            info = dict(self.info)
            if any(a[0] == ["upgrade", "--formula", "node@22"] for a in self.calls):
                info["linked_keg"] = self.after
                info["installed"] = [{"version": self.after, "poured_from_bottle": self.poured}]
            return subprocess.CompletedProcess(argv, 0, json.dumps({"formulae": [info]}), "")
        if "--dry-run" in argv:
            return subprocess.CompletedProcess(argv, 0, self.dry, "")
        return subprocess.CompletedProcess(argv, self.upgrade_rc, "", "")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    world = operator_home(tmp_path, monkeypatch)
    brew = FakeBrew()
    monkeypatch.setattr(runtime_patch, "_default_run", brew)
    monkeypatch.setattr(runtime_patch, "bottle_tag", lambda: TAG)
    monkeypatch.setattr(runtime_patch, "own_tool_formulae", lambda: {"python@3.14"})
    monkeypatch.setattr(runtime_patch, "latest_findings", lambda: [FLAGGED])
    monkeypatch.setattr(runtime_patch.runtimes, "patch_release_date", lambda kind, v: date(2026, 10, 1))
    for fn in ("check", "run", "preview"):   # the defaults were bound at definition: point them at the fake
        f = getattr(runtime_patch, fn)
        monkeypatch.setattr(f, "__kwdefaults__",
                            {**(f.__kwdefaults__ or {}), ("run" if fn == "check" else "runner"): brew})
    (tmp_path / "repos").mkdir()
    old = sign_grant(world["pem"], grant.build(ops=["stage-fix"], repo_roots=[str(tmp_path / "repos")], hosts=[],
                                              days=5, max_uses=5, key_id="op", governance_dir=str(world["gov"])))
    grant.install(old)
    grant.revoke(old["grant_id"])
    g = sign_grant(world["pem"], grant.build(ops=["stage-fix", "runtime-patch"], repo_roots=[str(tmp_path / "repos")],
                                            hosts=[], days=30, max_uses=50, key_id="op", governance_dir=str(world["gov"]),
                                            runtimes=["node@22"], ancestor=old["grant_id"]))
    grant.install(g)
    return {**world, "brew": brew, "cfg": {"governance_dir": str(world["gov"])}}


def digest() -> str:
    return runtime_patch.preview("node@22")["plan_sha256"]


def test_same_major_bottle_upgrade_of_one_signed_formula(env: dict[str, Any]) -> None:
    d = digest()
    res = executor.act("runtime-patch", "node@22", env["cfg"], payload={"confirm_sha256": d})
    assert res["status"] == "done" and res["from"] == "22.11.0" and res["to"] == "22.12.0", res
    assert [r["kind"] for r in ledger.read()] == ["begun", "note", "done"]
    upgrade = [c for c in env["brew"].calls if c[0] == ["upgrade", "--formula", "node@22"]]
    assert len(upgrade) == 1 and upgrade[0][1]["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert upgrade[0][1]["HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK"] == "1"
    assert all("sudo" not in a for a, _ in env["brew"].calls)
    preview_dry = next(e for a, e in env["brew"].calls if "--dry-run" in a)
    assert "HOMEBREW_NO_AUTO_UPDATE" not in preview_dry                   # only the preview may refresh the index


@pytest.mark.parametrize("change,match", [
    ({"tap": "someone/tap"}, "not homebrew/core"),
    ({"linked_keg": None}, "not installed and linked"),
    ({"pinned": True}, "pinned"),
    ({"bottle": {"stable": {"files": {"x86_64_linux": {}}}}}, "refusing a source build"),
])
def test_d4_refusals_from_brew_info(env: dict[str, Any], change: dict[str, Any], match: str) -> None:
    env["brew"].info.update(change)
    with pytest.raises(executor.ExecError, match=match):
        digest()


@pytest.mark.parametrize("dry,match", [
    ("==> Would upgrade 1 outdated package:\nnode@22 22.11.0 -> 23.0.0\n", "crosses a major"),
    ("==> Would upgrade 2 outdated packages:\nnode@22 22.11.0 -> 22.12.0\nicu4c 74 -> 76\n", "nothing else"),
    ("==> Would install 1 dependency for node@22:\nc-ares\nnode@22 22.11.0 -> 22.12.0\n", "nothing else"),
    ("", "nothing else"),
])
def test_d4_dry_run_must_name_exactly_one_formula_same_major(env: dict[str, Any], dry: str, match: str) -> None:
    env["brew"].dry = dry
    with pytest.raises(executor.ExecError, match=match):
        digest()


def test_refused_formulae_and_the_executors_own_python(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("python@3.14", "git", "gh", "openssh", "gnupg"):
        res = executor.act("runtime-patch", name, env["cfg"], payload={"confirm_sha256": "a" * 64})
        assert res["status"] == "refused" and "refuses" in res["detail"], name
    monkeypatch.setattr(runtime_patch, "own_tool_formulae", lambda: {"node@22"})
    with pytest.raises(executor.ExecError, match="executor's own"):
        digest()
    assert not ledger.read() or all(r["kind"] == "refused" for r in ledger.read())


def test_unsigned_runtime_is_not_covered(env: dict[str, Any]) -> None:
    res = executor.act("runtime-patch", "node@20", env["cfg"], payload={"confirm_sha256": "a" * 64})
    assert res["status"] == "refused" and "not authorized" in res["detail"]


def test_plan_changed_since_confirmation(env: dict[str, Any]) -> None:
    d = digest()
    env["brew"].dry = "==> Would upgrade 1 outdated package:\nnode@22 22.11.0 -> 22.13.0\n"
    res = executor.act("runtime-patch", "node@22", env["cfg"], payload={"confirm_sha256": d})
    assert res["status"] == "failed" and "changed since you confirmed" in res["detail"]
    assert not any(a == ["upgrade", "--formula", "node@22"] for a, _ in env["brew"].calls)


def test_wrong_end_state_escalates(env: dict[str, Any]) -> None:
    d = digest()
    env["brew"].after = "22.11.0"
    res = executor.act("runtime-patch", "node@22", env["cfg"], payload={"confirm_sha256": d})
    assert res["status"] == "failed" and "escalation" in res["detail"]
    assert escalations.open_escalations()[0].kind == "other"


def test_root_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_patch.os, "geteuid", lambda: 0)
    with pytest.raises(executor.ExecError, match="never runs as root"):
        runtime_patch.brew_path()


# ---------------------------------------------------------------- host_change.operator_act: preview, then confirm

def test_operator_act_asks_and_runs_only_on_yes(env: dict[str, Any]) -> None:
    said: list[str] = []
    calls: list[dict[str, Any]] = []

    def fake_act(op: str, target: str, cfg: dict[str, Any], **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"status": "done"}
    no = host_change.operator_act("runtime-patch", "node@22", env["cfg"], confirm_sha256=None, act=fake_act,
                                  ask=lambda _: "n", say=said.append, tty=lambda: True)
    assert no["status"] == "refused" and calls == [] and any("plan sha256" in s for s in said)
    yes = host_change.operator_act("runtime-patch", "node@22", env["cfg"], confirm_sha256=None, act=fake_act,
                                   ask=lambda _: "y", say=said.append, tty=lambda: True)
    assert yes["status"] == "done" and calls[0]["payload"] == {"confirm_sha256": digest()}


def test_operator_act_needs_a_terminal_even_with_the_digest(env: dict[str, Any]) -> None:
    ran: list[Any] = []

    def run(*a: Any, **k: Any) -> dict[str, Any]:
        ran.append(1)
        return {"status": "done"}
    for confirm in (None, digest()):
        res = host_change.operator_act("runtime-patch", "node@22", env["cfg"], confirm_sha256=confirm, act=run,
                                       ask=lambda _: "y", say=lambda _: None, tty=lambda: False)
        assert res["status"] == "refused" and "terminal" in res["detail"] and not ran
    res = host_change.operator_act("runtime-patch", "node@22", env["cfg"], confirm_sha256="b" * 64, act=run,
                                   ask=lambda _: "y", say=lambda _: None, tty=lambda: True)
    assert res["status"] == "refused" and not ran
    res = host_change.operator_act("runtime-patch", "node@22", env["cfg"], confirm_sha256=digest(), act=run,
                                   ask=lambda _: "y", say=lambda _: None, tty=lambda: True)
    assert res["status"] == "done" and ran == [1]


# ---------------------------------------------------------------- 8b: flagged, aged, poured; the executor's toolchain

def test_a_formula_the_latest_scan_did_not_flag_is_refused(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    for findings in ([], [{**FLAGGED, "target": "web1"}], [{**FLAGGED, "detail": "eol [nvm]"}],
                     [{**FLAGGED, "locations": ["/opt/homebrew/Cellar/node@20/20.1.0/bin/node"]}]):
        monkeypatch.setattr(runtime_patch, "latest_findings", lambda f=findings: f)
        with pytest.raises(executor.ExecError, match="no this-machine runtime finding"):
            digest()


@pytest.mark.parametrize("released,match", [(date(2026, 10, 25), "released"), (None, "unknown")])
def test_s10_cooldown_on_the_target_version(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                            released: date | None, match: str) -> None:
    monkeypatch.setattr(runtime_patch.runtimes, "patch_release_date", lambda kind, v: released)
    with pytest.raises(executor.ExecError, match=match):
        runtime_patch.check("node@22", apply=False, today=date(2026, 11, 3))


def test_a_source_build_after_the_upgrade_escalates(env: dict[str, Any]) -> None:
    d = digest()
    env["brew"].poured = False
    res = executor.act("runtime-patch", "node@22", env["cfg"], payload={"confirm_sha256": d})
    assert res["status"] == "failed" and "poured False" in res["detail"] and escalations.open_escalations()


def test_the_executors_toolchain_and_its_dependencies_are_refused(env: dict[str, Any],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    assert {"openssl@3", "ca-certificates"} <= runtime_patch.TOOL_DEPENDENCIES
    chain = runtime_patch.toolchain(env["brew"])
    assert {"python@3.14", "openssl@3", "mpdecimal", "ca-certificates"} <= chain
    env["brew"].deps = "node@22\n"                       # a tool depends on node@22 on this machine
    with pytest.raises(executor.ExecError, match="depends on"):
        digest()


def test_latest_findings_reads_the_newest_completed_run(env: dict[str, Any]) -> None:
    from agentteams.vulnscan import paths

    with pytest.raises(executor.ExecError, match="no completed scan"):
        REAL_LATEST()
    run = paths.state_dir() / "2026-11-02"
    run.mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"findings": [FLAGGED]}))
    assert REAL_LATEST() == [FLAGGED]
