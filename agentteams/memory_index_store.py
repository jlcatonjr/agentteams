"""On-disk layout of the committed memory index: compact lines, size-capped native shards.

The memory index (:mod:`agentteams.memory_index`) is a **committed** artifact, and it outgrew
GitHub's limits: a downstream index reached 56.1 MiB (GitHub warns above 50 MB and rejects files
above 100 MB). Measured on that index, 74% of the bytes were the ``postings`` map and about half
of the whole file was ``indent=2`` whitespace, one line per ``{"doc_id", "tf"}`` field. Stored
paragraph text was 12%. So the payload is not the problem; the layout is.

This module owns the bytes on disk, never the data:

- **Compact line layout** (:func:`serialize_index`). The same JSON object with the same schema,
  but one line per top-level key, per document, per stored paragraph and per postings term.
  About half the size, still plain JSON (any ``json.loads`` reader is unaffected), still
  line-diffable, and paragraphs stay one per line so the line-based content scanner sees the
  same units it saw before.
- **Native shards** (:func:`encode_memory_index`). When the compact text exceeds a cap
  (default 20 MiB, :data:`MAX_SHARD_BYTES_ENV` overrides it), the documents and postings are
  packed in order into ``memory-index.part-NNNN.json`` files, each a valid JSON object under the
  cap, and ``memory-index.json`` becomes a small manifest that records every part's size and
  sha256. The query path reads the parts directly (:func:`read_memory_index_payload`), so there
  is nothing to reassemble after a pull and no git hook is involved: hooks do not run in CI, on
  GitHub, or in a fresh clone, so a design that needed one would break exactly there.

Reading **fails safe**. A missing, truncated, merge-conflicted or tampered part fails its size or
sha256 check and raises :class:`~agentteams.errors.MemoryIndexError` naming the part and the
rebuild command. It never returns a partial index, which would silently answer queries from a
subset of the corpus. Hashes are taken over the bytes with every ``\\r`` removed: the layout
only ever puts a newline between JSON tokens (string values escape theirs), so a carriage
return can only be a line ending that Windows or ``core.autocrlf`` added, and ignoring it keeps
a Windows checkout verifying.

Indexes written before this layout (``indent=2``, one file) still read unchanged; the next
rebuild rewrites them in the new layout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentteams.atomicio import _atomic_write_text
from agentteams.errors import MemoryIndexError

#: ``artifact_type`` of the manifest that replaces ``memory-index.json`` when the index is sharded.
#: Deliberately not ``memory-index``: the index schema pins that value, so a reader that predates
#: sharding rejects the manifest instead of mistaking it for an empty index.
SHARDS_ARTIFACT_TYPE = "memory-index-shards"
#: ``artifact_type`` of each shard file.
PART_ARTIFACT_TYPE = "memory-index-part"
SHARD_FORMAT_VERSION = "1"
#: Shard size cap. GitHub warns above 50 MB and rejects above 100 MB; 20 MiB leaves headroom and
#: keeps a sharded index to a handful of files.
DEFAULT_MAX_SHARD_BYTES = 20 * 1024 * 1024
#: Smallest cap the environment override may set (tests use small caps to force sharding).
MIN_MAX_SHARD_BYTES = 1024
MAX_SHARD_BYTES_ENV = "AGENTTEAMS_MEMORY_INDEX_SHARD_MAX_BYTES"
#: Four-digit part numbers, so at most this many parts (about 195 GiB at the default cap).
MAX_PARTS = 9999
INDEX_FILE_NAME = "memory-index.json"
_PART_NAME_RE = re.compile(r"memory-index\.part-([0-9]{4})\.json")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_BULK_KEYS = ("documents", "postings")
REBUILD_HINT = "run `agentteams --refresh-index` to rebuild it"
_MANIFEST_NOTE = (
    "This memory index is stored in size-capped parts listed below; each part is a JSON object "
    "holding a slice of the documents and postings, in order. Query it with "
    "`agentteams --query-index`, which reads the parts directly. Do not edit by hand; "
    "on a merge conflict, run `agentteams --refresh-index`."
)


@dataclass(frozen=True)
class EncodedIndex:
    """A memory index rendered to files, ready to validate and write.

    Attributes:
        files: ``(file name, text)`` pairs, in write order. Shards come first and
            ``memory-index.json`` (the whole index, or the shard manifest) comes last, so a
            reader never sees a manifest whose parts are not on disk yet.
        chunks: The bytes a reader will hash for the validation cache key: the index bytes,
            or the manifest bytes followed by each part's bytes.
    """

    files: tuple[tuple[str, str], ...]
    chunks: tuple[bytes, ...]

    @property
    def sharded(self) -> bool:
        """True when the index is stored as a manifest plus parts."""
        return len(self.files) > 1


def part_file_name(number: int) -> str:
    """Return the file name of shard *number* (1-based).

    Args:
        number: Shard number, from 1 to :data:`MAX_PARTS`.

    Returns:
        ``memory-index.part-NNNN.json``.

    Raises:
        ValueError: If *number* is out of range.
    """
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= MAX_PARTS:
        raise ValueError(f"shard number {number} outside 1..{MAX_PARTS}")
    return f"memory-index.part-{number:04d}.json"


def is_part_file_name(name: str) -> bool:
    """True when *name* is a memory-index shard file name (``memory-index.part-NNNN.json``).

    Args:
        name: A bare file name.

    Returns:
        Whether it matches the shard naming pattern exactly.
    """
    return _PART_NAME_RE.fullmatch(name) is not None


def max_shard_bytes_from_env() -> int:
    """Return the shard size cap: :data:`MAX_SHARD_BYTES_ENV` when set and valid, else the default.

    Returns:
        The cap in bytes.

    Raises:
        MemoryIndexError: If the variable is set but is not an integer of at least
            :data:`MIN_MAX_SHARD_BYTES` (a misconfigured cap fails the write, never silently
            changes it).
    """
    raw = os.environ.get(MAX_SHARD_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_SHARD_BYTES
    message = f"{MAX_SHARD_BYTES_ENV}={raw!r} is not an integer >= {MIN_MAX_SHARD_BYTES}"
    try:
        value = int(raw)
    except ValueError as exc:
        raise MemoryIndexError(message) from exc
    if value < MIN_MAX_SHARD_BYTES:
        raise MemoryIndexError(message)
    return value


def _dump(value: Any) -> str:
    """Compact, ASCII-only JSON (escaped non-ASCII keeps byte length equal to string length)."""
    return json.dumps(value, separators=(",", ":"))


def _render_document(doc: Any) -> str:
    """Render one document on one line, except its paragraphs, which get one line each."""
    if not isinstance(doc, dict):
        return _dump(doc)
    pieces: list[str] = []
    for key, value in doc.items():
        if key == "paragraphs" and isinstance(value, list) and value:
            pieces.append(_dump(key) + ":[\n" + ",\n".join(_dump(p) for p in value) + "\n]")
        else:
            pieces.append(_dump(key) + ":" + _dump(value))
    return "{" + ",".join(pieces) + "}"


def _render_list(items: list[str]) -> str:
    return "[\n" + ",\n".join(items) + "\n]" if items else "[]"


def _render_object(items: list[str]) -> str:
    return "{\n" + ",\n".join(items) + "\n}" if items else "{}"


def _render_term(term: str, entries: Any) -> str:
    return _dump(term) + ":" + _dump(entries)


def _render(obj: dict[str, Any], rendered_bulk: dict[str, str]) -> str:
    """Render a top-level object one key per line, splicing in pre-rendered bulk values."""
    pieces = [
        _dump(key) + ":" + (rendered_bulk[key] if key in rendered_bulk else _dump(value))
        for key, value in obj.items()
    ]
    return "{\n" + ",\n".join(pieces) + "\n}\n"


def serialize_index(index: dict[str, Any]) -> str:
    """Render *index* in the compact line layout (same object, same schema, about half the bytes).

    Deterministic in its input: equal indexes always render to identical text. The index itself
    is not stable across rebuilds (``built_at``; ``source_mtime`` from the local checkout;
    ``doc_id`` and ``vector_norm_sq`` shift when a source is added), so a rebuild still rewrites
    most lines whatever the layout. That churn predates this module.

    Args:
        index: A memory index payload.

    Returns:
        JSON text ending in a newline.
    """
    bulk: dict[str, str] = {}
    documents = index.get("documents")
    if isinstance(documents, list):
        bulk["documents"] = _render_list([_render_document(d) for d in documents])
    postings = index.get("postings")
    if isinstance(postings, dict):
        bulk["postings"] = _render_object([_render_term(t, e) for t, e in postings.items()])
    return _render(index, bulk)


def _normalized(raw: bytes) -> bytes:
    """*raw* with every carriage return removed (see the module docstring for why that is safe)."""
    return raw.replace(b"\r", b"")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _render_part(number: int, docs: list[str], terms: list[str]) -> str:
    head = {"artifact_type": PART_ARTIFACT_TYPE, "part": number, "documents": None, "postings": None}
    return _render(head, {"documents": _render_list(docs), "postings": _render_object(terms)})


def _pack(items: list[tuple[str, str]], max_bytes: int) -> list[tuple[list[str], list[str]]]:
    """Greedily pack ``(kind, rendered)`` items, in order, into parts no larger than *max_bytes*.

    A part's size is bounded by the fixed wrapper plus each item and its separator. An item that
    alone exceeds the cap gets a part to itself (an item is one document or one term, so this
    needs a cap far below any real setting).
    """
    overhead = len(_render_part(MAX_PARTS, [], [])) + 8
    parts: list[tuple[list[str], list[str]]] = []
    docs: list[str] = []
    terms: list[str] = []
    size = overhead
    for kind, text in items:
        cost = len(text) + 2
        if (docs or terms) and size + cost > max_bytes:
            parts.append((docs, terms))
            docs, terms, size = [], [], overhead
        (docs if kind == "d" else terms).append(text)
        size += cost
    if docs or terms or not parts:
        parts.append((docs, terms))
    return parts


def encode_memory_index(index: dict[str, Any], *, max_shard_bytes: int | None = None) -> EncodedIndex:
    """Render *index* to the files that store it: one file, or a manifest plus shards.

    Args:
        index: A memory index payload (as built by :func:`agentteams.memory_index.build_memory_index`).
        max_shard_bytes: Cap for any one file. ``None`` reads :func:`max_shard_bytes_from_env`.

    Returns:
        The encoded files. When the compact single-file text fits the cap it is the only file,
        so small indexes keep the one-file layout every existing reader expects.

    Raises:
        TypeError: If *index* is not a dict.
        ValueError: If *max_shard_bytes* is not an integer of at least :data:`MIN_MAX_SHARD_BYTES`.
        MemoryIndexError: If the index would need more than :data:`MAX_PARTS` shards, or the
            environment cap is invalid.
    """
    if not isinstance(index, dict):
        raise TypeError(f"index must be a dict, not {type(index).__name__}")
    cap = max_shard_bytes if max_shard_bytes is not None else max_shard_bytes_from_env()
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < MIN_MAX_SHARD_BYTES:
        raise ValueError(f"max_shard_bytes must be an integer >= {MIN_MAX_SHARD_BYTES}, got {cap!r}")
    single = serialize_index(index)
    if len(single) <= cap:
        return EncodedIndex(files=((INDEX_FILE_NAME, single),), chunks=(single.encode("ascii"),))

    documents = index.get("documents") or []
    postings = index.get("postings") or {}
    items = [("d", _render_document(d)) for d in documents]
    items += [("t", _render_term(t, e)) for t, e in postings.items()]
    packed = _pack(items, cap)
    if len(packed) > MAX_PARTS:
        raise MemoryIndexError(
            f"memory index needs {len(packed)} shards at {cap} bytes each (max {MAX_PARTS}); "
            f"raise {MAX_SHARD_BYTES_ENV}"
        )
    files: list[tuple[str, str]] = []
    part_meta: list[dict[str, Any]] = []
    for number, (docs, terms) in enumerate(packed, start=1):
        name = part_file_name(number)
        text = _render_part(number, docs, terms)
        raw = text.encode("ascii")
        files.append((name, text))
        part_meta.append({
            "file": name, "bytes": len(raw), "sha256": _sha256(raw),
            "documents": len(docs), "terms": len(terms),
        })
    manifest = {
        "artifact_type": SHARDS_ARTIFACT_TYPE,
        "shard_format_version": SHARD_FORMAT_VERSION,
        "note": _MANIFEST_NOTE,
        "max_shard_bytes": cap,
        "index": {k: v for k, v in index.items() if k not in _BULK_KEYS},
        "parts": part_meta,
    }
    manifest_text = json.dumps(manifest, indent=1) + "\n"
    files.append((INDEX_FILE_NAME, manifest_text))
    chunks = [manifest_text.encode("ascii")] + [t.encode("ascii") for _, t in files[:-1]]
    return EncodedIndex(files=tuple(files), chunks=tuple(chunks))


def write_encoded_index(index_path: Path, encoded: EncodedIndex) -> list[Path]:
    """Write *encoded* beside *index_path* and remove shard files it no longer lists.

    Each file is written atomically; shards before the manifest. Stale shards are removed only
    after the new ``memory-index.json`` is in place, and only names that match the shard pattern
    exactly, in this one directory.

    Args:
        index_path: Path of ``memory-index.json``.
        encoded: The output of :func:`encode_memory_index`.

    Returns:
        The paths written, ``memory-index.json`` last.

    Raises:
        OSError: If a file cannot be written.
    """
    directory = index_path.parent
    written: list[Path] = []
    for name, text in encoded.files:
        target = index_path if name == INDEX_FILE_NAME else directory / name
        _atomic_write_text(target, text)
        written.append(target)
    keep = {name for name, _ in encoded.files}
    for stale in sorted(directory.glob("memory-index.part-*.json")):
        if not is_part_file_name(stale.name) or stale.name in keep:
            continue
        if stale.is_symlink() or stale.is_file():  # never a directory; a symlink, not its target
            stale.unlink(missing_ok=True)
    return written


def is_legacy_layout(index_path: Path) -> bool:
    """True when *index_path* is a single-file ``indent=2`` index, the pre-compact layout.

    The opt-in incremental sed updater (:mod:`agentteams.memory_index_incremental`) patches that
    layout line by line and has no anchors in the compact or sharded one.

    Args:
        index_path: Path of ``memory-index.json``.

    Returns:
        Whether the file exists and starts the way ``json.dumps(index, indent=2)`` does.
    """
    try:
        with index_path.open("rb") as handle:
            return handle.read(5) == b'{\n  "'
    except OSError:
        return False


def orphan_part_files(index_path: Path) -> list[Path]:
    """Shard files beside a *single-file* index, which no reader uses.

    They appear when an agentteams release older than sharding rewrites a sharded index as one
    file and leaves the parts behind. The next write by this release removes them.

    Args:
        index_path: Path of ``memory-index.json``.

    Returns:
        The orphaned part paths; empty when the index is sharded, absent, or has none.
    """
    try:
        with index_path.open("rb") as handle:
            head = handle.read(256)
    except OSError:
        return []
    if f'"{SHARDS_ARTIFACT_TYPE}"'.encode("ascii") in head:
        return []
    return sorted(p for p in index_path.parent.glob("memory-index.part-*.json")
                  if is_part_file_name(p.name))


def _fail(message: str) -> MemoryIndexError:
    return MemoryIndexError(f"{message}; {REBUILD_HINT}")


def _checked_parts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the manifest's shape and return its part records."""
    if manifest.get("shard_format_version") != SHARD_FORMAT_VERSION:
        raise _fail(f"memory index manifest has unsupported shard_format_version "
                    f"{manifest.get('shard_format_version')!r}")
    if not isinstance(manifest.get("index"), dict):
        raise _fail("memory index manifest has no 'index' metadata")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise _fail("memory index manifest has no valid 'parts' list")
    for number, part in enumerate(parts, start=1):
        ok = (
            isinstance(part, dict)
            and part.get("file") == part_file_name(number)
            and isinstance(part.get("bytes"), int) and not isinstance(part.get("bytes"), bool)
            and isinstance(part.get("sha256"), str) and _SHA256_RE.fullmatch(part["sha256"])
        )
        if not ok:
            raise _fail(f"memory index manifest entry {number} is malformed")
    return parts


