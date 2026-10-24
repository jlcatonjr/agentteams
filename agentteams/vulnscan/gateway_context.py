"""The host's DLP context for the containerized gateway (roadmap B2; hardening plan W2 residual).

The gateway (:mod:`.gateway`) runs in a container, so on its own its DLP filter (:mod:`.gateway_dlp`)
knows only the container's hostname, no secrets files and no private package names. Before every
gateway start (a run, the self-test, a debrief) the launcher calls :func:`prepare`, which gathers the
host's context at run time and passes it to the gateway as read-only file mounts:

| Context | Gathered from (on the host, each start) | Reaches the gateway as |
|---|---|---|
| host names | ``socket.gethostname()`` (full and short); on macOS ``scutil --get ComputerName`` / ``LocalHostName`` (and ``<LocalHostName>.local``), best effort; the host part of every ``<server kit>/hosts.conf`` ssh target (destination and ``-J`` hops) and, for ssh-config aliases, the ``hostname`` / ``proxyjump`` that ``ssh -G`` resolves (contacts nothing; best effort) | ``<gateway dir>/dlp/hostnames`` mounted at ``/run/dlp/hostnames:ro``; ``--dlp-hostnames-file`` |
| private package names | ``private_packages`` of every registry entry's brief (:func:`.registry.load` → :func:`.project_policy.read_policy` of the fix checkout) | ``<gateway dir>/dlp/private-names`` (same mount); ``--dlp-private-names-file`` |
| secrets | every ``*.env`` (and ``.env``) file of ``~/.config/agentteams-secrets`` | the directory mounted read-only at ``/run/secrets/dlp/<n>`` (``--mount``, CSV-quoted: any name or path works); ``--dlp-secrets-mount`` per directory, ``--dlp-skip-ok-file`` |

The upstream key file(s) the gateway already mounts are blocked too (the gateway reads their values).
The gateway is the only container that sees any of this; the agent container mounts none of it.

**Never redacted:** a ``hosts.conf`` *name* (first column), and any gathered name equal to one: the
agent names servers by them (``server-audit`` / ``server-dryrun`` targets), so redacting them would
break server operations. A name that reveals a host anyway (a fully qualified name or an address, or this
machine's own name: :func:`revealing_names`) is warned about at every gateway start (stderr) and counted in
``<gateway dir>/dlp/notes`` (``hosts_conf_unredacted=N``), which ``review`` shows as an anomaly.

**Unusable secrets fail closed (R5).** :func:`prepare` raises :class:`DlpRefused` (the run ends ``not-run``) when a
configured secrets directory exists but cannot be listed, or holds a ``*.env`` file the gateway could not use
(unreadable, not a regular file, or a symlink out of the directory) that ``agent.dlp_skip_ok`` does not acknowledge
by path and sha256 (:func:`validate_skip_ok`). Only an acknowledged file's bytes are read here, to hash them; the
gateway checks the text of the rest (a file that is not UTF-8 makes it exit 2). A missing directory is fine (counted
in the notes). The paths are printed on the operator's terminal only; the notes, the reason and the run record hold
counts.

**Unreadable context fails closed.** The gateway exits 2 when a names file or a secrets file or directory cannot be
read (:class:`agentteams.vulnscan.gateway.DlpContextError`), and records a count in ``<gateway dir>/dlp-refused``
(:func:`raise_if_refused`). Its user follows the Docker mode (:mod:`agentteams.vulnscan.docker_mode`, R4): uid 10001
on Docker Desktop, which maps file ownership; the operator's uid on a rootful Linux Engine and uid 0 mapped to the
operator under rootless Docker, where a bind mount keeps the operator's uid and 0700/0600 modes; :func:`prepare`
prints :data:`LINUX_NOTE` on Linux. Making the files world-readable is not done: the secrets would then be readable
by every local account.

**Never in argv or a repository.** The values are written (directory 0700, files 0600, symlinks
refused) under the run's gateway dir and regenerated at every start; the argv carries only fixed
in-container paths, so ``docker inspect`` / ``ps`` show no host name or package name. The self-test
fingerprint (:func:`agentteams.vulnscan.agent_run.flags_sha256`) hashes the gateway argv built with
:func:`placeholder` (one fixed secrets directory): it covers the presence and paths of these flags and
mounts, never the machine's values or its secrets files, so it does not churn. A secrets directory mount names
its host path (as the key file mounts do); file names stay out of argv (:data:`SKIP_OK` is a file).
"""

