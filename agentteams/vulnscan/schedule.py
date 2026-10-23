"""Install, remove and inspect the daily ``vulnscan daily`` job.

| Platform | Mechanism | Missed runs |
|---|---|---|
| macOS | launchd user agent ``~/Library/LaunchAgents/io.agentteams.vulnscan.plist`` | Run on wake |
| Linux with systemd | User timer ``~/.config/systemd/user/agentteams-vulnscan.{service,timer}`` | ``Persistent=true`` catches up |
| Other | A crontab line tagged ``# agentteams-vulnscan`` | — |

Every call is a dry run unless ``apply=True``. A dry run returns exactly the files and
commands it would write or run. The job runs this same interpreter
(``sys.executable -m agentteams.vulnscan daily``) with the PATH from install time, so it
finds npm, git and ssh. HOME, the XDG directories and every ``AGENTTEAMS_*`` override are
baked in as well, so the job resolves the same config and state as the CLI that installed it. SSH keys that need a passphrase must be available to the agent
(for example a macOS keychain-backed ssh-agent). Otherwise those hosts show up as
unreachable (exit 3), never as clean.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from agentteams.vulnscan import paths

LABEL = "io.agentteams.vulnscan"
UNIT = "agentteams-vulnscan"
CRON_TAG = "# agentteams-vulnscan"


@dataclass
class Plan:
    """What an install/remove would do (or did)."""

    files: dict[str, str] = field(default_factory=dict)       # path -> content ("" = delete)
    commands: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)            # failures while applying


def _program() -> list[str]:
    return [sys.executable, "-m", "agentteams.vulnscan", "daily"]


def job_environment() -> dict[str, str]:
    """Return the environment baked into the job, so it resolves the same config and state dirs.

    Returns:
        PATH, HOME, any XDG_CONFIG_HOME / XDG_STATE_HOME and every AGENTTEAMS_* variable set now.
    """
    keep = {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "SSH_AUTH_SOCK"}
    env = {k: v for k, v in os.environ.items() if k in keep or k.startswith("AGENTTEAMS_")}
    env.setdefault("PATH", "/usr/bin:/bin")
    return dict(sorted(env.items()))


def _systemd_quote(arg: str) -> str:
    """Quote one word for a systemd unit line (``%`` specifiers escaped, C-style escapes)."""
    arg = arg.replace("%", "%%")
    if not any(c in arg for c in " \t\"'\\"):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def launchd_plist(hour: int, minute: int) -> str:
    """Return the launchd agent definition.

    Args:
        hour: Local hour (0-23).
        minute: Minute (0-59).

    Returns:
        Plist XML text.
    """
    log = paths.state_dir() / "scheduler.log"
    args = "".join(f"\n    <string>{_xml(a)}</string>" for a in _program())
    env = "".join(f"\n    <key>{_xml(k)}</key><string>{_xml(v)}</string>" for k, v in job_environment().items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>{args}
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  <key>EnvironmentVariables</key>
  <dict>{env}
  </dict>
  <key>StandardOutPath</key><string>{_xml(str(log))}</string>
  <key>StandardErrorPath</key><string>{_xml(str(log))}</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
"""


def systemd_units(hour: int, minute: int) -> tuple[str, str]:
    """Return (service, timer) unit texts for a systemd user timer.

    Args:
        hour: Local hour.
        minute: Minute.

    Returns:
        The two unit files' contents.
    """
    cmd = " ".join(_systemd_quote(a) for a in _program())
    envs = "".join(f"Environment={_systemd_quote(f'{k}={v}')}\n" for k, v in job_environment().items())
    service = (f"[Unit]\nDescription=agentteams daily vulnerability scan\n\n[Service]\nType=oneshot\n"
               f"{envs}ExecStart={cmd}\n")
    timer = (f"[Unit]\nDescription=Run agentteams vulnscan daily\n\n[Timer]\n"
             f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\nPersistent=true\nRandomizedDelaySec=300\n\n"
             f"[Install]\nWantedBy=timers.target\n")
    return service, timer


def cron_line(hour: int, minute: int) -> str:
    """Return the crontab entry.

    Args:
        hour: Hour.
        minute: Minute.

    Returns:
        One crontab line ending in the tag comment.
    """
    log = paths.state_dir() / "scheduler.log"
    env = " ".join(f"{k}={shlex.quote(v)}" for k, v in job_environment().items())
    cmd = shlex.join(_program()).replace("%", "\\%")
    return f"{minute} {hour} * * * env {env} {cmd} >> {shlex.quote(str(log))} 2>&1 {CRON_TAG}"


