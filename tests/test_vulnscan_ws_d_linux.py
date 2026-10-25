"""R4 (rc.5): the container user follows the Docker mode. ``docker info`` is mocked: no Docker needed.

Linux Docker Engine cannot be exercised on a Docker Desktop machine; the CI job
``.github/workflows/vulnscan-linux-containers.yml`` runs the real containers on a rootful Engine.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agentteams.vulnscan import agent_run, docker_mode, executor, gateway_context, selftest_probe
from tests._vulnscan_helpers import goose_recipe

UID, GID = 1000, 1000
DESKTOP_MAC = {"ServerVersion": "29.0.0", "OSType": "linux", "OperatingSystem": "Docker Desktop",
               "SecurityOptions": ["name=seccomp,profile=builtin", "name=cgroupns"]}
ENGINE = {"ServerVersion": "29.0.0", "OSType": "linux", "OperatingSystem": "Ubuntu 24.04 LTS",
          "SecurityOptions": ["name=apparmor", "name=seccomp,profile=builtin", "name=cgroupns"]}
ROOTLESS = {**ENGINE, "SecurityOptions": ["name=seccomp,profile=builtin", "name=rootless", "name=cgroupns"]}
#: The operator's user on a rootful Engine: the real one, since the team dir must be owned by it.
ME = f"{os.getuid()}:{os.getgid()}"
NOT_ROOT = pytest.mark.skipif(os.getuid() == 0, reason="a rootful Engine refuses an operator who is root")
USERNS = {**ENGINE, "SecurityOptions": ["name=apparmor", "name=seccomp,profile=builtin", "name=userns"]}


def _classify(info: object, platform: str = "linux", uid: int = UID, gid: int = GID) -> docker_mode.DockerMode:
    return docker_mode.classify(info, uid=uid, gid=gid, platform=platform)


# ---------------------------------------------------------------- the mode table

@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_docker_desktop_keeps_the_image_user(platform: str) -> None:
    assert _classify(DESKTOP_MAC, platform) == docker_mode.DockerMode(docker_mode.DESKTOP)


def test_rootful_engine_runs_as_the_operator() -> None:
    assert _classify(ENGINE) == docker_mode.DockerMode(docker_mode.ROOTFUL, f"{UID}:{GID}")


def test_rootful_engine_refuses_an_operator_who_is_root() -> None:
    mode = _classify(ENGINE, uid=0)
    assert mode.name == docker_mode.ROOTFUL and mode.user is None and "running as root" in mode.problem
    mode = _classify(ENGINE, uid=501, gid=0)   # @security rc.5: root's group is refused too
    assert mode.user is None and "primary group" in mode.problem


@pytest.mark.parametrize("bad", ["501", "501:20,mode=0777", "a:b", "501:20\n", "-1:20", "1" * 11 + ":20"])
def test_a_container_user_must_be_decimal_uid_gid(bad: str, tmp_path: Path) -> None:
    """@security rc.5: the user reaches --user and the tmpfs options; a record can never unlock a malformed one."""
    with pytest.raises(ValueError, match="not a container user"):
        agent_run.container_user(bad)
    record = tmp_path / "agent-selftest.json"
    record.write_text(json.dumps({"container_user": bad}))
    assert docker_mode.recorded_user(record, agent_run.AGENT_UID) == agent_run.AGENT_UID
    assert agent_run.container_user("501:20") == "501:20" and agent_run.container_user(None) == agent_run.AGENT_UID


def test_rootless_runs_as_root_inside_the_user_namespace() -> None:
    assert _classify(ROOTLESS) == docker_mode.DockerMode(docker_mode.ROOTLESS, docker_mode.ROOTLESS_USER)


def test_userns_remap_is_refused_with_an_explanation() -> None:
    mode = _classify(USERNS)
    assert mode.name == docker_mode.USERNS_REMAP and mode.user is None and "userns-remap" in mode.problem


@pytest.mark.parametrize("info", [None, [], "x", {}, {"OperatingSystem": "Ubuntu"}, {**ENGINE, "OSType": "windows"}])
def test_without_positive_evidence_the_mode_is_unknown(info: object) -> None:
    assert _classify(info) == docker_mode.DockerMode(docker_mode.UNKNOWN)


def test_an_engine_on_a_macos_host_keeps_the_image_user() -> None:
    """Another VM on macOS (not Docker Desktop) is not verified: the old behaviour, uid 10001."""
    assert _classify(ENGINE, "darwin") == docker_mode.DockerMode(docker_mode.UNKNOWN)
    assert _classify(ROOTLESS, "darwin").user is None


# ---------------------------------------------------------------- detection

def _answer(stdout: object, rc: int = 0):
    return lambda argv: subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr="")


@NOT_ROOT
def test_detect_asks_docker_info_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_mode.sys, "platform", "linux")
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(ENGINE), stderr="")
    assert docker_mode.detect(runner).user == ME
    assert seen == [["docker", "info", "--format", "{{json .}}"]]


@pytest.mark.parametrize("runner", [_answer("not json"), _answer(json.dumps(ENGINE), rc=1), _answer(None),
                                    _answer(b"{}"), lambda argv: None])
def test_detect_falls_back_to_the_image_user(runner: object) -> None:
    assert docker_mode.detect(runner) == docker_mode.DockerMode(docker_mode.UNKNOWN)


@pytest.mark.parametrize("exc", [OSError("no docker"), subprocess.TimeoutExpired(["docker"], 60)])
def test_detect_survives_a_missing_or_hung_docker(exc: Exception) -> None:
    def runner(argv: list[str]) -> subprocess.CompletedProcess:
        raise exc
    assert docker_mode.detect(runner).name == docker_mode.UNKNOWN


def test_recorded_user(tmp_path: Path) -> None:
    rec = tmp_path / "agent-selftest.json"
    assert docker_mode.recorded_user(rec, "10001:10001") == "10001:10001"          # absent
    rec.write_text("[]")
    assert docker_mode.recorded_user(rec, "10001:10001") == "10001:10001"          # not an object
    rec.write_text(json.dumps({"container_user": "1000:1000"}))
    assert docker_mode.recorded_user(rec, "10001:10001") == "1000:1000"


# ---------------------------------------------------------------- argv and fingerprint (pure)

def test_hashing_and_argv_never_run_a_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **kw: object) -> None:
        raise AssertionError("a subprocess ran while hashing or building argv")
    monkeypatch.setattr(agent_run, "_docker", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    for user in (None, "1000:1000", "0:0"):
        agent_run.flags_sha256(image_id="sha256:x", user=user)
        agent_run._base_argv(Path("/r"), Path("/s"), Path("/t"), Path("/sp"), name="n", user=user)
        agent_run.gateway_argv({**agent_run.DEFAULT_AGENT, "key_file": "/k"}, "img", Path("/spend"),
                               dlp=gateway_context.placeholder(), user=user)


def test_docker_desktop_argv_is_unchanged() -> None:
    base = " ".join(agent_run._base_argv(Path("/r"), Path("/s"), Path("/t"), Path("/sp"), name="n"))
    assert "/agenthome:rw,size=256m,uid=10001,gid=10001 " in base
    assert "/work/cwd:rw,size=16m,uid=10001,gid=10001,mode=0700 " in base and "--user 10001:10001 " in base
    assert agent_run._base_argv(Path("/r"), Path("/s"), Path("/t"), Path("/sp"), name="n") == \
        agent_run._base_argv(Path("/r"), Path("/s"), Path("/t"), Path("/sp"), name="n", user=agent_run.AGENT_UID)
    assert agent_run.flags_sha256(image_id="i") == agent_run.flags_sha256(image_id="i", user=agent_run.AGENT_UID)
    assert agent_run.selftest_script() == agent_run.SELFTEST and selftest_probe.NON_ROOT in agent_run.SELFTEST


@pytest.mark.parametrize("user", ["1000:1000", "0:0"])
def test_the_container_user_and_tmpfs_owner_follow_the_mode(user: str) -> None:
    uid, gid = user.split(":")
    base = " ".join(agent_run._base_argv(Path("/r"), Path("/s"), Path("/t"), Path("/sp"), name="n", user=user))
    assert f"--user {user} " in base and "10001" not in base
    assert f"/agenthome:rw,size=256m,uid={uid},gid={gid} " in base
    assert f"/work/cwd:rw,size=16m,uid={uid},gid={gid},mode=0700 " in base
    gw = agent_run.gateway_argv({**agent_run.DEFAULT_AGENT, "key_file": "/k"}, "img", Path("/spend"),
                                dlp=gateway_context.placeholder(), user=user)
    assert gw[gw.index("--user") + 1] == user


def test_the_probe_checks_rootless_differently() -> None:
    rootless = agent_run.selftest_script(user="0:0")
    assert selftest_probe.NON_ROOT_ROOTLESS in rootless and selftest_probe.NON_ROOT + "\n" not in rootless
    assert selftest_probe.NON_ROOT in agent_run.selftest_script(user="1000:1000")


class _Os:
    def __init__(self, uid: int) -> None:
        self._uid = uid

    def getuid(self) -> int:
        return self._uid


@pytest.mark.parametrize(("uid", "uid_map", "ok"), [
    (0, "         0       1000          1\n         1     100000      65536\n", True),   # rootless: root maps to 1000
    (0, "         0          0 4294967295\n", False),                                  # real root
    (1000, "         0       1000          1\n", False),                                # not uid 0 inside
    (0, "", False),                                                                   # no mapping
])
def test_rootless_non_root_check(uid: int, uid_map: str, ok: bool) -> None:
    r: dict[str, bool] = {}

    def fake_open(path: str) -> object:
        assert path == "/proc/self/uid_map"

        class F:
            def read(self) -> str:
                return uid_map
        return F()
    exec(selftest_probe.NON_ROOT_ROOTLESS, {"os": _Os(uid), "open": fake_open, "r": r})   # noqa: S102
    assert r["non_root"] is ok


# ---------------------------------------------------------------- self-test and run

@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(docker_mode.sys, "platform", "linux")
    monkeypatch.setattr(agent_run.time, "sleep", lambda s: None)
    team = tmp_path / "team"
    team.mkdir()
    goose_recipe(team)                     # R7: a run and the self-test read a checked copy of it
    key = tmp_path / "or.env"
    key.write_text("OPENROUTER_API_KEY=sk-x\n")
    for d in ("requests", "results"):
        (executor.spool_dir() / d).mkdir(parents=True, exist_ok=True)
    return {"agent": {"team_dir": str(team), "key_file": str(key)}}


def _fake_docker(info: dict, calls: list[list[str]]):
    ok_line = "SELFTEST " + json.dumps({"no_capabilities": True})

    def docker(argv: list[str], **kw: object) -> subprocess.CompletedProcess:
        calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(info), stderr="")
        if "-c" in argv:   # the probe: the container reports the user it was started as (identity_matches)
            ran_as = argv[argv.index("--user") + 1]
            return subprocess.CompletedProcess(argv, 0, stdout=f"SELFTEST-IDENTITY {ran_as}\n{ok_line}", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="sha256:img", stderr="")
    return docker


@NOT_ROOT
def test_selftest_runs_as_the_detected_user_and_records_it(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_run, "_docker", _fake_docker(ENGINE, calls))
    res = agent_run.selftest(cfg)
    user = ME
    assert res["passed"] and res["docker_mode"] == "rootful" and res["container_user"] == user
    assert res["flags_sha256"] == agent_run.flags_sha256(agent_run.agent_cfg(cfg), "sha256:img", user)
    assert res["flags_sha256"] != agent_run.flags_sha256(agent_run.agent_cfg(cfg), "sha256:img")
    runs = [c for c in calls if c[:2] == ["docker", "run"]]
    assert len(runs) == 2 and all(c[c.index("--user") + 1] == user for c in runs)    # the gateway and the probe
    assert sum(c[:2] == ["docker", "info"] for c in calls) == 1
    assert agent_run.selftest_ok(cfg) == (True, "ok")


@NOT_ROOT
@pytest.mark.parametrize("reported", ["0:0", "10001:10001", None])
def test_selftest_fails_when_the_container_ran_as_someone_else(cfg: dict, monkeypatch: pytest.MonkeyPatch,
                                                               reported: str | None) -> None:
    """@audit #278: observed_user is enforced (identity_matches), not only recorded; a silent probe fails too."""
    real = _fake_docker(ENGINE, [])

    def docker(argv: list[str], **kw: object) -> subprocess.CompletedProcess:
        out = real(argv, **kw)
        if "-c" in argv:
            body = out.stdout.split("\n", 1)[1]
            out = subprocess.CompletedProcess(argv, 0, stdout=(f"SELFTEST-IDENTITY {reported}\n" if reported else "") + body,
                                              stderr="")
        return out

    monkeypatch.setattr(agent_run, "_docker", docker)
    res = agent_run.selftest(cfg)
    assert not res["passed"] and res["checks"]["identity_matches"] is False and res["observed_user"] == reported


