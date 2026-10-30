"""Run the executor's and the daily run's risky subprocesses in a hardened tools container (W1, decision D1).

The executor and the daily run's ``apply-worktree`` staging run tooling against repositories the operator does not
control: git (fetch, worktree, checkout, commit, push), ``npm audit fix`` and the server kit (``remote.sh`` /
``run-host.sh`` over ssh). Inside a confinement :func:`scope` each of those runs in a throwaway container of the
**tools image** (:mod:`.tools_image`) instead of on the host:

| Aspect | What the container gets |
|---|---|
| root filesystem | ``--read-only``; ``/tmp`` and ``$HOME`` (``/toolhome``) are empty tmpfs mounts |
| privileges | ``--cap-drop ALL``, ``no-new-privileges``, the operator's uid:gid (never root), pids and memory limits |
| writable mounts | only what the operation writes: the repository's git dirs (not for read-only queries), the worktree it
  stages in (under a scratch root: ``<state>/worktrees``, daily's ``<repo>.worktrees``), the npm scratch copy, the
  server kit's log directory |
| read-only mounts | the repository read, the executor's git identity file, the packaged server kit and its config dir |
| never mounted | the home directory or an ancestor of it, and credential locations (:data:`SECRET_DIRS`): ``~/.ssh``,
  ``~/.config/gh``, ``~/.aws``, ``~/.gnupg``, ``~/.docker``, signing keys. A server-kit run alone gets a generated
  ssh config, the target's known_hosts lines and the identity files its ssh config names, read-only
  (:mod:`.subproc_confine_ssh`) |
| network | ``none`` unless the operation needs it. Then an internal Docker network (:data:`NETWORK`) whose only way out
  is a per-operation CONNECT proxy (``egress_proxy.py`` in the image) allowing exactly the listed ``host:port``:
  git → origin's host, npm → ``registry.npmjs.org:443``, ssh → the hosts.conf target's first hop |

**Fail closed.** When confinement is on (``confine.subprocesses``, default true) and Docker, the tools image or a
passing ``tools-selftest`` for that image and these flags is missing, :func:`scope` raises :class:`Unavailable`
("tools container unavailable ...") and the caller refuses the operation. Nothing falls back to the host. A Docker
failure mid-operation (exit 125-127, a mount it refuses) raises it too. ``confine.subprocesses: false`` is the
operator's explicit opt-out: today's host behaviour, announced on stderr and in every review.

**Credentials.** A credentialed git fetch or push takes the operator's GitHub token from ``gh auth token --hostname
HOST`` on the host and hands it to the container on **stdin**: a wrapper in the image keeps it in one process's
environment, and a credential helper gives it only to that host over https. It never appears in an argv, the
container's config, a file or a log. ssh origins (``git@HOST:``, ``ssh://git@HOST/``) are rewritten to https for the
same host, so no ssh key reaches git. ``gh pr list`` / ``gh pr create`` stay on the host: they call the GitHub API
with the operator's token and execute no repository content.

Every ``docker`` command is built by a pure function (:func:`container_argv`, :func:`proxy_argv`), so tests check the
flags without Docker.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from agentteams.vulnscan import paths

NETWORK = "agentteams-vulnscan-tools-net"        # internal: no route out except through a proxy container
PROXY_PORT = 3128
PROXY_READY = "egress-proxy listening"
NAME_PREFIX = "agentteams-vulnscan-tools-"
HOME_IN = "/toolhome"
CRED_HELPER = "/usr/local/bin/git-credential-vulnscan"
WITH_TOKEN = "/usr/local/bin/vulnscan-with-token"
PROXY_SCRIPT = "/usr/local/lib/vulnscan/egress_proxy.py"
NPM_ENDPOINT = "registry.npmjs.org:443"          # fix.NPM_REGISTRY's host (a test keeps them equal)
INFRA_EXIT = (125, 126, 127)                     # docker could not run the container, or the command is missing
ENABLED_BY_DEFAULT = True                        # tests/conftest.py turns this off for the host-git test suite
DEFAULTS: dict[str, Any] = {"subprocesses": None, "image": "", "ssh_identity": ""}
OPT_OUT_WARNING = ("WARNING: confine.subprocesses is false (operator opt-out): git, npm and the server kit run on "
                   "this host unconfined")
#: Credential locations under the home directory that are never mounted (a server-kit run's key files excepted).
SECRET_DIRS = (".ssh", ".gnupg", ".aws", ".docker", ".kube", ".config/gh", ".config/agentteams/keys", ".netrc",
               ".git-credentials", ".npmrc", ".pypirc", "Library/Keychains")
_READ_ONLY_GIT = frozenset({"rev-parse", "log", "show", "diff", "ls-files", "ls-tree", "ls-remote", "merge-base",
                            "cat-file"})
_NETWORK_GIT = ("fetch", "push", "ls-remote")
_ENDPOINT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?:\d{1,5}")
_HTTPS = re.compile(r"https://(?:[^@/\s]+@)?([A-Za-z0-9.-]+)(?::(\d{1,5}))?/")
_SCP = re.compile(r"git@([A-Za-z0-9.-]+):")
_SSH = re.compile(r"ssh://git@([A-Za-z0-9.-]+)/")


class Unavailable(subprocess.SubprocessError):
    """The tools container cannot run the operation. Nothing ran on the host instead (fail closed).

    ``code`` is the :mod:`.docker_readiness` reason code when Docker, the image or its self-test is the cause, else "".
    """

    def __init__(self, *args: object, code: str = "") -> None:
        super().__init__(*args)
        self.code = code


# ---------------------------------------------------------------- configuration and scope

def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return the ``confine`` block merged over :data:`DEFAULTS`."""
    block = (cfg or {}).get("confine")
    return {**DEFAULTS, **(block if isinstance(block, dict) else {})}


