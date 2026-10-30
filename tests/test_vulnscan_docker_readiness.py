"""agentteams.vulnscan.docker_readiness (T2-A): one Docker probe with a reason code per layer, and the four
probes that delegate to it. Every subprocess goes through a fake runner: no test calls real docker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import (
    agent_run,
    daily,
    docker_readiness as dr,
    executor,
    executor_serve,
    ledger,
    paths,
    remediation_status,
    subproc_confine as sc,
)
from tests._vulnscan_helpers import operator_env

IMAGE_ID = "sha256:" + "a" * 64
AGENT_ID = "sha256:" + "b" * 64
ON = {"confine": {"subprocesses": True}, "servers": ["web1"]}


class FakeDocker:
    """A runner: the daemon is ``up`` or not, ``images`` maps tag -> id, ``starts`` counts start commands."""

    def __init__(self, *, up: bool = True, images: dict[str, str] | None = None, gui: bool = True,
                 comes_up_after: int | None = None) -> None:
        self.up = up
        self.images = images if images is not None else {}
        self.gui = gui
        self.comes_up_after = comes_up_after   # version probes until a started daemon answers
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout: int) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        assert timeout <= 60, "a probe never waits longer than a minute"

        def done(rc: int = 0, out: str = "") -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(argv, rc, out, "")

        if argv[:2] == ["docker", "version"]:
            if not self.up and self.comes_up_after is not None and self.started():
                self.comes_up_after -= 1
                self.up = self.comes_up_after <= 0
            return done(0, "27.0\n") if self.up else done(1)
        if argv[:3] == ["docker", "image", "inspect"]:
            tag = argv[-1]
            return done(0, self.images[tag] + "\n") if self.up and tag in self.images else done(1)
        if argv[:2] == ["launchctl", "print"]:
            return done(0 if self.gui else 113)
        if argv[:1] in (["open"], ["systemctl"]):
            return done(0)
        raise AssertionError(f"unexpected command {argv}")

    def started(self) -> bool:
        return any(c[:1] in (["open"], ["systemctl"]) for c in self.calls)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    w = operator_env(tmp_path, monkeypatch)
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker" if name == "docker"
                        else real_which(name, *a, **k))
    monkeypatch.setattr(dr.time, "sleep", lambda s: None)

    def no_real_docker(*a: Any, **k: Any) -> None:
        raise AssertionError("real subprocess.run called")
    monkeypatch.setattr(dr, "_run", no_real_docker)
    return w


def _tools_record(image_id: str = IMAGE_ID, *, passed: bool = True) -> None:
    path = sc.selftest_record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"passed": passed, "image_id": image_id, "flags_sha256": sc.flags_sha256(image_id)}))


def _tag() -> str:
    return sc.image_tag(sc.settings(ON))


# ---------------------------------------------------------------- ensure: the layers and their codes

def test_nothing_that_uses_docker_means_no_probe(env: dict) -> None:
    fake = FakeDocker()
    rec = dr.ensure({"confine": {"subprocesses": False}}, start=True, runner=fake)
    assert rec.ok and fake.calls == [] and all(v["state"] == "off" for v in rec.to_dict().values())


def test_ready_when_every_layer_passes(env: dict) -> None:
    _tools_record()
    rec = dr.ensure(ON, start=False, runner=FakeDocker(images={_tag(): IMAGE_ID}))
    assert rec.ok and rec.tools() == "" and rec.problem() == ""
    d = rec.to_dict()
    assert set(d) == set(dr.LAYERS) and {k: v["state"] for k, v in d.items()} == {
        "cli": "ok", "daemon": "ok", "tools_image": "ok", "agent_image": "off", "tools_selftest": "ok",
        "agent_selftest": "off"}


def test_each_failure_has_its_own_code_and_message(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    down = dr.ensure(ON, start=False, runner=FakeDocker(up=False))
    assert down.daemon.code == dr.DAEMON and "daemon does not answer" in down.daemon.message
    assert down.tools_image.state == "skipped" and down.tools().code == dr.DAEMON
    missing = dr.ensure(ON, start=False, runner=FakeDocker())
    assert missing.tools_image.code == dr.IMAGE and "is not built" in missing.tools_image.message
    stale = dr.ensure(ON, start=False, runner=FakeDocker(images={_tag(): IMAGE_ID}))
    assert stale.tools_selftest.code == dr.SELFTEST and "tools-selftest" in stale.tools_selftest.message
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    no_cli = dr.ensure(ON, start=False, runner=FakeDocker())
    assert no_cli.cli.code == dr.DAEMON and no_cli.daemon.state == "skipped"
    assert len({down.tools(), missing.tools(), stale.tools()}) == 3   # R4: no longer one message for all


def test_docker_is_started_only_when_autostart_is_on(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dr.platform, "system", lambda: "Darwin")
    off = FakeDocker(up=False, comes_up_after=1)
    rec = dr.ensure(ON, start=True, runner=off)          # autostart ships false
    assert not off.started() and rec.daemon.code == dr.DAEMON and "autostart is false" in rec.daemon.message
    on = {**ON, "docker": {"autostart": True}}
    fake = FakeDocker(up=False, comes_up_after=2, images={_tag(): IMAGE_ID})
    _tools_record()
    rec = dr.ensure(on, start=True, runner=fake)
    assert rec.ok and ["open", "-g", "-a", "Docker"] in fake.calls and "started Docker" in rec.actions
    assert all("sudo" not in arg for call in fake.calls for arg in call)
    read_only = FakeDocker(up=False, comes_up_after=1)
    assert not dr.ensure(on, start=False, runner=read_only).ok and not read_only.started()


def test_no_gui_session_and_a_start_timeout(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dr.platform, "system", lambda: "Darwin")
    on = {**ON, "docker": {"autostart": True, "start_timeout_s": 10}}
    rec = dr.ensure(on, start=True, runner=FakeDocker(up=False, gui=False))
    assert rec.daemon.code == dr.NO_GUI
    clock = iter(range(0, 1000, 5))
    monkeypatch.setattr(dr.time, "monotonic", lambda: next(clock))
    rec = dr.ensure(on, start=True, runner=FakeDocker(up=False))   # never comes up
    assert rec.daemon.code == dr.DAEMON and "within 10s" in rec.daemon.message


def test_linux_starts_the_user_unit_never_sudo(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dr.platform, "system", lambda: "Linux")
    _tools_record()
    fake = FakeDocker(up=False, comes_up_after=1, images={_tag(): IMAGE_ID})
    assert dr.ensure({**ON, "docker": {"autostart": True}}, start=True, runner=fake).ok
    assert ["systemctl", "--user", "start", "docker-desktop"] in fake.calls


def test_a_stale_tools_selftest_is_rerun_once_per_image(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    runs: list[str] = []

    def selftest(cfg: dict) -> tuple[bool, str]:
        runs.append("tools")
        _tools_record(passed=False)   # the real self-test records its result, pass or fail
        return False, "rootfs_read_only"
    monkeypatch.setattr(dr, "_tools_selftest_run", selftest)
    _tools_record("sha256:old")
    fake = FakeDocker(images={_tag(): IMAGE_ID})
    assert not dr.ensure(ON, start=False, runner=fake).ok and runs == []        # read-only: never runs one
    assert not dr.ensure({**ON, "docker": {"auto_selftest": "off"}}, start=True, runner=fake).ok and runs == []
    rec = dr.ensure(ON, start=True, runner=fake)
    assert runs == ["tools"] and rec.tools_selftest.code == dr.SELFTEST and "ran tools-selftest" in rec.actions[0]
    dr.ensure(ON, start=True, runner=fake)
    assert runs == ["tools"], "an image whose self-test failed is not re-tested every run"


def test_agent_layers(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {"confine": {"subprocesses": False},
           "agent": {"enabled": True, "image": "agent:t", "team_dir": "/t", "key_file": "/k"}}
    assert dr.needs({"agent": {"enabled": True}}) == (False, False)   # unconfigured: the settings are why, not Docker
    monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (False, "the agent image changed"))
    ran: list[bool] = []
    monkeypatch.setattr(dr, "_agent_selftest_run", lambda c: ran.append(True) or (True, ""))
    rec = dr.ensure(cfg, start=True, runner=FakeDocker())
    assert rec.agent_image.code == dr.IMAGE and rec.agent().code == dr.IMAGE and not ran
    rec = dr.ensure(cfg, start=True, runner=FakeDocker(images={"agent:t": AGENT_ID}))
    assert ran == [True] and rec.ok and rec.tools_image.state == "off"


# ---------------------------------------------------------------- the four probes delegate

def test_unavailable_reason_and_scope_carry_the_code(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "_docker", lambda argv, check=True, timeout=120: FakeDocker(up=False)(argv, timeout))
    why = sc.unavailable_reason(_tag())
    assert why.startswith("docker is not reachable") and why.code == dr.DAEMON
    with pytest.raises(sc.Unavailable, match="tools container unavailable: docker is not reachable") as caught:
        sc.scope(ON)
    assert caught.value.code == dr.DAEMON


def test_inside_a_run_the_record_answers_without_probing(env: dict) -> None:
    rec = dr.ensure(ON, start=False, runner=FakeDocker(up=False))
    with dr.using(rec):   # sc._docker is not patched: a probe here would reach the guard in env
        assert sc.unavailable_reason(_tag()).code == dr.DAEMON
        assert remediation_status.collect({**ON, "fix_mode": "apply-worktree"})["docker"]["daemon"]["code"] == dr.DAEMON
    assert remediation_status.collect({})["docker"] == {}


def test_agent_docker_unreachable_distinguishes_daemon_and_image(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDocker(up=True)
    monkeypatch.setattr(agent_run, "_docker", lambda argv, check=True, timeout=600: fake(argv, timeout))
    assert "agent image img:t is not built" in agent_run.docker_unreachable("img:t")
    assert agent_run.docker_unreachable() == ""
    fake.up = False
    assert agent_run.docker_unreachable("img:t").startswith("docker is not reachable")


def test_executor_refusals_record_the_reason_code(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "_docker", lambda argv, check=True, timeout=120: FakeDocker(up=True)(argv, timeout))
    res = executor.act("stage-fix", str(env["tmp"] / "repo"), {**ON, "governance_dir": str(env["gov"])})
    assert res["status"] == "refused" and res["detail"].startswith("tools container unavailable:")
    assert res["reason_code"] == dr.IMAGE
    row = [r for r in ledger.read() if r["kind"] == "refused"][-1]
    assert row["reason_code"] == dr.IMAGE and row["detail"].startswith("tools container unavailable:")
    block = executor_serve.batch_block(ON, "server-audit")
    assert block.startswith("tools container unavailable:") and block.code == dr.IMAGE


def test_the_daily_run_ensures_once_and_records_it(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    rec = dr.Readiness(cli=dr.Layer("ok"), daemon=dr.Layer("fail", dr.DAEMON, "down"),
                       tools_image=dr.Layer("skipped"), tools_tag=_tag())

    def ensure(cfg: dict, *, start: bool, runner: Any = None) -> dr.Readiness:
        calls.append(start)
        return rec
    monkeypatch.setattr(dr, "ensure", ensure)
    cfg = {**daily.DEFAULT_CONFIG, **ON, "local_runtimes": False, "notify": "none"}
    report, code, run_dir = daily.run_daily(cfg, today="2026-10-30")
    assert calls == [True] and code == 3
    assert any("web1: not scanned: tools container unavailable: down" in e for e in report.feed_errors)
    summary = json.loads((run_dir / "summary.json").read_text())
    assert "docker" not in summary and summary["remediation"]["docker"]["daemon"]["code"] == dr.DAEMON


def test_the_agent_phase_uses_the_record(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = dr.Readiness(cli=dr.Layer("ok"), daemon=dr.Layer("ok"),
                       agent_image=dr.Layer("fail", dr.IMAGE, "the agent image a:t is not built"), agent_tag="a:t")
    monkeypatch.setattr(agent_run, "run_agent", lambda *a, **k: pytest.fail("run_agent must not probe again"))
    monkeypatch.setattr(daily, "publish_run", lambda *a, **k: None)
    run_dir = paths.state_dir() / "2026-10-30"
    run_dir.mkdir(parents=True)
    with dr.using(rec):
        daily._agent_phase({"notify": "none", "agent": {"enabled": True}}, run_dir)
    beat = json.loads(daily.heartbeat_path().read_text())
    assert beat["agent"]["state"] == "not-run" and beat["agent"]["reason"].startswith("infra.tools_image: ")


# ---------------------------------------------------------------- config and the `ready` command

@pytest.mark.parametrize(("block", "word"), [
    ([], "must be an object"), ({"start": True}, "unknown keys"), ({"autostart": "yes"}, "autostart"),
    ({"start_timeout_s": 0}, "start_timeout_s"), ({"start_timeout_s": True}, "start_timeout_s"),
    ({"auto_selftest": "always"}, "auto_selftest")])
def test_config_problems(block: object, word: str) -> None:
    assert word in daily._config_problem({**daily.DEFAULT_CONFIG, "docker": block})


def test_the_default_never_starts_docker() -> None:
    assert daily.DEFAULT_CONFIG["docker"] == {"autostart": False, "start_timeout_s": 240, "auto_selftest": "on-change"}
    assert daily._config_problem({**daily.DEFAULT_CONFIG, "docker": {"autostart": True}}) == ""


def test_ready_cli_exit_codes(env: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from agentteams.vulnscan import __main__ as cli

    paths.config_file().parent.mkdir(parents=True, exist_ok=True)
    paths.config_file().write_text(json.dumps(ON))
    seen: list[bool] = []
    rec = {"value": dr.Readiness(cli=dr.Layer("ok"), daemon=dr.Layer("fail", dr.DAEMON, "down"))}
    monkeypatch.setattr(dr, "ensure", lambda cfg, *, start, runner=None: seen.append(start) or rec["value"])
    assert cli.main(["ready"]) == 3 and "DEGRADED infra.docker_daemon" in capsys.readouterr().out
    rec["value"] = dr.Readiness()
    assert cli.main(["ready", "--ensure", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["daemon"]["state"] == "off" and seen == [False, True]
    paths.config_file().write_text(json.dumps({"docker": {"autostart": "yes"}}))
    assert cli.main(["ready"]) == 2
