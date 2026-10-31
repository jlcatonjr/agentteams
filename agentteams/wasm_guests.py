"""wasm_guests.py — run Python, JavaScript or a prebuilt core module as a WASM guest (WASM sandbox plan P04).

One call turns "run this source / this module" into one executor run on the caller's grant. The interpreter's
stdlib reaches the guest read-only through *runtime mounts* whose guest names come from the pins file (CPython:
``/lib``), never from this module; the source reaches it as a private read-only ``/src/main.<ext>`` file, so stdin
stays free for data (C-4). The grant is never widened to make an interpreter work, an unsupported language refuses
(no native fallback), and :func:`probe_language` proves the deny posture per language with a liveness control.

Imports nothing from agentteams, not even lazily (R2): the ``agentteams_wasm`` bundle exec-loads this exact file. The
executor (``agentteams.wasm_exec`` or the bundle's exec-loaded copy), the resolver (``wasm_runtime.Resolver`` /
``wasm_pins.PinnedResolver``) and the OS-profile module are required keywords (R10). Stdlib only.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import stat
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

#: The guest languages (D3). Anything else refuses; nothing ever runs natively.
LANGUAGES = ("module", "python", "javascript")

#: Largest accepted guest source, in UTF-8 bytes.
MAX_SOURCE_BYTES = 1 << 20

#: Interpreter flags before ``/src/main.py``: isolated (``-I`` implies ``-E -P -s``), no ``site``, no ``.pyc``.
PYTHON_FLAGS = ("-I", "-S", "-B")

#: Measured startup floors ``(fuel, max_memory_bytes, timeout_s)`` per interpreter (plan step 1: dev CPython 3.14.6
#: WASI, wasmtime 36.0.16, 2026-10-31; ``import json, re, dataclasses`` needed 2.22e9 fuel and 41 MiB). A grant below
#: a floor still runs, with a notice; limits are never raised. JavaScript is unmeasured (not pinned here).
LANGUAGE_DEFAULT_LIMITS: dict[str, tuple[int, int, int]] = {"python": (7_000_000_000, 48 << 20, 5)}

#: Probe sources shipped beside this file (``data/wasm_probes/``); the module probe is the executor's binary.
PROBE_FILES = {"python": "probe.py", "javascript": "probe.mjs"}

_SRC_NAME = {"python": "main.py"}
# A top-level ES-module statement (static import/export) selects main.mjs; dynamic import( stays a script.
_ESM_RE = re.compile(r"^[ \t]*(?:import[ \t]*[\w*{\"']|export[ \t\n{*])", re.MULTILINE)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_RESOLVER_ERRORS = (OSError, ValueError, LookupError, RuntimeError)


class WasmGuestError(Exception):
    """A guest refusal: bad language or arguments, an unusable module path, or an executor/resolver refusal."""


class GuestResolver(Protocol):
    """What this module needs from provisioning (``wasm_runtime.Resolver``; ``wasm_pins.PinnedResolver``).

    Every method re-verifies against the pins on each call; failures raise ``OSError`` / ``ValueError`` /
    ``LookupError`` / ``RuntimeError`` subclasses, which become :class:`WasmGuestError`.
    """

    def resolve(self, component: str) -> Path:
        """Return the verified entry file of *component*."""

    def resolve_tree(self, component: str) -> Path:
        """Return the version dir of *component* after re-hashing its pinned tree."""

    def runtime_mounts(self, component: str) -> tuple[tuple[Any, str], ...]:
        """Return the verified ``(host_dir, guest_name)`` read-only mounts of *component*."""

    def pinned_version(self, component: str) -> str:
        """Return the pinned version of *component*."""


# --- argument checks -------------------------------------------------------------------------------------------------

def _check_language(language: object) -> str:
    if not isinstance(language, str) or language not in LANGUAGES:
        raise WasmGuestError(f"unsupported language {language!r}; supported: {', '.join(LANGUAGES)}")
    return language


def _source_bytes(source: str | bytes) -> bytes:
    if isinstance(source, bytes):
        try:
            source.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise WasmGuestError(f"guest source is not valid UTF-8 ({exc.reason} at byte {exc.start})") from exc
        data = source
    elif isinstance(source, str):
        try:
            data = source.encode("utf-8", "strict")
        except UnicodeEncodeError as exc:
            raise WasmGuestError(f"guest source is not encodable as UTF-8 ({exc.reason})") from exc
    else:
        raise WasmGuestError("guest source must be str or UTF-8 bytes")
    if len(data) > MAX_SOURCE_BYTES:
        raise WasmGuestError(f"guest source is {len(data)} bytes; the limit is {MAX_SOURCE_BYTES}")
    return data


def js_source_name(source: str | bytes) -> str:
    """The ``/src`` file name for a JavaScript source: ``main.mjs`` with a top-level static import/export.

    Args:
        source: The JavaScript source.

    Returns:
        ``"main.mjs"`` or ``"main.js"``.

    Raises:
        WasmGuestError: When *source* is not UTF-8.
    """
    return "main.mjs" if _ESM_RE.search(_source_bytes(source).decode("utf-8")) else "main.js"


def _within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def check_module_path(module_path: str | Path, project_root: str | Path, *, executor: ModuleType) -> str:
    """Validate a ``language="module"`` path: a regular, non-symlinked file inside the project, clear of the
    protected control-plane paths and the signing-key dir, at most ``executor.MAX_MODULE_BYTES``.

    Args:
        module_path: Project-relative or absolute path of the core module.
        project_root: The project root.
        executor: The ``wasm_exec`` module (supplies ``NEST_PROTECTED_REL``, ``NEST_KEY_DIR``, ``MAX_MODULE_BYTES``).

    Returns:
        The absolute real path.

    Raises:
        WasmGuestError: On any refusal.
    """
    if not isinstance(module_path, (str, Path)) or not str(module_path):
        raise WasmGuestError("module_path must be a non-empty path")
    root = os.path.realpath(str(project_root))
    path = os.path.normpath(os.path.join(root, str(module_path)))
    real = os.path.realpath(path)
    if real != path:
        raise WasmGuestError(f"module {module_path!r} is or passes through a symlink; refused")
    if not _within(real, root) or real == root:
        raise WasmGuestError(f"module {module_path!r} must lie inside the project {root}")
    protected = [os.path.join(root, p) for p in executor.NEST_PROTECTED_REL]
    protected.append(os.path.realpath(os.path.expanduser(executor.NEST_KEY_DIR)))
    if any(_within(real, p) for p in protected):
        raise WasmGuestError(f"module {module_path!r} lies under a protected path; refused")
    try:
        st = os.lstat(real)
    except OSError as exc:
        raise WasmGuestError(f"module {module_path!r} cannot be read ({exc.strerror})") from exc
    if not stat.S_ISREG(st.st_mode) or st.st_size > executor.MAX_MODULE_BYTES:
        raise WasmGuestError(f"module {module_path!r} must be a regular file of at most "
                             f"{executor.MAX_MODULE_BYTES} bytes")
    return real


def limit_notices(language: str, limits: Any) -> list[str]:
    """Notices for a grant whose limits sit below *language*'s measured startup floor (never raised silently).

    Args:
        language: One of :data:`LANGUAGES`.
        limits: An ``executor.Limits``.

    Returns:
        Zero or one notice string.

    Raises:
        WasmGuestError: For an unsupported language.
    """
    floor = LANGUAGE_DEFAULT_LIMITS.get(_check_language(language))
    if floor is None:
        return []
    low = [n for n, v, f in zip(("fuel", "max_memory_bytes", "timeout_s"),
                                (limits.fuel, limits.max_memory_bytes, limits.timeout_s), floor) if v < f]
    return [f"limits below the measured {language} startup floor ({', '.join(low)}); expect a fuel/memory trap"] \
        if low else []


# --- argv_for / run_code -------------------------------------------------------------------------------------------

def _resolve(resolver: GuestResolver, component: str) -> Path:
    try:
        return Path(resolver.resolve(component))
    except _RESOLVER_ERRORS as exc:
        raise WasmGuestError(f"could not resolve the {component} runtime: {exc}") from exc


def _runtime_mounts(resolver: GuestResolver, component: str, executor: ModuleType) -> tuple[Any, ...]:
    try:
        pairs = tuple(resolver.runtime_mounts(component))
        return tuple(executor.Mount(os.path.realpath(str(host)), guest, "ro") for host, guest in pairs)
    except executor.WasmExecError as exc:
        raise WasmGuestError(f"{component} runtime mount refused: {exc}") from exc
    except _RESOLVER_ERRORS as exc:
        raise WasmGuestError(f"could not verify the {component} runtime mounts: {exc}") from exc


def argv_for(language: str, source: str | bytes | None, module_path: str | Path | None, *,
             args: Sequence[str] = (), grant: Any, project_root: str | Path, resolver: GuestResolver,
             executor: ModuleType) -> tuple[Any, tuple[str, ...]]:
    """Prepare one guest run without spawning anything or writing to the filesystem.

    The P12 runner passes both results to ``executor.materialize(prepared, args, ...)``; :func:`run_code` passes
    them to ``executor.run``. The executor's ``Prepared`` carries no guest args, so they are returned beside it.

    Args:
        language: ``"module"``, ``"python"`` or ``"javascript"``.
        source: Guest source (UTF-8 ``str`` or bytes, at most 1 MiB) for an interpreter; None for a module.
        module_path: Core-module path (inside the project) for ``"module"``; None for an interpreter.
        args: User arguments, after the interpreter's own.
        grant: The caller's ``executor.Grant`` (passed through unchanged; never widened).
        project_root: The project root.
        resolver: Provisioning (:class:`GuestResolver`).
        executor: The ``wasm_exec`` module.

    Returns:
        ``(prepared, guest_args)``.

    Raises:
        WasmGuestError: On any refusal, including the executor's and the resolver's.
    """
    language = _check_language(language)
    if not isinstance(grant, executor.Grant):
        raise WasmGuestError("grant must be an executor Grant")
    user_args = list(args)
    if not all(isinstance(a, str) for a in user_args):
        raise WasmGuestError("guest args must be strings")
    try:
        if language == "module":
            if source is not None or module_path is None:
                raise WasmGuestError("language 'module' takes module_path and no source")
            path = check_module_path(module_path, project_root, executor=executor)
            return executor.prepare(path, grant=grant), tuple(user_args)
        if module_path is not None or source is None:
            raise WasmGuestError(f"language {language!r} takes source and no module_path")
        data = _source_bytes(source)
        name = _SRC_NAME.get(language) or js_source_name(data)
        module = _resolve(resolver, language)
        mounts = _runtime_mounts(resolver, language, executor)
        flags = PYTHON_FLAGS if language == "python" else ()
        prepared = executor.prepare(module, grant=grant, runtime_mounts=mounts, src_files={name: data})
        return prepared, (*flags, f"/src/{name}", *user_args)
    except executor.WasmExecError as exc:
        raise WasmGuestError(f"{language} guest refused: {exc}") from exc


def run_code(language: str, source: str | bytes | None, module_path: str | Path | None, *,
             args: Sequence[str] = (), stdin: bytes = b"", grant: Any, project_root: str | Path,
             os_confine: str = "auto", resolver: GuestResolver, executor: ModuleType, profiles: ModuleType,
             control_plane_protected: bool = False, stdout_cap: int = 1 << 20, stderr_cap: int = 256 << 10) -> Any:
    """Run Python, JavaScript or a core module once under *grant* and return the executor's result.

    Args:
        language: As :func:`argv_for`.
        source: As :func:`argv_for`.
        module_path: As :func:`argv_for`.
        args: As :func:`argv_for`.
        stdin: Guest stdin (data, never program text).
        grant: As :func:`argv_for`.
        project_root: As :func:`argv_for`.
        os_confine: Passed through to ``executor.run`` (``auto`` / ``require`` / ``off``; the P08 launcher alone
            passes the inherited mode).
        resolver: As :func:`argv_for`; also resolves wasmtime.
        executor: As :func:`argv_for`.
        profiles: The ``_sandbox_profiles`` module, passed through.
        control_plane_protected: Passed through (R3′).
        stdout_cap: Passed through.
        stderr_cap: Passed through.

    Returns:
        An ``executor.WasmResult`` (untrusted guest bytes, C-4).

    Raises:
        WasmGuestError: On any refusal before the guest starts.
    """
    prepared, guest_args = argv_for(language, source, module_path, args=args, grant=grant,
                                    project_root=project_root, resolver=resolver, executor=executor)
    for notice in limit_notices(language, grant.limits):
        print(f"WASM SANDBOX: {notice}", file=sys.stderr)
    try:
        return executor.run(prepared, guest_args, stdin=stdin, project_root=project_root, os_confine=os_confine,
                            stdout_cap=stdout_cap, stderr_cap=stderr_cap, resolver=resolver, profiles=profiles,
                            control_plane_protected=control_plane_protected)
    except executor.WasmExecError as exc:
        raise WasmGuestError(f"{language} guest refused: {exc}") from exc


# --- per-language deny probe -----------------------------------------------------------------------------------------

#: Probe rows that must be DENIED (or ABSENT) under a compute-only grant.
PROBE_DENY = ("fs-abs-file", "fs-abs-list", "fs-rel", "fs-parent", "fs-project", "fs-write-src", "fs-write-cwd",
              "scratch-write", "env", "env-canary", "net-connect", "net-dns", "proc-system", "proc-spawn")
#: Rule-12 carve-out: reported, never required either way (timing / nondeterminism inside the guest is permitted).
PROBE_INFO = ("clock", "random", "thread")
_PROBE_LINE = re.compile(rb"^PROBE ([a-z-]+) (DENIED|ALLOWED|ABSENT)(?: (.*))?$")


@dataclass(frozen=True)
class GuestProbeReport:
    """What :func:`probe_language` found; ``checks`` holds ``(name, passed, detail)`` in the order run."""

    language: str
    ok: bool
    os_confinement: str
    read_only_mode: str
    checks: tuple[tuple[str, bool, str], ...]


def parse_probe(stdout: bytes) -> dict[str, str] | None:
    """Parse ``PROBE <name> DENIED|ALLOWED|ABSENT [detail]`` lines (other lines ignored; C-4: data only).

    Args:
        stdout: The probe's stdout.

    Returns:
        ``{name: verdict}``, or None when a name repeats (a forged line can only fail the probe).

    Raises:
        Nothing.
    """
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        found = _PROBE_LINE.match(line)
        if found:
            name = found[1].decode("ascii")
            if name in out:
                return None
            out[name] = found[2].decode("ascii")
    return out


def _tree_sha(paths: Sequence[str]) -> str:
    """sha256 over every regular file below *paths* (names + contents), not following symlinks."""
    digest = hashlib.sha256()
    for top in paths:
        for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                digest.update(os.path.relpath(full, top).encode("utf-8", "surrogateescape") + b"\0")
                fd = os.open(full, os.O_RDONLY | _NOFOLLOW)
                with os.fdopen(fd, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
    return digest.hexdigest()


def _probe_source(language: str) -> bytes:
    path = Path(__file__).with_name("data") / "wasm_probes" / PROBE_FILES[language]
    return path.read_bytes()


def probe_language(language: str, project_root: str | Path, *, resolver: GuestResolver, executor: ModuleType,
                   profiles: ModuleType, os_confine: str = "auto", control_plane_protected: bool = False,
                   probe_source: bytes | None = None, probe_module: str | Path | None = None) -> GuestProbeReport:
    """Prove *language*'s deny posture here: a compute-only deny run that must leak no canary, a liveness control
    that must see a widened grant, the fuel and memory traps, and (interpreters) an unchanged runtime tree.

    The canaries live in a temporary ``.agentteams-wasm-probe-<hex>/`` directory inside *project_root* (a
    ``/project`` mount must lie in the project), removed afterwards; the token is never placed in any environment.

    Args:
        language: One of :data:`LANGUAGES`.
        project_root: The project root (run dirs go under its ``.agentteams/wasm/exec``).
        resolver: As :func:`run_code`.
        executor: As :func:`run_code`.
        profiles: As :func:`run_code`.
        os_confine: ``auto`` or ``require``.
        control_plane_protected: As :func:`run_code`.
        probe_source: Override of the shipped probe script (interpreters; the bundle passes its verified copy).
        probe_module: Override of the executor's deny-probe binary (``module``); still checked against
            ``executor.DENY_PROBE_SHA256``. An exec-loaded executor without ``__file__`` needs it.

    Returns:
        A :class:`GuestProbeReport`; ``ok`` only when every check ran and passed.

    Raises:
        WasmGuestError: For an unsupported language or an unusable *project_root*.
    """
    language = _check_language(language)
    if os_confine not in ("auto", "require"):
        raise WasmGuestError("probe os_confine must be 'auto' or 'require'")
    root = os.path.realpath(str(project_root))
    if not os.path.isdir(root):
        raise WasmGuestError(f"project root {project_root} is not a directory")
    token = "canary-" + secrets.token_hex(16)
    base = os.path.join(root, f".agentteams-wasm-probe-{secrets.token_hex(6)}")
    checks: list[tuple[str, bool, str]] = []
    seen = {"conf": "none", "ro": "none"}
    D, L = executor.DEFAULT_LIMITS, executor.Limits

    def go(name: str, grant: Any, mode: str, want: Any, *, leak_ok: bool = False) -> None:
        try:
            if language == "module":
                module = str(probe_module or Path(getattr(executor, "__file__", "") or ".").with_name("data")
                             / "wasm_deny_probe.wasm")
                prepared, args = executor.prepare(module, grant=grant), (mode,)
                if prepared.module_sha256 != executor.DENY_PROBE_SHA256:
                    raise WasmGuestError("the deny-probe binary does not match executor.DENY_PROBE_SHA256")
            else:   # argv_for + run directly: the deliberately low fuel/memory runs need no floor notice
                prepared, args = argv_for(language, probe_source or _probe_source(language), None, args=[mode],
                                          grant=grant, project_root=root, resolver=resolver, executor=executor)
            res = executor.run(prepared, args, project_root=root, os_confine=os_confine, resolver=resolver,
                               profiles=profiles, control_plane_protected=control_plane_protected)
        except (WasmGuestError, executor.WasmExecError) as exc:
            checks.append((name, False, str(exc)))
            return
        if name == "deny":   # the report's confinement is that of the compute-only run
            seen.update(conf=res.os_confinement, ro=res.read_only_mode)
        leaked = token.encode() in res.stdout or token.encode() in res.stderr
        passed, detail = want(res)
        if leaked and not leak_ok:
            passed, detail = False, "canary leaked; " + detail
        checks.append((name, passed, f"{detail} exit={res.exit_code} trap={res.trap} conf={res.os_reason}"))

    def deny(res: Any) -> tuple[bool, str]:
        if language == "module":
            return res.exit_code == 0 and res.stdout == b"PREOPEN0\nENV0\nSOCK0\n", repr(res.stdout[:64])
        rows = parse_probe(res.stdout)
        if rows is None:
            return False, "duplicate PROBE line"
        bad = [n for n in PROBE_DENY if rows.get(n) not in ("DENIED", "ABSENT")]
        if rows.get("stdio") != "ALLOWED" or "fs-write-runtime" not in rows:
            bad.append("stdio/fs-write-runtime missing")
        return res.exit_code == 0 and not bad, f"not denied: {bad}" if bad else "all denied"

    def control(res: Any) -> tuple[bool, str]:
        if language == "module":
            return res.exit_code == 0 and res.stdout.startswith(b"PREOPEN1\n"), repr(res.stdout[:64])
        rows = parse_probe(res.stdout) or {}
        want = {"fs-project": "ALLOWED", "scratch-write": "ALLOWED"}
        got = {k: rows.get(k) for k in want}
        live = got == want and token.encode() in res.stdout
        return res.exit_code == 0 and live, f"control saw {got}, canary {'seen' if live else 'missing'}"

    try:
        os.makedirs(os.path.join(base, "c"), 0o700)
        for rel in ("parent-canary.txt", os.path.join("c", "probe-canary.txt")):
            with open(os.path.join(base, rel), "x", encoding="utf-8") as fh:
                fh.write(token + "\n")
        runtime: list[str] = []
        if language != "module":
            try:
                runtime = [os.path.realpath(str(h)) for h, _ in resolver.runtime_mounts(language)]
            except _RESOLVER_ERRORS as exc:
                checks.append(("resolve", False, str(exc)))
        before = _tree_sha(runtime)
        if not checks:
            go("deny", executor.COMPUTE_ONLY, "deny", deny)
            widened = executor.Grant((executor.Mount(os.path.join(base, "c"), "/project/c", "ro"),
                                      executor.Mount(executor.RUN_SCRATCH, "/scratch", "rw")), D)
            go("control", widened, "deny", control, leak_ok=True)
            fuel = 1_000_000 if language == "module" else 2_000_000_000
            go("fuel", executor.Grant((), L(fuel, D.max_memory_bytes, 60)), "spin",
               lambda r: (r.trap == "fuel", "fuel trap" if r.trap == "fuel" else "no fuel trap"))
            mem = 1 << 20 if language == "module" else D.max_memory_bytes
            go("memory", executor.Grant((), L(D.fuel, mem, 60)), "grow", lambda r: (
                r.trap == "memory" or (r.exit_code != 0 and re.search(rb"MemoryError|RangeError|out of memory",
                                                                      r.stderr) is not None), "memory bounded"))
            same = _tree_sha(runtime) == before
            checks.append(("runtime-unchanged", same, "host runtime tree unchanged" if same else "CHANGED"))
    except OSError as exc:
        checks.append(("setup", False, str(exc)))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    names = tuple(c[0] for c in checks)
    ok = names == ("deny", "control", "fuel", "memory", "runtime-unchanged") and all(c[1] for c in checks)
    return GuestProbeReport(language=language, ok=ok, os_confinement=seen["conf"], read_only_mode=seen["ro"],
                            checks=tuple(checks))