def config_problem(cfg: dict[str, Any]) -> str:
    """Return why ``cfg["confine"]`` is malformed, or "" (absent or ``{}`` means the defaults)."""
    block = cfg.get("confine", {})
    if block is None:
        return ""
    if not isinstance(block, dict):
        return f"confine must be an object (got {repr(block)[:80]})"
    unknown = sorted(set(block) - set(DEFAULTS))
    if unknown:
        return f"confine has unknown keys {unknown} (known: {', '.join(DEFAULTS)})"
    if not isinstance(block.get("subprocesses", True), bool):
        return f"confine.subprocesses must be true or false (got {repr(block['subprocesses'])[:80]})"
    for key in ("image", "ssh_identity"):
        if not isinstance(block.get(key, ""), str):
            return f"confine.{key} must be a string"
    return ""


def enabled(cfg: dict[str, Any] | None) -> bool:
    """Return whether git, npm and the server kit must run in the tools container for this config."""
    value = settings(cfg)["subprocesses"]
    return ENABLED_BY_DEFAULT if value is None else bool(value)


def opted_out(cfg: dict[str, Any] | None) -> bool:
    """Return whether the operator explicitly set ``confine.subprocesses: false``."""
    return settings(cfg)["subprocesses"] is False


@dataclass(frozen=True)
class Confined:
    """The active confinement: the image to use and the roots under which worktrees may be written."""

    image: str
    scratch: tuple[Path, ...] = ()
    ssh_identity: str = ""


_ACTIVE: contextvars.ContextVar[Confined | None] = contextvars.ContextVar("vulnscan_confine", default=None)


def active() -> Confined | None:
    """Return the active confinement, or None (outside a scope, or opted out: subprocesses run on the host)."""
    return _ACTIVE.get()


@contextlib.contextmanager
def _activate(state: Confined | None) -> Iterator[Confined | None]:
    token = _ACTIVE.set(state)
    try:
        yield state
    finally:
        _ACTIVE.reset(token)


