"""Inventory the scripts under one or more directories, and mark which are copies.

The first two steps of a script-consolidation review (``references/script-consolidation.reference.md``):

- ``inventory ROOT…`` walks each root and writes one CSV row per script to stdout, one meaning per
  column: ``path, size, lines, sha256, ext, root``. Everything it leaves out is counted on stderr by
  reason, so a reader can see what the inventory does not cover.
- ``mark-copies`` reads that CSV on stdin and adds ``copy_kind``: ``snapshot`` for a file under a
  snapshot or worktree folder, ``tracked-duplicate`` for an untracked file whose bytes equal a file
  the repository tracks, ``authored`` otherwise, and ``missing`` when the file can no longer be read.

Both are read-only and print to stdout. A symbolic link below a root is never followed or read, and a
root that is itself a link is refused. git is asked only about the one repository named: where its
top is, and what it tracks. It is never run inside a nested checkout, and the blob ids of scanned
files are computed here in Python.

Classification, usage ranking and fleet-wide runs are not part of this module.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import os
import stat
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

from agentteams.backup import BACKUP_DIR_NAME
from agentteams.git_exec import run_git
from agentteams.ingest import PRUNE_DIRS as _INGEST_PRUNE_DIRS

#: Directory names never entered. CH-05: this extends the set ``ingest`` already keeps (which itself
#: names ``backup.BACKUP_DIR_NAME``) instead of restating it; ``cli.artifacts._SCRATCH_DIR_NAMES`` is
#: a third, narrower copy of the same idea. Only the additions below are this module's own.
PRUNE_DIR_NAMES: frozenset[str] = (frozenset(_INGEST_PRUNE_DIRS) | frozenset({
    ".ruff_cache", ".ipynb_checkpoints", "site-packages", ".eggs", "_site", ".cache",
})) - frozenset({"tmp"})  # ``tmp`` is where one-off scripts live; a census that skipped it would miss its subject
#: Extensions counted as scripts by default. A file with no extension counts when it starts with ``#!``.
SCRIPT_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".sh", ".bash", ".zsh", ".sql", ".js", ".mjs", ".ts", ".rb", ".pl", ".r", ".ps1", ".awk", ".sed",
})
#: Files larger than this are counted and left out: a script that size is data or a bundle.
MAX_BYTES: int = 1_000_000
#: Path components that mark a snapshot or a second working tree (``fnmatch`` patterns, case-sensitive).
SNAPSHOT_MARKERS: tuple[str, ...] = (BACKUP_DIR_NAME, "snapshots", "snapshots-*", ".worktrees", "worktrees", "archive")
#: The inventory columns, in order.
INVENTORY_COLUMNS: tuple[str, ...] = ("path", "size", "lines", "sha256", "ext", "root")
#: The values ``mark-copies`` writes to ``copy_kind``.
COPY_KINDS: tuple[str, ...] = ("authored", "snapshot", "tracked-duplicate", "missing")
#: Seconds allowed for the one git listing.
GIT_TIMEOUT: float = 60.0
#: Markers of a separate project below a root: its scripts belong to its own census.
_NESTED_MARKERS: tuple[str, ...] = (".git", "pyproject.toml")


class CensusError(ValueError):
    """A request that cannot be carried out: a root that is not a directory, or input that is not an inventory."""


def _read_regular(path: str, max_bytes: int) -> bytes | None:
    """The bytes of *path* when it is a regular file opened without following a link; otherwise ``None``.

    ``None`` also when the file is larger than *max_bytes* or cannot be read. At most ``max_bytes + 1``
    bytes are read, so a file that grows after it is opened cannot be read without bound.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as fh:
            data = fh.read(max_bytes + 1)
        return data if len(data) <= max_bytes else None
    except OSError:
        return None
    finally:
        os.close(fd)


