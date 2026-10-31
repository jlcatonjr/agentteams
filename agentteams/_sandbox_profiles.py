"""_sandbox_profiles.py — the pure OS-sandbox profile builders (Seatbelt SBPL, bwrap argv), the platform probe, and
the snapshot copy that stands in for an OS-enforced read-only mount when no profile can nest (:func:`snapshot_tree`).

One source of truth for two callers (METAPLAN v2 §6 R2):

* :mod:`agentteams.confinement` (the proposal runner's sandbox) imports everything here and wraps the builders
  with its own constants (``PROTECTED_REL``, ``KEY_DIR``, ``_DEV_WRITES``), so its output is unchanged.
* :mod:`agentteams.wasm_exec` receives this module as an explicit ``profiles=`` argument and nests wasmtime in it.
  The standalone ``agentteams_wasm`` bundle exec-loads a byte-identical copy of this file, so it may import
  **nothing from agentteams** (an AST test enforces that). Every project-specific constant is a parameter.

Three parameters exist only for the WASM nesting, and each is a Rule-12 exclusion with a stated bound:

* ``deny_network`` adds ``(deny network*)`` / ``--unshare-net``. It only removes authority.
* ``deny_reads`` read-denies credential stores (R5). bwrap skips absent paths (nothing to hide); Seatbelt denies
  by path string, so an absent path is denied too.
* ``late_write_allows`` / ``late_binds`` re-open **one** directory inside the write-denied ``.agentteams`` tree:
  the guest scratch (R8, R11). The builders refuse any path outside ``.agentteams/wasm/scratch/<agent>`` or
  ``.agentteams/wasm/exec/<run-id>/scratch``, so the allow cannot widen to the rest of the control plane.

It also holds the one credential-name deny list (:func:`credential_name`, :func:`credential_scan`) that
``wasm_config`` (brief read paths), ``runner_wasm`` (runner jails) and ``wasm_exec`` (materialize, through
``profiles=``) share, so the three can never drift (wave-3 @security S1). ``.env*`` is decided once: every segment
starting ``.env`` is credential material except the literal ``.env.example`` / ``.env.sample`` / ``.env.template``.

Stdlib only.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

#: Characters a path may not carry into SBPL or a bwrap argument.
BAD_PATH_CHARS = ('"', "\n", "\r", "\\", "\0")

#: The only shapes a late write-allow may take, relative to the project root (R8/R11).
_LATE_ALLOW_SHAPES = (
    re.compile(r"^\.agentteams/wasm/scratch/[a-z0-9][a-z0-9-]{0,63}$"),
    re.compile(r"^\.agentteams/wasm/exec/[0-9a-f]{16}/scratch$"),
)


class ConfinementError(Exception):
    """A path can't be expressed safely, or no sandbox is usable."""


# --- the shared credential-name deny list (wave-3 @security S1/S3) -------------------------------------------------

#: Directory names that hold credentials (compared lowercased).
CREDENTIAL_PARTS = (".ssh", ".aws", ".gnupg", ".kube", ".docker")
#: Exact credential file names.
CREDENTIAL_NAMES = (".netrc", ".npmrc", ".pypirc", ".pgpass", ".htpasswd", ".git-credentials")
#: Credential name prefixes: every ``.env*`` (``.env``, ``.envrc``, ``.env.local``), SSH keys (``id_*``), and
#: ``credentials*``.
CREDENTIAL_PREFIXES = (".env", "id_", "credentials")
#: Credential name suffixes (keys, keystores, Terraform state and variables, ``*.env``).
CREDENTIAL_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".tfstate", ".tfvars", ".env")
#: Infixes: ``*.tfstate.*`` backups.
CREDENTIAL_INFIXES = (".tfstate.",)
#: The ``.env`` templates that carry no secret (the one allowance, decided here once).
CREDENTIAL_ALLOWED = (".env.example", ".env.sample", ".env.template")
#: Caps of :func:`credential_scan`; reaching either refuses (a tree too big to check is not granted).
CREDENTIAL_SCAN_MAX_ENTRIES = 20_000
CREDENTIAL_SCAN_MAX_DEPTH = 32