from __future__ import annotations

import csv
import hashlib
import io
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
#: In-gateway parent of the mounted secrets directories (the configured directory ``n`` is at ``<this>/<n>``).
SECRETS_IN_GW = "/run/secrets/dlp"
#: The acknowledged files (``agent.dlp_skip_ok`` entries that match), one in-gateway path per line.
SKIP_OK = "skip-ok"
#: Printed at every gateway start on Linux (@security rc.4 Low 5): the gateway refuses to start (exit 2) on an
#: unreadable DLP file, and a Linux bind mount keeps the host's owner and modes (0700 / 0600 here).
LINUX_NOTE = ("on Linux the gateway container must be able to read the DLP context dir, the key file(s) and each "
              "secrets file it mounts, or it refuses to start (exit 2) rather than run with an incomplete filter. "
              "It runs as your uid on a rootful Docker Engine and as uid 0 mapped to you under rootless Docker, so "
              "files you own are readable; userns-remap is refused (docs: 'Docker modes', 'Gateway DLP context')")
HEADER = "# agentteams vulnscan gateway DLP context: machine-local, regenerated at every gateway start\n"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{2,252}")
_LOCAL = frozenset({"localhost", "localhost.localdomain", "local"})


@dataclass(frozen=True)
class Context:
    """Where the gateway's DLP context lives on the host.

    Attributes:
        directory: Host directory holding :data:`HOSTNAMES`, :data:`PRIVATE_NAMES` and :data:`SKIP_OK`.
        secrets_dirs: Host secrets directories, each mounted read-only into the gateway (R5).
    """

    directory: Path
    secrets_dirs: tuple[Path, ...] = ()


class DlpRefused(RuntimeError):
    """The gateway's DLP context is incomplete, so no gateway starts and the run ends ``not-run`` (R5).

    The message holds counts only: it reaches the run record. The paths went to the operator's terminal.
    """


def for_dir(spend_dir: Path) -> Context:
    """Return the context location for a gateway dir, with no secrets directories (nothing is read or written)."""
    return Context(spend_dir / DIR_NAME)


def placeholder() -> Context:
    """The fixed context :func:`agentteams.vulnscan.agent_run.flags_sha256` hashes (no machine values)."""
    return Context(Path("/spend") / DIR_NAME, (Path("/secrets"),))


def bind_spec(source: Path, target: str) -> str:
    """Return a read-only ``--mount`` value, CSV-quoted so a comma, colon or quote in ``source`` stays one path.

    Raises:
        ValueError: ``source`` holds a line break (docker's CSV parser cannot take one).
    """
    if "\n" in str(source) or "\r" in str(source):
        raise ValueError("a mount source with a line break cannot be passed to docker")
    buf = io.StringIO()
    csv.writer(buf, lineterminator="").writerow(["type=bind", f"source={source}", f"target={target}", "readonly"])
    return buf.getvalue()


def argv_parts(ctx: Context) -> tuple[list[str], list[str]]:
    """Return ``(docker mounts, gateway flags)`` for a context: every mount read-only, only fixed paths in flags."""
    mounts = ["-v", f"{ctx.directory}:{IN_GW}:ro"]
    flags = ["--dlp-hostnames-file", f"{IN_GW}/{HOSTNAMES}", "--dlp-private-names-file", f"{IN_GW}/{PRIVATE_NAMES}"]
    for n, d in enumerate(ctx.secrets_dirs):
        mounts += ["--mount", bind_spec(d, f"{SECRETS_IN_GW}/{n}")]
        flags += ["--dlp-secrets-mount", f"{SECRETS_IN_GW}/{n}"]
    if ctx.secrets_dirs:
        flags += ["--dlp-skip-ok-file", f"{IN_GW}/{SKIP_OK}"]
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


