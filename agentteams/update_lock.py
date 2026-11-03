"""update_lock.py — a per-checkout update lock and an append-only run record.

Two self-updates of the same checkout ran unrecorded, minutes apart, the first while a cleared handoff
was in flight; ``build-log.json`` keeps only the last run, so nothing showed that two had happened.
:func:`update_run` makes concurrent team writes visible:

* **Lock.** An exclusive, non-blocking ``flock`` on ``<git-dir>/agentteams-update.lock``. A second run
  against the same checkout refuses before writing anything and names the holder. The OS releases the
  lock when the holding process dies, so a crash leaves nothing stale.
* **Run record.** One CSV row per locked run in ``<git-dir>/agentteams-update-runs.log.csv``, appended
  while the lock is still held.

**Where.** The *per-checkout* git directory (a linked worktree's own ``.git/worktrees/<name>``): never
tracked, so consumer trees gain no file or directory and no output layout changes, and separate worktrees
stay independent. It is found without spawning git. Outside a git checkout the run proceeds unlocked with
a one-line notice.

**Custody.** The git directory is ``lstat``-ed and refused when it is a symlink; the lock and the record are
opened ``O_NOFOLLOW`` (a planted link is refused, never followed) with mode 0600. The holder JSON
(``{pid, started_at, invoker, argv_redacted}``) is a separate file replaced atomically, so a reader never
sees half a record and the locked inode is never swapped out from under ``flock``. Recorded argv keeps flag
names only; values are dropped except the ``--framework``, ``--shrink-policy`` and ``--adopt-only`` slugs.

**Fail-open cases.** No ``fcntl`` (non-POSIX), or ``EPERM``/``EACCES``/``EROFS`` creating the lock (a
sandbox), proceeds unlocked with a notice; under a sandbox the sandboxed-write preflight refuses the real
write anyway. Dry runs and inspection-only runs take no lock and write no record; they print a notice when
the lock is held, because what they read may be mid-write.

**Re-entrant per process.** A nested :func:`update_run` in a process that already holds the lock (an
in-process re-entry of the pipeline) is a no-op, so a run never refuses itself.
"""

from __future__ import annotations

import csv
import errno
import io
import json
import os
import re
import stat
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from agentteams.atomicio import read_regular_nofollow, write_new_atomic

LOCK_NAME = "agentteams-update.lock"
HOLDER_NAME = "agentteams-update.holder.json"
RECORD_NAME = "agentteams-update-runs.log.csv"
RECORD_FIELDS = ("started_at", "finished_at", "pid", "invoker", "mode", "framework", "argv",
                 "agentteams_commit", "exit")

#: Environment markers naming the agent shell a run came from (the markers the sign scripts check).
#: First match wins; no marker means a terminal. Advisory only: an environment can be forged.
INVOKER_ENV_MARKERS: tuple[tuple[str, str], ...] = (
    ("CLAUDECODE", "claude-code"),
    ("CLAUDE_CODE_ENTRYPOINT", "claude-code"),
    ("GOOSE_SESSION_ID", "goose"),
    ("GOOSE_TERMINAL", "goose"),
    ("CODEX_SANDBOX", "codex"),
    ("CODEX_SANDBOX_NETWORK_DISABLED", "codex"),
)

#: Flags whose value is kept in recorded argv (a framework id, a policy name, adopted slugs).
_KEPT_VALUE_FLAGS = frozenset({"--framework", "-f", "--shrink-policy", "--adopt-only"})
_SLUG_VALUE_RE = re.compile(r"^[a-z0-9][a-z0-9,_-]{0,199}$")
_FLAG_NAME_RE = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_SANDBOX_ERRNOS = frozenset({errno.EPERM, errno.EACCES, errno.EROFS})

#: Read-only flags; a run carrying one of them (and none of :data:`_WRITING_FLAGS`) takes no lock.
_INSPECTION_FLAGS = ("check", "list_backups", "scan_security", "check_budget", "check_rank",
                     "check_rank_strict", "check_wiring", "query_index", "query_code")
#: Flags that write even beside an inspection flag (they run first, or write on the adopt step).
_WRITING_FLAGS = ("adopt_only", "adopt_orphans", "overwrite", "refresh_index", "refresh_code_index",
                  "reconcile_front_matter")

_depth = 0  # >0 while this process holds (or deliberately skipped) the lock; nested runs are no-ops


class UpdateLockError(RuntimeError):
    """The lock location failed a custody check (a symlinked git dir or lock file); the run is refused."""


