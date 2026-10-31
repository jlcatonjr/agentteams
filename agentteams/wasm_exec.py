"""wasm_exec.py — the one place agentteams starts ``wasmtime`` (WASM sandbox plan P02).

A guest runs under a pinned compute-only grant (no preopen, empty environment, no sockets) with fuel, memory and
wall-clock limits; :func:`assert_grant_shape` refuses any argv but that exact shape plus a :class:`Grant`'s ``--dir``
pairs. wasmtime gets an empty environment, bounded stdio, its own process group, and runs nested in Seatbelt/bwrap
when one is usable (D6). wasmtime 36 has no read-only preopen (``::readonly`` is accepted and writable), so ``ro`` is
enforced by the nested profile (``read_only_mode="os"``) or a hardened per-run snapshot (``"snapshot"``, R7). Only
``os_confinement="nested"`` counts as enforced (R5). ``trap`` is diagnostic: the guest can forge wasmtime's stderr
(C-4); only the host-set ``host-timeout`` / ``output-cap`` may drive a decision.

Imports nothing from agentteams, not even lazily (R2): the ``agentteams_wasm`` bundle exec-loads this exact file.
wasmtime comes from an injected *resolver* (:class:`WasmResolver`; ``wasm_pins.pinned_resolver``), nesting from the
injected *profiles* module (``agentteams._sandbox_profiles``); both are required keywords. Stdlib only.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Protocol

# --- the pinned grant (canonical spec; P19 parity-tests it against baseAgent) ------------------------------------

#: The canonical wasmtime grant. ``python -m agentteams.wasm_exec --print-spec`` prints it as canonical JSON.
GRANT_SPEC: Mapping[str, Any] = {
    "schema": "agentteams-wasm-grant/v1",
    "wasmtime_major": 36,
    "seeded_from": "baseAgent experiments/battery/wasmtime_cmd_runner.mjs (2026-10-31 read)",
    "flag_order": ["-C", "-W", "-S"],
    "C": "cache=n",
    "S": "cli=y,inherit-env=n,inherit-network=n,tcp=n,udp=n,allow-ip-name-lookup=n,http=n",
    "W_variable": ["fuel", "timeout", "max-memory-size"],
    "W_fixed": {"module": "trap-on-grow-failure=y,max-instances=1,max-memories=1,max-tables=1",
                "component": "trap-on-grow-failure=y,max-instances=19,max-memories=2,max-tables=4"},
    "W_regex": r"^fuel=(\d+),timeout=(\d+)(ms|s),max-memory-size=(\d+),(.*)$",
    "limit_bounds": {"fuel": [1, 10**15], "max_memory_bytes": [65536, 2**32], "timeout_ms": [1, 3600000]},
    "defaults": {"fuel": 10_000_000_000, "max_memory_bytes": 67108864, "timeout": "120s"},
    "never": ["--dir (BA)", "--env", "--preload", "--allow-precompiled", "--config", "-S inherit-network=y"],
    "extensions": {
        "agentteams-mounts": "agentteams alone may append --dir <abs-real-host>::<guest> pairs after the six flags, "
                             "derived from Grant.mounts and runtime mounts, per P02 §5.2 (no '::' in host; closed "
                             "guest grammar /project/<rel>, /scratch, /src, /lib). BA's assertGrantFlags keeps "
                             "refusing --dir.",
    },
}
_SPEC_JSON = json.dumps(GRANT_SPEC, sort_keys=True, separators=(",", ":"))
#: sha256 of the canonical JSON ``--print-spec`` prints (without its trailing newline).
GRANT_SPEC_SHA256 = hashlib.sha256(_SPEC_JSON.encode("utf-8")).hexdigest()
_W_RE = re.compile(GRANT_SPEC["W_regex"], re.ASCII)

#: Inclusive bounds for each limit (``timeout_s`` derived from the spec's millisecond bounds).
_B = GRANT_SPEC["limit_bounds"]
LIMIT_BOUNDS: dict[str, tuple[int, int]] = {"fuel": (_B["fuel"][0], _B["fuel"][1]), "max_memory_bytes": (
    _B["max_memory_bytes"][0], _B["max_memory_bytes"][1]), "timeout_s": (1, _B["timeout_ms"][1] // 1000)}

# --- nesting constants (a parity test pins them to confinement.PROTECTED_REL / _sandbox_emit.SIGNING_KEY_DIR) -----
NEST_PROTECTED_REL = (".agentteams", ".agentteams-queue", ".agentteams-status", ".git", ".claude", ".goose", ".codex",
                      ".grok", ".github")
NEST_KEY_DIR = "~/.config/agentteams/keys"
NEST_DEV_WRITES = ("/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/dtracehelper")
#: Credential stores the nested wasmtime may not read (R5).
NEST_DENY_READS = ("~/.ssh", "~/.aws", "~/.config/gh", "~/.config/gcloud", "~/.netrc", "~/Library/Keychains")

#: R8: files a guest may never place where an agent harness would load them as instructions.
REFUSED_COPY_OUT_NAMES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", ".cursorrules")
#: The host of a one-off ``/scratch`` that lives (and dies) in the run directory.
RUN_SCRATCH = "@run-scratch"
#: sha256 of ``data/wasm_deny_probe.wasm`` (built from tests/fixtures/wasm/wasm_deny_probe.wat with ``jco parse``).
DENY_PROBE_SHA256 = "dbcb3891225e2ce1a9145bd191d7a68afaed687b6572d88c7918baf6e7e7b365"

MAX_MODULE_BYTES = 512 << 20
MAX_STDIN_BYTES = 8 << 20
MAX_ARGS, MAX_ARGS_BYTES = 256, 64 << 10
MAX_SRC_BYTES = 8 << 20
SNAPSHOT_MAX_BYTES, SNAPSHOT_MAX_ENTRIES = 64 << 20, 10_000
DEFAULT_COMPILE_GRACE_S, MAX_COMPILE_GRACE_S = 15, 120
POST_KILL_DRAIN_S = 5.0

_CORE_HEADER = b"\0asm\x01\0\0\0"
_COMPONENT_HEADER = b"\0asm\x0d\0\x01\0"
_GUEST_RE = re.compile(r"^/(project(/[A-Za-z0-9._-]+)+|scratch|lib(/[A-Za-z0-9._-]+)*|src)$", re.ASCII)
_ARGV0_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$", re.ASCII)
_AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$", re.ASCII)
_SRC_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", re.ASCII)
_VERSION_RE = re.compile(r"^wasmtime (\d+\.\d+\.\d+) ", re.ASCII)
_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_BAD_HOST_CHARS = ('"', "\n", "\r", "\0") + (() if os.name == "nt" else ("\\",))
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CONTROL_PLANE = (".agentteams", "wasm")

OsConfine = Literal["auto", "require", "inherited", "off"]


class WasmExecError(Exception):
    """A refusal: a bad grant, argv, module, path or copy, or an ``os_confine`` that can't be met."""