# ---------------------------------------------------------------- secrets directories (R5)

def validate_skip_ok(value: object) -> list[tuple[str, str]]:
    """Check ``agent.dlp_skip_ok``: a list of ``{"path": str, "sha256": 64 lower-case hex}`` objects.

    Returns:
        ``(absolute path with ~ expanded, sha256)`` per entry.

    Raises:
        ValueError: The value or an entry has the wrong shape; the message names ``agent.dlp_skip_ok[i]``.
    """
    if not isinstance(value, list):
        raise ValueError(f"agent.dlp_skip_ok must be a list of {{path, sha256}} objects (got {repr(value)[:80]})")
    out = []
    for i, e in enumerate(value):
        path, digest = (e.get("path"), e.get("sha256")) if isinstance(e, dict) else (None, None)
        if not isinstance(e, dict) or set(e) != {"path", "sha256"} or not isinstance(path, str) or not path.strip() \
                or "\0" in path or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError(f"agent.dlp_skip_ok[{i}] must be {{\"path\": <file>, \"sha256\": <64 lower-case hex>}} "
                             f"(got {repr(e)[:80]})")
        out.append((os.path.abspath(os.path.expanduser(path)), digest))
    return out


def _unusable(f: Path, root: Path) -> str:
    """Why the gateway could not use ``f``, or "" (the bytes are not read: the gateway checks the text itself)."""
    if f.is_symlink() and not f.resolve().is_relative_to(root):
        return "a symlink out of the directory (the gateway cannot follow it)"
    if f.is_symlink() and os.path.isabs(os.readlink(f)):   # a host path: it does not exist inside the gateway
        return "an absolute symlink (the gateway sees the directory elsewhere; use a relative link)"
    if not f.is_file():
        return "not a regular file"
    return "" if os.access(f, os.R_OK) else "cannot be read"


def _norm(path: str) -> str:
    """A file path with its directory resolved (``/var`` and ``/private/var`` agree), the name itself kept."""
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def _sha256(f: Path) -> str:
    """The sha256 of a regular file's bytes, or "" (unreadable, or not a regular file: a FIFO would block)."""
    try:
        return hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else ""
    except OSError:   # an acknowledgement needs the bytes: without them the file is not acknowledged
        return ""


def check_secrets(directories: Iterable[str | Path], skip_ok: Iterable[tuple[str, str]] = ()
                  ) -> tuple[tuple[Path, ...], list[str], list[str], dict[str, int]]:
    """Check the configured secrets directories on the host before they are mounted (R5).

    Args:
        directories: The configured secrets directories (``~`` expanded here).
        skip_ok: :func:`validate_skip_ok` entries; one that matches a file's path and current sha256 lets
            that file go unchecked (the gateway skips it too when it cannot read it).

    Returns:
        ``(directories to mount, problems (each names a path: terminal only), acknowledged in-gateway paths,
        counts for the notes)``.
    """
    acks = {_norm(p): digest for p, digest in skip_ok}
    mounts: list[Path] = []
    problems: list[str] = []
    acked: list[str] = []
    counts = {"secrets_dirs_missing": 0, "secrets_files": 0}
    for raw in directories:
        d = Path(os.path.abspath(os.path.expanduser(str(raw))))
        if not os.path.lexists(d):   # not configured on this machine: fine
            counts["secrets_dirs_missing"] += 1
            continue
        try:
            root = d.resolve(strict=True)
            files = gateway_dlp.env_candidates(d) if d.is_dir() and "\n" not in str(d) and "\r" not in str(d) else None
        except OSError:
            files = None
        if files is None:
            problems.append(f"{d}: a configured secrets directory that cannot be read")
            continue
        inside = f"{SECRETS_IN_GW}/{len(mounts)}"
        mounts.append(d)
        for f in files:
            counts["secrets_files"] += 1
            if _norm(str(f)) in acks and _sha256(f) == acks[_norm(str(f))]:   # only an acknowledged file is read
                acked.append(f"{inside}/{f.name}")
                continue
            why = _unusable(f, root)
            if why:
                problems.append(f"{f}: {why}")
    counts["secrets_skip_ok"] = len(acked)
    counts["secrets_skip_ok_unmatched"] = len(acks) - len(acked)
    return tuple(mounts), problems, acked, counts