class UpdateLocked(UpdateLockError):
    """Another process holds the update lock for this checkout."""


class RunHandle:
    """What :func:`update_run` yields: the body reports its exit code through :meth:`set_exit`."""

    def __init__(self) -> None:
        """Start with no exit code; the record says ``unset`` if the body never sets one."""
        self.exit_code: int | None = None

    def set_exit(self, rc: int) -> None:
        """Record the run's exit code for the run record.

        Args:
            rc: The process exit code the run returns.

        Returns:
            None.
        """
        self.exit_code = rc


def find_git_dir(start: Path) -> Path | None:
    """Return the per-checkout git directory for *start*, without spawning git.

    Walks up from *start* to the first ``.git``. A directory is the git dir; a file is a linked worktree
    (or submodule) whose ``gitdir:`` line names it, relative to the file's directory when not absolute.

    Args:
        start: A path inside the checkout (need not exist yet).

    Returns:
        The git directory, or ``None`` outside a git checkout.

    Raises:
        UpdateLockError: ``.git`` or the directory a ``gitdir:`` line names is a symlink or not a
            directory, or a ``.git`` file has no ``gitdir:`` line.
        OSError: ``.git`` exists but cannot be read (a sandbox denying it).
    """
    for base in (start, *start.parents):
        dot_git = base / ".git"
        if not os.path.lexists(dot_git):
            continue
        st = os.lstat(dot_git)
        if stat.S_ISDIR(st.st_mode):
            return dot_git
        if not stat.S_ISREG(st.st_mode):
            raise UpdateLockError(f"{dot_git} is a symlink or not a regular file/directory")
        text = read_regular_nofollow(dot_git, 4096).decode("utf-8", "replace")
        match = re.search(r"^gitdir:\s*(.+?)\s*$", text, re.M)
        if not match:
            raise UpdateLockError(f"{dot_git} has no gitdir: line")
        target = Path(match.group(1))
        target = target if target.is_absolute() else base / target
        tst = os.lstat(target)
        if not stat.S_ISDIR(tst.st_mode):
            raise UpdateLockError(f"{target} (named by {dot_git}) is a symlink or not a directory")
        return target
    return None


def redact_argv(argv: Sequence[str]) -> list[str]:
    """Reduce *argv* to flag names, keeping only the values of the framework/policy/slug flags.

    Args:
        argv: The command-line arguments (without the program name).

    Returns:
        The flag names in order; ``--framework``, ``-f``, ``--shrink-policy`` and ``--adopt-only`` keep
        their value (inline ``=value`` or the next token) when it looks like a slug. Anything else —
        positional values, other flags' values, malformed flag names — is dropped.
    """
    out: list[str] = []
    keep_next = False
    for token in argv:
        token = str(token)
        if keep_next:
            keep_next = False
            if not token.startswith("-") and _SLUG_VALUE_RE.match(token):
                out.append(token)
                continue
        if not token.startswith("-") or token in ("-", "--"):
            continue
        name, has_value, value = token.partition("=")
        if not _FLAG_NAME_RE.match(name):
            continue
        out.append(name)
        if name in _KEPT_VALUE_FLAGS:
            if not has_value:
                keep_next = True
            elif _SLUG_VALUE_RE.match(value):
                out[-1] = f"{name}={value}"
    return out


def detect_invoker(env: Mapping[str, str] | None = None) -> str:
    """Name the agent shell this process runs in, from :data:`INVOKER_ENV_MARKERS`.

    Args:
        env: The environment to read (default ``os.environ``).

    Returns:
        ``claude-code``, ``goose``, ``codex`` or ``terminal``.
    """
    env = os.environ if env is None else env
    for var, name in INVOKER_ENV_MARKERS:
        if env.get(var):
            return name
    return "terminal"


def run_mode(args: Any, *, migrate_exemption: bool = False) -> str:
    """Classify a run for the record.

    Args:
        args: The parsed CLI namespace.
        migrate_exemption: The run is ``--migrate``'s delegated overwrite.

    Returns:
        ``migrate``, ``adopt-only``, ``restore-backup``, ``update``, ``overwrite`` or ``generate``.
    """
    if migrate_exemption:
        return "migrate"
    if getattr(args, "adopt_only", None):
        return "adopt-only"
    if getattr(args, "restore_backup", None) is not None:
        return "restore-backup"
    if getattr(args, "update", False):
        return "update"
    return "overwrite" if getattr(args, "overwrite", False) else "generate"