def _assemble(
    index_path: Path, manifest: dict[str, Any], confine: Callable[[Path], Path] | None
) -> tuple[dict[str, Any], list[bytes]]:
    """Read, verify and merge every shard the manifest lists."""
    parts = _checked_parts(manifest)
    documents: list[Any] = []
    postings: dict[str, Any] = {}
    chunks: list[bytes] = []
    for number, part in enumerate(parts, start=1):
        path = index_path.parent / part["file"]
        try:
            inside = path.resolve().parent == index_path.parent.resolve()
        except (OSError, RuntimeError):  # RuntimeError: a symlink loop on Python < 3.13
            inside = False
        if not inside:
            raise _fail(f"memory index shard {part['file']} resolves outside the index directory")
        if confine is not None:
            path = confine(path)
        try:
            if path.stat().st_size > 2 * part["bytes"] + 1:  # CRLF at most doubles it; skip reading junk
                raise _fail(f"memory index shard {part['file']} does not match its recorded size")
            raw = _normalized(path.read_bytes())
        except FileNotFoundError:
            raise _fail(f"memory index shard {part['file']} is missing") from None
        except OSError as exc:
            raise _fail(f"cannot read memory index shard {part['file']}: {exc}") from exc
        if len(raw) != part["bytes"] or _sha256(raw) != part["sha256"]:
            raise _fail(f"memory index shard {part['file']} does not match its recorded "
                        "size/sha256 (corrupt, truncated or merge-conflicted)")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise _fail(f"memory index shard {part['file']} is not valid JSON: {exc}") from exc
        if (not isinstance(data, dict) or data.get("artifact_type") != PART_ARTIFACT_TYPE
                or data.get("part") != number or not isinstance(data.get("documents"), list)
                or not isinstance(data.get("postings"), dict)):
            raise _fail(f"memory index shard {part['file']} has an unexpected shape")
        documents.extend(data["documents"])
        for term, entries in data["postings"].items():
            if term in postings:
                raise _fail(f"memory index shard {part['file']} repeats a postings term")
            postings[term] = entries
        chunks.append(raw)
    index = dict(manifest["index"])
    index["documents"] = documents
    index["postings"] = postings
    return index, chunks