def credential_name(segment: str) -> bool:
    """Whether one path segment names credential material no WASM guest may see.

    Args:
        segment: One path component (compared lowercased, so ``.SSH`` matches on any filesystem).

    Returns:
        True when it matches the parts, names, prefixes, suffixes or infixes and is not an allowed ``.env`` template.

    Raises:
        Nothing.
    """
    low = segment.lower()
    if low in CREDENTIAL_ALLOWED:
        return False
    return (low in CREDENTIAL_PARTS or low in CREDENTIAL_NAMES or low.startswith(CREDENTIAL_PREFIXES)
            or low.endswith(CREDENTIAL_SUFFIXES) or any(i in low for i in CREDENTIAL_INFIXES))


def credential_scan(top: str, *, max_entries: int = CREDENTIAL_SCAN_MAX_ENTRIES,
                    max_depth: int = CREDENTIAL_SCAN_MAX_DEPTH) -> list[str]:
    """Return every credential-named entry under the directory *top* (a bounded walk; symlinks are not followed).

    Every entry's own name is checked, symlinks included (a link named ``.env`` is refused whatever it points to).

    Args:
        top: The real directory a grant would mount.
        max_entries: The entry cap.
        max_depth: The directory-depth cap (``top`` is depth 0).

    Returns:
        The matching paths relative to *top* (POSIX, sorted); empty when the tree is clean.

    Raises:
        ConfinementError: A cap is reached or a directory cannot be listed (fail closed: an unchecked tree is
            never granted).
    """
    hits: list[str] = []
    seen = 0
    stack: list[tuple[str, int]] = [("", 0)]
    while stack:
        rel, depth = stack.pop()
        try:
            with os.scandir(os.path.join(top, rel) if rel else top) as it:
                entries = list(it)
        except OSError as exc:
            raise ConfinementError(f"credential scan cannot list {rel or '.'} ({exc.strerror}); refused") from exc
        for e in entries:
            seen += 1
            if seen > max_entries:
                raise ConfinementError(f"credential scan stopped at {max_entries} entries; a mount this large "
                                       "cannot be checked and is refused")
            child = f"{rel}/{e.name}" if rel else e.name
            if credential_name(e.name):
                hits.append(child)
            elif e.is_dir(follow_symlinks=False):
                if depth + 1 > max_depth:
                    raise ConfinementError(f"credential scan stopped at depth {max_depth} ({child}); refused")
                stack.append((child, depth + 1))
    return sorted(hits)


def refuse_credentials(top: str, what: str) -> None:
    """Raise unless :func:`credential_scan` finds nothing under *top*.

    Args:
        top: The directory a grant mounts.
        what: The grant entry for the message (``/project/docs``, ``wasm_sandbox.agents.a.read_paths``).

    Returns:
        None.

    Raises:
        ConfinementError: Credential-named entries were found (the first few are named), or the scan hit a cap.
    """
    hits = credential_scan(top)
    if hits:
        more = f" and {len(hits) - 5} more" if len(hits) > 5 else ""
        raise ConfinementError(f"{what} holds credential-named entries ({', '.join(hits[:5])}{more}); a WASM grant "
                               "never mounts them: move them out or grant a narrower directory")


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
    if any(c in path for c in BAD_PATH_CHARS) or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise ConfinementError(f"path {path!r} contains a character that can't be expressed safely; refused")
    return path


def _real(path: str) -> str:
    return _clean(os.path.realpath(os.path.expanduser(path)))


def check_late_allow(root: Path, path: str) -> str:
    """Validate one late write-allow (the WASM guest scratch) and return its real path.

    Args:
        root: The project root.
        path: The directory to re-open for writes inside the protected ``.agentteams`` tree.

    Returns:
        The real path.

    Raises:
        ConfinementError: When the real path is not exactly ``<root>/.agentteams/wasm/scratch/<agent>`` or
            ``<root>/.agentteams/wasm/exec/<run-id>/scratch``.
    """
    real_root, real = _real(str(root)), _real(path)
    rel = os.path.relpath(real, real_root).replace(os.sep, "/")
    if not any(shape.match(rel) for shape in _LATE_ALLOW_SHAPES):
        raise ConfinementError(f"late write-allow {path!r} is not a WASM scratch directory; refused")
    return real


