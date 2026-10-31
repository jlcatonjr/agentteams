"""_wasm_archive.py — hashing, path checks and safe archive extraction for ``wasm_runtime``.

Private helper split out of ``agentteams/wasm_runtime.py`` (CH-07 module ceiling). Stdlib only; imports nothing
from agentteams, so it can join the standalone WASM bundle if P10 needs tree verification there. Extraction is
hand-rolled, member by member (no ``extractall``): absolute paths, ``..``/``.`` segments, backslashes, drive
letters, NUL, links, devices, FIFOs, sparse members, encrypted or exotic zip entries, duplicates, and members
outside the pinned prefix are refused, and sizes are counted while streaming, never taken from headers.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Extraction bounds (plan P03 §5.4).
MAX_MEMBERS = 20_000
MAX_TOTAL_BYTES = 512 << 20
MAX_FILE_BYTES = 256 << 20
MAX_ZIP_RATIO = 200
_CHUNK = 1 << 20
_DRIVE = re.compile(r"^[A-Za-z]:")


class WasmRuntimeError(Exception):
    """Provisioning or verification refused; the message names the operator fix."""


class WasmNetworkError(WasmRuntimeError):
    """A download or release-metadata request failed at the network layer."""



# ---------------------------------------------------------------------------
# Hashing and path checks (verify on use)
# ---------------------------------------------------------------------------

def _hash_file_nofollow(path: Path) -> tuple[str, int]:
    """sha256 + size of a regular file opened without following a final symlink."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise WasmRuntimeError(f"cannot open {path} without following links: {exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise WasmRuntimeError(f"{path} is not a regular file")
        h = hashlib.sha256()
        n = 0
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            n += len(chunk)
            h.update(chunk)
        return h.hexdigest(), n
    finally:
        os.close(fd)


def _lstat_walk(base: Path, rel_parts: Sequence[str], *, leaf_dir: bool = False) -> Path:
    """Walk ``rel_parts`` under ``base`` refusing any symlink; return the final path."""
    cur = base
    for i, part in enumerate(rel_parts):
        cur = cur / part
        try:
            st = os.lstat(cur)
        except FileNotFoundError as exc:
            raise WasmRuntimeError(f"{cur} is absent") from exc
        if stat.S_ISLNK(st.st_mode):
            raise WasmRuntimeError(f"{cur} is a symlink; refusing")
        last = i == len(rel_parts) - 1
        if not last or leaf_dir:
            if not stat.S_ISDIR(st.st_mode):
                raise WasmRuntimeError(f"{cur} is not a directory")
        elif not stat.S_ISREG(st.st_mode):
            raise WasmRuntimeError(f"{cur} is not a regular file")
    return cur


def tree_digest(root: Path) -> tuple[str, int]:
    """Digest every regular file under ``root``: sha256 over sorted ``"<rel>\\0<sha256>\\n"`` lines.

    Args:
        root: A directory (not followed if it is a symlink).

    Returns:
        ``(hexdigest, file_count)``.

    Raises:
        WasmRuntimeError: A symlink, device, FIFO or socket is present, or ``root`` is not a directory.
    """
    try:
        st = os.lstat(root)
    except OSError as exc:
        raise WasmRuntimeError(f"{root} is absent") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise WasmRuntimeError(f"{root} is not a directory")
    lines: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in list(dirnames) + filenames:
            p = Path(dirpath) / name
            mode = os.lstat(p).st_mode
            rel = p.relative_to(root).as_posix()
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise WasmRuntimeError(f"{p} is not a regular file or directory; refusing")
            lines.append(f"{rel}\0{_hash_file_nofollow(p)[0]}\n")
    lines.sort()
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest(), len(lines)



# ---------------------------------------------------------------------------
# Archive safety
# ---------------------------------------------------------------------------

def _member_problem(name: str) -> str | None:
    """Why ``name`` is an unsafe archive member path, or ``None``."""
    if not name:
        return "empty name"
    if "\0" in name:
        return "NUL in name"
    if "\\" in name:
        return "backslash in name"
    if name.startswith("/"):
        return "absolute path"
    if _DRIVE.match(name):
        return "drive letter"
    for seg in name.rstrip("/").split("/"):
        if seg in ("", ".", ".."):
            return f"bad path segment {seg!r}"
    return None


def _kept(rel: str, keep: Sequence[str]) -> bool:
    for k in keep:
        if k.endswith("/"):
            if rel == k.rstrip("/") or rel.startswith(k):
                return True
        elif rel == k:
            return True
    return False


def _mkdirs(base: Path, rel_parts: Sequence[str]) -> Path:
    cur = base
    for part in rel_parts:
        cur = cur / part
        if not os.path.lexists(cur):
            os.mkdir(cur, 0o700)
        st = os.lstat(cur)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise WasmRuntimeError(f"extraction path {cur} is not a real directory")
    return cur


class _Extractor:
    """Bounded, member-by-member writer shared by the tar and zip paths."""

    def __init__(self, dest: Path, strip: str, keep: Sequence[str]) -> None:
        self.dest = dest
        self.strip = strip.strip("/")
        self.keep = list(keep)
        self.members = 0
        self.total = 0
        self.seen: set[str] = set()
        self.skipped: list[str] = []
        self.written: list[str] = []

    def target(self, raw: str, is_dir: bool) -> str | None:
        """Validate a member name; return the stripped relative path to write, or None to skip."""
        self.members += 1
        if self.members > MAX_MEMBERS:
            raise WasmRuntimeError(f"archive has more than {MAX_MEMBERS} members")
        why = _member_problem(raw)
        if why:
            raise WasmRuntimeError(f"unsafe archive member {raw!r}: {why}")
        name = raw.rstrip("/")
        if self.strip:
            if name == self.strip:
                return None
            if not name.startswith(self.strip + "/"):
                raise WasmRuntimeError(f"archive member {raw!r} is outside the pinned prefix {self.strip!r}/")
            name = name[len(self.strip) + 1:]
        if name in self.seen:
            raise WasmRuntimeError(f"duplicate archive member {name!r}")
        self.seen.add(name)
        if not _kept(name, self.keep):
            if not is_dir:
                self.skipped.append(name)
            return None
        return name

    def write(self, rel: str, src: Any) -> None:
        """Stream ``src`` into ``dest/rel`` with streaming size bounds."""
        parts = rel.split("/")
        parent = _mkdirs(self.dest, parts[:-1])
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0))
        fd = os.open(parent / parts[-1], flags, 0o600)
        n = 0
        try:
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                n += len(chunk)
                self.total += len(chunk)
                if n > MAX_FILE_BYTES:
                    raise WasmRuntimeError(f"archive member {rel!r} exceeds {MAX_FILE_BYTES} bytes")
                if self.total > MAX_TOTAL_BYTES:
                    raise WasmRuntimeError(f"archive expands past {MAX_TOTAL_BYTES} bytes")
                os.write(fd, chunk)
        finally:
            os.close(fd)
        self.written.append(rel)