def scope(cfg: dict[str, Any], *, scratch: Sequence[Path] = ()) -> contextlib.AbstractContextManager[Confined | None]:
    """Return the context in which git, npm and the server kit run confined (opted out: on the host, with a warning).

    Args:
        cfg: Daily config (``confine`` block).
        scratch: Extra roots under which a linked worktree may be created and written (``<state>/worktrees`` always).

    Returns:
        A context manager.

    Raises:
        Unavailable: Confinement is on and Docker, the tools image or its passing self-test is missing.
    """
    if not enabled(cfg):
        if opted_out(cfg):
            print(OPT_OUT_WARNING, file=sys.stderr)
        return _activate(None)
    s = settings(cfg)
    tag = image_tag(s)
    why = unavailable_reason(tag)
    if why:
        raise Unavailable(f"tools container unavailable: {why}", code=getattr(why, "code", ""))
    roots = tuple(dict.fromkeys(Path(p).resolve() for p in (paths.state_dir() / "worktrees", *scratch)))
    return _activate(Confined(image=tag, scratch=roots, ssh_identity=str(s["ssh_identity"])))


def image_tag(s: dict[str, Any] | None = None) -> str:
    """Return the tools image tag: ``confine.image``, else ``agentteams-vulnscan-tools:<agentteams version>``."""
    if s and s.get("image"):
        return str(s["image"])
    try:
        ver = metadata.version("agentteams")
    except metadata.PackageNotFoundError:
        ver = "dev"
    return f"agentteams-vulnscan-tools:{ver}"


def selftest_record_path() -> Path:
    """Return where a passing ``tools-selftest`` is recorded (``<state>/tools-selftest.json``)."""
    return paths.state_dir() / "tools-selftest.json"


def unavailable_reason(tag: str) -> str:
    """Return why the tools container cannot be used (Docker, the image, its self-test), or "".

    The probe is :func:`.docker_readiness.tools_problem` (inside a daily run: that run's one check). The result is a
    ``docker_readiness.Reason``: a string that also carries its reason code (``.code``).
    """
    from agentteams.vulnscan import docker_readiness

    if os.getuid() == 0:
        return "the executor runs as root; the tools container never runs as root"
    return docker_readiness.tools_problem(
        tag, runner=lambda argv, timeout: _docker(argv, check=False, timeout=timeout))


# ---------------------------------------------------------------- docker

@dataclass(frozen=True)
class Mount:
    """A bind mount at the same path inside (``target`` "" ) or at ``target``."""

    source: Path
    target: str = ""
    writable: bool = False
    secret_ok: bool = False   # only a server-kit run's ssh key files and known_hosts

    @property
    def dest(self) -> str:
        return self.target or str(self.source)


def mount_problem(path: Path, *, secret_ok: bool = False) -> str:
    """Return why ``path`` may not be mounted into the tools container, or ""."""
    p = Path(path).resolve()
    s = str(p)
    if not p.is_absolute() or any(c in s for c in ',"\n\r'):
        return f"{s!r} cannot be mounted (a relative path, or one with a comma, quote or newline)"
    home = Path.home().resolve()
    if p == home or home.is_relative_to(p):
        return f"{p} is the home directory or an ancestor of it"
    if not secret_ok and any(p == home / d or p.is_relative_to(home / d) or (home / d).is_relative_to(p)
                             for d in SECRET_DIRS):
        return f"{p} is a credential location, or contains one"
    return ""


def _mount_args(mounts: Sequence[Mount]) -> list[str]:
    chosen: dict[str, Mount] = {}
    for m in mounts:
        why = mount_problem(m.source, secret_ok=m.secret_ok)
        if why or any(c in m.dest for c in ',"\n\r'):
            raise Unavailable(f"tools container unavailable: refusing to mount {m.dest!r}: {why or 'bad target'}")
        prev = chosen.get(m.dest)
        if prev is None or (m.writable and not prev.writable):   # the same path twice: rw wins over ro
            chosen[m.dest] = m
    out: list[str] = []
    for dest in sorted(chosen):
        m = chosen[dest]
        out += ["--mount", f"type=bind,src={Path(m.source).resolve()},dst={dest}" + ("" if m.writable else ",readonly")]
    return out


