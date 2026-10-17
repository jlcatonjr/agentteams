"""confinement.py — run one command inside an OS sandbox for the proposal runner (pilot P4b).

The out-of-session runner (:mod:`agentteams.proposal_runner`) holds the ledger key, so every command or gate it
starts must be confined. That includes each ``command-request`` and each pre-write gate.

* **macOS (Seatbelt).** An inline ``sandbox-exec -p`` profile. Reads are allowed. Writes are denied except
  under the declared write roots, a private ``TMPDIR`` and a few ``/dev`` nodes. The ledger, the queue,
  ``.git`` and the control plane are write-denied even inside a root. The key directory is read-denied.
  ``process-exec`` is denied except under the declared ``exec`` paths, matched as subpaths so a toolchain
  can run its own helpers. ``/bin/sh`` re-executes ``/bin/bash``, so a shell-using program needs both.
  Most developer tools in ``/usr/bin`` (``git``, ``make``, ``clang``) are Apple's xcrun launcher, which execs
  the real tool from the active developer directory. When ``exec`` names such a program, :func:`wrap` also
  allows the real tool by ``literal`` path (``xcrun --find``) and, for git, its ``--exec-path`` as a subpath;
  only a declared ``/usr/bin`` itself adds whole tool directories. Each added path, and every parent, must be
  root-owned and not group- or world-writable. Otherwise, or if xcrun is missing or fails, nothing is added for
  that launcher and a one-line notice says why; its confined calls will then fail with exit 71.
  ``--check-wiring`` reports the same. The runner warns when a command (or ``lake``, which runs git) needs an
  unresolved launcher, and refuses the run when a write root also covers a package store, unless that agent's
  ``confined_programs`` entry sets ``allow_unresolved_launcher_with_package_store: true``.
* **Linux (bwrap).** The root is mounted read-only, and the write roots and the private ``TMPDIR`` are
  bind-mounted writable. The ledger, queue and ``.git`` inside them are re-mounted read-only, and the key
  directory is masked with an empty tmpfs. The command gets a new PID namespace with ``--die-with-parent``,
  so nothing it starts outlives it. Operator decision C: there is no program allowlist on Linux in P4b.

Write roots that equal, contain or lie inside a package store (``.lake/packages``, ``node_modules``, ...) get a
warning from :func:`package_store_warnings`, not a refusal: a tool whose probe fails inside the sandbox may
delete and re-create such a store (mathAgents M6: ``lake`` removed ``.lake/packages/mathlib``).

:func:`available` checks for a usable sandbox by actually applying a trivial profile. That catches the
nesting case live probes found (``sandbox-exec`` inside a sandboxed process fails with
``sandbox_apply: Operation not permitted``).

Stdlib only; integrity-pinned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple

from agentteams.atomicio import read_regular_nofollow, write_new_atomic

#: Project paths a confined command may never write, even inside a declared write root.
PROTECTED_REL = (".agentteams", ".agentteams-queue", ".agentteams-status", ".git", ".claude", ".goose", ".codex", ".grok", ".github")
#: Read-denied to every confined command: the ledger key and the operator signing keys (one source of truth).
from agentteams.frameworks._sandbox_emit import SIGNING_KEY_DIR as KEY_DIR  # noqa: E402

#: P5b: operator-owned per-project ``confined_programs`` files (see :func:`confined_file_for`); one source of truth
#: with the emitted ``Edit(...)`` deny rule.
from agentteams.frameworks._sandbox_emit import CONFINED_PROGRAMS_DIR as CONFINED_DIR  # noqa: E402
MAX_CONFINED_BYTES = 64 * 1024

_DEV_WRITES = ("/dev/null", "/dev/zero", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/dtracehelper")
_BAD_PATH_CHARS = ('"', "\n", "\r", "\\", "\0")


class ConfinementError(Exception):
    """A path can't be expressed safely, or no sandbox is usable."""


def kind() -> str | None:
    """The sandbox this platform would use: ``"seatbelt"``, ``"bwrap"`` or ``None``.

    Returns:
        The sandbox kind, from the platform and the presence of its binary (not yet probed).

    Raises:
        Nothing.
    """
    if sys.platform == "darwin" and shutil.which("sandbox-exec", path="/usr/bin"):
        return "seatbelt"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return None