def inventory(roots: Sequence[Path | str], *, extensions: Iterable[str] | None = None,
              max_bytes: int = MAX_BYTES) -> tuple[list[dict[str, str]], Counter[str]]:
    """List the scripts under *roots* and count everything left out.

    Args:
        roots: Directories to walk. Each is named in the ``root`` column exactly as given.
        extensions: Extensions that count as scripts, with the dot, compared in lower case
            (default :data:`SCRIPT_EXTENSIONS`). A file with no extension counts when it starts
            with ``#!``.
        max_bytes: Files larger than this are left out.

    Returns:
        ``(rows, pruned)``. ``rows`` are dicts keyed by :data:`INVENTORY_COLUMNS`, sorted by root
        (in the order given) and then path, with ``path`` relative to its root in POSIX form.
        ``pruned`` counts what was left out, by reason: ``pruned-dir``, ``nested-checkout``,
        ``symlink``, ``oversize``, ``other-extension``, ``unreadable``, ``unreadable-dir``.

    Raises:
        CensusError: A root is not a directory or is itself a symbolic link, or *max_bytes* is negative.
    """
    wanted = frozenset(e.lower() for e in (extensions if extensions is not None else SCRIPT_EXTENSIONS))
    if max_bytes < 0:
        raise CensusError("max_bytes must not be negative")
    rows: list[dict[str, str]] = []
    pruned: Counter[str] = Counter()
    for root in roots:
        top = str(root)
        if os.path.islink(top.rstrip("/\\") or top):
            raise CensusError(f"{top} is a symbolic link; name the directory it points to")
        if not os.path.isdir(top):
            raise CensusError(f"{top} is not a directory")
        found: list[dict[str, str]] = []

        def unreadable_dir(_exc: OSError) -> None:
            pruned["unreadable-dir"] += 1

        for here, dirs, files in os.walk(top, followlinks=False, onerror=unreadable_dir):
            keep: list[str] = []
            for name in sorted(dirs):
                full = os.path.join(here, name)
                if os.path.islink(full):
                    pruned["symlink"] += 1
                elif name in PRUNE_DIR_NAMES:
                    pruned["pruned-dir"] += 1
                elif any(os.path.lexists(os.path.join(full, marker)) for marker in _NESTED_MARKERS):
                    pruned["nested-checkout"] += 1
                else:
                    keep.append(name)
            dirs[:] = keep
            for name in sorted(files):
                full = os.path.join(here, name)
                if os.path.islink(full):
                    pruned["symlink"] += 1
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext and ext not in wanted:
                    pruned["other-extension"] += 1
                    continue
                try:
                    if os.lstat(full).st_size > max_bytes:
                        pruned["oversize"] += 1
                        continue
                except OSError:
                    pruned["unreadable"] += 1
                    continue
                data = _read_regular(full, max_bytes)
                if data is None:
                    pruned["unreadable"] += 1
                    continue
                if not ext and not data.startswith(b"#!"):
                    pruned["other-extension"] += 1
                    continue
                rel = PurePosixPath(*Path(os.path.relpath(full, top)).parts).as_posix()
                found.append({
                    "path": rel, "size": str(len(data)),
                    "lines": str(data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)),
                    "sha256": hashlib.sha256(data).hexdigest(), "ext": ext, "root": top,
                })
        rows.extend(sorted(found, key=lambda r: r["path"]))
    return rows, pruned


