"""agentteams.vulnscan.schedule: launchd / systemd / cron install, remove, status and the baked job environment.

No real scheduler is touched: ``_mechanism`` is forced per test and ``launchctl`` / ``systemctl`` /
``crontab`` are fake scripts on a temporary PATH that log their arguments.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import sys
from pathlib import Path

import pytest

from agentteams.vulnscan import paths, schedule

FAKE_LOG_TOOL = '#!/bin/sh\necho "$(basename "$0") $*" >> "$FAKE_LOG"\n'
FAKE_SYSTEMCTL = FAKE_LOG_TOOL + '[ "$2" = is-enabled ] && exit "${FAKE_ENABLED_RC:-1}"\nexit 0\n'
FAKE_CRONTAB = """#!/bin/sh
echo "crontab $*" >> "$FAKE_LOG"
case "$1" in
  -l) if [ -f "$FAKE_CRONTAB" ]; then cat "$FAKE_CRONTAB"; else echo "crontab: no crontab for me" >&2; exit 1; fi ;;
  -) cat > "$FAKE_CRONTAB" ;;
esac
"""
SOCK = "/private/tmp/com.apple.launchd.AbCdEf123/Listeners"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A fake home, vulnscan dirs, and fake scheduler tools first on PATH."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("launchctl", FAKE_LOG_TOOL), ("systemctl", FAKE_SYSTEMCTL), ("crontab", FAKE_CRONTAB)):
        tool = bin_dir / name
        tool.write_text(text)
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "tools.log"))
    monkeypatch.setenv("FAKE_CRONTAB", str(tmp_path / "crontab.txt"))
    return {"home": home, "log": tmp_path / "tools.log", "crontab": tmp_path / "crontab.txt", "tmp": tmp_path}


def _use(monkeypatch: pytest.MonkeyPatch, mech: str) -> None:
    monkeypatch.setattr(schedule, "_mechanism", lambda: mech)


def _log(env: dict[str, Path]) -> list[str]:
    return env["log"].read_text().splitlines() if env["log"].exists() else []


# ---------------------------------------------------------------- D9: no per-login values baked in

def test_job_environment_does_not_bake_the_per_login_ssh_agent_socket(env: dict[str, Path],
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSH_AUTH_SOCK", SOCK)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/501")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/501/bus")
    jenv = schedule.job_environment()
    assert set(jenv) <= {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"} | {k for k in jenv
                                                                                 if k.startswith("AGENTTEAMS_")}
    service, _ = schedule.systemd_units(7, 30)
    for text in (schedule.launchd_plist(7, 30), service, schedule.cron_line(7, 30)):
        assert "SSH_AUTH_SOCK" not in text and SOCK not in text and "/run/user/501" not in text


# ---------------------------------------------------------------- I12: the environment round-trips

def test_cron_job_environment_resolves_the_same_dirs(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(env["tmp"] / "xdg state"))
    monkeypatch.delenv("AGENTTEAMS_VULNSCAN_STATE_DIR")
    want = (paths.config_dir(), paths.state_dir(), paths.server_kit_dir())
    words = shlex.split(schedule.cron_line(7, 30).split(schedule.CRON_TAG)[0])
    assert words[5] == "env"
    job_env = dict(w.split("=", 1) for w in words[6:words.index(sys.executable)])
    assert job_env == schedule.job_environment()
    for key in list(os.environ):                                  # the job starts with only what was baked
        monkeypatch.delenv(key)
    for key, value in job_env.items():
        monkeypatch.setenv(key, value)
    assert (paths.config_dir(), paths.state_dir(), paths.server_kit_dir()) == want


def test_launchd_and_systemd_environment_match_the_job_environment(env: dict[str, Path]) -> None:
    plist = schedule.launchd_plist(7, 30)
    block = plist.split("<key>EnvironmentVariables</key>")[1].split("</dict>")[0]
    assert dict(re.findall(r"<key>([^<]+)</key><string>([^<]*)</string>", block)) == schedule.job_environment()
    service, _ = schedule.systemd_units(7, 30)
    lines = [ln[len("Environment="):] for ln in service.splitlines() if ln.startswith("Environment=")]
    assert [shlex.split(ln)[0] for ln in lines] == [f"{k}={v}" for k, v in schedule.job_environment().items()]


# ---------------------------------------------------------------- I12: dry runs per mechanism

@pytest.mark.parametrize("mech", ["launchd", "systemd", "cron"])
def test_dry_run_install_and_remove_touch_nothing(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
                                                  mech: str) -> None:
    _use(monkeypatch, mech)
    plan = schedule.install(6, 5, apply=False)
    removal = schedule.remove(apply=False)
    assert plan.commands and removal.commands and not plan.errors and not removal.errors
    assert _log(env) == [] and not any(Path(p).exists() for p in plan.files if p != "crontab")
    assert not (env["tmp"] / "state").exists()
    uid = os.getuid()
    if mech == "launchd":
        target = str(env["home"] / "Library" / "LaunchAgents" / f"{schedule.LABEL}.plist")
        assert list(plan.files) == [target] and "<integer>6</integer>" in plan.files[target]
        assert plan.commands == [["launchctl", "bootout", f"gui/{uid}/{schedule.LABEL}"],
                                 ["launchctl", "bootstrap", f"gui/{uid}", target]]
        assert removal.files == {target: ""}
    elif mech == "systemd":
        unit_dir = env["home"] / ".config" / "systemd" / "user"
        assert set(plan.files) == {str(unit_dir / f"{schedule.UNIT}.service"), str(unit_dir / f"{schedule.UNIT}.timer")}
        assert plan.commands == [["systemctl", "--user", "daemon-reload"],
                                 ["systemctl", "--user", "enable", "--now", f"{schedule.UNIT}.timer"]]
        assert removal.commands == [["systemctl", "--user", "disable", "--now", f"{schedule.UNIT}.timer"],
                                    ["systemctl", "--user", "daemon-reload"]]
        assert set(removal.files) == set(plan.files) and not any(removal.files.values())
    else:
        assert plan.files["crontab"].startswith("5 6 * * * ") and plan.commands == [["crontab", "-"]]
        assert removal.commands == [["crontab", "-"]] and not env["crontab"].exists()


# ---------------------------------------------------------------- I12: apply with fake tools

def test_launchd_apply_install_status_remove(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "launchd")
    uid = os.getuid()
    target = env["home"] / "Library" / "LaunchAgents" / f"{schedule.LABEL}.plist"
    assert "installed: False" in schedule.status() and "(no run recorded)" in schedule.status()
    plan = schedule.install(7, 30, apply=True)
    assert not plan.errors and target.read_text() == plan.files[str(target)]
    assert _log(env) == [f"launchctl bootout gui/{uid}/{schedule.LABEL}", f"launchctl bootstrap gui/{uid} {target}"]
    assert "mechanism: launchd\ninstalled: True" in schedule.status()
    env["log"].unlink()
    removal = schedule.remove(apply=True)
    assert not removal.errors and not target.exists() and _log(env) == [f"launchctl bootout gui/{uid}/{schedule.LABEL}"]
    assert "installed: False" in schedule.status()


def test_launchd_bootstrap_failure_is_reported(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "launchd")
    (env["tmp"] / "bin" / "launchctl").write_text(FAKE_LOG_TOOL + 'echo "Bootstrap failed: 5" >&2\nexit 5\n')
    plan = schedule.install(7, 30, apply=True)
    assert len(plan.errors) == 1 and "bootstrap" in plan.errors[0] and "exited 5" in plan.errors[0]  # bootout may fail


def test_systemd_apply_install_status_remove(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "systemd")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(env["tmp"] / "xdgcfg"))
    unit_dir = env["tmp"] / "xdgcfg" / "systemd" / "user"
    plan = schedule.install(7, 30, apply=True)
    assert not plan.errors and (unit_dir / f"{schedule.UNIT}.timer").read_text() == plan.files[
        str(unit_dir / f"{schedule.UNIT}.timer")]
    assert "ExecStart=" in (unit_dir / f"{schedule.UNIT}.service").read_text()
    assert _log(env) == ["systemctl --user daemon-reload", f"systemctl --user enable --now {schedule.UNIT}.timer"]
    assert "installed: False" in schedule.status()                     # is-enabled exits 1 by default
    monkeypatch.setenv("FAKE_ENABLED_RC", "0")
    assert "mechanism: systemd\ninstalled: True" in schedule.status()
    removal = schedule.remove(apply=True)
    assert not removal.errors and not list(unit_dir.iterdir())
    assert _log(env)[-3:] == [f"systemctl --user is-enabled {schedule.UNIT}.timer",
                              f"systemctl --user disable --now {schedule.UNIT}.timer",
                              "systemctl --user daemon-reload"]


# ---------------------------------------------------------------- E10: crontab handling

def test_cron_install_with_no_crontab_creates_one(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "cron")
    assert "installed: False" in schedule.status()
    plan = schedule.install(7, 30, apply=True)
    assert not plan.errors and env["crontab"].read_text() == plan.files["crontab"] + "\n"
    assert "mechanism: cron\ninstalled: True" in schedule.status()


def test_cron_install_and_remove_keep_other_entries(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "cron")
    others = ["MAILTO=me@example.org", "0 1 * * * /usr/local/bin/backup", "# a comment"]
    env["crontab"].write_text("\n".join([*others, f"9 9 * * * old-job {schedule.CRON_TAG}"]) + "\n")
    plan = schedule.install(7, 30, apply=True)
    lines = env["crontab"].read_text().splitlines()
    assert not plan.errors and lines == [*others, plan.files["crontab"]]       # the old tagged line was replaced
    removal = schedule.remove(apply=True)
    assert not removal.errors and env["crontab"].read_text().splitlines() == others


def test_cron_remove_without_a_job_leaves_the_crontab_alone(env: dict[str, Path],
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "cron")
    removal = schedule.remove(apply=True)                               # the user has no crontab at all
    assert not removal.errors and not env["crontab"].exists()           # none is created
    env["crontab"].write_text("0 1 * * * /usr/local/bin/backup\n")
    schedule.remove(apply=True)
    assert env["crontab"].read_text() == "0 1 * * * /usr/local/bin/backup\n"
    assert _log(env) == ["crontab -l", "crontab -l"]                    # read only, never rewritten


def test_cron_unreadable_crontab_is_never_overwritten(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "cron")
    (env["tmp"] / "bin" / "crontab").write_text(FAKE_LOG_TOOL + 'echo "crontab: permission denied" >&2\nexit 1\n')
    for plan in (schedule.install(7, 30, apply=True), schedule.remove(apply=True)):
        assert plan.errors and "not touching the crontab" in plan.errors[0]
    assert _log(env) == ["crontab -l", "crontab -l"]


def test_cron_remove_reports_a_failed_write(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "cron")
    env["crontab"].write_text(f"9 9 * * * old-job {schedule.CRON_TAG}\n")
    (env["tmp"] / "bin" / "crontab").write_text(FAKE_CRONTAB.replace('-) cat > "$FAKE_CRONTAB" ;;',
                                                                      '-) echo "crontab: disk full" >&2; exit 1 ;;'))
    removal = schedule.remove(apply=True)
    assert removal.errors and "disk full" in removal.errors[0]


# ---------------------------------------------------------------- X10: remove when the units are not installed

FAKE_SYSTEMCTL_NO_UNIT = FAKE_LOG_TOOL + """[ "$2" = disable ] && {
  echo "Failed to disable unit: Unit file agentteams-vulnscan.timer does not exist." >&2; exit 1; }