def available() -> str | None:
    """Probe for a usable sandbox by applying a trivial profile.

    Returns:
        ``"seatbelt"`` or ``"bwrap"`` when a trivial confined ``true`` succeeds here, else ``None``. That
        includes inside an already-sandboxed process, where Seatbelt can't nest.

    Raises:
        Nothing.
    """
    sandbox = kind()
    if sandbox == "seatbelt":
        argv = ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"]
    elif sandbox == "bwrap":
        argv = [shutil.which("bwrap") or "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-pid",
                "--die-with-parent", "true"]
    else:
        return None
    try:
        ok = subprocess.run(argv, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return sandbox if ok else None


def _clean(path: str) -> str:
    if any(c in path for c in _BAD_PATH_CHARS) or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise ConfinementError(f"path {path!r} contains a character that can't be expressed safely; refused")
    return path


def _real(path: str) -> str:
    return _clean(os.path.realpath(os.path.expanduser(path)))


def seatbelt_profile(*, root: Path, exec_paths: list[str], write_roots: list[str], tmp_dir: Path,
                     exec_literals: tuple[str, ...] | list[str] = ()) -> str:
    """Build the Seatbelt profile for one confined run.

    Args:
        root: The project root.
        exec_paths: Programs or directories the command may execute (subpath match), already validated.
        write_roots: Project-relative directories the command may write.
        tmp_dir: The run's private ``TMPDIR``.
        exec_literals: Programs the command may execute by exact path only (``literal``): an xcrun launcher's
            real tool, so allowing ``/usr/bin/git`` doesn't allow its whole developer ``bin`` directory.

    Returns:
        SBPL text for ``sandbox-exec -p``.

    Raises:
        ConfinementError: When a path contains a character SBPL can't carry safely.
    """
    real_root = _real(str(root))
    writes = [_real(str(tmp_dir))] + [_real(str(root / w)) for w in write_roots]
    protected = [_real(str(root / p)) for p in PROTECTED_REL]
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write*",
        *[f'    (subpath "{w}")' for w in writes],
        *[f'    (literal "{d}")' for d in _DEV_WRITES],
        ")",
        "(deny file-write*",
        *[f'    (subpath "{p}")' for p in protected],
        f'    (literal "{real_root}")',
        f'    (subpath "{_real(KEY_DIR)}")',
        '    (regex #"/\\.[gG][iI][tT](/|$)")',  # a nested repo inside a write root (case-insensitive: APFS)
        ")",
        f'(deny file-read* (subpath "{_real(KEY_DIR)}"))',
        "(deny process-exec*)",
        "(allow process-exec*",
        *[f'    (subpath "{_real(e)}")' for e in exec_paths],
        *[f'    (literal "{_real(e)}")' for e in exec_literals],
        ")",
    ]
    return "\n".join(lines) + "\n"


# --- macOS xcrun launchers (mathAgents M6) ----------------------------------------------------------------------
# On macOS most developer tools in /usr/bin (git, make, clang, python3, ...) are one Apple launcher binary, hard-
# linked under each name. It execs the real tool from the active developer directory (``xcrun --find NAME``), and
# git then runs its helpers from ``git --exec-path``. A profile that allows only ``/usr/bin/git`` denies both, so
# every confined git call fails with exit 71. :func:`expand_exec_paths` adds the narrow real paths.

#: Programs known to exec an xcrun launcher themselves: ``lake`` runs ``git`` for its package fetches.
LAUNCHER_WRAPPERS: dict[str, tuple[str, ...]] = {"lake": ("git",)}
#: Launchers whose real tool needs a helper directory as well, and the flag that prints it.
_LAUNCHER_HELPERS = {"git": "--exec-path"}
_USR_BIN = "/usr/bin"
_XCRUN = "/usr/bin/xcrun"
#: Seconds per xcrun, xcode-select or git call, and in all for one expansion.
XCRUN_TIMEOUT = 5.0
XCRUN_BUDGET = 20.0

#: An expansion entry: ``("literal", path)`` allows exactly that program, ``("subpath", dir)`` everything under it.
ExecEntry = tuple[str, str]
#: A resolver maps a launcher name to what it needs: ``[("literal", real tool), ("subpath", helper dir), ...]``,
#: ``[]`` when the name is not an xcrun launcher, ``None`` when xcrun is missing or fails.
LauncherResolver = Callable[[str], "list[ExecEntry] | None"]


_lstat = os.lstat  # the ownership check's stat (a test seam)


class Expansion(NamedTuple):
    """What :func:`expand_exec_paths` adds to an exec allowlist."""

    extras: list[ExecEntry]  #: entries to allow beyond the declared paths, in order
    unresolved: list[str]  #: launchers whose real tool is not allowed (confined calls to them fail, exit 71)
    dropped: list[str]  #: resolved paths refused by the ownership check, each with its reason


def xcrun_resolver(timeout: float = XCRUN_TIMEOUT) -> LauncherResolver:
    """The real launcher resolver: ``xcrun --find`` under the active developer directory, cached per instance.

    Every call runs with ``cwd="/"`` and a fixed minimal environment, so neither the project nor a
    ``DEVELOPER_DIR`` in the caller's environment can steer it. A tool counts as a launcher's only when its real
    path lies under ``xcode-select -p`` (``xcrun`` also finds plain ``PATH`` programs such as ``/usr/bin/true``).
    For ``git`` the real tool's ``--exec-path`` is added as a subpath, and must lie under the developer directory.

    Args:
        timeout: Seconds allowed for each subprocess.

    Returns:
        A :data:`LauncherResolver`. Create one per profile build (or per run) and reuse it.

    Raises:
        Nothing.
    """
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.path.expanduser("~")}
    cache: dict[str, list[ExecEntry] | None] = {}
    dev: list[str | None] = []

    def run(argv: list[str]) -> str | None:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, cwd="/",
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return None
        out = proc.stdout.strip()
        return out if proc.returncode == 0 and out.startswith("/") and "\n" not in out else None

    def resolve(name: str) -> list[ExecEntry] | None:
        if name not in cache:
            if not dev:
                found = run(["/usr/bin/xcode-select", "-p"]) if os.path.exists(_XCRUN) else None
                dev.append(os.path.realpath(found) if found else None)
            result: list[ExecEntry] | None = None
            tool = run([_XCRUN, "--find", name]) if dev[0] not in (None, "/") else None
            if tool is not None:
                real = os.path.realpath(tool)
                result = [("literal", real)] if _within(real, str(dev[0])) else []
                if result and name in _LAUNCHER_HELPERS:
                    helper = run([real, _LAUNCHER_HELPERS[name]])
                    helper = os.path.realpath(helper) if helper else None
                    result = result + [("subpath", helper)] if helper and _within(helper, str(dev[0])) else None
            cache[name] = result
        return cache[name]

    return resolve