def _extract(archive: Path, kind: str, dest: Path, *, strip: str, keep: Sequence[str],
             entry: str) -> list[str]:
    """Safely extract ``archive`` into the existing empty directory ``dest``.

    Args:
        archive: The verified download.
        kind: ``"tar.xz"``, ``"zip"`` or ``"none"`` (the download *is* ``entry``).
        dest: Target directory.
        strip: Required leading directory, removed from every member (``""`` for none).
        keep: Paths to extract; a trailing ``/`` keeps a subtree. Everything else is skipped and reported.
        entry: The entry file name (used for ``kind == "none"``).

    Returns:
        Notices (skipped members).

    Raises:
        WasmRuntimeError: Any unsafe member, bound exceeded, or unsupported archive.
    """
    ex = _Extractor(dest, strip, keep)
    if kind == "none":
        if _member_problem(entry):
            raise WasmRuntimeError(f"unsafe entry name {entry!r}")
        with open(archive, "rb") as src:
            ex.write(entry, src)
    elif kind == "tar.xz":
        _extract_tar(archive, ex)
    elif kind == "zip":
        _extract_zip(archive, ex)
    else:
        raise WasmRuntimeError(f"unsupported archive kind {kind!r}")
    notices = []
    if ex.skipped:
        shown = ", ".join(sorted(ex.skipped)[:20])
        more = f" (+{len(ex.skipped) - 20} more)" if len(ex.skipped) > 20 else ""
        notices.append(f"skipped {len(ex.skipped)} archive member(s) not in the pinned keep list: {shown}{more}")
    return notices