def read_memory_index_payload(
    index_path: Path, *, confine: Callable[[Path], Path] | None = None
) -> tuple[dict[str, Any], list[bytes]]:
    """Load the memory index at *index_path*, single-file or sharded.

    A torn read (a writer replacing shards between this reader's manifest read and its shard
    reads) fails the sha256 check; the read is retried once from a fresh manifest before the
    failure is reported.

    Args:
        index_path: Path of ``memory-index.json``.
        confine: Optional callback applied to each shard path before it is read; it returns the
            path to read or raises to refuse it (the recall MCP server passes its
            project-confinement check).

    Returns:
        ``(index, chunks)``: the parsed index, and the carriage-return-normalized bytes to key
        the validation cache on.

    Raises:
        MemoryIndexError: If the file or any shard is missing, unreadable, corrupt or does not
            match the manifest. The message names the file and the rebuild command.
    """
    if not index_path.exists():
        raise MemoryIndexError(
            f"memory index not found at {index_path}; run --refresh-index or --update first"
        )
    attempts = 2
    for attempt in range(attempts):
        try:
            raw = _normalized(index_path.read_bytes())
            data = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise _fail(f"failed reading memory index at {index_path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("artifact_type") != SHARDS_ARTIFACT_TYPE:
            return data, [raw]
        try:
            index, part_chunks = _assemble(index_path, data, confine)
        except MemoryIndexError:
            if attempt == attempts - 1:
                raise
            continue
        return index, [raw, *part_chunks]
    raise _fail(f"failed reading memory index at {index_path}")  # pragma: no cover - loop returns


__all__ = [
    "DEFAULT_MAX_SHARD_BYTES",
    "EncodedIndex",
    "INDEX_FILE_NAME",
    "MAX_PARTS",
    "MAX_SHARD_BYTES_ENV",
    "PART_ARTIFACT_TYPE",
    "REBUILD_HINT",
    "SHARDS_ARTIFACT_TYPE",
    "encode_memory_index",
    "is_legacy_layout",
    "is_part_file_name",
    "orphan_part_files",
    "max_shard_bytes_from_env",
    "part_file_name",
    "read_memory_index_payload",
    "serialize_index",
    "write_encoded_index",
]