def ownership_problem(path: str, lstat: Callable[[str], os.stat_result] | None = None) -> str | None:
    """Why *path* can't be trusted as an exec allowance, or None: it and every parent must be root's, not g/o-writable.

    A developer directory a user can write (an Xcode under ``~/Applications``, say) would let a confined command
    swap the real tool. *lstat* is the test seam.

    Args:
        path: An absolute real path.
        lstat: ``os.lstat`` (through the module's ``_lstat`` seam) by default.

    Returns:
        A short reason naming the offending component, or None.

    Raises:
        Nothing.
    """
    current, lstat = path, lstat or _lstat
    while True:
        try:
            st = lstat(current)
        except OSError as exc:
            return f"{current} can't be checked ({exc.strerror or exc})"
        if st.st_uid != 0:
            return f"{current} is not owned by root"
        if st.st_mode & 0o022:
            return f"{current} is writable by group or others"
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _usr_bin_launcher_names() -> list[str]:
    """Candidate launcher names when ``/usr/bin`` itself is allowed: its hard-linked regular files, sorted."""
    try:
        entries = sorted(os.scandir(_USR_BIN), key=lambda e: e.name)
    except OSError:
        return []
    names = []
    for entry in entries:
        try:
            st: os.stat_result | None = entry.stat(follow_symlinks=False)
        except OSError:
            st = None  # vanished or unreadable: not a candidate
        if st is not None and stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            names.append(entry.name)
    return names


def expand_exec_paths(exec_paths: list[str], resolver: LauncherResolver, *, platform: str | None = None,
                      usr_bin_names: Callable[[], list[str]] = _usr_bin_launcher_names,
                      budget: float = XCRUN_BUDGET,
                      lstat: Callable[[str], os.stat_result] | None = None) -> Expansion:
    """What each xcrun launcher in *exec_paths* needs beyond the declared paths (darwin only).

    A program named directly under ``/usr/bin`` gets its real tool as a ``literal`` and its helper directory as a
    ``subpath``, so the allowlist still limits which programs run. Only when ``/usr/bin`` itself is declared does
    each launcher's real tool *directory* become a ``subpath``. Every path must pass :func:`ownership_problem`;
    one that fails is dropped, and its launcher counts as unresolved. Pure apart from the injected callables.

    Args:
        exec_paths: The validated exec allowlist.
        resolver: See :data:`LauncherResolver`.
        platform: ``sys.platform`` by default; anything but ``"darwin"`` adds nothing.
        usr_bin_names: Lists candidate names when ``/usr/bin`` itself is allowed.
        budget: Seconds for the whole expansion; names left when it runs out count as unresolved.
        lstat: The ownership check's ``os.lstat`` (test seam).

    Returns:
        An :class:`Expansion`. ``unresolved`` names the declared ``/usr/bin`` programs left without their real
        tool, or ``/usr/bin`` itself when none of its candidates resolved.

    Raises:
        Nothing.
    """
    out = Expansion([], [], [])
    if (platform or sys.platform) != "darwin":
        return out
    named: list[str] = []
    listed: list[str] = []
    for path in exec_paths:
        real = os.path.realpath(os.path.expanduser(path))
        if real == _USR_BIN:
            listed += [n for n in usr_bin_names() if n not in listed]
        elif os.path.dirname(real) == _USR_BIN and os.path.basename(real) not in named:
            named.append(os.path.basename(real))
    deadline = time.monotonic() + budget
    resolved_any = False
    for name in named + [n for n in listed if n not in named]:
        entries = [] if name == "xcrun" else resolver(name) if time.monotonic() < deadline else None
        if entries is None:
            # A named program is reported; of a listed /usr/bin, only total failure is (an Xcode-only launcher on a
            # Command Line Tools host fails outside the sandbox too).
            if name in named:
                out.unresolved.append(f"{_USR_BIN}/{name}")
            continue
        resolved_any = True
        if name not in named:  # /usr/bin itself was declared: allow the tool's directory, as the operator did
            entries = [("subpath", os.path.dirname(p)) if kind_ == "literal" else (kind_, p) for kind_, p in entries]
        ok = True
        for entry in entries:
            problem = ownership_problem(entry[1], lstat)
            if problem:
                ok = False
                if f"{entry[1]} ({problem})" not in out.dropped:
                    out.dropped.append(f"{entry[1]} ({problem})")
            elif entry not in out.extras:
                out.extras.append(entry)
        if not ok and name in named:
            out.unresolved.append(f"{_USR_BIN}/{name}")
    if listed and not resolved_any:
        out.unresolved.append(f"{_USR_BIN} (its xcrun launchers)")
    return out