def container_argv(image: str, argv: Sequence[str], *, name: str, mounts: Sequence[Mount], env: dict[str, str],
                   network: str = "none", workdir: str = "/tmp", stdin: bool = False,
                   uid: int | None = None, gid: int | None = None) -> list[str]:
    """Return the ``docker run`` command for one confined operation (pure: builds, never runs).

    Raises:
        Unavailable: A mount is refused (:func:`mount_problem`).
    """
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    return ["docker", "run", "--rm", "--pull", "never", *(["-i"] if stdin else []), "--name", name, "--network", network,
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", f"{uid}:{gid}",
            "--pids-limit", "256", "--memory", "2g", "--tmpfs", "/tmp:rw,size=512m,mode=1777",
            "--tmpfs", f"{HOME_IN}:rw,size=1g,uid={uid},gid={gid},mode=0700", "-e", f"HOME={HOME_IN}",
            *_mount_args(mounts), "-w", workdir, *(x for k, v in sorted(env.items()) for x in ("-e", f"{k}={v}")),
            image, *argv]


def proxy_argv(image: str, name: str, egress: Sequence[str]) -> list[str]:
    """Return the ``docker run`` command for an operation's egress proxy (on the default bridge, plus :data:`NETWORK`)."""
    return ["docker", "run", "-d", "--rm", "--pull", "never", "--name", name, "--network", "bridge", "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", "65534:65534", "--pids-limit", "64", "--memory", "128m",
            image, "python3", "-I", PROXY_SCRIPT, "--listen", f"0.0.0.0:{PROXY_PORT}",
            *(x for e in egress for x in ("--allow", e))]


def proxy_env(proxy: str) -> dict[str, str]:
    """Return the environment that routes git (libcurl) and npm through ``proxy``."""
    url = f"http://{proxy}:{PROXY_PORT}"
    return {"HTTPS_PROXY": url, "https_proxy": url, "HTTP_PROXY": url, "http_proxy": url, "NO_PROXY": "",
            "no_proxy": "", "npm_config_https_proxy": url, "npm_config_proxy": url}