def seatbelt_profile(*, root: Path, exec_paths: Sequence[str], write_roots: Sequence[str], tmp_dir: Path,
                     exec_literals: Sequence[str] = (), protected_rel: Sequence[str], key_dir: str,
                     dev_writes: Sequence[str], late_write_allows: Sequence[str] = (),
                     deny_reads: Sequence[str] = (), deny_network: bool = False) -> str:
    """Build the Seatbelt profile for one confined run.

    Args:
        root: The project root.
        exec_paths: Programs or directories the command may execute (subpath match), already validated.
        write_roots: Project-relative directories the command may write.
        tmp_dir: The run's private ``TMPDIR``.
        exec_literals: Programs the command may execute by exact path only (``literal``).
        protected_rel: Project-relative paths write-denied even inside a write root.
        key_dir: The key directory, read- and write-denied.
        dev_writes: ``/dev`` nodes the command may write.
        late_write_allows: WASM scratch dirs re-allowed after the protected deny (see :func:`check_late_allow`).
        deny_reads: Extra paths read-denied (credential stores), matched as subpaths.
        deny_network: Append ``(deny network*)``.

    Returns:
        SBPL text for ``sandbox-exec -p``.

    Raises:
        ConfinementError: When a path contains a character SBPL can't carry safely, or a late allow is not a
            WASM scratch directory.
    """
    real_root = _real(str(root))
    writes = [_real(str(tmp_dir))] + [_real(str(root / w)) for w in write_roots]
    protected = [_real(str(root / p)) for p in protected_rel]
    late = [check_late_allow(root, p) for p in late_write_allows]
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write*",
        *[f'    (subpath "{w}")' for w in writes],
        *[f'    (literal "{d}")' for d in dev_writes],
        ")",
        "(deny file-write*",
        *[f'    (subpath "{p}")' for p in protected],
        f'    (literal "{real_root}")',
        f'    (subpath "{_real(key_dir)}")',
        '    (regex #"/\\.[gG][iI][tT](/|$)")',  # a nested repo inside a write root (case-insensitive: APFS)
        ")",
    ]
    if late:
        lines += ["(allow file-write*", *[f'    (subpath "{p}")' for p in late], ")",
                  '(deny file-write* (regex #"/\\.[gG][iI][tT](/|$)"))']  # no nested repo inside the scratch
    lines.append(f'(deny file-read* (subpath "{_real(key_dir)}"))')
    if deny_reads:
        lines += ["(deny file-read*", *[f'    (subpath "{_real(p)}")' for p in deny_reads], ")"]
    lines += [
        "(deny process-exec*)",
        "(allow process-exec*",
        *[f'    (subpath "{_real(e)}")' for e in exec_paths],
        *[f'    (literal "{_real(e)}")' for e in exec_literals],
        ")",
    ]
    if deny_network:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def bwrap_argv(*, root: Path, write_roots: Sequence[str], tmp_dir: Path, cwd: Path, protected_rel: Sequence[str],
               key_dir: str, late_binds: Sequence[str] = (), deny_reads: Sequence[str] = (),
               deny_network: bool = False) -> list[str]:
    """Build the bwrap prefix for one confined run (no program allowlist on Linux).

    Args:
        root: The project root.
        write_roots: Project-relative directories the command may write.
        tmp_dir: The run's private ``TMPDIR``.
        cwd: The working directory inside the sandbox.
        protected_rel: Project-relative paths re-mounted read-only on top of any writable root.
        key_dir: The key directory, masked with an empty tmpfs.
        late_binds: WASM scratch dirs bound writable after the protected re-mounts (see :func:`check_late_allow`).
        deny_reads: Extra paths hidden: an existing directory gets a tmpfs, an existing file ``/dev/null``;
            absent paths are skipped.
        deny_network: Add ``--unshare-net``.

    Returns:
        The argv prefix; append ``--`` and the command.

    Raises:
        ConfinementError: When a path contains a character that can't be carried safely, or a late bind is not a
            WASM scratch directory.
    """
    argv = [shutil.which("bwrap") or "bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--bind", _real(str(tmp_dir)), _real(str(tmp_dir))]
    for w in write_roots:
        target = _real(str(root / w))
        argv += ["--bind", target, target]
    for p in protected_rel:  # re-mount read-only on top of any writable root that contains them
        target = root / p
        if target.exists():
            argv += ["--ro-bind", _real(str(target)), _real(str(target))]
    for p in late_binds:
        real = check_late_allow(root, p)
        argv += ["--bind", real, real]
    keys = Path(os.path.expanduser(key_dir))
    if keys.exists():
        argv += ["--tmpfs", _real(str(keys))]
    for p in deny_reads:
        hide = Path(os.path.expanduser(p))
        if hide.is_dir():
            argv += ["--tmpfs", _real(str(hide))]
        elif hide.exists():
            argv += ["--ro-bind", "/dev/null", _real(str(hide))]
    argv += ["--unshare-pid", "--unshare-ipc", *(["--unshare-net"] if deny_network else []),
             "--die-with-parent", "--new-session", "--chdir", _real(str(cwd))]
    return argv