exit 0
"""


def test_systemd_remove_tolerates_a_failed_disable_when_nothing_is_installed(env: dict[str, Path],
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "systemd")
    (env["tmp"] / "bin" / "systemctl").write_text(FAKE_SYSTEMCTL_NO_UNIT)
    removal = schedule.remove(apply=True)
    assert not removal.errors
    assert _log(env) == [f"systemctl --user disable --now {schedule.UNIT}.timer", "systemctl --user daemon-reload"]


def test_systemd_remove_still_reports_a_failed_disable_of_installed_units(env: dict[str, Path],
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "systemd")
    schedule.install(7, 30, apply=True)
    (env["tmp"] / "bin" / "systemctl").write_text(FAKE_SYSTEMCTL_NO_UNIT)
    removal = schedule.remove(apply=True)
    assert len(removal.errors) == 1 and "disable" in removal.errors[0] and "exited 1" in removal.errors[0]
    unit_dir = env["home"] / ".config" / "systemd" / "user"
    assert not list(unit_dir.iterdir())                                 # the files are removed regardless


# ---------------------------------------------------------------- X9: % in the cron line

def test_cron_line_escapes_percent_in_environment_values_and_the_log_path(env: dict[str, Path],
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(env["tmp"] / "st%ate 100%"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_LOGS", "a%b%%c")
    line = schedule.cron_line(7, 30)
    head, tag = line.split(schedule.CRON_TAG)
    assert tag == "" and re.search(r"(?<!\\)%", head) is None           # cron turns a bare % into a newline
    words = shlex.split(head.replace("\\%", "%"))                       # what the shell gets after cron
    job_env = dict(w.split("=", 1) for w in words[6:words.index(sys.executable)])
    assert job_env == schedule.job_environment() and job_env["AGENTTEAMS_SERVER_KIT_LOGS"] == "a%b%%c"
    assert words[-3:] == [">>", str(env["tmp"] / "st%ate 100%" / "scheduler.log"), "2>&1"]


# ---------------------------------------------------------------- G18: hour / minute range

BAD_TIMES = [(24, 0), (-1, 0), (7, 60), (7, -1), (True, 0), (7, False), (7.5, 0), ("7", 30), (None, 30)]


@pytest.mark.parametrize("mech", ["launchd", "systemd", "cron"])
@pytest.mark.parametrize(("hour", "minute"), BAD_TIMES)
def test_install_refuses_an_out_of_range_or_non_integer_time(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
                                                            mech: str, hour: object, minute: object) -> None:
    _use(monkeypatch, mech)
    plan = schedule.install(hour, minute, apply=True)  # type: ignore[arg-type]
    assert plan.errors and re.search(r"hour|minute", plan.errors[0])
    assert not plan.files and not plan.commands and _log(env) == [] and not env["crontab"].exists()
    assert not (env["home"] / "Library").exists() and not (env["home"] / ".config").exists()


@pytest.mark.parametrize(("hour", "minute"), BAD_TIMES)
def test_job_definitions_refuse_a_bad_time(env: dict[str, Path], hour: object, minute: object) -> None:
    for build in (schedule.launchd_plist, schedule.systemd_units, schedule.cron_line):
        with pytest.raises(ValueError, match="hour|minute"):
            build(hour, minute)  # type: ignore[arg-type]


def test_cli_schedule_install_with_a_bad_hour_writes_nothing(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    from agentteams.vulnscan.__main__ import main

    _use(monkeypatch, "cron")
    assert main(["schedule", "install", "--hour", "25", "--apply"]) == 1
    assert "hour" in capsys.readouterr().err and _log(env) == [] and not env["crontab"].exists()


@pytest.mark.parametrize(("hour", "minute"), [(0, 0), (23, 59)])
def test_install_accepts_the_range_bounds(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch,
                                          hour: int, minute: int) -> None:
    _use(monkeypatch, "cron")
    plan = schedule.install(hour, minute, apply=False)
    assert not plan.errors and plan.files["crontab"].startswith(f"{minute} {hour} * * * ")


# ---------------------------------------------------------------- V26: mechanism selection

@pytest.mark.parametrize(("system", "systemctl", "runtime", "want"), [
    ("Darwin", "/usr/bin/systemctl", True, "launchd"),       # macOS wins even with a systemctl on PATH
    ("Linux", "/usr/bin/systemctl", True, "systemd"),
    ("Linux", None, True, "cron"),                           # no systemctl
    ("Linux", "/usr/bin/systemctl", False, "cron"),          # systemctl installed but systemd not running
    ("FreeBSD", None, False, "cron"),
])
def test_mechanism_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system: str, systemctl: str | None,
                             runtime: bool, want: str) -> None:
    run_dir = tmp_path / "run-systemd-system"
    if runtime:
        run_dir.mkdir()
    asked: list[str] = []
    monkeypatch.setattr(schedule.platform, "system", lambda: system)
    monkeypatch.setattr(schedule.shutil, "which", lambda name: asked.append(name) or systemctl)
    monkeypatch.setattr(schedule, "SYSTEMD_RUNTIME_DIR", run_dir)
    assert schedule._mechanism() == want
    assert asked == ([] if system == "Darwin" else ["systemctl"])


def test_the_systemd_runtime_check_is_the_documented_directory() -> None:
    assert schedule.SYSTEMD_RUNTIME_DIR == Path("/run/systemd/system")


# ---------------------------------------------------------------- V27: the plist parses

def test_launchd_plist_parses_with_plistlib(env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import plistlib

    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(env["tmp"] / "state & <logs> 100%"))
    monkeypatch.setenv("AGENTTEAMS_SERVER_KIT_LOGS", "a&b<c>d%e\"f'g")
    data = plistlib.loads(schedule.launchd_plist(6, 5).encode("utf-8"))
    log = str(env["tmp"] / "state & <logs> 100%" / "scheduler.log")
    assert data["Label"] == schedule.LABEL
    assert data["ProgramArguments"] == [sys.executable, "-P", "-m", "agentteams.vulnscan", "daily"]
    assert data["StartCalendarInterval"] == {"Hour": 6, "Minute": 5}
    assert data["StandardOutPath"] == data["StandardErrorPath"] == log
    assert data["EnvironmentVariables"] == schedule.job_environment()
    assert data["EnvironmentVariables"]["AGENTTEAMS_SERVER_KIT_LOGS"] == "a&b<c>d%e\"f'g"
    assert data["ProcessType"] == "Background"


def test_job_environment_bakes_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job file is plain text: only the location variables are baked, never other AGENTTEAMS_* values."""
    monkeypatch.setenv("AGENTTEAMS_BRAVE_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setenv("AGENTTEAMS_DECISION_ED25519_KEYFILE", "/x/op.pem")
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", "/srv/state")
    env = schedule.job_environment()
    assert env["AGENTTEAMS_VULNSCAN_STATE_DIR"] == "/srv/state"
    assert "AGENTTEAMS_BRAVE_API_KEY" not in env and "AGENTTEAMS_DECISION_ED25519_KEYFILE" not in env
    assert set(env) <= schedule.JOB_ENV_VARS
    for text in (schedule.launchd_plist(6, 0), schedule.cron_line(6, 0)):
        assert "sk-test-not-a-real-key" not in text and "op.pem" not in text


@pytest.mark.parametrize("value", ["/srv/state\nExecStartPre=/bin/evil", "/srv/a\rb", "/srv/\x1b[0m"])
def test_r8_5_control_characters_are_never_baked(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """A newline in a baked value would add a line to the unit or crontab: refuse, write nothing."""
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", value)
    with pytest.raises(ValueError, match="control characters"):
        schedule.job_environment()
    plan = schedule.install(hour=6, minute=0, apply=False)
    assert plan.errors and "control characters" in plan.errors[0] and not plan.files


def _tool(d: Path, name: str) -> None:
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("#!/bin/sh\n")
    (d / name).chmod(0o755)


def test_job_path_never_contains_a_directory_inside_a_repository(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """A project virtualenv's bin early in PATH (first live install) must not reach the unsandboxed job;
    a package-manager prefix outside $HOME that is itself a git work tree (Homebrew) stays."""
    home = tmp_path / "home"
    monkeypatch.setattr(schedule.Path, "home", classmethod(lambda cls: home))
    repo = home / "repo"
    (repo / ".git").mkdir(parents=True)
    venv_bin = repo / ".venv-ci" / "bin"
    _tool(venv_bin, "git")                        # an agent working in that repo could plant this
    system = tmp_path / "homebrew" / "bin"          # outside $HOME, inside a work tree like /opt/homebrew
    (tmp_path / "homebrew" / ".git").mkdir(parents=True)
    for t in ("git", "gh", "docker"):
        _tool(system, t)
    only_in_repo = repo / "tools"
    _tool(only_in_repo, "npm")
    path = ":".join([str(venv_bin), "~/.dotnet/tools", "relative/bin", str(only_in_repo), str(system)])
    job, skipped = schedule.job_path(path)
    parts = job.split(":")
    assert str(venv_bin) not in parts and str(only_in_repo) not in parts
    assert "~/.dotnet/tools" not in parts and "relative/bin" not in parts
    assert parts[0] == str(system) and parts[-4:] == list(schedule.SYSTEM_PATH)
    assert skipped == [f"npm ({only_in_repo})"]


def test_the_job_runs_python_with_safe_path() -> None:
    assert schedule._program()[1] == "-P"
    assert "<string>-P</string>" in schedule.launchd_plist(9, 0)

