"""Server-kit runs (``remote.sh`` / ``run-host.sh`` over ssh) inside the tools container (W1).

The packaged kit runs ``ssh <hosts.conf target>``. Confined, the container sees none of ``~/.ssh``. Instead, for one
run, this module resolves the target on the host with ``ssh -G`` (configuration only: nothing is contacted) and
builds, in a fresh directory that is removed afterwards:

- ``ssh_config`` (mounted at ``/etc/ssh/ssh_config``): a block for the target and, when it jumps through a bastion
  (``-J`` or ``ProxyJump``, one hop), for the bastion, with the resolved ``HostName``, ``Port``, ``User`` and
  ``HostKeyAlias``; then ``BatchMode``, ``StrictHostKeyChecking yes``, ``IdentitiesOnly``, no agent or X11 forwarding,
  no multiplexing, and ``ProxyCommand nc -X connect`` through this run's egress proxy;
- ``known_hosts``: only the target's and the bastion's lines (``ssh-keygen -F``), so an unknown host key fails;
- the identity files the target's ssh config names (or ``confine.ssh_identity``), each mounted read-only.

The egress allowlist is the first hop (the bastion, else the target) at its port. Refused (fail closed, with the
reason): a ``ProxyCommand``, a multi-hop ``ProxyJump``, a host with no known_hosts line, no identity file, or a key
that needs ssh-agent (``BatchMode`` cannot prompt; the agent socket is never forwarded).

The kit itself is not mounted from the installed package: :func:`stage_kit` copies it into the state dir (regular
files only, symlinks refused), re-staging when the packaged files change, and every run re-checks the copy against
the digests taken then. A reinstall that replaces the package directory during a run cannot remove the mount source
of later runs, and an edit to the copy after staging is refused rather than run on a server. A kit already altered
on disk before staging is not detected here: the integrity pins are not shipped in an installed package.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

from agentteams import atomicio
from agentteams.vulnscan import paths
from agentteams.vulnscan.subproc_confine import (
    PROXY_PORT,
    Mount,
    Unavailable,
    active,
    new_name,
    run_confined,
)

#: ssh options that take a value (``-p 22``, ``-p22``).
_VALUE_OPTS = set("BbcDEeFIiJLlmOoPpQRSWw")
CONFIG_IN = "/etc/ssh/ssh_config"
KNOWN_IN = "/etc/ssh/vulnscan_known_hosts"
_UNSAFE = set(" \t\r\n\"'\\#")
#: Kit files a staged copy must hold; a package missing one is refused, never run in part.
KIT_REQUIRED = ("remote.sh", "run-host.sh", "L1-audit.sh", "L2-os-patch.sh", "L3-node-runtime.sh",
                "L4-python-app.sh", "L5-containers.sh")
#: A kit file larger than this is not staged (the packaged scripts are a few KiB each).
KIT_MAX_BYTES = 1_000_000
#: Source kit dir -> (staged copy, {file name: sha256}) for this process (:func:`stage_kit`).
_STAGED: dict[str, tuple[Path, dict[str, str]]] = {}


def kit_target(conf_dir: Path, host: str) -> str:
    """Return ``host``'s ssh target from ``hosts.conf`` (fields stripped as remote.sh does), or ""."""
    conf = conf_dir / "hosts.conf"
    if not conf.is_file():
        return ""
    for ln in conf.read_text(encoding="utf-8").replace("\r", "").splitlines():
        if ln.strip() and not ln.lstrip().startswith("#"):
            fields = ln.split("|")
            if fields[0].strip() == host and len(fields) > 1:
                return fields[1].strip()
    return ""


def split_target(args: list[str]) -> tuple[str, str]:
    """Return (destination, ``-J`` value or "") from ssh arguments."""
    dest, jump, i = "", "", 0
    while i < len(args) and not dest:
        a = args[i]
        if a.startswith("-") and len(a) > 1:
            for j, c in enumerate(a[1:], start=1):
                if c in _VALUE_OPTS:
                    inline = a[j + 1:]
                    value = inline or (args[i + 1] if i + 1 < len(args) else "")
                    if not inline:
                        i += 1   # the value is the next argument
                    if c == "J":
                        jump = value
                    break
        else:
            dest = a
        i += 1
    return dest, jump