def _extract_tar(archive: Path, ex: _Extractor) -> None:
    try:
        import lzma  # noqa: F401 — presence check; tarfile uses it for r:xz
    except ImportError as exc:
        raise WasmRuntimeError("this Python lacks lzma; .tar.xz cannot be verified-extracted") from exc
    import tarfile

    try:
        with tarfile.open(archive, mode="r:xz") as tf:
            for m in tf:
                if m.issym() or m.islnk():
                    raise WasmRuntimeError(f"archive member {m.name!r} is a link; refusing")
                if m.isdev() or m.isfifo() or m.ischr() or m.isblk():
                    raise WasmRuntimeError(f"archive member {m.name!r} is a device/FIFO; refusing")
                if m.issparse():
                    raise WasmRuntimeError(f"archive member {m.name!r} is sparse; refusing")
                if not (m.isreg() or m.isdir()):
                    raise WasmRuntimeError(f"archive member {m.name!r} has unsupported type {m.type!r}")
                rel = ex.target(m.name, m.isdir())
                if rel is None or m.isdir():
                    continue
                src = tf.extractfile(m)
                if src is None:
                    raise WasmRuntimeError(f"archive member {m.name!r} has no data")
                with src:
                    ex.write(rel, src)
    except (tarfile.TarError, EOFError, OSError) as exc:
        if isinstance(exc, WasmRuntimeError):
            raise
        raise WasmRuntimeError(f"corrupt tar.xz archive: {exc}") from exc


def _extract_zip(archive: Path, ex: _Extractor) -> None:
    import zipfile

    try:
        with zipfile.ZipFile(archive) as zf:
            for zi in zf.infolist():
                raw = zi.orig_filename
                mode = (zi.external_attr >> 16) & 0o170000
                is_dir = raw.endswith("/")
                if mode == stat.S_IFLNK:
                    raise WasmRuntimeError(f"zip member {raw!r} is a symlink; refusing")
                if mode and mode not in (stat.S_IFREG, stat.S_IFDIR):
                    raise WasmRuntimeError(f"zip member {raw!r} is not a regular file or directory")
                if zi.flag_bits & 0x1:
                    raise WasmRuntimeError(f"zip member {raw!r} is encrypted; refusing")
                if zi.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise WasmRuntimeError(f"zip member {raw!r} uses unsupported compression {zi.compress_type}")
                if not is_dir and zi.file_size / max(zi.compress_size, 1) > MAX_ZIP_RATIO:
                    raise WasmRuntimeError(f"zip member {raw!r} expands more than {MAX_ZIP_RATIO}x; refusing")
                rel = ex.target(raw, is_dir)
                if rel is None or is_dir:
                    continue
                with zf.open(zi) as src:
                    ex.write(rel, src)
    except zipfile.BadZipFile as exc:
        raise WasmRuntimeError(f"corrupt zip archive: {exc}") from exc


def _verify_version_dir(verdir: Path, pe: Mapping[str, Any]) -> None:
    """Strictly verify an extracted version dir: every file pinned, nothing extra, no links.

    Raises:
        WasmRuntimeError: Any mismatch, missing file, extra file, or non-regular entry.
    """
    files = pe.get("files") or {}
    tree = pe.get("tree")
    tree_root = str(tree["root"]) if tree else None
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(verdir, followlinks=False):
        for name in list(dirnames) + filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(verdir).as_posix()
            mode = os.lstat(p).st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise WasmRuntimeError(f"{rel} is not a regular file")
            if tree_root and (rel + "/").startswith(tree_root + "/"):
                continue
            if rel not in files:
                raise WasmRuntimeError(f"{rel} is not pinned")
            got, _ = _hash_file_nofollow(p)
            if got != files[rel]["sha256"]:
                raise WasmRuntimeError(f"{rel} sha256 mismatch (have {got}, pinned {files[rel]['sha256']})")
            seen.add(rel)
    missing = sorted(set(files) - seen)
    if missing:
        raise WasmRuntimeError(f"pinned file(s) missing: {', '.join(missing)}")
    if tree:
        root = _lstat_walk(verdir, tree_root.split("/"), leaf_dir=True)
        digest, count = tree_digest(root)
        if digest != tree["sha256"] or count != tree["count"]:
            raise WasmRuntimeError(f"tree {tree_root}/ digest/count mismatch ({count} files)")


def _chmod_readonly(verdir: Path, pe: Mapping[str, Any]) -> None:
    files = pe.get("files") or {}
    for dirpath, dirnames, filenames in os.walk(verdir, topdown=False, followlinks=False):
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(verdir).as_posix()
            os.chmod(p, 0o555 if (files.get(rel) or {}).get("exec") else 0o444)
        os.chmod(dirpath, 0o555)


def _make_writable(path: Path) -> None:
    if not os.path.lexists(path) or os.path.islink(path):
        return
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        os.chmod(dirpath, 0o700)
    os.chmod(path, 0o700)