def is_lock_exempt(args: Any) -> bool:
    """Whether a run writes nothing and so takes no lock and leaves no record.

    True for ``--dry-run``, and for an inspection-only run (``--check``, ``--list-backups``, the scan/check
    modes, ``--query-index``/``--query-code``) that carries none of the flags that write first.

    Args:
        args: The parsed CLI namespace.

    Returns:
        ``True`` when the run is lock-exempt.
    """
    if getattr(args, "dry_run", False):
        return True
    if getattr(args, "restore_backup", None) is not None or any(getattr(args, f, None) for f in _WRITING_FLAGS):
        return False
    return any(getattr(args, f, None) for f in _INSPECTION_FLAGS)


def agentteams_commit() -> str:
    """Return the commit of the agentteams checkout this code runs from, read from git files (no git spawn).

    Returns:
        The full commit sha, or ``unknown`` (an installed wheel, a detached state that can't be read).
    """
    try:
        git_dir = find_git_dir(Path(__file__).resolve().parent)
        if git_dir is None:
            return "unknown"
        head = read_regular_nofollow(git_dir / "HEAD", 4096).decode("ascii", "replace").strip()
        if _SHA_RE.match(head):
            return head
        if not head.startswith("ref: "):
            return "unknown"
        ref = head[5:].strip()
        common = git_dir
        if (git_dir / "commondir").is_file():
            rel = (git_dir / "commondir").read_text(encoding="utf-8").strip()
            common = Path(rel) if Path(rel).is_absolute() else git_dir / rel
        for root in (git_dir, common):
            if (root / ref).is_file():
                sha = (root / ref).read_text(encoding="ascii").strip()
                return sha if _SHA_RE.match(sha) else "unknown"
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and _SHA_RE.match(parts[0]):
                    return parts[0]
    except (OSError, ValueError, UpdateLockError):
        return "unknown"
    return "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(value: Any, limit: int = 200) -> str:
    """Printable text only, truncated: holder JSON is read content and never echoed raw."""
    return "".join(c for c in str(value) if c.isprintable())[:limit]


def describe_holder(git_dir: Path) -> str:
    """Describe the current lock holder from its holder file, for a refusal or notice.

    Args:
        git_dir: The per-checkout git directory.

    Returns:
        A one-line description (pid, invoker, start time, recorded argv), or a placeholder when the
        holder file is missing or unreadable.
    """
    try:
        data = json.loads(read_regular_nofollow(git_dir / HOLDER_NAME, 16384))
    except (OSError, ValueError):
        return "holder unknown (no readable holder record)"
    if not isinstance(data, dict):
        return "holder unknown (malformed holder record)"
    argv = data.get("argv_redacted")
    argv_text = " ".join(_clean(a, 80) for a in argv) if isinstance(argv, list) else ""
    return (f"pid {_clean(data.get('pid'), 12)} ({_clean(data.get('invoker'), 20)}), started "
            f"{_clean(data.get('started_at'), 32)}, argv: {_clean(argv_text) or '(none)'}")


def _lock_held(git_dir: Path) -> bool:
    """Probe the lock without creating it: a shared, non-blocking flock that fails means it is held."""
    try:
        import fcntl
    except ImportError:
        return False
    if not (git_dir / HOLDER_NAME).exists():  # probe only when a holder is likely, to keep the race tiny
        return False
    try:
        fd = os.open(git_dir / LOCK_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def _append_record(git_dir: Path, row: dict[str, str]) -> None:
    """Append one row to the run record (created 0600 with a header), never through a link."""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow([row.get(k, "") for k in RECORD_FIELDS])
    fd = os.open(git_dir / RECORD_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"{git_dir / RECORD_NAME} is not a regular file")
        data = buf.getvalue()
        if st.st_size == 0:
            data = ",".join(RECORD_FIELDS) + "\n" + data
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)