# --- the grant ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Limits:
    """Engine limits for one run; each must lie inside :data:`LIMIT_BOUNDS`."""

    fuel: int
    max_memory_bytes: int
    timeout_s: int

    def __post_init__(self) -> None:
        for name, (lo, hi) in LIMIT_BOUNDS.items():
            value = getattr(self, name)
            if type(value) is not int or not lo <= value <= hi:
                raise WasmExecError(f"limit {name}={value!r} outside [{lo}, {hi}]")


#: The spec's defaults: fuel 1e10, 64 MiB, 120 s (baseAgent's pinned limits).
DEFAULT_LIMITS = Limits(fuel=GRANT_SPEC["defaults"]["fuel"], max_memory_bytes=GRANT_SPEC["defaults"]["max_memory_bytes"],
                        timeout_s=int(GRANT_SPEC["defaults"]["timeout"].rstrip("s")))


def _check_host(host: str) -> None:
    if host == RUN_SCRATCH:
        return
    # wasmtime splits --dir HOST::GUEST[::opts] on ':' runs, so ANY ':' in the host path is refused, not only
    # '::' (a single ':' can still shift the split on some releases). The one exception is a Windows drive prefix.
    rest = host[2:] if os.name == "nt" and isinstance(host, str) and _WIN_DRIVE.match(host) else host
    if not isinstance(host, str) or not os.path.isabs(host) or ":" in rest or host.startswith("-"):
        raise WasmExecError(f"mount host {host!r} must be an absolute path without ':'; refused")
    if any(c in host for c in _BAD_HOST_CHARS) or any(ord(c) < 0x20 or ord(c) == 0x7F for c in host):
        raise WasmExecError(f"mount host {host!r} contains a character that can't be passed safely; refused")


@dataclass(frozen=True)
class Mount:
    """One preopened directory: ``host`` (absolute, real) seen by the guest as ``guest``.

    Guest names (R9): ``/project/<rel>`` (ro), ``/scratch`` (rw), ``/src`` and ``/lib[/...]`` (ro, runtime only).
    """

    host: str
    guest: str
    mode: Literal["ro", "rw"]

    def __post_init__(self) -> None:
        _check_host(self.host)
        if not isinstance(self.guest, str) or not _GUEST_RE.match(self.guest) or any(
                seg in (".", "..") for seg in self.guest.split("/")):
            raise WasmExecError(f"guest name {self.guest!r} is not /project/<rel>, /scratch, /src or /lib[/...]")
        if self.mode not in ("ro", "rw"):
            raise WasmExecError(f"mount mode {self.mode!r} must be 'ro' or 'rw'")
        if (self.mode == "rw") != (self.guest == "/scratch"):
            raise WasmExecError(f"{self.guest} must be {'rw' if self.guest == '/scratch' else 'ro'}")
        if self.host == RUN_SCRATCH and self.guest != "/scratch":
            raise WasmExecError(f"{RUN_SCRATCH} may only back /scratch")


@dataclass(frozen=True)
class Grant:
    """What a guest may touch: its ``mounts`` (``/project/<rel>`` ro, at most one ``/scratch`` rw) and ``limits``."""

    mounts: tuple[Mount, ...]
    limits: Limits

    def __post_init__(self) -> None:
        if not isinstance(self.mounts, tuple) or not all(isinstance(m, Mount) for m in self.mounts):
            raise WasmExecError("Grant.mounts must be a tuple of Mount")
        if not isinstance(self.limits, Limits):
            raise WasmExecError("Grant.limits must be Limits")
        if any(m.guest == "/src" or m.guest.startswith("/lib") for m in self.mounts):
            raise WasmExecError("a grant may not mount /src or /lib (runtime mounts only)")
        guests = [m.guest for m in self.mounts]
        if len(set(guests)) != len(guests):
            raise WasmExecError("a guest name may be mounted once")


#: The default grant: no mounts, :data:`DEFAULT_LIMITS` (baseAgent's exact compute-only grant).
COMPUTE_ONLY = Grant(mounts=(), limits=DEFAULT_LIMITS)


def grant_flags(limits: Limits, *, profile: str = "module") -> list[str]:
    """The six pinned flag elements for *limits*.

    Args:
        limits: The engine limits.
        profile: ``"module"`` or ``"component"`` (the latter for baseAgent parity only).

    Returns:
        ``["-C", C, "-W", W, "-S", S]``.

    Raises:
        WasmExecError: For an unknown profile.
    """
    if profile not in GRANT_SPEC["W_fixed"]:
        raise WasmExecError(f"unknown grant profile {profile!r}")
    return ["-C", GRANT_SPEC["C"], "-W", f"fuel={limits.fuel},timeout={limits.timeout_s}s,max-memory-size="
            f"{limits.max_memory_bytes},{GRANT_SPEC['W_fixed'][profile]}", "-S", GRANT_SPEC["S"]]