def flags_sha256(image_id: str) -> str:
    """Return the fingerprint of the container and proxy flags a passing ``tools-selftest`` vouches for."""
    blob = json.dumps({"container": container_argv("img", ["probe"], name="n", mounts=[Mount(Path("/w"), writable=True)],
                                                   env={"K": "v"}, network=NETWORK, stdin=True, uid=1000, gid=1000),
                       "proxy": proxy_argv("img", "p", ["h:1"]), "proxy_env": proxy_env("p"),
                       "image_id": image_id}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def new_name(op: str) -> str:
    """Return a fresh container name for ``op``."""
    return f"{NAME_PREFIX}{re.sub(r'[^a-z0-9-]', '-', op.lower())[:24]}-{os.urandom(4).hex()}"


def _docker(argv: list[str], *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unavailable(f"tools container unavailable: `{' '.join(argv[:3])}` failed: {exc}") from exc
    if check and proc.returncode:
        raise Unavailable(f"tools container unavailable: `{' '.join(argv[:3])}` failed: "
                          f"{(proc.stderr or proc.stdout).strip()[:300]}")
    return proc


def _quiet_rm(name: str) -> None:
    try:
        _docker(["docker", "rm", "-f", name], check=False, timeout=60)
    except Unavailable as exc:   # cleanup must not mask the operation's own outcome
        print(f"vulnscan: could not remove container {name}: {exc}", file=sys.stderr)


def image_id_of(tag: str) -> str:
    """Return the local image id of ``tag``, or "" (Docker unreachable or the image absent)."""
    try:
        return _docker(["docker", "image", "inspect", "-f", "{{.Id}}", tag], check=False, timeout=60).stdout.strip()
    except Unavailable:
        return ""


def ensure_network() -> None:
    """Create :data:`NETWORK` (``--internal``: no route out) when it does not exist; refuse an existing one that is not.

    Raises:
        Unavailable: A network of that name exists without ``--internal`` (someone else's, or tampered with).
    """
    probe = _docker(["docker", "network", "inspect", "--format", "{{.Internal}}", NETWORK], check=False)
    if probe.returncode:
        _docker(["docker", "network", "create", "--internal", NETWORK])
    elif (probe.stdout or "").strip() != "true":
        raise Unavailable(f"tools container unavailable: docker network {NETWORK} exists but is not --internal "
                          f"(remove it: docker network rm {NETWORK})")


def start_proxy(image: str, name: str, egress: Sequence[str]) -> None:
    """Start an egress proxy allowing exactly ``egress`` (``host:port``) and wait until it listens.

    Raises:
        Unavailable: An endpoint is malformed, or the proxy did not start.
    """
    bad = [e for e in egress if not _ENDPOINT.fullmatch(e)]
    if bad or not egress:
        raise Unavailable(f"tools container unavailable: invalid egress allowlist {bad or list(egress)}")
    ensure_network()
    _docker(proxy_argv(image, name, egress))
    try:
        _docker(["docker", "network", "connect", NETWORK, name])
        deadline = time.monotonic() + 20
        while True:
            logs = _docker(["docker", "logs", name], check=False)
            if PROXY_READY in (logs.stdout or "") + (logs.stderr or ""):
                break
            if time.monotonic() > deadline:
                raise Unavailable("tools container unavailable: the egress proxy did not start")
            time.sleep(0.2)
    except Unavailable:
        _quiet_rm(name)
        raise


def _identity_mounts() -> list[Mount]:
    """Return ``/etc/passwd`` and ``/etc/group`` naming the operator's uid (ssh needs a passwd entry)."""
    uid, gid = os.getuid(), os.getgid()
    d = paths.state_dir() / "tools"
    d.mkdir(parents=True, exist_ok=True)
    grp = "root:x:0:\nnogroup:x:65534:\n" + ("" if gid in (0, 65534) else f"vulnscan:x:{gid}:\n")
    files = {"passwd": ("root:x:0:0:root:/root:/usr/sbin/nologin\nnobody:x:65534:65534:nobody:/nonexistent:"
                        f"/usr/sbin/nologin\nvulnscan:x:{uid}:{gid}:vulnscan:{HOME_IN}:/usr/sbin/nologin\n"),
             "group": grp}
    for fname, text in files.items():
        if not (d / fname).is_file() or (d / fname).read_text(encoding="utf-8") != text:
            (d / fname).write_text(text, encoding="utf-8")
    return [Mount(d / "passwd", "/etc/passwd"), Mount(d / "group", "/etc/group")]


def run_confined(op: str, argv: Sequence[str], *, writable: Sequence[Path] = (), readonly: Sequence[Path] = (),
                 egress: Sequence[str] = (), env: dict[str, str] | None = None, timeout: float = 600,
                 stdin: str | bytes | None = None, text: bool = True, mounts: Sequence[Mount] = (),
                 proxy_name: str = "", image: str = "", workdir: str = "/tmp") -> subprocess.CompletedProcess:
    """Run ``argv`` in a fresh tools container and return its process (``args`` is ``argv``, not the docker command).

    Args:
        op: Short operation name (container name, messages).
        argv: The command inside the container.
        writable: Host paths bind-mounted read-write at the same path.
        readonly: Host paths bind-mounted read-only at the same path.
        egress: ``host:port`` endpoints the operation may reach (empty: ``--network none``).
        env: The container's whole environment besides ``HOME`` and the proxy variables (nothing is inherited).
        timeout: Seconds; on expiry the container is removed and :class:`subprocess.TimeoutExpired` re-raised.
        stdin: Text (or bytes) on the container's stdin (``-i``): how a token is handed over.
        text: Text or bytes output.
        mounts: Further mounts (other targets, the ssh files).
        proxy_name: Name for the egress proxy (a caller that must name it in a file passes it).
        image: Tools image (default: the active scope's).
        workdir: Working directory inside.

    Returns:
        The completed process.

    Raises:
        Unavailable: No image or scope, a refused mount, the proxy did not start, or docker could not run the command.
        subprocess.TimeoutExpired: The operation ran longer than ``timeout``.
    """
    st = active()
    image = image or (st.image if st else "")
    if not image:
        raise Unavailable("tools container unavailable: no confinement scope is active")
    name = new_name(op)
    run_env = dict(env or {})
    network, proxy = "none", ""
    all_mounts = [*(Mount(Path(p), writable=True) for p in writable), *(Mount(Path(p)) for p in readonly),
                  *mounts, *_identity_mounts()]
    cmd = container_argv(image, argv, name=name, mounts=all_mounts, env=run_env, workdir=workdir,
                         stdin=stdin is not None)   # refused mounts raise before anything starts
    if egress:
        proxy = proxy_name or new_name("egress")
        start_proxy(image, proxy, egress)
        network = NETWORK
        cmd = container_argv(image, argv, name=name, mounts=all_mounts, env={**run_env, **proxy_env(proxy)},
                             network=network, workdir=workdir, stdin=stdin is not None)
    try:
        try:
            proc = subprocess.run(cmd, input=stdin, capture_output=True, text=text, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            _quiet_rm(name)   # the client is gone; the container would keep running
            raise
        except OSError as exc:
            raise Unavailable(f"tools container unavailable: docker could not be started: {exc}") from exc
    finally:
        if proxy:
            _quiet_rm(proxy)
    if proc.returncode in INFRA_EXIT:
        err = proc.stderr if isinstance(proc.stderr, str) else (proc.stderr or b"").decode("utf-8", "replace")
        raise Unavailable(f"tools container unavailable: {op} could not run (docker exit {proc.returncode}): "
                          f"{err.strip()[:300]}")
    return subprocess.CompletedProcess(list(argv), proc.returncode, proc.stdout, proc.stderr)


# ---------------------------------------------------------------- git

@dataclass(frozen=True)
class GitLayout:
    """Where a directory's repository lives, found by reading ``.git`` files (git is not run)."""

    top: Path | None = None
    gitdir: Path | None = None
    common: Path | None = None
    linked: bool = False


def git_layout(cwd: Path) -> GitLayout:
    """Return the repository enclosing ``cwd`` (its top, git dir and common dir), or an empty layout."""
    cwd = Path(cwd).resolve()
    for d in (cwd, *cwd.parents):
        dot = d / ".git"
        if dot.is_dir():
            gitdir, linked = dot, False
        elif dot.is_file():
            text = dot.read_text(encoding="utf-8", errors="replace").strip()
            if not text.startswith("gitdir:"):
                return GitLayout()
            gitdir, linked = (d / text[len("gitdir:"):].strip()).resolve(), True
        else:
            continue
        common = gitdir
        if (gitdir / "commondir").is_file():
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        return GitLayout(d, gitdir.resolve(), common.resolve(), linked)
    return GitLayout()


def git_reads_only(args: Sequence[str]) -> bool:
    """Return whether a git command only reads the repository (its git dirs are then mounted read-only)."""
    if not args or args[0] in _READ_ONLY_GIT:
        return True
    rest = list(args[1:])
    if args[0] == "remote":
        return rest[:1] == ["get-url"]
    if args[0] == "worktree":
        return rest[:1] == ["list"]
    if args[0] == "config":
        return any(x in ("--get", "--get-all", "--get-regexp") for x in rest)
    if args[0] == "symbolic-ref":
        return len([x for x in rest if not x.startswith("-")]) == 1
    return False


def _under(p: Path, roots: Sequence[Path]) -> bool:
    p = Path(p).resolve()
    return any(p == r or p.is_relative_to(r) for r in roots)


def git_mounts(cwd: Path, args: Sequence[str], *, readonly: bool,
               scratch: Sequence[Path]) -> tuple[list[Path], list[Path], list[Mount]]:
    """Return (writable, read-only, extra) mounts for one git command run in ``cwd``.

    The worktree is writable only when it is a linked worktree under a scratch root; the git dirs only when the
    command writes. ``worktree add|remove|move`` gets the scratch root that holds its path (any other path is
    refused). The other registered worktrees' ``.git`` files are mounted read-only so that git, seeing them,
    never treats them as prunable.

    Raises:
        Unavailable: A worktree path outside the scratch roots.
    """
    lay = git_layout(cwd)
    rw: list[Path] = []
    ro: list[Path] = []
    if lay.top is None or lay.gitdir is None or lay.common is None:
        return rw, ([Path(cwd).resolve()] if Path(cwd).exists() else []), []
    writes = not readonly and not git_reads_only(args)
    (rw if writes and lay.linked and _under(lay.top, scratch) else ro).append(lay.top)
    for d in dict.fromkeys((lay.gitdir, lay.common)):
        if writes:
            rw.append(d)
        elif not _under(d, [lay.top]):
            ro.append(d)
    if writes and args[0] == "worktree" and len(args) > 1 and args[1] in ("add", "remove", "move"):
        for a in (x for x in args[2:] if x.startswith("/")):
            root = next((r for r in scratch if _under(Path(a), [r])), None)
            if root is None:
                raise Unavailable(f"tools container unavailable: worktree path {a} is outside the confined "
                                  "scratch roots")
            root.mkdir(parents=True, exist_ok=True)
            rw.append(root)
    extra: list[Mount] = []
    admin = lay.common / "worktrees"
    if writes and admin.is_dir():
        for gd in sorted(admin.glob("*/gitdir")):
            target = Path(gd.read_text(encoding="utf-8", errors="replace").strip())
            if target.is_absolute() and target.is_file() and not _under(target, rw + ro) \
                    and not mount_problem(target):
                extra.append(Mount(target))
    return rw, ro, extra


@dataclass(frozen=True)
class Remote:
    """How a confined git command reaches origin: a local path, or an https host through the proxy."""

    local: Path | None = None
    host: str = ""          # the credential host, as git names it (``host`` or ``host:port``)
    endpoint: str = ""      # ``host:port`` for the proxy
    rewrite: tuple[str, ...] = field(default_factory=tuple)


def classify_remote(url: str) -> Remote:
    """Return how origin ``url`` is reached from the tools container.

    Raises:
        Unavailable: Not a local path, an https URL, ``git@HOST:`` or ``ssh://git@HOST/``.
    """
    if url.startswith("/"):
        return Remote(local=Path(url))
    m = _HTTPS.match(url)
    if m:
        host, port = m.group(1).lower(), m.group(2)
        return Remote(host=f"{host}:{port}" if port else host, endpoint=f"{host}:{port or 443}")
    for rx, form in ((_SCP, "git@{}:"), (_SSH, "ssh://git@{}/")):
        m = rx.match(url)
        if m:
            host = m.group(1).lower()
            return Remote(host=host, endpoint=f"{host}:443", rewrite=(
                "-c", f"url.https://{host}/.insteadOf={form.format(m.group(1))}"))
    raise Unavailable(f"tools container unavailable: origin URL {url[:80]!r} cannot be reached from the tools "
                      "container (https, git@HOST: and ssh://git@HOST/ origins, or a local path)")


def gh_token(host: str) -> str:
    """Return the operator's GitHub token for ``host`` from ``gh auth token`` (host-side), or "" when there is none."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {"GH_PROMPT_DISABLED": "1",
                                                                               "NO_COLOR": "1"}
    try:
        proc = subprocess.run(["gh", "auth", "token", "--hostname", host.split(":", 1)[0]], capture_output=True,
                              text=True, env=env, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"vulnscan: no GitHub token for {host} ({type(exc).__name__}); git runs without one", file=sys.stderr)
        return ""
    tok = proc.stdout.strip()
    return tok if proc.returncode == 0 and re.fullmatch(r"[\x21-\x7e]{1,4096}", tok) else ""


def run_git(repo: Path, args: Sequence[str], *, hardening: Sequence[str], env: dict[str, str], push: bool = False,
            timeout: float = 600, text: bool = True, readonly: bool = False) -> subprocess.CompletedProcess:
    """Run ``git -C repo args`` hardened: in the tools container inside a scope, else on the host as before.

    Args:
        repo: Directory git runs in.
        args: git arguments (built by the executor; network commands already carry the pinned pack program).
        hardening: The executor's ``-c`` flags.
        env: The executor's git environment (only ``GIT_*`` variables reach the container).
        push: Credentials are needed: on the host the gh credential helper, in the container the stdin token.
        timeout: Seconds.
        text: Text or bytes output.
        readonly: A query (``query_git``): every mount read-only whatever the command.

    Returns:
        The completed process.

    Raises:
        Unavailable: Confined, and the container could not run it (never retried on the host).
    """
    st = active()
    if st is None:
        pre = [*hardening, *(["-c", "credential.helper=!gh auth git-credential"] if push else [])]
        return subprocess.run(["git", *pre, "-C", str(repo), *args], capture_output=True, text=text, env=env,
                              timeout=timeout, check=False)
    repo = Path(repo).resolve()
    if args and args[0] == "worktree":   # mounts are at resolved paths
        args = [str(Path(a).resolve()) if a.startswith("/") else a for a in args]
    rw, ro, extra = git_mounts(repo, args, readonly=readonly, scratch=st.scratch)
    env_in = {k: v for k, v in env.items() if k.startswith("GIT_")}
    gcfg = env_in.get("GIT_CONFIG_GLOBAL", "")
    if gcfg and gcfg != os.devnull:
        env_in["GIT_CONFIG_GLOBAL"] = str(Path(gcfg).resolve())
        ro.append(Path(gcfg))
    pre = [*hardening, "-c", "gc.auto=0", "-c", "maintenance.auto=false"]
    egress: tuple[str, ...] = ()
    stdin: str | bytes | None = None
    wrap: list[str] = []
    if args and args[0] in _NETWORK_GIT:
        url = run_git(repo, ("remote", "get-url", *(("--push",) if args[0] == "push" else ()), "origin"),
                      hardening=hardening, env=env, timeout=60, readonly=True).stdout.strip()
        remote = classify_remote(url)
        if remote.local is not None:
            if remote.local.exists():
                (rw if args[0] == "push" else ro).append(remote.local)
        else:
            egress, pre = (remote.endpoint,), [*pre, *remote.rewrite]
            if push:
                pre += ["-c", f"credential.helper={CRED_HELPER}"]
                env_in["VULNSCAN_GIT_HOST"] = remote.host
                wrap = [WITH_TOKEN]
                line = gh_token(remote.host) + "\n"
                stdin = line if text else line.encode()
    return run_confined("git", [*wrap, "git", *pre, "-C", str(repo), *args], writable=rw, readonly=ro, mounts=extra,
                        egress=egress, env=env_in, timeout=timeout, stdin=stdin, text=text)


# ---------------------------------------------------------------- npm

def scratch_parent() -> Path:
    """Return (creating it) the directory under which confined npm runs get their scratch copies."""
    d = paths.state_dir() / "tools-scratch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_npm(args: Sequence[str], *, workdir: Path, scratch_root: Path, env: dict[str, str],
            timeout: float = 600) -> subprocess.CompletedProcess:
    """Run ``npm args`` in the tools container: ``scratch_root`` writable, only the npm registry reachable.

    Raises:
        Unavailable: No scope is active or the container could not run.
        subprocess.TimeoutExpired: npm ran longer than ``timeout``.
    """
    if active() is None:
        raise Unavailable("tools container unavailable: no confinement scope is active")
    return run_confined("npm", ["npm", *args], writable=[scratch_root], egress=(NPM_ENDPOINT,), env=env,
                        timeout=timeout, workdir=str(Path(workdir).resolve()))


def data_dir() -> Path:
    """Return the packaged tools-image build context (``agentteams/data/vulnscan_tools``)."""
    return Path(str(resources.files("agentteams") / "data" / "vulnscan_tools"))