def expansion_notices(expansion: Expansion) -> list[str]:
    """The one-line notices for an :class:`Expansion`: dropped paths, then unresolved launchers.

    Args:
        expansion: From :func:`expand_exec_paths`.

    Returns:
        Zero, one or two lines.

    Raises:
        Nothing.
    """
    lines = []
    if expansion.dropped:
        lines.append(f"[confinement] notice: not allowing {'; '.join(expansion.dropped)}: an exec allowance must be "
                     "root-owned and not group- or world-writable, with every parent")
    if expansion.unresolved:
        lines.append(f"[confinement] notice: xcrun could not resolve {', '.join(expansion.unresolved)}; if these are "
                     "xcrun launchers, their real tools are not allowed and confined calls to them will fail (exit 71)")
    return lines


def unresolved_launchers(program: str, exec_paths: list[str], resolver: LauncherResolver, *,
                         platform: str | None = None,
                         lstat: Callable[[str], os.stat_result] | None = None) -> list[str]:
    """The ``/usr/bin`` launchers *program* needs (itself, or via :data:`LAUNCHER_WRAPPERS`) that won't work confined.

    Args:
        program: The command's resolved ``argv[0]`` (its path, not its real path: elan's proxies are symlinks).
        exec_paths: The command's exec allowlist.
        resolver: The launcher resolver.
        platform: ``sys.platform`` by default; only ``"darwin"`` has launchers.
        lstat: The ownership check's ``os.lstat`` (test seam).

    Returns:
        Launcher paths that could not be resolved or failed the ownership check.

    Raises:
        Nothing.
    """
    if (platform or sys.platform) != "darwin":
        return []
    names = [os.path.basename(program)] if os.path.dirname(program) == _USR_BIN else []
    names += [n for n in LAUNCHER_WRAPPERS.get(os.path.basename(program), ())
              if exec_allows(f"{_USR_BIN}/{n}", exec_paths) and n not in names]
    paths = [f"{_USR_BIN}/{n}" for n in names if n != "xcrun"]
    return expand_exec_paths(paths, resolver, platform=platform, lstat=lstat).unresolved if paths else []


def launcher_warning(program: str, unresolved: list[str]) -> str | None:
    """The runner's WARNING for :func:`unresolved_launchers`, or None when there are none.

    Args:
        program: The command's resolved ``argv[0]``.
        unresolved: From :func:`unresolved_launchers`.

    Returns:
        The warning text.

    Raises:
        Nothing.
    """
    if not unresolved:
        return None
    return (f"WARNING: {', '.join(unresolved)} may be an xcrun launcher that could not be resolved; confined calls "
            f"to it from {program} will fail (exit 71), and a tool that misreads that failure may act on empty output")


def launcher_report(exec_lists: dict[str, list[str]], resolver: LauncherResolver, *,
                    platform: str | None = None,
                    usr_bin_names: Callable[[], list[str]] = _usr_bin_launcher_names,
                    lstat: Callable[[str], os.stat_result] | None = None) -> list[str]:
    """``--check-wiring`` lines for every xcrun launcher in *exec_lists*, using the profile build's expansion.

    Args:
        exec_lists: ``{label: exec allowlist}``, e.g. ``confined_programs.lean`` or ``proposal_gates.lint``.
        resolver: The launcher resolver.
        platform: ``sys.platform`` by default; other platforms report nothing.
        usr_bin_names: As for :func:`expand_exec_paths`.
        lstat: As for :func:`expand_exec_paths`.

    Returns:
        Per allowlist with launchers: what was added, then a WARNING naming unresolved or refused ones.

    Raises:
        Nothing.
    """
    lines: list[str] = []
    for label in sorted(exec_lists):
        exp = expand_exec_paths(exec_lists[label], resolver, platform=platform, usr_bin_names=usr_bin_names,
                                lstat=lstat)
        if exp.extras:
            lines.append(f"{label}: xcrun launchers reach their real tools; also allowed: "
                         f"{', '.join(f'{k} {p}' for k, p in exp.extras)}")
        if exp.unresolved or exp.dropped:
            lines.append(f"WARNING: {label}: {', '.join(exp.unresolved + exp.dropped)} could not be resolved or "
                         "allowed; if they are xcrun launchers, confined calls to them will fail (exit 71)")
    return lines


# --- package stores under write roots (mathAgents M6) -------------------------------------------------------------
#: Package and dependency stores a tool may delete and re-create when a probe fails inside the sandbox.
PACKAGE_STORES: tuple[str, ...] = (".lake/packages", "node_modules", "site-packages", ".venv", "venv", "vendor",
                                   ".cargo/registry", "Pods", ".gradle")
_STORE_WALK_DEPTH = 6
_STORE_WALK_LIMIT = 20000


