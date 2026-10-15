"""scan_operational.py — which pipeline-written JSON files the content scanner exempts.

Carved from ``scan.py`` (CH-07: it sat at 975 of 1000 lines once memory-index shards needed a
rule). Integrity-pinned like ``scan.py`` (``agentteams.integrity.ENFORCEMENT_MODULES``): an
exemption predicate decides what the scanner does *not* look at, so a silent widening here
blinds it as surely as an edit to the scanner would. Stdlib-only and imports nothing from the
package, for the same reason: a predicate that delegated to an unpinned module could be widened
without tripping the pin.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# T3a.2 v2: operational-metadata JSON files written by the build
# pipeline legitimately carry absolute paths and content hashes (delivery
# receipts, memory index, build log, etc.). The scanner skips its
# absolute-path, entropy and unresolved-placeholder detectors for these
# files; injection and pattern-based credential checks still apply.
_OPERATIONAL_JSON_NAMES = frozenset({
    "build-log.json",
    "delivery-receipt.json",
    "memory-index.json",
    "eval-suite.json",
    "doc-hashes.json",
    "security-vulnerability-watch.json",
})

#: Directory, relative to the scanned agents dir, where the pipeline writes those files.
#:
#: The exemption was keyed on BASENAME and applied via `rglob`, so any file anywhere in the
#: tree named `memory-index.json` inherited it — an agent with write access could pre-blind the
#: scanner to its own payload simply by choosing the filename (probe B9, the LLM08
#: retrieval-poisoning path). Anchoring to the one directory the pipeline actually writes them
#: to makes the exemption a statement about provenance rather than about spelling.
_OPERATIONAL_JSON_DIR = "references"

#: Shards of a size-capped memory index (``agentteams.memory_index_store``). Same shape as that
#: module's pattern, restated rather than imported (see the module docstring).
_MEMORY_INDEX_PART_RE = re.compile(r"memory-index\.part-[0-9]{4}\.json")

#: A shard manifest is about 1 KB per hundred parts; anything larger is a whole index, not one.
_MANIFEST_MAX_BYTES = 1 << 20


def _is_listed_memory_index_part(file_path: Path) -> bool:
    """True when the sibling shard manifest lists *file_path* with its exact sha256.

    Rule 12: exempting the shard *name pattern* alone would hand 9999 new exempt names to
    anyone who can write to ``references/``. Requiring the manifest to list the file, with the
    hash of its current bytes, keeps the exemption to the shards the index writer produced: an
    unlisted, planted or edited part is scanned like any other file. Hashes are over
    CR-stripped bytes, matching the writer.

    Args:
        file_path: A file whose name matches the shard pattern.

    Returns:
        Whether it is a verified part of the index beside it. Any read or parse failure is False.
    """
    manifest_path = file_path.parent / "memory-index.json"
    try:
        if manifest_path.stat().st_size > _MANIFEST_MAX_BYTES:
            return False
        manifest = json.loads(manifest_path.read_bytes())
        digest = hashlib.sha256(file_path.read_bytes().replace(b"\r", b"")).hexdigest()
    except (OSError, ValueError):
        return False
    if not isinstance(manifest, dict) or manifest.get("artifact_type") != "memory-index-shards":
        return False
    parts = manifest.get("parts")
    return isinstance(parts, list) and any(
        isinstance(p, dict) and p.get("file") == file_path.name and p.get("sha256") == digest
        for p in parts
    )


def _is_operational_json(file_path: Path, agents_dir: Path) -> bool:
    """True when *file_path* is a pipeline-written operational JSON file.

    Requires BOTH the known basename AND residence directly in the agents dir's
    ``references/`` directory. A file that satisfies only one is scanned normally. A memory-index
    shard (``memory-index.part-NNNN.json``) additionally has to be listed, with its current
    sha256, by the manifest beside it (:func:`_is_listed_memory_index_part`).

    Args:
        file_path: The file being scanned.
        agents_dir: The scanned agents directory.

    Returns:
        Whether the absolute-path, entropy and placeholder detectors are skipped for it.
    """
    is_named = file_path.name in _OPERATIONAL_JSON_NAMES
    if not is_named and not _MEMORY_INDEX_PART_RE.fullmatch(file_path.name):
        return False
    try:
        in_refs = file_path.resolve().parent == (agents_dir / _OPERATIONAL_JSON_DIR).resolve()
    except OSError:
        return False
    return in_refs and (is_named or _is_listed_memory_index_part(file_path))