def _mechanism() -> str:
    if platform.system() == "Darwin":
        return "launchd"
    if shutil.which("systemctl") and Path("/run/systemd/system").exists():
        return "systemd"
    return "cron"


def _run(cmd: list[str], apply: bool, plan: Plan, *, may_fail: bool = False) -> subprocess.CompletedProcess | None:
    plan.commands.append(cmd)
    if not apply:
        return None
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode and not may_fail:
        plan.errors.append(f"{' '.join(cmd)} exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:300]}")
    return proc


def _read_crontab(plan: Plan) -> str | None:
    """Current crontab, "" when the user has none, None when it could not be read (never overwrite then)."""
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if proc.returncode == 0:
        return proc.stdout
    if "no crontab" in (proc.stderr or "").lower():
        return ""
    plan.errors.append(f"crontab -l failed ({proc.returncode}): {proc.stderr.strip()[:200]} — not touching the crontab")
    return None


def install(hour: int = 7, minute: int = 30, *, apply: bool = False) -> Plan:
    """Install the daily job for this user.

    Args:
        hour: Local hour to run.
        minute: Minute to run.
        apply: Actually write and activate (otherwise a dry run).

    Returns:
        The :class:`Plan` (performed when ``apply``).
    """
    plan = Plan()
    if apply:
        paths.state_dir().mkdir(parents=True, exist_ok=True)
    mech = _mechanism()
    if mech == "launchd":
        target = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        plan.files[str(target)] = launchd_plist(hour, minute)
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(plan.files[str(target)], encoding="utf-8")
        uid = str(os.getuid())
        _run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], apply, plan, may_fail=True)   # absent on first install
        _run(["launchctl", "bootstrap", f"gui/{uid}", str(target)], apply, plan)
    elif mech == "systemd":
        unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
        service, timer = systemd_units(hour, minute)
        plan.files[str(unit_dir / f"{UNIT}.service")] = service
        plan.files[str(unit_dir / f"{UNIT}.timer")] = timer
        if apply:
            unit_dir.mkdir(parents=True, exist_ok=True)
            for p, text in plan.files.items():
                Path(p).write_text(text, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"], apply, plan)
        _run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"], apply, plan)
        plan.notes.append("for runs while logged out: `loginctl enable-linger $USER` (needs admin once)")
    else:
        line = cron_line(hour, minute)
        plan.files["crontab"] = line
        if apply:
            cur = _read_crontab(plan)
            if cur is not None:
                kept = [ln for ln in cur.splitlines() if CRON_TAG not in ln]
                proc = subprocess.run(["crontab", "-"], input="\n".join([*kept, line]) + "\n", text=True,
                                      capture_output=True, check=False)
                if proc.returncode:
                    plan.errors.append(f"crontab - failed: {proc.stderr.strip()[:200]}")
        plan.commands.append(["crontab", "-"])
    return plan


def remove(*, apply: bool = False) -> Plan:
    """Remove the daily job.

    Args:
        apply: Actually remove (otherwise a dry run).

    Returns:
        The :class:`Plan`.
    """
    plan = Plan()
    mech = _mechanism()
    if mech == "launchd":
        target = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], apply, plan, may_fail=True)
        plan.files[str(target)] = ""
        if apply and target.exists():
            target.unlink()
    elif mech == "systemd":
        _run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"], apply, plan)
        unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
        for name in (f"{UNIT}.service", f"{UNIT}.timer"):
            plan.files[str(unit_dir / name)] = ""
            if apply and (unit_dir / name).exists():
                (unit_dir / name).unlink()
    else:
        plan.commands.append(["crontab", "-"])
        if apply:
            cur = _read_crontab(plan)
            if cur is not None:
                kept = [ln for ln in cur.splitlines() if CRON_TAG not in ln]
                subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True, check=False)
    return plan


def status() -> str:
    """Describe whether the job is installed and when it last ran.

    Returns:
        Human-readable status.
    """
    mech = _mechanism()
    if mech == "launchd":
        target = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        installed = target.exists()
    elif mech == "systemd":
        installed = subprocess.run(["systemctl", "--user", "is-enabled", f"{UNIT}.timer"],
                                   capture_output=True, text=True, check=False).returncode == 0
    else:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False).stdout
        installed = CRON_TAG in cur
    hb = paths.state_dir() / "heartbeat.json"
    last = hb.read_text(encoding="utf-8").strip() if hb.exists() else "(no run recorded)"
    return f"mechanism: {mech}\ninstalled: {installed}\nconfig: {paths.config_file()}\nstate: {paths.state_dir()}\nlast run: {last}"
