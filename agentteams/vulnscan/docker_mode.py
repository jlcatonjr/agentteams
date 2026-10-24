"""Which Docker the agent containers run on, and so which user they run as (R4, rc.5).

A bind mount on Docker Desktop maps file ownership; on Linux Docker Engine it keeps the operator's uid and
modes, so the image's own user (uid 10001) can read neither the 0600 key files nor the DLP context, and cannot
write the spool. The container user therefore follows the Docker mode that ``docker info`` reports:

| Docker mode | Container user |
|---|---|
| Docker Desktop (macOS or Linux) | the image's uid 10001, unchanged (``user`` None) |
| Rootful Engine (Linux host) | the operator's ``uid:gid`` (refused when the operator is root) |
| Rootless Engine (Linux host) | ``0:0``, which the user namespace maps to the operator |
| Rootful with ``userns-remap`` | refused: the files would need subordinate-uid ownership |
| anything else (an unreadable answer, another VM on macOS) | the image's uid 10001, unchanged |

The fallback is the old behaviour: uid 10001 never has more access than the operator meant to give, so a
misread mode fails closed (the gateway cannot read its files and refuses to start). Only positive evidence
selects the operator's uid or ``0:0``, and the self-test's ``non_root`` check
(:mod:`agentteams.vulnscan.selftest_probe`) proves, under rootless, that uid 0 inside is not root outside.

Detection (:func:`detect`) runs ``docker info`` once, when a run or a self-test starts; the argv builders and
:func:`agentteams.vulnscan.agent_run.flags_sha256` take the user as a value and never run a subprocess. The
self-test records the user it ran as; a run uses the user its own detection gives, and refuses when that differs
from the recorded one (:func:`recorded_user`), so a passing self-test never vouches for another mode.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DESKTOP = "desktop"
ROOTFUL = "rootful"
ROOTLESS = "rootless"
USERNS_REMAP = "userns-remap"
UNKNOWN = "unknown"
#: The container user under rootless Docker: root inside a user namespace that maps it to the operator.
ROOTLESS_USER = "0:0"
INFO_ARGV = ["docker", "info", "--format", "{{json .}}"]
USERNS_PROBLEM = ("Docker runs with userns-remap: container uids map to subordinate uids, which cannot read your "
                  "key files or write the run directories. Agent mode is not supported there; use rootless Docker, "
                  "or a daemon without userns-remap")
ROOT_PROBLEM = ("vulnscan is running as root, or with root as its primary group, on a rootful Docker Engine: the "
                "agent containers would run with root's uid or group. Run vulnscan from your own account")
#: A container user: ``uid:gid``, both decimal (what ``--user`` and the tmpfs ``uid=``/``gid=`` options take).
USER_RE = re.compile(r"[0-9]{1,10}:[0-9]{1,10}")

Runner = Callable[[list[str]], subprocess.CompletedProcess]


@dataclass(frozen=True)
class DockerMode:
    """One Docker mode and the container user it implies.

    Attributes:
        name: :data:`DESKTOP`, :data:`ROOTFUL`, :data:`ROOTLESS`, :data:`USERNS_REMAP` or :data:`UNKNOWN`.
        user: ``uid:gid`` for ``--user``, or None for the image's own user (uid 10001).
        problem: Why agent mode is refused in this mode, or "".
    """

    name: str
    user: str | None = None
    problem: str = ""


def _security_names(info: dict) -> set[str]:
    """Return the ``name=`` of each ``SecurityOptions`` entry (``name=rootless``, ``name=userns``, ...)."""
    names = set()
    for opt in info.get("SecurityOptions") or ():
        for part in str(opt).split(","):
            if part.startswith("name="):
                names.add(part[5:])
    return names


def classify(info: object, *, uid: int, gid: int, platform: str | None = None) -> DockerMode:
    """Return the Docker mode that a parsed ``docker info`` describes (pure).

    Args:
        info: The parsed ``docker info --format '{{json .}}'``.
        uid: The operator's uid.
        gid: The operator's primary gid.
        platform: The host's ``sys.platform`` (default: this one); an Engine mode is recognised on a Linux host only.

    Returns:
        The mode; :data:`UNKNOWN` (the image's user) without positive evidence of another.
    """
    if not isinstance(info, dict) or not info.get("ServerVersion"):
        return DockerMode(UNKNOWN)
    if "Docker Desktop" in str(info.get("OperatingSystem") or ""):
        return DockerMode(DESKTOP)
    if not (platform or sys.platform).startswith("linux") or info.get("OSType") != "linux":
        return DockerMode(UNKNOWN)
    names = _security_names(info)
    if "rootless" in names:
        return DockerMode(ROOTLESS, ROOTLESS_USER)
    if "userns" in names:
        return DockerMode(USERNS_REMAP, problem=USERNS_PROBLEM)
    if uid == 0 or gid == 0:   # root's uid, or root's group on every file the containers create
        return DockerMode(ROOTFUL, problem=ROOT_PROBLEM)
    return DockerMode(ROOTFUL, f"{uid}:{gid}")


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)


def detect(runner: Runner | None = None) -> DockerMode:
    """Ask ``docker info`` which mode the daemon runs in (the one subprocess of this module).

    Args:
        runner: Runs an argv and returns the completed process (default: ``subprocess.run``, 60 s).

    Returns:
        :func:`classify` of the answer; :data:`UNKNOWN` when docker is absent, hung, fails or answers
        something unreadable.
    """
    try:
        proc = (runner or _run)(list(INFO_ARGV))
    except (OSError, subprocess.SubprocessError):   # not installed, or a hung client: the image's user
        return DockerMode(UNKNOWN)
    if not isinstance(proc, subprocess.CompletedProcess) or proc.returncode:
        return DockerMode(UNKNOWN)
    try:
        info = json.loads(proc.stdout or "")
    except (TypeError, ValueError):   # bytes or a non-string stdout, or not JSON
        return DockerMode(UNKNOWN)
    return classify(info, uid=os.getuid(), gid=os.getgid())


def recorded_user(record: Path, default: str) -> str:
    """Return the container user a self-test record says the self-test ran as (``container_user``), else ``default``.

    The fingerprint check binds that user to the flags; a run refuses unless the mode it detects gives the same
    user. An unreadable record gives ``default`` (the self-test check reports the record itself).
    """
    try:
        rec = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    user = rec.get("container_user") if isinstance(rec, dict) else None
    return user if isinstance(user, str) and USER_RE.fullmatch(user) else default   # anything else: no unlock
