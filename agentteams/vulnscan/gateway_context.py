"""The host's DLP context for the containerized gateway (roadmap B2; hardening plan W2 residual).

The gateway (:mod:`.gateway`) runs in a container, so on its own its DLP filter (:mod:`.gateway_dlp`)
knows only the container's hostname, no secrets files and no private package names. Before every
gateway start (a run, the self-test, a debrief) the launcher calls :func:`prepare`, which gathers the
host's context at run time and passes it to the gateway as read-only file mounts:

| Context | Gathered from (on the host, each start) | Reaches the gateway as |
|---|---|---|
| host names | ``socket.gethostname()`` (full and short); on macOS ``scutil --get ComputerName`` / ``LocalHostName`` (and ``<LocalHostName>.local``), best effort; the host part of every ``<server kit>/hosts.conf`` ssh target (destination and ``-J`` hops) and, for ssh-config aliases, the ``hostname`` / ``proxyjump`` that ``ssh -G`` resolves (contacts nothing; best effort) | ``<gateway dir>/dlp/hostnames`` mounted at ``/run/dlp/hostnames:ro``; ``--dlp-hostnames-file`` |
| private package names | ``private_packages`` of every registry entry's brief (:func:`.registry.load` → :func:`.project_policy.read_policy` of the fix checkout) | ``<gateway dir>/dlp/private-names`` (same mount); ``--dlp-private-names-file`` |
| secrets | ``~/.config/agentteams-secrets/*.env`` | each file mounted read-only at ``/run/secrets/dlp/<name>``; ``--dlp-secrets-file`` per file |

The upstream key file(s) the gateway already mounts are blocked too (the gateway reads their values).
The gateway is the only container that sees any of this; the agent container mounts none of it.

**Never redacted:** a ``hosts.conf`` *name* (first column), and any gathered name equal to one: the
agent names servers by them (``server-audit`` / ``server-dryrun`` targets), so redacting them would
break server operations. A name that reveals a host anyway (a fully qualified name or an address, or this
machine's own name: :func:`revealing_names`) is warned about at every gateway start (stderr) and counted in
``<gateway dir>/dlp/notes`` (``hosts_conf_unredacted=N``), which ``review`` shows as an anomaly.

**Unreadable context fails closed.** The gateway exits 2 when a names file or a secrets file cannot be read
(:class:`agentteams.vulnscan.gateway.DlpContextError`). It runs as uid 10001: Docker Desktop (macOS) maps file
ownership, a Linux Docker Engine bind mount does not (the files keep the operator's uid and 0700/0600 modes), so
on Linux the gateway refuses to start; :func:`prepare` prints :data:`LINUX_NOTE` there. Making the files
world-readable is not done: the secrets would then be readable by every local account.

**Never in argv or a repository.** The values are written (directory 0700, files 0600, symlinks
refused) under the run's gateway dir and regenerated at every start; the argv carries only fixed
in-container paths, so ``docker inspect`` / ``ps`` show no host name or package name. The self-test
fingerprint (:func:`agentteams.vulnscan.agent_run.flags_sha256`) hashes the gateway argv built with
:func:`placeholder` (one fixed secrets file): it covers the presence and paths of these flags and
mounts, never the machine's values or its number of secrets files, so it does not churn.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agentteams.vulnscan import gateway_dlp, paths, project_policy, registry
from agentteams.vulnscan.agent_upstreams import SECRETS_DIR
from agentteams.vulnscan.subproc_confine import Unavailable
from agentteams.vulnscan.subproc_confine_ssh import resolve, split_target

#: The context directory's name under the gateway dir, and its in-gateway mount point.
DIR_NAME = "dlp"
IN_GW = "/run/dlp"
HOSTNAMES = "hostnames"
PRIVATE_NAMES = "private-names"
#: Launcher notes for the operator's review (counts only), regenerated at every start: ``key=value`` lines.
NOTES = "notes"
#: In-gateway directory of the mounted secrets files.
SECRETS_IN_GW = "/run/secrets/dlp"
#: Printed at every gateway start on Linux (@security rc.4 Low 5): the gateway refuses to start (exit 2) on an
#: unreadable DLP file, and a rootful Linux bind mount keeps the host's owner and modes (0700 / 0600 here).
LINUX_NOTE = ("on Linux the gateway container (uid 10001) must be able to read the DLP context dir, the key "
              "file(s) and each secrets file it mounts; a Docker Engine bind mount keeps your uid and the 0700/0600 "
              "modes, so the gateway then refuses to start (exit 2) rather than run with an incomplete filter. "
              "Agent mode is verified with Docker Desktop on macOS only (docs: 'Gateway DLP context')")
HEADER = "# agentteams vulnscan gateway DLP context: machine-local, regenerated at every gateway start\n"
_SAFE_ENV = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.env")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{2,252}")
_LOCAL = frozenset({"localhost", "localhost.localdomain", "local"})


@dataclass(frozen=True)
class Context:
    """Where the gateway's DLP context lives on the host.

    Attributes:
        directory: Host directory holding :data:`HOSTNAMES` and :data:`PRIVATE_NAMES`.
        secrets_files: Host secrets files, each mounted read-only into the gateway.
    """

    directory: Path
    secrets_files: tuple[Path, ...] = ()


def for_dir(spend_dir: Path) -> Context:
    """Return the context location for a gateway dir, with no secrets files (nothing is read or written)."""
    return Context(spend_dir / DIR_NAME)


def placeholder() -> Context:
    """The fixed context :func:`agentteams.vulnscan.agent_run.flags_sha256` hashes (no machine values)."""
    return Context(Path("/spend") / DIR_NAME, (Path("/secrets/secret.env"),))


def argv_parts(ctx: Context) -> tuple[list[str], list[str]]:
    """Return ``(docker mounts, gateway flags)`` for a context: every mount read-only, only fixed paths in flags."""
    mounts = ["-v", f"{ctx.directory}:{IN_GW}:ro"]
    flags = ["--dlp-hostnames-file", f"{IN_GW}/{HOSTNAMES}", "--dlp-private-names-file", f"{IN_GW}/{PRIVATE_NAMES}"]
    for f in ctx.secrets_files:
        inside = f"{SECRETS_IN_GW}/{f.name}"
        mounts += ["-v", f"{f}:{inside}:ro"]
        flags += ["--dlp-secrets-file", inside]
    return mounts, flags


# ---------------------------------------------------------------- host names

def _scutil(key: str) -> str:
    """Return ``scutil --get key`` on macOS, or "" (not macOS, no scutil, unset, or it failed)."""
    if sys.platform != "darwin" or not shutil.which("scutil"):
        return ""
    try:
        proc = subprocess.run(["scutil", "--get", key], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):   # best effort: the socket names still apply
        return ""
    lines = proc.stdout.strip().splitlines()
    return lines[0].strip() if proc.returncode == 0 and lines else ""


def local_hostnames() -> set[str]:
    """This machine's names: ``gethostname()`` full and short, and macOS's ComputerName / LocalHostName."""
    full = socket.gethostname().strip().lower()
    names = {full, full.split(".", 1)[0]}
    computer, local = _scutil("ComputerName").lower(), _scutil("LocalHostName").lower()
    names |= {computer, local, f"{local}.local" if local else ""}
    return names


def host_part(target: str) -> str:
    """Return the host of an ssh destination or jump hop (``[ssh://][user@]host[:port]``), or ""."""
    s = target.strip()
    s = s[6:] if s.startswith("ssh://") else s
    s = s.rsplit("@", 1)[-1].split("/", 1)[0]
    if s.startswith("["):
        s = s[1:].split("]", 1)[0]
    elif s.count(":") == 1:
        s = s.split(":", 1)[0]
    return s.lower() if _HOST.fullmatch(s) else ""


def _resolved(args: list[str]) -> list[str]:
    """``ssh -G``'s ``hostname`` and ``proxyjump`` hops for a target (ssh-config aliases), or [] (best effort)."""
    try:
        out = resolve(args)
    except Unavailable:   # no ssh, or it rejected the target: the literal host part still applies
        return []
    return [*out.get("hostname", []), *(h for v in out.get("proxyjump", []) if v != "none" for h in v.split(","))]


def kit_hosts(kit_dir: Path) -> tuple[set[str], set[str]]:
    """Return ``(hosts.conf names, the host parts of their ssh targets)`` from ``kit_dir/hosts.conf``."""
    conf = kit_dir / "hosts.conf"
    try:
        text = conf.read_text(encoding="utf-8") if conf.is_file() else ""
    except (OSError, UnicodeDecodeError):   # best effort: an unreadable kit adds no names
        text = ""
    lines = [ln for ln in text.replace("\r", "").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    names, hosts = set(), set()
    for ln in lines:
        fields = ln.split("|")
        names.add(fields[0].strip().lower())
        args = fields[1].split() if len(fields) > 1 else []
        dest, jump = split_target(args)
        hops = [dest, *jump.split(","), *(_resolved(args) if dest else [])]
        hosts |= {host_part(h) for h in hops if h}
    return names, hosts


def hostnames(kit_dir: Path) -> list[str]:
    """Every host name to redact: this machine's and the server kit's, minus the hosts.conf names."""
    return _hostnames(kit_hosts(kit_dir), local_hostnames())


def _hostnames(kit: tuple[set[str], set[str]], local: set[str]) -> list[str]:
    names, hosts = kit
    return sorted(n for n in local | hosts if len(n) >= 3 and n not in _LOCAL and n not in names and n.isprintable())


def revealing_names(names: Iterable[str], local: set[str]) -> list[str]:
    """Return the ``hosts.conf`` names that reveal a host although they are never redacted (@security rc.4 Low 6):
    a fully qualified name or address (it has a dot) or one equal to this machine's name."""
    return sorted(n for n in names if n and ("." in n.strip(".") or n in local))


def unredacted_note(count: int) -> str:
    """The operator-facing warning for :func:`revealing_names` (a count, never the names: the review may be published)."""
    return (f"{count} hosts.conf name(s) are a full host name, an address or this machine's name; hosts.conf names "
            "reach the model unredacted (the agent names servers by them): rename them to short aliases")


# ---------------------------------------------------------------- private package names

def private_names(entries: Iterable[registry.Entry] | None = None) -> tuple[list[str], str]:
    """Return ``(the union of every enrolled project's private_packages, problem or "")``.

    Args:
        entries: Registry entries (default: :func:`.registry.load`); every entry counts, whatever its level.
    """
    if entries is None:
        try:
            entries = registry.load()
        except (OSError, ValueError) as exc:
            return [], f"the registry cannot be read ({type(exc).__name__}): private package names are not redacted"
    pats = {p for e in entries for p in project_policy.read_policy(Path(e.fix_checkout)).private_packages}
    return sorted(pats), ""


# ---------------------------------------------------------------- secrets files

def secrets_files(directory: str | Path = SECRETS_DIR) -> tuple[Path, ...]:
    """The secrets directory's ``*.env`` files whose names and paths are safe in a ``-v`` mount spec."""
    return tuple(p for p in gateway_dlp.secrets_dir_files(directory)
                 if _SAFE_ENV.fullmatch(p.name) and ":" not in str(p) and "," not in str(p))


# ---------------------------------------------------------------- writing

def _write(path: Path, lines: Iterable[str]) -> None:
    """Write ``lines`` to a fresh 0600 file (an existing file or planted symlink is removed, never followed)."""
    if os.path.lexists(path):
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(HEADER + "".join(f"{ln}\n" for ln in lines))


def prepare(spend_dir: Path, *, kit_dir: Path | None = None, secrets_dir: str | Path = SECRETS_DIR,
            entries: Iterable[registry.Entry] | None = None) -> Context:
    """Gather the host's DLP context and write it under ``spend_dir`` (the gateway dir) for one gateway start.

    Args:
        spend_dir: The gateway's host dir (``<run>/gateway``, the self-test's or a debrief's).
        kit_dir: Server-kit config dir (default :func:`.paths.server_kit_dir`).
        secrets_dir: The agentteams secrets directory.
        entries: Registry entries (default: :func:`.registry.load`).

    Returns:
        The context to pass to :func:`agentteams.vulnscan.agent_run.gateway_argv`.

    Raises:
        ValueError: The context dir is a symlink or not a directory (refused, never followed).
        OSError: The context cannot be written (the gateway is not started without it).
    """
    d = spend_dir / DIR_NAME
    if d.is_symlink() or (d.exists() and not d.is_dir()):
        raise ValueError(f"refusing the gateway DLP context dir {d}: not a plain directory")
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    names, problem = private_names(entries)
    if problem:
        print(f"vulnscan: gateway DLP: {problem}", file=sys.stderr)
    kit, local = kit_hosts(kit_dir or paths.server_kit_dir()), local_hostnames()
    revealing = revealing_names(kit[0], local)
    if revealing:   # once per gateway start (a run starts one); the review shows the note file
        print(f"vulnscan: gateway DLP: warning: {unredacted_note(len(revealing))}", file=sys.stderr)
    _write(d / HOSTNAMES, _hostnames(kit, local))
    _write(d / PRIVATE_NAMES, names)
    _write(d / NOTES, [f"hosts_conf_unredacted={len(revealing)}"])
    files = secrets_files(secrets_dir)
    if sys.platform.startswith("linux"):
        print(f"vulnscan: gateway DLP: {LINUX_NOTE}", file=sys.stderr)
    return Context(d, files)