def _store_hit(parts: list[str]) -> str | None:
    """The store a relative path equals, lies inside, or (by its trailing components) is the parent part of."""
    for store in PACKAGE_STORES:
        sp = store.split("/")
        for i in range(len(parts)):
            window = parts[i:i + len(sp)]
            if window == sp or (i + len(window) == len(parts) and window == sp[:len(window)] and window):
                return store
    return None


def _store_below(top: Path) -> str | None:
    """The first package store below *top* (sorted walk, bounded depth and size, no symlinks), as a path."""
    count, stack = 0, [(top, 0)]
    while stack:
        path, depth = stack.pop(0)
        try:
            entries = sorted((e for e in os.scandir(path) if e.is_dir(follow_symlinks=False)), key=lambda e: e.name)
        except OSError:
            entries = []  # unreadable: nothing below it to report
        for entry in entries:
            count += 1
            rel = list(Path(os.path.relpath(entry.path, top)).parts)
            if any(rel[-len(s.split("/")):] == s.split("/") for s in PACKAGE_STORES):
                return entry.path
            if count >= _STORE_WALK_LIMIT:
                return None
            if depth + 1 < _STORE_WALK_DEPTH:
                stack.append((Path(entry.path), depth + 1))
    return None


def package_store_warnings(write_roots: list[str], root: Path | None = None) -> list[str]:
    """Warnings for write roots that equal, contain, or lie inside a :data:`PACKAGE_STORES` entry.

    Equality, lying inside a store, and ending in a store's leading part (``lean/.lake``) are read from the path.
    With *root*, an existing store further below a write root is also found by a bounded, breadth-first, sorted
    walk that follows no symlinks, so the result is deterministic. The operator chose a warning, not a refusal.

    Args:
        write_roots: Project-relative write roots.
        root: The project root, to look for stores below each write root; None checks the paths only.

    Returns:
        One warning per affected write root, in input order.

    Raises:
        Nothing.
    """
    out = []
    for rel in write_roots:
        store = _store_hit([p for p in rel.strip("/").split("/") if p not in ("", ".")])
        found = _store_below(root / rel) if store is None and root is not None else None
        if found is not None:
            store = os.path.relpath(found, root)
        if store is not None:
            out.append(f"warning: write root {rel!r} covers the package store {store!r}: tools may delete and "
                       "re-create it when a probe fails inside the sandbox. Narrow the root to build outputs")
    return out


def bwrap_argv(*, root: Path, write_roots: list[str], tmp_dir: Path, cwd: Path) -> list[str]:
    """Build the bwrap prefix for one confined run (decision C: no program allowlist on Linux).

    Args:
        root: The project root.
        write_roots: Project-relative directories the command may write.
        tmp_dir: The run's private ``TMPDIR``.
        cwd: The working directory inside the sandbox.

    Returns:
        The argv prefix; append ``--`` and the command.

    Raises:
        ConfinementError: When a path contains a character that can't be carried safely.
    """
    argv = [shutil.which("bwrap") or "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--bind", _real(str(tmp_dir)), _real(str(tmp_dir))]
    for w in write_roots:
        target = _real(str(root / w))
        argv += ["--bind", target, target]
    for p in PROTECTED_REL:  # re-mount read-only on top of any writable root that contains them
        target = root / p
        if target.exists():
            argv += ["--ro-bind", _real(str(target)), _real(str(target))]
    keys = Path(os.path.expanduser(KEY_DIR))
    if keys.exists():
        argv += ["--tmpfs", _real(str(keys))]
    argv += ["--unshare-pid", "--unshare-ipc", "--die-with-parent", "--new-session",
             "--chdir", _real(str(cwd))]
    return argv


def wrap(argv: list[str], *, sandbox: str, root: Path, cwd: Path, exec_paths: list[str],
         write_roots: list[str], tmp_dir: Path, resolver: LauncherResolver | None = None) -> list[str]:
    """Return *argv* wrapped in *sandbox*.

    For Seatbelt the exec allowlist is first expanded by :func:`expand_exec_paths` (xcrun launchers reach their
    real tool, as a ``literal``, and its helpers). An added path that is ``/``, home, an ancestor of the project, or overlaps a
    write root or the ``TMPDIR`` is dropped, as :func:`check_roots` would refuse it. Dropped and unresolved
    launchers get one-line notices on stderr.

    Args:
        argv: The command, with ``argv[0]`` already resolved to an absolute path.
        sandbox: ``"seatbelt"`` or ``"bwrap"``, from :func:`available`.
        root: The project root.
        cwd: The working directory.
        exec_paths: Seatbelt's program allowlist (ignored by bwrap: decision C).
        write_roots: Project-relative writable directories.
        tmp_dir: The run's private ``TMPDIR`` (must exist).
        resolver: The xcrun launcher resolver; a fresh :func:`xcrun_resolver` when None (Seatbelt only).

    Returns:
        The wrapped argv.

    Raises:
        ConfinementError: For an unknown sandbox, or a path that can't be expressed safely.
    """
    if sandbox == "seatbelt":
        expansion = expand_exec_paths(exec_paths, resolver or xcrun_resolver())
        for notice in expansion_notices(expansion):
            print(notice, file=sys.stderr)
        real_root, home = os.path.realpath(root), os.path.realpath(os.path.expanduser("~"))
        unsafe = [os.path.realpath(root / w) for w in write_roots] + [os.path.realpath(tmp_dir)]
        extra = [(k, e) for k, e in expansion.extras if e not in ("/", home) and not _within(real_root, e)
                 and not any(_within(e, u) or _within(u, e) for u in unsafe)]
        profile = seatbelt_profile(root=root, exec_paths=[*exec_paths, *(e for k, e in extra if k == "subpath")],
                                   write_roots=write_roots, tmp_dir=tmp_dir,
                                   exec_literals=[e for k, e in extra if k == "literal"])
        return ["/usr/bin/sandbox-exec", "-p", profile, *argv]
    if sandbox == "bwrap":
        return [*bwrap_argv(root=root, write_roots=write_roots, tmp_dir=tmp_dir, cwd=cwd), "--", *argv]
    raise ConfinementError(f"unknown sandbox {sandbox!r}")