def raise_if_refused(spend_dir: Path) -> None:
    """Raise :class:`DlpRefused` when the gateway recorded a DLP-context refusal (it exited 2) in ``spend_dir``."""
    marker = spend_dir / gateway_dlp.REFUSED_NAME
    try:
        why = marker.read_text(encoding="utf-8", errors="replace").strip()[:300] if marker.is_file() else ""
    except OSError as exc:   # present but unreadable: still a refusal
        why = f"its record could not be read ({type(exc).__name__})"
    if why:
        raise DlpRefused(f"the gateway refused to start (exit 2): {why}; see 'Gateway DLP context' in the docs")


# ---------------------------------------------------------------- writing

def _write(path: Path, lines: Iterable[str]) -> None:
    """Write ``lines`` to a fresh 0600 file (an existing file or planted symlink is removed, never followed)."""
    if os.path.lexists(path):
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(HEADER + "".join(f"{ln}\n" for ln in lines))


def prepare(spend_dir: Path, *, kit_dir: Path | None = None,
            secrets_dir: str | Path | Iterable[str | Path] = SECRETS_DIR,
            entries: Iterable[registry.Entry] | None = None, skip_ok: object = None) -> Context:
    """Gather the host's DLP context and write it under ``spend_dir`` (the gateway dir) for one gateway start.

    Args:
        spend_dir: The gateway's host dir (``<run>/gateway``, the self-test's or a debrief's).
        kit_dir: Server-kit config dir (default :func:`.paths.server_kit_dir`).
        secrets_dir: The agentteams secrets directory, or several (each mounted at ``/run/secrets/dlp/<n>``).
        entries: Registry entries (default: :func:`.registry.load`).
        skip_ok: ``agent.dlp_skip_ok`` (:func:`validate_skip_ok`).

    Returns:
        The context to pass to :func:`agentteams.vulnscan.agent_run.gateway_argv`.

    Raises:
        ValueError: The context dir is a symlink or not a directory (refused, never followed), or ``skip_ok``
            is malformed.
        DlpRefused: A secrets directory or file the gateway could not use (R5; the paths are printed on stderr).
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
    dirs = [secrets_dir] if isinstance(secrets_dir, (str, Path)) else list(secrets_dir)
    mounts, problems, acked, counts = check_secrets(dirs, validate_skip_ok([] if skip_ok is None else skip_ok))
    _write(d / SKIP_OK, acked)
    _write(d / NOTES, [f"hosts_conf_unredacted={len(revealing)}", *(f"{k}={v}" for k, v in counts.items()),
                       f"secrets_problems={len(problems)}"])   # counts only: the review may be published
    refused = spend_dir / gateway_dlp.REFUSED_NAME
    if os.path.lexists(refused):   # an earlier start's refusal must not end this one
        refused.unlink()
    if sys.platform.startswith("linux"):
        print(f"vulnscan: gateway DLP: {LINUX_NOTE}", file=sys.stderr)
    if counts["secrets_skip_ok_unmatched"]:
        print(f"vulnscan: gateway DLP: {counts['secrets_skip_ok_unmatched']} agent.dlp_skip_ok entr(ies) match no "
              "current file (moved, or edited since: an edited file needs its new sha256)", file=sys.stderr)
    for line in problems:   # the operator's terminal only: never the notes, the reason or the run record
        print(f"vulnscan: gateway DLP: {line}", file=sys.stderr)
    if problems:
        raise DlpRefused(f"gateway DLP: {len(problems)} secrets director(ies) or file(s) the gateway cannot use "
                         "(paths on the terminal); fix their permissions, or acknowledge one exact file version in "
                         "agent.dlp_skip_ok ({\"path\": ..., \"sha256\": ...})")
    return Context(d, mounts)
