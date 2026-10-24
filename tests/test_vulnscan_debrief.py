"""agentteams.vulnscan.debrief: argv, lock refusal, cleanup and goose facts. No Docker, no network."""

from __future__ import annotations

import fcntl
import json
import signal
import subprocess
from pathlib import Path
from typing import Any, ClassVar

import pytest

from agentteams.vulnscan import agent_run, debrief, executor


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    run = tmp_path / "state" / "2026-10-22"
    agent_run.sessions_dir(run).mkdir(parents=True)
    team = tmp_path / "team"
    team.mkdir()
    key = tmp_path / "secrets" / "openrouter.env"
    key.parent.mkdir()
    key.write_text("OPENROUTER_API_KEY=placeholder\n")
    cfg = {"agent": {"enabled": True, "team_dir": str(team), "key_file": str(key), "image": "img:test"}}
    calls: list[list[str]] = []

    def fake_docker(argv: list[str], *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(agent_run, "_docker", fake_docker)
    monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (True, "ok"))
    monkeypatch.setattr(debrief.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(debrief, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(debrief.time, "sleep", lambda s: None)
    monkeypatch.setattr(debrief.gateway_context, "_scutil", lambda key: "")   # Popen is faked: no scutil
    return {"run": run, "cfg": cfg, "key": key, "calls": calls, "tmp": tmp_path}


class FakePopen:
    """Stands in for the inherited-stdio ``docker run -it`` client."""

    instances: ClassVar[list[FakePopen]] = []

    def __init__(self, argv: list[str], rc: int = 0, hang: bool = False) -> None:
        self.argv, self.rc, self.hang, self.killed = argv, rc, hang, False
        self.kwargs_seen: dict[str, Any] = {}
        FakePopen.instances.append(self)

    def wait(self, timeout: float | None = None) -> int:
        if self.hang and timeout is not None:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return -9 if self.killed else self.rc

    def kill(self) -> None:
        self.killed = True


def _popen(monkeypatch: pytest.MonkeyPatch, *, rc: int = 0, hang: bool = False) -> list[FakePopen]:
    FakePopen.instances = []

    def factory(argv: list[str], **kwargs: Any) -> FakePopen:
        p = FakePopen(argv, rc=rc, hang=hang)
        p.kwargs_seen = kwargs
        return p

    monkeypatch.setattr(debrief.subprocess, "Popen", factory)
    return FakePopen.instances


def _mounts(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, x in enumerate(argv) if x == "-v"]


# ---------------------------------------------------------------- argv

def test_debrief_argv_is_interactive_read_only_and_isolated(env: dict[str, Any]) -> None:
    a = agent_run.agent_cfg(env["cfg"])
    argv = debrief.debrief_argv(a, "img:test", env["run"], "vulnscan-2026-10-22")
    assert argv[:4] == ["docker", "run", "--rm", "-it"]
    assert argv[argv.index("--name") + 1] == debrief.DEBRIEF_CONTAINER == "agentteams-vulnscan-debrief"
    assert argv[argv.index("--network") + 1] == agent_run.NETWORK
    for flag in ("--read-only", "--cap-drop", "no-new-privileges"):
        assert flag in argv
    assert argv[argv.index("--user") + 1] == agent_run.AGENT_UID
    mounts = _mounts(argv)
    spool = executor.spool_dir()
    assert f"{spool / 'requests'}:/work/spool/requests:ro" in mounts
    assert f"{env['run']}:/work/run:ro" in mounts
    assert f"{agent_run.sessions_dir(env['run'])}:{agent_run.GOOSE_SESSIONS_IN_CONTAINER}:rw" in mounts
    writable = [m for m in mounts if m.endswith(":rw")]
    assert writable == [f"{agent_run.sessions_dir(env['run'])}:{agent_run.GOOSE_SESSIONS_IN_CONTAINER}:rw"]
    assert f"OPENROUTER_HOST=http://{debrief.DEBRIEF_GATEWAY}:{agent_run.GW_PORT}" in argv
    tail = argv[argv.index("img:test"):]
    assert tail == ["img:test", "goose", "session", "--resume", "--name", "vulnscan-2026-10-22",
                    "--with-extension", "python -m agentteams.vulnscan.mcp"]


def test_debrief_argv_carries_no_home_or_credential_paths(env: dict[str, Any]) -> None:
    argv = debrief.debrief_argv(agent_run.agent_cfg(env["cfg"]), "img:test", env["run"], "vulnscan-2026-10-22")
    joined = " ".join(argv)
    assert str(env["key"]) not in joined and "openrouter.env" not in joined   # the key reaches the gateway only
    for marker in (".ssh", ".config/gh", ".git-credentials", ".netrc", ".aws", ".gnupg", "~", "GH_TOKEN",
                   "GITHUB_TOKEN", "SSH_AUTH_SOCK"):
        assert marker not in joined, marker
    home = str(Path.home())
    sources = [m.split(":", 1)[0] for m in _mounts(argv)]
    assert not any(s == home for s in sources)


@pytest.mark.parametrize("bad", ["", "../x", "a b", "-n", "x;rm -rf /"])
def test_resume_command_rejects_unsafe_session_names(bad: str) -> None:
    with pytest.raises(ValueError):
        debrief.resume_command(bad)


def test_session_name_matches_the_agent_run() -> None:
    assert debrief.session_name(Path("/state/2026-10-22")) == "vulnscan-2026-10-22"


# ---------------------------------------------------------------- run_debrief

def test_run_debrief_refuses_while_the_run_lock_is_held(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    procs = _popen(monkeypatch)
    lock = agent_run.run_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert debrief.run_debrief(env["cfg"], env["run"]) == debrief.EXIT_LOCKED
        fcntl.flock(held, fcntl.LOCK_UN)
    assert procs == [] and env["calls"] == []
    assert "run lock" in capsys.readouterr().err
    assert not (env["run"] / "debrief").exists()               # G2: nothing written into the run dir


def test_run_debrief_holds_the_lock_and_cleans_up_on_success(env: dict[str, Any],
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    lock_was_held: list[bool] = []

    def factory(argv: list[str], **kwargs: Any) -> FakePopen:
        with open(agent_run.run_lock_path(), "a+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe, fcntl.LOCK_UN)
                lock_was_held.append(False)
            except BlockingIOError:
                lock_was_held.append(True)
        assert kwargs == {}                    # inherited stdio, no capture
        return FakePopen(argv)

    monkeypatch.setattr(debrief.subprocess, "Popen", factory)
    assert debrief.run_debrief(env["cfg"], env["run"]) == 0
    assert lock_was_held == [True]
    calls = env["calls"]
    gw = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
    assert gw[gw.index("--name") + 1] == debrief.DEBRIEF_GATEWAY
    assert gw[gw.index("--budget-usd") + 1] == "0.5"
    assert gw[gw.index("--max-tokens-total") + 1] == str(debrief.DEFAULT_DEBRIEF_MAX_TOKENS)   # V17
    assert ["docker", "network", "connect", agent_run.NETWORK, debrief.DEBRIEF_GATEWAY] in calls
    assert agent_run.GATEWAY not in [c[-1] for c in calls if c[:3] == ["docker", "rm", "-f"]]  # never a run's gateway
    assert calls[-2:] == [["docker", "rm", "-f", debrief.DEBRIEF_CONTAINER],
                          ["docker", "rm", "-f", debrief.DEBRIEF_GATEWAY]]
    status = json.loads(next((env["run"] / "debrief").glob("*/status.json")).read_text())
    assert status["exit"] == 0 and status["budget_usd"] == 0.5 and status["max_minutes"] == 20.0


def test_cleanup_runs_when_the_container_exits_non_zero(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    _popen(monkeypatch, rc=3)
    assert debrief.run_debrief(env["cfg"], env["run"]) == 3
    assert env["calls"][-2:] == [["docker", "rm", "-f", debrief.DEBRIEF_CONTAINER],
                                 ["docker", "rm", "-f", debrief.DEBRIEF_GATEWAY]]
    with open(agent_run.run_lock_path(), "a+") as probe:     # the lock is released afterwards
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_cleanup_runs_when_the_gateway_fails_to_start(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    procs = _popen(monkeypatch)
    calls: list[list[str]] = []

    def failing(argv: list[str], *, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
        calls.append(list(argv))
        if argv[:3] == ["docker", "run", "-d"]:
            raise RuntimeError("docker run failed: boom")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(agent_run, "_docker", failing)
    with pytest.raises(RuntimeError):
        debrief.run_debrief(env["cfg"], env["run"])
    assert procs == []
    assert calls[-2:] == [["docker", "rm", "-f", debrief.DEBRIEF_CONTAINER],
                          ["docker", "rm", "-f", debrief.DEBRIEF_GATEWAY]]


def test_time_cap_removes_the_container(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    env["cfg"]["agent"].update(debrief_max_minutes=0.01, debrief_budget_usd=0.25)
    procs = _popen(monkeypatch, hang=True)
    assert debrief.run_debrief(env["cfg"], env["run"]) == debrief.EXIT_TIMED_OUT
    assert procs[0].killed
    rm = ["docker", "rm", "-f", debrief.DEBRIEF_CONTAINER]
    assert env["calls"].count(rm) >= 3                       # pre-clean, at the cap, and in finally
    gw = next(c for c in env["calls"] if c[:3] == ["docker", "run", "-d"])
    assert gw[gw.index("--budget-usd") + 1] == "0.25"


@pytest.mark.parametrize("case", ["no-docker", "no-sessions", "no-tty", "no-selftest", "no-key", "home-team-dir"])
def test_preconditions_refuse_without_docker_calls(case: str, env: dict[str, Any],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    procs = _popen(monkeypatch)
    run = env["run"]
    if case == "no-docker":
        monkeypatch.setattr(debrief.shutil, "which", lambda name: None)
    elif case == "no-sessions":
        run = env["tmp"] / "state" / "2026-10-21"
        run.mkdir(parents=True)
    elif case == "no-tty":
        monkeypatch.setattr(debrief, "_stdin_is_tty", lambda: False)
    elif case == "no-selftest":
        monkeypatch.setattr(agent_run, "selftest_ok", lambda c: (False, "no isolation self-test has run"))
    elif case == "home-team-dir":   # a team_dir that would mount the home directory (and keys/) is refused
        env["cfg"]["agent"]["team_dir"] = str(Path.home())
    else:
        env["cfg"]["agent"]["key_file"] = ""
    assert debrief.run_debrief(env["cfg"], run) == debrief.EXIT_REFUSED
    assert procs == [] and env["calls"] == []


@pytest.mark.parametrize(("key", "bad"), [("debrief_budget_usd", 0), ("debrief_max_minutes", "ten"),
                                          ("debrief_max_tokens", 3.5)])
def test_bad_debrief_limits_refuse_with_exit_2_and_the_reason(env: dict[str, Any], key: str, bad: object,
                                                              capsys: pytest.CaptureFixture) -> None:
    """Item 8: a bad debrief limit is a refusal naming the key (exit 2), not an uncaught ValueError."""
    env["cfg"]["agent"][key] = bad
    assert debrief.run_debrief(env["cfg"], env["run"]) == debrief.EXIT_REFUSED == 2
    err = capsys.readouterr().err
    assert err.startswith("debrief refused: ") and f"agent.{key}" in err
    assert env["calls"] == []


@pytest.mark.parametrize("key", ["debrief_budget_usd", "debrief_max_minutes"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "0.5", None, -1, 10**7])
def test_debrief_limits_use_the_run_limits_type_rules(key: str, bad: object) -> None:
    with pytest.raises(ValueError, match=key):                  # X11
        debrief.debrief_limits({key: bad})


def test_debrief_gateway_gets_a_configured_token_cap(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    env["cfg"]["agent"]["debrief_max_tokens"] = 1234
    _popen(monkeypatch)
    assert debrief.run_debrief(env["cfg"], env["run"]) == 0
    gw = next(c for c in env["calls"] if c[:3] == ["docker", "run", "-d"])
    assert gw[gw.index("--max-tokens-total") + 1] == "1234"     # V17


def test_sigterm_during_a_debrief_still_removes_container_and_gateway(env: dict[str, Any],
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    before = signal.getsignal(signal.SIGTERM)

    class Terminated(FakePopen):
        def wait(self, timeout: float | None = None) -> int:
            if timeout is None:                                 # a wait after the handler: never block
                return -15
            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler) and handler is not before, "no SIGTERM handler during the debrief"
            handler(signal.SIGTERM, None)                       # M4: launchd unload / logout / kill
            raise AssertionError("the SIGTERM handler did not end the debrief")

    monkeypatch.setattr(debrief.subprocess, "Popen", lambda argv, **kw: Terminated(argv))
    with pytest.raises(SystemExit) as exc:
        debrief.run_debrief(env["cfg"], env["run"])
    assert exc.value.code == agent_run.SIGTERM_EXIT
    assert env["calls"][-2:] == [["docker", "rm", "-f", debrief.DEBRIEF_CONTAINER],
                                 ["docker", "rm", "-f", debrief.DEBRIEF_GATEWAY]]
    assert signal.getsignal(signal.SIGTERM) is before
    status = json.loads(next((env["run"] / "debrief").glob("*/status.json")).read_text())
    assert status["terminated"] is True and status["exit"] == agent_run.SIGTERM_EXIT
    with open(agent_run.run_lock_path(), "a+") as probe:       # the lock is released
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


# ---------------------------------------------------------------- goose facts

def test_verify_goose_sessions_cites_sources_and_tracks_version() -> None:
    new = debrief.verify_goose_sessions("1.12.0")
    assert new["storage"]["kind"] == "sqlite"
    assert debrief.verify_goose_sessions("v1.9.3")["storage"]["kind"] == "jsonl"
    assert "unverified" in debrief.verify_goose_sessions(None)["storage"]["kind"]
    assert new["sources"] and all(u.startswith("https://") for u in new["sources"].values())
    for key in ("storage", "resume", "run_name_resumable", "extensions_on_resume"):
        assert new[key]["sources"] and set(new[key]["sources"]) <= set(new["sources"])
    assert new["resume"]["interactive"][:3] == ["goose", "session", "--resume"]
    assert new["container_path"]["provisional_constant"] == agent_run.GOOSE_SESSIONS_IN_CONTAINER
    assert "unverified" in json.dumps(new)                   # open questions are marked, not guessed


@pytest.mark.parametrize("bad", [True, 3.9, "5", 0, -1, None, 10**9])
def test_debrief_max_tokens_follows_the_run_limit_rules(bad: object) -> None:
    """Bools, floats, strings, non-positive and over-ceiling values are refused, as in run_limits."""
    with pytest.raises(ValueError, match="debrief_max_tokens"):
        debrief.debrief_max_tokens({"debrief_max_tokens": bad})
    assert debrief.debrief_max_tokens({}) == debrief.DEFAULT_DEBRIEF_MAX_TOKENS
    assert debrief.debrief_max_tokens({"debrief_max_tokens": 5}) == 5