#: The per-agent ``confined_programs`` opt-in that lets a run proceed when it needs an unresolved xcrun launcher
#: *and* a write root covers a package store (mathAgents M6's combination; see :func:`launcher_store_refusal`).
OPT_IN_LAUNCHER_STORE = "allow_unresolved_launcher_with_package_store"


def launcher_store_refusal(agent: str, unresolved: list[str], store_warnings: list[str],
                           jail: dict[str, Any]) -> str | None:
    """Why a confined run must be refused for the M6 combination, or None.

    Either condition alone only warns. Together, a tool whose launcher fails (exit 71) can misread the failure and
    delete the package store it can write, as ``lake`` deleted ``.lake/packages/mathlib``.

    Args:
        agent: The agent whose ``confined_programs`` entry applies.
        unresolved: From :func:`unresolved_launchers`.
        store_warnings: From :func:`package_store_warnings` for the agent's write roots.
        jail: The agent's validated entry (:func:`load_confined`).

    Returns:
        The refusal message, naming both conditions and the opt-in, or None.

    Raises:
        Nothing.
    """
    if not (unresolved and store_warnings) or jail.get(OPT_IN_LAUNCHER_STORE) is True:
        return None
    stores = "; ".join(w.removeprefix("warning: ").split(":", 1)[0] for w in store_warnings)
    return (f"refused: {', '.join(unresolved)} could not be resolved (confined calls to it fail with exit 71) and "
            f"{stores}. A tool that misreads that failure may delete the store. Narrow the write root to build "
            f"outputs, fix xcrun (xcode-select -p), or set confined_programs.{agent}.{OPT_IN_LAUNCHER_STORE}: true")


def load_confined(raw: Any, gates: dict[str, Any], control_plane_of: Callable[[str], Any]) -> dict[str, dict[str, Any]]:
    """Validate ``confined_programs`` and gates' optional ``exec``.

    Exec paths must be absolute (or ``~/``). Write roots must be safe relative paths clear of the project
    root, the ledger, the queue, ``.git`` and the control plane. No exec path may lie inside a write root, or a
    command could build a binary there and then run it.

    Args:
        raw: ``confined_programs``, from the operator-owned file (P5b) or else the brief.
        gates: The brief's ``proposal_gates`` (their optional ``exec`` lists are checked).
        control_plane_of: :func:`agentteams.frameworks._write_roots.control_plane_of`.

    Returns:
        ``{agent: {"exec": [...], "write": [...]}}``, plus ``OPT_IN_LAUNCHER_STORE: True`` when set.

    Raises:
        ConfinementError: On any malformed or unsafe entry.
    """
    if not isinstance(raw, dict):
        raise ConfinementError("confined_programs must be an object of {agent: {exec, write}}")
    for name, gate in gates.items():
        for path in gate.get("exec") or []:
            if not (isinstance(path, str) and (path.startswith("/") or path.startswith("~/"))):
                raise ConfinementError(f"proposal_gates.{name}.exec entries must be absolute or ~/ paths")
    confined: dict[str, dict[str, list[str]]] = {}
    for agent, spec in raw.items():
        if not (isinstance(spec, dict) and set(spec) <= {"exec", "write", OPT_IN_LAUNCHER_STORE}):
            raise ConfinementError(f"confined_programs.{agent} must be {{exec, write}} (and optionally "
                                   f"{OPT_IN_LAUNCHER_STORE})")
        opt_in = spec.get(OPT_IN_LAUNCHER_STORE, False)
        if not isinstance(opt_in, bool):
            raise ConfinementError(f"confined_programs.{agent}.{OPT_IN_LAUNCHER_STORE} must be true or false")
        exec_paths, write = spec.get("exec") or [], spec.get("write") or []
        if not (isinstance(exec_paths, list) and isinstance(write, list)):
            raise ConfinementError(f"confined_programs.{agent}.exec and .write must be lists")
        if not exec_paths:
            raise ConfinementError(f"confined_programs.{agent}.exec is empty; nothing could run")
        for path in exec_paths:
            if not (isinstance(path, str) and (path.startswith("/") or path.startswith("~/"))):
                raise ConfinementError(f"confined_programs.{agent}.exec entries must be absolute or ~/ paths")
        for rel in write:
            if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or ".." in rel.split("/"):
                raise ConfinementError(f"confined_programs.{agent}.write entries must be project-relative paths")
            if any(c in rel for c in "*?["):
                # Literal only: the runner's brief- and protected-path containment checks treat these as paths.
                raise ConfinementError(f"confined_programs.{agent}.write {rel!r} contains a wildcard; give a "
                                       "literal directory")
            top = rel.strip("/").split("/")[0]
            if rel.strip("/") in (".", "") or top in PROTECTED_REL or control_plane_of(rel):
                raise ConfinementError(f"confined_programs.{agent}.write {rel!r} covers the project root, the ledger, "
                                    "the queue, .git or the control plane; refused")
        # The exec-inside-a-write-root check needs the project root: see check_roots (run time).
        confined[agent] = {"exec": exec_paths, "write": write, **({OPT_IN_LAUNCHER_STORE: True} if opt_in else {})}
    return confined


