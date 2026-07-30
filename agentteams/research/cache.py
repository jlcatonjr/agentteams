"""TTL disk cache for external retrieval — search results and fetched page text.

Before this module every ``web_search``/``fetch_text`` was a cold round trip, and
``reputable.reputable_sources`` fanned out ``len(repos) + 1`` concurrent queries per topic
with no rate limiting — plausibly a direct contributor to the challenge responses that
motivated :mod:`agentteams.research.backends`. Repeating an identical query inside one agent
session paid full latency every time and added avoidable load to a free endpoint.

Mirrors the snapshot-with-staleness pattern :mod:`agentteams.framework_research` already
established (gitignored operator-local state, regenerated on demand), applied per-entry rather
than per-run.

Security posture — this cache persists **untrusted third-party bytes**, so:

- Filenames are SHA-256 hex digests only. No fragment of the query or URL ever reaches a path
  component, so no external input can influence where a file lands.
- A corrupt, truncated, or unparseable entry is treated as a **miss**, never an error — a
  poisoned or half-written entry can degrade performance, never correctness or control flow.
- Entries above :data:`_MAX_ENTRY_BYTES` are refused on write and ignored on read.
- Writes are atomic (``*.tmp`` then ``os.replace``), so a crash mid-write cannot leave an
  entry that reads as valid.
- The cache directory is gitignored; nothing here is ever committed.

Disable entirely with ``AGENTTEAMS_RESEARCH_NO_CACHE=1``.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

#: Kill switch. Any non-empty value other than "0"/"false" disables read and write.
NO_CACHE_ENV = "AGENTTEAMS_RESEARCH_NO_CACHE"

#: Overrides the cache location. Defaults to ``<cwd>/references/research-cache``, alongside the
#: other gitignored local retrieval caches (``references/code-index/``, ``references/memory-index.*``).
CACHE_DIR_ENV = "AGENTTEAMS_RESEARCH_CACHE_DIR"

_DEFAULT_CACHE_REL = Path("references") / "research-cache"

#: Default entry lifetime. Six hours: long enough that a multi-turn agent session reuses its own
#: lookups, short enough that a daily pipeline never serves yesterday's answer as today's.
DEFAULT_TTL_S = 6 * 60 * 60

#: Per-entry ceiling. ``fetch_text`` already caps returned text at 4 000 chars by default and
#: downloads at 400 KB; 2 MB leaves generous headroom for a large explicit override while still
#: refusing anything pathological.
_MAX_ENTRY_BYTES = 2 * 1024 * 1024


def cache_enabled() -> bool:
    """Whether caching is active in this process.

    Returns:
        False when :data:`NO_CACHE_ENV` is set to anything other than an explicit
        ``"0"``/``"false"``/empty value; True otherwise.
    """
    raw = os.environ.get(NO_CACHE_ENV, "").strip().lower()
    return raw in ("", "0", "false")


def cache_dir() -> Path:
    """Return the directory cache entries live in.

    Returns:
        The path named by :data:`CACHE_DIR_ENV` when set, else
        ``references/research-cache`` under the current working directory. The directory is
        not created here — :func:`store` creates it lazily on first write.
    """
    override = os.environ.get(CACHE_DIR_ENV, "").strip()
    return Path(override) if override else Path.cwd() / _DEFAULT_CACHE_REL


def make_key(kind: str, *parts: Any) -> str:
    """Build a stable cache key from a kind label and arbitrary scalar parts.

    Args:
        kind: A namespace for the entry (e.g. ``"search"``, ``"fetch"``, ``"scholar"``).
        *parts: Values identifying the request — query text, URL, result count, backend name.
            Every part participates in the digest, so changing ``k`` or the backend produces a
            different key rather than silently reusing a differently-shaped result.

    Returns:
        A 64-character SHA-256 hex digest. Callers never construct paths themselves.
    """
    payload = "\x00".join([kind, *(str(p) for p in parts)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry_path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def load(key: str, ttl_s: int = DEFAULT_TTL_S) -> Any | None:
    """Read a cache entry.

    Args:
        key: A key from :func:`make_key`.
        ttl_s: Maximum entry age in seconds. An older entry is treated as a miss.

    Returns:
        The stored value, or ``None`` on any miss — disabled cache, absent file, expired
        entry, oversized file, malformed JSON, or unreadable path. Never raises: a cache is a
        performance device, and a broken one must degrade to "no cache", not to an error.
    """
    if not cache_enabled():
        return None
    path = _entry_path(key)
    try:
        if not path.is_file() or path.stat().st_size > _MAX_ENTRY_BYTES:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        # CH-24: named types — missing/unreadable file, malformed JSON (ValueError covers
        # JSONDecodeError), or undecodable bytes. Each means "no usable entry", which is a miss.
        return None
    if not isinstance(raw, dict) or "stored_at" not in raw or "value" not in raw:
        return None
    stored_at = raw.get("stored_at")
    if not isinstance(stored_at, (int, float)):
        return None
    if time.time() - stored_at > ttl_s:
        return None
    return raw["value"]


def store(key: str, value: Any) -> bool:
    """Write a cache entry atomically.

    Args:
        key: A key from :func:`make_key`.
        value: Any JSON-serialisable value.

    Returns:
        True when the entry was written, False on any refusal — disabled cache, value too
        large, value not serialisable, or an I/O failure. Never raises.
    """
    if not cache_enabled():
        return False
    try:
        payload = json.dumps({"stored_at": time.time(), "value": value})
    except (TypeError, ValueError):
        # CH-24: named types — a non-serialisable value is a caller bug, but a cache must not
        # be able to break the call it is accelerating.
        return False
    if len(payload.encode("utf-8")) > _MAX_ENTRY_BYTES:
        return False
    directory = cache_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Atomic: write a sibling temp file then os.replace, so a crash mid-write can never
        # leave a partially-written file that parses as a valid entry.
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, _entry_path(key))
        except OSError:
            Path(tmp_name).unlink(missing_ok=True)
            return False
    except OSError:
        # CH-24: named type — an unwritable or full cache directory. Degrade to no-cache.
        return False
    return True


def purge_expired(ttl_s: int = DEFAULT_TTL_S) -> int:
    """Delete every expired entry.

    Args:
        ttl_s: Maximum entry age in seconds.

    Returns:
        The number of entries removed. Zero when the cache is disabled, absent, or clean.
        Never raises — an entry that cannot be inspected or removed is skipped.
    """
    if not cache_enabled():
        return 0
    directory = cache_dir()
    if not directory.is_dir():
        return 0
    removed = 0
    now = time.time()
    for path in directory.glob("*.json"):
        if _is_live(path, now, ttl_s):
            continue
        # An expired OR unreadable/corrupt entry is garbage either way, so both take the same
        # branch — which is also why this needs no swallow-and-continue handler of its own.
        removed += int(_unlink_quietly(path))
    return removed


def _is_live(path: Path, now: float, ttl_s: int) -> bool:
    """Whether a cache file is a readable, unexpired entry.

    Args:
        path: The candidate entry file.
        now: Current wall-clock time (passed in so one purge pass uses a single instant).
        ttl_s: Maximum entry age in seconds.

    Returns:
        True only for a well-formed entry within its TTL. An unreadable or malformed file
        returns False — it is garbage, and garbage is treated the same as expired.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        # CH-24: named types — unreadable file, malformed JSON, or undecodable bytes.
        return False
    stored_at = raw.get("stored_at") if isinstance(raw, dict) else None
    return isinstance(stored_at, (int, float)) and now - stored_at <= ttl_s


def _unlink_quietly(path: Path) -> bool:
    """Delete ``path``, reporting success rather than raising.

    Args:
        path: The file to remove.

    Returns:
        True when the file was removed, False when it could not be (permissions, or a
        concurrent purge already removed it).
    """
    try:
        path.unlink()
    except OSError:  # CH-24: named type — permissions, or a concurrent removal.
        return False
    return True