def _check_args(args: Sequence[str]) -> list[str]:
    out = list(args)
    if len(out) > MAX_ARGS or not all(isinstance(a, str) and "\0" not in a for a in out):
        raise WasmExecError(f"guest args must be at most {MAX_ARGS} strings without NUL")
    if sum(len(a.encode("utf-8", "surrogatepass")) for a in out) > MAX_ARGS_BYTES:
        raise WasmExecError(f"guest args exceed {MAX_ARGS_BYTES} bytes")
    return out


def build_argv(wasmtime: str, module: str, args: Sequence[str], grant: Grant, *,
               runtime_mounts: tuple[Mount, ...] = (), argv0: str | None = None) -> list[str]:
    """Build the full wasmtime argv and check it with :func:`assert_grant_shape`.

    Args:
        wasmtime: Absolute path of the wasmtime binary.
        module: Absolute path of the core module.
        args: Guest arguments (after ``--`` and the module, so wasmtime never parses them).
        grant: The grant; its mounts' hosts are the paths actually preopened.
        runtime_mounts: Interpreter mounts (``/lib...``, ``/src``), preopened first.
        argv0: The guest's ``argv[0]`` (default: module basename without ``.wasm``).

    Returns:
        ``[wasmtime, "run", <6 flags>, ("--dir", "host::guest")*, "--argv0", argv0, "--", module, *args]``.

    Raises:
        WasmExecError: When the result is not the pinned shape.
    """
    name = argv0 if argv0 is not None else _argv0(module)
    argv = [wasmtime, "run", *grant_flags(grant.limits)]
    for m in (*runtime_mounts, *grant.mounts):
        argv += ["--dir", f"{m.host}::{m.guest}"]
    argv += ["--argv0", name, "--", module, *_check_args(args)]
    assert_grant_shape(argv, grant, runtime_mounts=runtime_mounts)
    return argv


def assert_grant_shape(argv: Sequence[str], grant: Grant | None, *, runtime_mounts: tuple[Mount, ...] = (),
                       profile: str = "module") -> None:
    """Refuse any wasmtime argv that is not exactly the pinned shape.

    With a :class:`Grant`, the flags must equal those built from ``grant.limits`` and the ``--dir`` pairs exactly
    *runtime_mounts* then ``grant.mounts``. With ``grant=None`` (baseAgent parity) the limits are read back and
    bound-checked, ``<n>ms`` timeouts pass and any ``--dir`` is refused, as baseAgent's ``assertGrantFlags``.

    Args:
        argv: ``[wasmtime, "run", ...]``.
        grant: The grant the argv must carry, or None for parity mode.
        runtime_mounts: The runtime mounts the argv must carry first (grant mode).
        profile: ``"module"``; ``"component"`` is accepted in parity mode only.

    Returns:
        None.

    Raises:
        WasmExecError: Naming the first element that differs from the pinned value.
    """
    a = list(argv)
    if len(a) < 12 or not all(isinstance(x, str) for x in a) or a[1] != "run":
        raise WasmExecError("refused wasmtime argv: not [wasmtime, 'run', <6 flags>, ..., '--argv0', n, '--', m]")
    if grant is not None and profile != "module":
        raise WasmExecError("component-model guests are not supported in v1")
    want = grant_flags(grant.limits if grant else DEFAULT_LIMITS, profile=profile)
    m = _W_RE.match(a[5]) if a[2:5] == want[:3] else None
    if m is not None:
        tms = int(m[2]) * (1000 if m[3] == "s" else 1)
        for label, v, key in (("fuel", m[1], "fuel"), ("timeout", tms, "timeout_ms"),
                              ("max-memory-size", m[4], "max_memory_bytes")):
            if not _B[key][0] <= int(v) <= _B[key][1]:
                raise WasmExecError(f"refused wasmtime argv: {label} outside {_B[key]}")
        if grant is None:
            want[3] = f"fuel={m[1]},timeout={m[2]}{m[3]},max-memory-size={m[4]},{GRANT_SPEC['W_fixed'][profile]}"
    for k in range(6):
        if a[2 + k] != want[k]:
            raise WasmExecError(f"refused wasmtime argv: element {2 + k} is {a[2 + k]!r}, pinned {want[k]!r}")
    i, dirs = 8, []
    while i + 1 < len(a) and a[i] == "--dir":
        dirs.append(a[i + 1])
        i += 2
    if len(a) < i + 4 or a[i] != "--argv0" or not _ARGV0_RE.match(a[i + 1]) or a[i + 2] != "--":
        raise WasmExecError(f"refused wasmtime argv: element {i} must start '--argv0 <name> -- <module>'")
    if not os.path.isabs(a[i + 3]):
        raise WasmExecError("refused wasmtime argv: the module path must be absolute")
    _check_args(a[i + 4:])
    if grant is None:
        if dirs:
            raise WasmExecError("refused wasmtime argv: --dir is not allowed in parity mode")
        return
    want_dirs = [f"{mt.host}::{mt.guest}" for mt in (*runtime_mounts, *grant.mounts)]
    if dirs != want_dirs:
        raise WasmExecError(f"refused wasmtime argv: --dir pairs {dirs!r} differ from the grant's {want_dirs!r}")
    for mt in (*runtime_mounts, *grant.mounts):
        if mt.host == RUN_SCRATCH:
            raise WasmExecError("refused wasmtime argv: an unmaterialised run scratch")


# --- prepare (pure validation) --------------------------------------------------------------------------------------

def _argv0(module: str) -> str:
    name = os.path.basename(module)
    name = name[:-5] if name.endswith(".wasm") else name
    return name if _ARGV0_RE.match(name) else "guest"


