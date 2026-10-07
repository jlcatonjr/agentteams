"""confinement.py — run one command inside an OS sandbox for the proposal runner (pilot P4b).

The out-of-session runner (:mod:`agentteams.proposal_runner`) holds the ledger key, so every command or gate it
starts must be confined. That includes each ``command-request`` and each pre-write gate.

* **macOS (Seatbelt).** An inline ``sandbox-exec -p`` profile. Reads are allowed. Writes are denied except
  under the declared write roots, a private ``TMPDIR`` and a few ``/dev`` nodes. The ledger, the queue,
  ``.git`` and the control plane are write-denied even inside a root. The key directory is read-denied.
  ``process-exec`` is denied except under the declared ``exec`` paths, matched as subpaths so a toolchain
  can run its own helpers. ``/bin/sh`` re-executes ``/bin/bash``, so a shell-using program needs both.
* **Linux (bwrap).** The root is mounted read-only, and the write roots and the private ``TMPDIR`` are
  bind-mounted writable. The ledger, queue and ``.git`` inside them are re-mounted read-only, and the key
  directory is masked with an empty tmpfs. The command gets a new PID namespace with ``--die-with-parent``,
  so nothing it starts outlives it. Operator decision C: there is no program allowlist on Linux in P4b.

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
from pathlib import Path
from typing import Any, Callable

#: Project paths a confined command may never write, even inside a declared write root.
PROTECTED_REL = (".agentteams", ".agentteams-queue", ".git", ".claude", ".goose", ".codex", ".github")
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


def seatbelt_profile(*, root: Path, exec_paths: list[str], write_roots: list[str], tmp_dir: Path) -> str:
    """Build the Seatbelt profile for one confined run.

    Args:
        root: The project root.
        exec_paths: Programs or directories the command may execute (subpath match), already validated.
        write_roots: Project-relative directories the command may write.
        tmp_dir: The run's private ``TMPDIR``.

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
        ")",
    ]
    return "\n".join(lines) + "\n"


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
         write_roots: list[str], tmp_dir: Path) -> list[str]:
    """Return *argv* wrapped in *sandbox*.

    Args:
        argv: The command, with ``argv[0]`` already resolved to an absolute path.
        sandbox: ``"seatbelt"`` or ``"bwrap"``, from :func:`available`.
        root: The project root.
        cwd: The working directory.
        exec_paths: Seatbelt's program allowlist (ignored by bwrap: decision C).
        write_roots: Project-relative writable directories.
        tmp_dir: The run's private ``TMPDIR`` (must exist).

    Returns:
        The wrapped argv.

    Raises:
        ConfinementError: For an unknown sandbox, or a path that can't be expressed safely.
    """
    if sandbox == "seatbelt":
        profile = seatbelt_profile(root=root, exec_paths=exec_paths, write_roots=write_roots, tmp_dir=tmp_dir)
        return ["/usr/bin/sandbox-exec", "-p", profile, *argv]
    if sandbox == "bwrap":
        return [*bwrap_argv(root=root, write_roots=write_roots, tmp_dir=tmp_dir, cwd=cwd), "--", *argv]
    raise ConfinementError(f"unknown sandbox {sandbox!r}")


def load_confined(raw: Any, gates: dict[str, Any], control_plane_of: Callable[[str], Any]) -> dict[str, dict[str, list[str]]]:
    """Validate ``confined_programs`` and gates' optional ``exec``.

    Exec paths must be absolute (or ``~/``). Write roots must be safe relative paths clear of the project
    root, the ledger, the queue, ``.git`` and the control plane. No exec path may lie inside a write root, or a
    command could build a binary there and then run it.

    Args:
        raw: The brief's ``confined_programs``.
        gates: The brief's ``proposal_gates`` (their optional ``exec`` lists are checked).
        control_plane_of: :func:`agentteams.frameworks._write_roots.control_plane_of`.

    Returns:
        ``{agent: {"exec": [...], "write": [...]}}``.

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
        if not (isinstance(spec, dict) and set(spec) <= {"exec", "write"}):
            raise ConfinementError(f"confined_programs.{agent} must be {{exec, write}}")
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
        # The exec-inside-a-write-root check needs the project root: see check_overlap (run time).
        confined[agent] = {"exec": exec_paths, "write": write}
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
    # O_NONBLOCK: a FIFO swapped in after the lstat must not hang the runner. The fstat re-checks the file that
    # was actually opened, so a swap between the checks and the open is refused rather than read.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = _custody_check(path, os.fstat(fd), directory=False)
        if (opened.st_ino, opened.st_dev) != (before.st_ino, before.st_dev):
            raise ConfinementError(f"{path} changed while it was being read; retry")
        raw = os.read(fd, MAX_CONFINED_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_CONFINED_BYTES:
        raise ConfinementError(f"{path} is larger than {MAX_CONFINED_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ConfinementError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfinementError(f"{path} must hold a JSON object of {{agent: {{exec, write}}}}")
    return data, hashlib.sha256(raw).hexdigest()


def install_confined_file(root: Path, data: dict[str, Any]) -> Path:
    """Write *data* as the operator-owned ``confined_programs`` file for *root* (mode 0600, atomic).

    The caller validates *data* first (``--install-confined`` runs it through the brief's policy). An agent
    never calls this: under the switch the orchestrator writes a script that runs ``--install-confined`` and the
    user runs it outside every session.

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
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, confined_bytes(data))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    return path


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