def confined_file_for(root: Path) -> Path:
    """The operator-owned ``confined_programs`` file for *root* (P5b).

    Machine paths are per-machine facts, so they can live here instead of in the committed brief. The file sits
    outside the project, where no session sandbox lets an agent write, and the emitted ``permissions.deny``
    covers the built-in Edit/Write tools too. The name is the project directory's basename plus a hash of its
    real path, so two checkouts with the same name never share a file.

    Args:
        root: The project root.

    Returns:
        ``~/.config/agentteams/confined/<basename>-<sha256 of the real path, 12 hex>.json``, expanded.

    Raises:
        Nothing.
    """
    real = os.path.realpath(root)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(real)) or "root"
    digest = hashlib.sha256(real.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    return Path(os.path.expanduser(CONFINED_DIR)) / f"{name}-{digest}.json"


def _custody_check(path: Path, st: os.stat_result, *, directory: bool) -> os.stat_result:
    if not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
        raise ConfinementError(f"{path} must be a real {'directory' if directory else 'file'}, not a symlink")
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ConfinementError(f"{path} must be owned by you and not group- or world-writable")
    return st


def _check_custody(path: Path, *, directory: bool) -> os.stat_result:
    return _custody_check(path, os.lstat(path), directory=directory)


def _inside(path: Path, root: Path) -> bool:
    """Whether *path* resolves inside *root*; case-folded on macOS, whose default filesystem ignores case."""
    a, b = os.path.realpath(path), os.path.realpath(root).rstrip(os.sep) + os.sep
    if sys.platform == "darwin":
        a, b = a.lower(), b.lower()
    return a.startswith(b)


def confined_bytes(data: dict[str, Any]) -> bytes:
    """The exact bytes :func:`install_confined_file` writes for *data*: what the operator reviews and hashes.

    Args:
        data: The ``confined_programs`` object.

    Returns:
        Sorted, indented JSON with a trailing newline, UTF-8 encoded.

    Raises:
        TypeError: When *data* is not JSON-serialisable.
    """
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_confined_file(root: Path) -> tuple[dict[str, Any], str] | None:
    """Read the operator-owned ``confined_programs`` file for *root*, if there is one (P5b).

    Args:
        root: The project root.

    Returns:
        ``(confined_programs, sha256 of the file)``, or None when the file does not exist.

    Raises:
        ConfinementError: When the file or its directory is a symlink, is not owned by the current user, is
            group- or world-writable, lies inside the project, is too large, or is not a JSON object.
    """
    path = confined_file_for(root)
    if not os.path.lexists(path):
        return None
    _check_custody(path.parent, directory=True)
    before = _check_custody(path, directory=False)
    if _inside(path, root):
        raise ConfinementError(f"{path} lies inside the project; agents could write it")
    # The shared custody read opens O_NOFOLLOW|O_NONBLOCK (a FIFO swapped in can't hang the runner) and re-checks
    # the file it actually opened, so a swap between the lstat above and the open is refused rather than read.
    def _same_file(opened: os.stat_result) -> None:
        _custody_check(path, opened, directory=False)
        if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
            raise ConfinementError(f"{path} changed while it was being read; retry")

    try:
        raw = read_regular_nofollow(path, MAX_CONFINED_BYTES, check=_same_file)
    except ValueError as exc:
        raise ConfinementError(f"{path} is {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ConfinementError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfinementError(f"{path} must hold a JSON object of {{agent: {{exec, write}}}}")
    return data, hashlib.sha256(raw).hexdigest()


def install_confined_file(root: Path, data: dict[str, Any]) -> Path:
    """Write *data* as the operator-owned ``confined_programs`` file for *root* (mode 0600, atomic).

    The caller validates *data* first (``--install-confined`` runs it through the brief's policy and requires a
    matching ``--confirm-review-sha256``). An agent never calls this: under the switch the orchestrator writes
    only a candidate JSON and gives the user the one ``--install-confined`` command to run outside every session.

    Args:
        root: The project root.
        data: The ``confined_programs`` object.

    Returns:
        The path written.

    Raises:
        ConfinementError: When the directory fails the custody checks.
        OSError: When the file can't be written.
    """
    path = confined_file_for(root)
    if _inside(path, root):
        raise ConfinementError(f"{path} would lie inside the project; agents could write it")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_custody(path.parent, directory=True)
    if os.path.lexists(path):
        _check_custody(path, directory=False)
    return write_new_atomic(path.parent, path.name, confined_bytes(data))


def exec_inside_write_roots(root: Path, exec_paths: list[str], write_roots: list[str]) -> str | None:
    """The first exec path overlapping one of *write_roots* (resolved, in either direction), or None.

    A gate runs with no write roots of its own, so ``check_roots`` can't see an overlap with the agents' write
    roots. An exec path inside a write root, or containing one (exec paths match as subpaths), would let a
    confined command build a binary there and a later gate run it.

    Args:
        root: The project root.
        exec_paths: A gate's exec allowlist.
        write_roots: Every agent's project-relative write roots.

    Returns:
        The offending exec path, or None.

    Raises:
        Nothing.
    """
    fold = (lambda s: s.lower()) if sys.platform == "darwin" else (lambda s: s)  # APFS ignores case, like _inside
    for rel in write_roots:
        wreal = fold(os.path.realpath(root / rel))
        for path in exec_paths:
            real = fold(os.path.realpath(os.path.expanduser(path)))
            if _within(real, wreal) or _within(wreal, real):
                return path
    return None


def exec_allows(program: str, exec_paths: list[str]) -> bool:
    """Whether *program* (resolved) lies under one of *exec_paths*.

    Args:
        program: An absolute program path.
        exec_paths: Allowed programs or directories.

    Returns:
        True when the program's real path equals or lies under an allowed path.

    Raises:
        Nothing.
    """
    real = os.path.realpath(program)
    for path in exec_paths:
        allowed = os.path.realpath(os.path.expanduser(path))
        if real == allowed or real.startswith(allowed.rstrip(os.sep) + os.sep):
            return True
    return False


def gate_exec_paths(gate: dict[str, Any], program: str) -> list[str]:
    """A gate's program allowlist: its declared ``exec``, else its own program plus a shebang interpreter.

    Args:
        gate: The gate's brief entry.
        program: The gate's resolved program.

    Returns:
        The allowed exec paths.

    Raises:
        Nothing.
    """
    if gate.get("exec"):
        return list(gate["exec"])
    paths = [os.path.realpath(program)]
    try:
        with open(program, "rb") as fh:
            first = fh.readline(512)
    except OSError:
        first = b""
    if first.startswith(b"#!"):
        interp = first[2:].strip().split(b" ")[0].decode("utf-8", "replace")
        if interp.startswith("/"):
            paths.append(os.path.realpath(interp))
    return paths


def _within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def check_roots(root: Path, exec_paths: list[str], write_roots: list[str],
                tmp_dir: Path | None = None) -> list[str]:
    """Run-time checks of one agent's confinement against the real project, before anything runs.

    * Each write root must be a real directory inside *root*: no symlink anywhere below the root, so a root
      swapped for a link to ``~`` can't widen the writable set.
    * No exec path may be ``/``, ``~`` or an ancestor of the project.
    * Exec paths and writable places must not overlap in either direction, the run's ``TMPDIR`` included, or
      a command could build a binary and then run it.

    Args:
        root: The project root.
        exec_paths: The exec allowlist.
        write_roots: Project-relative write roots.
        tmp_dir: The run's private ``TMPDIR``, when known.

    Returns:
        The checked write roots as absolute real paths. Pass these to :func:`wrap`, so the sandbox uses exactly
        what was checked: a root swapped for a symlink after the check can't widen it.

    Raises:
        ConfinementError: On any violation.
    """
    real_root = os.path.realpath(root)
    home = os.path.realpath(os.path.expanduser("~"))
    writable: list[tuple[str, str]] = []
    for rel in write_roots:
        path = root
        for part in Path(rel).parts:
            path = path / part
            if path.is_symlink():
                raise ConfinementError(f"write root {rel!r} passes through a symlink ({path}); refused")
        if not path.is_dir():
            raise ConfinementError(f"write root {rel!r} is not an existing directory in the project")
        real = os.path.realpath(path)
        if not _within(real, real_root) or real == real_root:
            raise ConfinementError(f"write root {rel!r} resolves outside the project; refused")
        writable.append((rel, real))
    if tmp_dir is not None:
        writable.append(("TMPDIR", os.path.realpath(tmp_dir)))
    for path in exec_paths:
        real = os.path.realpath(os.path.expanduser(path))
        if real in ("/", home) or _within(real_root, real):
            raise ConfinementError(f"exec path {path!r} is too broad (/, home, or an ancestor of the project)")
        for rel, wreal in writable:
            if _within(real, wreal) or _within(wreal, real):
                raise ConfinementError(f"exec path {path!r} and write root {rel!r} overlap; a command could "
                                       "build a binary there and run it")
    return [real for rel, real in writable if rel != "TMPDIR"]