def _hash_module(path: str) -> str:
    """Open *path* without following a final symlink, check the core-module header, return its sha256."""
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError as exc:
        raise WasmExecError(f"module {path!r} could not be opened ({exc.strerror}); a symlink is refused") from exc
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_MODULE_BYTES:
            raise WasmExecError(f"module {path!r} must be a regular file of at most {MAX_MODULE_BYTES} bytes")
        head = fh.read(8)
        if head == _COMPONENT_HEADER:
            raise WasmExecError("component-model guests are not supported in v1")
        if head != _CORE_HEADER:
            raise WasmExecError(f"{path!r} is not a WebAssembly core module (binary '\\0asm' v1 header required)")
        digest = hashlib.sha256(head)
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Prepared:
    """A validated module + grant, ready for :func:`run` / :func:`materialize` (no filesystem writes yet)."""

    module: str
    module_sha256: str
    grant: Grant
    runtime_mounts: tuple[Mount, ...]
    src_files: tuple[tuple[str, bytes], ...]
    argv0: str
    compile_grace_s: int = DEFAULT_COMPILE_GRACE_S


def prepare(module: str | Path, *, grant: Grant, runtime_mounts: tuple[Mount, ...] = (),
            src_files: Mapping[str, bytes] | None = None, compile_grace_s: int = DEFAULT_COMPILE_GRACE_S
            ) -> Prepared:
    """Validate a module, its grant, its runtime mounts and its private source files.

    Args:
        module: Path of a binary core module (``.wat`` text and components are refused).
        grant: The agent's grant.
        runtime_mounts: ``ro`` ``/lib[/...]`` mounts; hosts must lie inside the module's directory (checked on use).
        src_files: Guest source files, written to a private read-only ``/src`` per run.
        compile_grace_s: Seconds added to the outer timeout for compilation (at most 120).

    Returns:
        A :class:`Prepared`.

    Raises:
        WasmExecError: On any refusal.
    """
    if not isinstance(grant, Grant):
        raise WasmExecError("grant must be a Grant")
    for m in runtime_mounts:
        if not isinstance(m, Mount) or m.mode != "ro" or not m.guest.startswith("/lib"):
            raise WasmExecError(f"runtime mount {m!r} must be a read-only /lib[/...] Mount")
    guests = [m.guest for m in (*runtime_mounts, *grant.mounts)] + (["/src"] if src_files else [])
    if len(set(guests)) != len(guests):
        raise WasmExecError("a guest name may be mounted once")
    files = tuple(sorted((src_files or {}).items()))
    if any(not isinstance(n, str) or not _SRC_NAME_RE.match(n) or not isinstance(b, bytes) for n, b in files):
        raise WasmExecError("src_files names must match [A-Za-z0-9][A-Za-z0-9._-]{0,63} with bytes contents")
    if sum(len(b) for _, b in files) > MAX_SRC_BYTES:
        raise WasmExecError(f"src_files exceed {MAX_SRC_BYTES} bytes")
    if type(compile_grace_s) is not int or not 0 <= compile_grace_s <= MAX_COMPILE_GRACE_S:
        raise WasmExecError(f"compile_grace_s must be an int in [0, {MAX_COMPILE_GRACE_S}]")
    path = os.path.abspath(str(module))
    sha = _hash_module(path)
    return Prepared(module=path, module_sha256=sha, grant=grant, runtime_mounts=tuple(runtime_mounts),
                    src_files=files, argv0=_argv0(path), compile_grace_s=compile_grace_s)


# --- results, traps, copy-out ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class WasmResult:
    """The outcome of one run. ``stdout`` / ``stderr`` are untrusted guest bytes (C-4); ``trap`` is diagnostic."""

    exit_code: int
    stdout: bytes
    stderr: bytes
    trap: str | None
    os_confinement: Literal["nested", "inherited", "none"]
    truncated: bool
    os_reason: str
    read_only_mode: Literal["none", "os", "snapshot"]
    wall_s: float
    module_sha256: str


#: (stderr text, label), as measured on wasmtime 36.0.16 (plan §4 V7); first match wins.
_TRAP_TEXTS = (("all fuel consumed by WebAssembly", "fuel"), ("wasm trap: interrupt", "timeout"),
               ("forcing trap when growing memory to", "memory"))


def classify_trap(exit_code: int, stderr_tail: bytes, *, host_label: str | None = None) -> str | None:
    """Map an exit to a diagnostic trap label (texts measured on wasmtime 36.0.16, 2026-10-31).

    Args:
        exit_code: wasmtime's exit code.
        stderr_tail: Its stderr; only the last 4 KiB is scanned.
        host_label: ``"host-timeout"`` / ``"output-cap"`` when the host killed the run (wins).

    Returns:
        ``host-timeout``, ``output-cap``, ``fuel``, ``timeout``, ``memory``, ``trap:<text>``, ``wasmtime-error``
        or None.

    Raises:
        Nothing.
    """
    if host_label or exit_code == 0:
        return host_label or None
    tail = stderr_tail[-4096:].decode("utf-8", "replace")
    for text, label in _TRAP_TEXTS:
        if text in tail:
            return label
    found = re.search(r"wasm trap: ([^\n]*)", tail)
    return "trap:" + found[1][:60] if found else "wasmtime-error" if "Error: " in tail else None