def _open_lock(git_dir: Path) -> int | None:
    """Create/open the lock file and take the flock; ``None`` when the run must proceed unlocked.

    Raises:
        UpdateLocked: Another process holds the lock.
        UpdateLockError: The lock file is a symlink or not a regular file, or it can't be opened for a
            reason other than a sandbox denial.
    """
    try:
        import fcntl
    except ImportError:
        print("  ℹ  update lock: no fcntl on this platform; running unlocked and unrecorded.", file=sys.stderr)
        return None
    try:
        fd = os.open(git_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as exc:
        if exc.errno in _SANDBOX_ERRNOS:
            print(f"  ℹ  update lock: cannot create {git_dir / LOCK_NAME} ({exc.strerror}); running unlocked "
                  "and unrecorded.", file=sys.stderr)
            return None
        if exc.errno == errno.ELOOP:
            raise UpdateLockError(f"{git_dir / LOCK_NAME} is a symlink; refusing to follow it") from exc
        raise UpdateLockError(f"cannot open {git_dir / LOCK_NAME}: {exc}") from exc
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UpdateLockError(f"{git_dir / LOCK_NAME} is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise UpdateLocked(
                    f"another agentteams run is writing this checkout ({describe_holder(git_dir)}). "
                    f"Nothing was written. Wait for it to finish; past runs are in {git_dir / RECORD_NAME}."
                ) from exc
            raise UpdateLockError(f"cannot lock {git_dir / LOCK_NAME}: {exc}") from exc
        locked = True
    finally:
        if not locked:
            os.close(fd)
    return fd


@contextmanager
def update_run(project_root: Path, argv: Sequence[str], *, dry_run: bool, mode: str = "generate",
               framework: str = "") -> Iterator[RunHandle]:
    """Hold the per-checkout update lock around one run and append its run record.

    Args:
        project_root: The project the run writes (any path inside the checkout).
        argv: The run's command-line arguments; recorded redacted (:func:`redact_argv`).
        dry_run: The run writes nothing (``--dry-run`` or :func:`is_lock_exempt`): no lock and no record,
            only a notice when the lock is held.
        mode: The run's mode for the record (:func:`run_mode`).
        framework: The run's framework id for the record.

    Yields:
        A :class:`RunHandle`; the body calls ``set_exit(rc)`` with its exit code.

    Raises:
        UpdateLocked: Another process holds the lock (raised before the body runs; nothing is written).
        UpdateLockError: The git directory or lock file failed a custody check.
    """
    global _depth
    handle = RunHandle()
    if _depth > 0:  # re-entry in a process that already holds the lock: never refuse ourselves
        _depth += 1
        try:
            yield handle
        finally:
            _depth -= 1
        return
    try:
        git_dir = find_git_dir(Path(project_root).absolute())
    except OSError as exc:
        print(f"  ℹ  update lock: cannot read the git directory ({exc.strerror}); running unlocked and "
              "unrecorded.", file=sys.stderr)
        git_dir = None
    else:
        if git_dir is None:
            print(f"  ℹ  update lock: {project_root} is not in a git checkout; running unlocked and unrecorded.",
                  file=sys.stderr)
    fd: int | None = None  # find_git_dir has already lstat-refused a symlinked git dir
    if git_dir is not None and dry_run:
        if _lock_held(git_dir):
            print(f"  ⚠  update lock is held: another run is writing this checkout ({describe_holder(git_dir)}); "
                  "what this run reads may be mid-write.", file=sys.stderr)
    elif git_dir is not None:
        fd = _open_lock(git_dir)
    started = _now()
    argv_redacted = redact_argv(argv)
    invoker = detect_invoker()
    holder_written = False
    if fd is not None:
        holder = {"pid": os.getpid(), "started_at": started, "invoker": invoker, "argv_redacted": argv_redacted}
        try:
            write_new_atomic(git_dir, HOLDER_NAME, json.dumps(holder).encode("utf-8"))
            holder_written = True
        except OSError as exc:
            print(f"  ⚠  update lock: holder record not written: {exc}", file=sys.stderr)
    _depth += 1
    try:
        yield handle
    finally:
        _depth -= 1
        if fd is not None:
            exc_type = sys.exc_info()[0]
            exit_text = ("exception" if exc_type is not None and handle.exit_code is None
                         else "unset" if handle.exit_code is None else str(handle.exit_code))
            row = {"started_at": started, "finished_at": _now(), "pid": str(os.getpid()), "invoker": invoker,
                   "mode": mode, "framework": _clean(framework, 40), "argv": " ".join(argv_redacted),
                   "agentteams_commit": agentteams_commit(), "exit": exit_text}
            try:
                _append_record(git_dir, row)
            except OSError as exc:
                print(f"  ⚠  update lock: run record not appended: {exc}", file=sys.stderr)
            try:
                if holder_written:  # removed before releasing, so a successor's holder is never removed
                    os.unlink(git_dir / HOLDER_NAME)
            except OSError as exc:
                print(f"  ⚠  update lock: stale holder record left behind: {exc}", file=sys.stderr)
            finally:
                os.close(fd)