# --- read-only fallback: the snapshot copy (WASM, when no profile can nest; METAPLAN v2 R7) ---------------------------

def _charge(budget: list[int], nbytes: int = 0, entries: int = 0) -> None:
    budget[0] -= nbytes
    budget[1] -= entries
    if budget[0] < 0 or budget[1] < 0:
        raise ConfinementError("read mount too large to snapshot; enable an OS sandbox")


def _snap_dir(dfd: int, dst: str, budget: list[int]) -> None:
    os.mkdir(dst, 0o700)
    with os.scandir(dfd) as entries:
        names = sorted(e.name for e in entries)
    for name in names:
        _charge(budget, entries=1)
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        is_dir, is_reg = stat.S_ISDIR(st.st_mode), stat.S_ISREG(st.st_mode) and st.st_nlink == 1
        if not (is_dir or is_reg):
            what = "a hardlink" if stat.S_ISREG(st.st_mode) else "a symlink" if stat.S_ISLNK(st.st_mode) else \
                "a FIFO, socket or device"
            raise ConfinementError(f"snapshot: {name} is {what}; refused")
        flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if is_dir else os.O_NONBLOCK)
        cfd = os.open(name, flags, dir_fd=dfd)
        try:
            fst = os.fstat(cfd)
            if fst.st_ino != st.st_ino or fst.st_mode != st.st_mode or (is_reg and fst.st_nlink != 1):
                raise ConfinementError(f"snapshot: {name} changed during the copy")
            if is_dir:
                _snap_dir(cfd, os.path.join(dst, name), budget)
                continue
            out = os.open(os.path.join(dst, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            try:
                for chunk in iter(lambda: os.read(cfd, 1 << 20), b""):
                    _charge(budget, nbytes=len(chunk))
                    os.write(out, chunk)
            finally:
                os.close(out)
        finally:
            os.close(cfd)
    os.chmod(dst, 0o500)


def snapshot_tree(src: str, dst: str, budget: list[int]) -> None:
    """Copy the directory *src* to the new *dst* for a read-only mount no OS profile can enforce.

    The walk opens every directory and file relative to its parent's descriptor with ``O_NOFOLLOW`` and re-checks
    each entry with ``fstat`` after opening, so a path swapped mid-walk is refused rather than followed. Symlinks,
    hardlinked files, FIFOs, sockets and devices are refused. Copies are 0o400 and directories 0o500.

    Args:
        src: The real source directory.
        dst: The destination, which must not exist (its parent must).
        budget: ``[bytes, entries]`` still allowed; decremented during the copy, shared across one run's mounts.

    Returns:
        None.

    Raises:
        ConfinementError: On a refused entry, a change during the walk, an exhausted budget, or a platform
            without ``dir_fd`` support.
        OSError: On an I/O failure.
    """
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ConfinementError("read-only mounts without an OS sandbox need dir_fd support (not on this platform)")
    fd = os.open(src, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _snap_dir(fd, dst, budget)
    finally:
        os.close(fd)