def copy_out_refused(name: str) -> bool:
    """Whether a guest-written file may not be copied out of scratch (R8): an instruction file a harness loads.

    Args:
        name: The file name (only the basename is considered; case-insensitive, as APFS/NTFS are).

    Returns:
        True for ``CLAUDE.md``, ``AGENTS.md``, ``GEMINI.md``, ``.cursorrules`` and ``*.agent.md``.

    Raises:
        Nothing.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return base in {n.lower() for n in REFUSED_COPY_OUT_NAMES} or base.endswith(".agent.md")


# --- resolver protocol (provisioning is injected; see wasm_pins.pinned_resolver) --------------------------------

class WasmResolver(Protocol):
    """What :func:`run` needs from provisioning (``wasm_pins.PinnedResolver``, P03 ``wasm_runtime.Resolver``).

    ``resolve`` re-hashes on every call; errors subclassing ``OSError``/``ValueError``/``LookupError``/``RuntimeError``
    become :class:`WasmExecError`, others propagate (still fail-closed). An optional ``expected_sha256(component)``
    is checked against verified copies.
    """

    def resolve(self, component: str) -> Path:
        """Return the verified entry file of *component*."""

    def pinned_version(self, component: str) -> str:
        """Return the pinned version of *component*."""


def _sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: os.read(fd, 1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _resolve(resolver: WasmResolver, component: str) -> str:
    try:
        return os.path.realpath(str(resolver.resolve(component)))
    except (OSError, ValueError, LookupError, RuntimeError) as exc:
        raise WasmExecError(f"could not resolve {component}: {exc}") from exc


# --- materialize: run dir, verified copies, mounts, snapshots, nesting ----------------------------------------------

_SESSION_HINT = ("cannot create {what} under .agentteams/wasm (are you inside a session sandbox? run through the "
                 "runner or the agentteams_wasm server)")


def _control_dir(root: str, *parts: str, what: str = "verified copies") -> str:
    """Create (0o700) or check each directory of ``root/parts``; refuse a symlink or non-directory anywhere."""
    path = root
    for part in parts:
        path = os.path.join(path, part)
        try:
            if not os.path.lexists(path):
                with contextlib.suppress(FileExistsError):  # a concurrent run created it first; lstat checks it
                    os.mkdir(path, 0o700)
            st = os.lstat(path)
        except OSError as exc:
            raise WasmExecError(_SESSION_HINT.format(what=what) + f": {exc}") from exc
        if not stat.S_ISDIR(st.st_mode):
            raise WasmExecError(f"{path} is not a real directory (symlink?); refused")
    return path


def _within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _in_install_area(root: str, path: str) -> bool:
    """True for ``<root>/.agentteams/wasm/<x>/...`` other than ``exec/`` and ``scratch/`` (R3′ in-place area)."""
    rel = os.path.relpath(path, root).split(os.sep)
    return rel[:2] == list(_CONTROL_PLANE) and len(rel) > 3 and rel[2] not in ("exec", "scratch")


def _copy_verified(src: str, dst: str, mode: int) -> str:
    """Copy *src* to a new *dst* (``O_EXCL``, no symlink following); re-hash the copy; return the sha256."""
    try:
        sfd = os.open(src, os.O_RDONLY | _NOFOLLOW)
        dfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
    except OSError as exc:
        raise WasmExecError(f"verified copy of {src} failed: {exc}") from exc
    digest = hashlib.sha256()
    try:
        if not stat.S_ISREG(os.fstat(sfd).st_mode):
            raise WasmExecError(f"{src} is not a regular file")
        for chunk in iter(lambda: os.read(sfd, 1 << 20), b""):
            digest.update(chunk)
            os.write(dfd, chunk)
    finally:
        os.close(sfd)
        os.close(dfd)
    os.chmod(dst, mode)
    rfd = os.open(dst, os.O_RDONLY | _NOFOLLOW)
    try:
        if _sha256_fd(rfd) != digest.hexdigest():
            raise WasmExecError(f"verified copy of {src} differs from its source")
    finally:
        os.close(rfd)
    return digest.hexdigest()


def _remove_tree(path: str) -> None:
    """Remove a run dir: permissions restored on real directories only; nothing behind a symlink is touched."""
    if not os.path.isdir(path) or os.path.islink(path):
        return
    for dirpath, dirnames, _ in os.walk(path, followlinks=False):
        for d in [dirpath, *(os.path.join(dirpath, n) for n in dirnames)]:
            if stat.S_ISDIR(os.lstat(d).st_mode):
                os.chmod(d, 0o700)
    shutil.rmtree(path, ignore_errors=True)
    if os.path.lexists(path):
        print(f"WASM SANDBOX: could not remove run directory {path}", file=sys.stderr)


def _check_mount_host(root: str, m: Mount, scratch: str | None, inside: str | None = None) -> None:
    """A ``/project`` mount: a real directory in the project, not its root, clear of protected paths and keys.
    A runtime mount (*inside* given): a real directory inside the module's directory. Neither may touch scratch."""
    real = os.path.realpath(m.host)
    if real != os.path.normpath(m.host) or not os.path.isdir(real):
        raise WasmExecError(f"{m.guest}: host {m.host} must be an existing real directory (no symlinks)")
    if inside is not None and not _within(real, inside):
        raise WasmExecError(f"runtime mount {m.host} must lie inside {inside}")
    if inside is None:
        keys = os.path.realpath(os.path.expanduser(NEST_KEY_DIR))
        if not _within(real, root) or real == root:
            raise WasmExecError(f"{m.guest}: host {m.host} must be inside the project, not its root")
        for prot in [os.path.join(root, p) for p in NEST_PROTECTED_REL] + [keys]:
            if _within(real, prot) or _within(prot, real):
                raise WasmExecError(f"{m.guest}: host {m.host} overlaps the protected {prot}; refused")
    if scratch and (_within(real, scratch) or _within(scratch, real)):
        raise WasmExecError(f"{m.guest}: a read mount may not overlap the scratch directory")


def _spawn_env() -> dict[str, str]:
    """The whole environment wasmtime gets: empty on POSIX, ``SYSTEMROOT`` only on Windows (R9)."""
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        return {"SYSTEMROOT": os.environ["SYSTEMROOT"]}
    return {}


@dataclass(frozen=True)
class Launch:
    """A spawnable wasmtime run (for callers that own the ``Popen``, e.g. the runner); call ``cleanup()`` after."""

    argv: list[str]
    env: dict[str, str]
    cwd: str
    run_dir: str | None
    cleanup: Callable[[], None]
    os_confinement: Literal["nested", "inherited", "none"]
    os_reason: str
    read_only_mode: Literal["none", "os", "snapshot"]
    timeout_s: int
    module_sha256: str