def tracked_blobs(repo: Path | str) -> dict[str, set[str]]:
    """The blob ids of the content *repo* tracks, each with the repository paths that hold it.

    One ``git ls-files -s -z`` at the top of *repo*, with inherited ``GIT_*`` variables removed so a
    ``GIT_DIR`` in the environment cannot redirect it. Submodule entries are left out.

    Args:
        repo: A directory inside the repository.

    Returns:
        ``{blob id: {path relative to the repository root, POSIX form}}``.

    Raises:
        CensusError: *repo* is not a git repository, or git cannot be run.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    top = _repo_root(repo)  # ls-files lists only the folder it runs in; the listing must cover the whole repository
    try:
        proc = run_git(Path(top), "ls-files", "-s", "-z", "--full-name", hardened=True, env=env, timeout=GIT_TIMEOUT,
                       devnull_stdin=True)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise CensusError(f"git could not list {repo}: {exc}") from exc
    if proc.returncode != 0:
        raise CensusError(f"{repo} is not a git repository")
    blobs: dict[str, set[str]] = {}
    for entry in (proc.stdout or "").split("\0"):
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[0] != "160000" and path:
            blobs.setdefault(fields[1], set()).add(path)
    return blobs


def _repo_root(repo: Path | str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        proc = run_git(Path(repo), "rev-parse", "--show-toplevel", hardened=True, env=env, timeout=GIT_TIMEOUT,
                       devnull_stdin=True)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise CensusError(f"git could not run in {repo}: {exc}") from exc
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        raise CensusError(f"{repo} is not a git repository")
    return os.path.realpath(proc.stdout.strip())


def _blob_id(data: bytes, hex_length: int) -> str:
    """The git blob id of *data*: SHA-1, or SHA-256 when the repository's ids are 64 characters."""
    digest = hashlib.sha256() if hex_length == 64 else hashlib.sha1(usedforsecurity=False)
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


def mark_copies(rows: Iterable[dict[str, str]], *, repo: Path | str = ".",
                snapshot_markers: Sequence[str] = SNAPSHOT_MARKERS, max_bytes: int = MAX_BYTES) -> list[dict[str, str]]:
    """Add ``copy_kind`` to inventory rows.

    Checked in this order: ``snapshot`` when a folder in ``path``, or in a relative ``root``,
    matches a snapshot marker (the folders above an absolute root are not looked at: a checkout
    that happens to live under ``archive/`` is not a snapshot); ``missing`` when the file can no longer be read as a regular file below its
    root (it is gone, larger than *max_bytes*, or now reached through a symbolic link);
    ``tracked-duplicate`` when the file is not itself tracked by *repo* and its bytes equal a file
    *repo* tracks; otherwise ``authored``. Two tracked files with the same bytes are both
    ``authored``: the repository already records that.

    Args:
        rows: Inventory rows (at least ``path`` and ``root``). A relative ``root`` is read from the
            current directory, as ``inventory`` wrote it.
        repo: The repository whose tracked content is the reference.
        snapshot_markers: ``fnmatch`` patterns matched against each path component.
        max_bytes: Files larger than this are ``missing``. Use the value the inventory was made with.

    The rows are text from stdin, so they are not trusted: ``path`` must be relative with no ``..``
    component. What a row can reveal is one verdict about a file the caller can already read.

    Returns:
        The rows, in order, each with ``copy_kind`` set to one of :data:`COPY_KINDS`.

    Raises:
        CensusError: A row lacks ``path`` or ``root``, a ``path`` is absolute or has a ``..``
            component, *max_bytes* is negative, or *repo* is not a git repository.
    """
    if max_bytes < 0:
        raise CensusError("max_bytes must not be negative")
    blobs = tracked_blobs(repo)
    top = _repo_root(repo)
    hex_length = len(next(iter(blobs))) if blobs else 40
    out: list[dict[str, str]] = []
    for row in rows:
        if "path" not in row or "root" not in row:
            raise CensusError("a row has no 'path' or 'root' column: the input is not an inventory")
        rel = PurePosixPath(row["path"])
        if not rel.parts or rel.is_absolute() or ".." in rel.parts or os.path.isabs(row["path"]):
            raise CensusError(f"path {row['path']!r} is not a relative path below its root")
        full = os.path.join(row["root"], *rel.parts)
        above = () if os.path.isabs(row["root"]) else Path(os.path.normpath(row["root"])).parts
        parts = (*above, *rel.parts[:-1])
        if any(fnmatch.fnmatchcase(part, marker) for part in parts for marker in snapshot_markers):
            kind = "snapshot"
        else:
            inside = os.path.realpath(full) == os.path.join(os.path.realpath(row["root"]), *rel.parts)
            data = _read_regular(full, max_bytes) if inside else None  # not inside: a link stands in the way
            if data is None:
                kind = "missing"
            else:
                holders = blobs.get(_blob_id(data, hex_length), set())
                real = os.path.realpath(full)
                own = os.path.relpath(real, top).replace(os.sep, "/") if real.startswith(top + os.sep) else None
                kind = "tracked-duplicate" if holders and own not in holders else "authored"
        out.append({**row, "copy_kind": kind})
    return out