def resolve(args: list[str]) -> dict[str, list[str]]:
    """Return ``ssh -G args`` (lower-cased keys, every value kept). Contacts nothing.

    Raises:
        Unavailable: ssh is missing or rejected the arguments.
    """
    try:
        proc = subprocess.run(["ssh", "-G", *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unavailable(f"tools container unavailable: `ssh -G` failed: {exc}") from exc
    if proc.returncode:
        raise Unavailable(f"tools container unavailable: `ssh -G` rejected the target: {proc.stderr.strip()[:200]}")
    out: dict[str, list[str]] = {}
    for ln in proc.stdout.splitlines():
        key, _, value = ln.partition(" ")
        out.setdefault(key.lower(), []).append(value.strip())
    return out


def _one(cfg: dict[str, list[str]], key: str, default: str = "") -> str:
    return (cfg.get(key) or [default])[0]


def _jump_args(spec: str) -> list[str]:
    """Turn a ``ProxyJump`` / ``-J`` value (``[user@]host[:port]`` or ``ssh://...``) into ssh arguments."""
    spec = spec.removeprefix("ssh://")
    user, _, hostport = spec.rpartition("@")
    host, _, port = hostport.partition(":")
    return [*(["-p", port] if port else []), f"{user}@{host}" if user else host]


def _hop(cfg: dict[str, list[str]], what: str) -> dict[str, str]:
    if _one(cfg, "proxycommand", "none") != "none":
        raise Unavailable(f"tools container unavailable: the {what}'s ssh config uses a ProxyCommand, which the tools "
                          "container does not run")
    hop = {k: _one(cfg, k) for k in ("hostname", "port", "user", "hostkeyalias")}
    hop["port"] = hop["port"] or "22"
    if hop["hostkeyalias"] == "none":
        hop["hostkeyalias"] = ""
    for k, v in hop.items():
        if any(c in _UNSAFE for c in v):
            raise Unavailable(f"tools container unavailable: the {what}'s {k} {v!r} cannot be written to an ssh config")
    return hop


def _known_lines(cfg: dict[str, list[str]], hop: dict[str, str]) -> list[str]:
    key = hop["hostkeyalias"] or (hop["hostname"] if hop["port"] == "22" else f"[{hop['hostname']}]:{hop['port']}")
    files = [f for v in cfg.get("userknownhostsfile", []) + cfg.get("globalknownhostsfile", []) for f in v.split()]
    lines: list[str] = []
    for f in dict.fromkeys(os.path.expanduser(x) for x in files):
        if Path(f).is_file():
            proc = subprocess.run(["ssh-keygen", "-F", key, "-f", f], capture_output=True, text=True, timeout=30,
                                  check=False)
            lines += [ln for ln in proc.stdout.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        raise Unavailable(f"tools container unavailable: no known_hosts line for {key}; connect once from this "
                          "machine (plain ssh) so its host key is recorded")
    return lines


def _identities(cfg: dict[str, list[str]], override: str) -> list[Path]:
    names = [override] if override else cfg.get("identityfile", [])
    found = [Path(os.path.expanduser(n)) for n in names if "%" not in n]
    found = [p.resolve() for p in found if p.is_file()]
    if not found:
        raise Unavailable("tools container unavailable: no ssh identity file for the server-kit target (set "
                          "confine.ssh_identity, or IdentityFile in your ssh config)")
    return list(dict.fromkeys(found))


def build_ssh_files(dest: str, jump: str, workdir: Path, proxy: str, *, identity: str = "") -> tuple[list[Mount], str]:
    """Write the run's ``ssh_config`` and ``known_hosts`` into ``workdir``; return (mounts, egress endpoint).

    Raises:
        Unavailable: An unsupported or unknown target (see the module docstring).
    """
    target_cfg = resolve([*(["-J", jump] if jump else []), dest])
    target = _hop(target_cfg, "target")
    jump_spec = jump or (_one(target_cfg, "proxyjump", "none") if _one(target_cfg, "proxyjump", "none") != "none"
                         else "")
    if "," in jump_spec:
        raise Unavailable("tools container unavailable: a multi-hop ProxyJump is not supported in the tools container")
    hops = [("target", dest.rpartition("@")[2], target_cfg, target)]
    if jump_spec:
        jcfg = resolve(_jump_args(jump_spec))
        hops.append(("bastion", _jump_args(jump_spec)[-1].rpartition("@")[2], jcfg, _hop(jcfg, "bastion")))
    known: list[str] = []
    mounts: list[Mount] = []
    blocks: list[str] = []
    n = 0
    for what, alias, cfg, hop in hops:
        if any(c in _UNSAFE for c in alias) or not alias:
            raise Unavailable(f"tools container unavailable: the {what} name {alias!r} cannot be used in an ssh config")
        known += _known_lines(cfg, hop)
        lines = [f"Host {alias}", f"  HostName {hop['hostname']}", f"  Port {hop['port']}"]
        lines += [f"  User {hop['user']}"] if hop["user"] else []
        lines += [f"  HostKeyAlias {hop['hostkeyalias']}"] if hop["hostkeyalias"] else []
        if what == "target" and jump_spec and not jump:
            lines.append(f"  ProxyJump {jump_spec}")
        for key in _identities(cfg, identity):
            n += 1
            inside = f"/etc/ssh/vulnscan_id_{n}"
            mounts.append(Mount(key, inside, secret_ok=True))
            lines.append(f"  IdentityFile {inside}")
        blocks.append("\n".join(lines))
    first = hops[-1][3]
    blocks.append("\n".join(["Host *", "  BatchMode yes", "  StrictHostKeyChecking yes",
                             f"  UserKnownHostsFile {KNOWN_IN}", "  GlobalKnownHostsFile /dev/null",
                             "  IdentitiesOnly yes", "  ForwardAgent no", "  ForwardX11 no", "  ControlMaster no",
                             "  ControlPath none", "  UpdateHostKeys no", "  PermitLocalCommand no",
                             "  CheckHostIP no", "  HashKnownHosts no",
                             f"  ProxyCommand nc -X connect -x {proxy}:{PROXY_PORT} %h %p"]))
    (workdir / "ssh_config").write_text("# generated by agentteams vulnscan for one server-kit run\n"
                                        + "\n\n".join(blocks) + "\n", encoding="utf-8")
    (workdir / "known_hosts").write_text("\n".join(dict.fromkeys(known)) + "\n", encoding="utf-8")
    mounts += [Mount(workdir / "ssh_config", CONFIG_IN), Mount(workdir / "known_hosts", KNOWN_IN, secret_ok=True)]
    return mounts, f"{first['hostname']}:{first['port']}"


def _read_kit_file(path: Path) -> tuple[bytes, int]:
    """Return a kit file's bytes and mode bits: a regular file, not followed through a symlink, size-bounded."""
    mode = [0]

    def keep_mode(st: os.stat_result) -> None:
        mode[0] = stat.S_IMODE(st.st_mode)

    try:
        return atomicio.read_regular_nofollow(path, KIT_MAX_BYTES, check=keep_mode), mode[0]
    except OSError as exc:
        raise Unavailable(f"tools container unavailable: server kit file {path.name} unreadable ({exc.strerror})") from exc
    except ValueError as exc:   # not a regular file, or larger than KIT_MAX_BYTES
        raise Unavailable(f"tools container unavailable: server kit entry {path.name} refused ({exc})") from exc


def _read_kit(src: Path) -> dict[str, tuple[bytes, int]]:
    """Return every file of the kit directory ``src`` (name -> (bytes, mode)), refusing an incomplete kit."""
    try:
        names = sorted(os.listdir(src))
    except OSError as exc:
        raise Unavailable(f"tools container unavailable: the packaged server kit is missing ({exc.strerror}); "
                          "was agentteams reinstalled during the run?") from exc
    missing = [n for n in KIT_REQUIRED if n not in names]
    if missing:
        raise Unavailable(f"tools container unavailable: the packaged server kit lacks {', '.join(missing)}")
    return {n: _read_kit_file(src / n) for n in names}


def _remove_staged() -> None:
    for copy, _digests in _STAGED.values():
        shutil.rmtree(copy.parent, ignore_errors=True)
    _STAGED.clear()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:   # someone else's process: alive
        return True
    return True


def _remove_orphans(base: Path) -> None:
    """Remove ``kit-<pid>-*`` copies left by processes that ended without their exit hook (killed)."""
    for d in base.glob("kit-*-*"):
        pid = d.name.split("-")[1]
        if pid.isdigit() and int(pid) != os.getpid() and not _pid_alive(int(pid)) and not d.is_symlink():
            shutil.rmtree(d, ignore_errors=True)


def stage_kit(src: Path) -> tuple[Path, dict[str, str]]:
    """Return a verified copy of the packaged server kit ``src`` for this process, making it when needed.

    The copy lives in a fresh 0700 directory under ``<state dir>/tools/`` and is removed when the process exits
    (copies left by killed processes are removed by the next one). Every entry of ``src`` must be a regular file
    (a symlink, FIFO or subdirectory is refused, never followed) and every :data:`KIT_REQUIRED` file present.
    The packaged files are re-read on each call: while they are unchanged the copy is reused; when they changed
    (an upgrade under a long-running executor) a new copy is made; when they are gone (a reinstall in progress)
    the existing copy is used.

    Args:
        src: The packaged kit directory (:func:`agentteams.vulnscan.executor.kit_dir`).

    Returns:
        ``(copy dir, {file name: sha256})``.

    Raises:
        Unavailable: The kit is missing (and was never staged), incomplete or holds anything but regular files.
    """
    key = str(src)
    try:
        files = _read_kit(src)
    except Unavailable:
        if key in _STAGED and not src.exists():
            return _STAGED[key]
        raise
    digests = {n: hashlib.sha256(data).hexdigest() for n, (data, _mode) in files.items()}
    if key in _STAGED and _STAGED[key][1] == digests:
        return _STAGED[key]
    base = paths.state_dir() / "tools"
    base.mkdir(parents=True, exist_ok=True)
    _remove_orphans(base)
    root = base / f"kit-{os.getpid()}-{os.urandom(6).hex()}"
    root.mkdir(mode=0o700)
    copy = root / "kit"
    try:
        copy.mkdir(mode=0o700)
        for name, (data, mode) in files.items():
            fd = os.open(copy / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode & 0o755)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
    except OSError as exc:
        shutil.rmtree(root, ignore_errors=True)
        raise Unavailable(f"tools container unavailable: could not stage the server kit ({exc.strerror})") from exc
    if not _STAGED:
        atexit.register(_remove_staged)
    if key in _STAGED:
        shutil.rmtree(_STAGED[key][0].parent, ignore_errors=True)
    _STAGED[key] = (copy, digests)
    return copy, digests


def _check_staged(copy: Path, digests: dict[str, str]) -> None:
    """Refuse a staged kit whose files changed, appeared or disappeared since :func:`stage_kit` made it."""
    try:
        names = sorted(os.listdir(copy))
    except OSError as exc:
        raise Unavailable(f"tools container unavailable: the staged server kit is gone ({exc.strerror})") from exc
    if names != sorted(digests):
        raise Unavailable("tools container unavailable: the staged server kit changed after staging")
    for name in names:
        if hashlib.sha256(_read_kit_file(copy / name)[0]).hexdigest() != digests[name]:
            raise Unavailable(f"tools container unavailable: staged server kit file {name} changed after staging")


def run_kit(script: Path, host: str, script_args: list[str], *, conf_dir: Path, logs: Path,
            timeout: float = 3600) -> subprocess.CompletedProcess:
    """Run one packaged server-kit script (``bash script host args``) against ``host`` in the tools container.

    Args:
        script: The packaged ``remote.sh`` or ``run-host.sh``; its directory is staged (:func:`stage_kit`) and the
            verified copy is mounted read-only.
        host: Exact hosts.conf name.
        script_args: Further arguments (``L1-audit.sh`` for an audit).
        conf_dir: The server-kit config dir (``hosts.conf``, ``apps.conf``, ``ioc-*.txt``), mounted read-only.
        logs: Log directory, the only writable mount.
        timeout: Seconds.

    Returns:
        The completed process.

    Raises:
        Unavailable: No scope, an unknown or unsupported target, a missing or altered kit, or the container could
            not run.
        subprocess.TimeoutExpired: The run took longer than ``timeout``.
    """
    st = active()
    if st is None:
        raise Unavailable("tools container unavailable: no confinement scope is active")
    target = kit_target(conf_dir, host)
    dest, jump = split_target(target.split())
    if not dest:
        raise Unavailable(f"tools container unavailable: host {host!r} has no ssh target in hosts.conf")
    work = paths.state_dir() / "tools" / f"ssh-{os.urandom(6).hex()}"
    work.mkdir(parents=True, mode=0o700)
    logs.mkdir(parents=True, exist_ok=True)
    proxy = new_name("egress")
    try:
        mounts, endpoint = build_ssh_files(dest, jump, work, proxy, identity=st.ssh_identity)
        env = {"AGENTTEAMS_SERVER_KIT_CONFIG": str(conf_dir.resolve()), "AGENTTEAMS_SERVER_KIT_LOGS": str(logs.resolve()),
               "AGENTTEAMS_VULNSCAN_NO_REPORT": "1"}
        kit, digests = stage_kit(script.parent)
        _check_staged(kit, digests)
        return run_confined("server-kit", ["bash", str(kit / script.name), host, *script_args], writable=[logs],
                            readonly=[kit, conf_dir], mounts=mounts, egress=(endpoint,),
                            env=env, timeout=timeout, proxy_name=proxy)
    finally:
        shutil.rmtree(work, ignore_errors=True)