def _nesting(os_confine: str, profiles: ModuleType) -> tuple[str | None, str, str]:
    if os_confine not in ("auto", "require", "inherited", "off"):
        raise WasmExecError(f"os_confine {os_confine!r} must be auto, require, inherited or off")
    if os_confine in ("inherited", "off"):
        return None, "inherited" if os_confine == "inherited" else "none", os_confine
    kind = profiles.available()
    if kind in ("seatbelt", "bwrap"):
        return kind, "nested", f"nested:{kind}"
    if os_confine == "require":
        raise WasmExecError(f"os_confine='require' but no OS sandbox is usable here ({sys.platform})")
    return None, "none", f"unavailable:{sys.platform}"


def materialize(prepared: Prepared, args: Sequence[str], *, project_root: str | Path, os_confine: OsConfine,
                resolver: WasmResolver, profiles: ModuleType, control_plane_protected: bool = False) -> Launch:
    """Turn a :class:`Prepared` into a spawnable, verified, optionally OS-nested wasmtime argv.

    wasmtime and the module run in place, re-hashed now, only when *control_plane_protected* (the caller knows R1
    write-denies ``.agentteams/`` to sessions) and they lie in its install area (R3′). Otherwise each is copied into
    ``<project>/.agentteams/wasm/exec/<run-id>/bin`` and the copy re-hashed; never TMPDIR.

    Args:
        prepared: From :func:`prepare`.
        args: Guest arguments.
        project_root: The project root.
        os_confine: ``auto`` / ``require`` / ``inherited`` (the P08 launcher only) / ``off``.
        resolver: Provisioning (:class:`WasmResolver`).
        profiles: The ``agentteams._sandbox_profiles`` module (or the bundle's exec-loaded copy).
        control_plane_protected: See above.

    Returns:
        A :class:`Launch`; the caller must call ``cleanup()``.

    Raises:
        WasmExecError: On any refusal, including a run dir that cannot be created.
    """
    root = os.path.realpath(str(project_root))
    if not os.path.isdir(root):
        raise WasmExecError(f"project root {project_root} is not a directory")
    kind, confinement, reason = _nesting(os_confine, profiles)
    state: dict[str, str] = {}

    def run_dir(*sub: str, what: str = "verified copies") -> str:
        if "dir" not in state:
            state["dir"] = _control_dir(root, *_CONTROL_PLANE, "exec", secrets.token_hex(8), what=what)
        return _control_dir(state["dir"], *sub, what=what)

    def cleanup() -> None:
        if "dir" in state:
            _remove_tree(state["dir"])

    done = False
    try:
        if kind is not None:
            run_dir()
        wasmtime = _resolve(resolver, "wasmtime")
        if not (control_plane_protected and _in_install_area(root, wasmtime)):
            got = _copy_verified(wasmtime, os.path.join(run_dir("bin"), "wasmtime"), 0o500)
            expected = getattr(resolver, "expected_sha256", None)
            if expected is not None and got != expected("wasmtime"):
                raise WasmExecError("wasmtime changed between resolve and copy")
            wasmtime = os.path.join(run_dir("bin"), "wasmtime")
        module = os.path.realpath(prepared.module)
        if control_plane_protected and _in_install_area(root, module):
            got = _hash_module(module)
        else:
            got, module = _copy_verified(module, os.path.join(run_dir("bin"), "module.wasm"), 0o400), \
                os.path.join(run_dir("bin"), "module.wasm")
        if got != prepared.module_sha256:
            raise WasmExecError("module changed since prepare")
        scratch: str | None = None
        for m in prepared.grant.mounts:
            if m.guest == "/scratch" and m.host == RUN_SCRATCH:
                scratch = run_dir("scratch", what="a run scratch")
            elif m.guest == "/scratch":
                rel = os.path.relpath(os.path.abspath(m.host), root).split(os.sep)
                if rel[:3] != [*_CONTROL_PLANE, "scratch"] or len(rel) != 4 or not _AGENT_RE.match(rel[3]):
                    raise WasmExecError(f"/scratch host {m.host} must be <project>/.agentteams/wasm/scratch/<agent>")
                scratch = _control_dir(root, *rel, what="the agent scratch")
        for m in prepared.grant.mounts:
            if m.guest != "/scratch":
                _check_mount_host(root, m, scratch)
        for m in prepared.runtime_mounts:
            _check_mount_host(root, m, scratch, inside=os.path.dirname(os.path.realpath(prepared.module)))
        runtime, mounts = list(prepared.runtime_mounts), list(prepared.grant.mounts)
        if prepared.src_files:
            src = run_dir("src", what="guest source")
            for name, data in prepared.src_files:  # nested: left writable, so the OS profile alone must deny writes
                fd = os.open(os.path.join(src, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                             0o600 if kind else 0o400)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
            os.chmod(src, 0o700 if kind else 0o500)
            runtime.append(Mount(src, "/src", "ro"))
        ro = sum(1 for m in (*runtime, *mounts) if m.mode == "ro")
        read_only_mode: Literal["none", "os", "snapshot"] = "none" if not ro else "os" if kind else "snapshot"
        if read_only_mode == "snapshot":
            budget, snapdir, n = [SNAPSHOT_MAX_BYTES, SNAPSHOT_MAX_ENTRIES], run_dir("snap", what="snapshots"), 0
            for group in (runtime, mounts):
                for i, m in enumerate(group):
                    if m.mode == "ro" and m.guest != "/src":
                        n, dst = n + 1, os.path.join(snapdir, str(n))
                        try:
                            profiles.snapshot_tree(os.path.realpath(m.host), dst, budget)
                        except (profiles.ConfinementError, OSError) as exc:
                            raise WasmExecError(f"snapshot of {m.guest}: {exc}") from exc
                        group[i] = Mount(dst, m.guest, "ro")
        mounts = [Mount(scratch, m.guest, m.mode) if m.guest == "/scratch" and scratch else m for m in mounts]
        grant = Grant(mounts=tuple(mounts), limits=prepared.grant.limits)
        argv = build_argv(wasmtime, module, args, grant, runtime_mounts=tuple(runtime), argv0=prepared.argv0)
        cwd = state.get("dir", "/")
        if kind is not None:
            common: dict[str, Any] = dict(root=Path(root), write_roots=[], tmp_dir=Path(run_dir("tmp")),
                                          protected_rel=NEST_PROTECTED_REL, key_dir=NEST_KEY_DIR,
                                          deny_reads=NEST_DENY_READS, deny_network=True)
            try:
                if kind == "seatbelt":
                    argv = ["/usr/bin/sandbox-exec", "-p", profiles.seatbelt_profile(
                        exec_paths=[], exec_literals=[wasmtime], dev_writes=NEST_DEV_WRITES,
                        late_write_allows=[scratch] if scratch else [], **common), *argv]
                else:
                    argv = [*profiles.bwrap_argv(cwd=Path(cwd), late_binds=[scratch] if scratch else [], **common),
                            "--", *argv]
            except profiles.ConfinementError as exc:
                raise WasmExecError(f"OS nesting refused: {exc}") from exc
        elif confinement == "none":
            print(f"WASM SANDBOX: wasmtime runs without OS confinement ({reason})", file=sys.stderr)
        done = True
    finally:
        if not done:
            cleanup()
    return Launch(argv=argv, env=_spawn_env(), cwd=cwd, run_dir=state.get("dir"), cleanup=cleanup,
                  os_confinement=confinement, os_reason=reason, read_only_mode=read_only_mode,  # type: ignore[arg-type]
                  timeout_s=prepared.grant.limits.timeout_s + prepared.compile_grace_s,
                  module_sha256=prepared.module_sha256)


# --- execute -------------------------------------------------------------------------------------------------------

def _kill_group(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        proc.kill()
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):  # the group already exited
        os.killpg(proc.pid, signal.SIGKILL)


def _execute(launch: Launch, stdin: bytes, stdout_cap: int, stderr_cap: int
             ) -> tuple[int, bytes, bytes, str | None]:
    group: dict[str, Any] = ({"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt"
                             else {"start_new_session": True})
    try:
        proc = subprocess.Popen(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, **group)
    except OSError as exc:
        raise WasmExecError(f"wasmtime could not start: {exc}") from exc
    labels: list[str] = []

    def kill(why: str) -> None:
        labels.append(why)
        _kill_group(proc)

    def reader(stream: Any, cap: int, buf: bytearray) -> None:
        for chunk in iter(lambda: stream.read1(1 << 16), b""):
            room = cap - len(buf)
            buf += chunk[:room]
            if len(chunk) > room:
                return kill("output-cap")

    def writer() -> None:
        with contextlib.suppress(BrokenPipeError):  # the guest exited without reading all of stdin
            proc.stdin.write(stdin)
            proc.stdin.close()

    out, err = bytearray(), bytearray()
    threads = [threading.Thread(target=reader, args=(proc.stdout, stdout_cap, out), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, stderr_cap, err), daemon=True),
               threading.Thread(target=writer, daemon=True)]
    for t in threads:
        t.start()
    try:
        proc.wait(timeout=launch.timeout_s)
    except subprocess.TimeoutExpired:
        kill("host-timeout")
        proc.wait()
    for t in threads:
        t.join(POST_KILL_DRAIN_S)
    _kill_group(proc)
    return proc.returncode, bytes(out), bytes(err), labels[0] if labels else None


def run(prepared: Prepared, args: Sequence[str], *, stdin: bytes = b"", project_root: str | Path,
        os_confine: OsConfine, stdout_cap: int = 1 << 20, stderr_cap: int = 256 << 10, resolver: WasmResolver,
        profiles: ModuleType, control_plane_protected: bool = False) -> WasmResult:
    """Run one guest to completion under the pinned grant.

    Args:
        prepared: From :func:`prepare`.
        args: Guest arguments.
        stdin: The guest's stdin (at most 8 MiB; data, never instructions).
        project_root: The project root.
        os_confine: As :func:`materialize`.
        stdout_cap: Bytes kept from stdout; more kills the run (``trap="output-cap"``, ``truncated``).
        stderr_cap: The same for stderr.
        resolver: As :func:`materialize`.
        profiles: As :func:`materialize`.
        control_plane_protected: As :func:`materialize`.

    Returns:
        A :class:`WasmResult`.

    Raises:
        WasmExecError: On any refusal before the guest starts.
    """
    if not isinstance(stdin, bytes) or len(stdin) > MAX_STDIN_BYTES:
        raise WasmExecError(f"stdin must be bytes of at most {MAX_STDIN_BYTES}")
    if min(stdout_cap, stderr_cap) < 1:
        raise WasmExecError("output caps must be positive")
    launch = materialize(prepared, args, project_root=project_root, os_confine=os_confine, resolver=resolver,
                         profiles=profiles, control_plane_protected=control_plane_protected)
    started = time.monotonic()
    try:
        code, out, err, host = _execute(launch, stdin, stdout_cap, stderr_cap)
    finally:
        launch.cleanup()
    return WasmResult(exit_code=code, stdout=out, stderr=err, trap=classify_trap(code, err, host_label=host),
                      os_confinement=launch.os_confinement, truncated=host == "output-cap",
                      os_reason=launch.os_reason, read_only_mode=launch.read_only_mode,
                      wall_s=round(time.monotonic() - started, 3), module_sha256=launch.module_sha256)


# --- capability probe ----------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ProbeReport:
    """What :func:`probe` found; ``checks`` holds ``(name, passed, detail)`` in the order run."""

    ok: bool
    wasmtime: str | None
    version: str | None
    os_kind: str | None
    checks: tuple[tuple[str, bool, str], ...]


_PROBE_CHECKS = ("resolve", "version", "probe-sha", "deny", "control", "fuel", "memory", "read-only")


def probe(project_root: str | Path, *, resolver: WasmResolver, profiles: ModuleType,
          probe_module: str | Path | None = None, os_confine: Literal["auto", "require"] = "auto",
          control_plane_protected: bool = False) -> ProbeReport:
    """Prove the grant holds here: the version pin, a deny run, a control run that must see a widening, the fuel
    and memory traps, and a write attempt on a read-only mount.

    Args:
        project_root: The project root (run dirs go under its ``.agentteams/wasm/exec``).
        resolver: As :func:`materialize`.
        profiles: As :func:`materialize`.
        probe_module: The deny-probe binary; default ``data/wasm_deny_probe.wasm`` beside this file.
        os_confine: ``auto`` or ``require``.
        control_plane_protected: As :func:`materialize`.

    Returns:
        A :class:`ProbeReport`; ``ok`` only when every check ran and passed.

    Raises:
        Nothing (failures are reported as checks).
    """
    checks: list[tuple[str, bool, str]] = []
    info: dict[str, str | None] = {"wasmtime": None, "version": None, "os_kind": None}
    module = str(probe_module or Path(__file__).with_name("data") / "wasm_deny_probe.wasm")

    def go(name: str, grant: Grant, args: list[str], want: Callable[[WasmResult], bool],
           src: Mapping[str, bytes] | None = None) -> bool:
        try:
            res = run(prepare(module, grant=grant, src_files=src), args, project_root=project_root,
                      os_confine=os_confine, resolver=resolver, profiles=profiles,
                      control_plane_protected=control_plane_protected)
        except WasmExecError as exc:
            checks.append((name, False, str(exc)))
            return False
        info["os_kind"] = res.os_reason.split(":", 1)[1] if res.os_confinement == "nested" else None
        checks.append((name, want(res), f"exit={res.exit_code} trap={res.trap} out={res.stdout[:64]!r} "
                                        f"confinement={res.os_reason} ro={res.read_only_mode}"))
        return checks[-1][1]

    try:
        info["wasmtime"] = _resolve(resolver, "wasmtime")
        pinned = resolver.pinned_version("wasmtime")
        sha = prepare(module, grant=COMPUTE_ONLY).module_sha256
        out = subprocess.run([info["wasmtime"], "--version"], capture_output=True, timeout=10, env=_spawn_env(),
                             stdin=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
    except (WasmExecError, OSError, ValueError, LookupError, RuntimeError, subprocess.SubprocessError) as exc:
        checks.append(("resolve", False, str(exc)))
    else:
        found = _VERSION_RE.match(out)
        info["version"] = found[1] if found else None
        checks += [("resolve", True, str(info["wasmtime"])),
                   ("version", info["version"] == pinned, f"{out.strip()[:80]!r}, pinned {pinned}"),
                   ("probe-sha", sha == DENY_PROBE_SHA256, sha)]
    scratch = Grant(mounts=(Mount(RUN_SCRATCH, "/scratch", "rw"),), limits=DEFAULT_LIMITS)
    if all(c[1] for c in checks) and go("deny", COMPUTE_ONLY, ["deny"],
                                        lambda r: r.exit_code == 0 and r.stdout == b"PREOPEN0\nENV0\nSOCK0\n"):
        go("control", scratch, ["deny"], lambda r: r.exit_code == 0 and r.stdout.startswith(b"PREOPEN1\n"))
        go("fuel", Grant((), Limits(1_000_000, DEFAULT_LIMITS.max_memory_bytes, 30)), ["spin"],
           lambda r: r.trap == "fuel")
        go("memory", Grant((), Limits(DEFAULT_LIMITS.fuel, 1 << 20, 30)), ["grow"], lambda r: r.trap == "memory")
        go("read-only", COMPUTE_ONLY, ["write"], lambda r: r.stdout == b"WRITE0\n", src={"probe.txt": b"probe\n"})
    ok = tuple(c[0] for c in checks) == _PROBE_CHECKS and all(c[1] for c in checks)
    return ProbeReport(ok=ok, checks=tuple(checks), **info)


def available(project_root: str | Path, *, resolver: WasmResolver, profiles: ModuleType,
              probe_module: str | Path | None = None, os_confine: Literal["auto", "require"] = "auto",
              control_plane_protected: bool = False) -> str | None:
    """Whether WASM execution is usable here, proven by :func:`probe` (never just "a binary exists").

    Args:
        project_root: The project root.
        resolver: As :func:`materialize`.
        profiles: As :func:`materialize`.
        probe_module: The deny-probe binary (the bundle passes its own copy).
        os_confine: ``auto`` or ``require`` (``require`` fails without a usable OS sandbox).
        control_plane_protected: As :func:`materialize`.

    Returns:
        ``"wasmtime <version> (+<seatbelt|bwrap|alone>)"`` when every check passed, else None.

    Raises:
        Nothing.
    """
    rep = probe(project_root, resolver=resolver, profiles=profiles, probe_module=probe_module, os_confine=os_confine,
                control_plane_protected=control_plane_protected)
    return f"wasmtime {rep.version} (+{rep.os_kind or 'alone'})" if rep.ok else None


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m agentteams.wasm_exec --print-spec``: print :data:`GRANT_SPEC` as canonical JSON.

    Args:
        argv: Command-line arguments (default ``sys.argv[1:]``).

    Returns:
        0 after printing, 2 on a usage error.

    Raises:
        Nothing.
    """
    if list(sys.argv[1:] if argv is None else argv) != ["--print-spec"]:
        print("usage: python -m agentteams.wasm_exec --print-spec", file=sys.stderr)
        return 2
    sys.stdout.write(_SPEC_JSON + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