def test_selftest_refuses_userns_remap_before_any_container(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(agent_run, "_docker", _fake_docker(USERNS, calls))
    res = agent_run.selftest(cfg)
    assert not res["passed"] and res["checks"] == {"docker_mode_supported": False} and "userns-remap" in res["error"]
    assert not [c for c in calls if c[:2] == ["docker", "run"]]


def _run(cfg: dict, monkeypatch: pytest.MonkeyPatch, info: dict, recorded: str | None) -> tuple[dict, list[str | None]]:
    state = Path(agent_run.paths.state_dir())
    state.mkdir(parents=True, exist_ok=True)
    rec = {"passed": True} if recorded is None else {"passed": True, "container_user": recorded}
    (state / "agent-selftest.json").write_text(json.dumps(rec))
    run_dir = state / "2026-10-22"
    run_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(agent_run.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (True, "ok"))
    monkeypatch.setattr(agent_run, "_docker", _fake_docker(info, []))
    users: list[str | None] = []

    def launch(*args: object) -> tuple[bool, int]:
        users.append(args[-1])
        return False, 0
    monkeypatch.setattr(agent_run, "_launch", launch)
    return agent_run.run_agent(cfg, run_dir), users


@NOT_ROOT
def test_a_run_uses_the_mode_its_self_test_ran_in(cfg: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _, users = _run(cfg, monkeypatch, ENGINE, ME)
    assert users == [ME]
    _, users = _run(cfg, monkeypatch, DESKTOP_MAC, None)                 # a pre-R4 record: Docker Desktop
    assert users == [agent_run.AGENT_UID]


@NOT_ROOT
@pytest.mark.parametrize(("info", "recorded", "reason"), [
    (DESKTOP_MAC, ME, "Docker mode changed"),
    (ENGINE, None, "Docker mode changed"),
    (ROOTLESS, ME, "Docker mode changed"),
    (USERNS, None, "userns-remap"),
])
def test_a_run_refuses_another_mode(cfg: dict, monkeypatch: pytest.MonkeyPatch, info: dict, recorded: str | None,
                                    reason: str) -> None:
    st, users = _run(cfg, monkeypatch, info, recorded)
    assert st["run_state"] == "not-run" and reason in st["reason"] and users == []


# ---------------------------------------------------------------- the Linux CI job

def test_the_linux_containers_workflow() -> None:
    yaml = pytest.importorskip("yaml")
    path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "vulnscan-linux-containers.yml"
    text = path.read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    assert wf["permissions"] == {"contents": "read"}
    job = wf["jobs"]["linux-containers"]
    assert job["runs-on"] == "ubuntu-latest" and "permissions" not in job
    uses = [s["uses"] for s in job["steps"] if "uses" in s]
    assert uses and all(len(u.split("@")[1]) == 40 for u in uses)               # pinned by commit SHA (@security rc.5)
    assert "observed_user" in text                                              # asserts what the container ran as
    assert "_emit_recipe(" in text and '"description": "Task for' in text        # a recipe goose itself accepts
    assert "secrets." not in text and "tools-image.sh" in text and "FRAMEWORK=goose" in text


# ---------------------------------------------------------------- R7 rc.5: goose's own recipe check

@pytest.mark.parametrize(("rc", "exc", "ok"), [(0, None, True), (1, None, False), (0, OSError("no goose"), False),
                                               (0, subprocess.TimeoutExpired(["goose"], 60), False)])
def test_goose_recipe_entry(monkeypatch: pytest.MonkeyPatch, rc: int, exc: Exception | None, ok: bool) -> None:
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess:
        seen.append(argv)
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(argv, rc)
    monkeypatch.setattr(subprocess, "run", fake_run)
    r: dict[str, bool] = {}
    exec(selftest_probe.goose_recipe_entry('/work/run/a "b".yaml'), {"r": r})   # noqa: S102
    assert r == {"goose_recipe_valid": ok} and seen == [["goose", "recipe", "validate", '/work/run/a "b".yaml']]