def _write_csv(rows: Iterable[dict[str, str]], columns: Sequence[str]) -> None:
    writer = csv.DictWriter(sys.stdout, fieldnames=list(columns), lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Args:
        argv: Arguments, or ``None`` for ``sys.argv[1:]``.

    Returns:
        0 when the table was written; 2 for a request that cannot be carried out.

    Raises:
        Nothing. Refusals are printed to stderr and returned as exit status 2.
    """
    parser = argparse.ArgumentParser(
        prog="python -m agentteams.script_census",
        description="Inventory the scripts under directories, and mark which are copies. Read-only; CSV on stdout.")
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="one CSV row per script under each ROOT; what is left out is counted on stderr")
    inv.add_argument("roots", nargs="+", metavar="ROOT", help="directory to walk")
    inv.add_argument("--ext", metavar="LIST", help="comma-separated extensions that count as scripts (default: a built-in set)")
    inv.add_argument("--max-bytes", type=int, default=MAX_BYTES, metavar="N", help=f"leave out larger files (default {MAX_BYTES})")
    mark = sub.add_parser("mark-copies", help="read an inventory on stdin and add copy_kind")
    mark.add_argument("--repo", default=".", metavar="DIR", help="repository whose tracked content is the reference (default: .)")
    mark.add_argument("--max-bytes", type=int, default=MAX_BYTES, metavar="N",
                      help=f"the limit the inventory was made with (default {MAX_BYTES}); a larger file is 'missing'")
    mark.add_argument("--snapshot-marker", action="append", default=[], metavar="NAME",
                      help="another folder-name pattern that marks a snapshot (repeatable)")
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            extensions = None
            if args.ext is not None:
                extensions = [e if e.startswith(".") else f".{e}" for e in (p.strip() for p in args.ext.split(",")) if e]
                if not extensions:
                    raise CensusError("--ext names no extension")
            rows, pruned = inventory(args.roots, extensions=extensions, max_bytes=args.max_bytes)
            _write_csv(rows, INVENTORY_COLUMNS)
            print(f"# {len(rows)} script(s) under {len(args.roots)} root(s)", file=sys.stderr)
            for reason in sorted(pruned):
                print(f"# left out, {reason}: {pruned[reason]}", file=sys.stderr)
            return 0
        reader = csv.DictReader(sys.stdin)
        header = list(reader.fieldnames or [])
        if "path" not in header or "root" not in header:
            raise CensusError("stdin is not an inventory: it has no 'path' and 'root' columns")
        marked = mark_copies(reader, repo=args.repo, snapshot_markers=(*SNAPSHOT_MARKERS, *args.snapshot_marker),
                             max_bytes=args.max_bytes)
        _write_csv(marked, [*(c for c in header if c != "copy_kind"), "copy_kind"])
        counts = Counter(r["copy_kind"] for r in marked)
        for kind in COPY_KINDS:
            print(f"# {kind}: {counts[kind]}", file=sys.stderr)
        return 0
    except CensusError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


__all__ = ["COPY_KINDS", "CensusError", "GIT_TIMEOUT", "INVENTORY_COLUMNS", "MAX_BYTES", "PRUNE_DIR_NAMES",
           "SCRIPT_EXTENSIONS", "SNAPSHOT_MARKERS", "inventory", "main", "mark_copies", "tracked_blobs"]

if __name__ == "__main__":
    sys.exit(main())
