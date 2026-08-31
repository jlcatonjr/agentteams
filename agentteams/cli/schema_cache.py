"""schema_cache.py — shared JSON-Schema validation + a content-hash validation cache.

Carved from ``cli/artifacts.py`` (CH-07 length ceiling) once the memory-index and
code-index hot paths, plus the three build-time writers, all needed the same
``require-jsonschema → load-schema → validate`` machinery. Kept in ``agentteams/cli/``
so ``_schema_path`` resolves ``parents[2]/schemas`` to the same repo-root ``schemas``
dir as ``artifacts.py``. Leaf-ish module: imports only ``atomicio`` (a leaf) — no
import back into ``artifacts``, so the graph stays acyclic.

Schema validation dominates every hot-path read: on a real ~16MB memory index
``jsonschema`` is ~95% of wall-clock and the ranking math it guards is <0.1%.
Validation is pure — identical (content bytes, schema bytes) always yield the same
verdict — so a reader records a tiny ``.vcache`` sidecar noting the (content-hash,
schema-hash) pair that last passed and short-circuits when it still matches.
Measured ~28.8x on a real memory index; the same primitive backs the code-index
read path.

FAIL-OPEN by construction: any missing/torn/mismatched sidecar or any error runs
the full validation, and the sidecar is written *only after* a validation success.
A changed payload OR a changed (e.g. package-upgraded) schema changes the key and
forces a re-validate. The sidecar holds only two sha256 hashes (no machine-specific
data — C4), is a ``.vcache`` (never mistaken for a generator ``.json`` artifact; the
source scanner only takes ``*.md``), and is gitignored in every consumer. See
SEC-CM-2026-07-16-AGENTTEAMS-VCACHE-001 (@security C0-C6 + repo-liaison C-L1..C-L7).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

from agentteams.atomicio import _atomic_write_text

_SCHEMA_VALIDATOR_CACHE: dict[str, Any] = {}  # schema_hash -> compiled Draft7Validator


def _require_jsonschema(error_cls: type[Exception], artifact: str) -> Any:
    """Import and return ``jsonschema``, or raise *error_cls* if it is absent.

    A *missing* jsonschema module is an environment gap (e.g. the run was driven
    by an interpreter without the dep), not a malformed artifact. Every artifact
    writer is wrapped by ``main()`` in a non-fatal ``except (OSError, <error_cls>)``
    handler, so converting the ``ImportError`` into the writer's own error type
    lets a fully successful, non-destructive merge finish cleanly (exit 0) instead
    of aborting with a traceback *after* the merge already wrote every file. The
    artifact is re-emitted on the next ``--update`` once jsonschema is installed.
    """
    try:
        import jsonschema
        return jsonschema
    except ImportError as exc:
        raise error_cls(
            f"jsonschema is not installed; cannot validate the {artifact}. "
            "The merge itself is complete — install jsonschema (or run via the "
            f"`agentteams` console entry point) to re-emit the {artifact} on the "
            "next --update."
        ) from exc


def _schema_path(name: str) -> Path:
    """Resolve a trusted, package-bundled schema by filename (C3/C6)."""
    return Path(__file__).resolve().parents[2] / "schemas" / name


def _load_schema_bytes(schema_path: Path, error_cls: type[Exception], label: str) -> bytes:
    """Read a bundled schema's raw bytes, or raise *error_cls* (never OSError).

    Bytes (not a parsed dict) so callers can fold them into a content-hash key.
    """
    try:
        return schema_path.read_bytes()
    except OSError as exc:
        raise error_cls(f"{label} schema unavailable ({schema_path}): {exc}") from exc


def _validate_against_schema(
    obj: object, schema_bytes: bytes, *, error_cls: type[Exception], label: str
) -> None:
    """Validate *obj* against the schema in *schema_bytes* with a process-cached
    ``Draft7Validator``. Raises *error_cls* (a RuntimeError, never OSError) on a
    malformed schema or a validation failure, so every caller can treat it as
    non-fatal. This is the one place the package talks to ``jsonschema``.
    """
    jsonschema = _require_jsonschema(error_cls, label)
    try:
        schema = json.loads(schema_bytes)
    except ValueError as exc:
        raise error_cls(f"{label} schema unavailable: {exc}") from exc
    schema_hash = hashlib.sha256(schema_bytes).hexdigest()
    validator = _SCHEMA_VALIDATOR_CACHE.get(schema_hash)
    if validator is None:
        validator = jsonschema.Draft7Validator(schema)
        _SCHEMA_VALIDATOR_CACHE[schema_hash] = validator
    try:
        validator.validate(obj)
    except jsonschema.ValidationError as exc:
        raise error_cls(f"{label} failed schema validation: {exc.message}") from exc


def _vcache_content_digest(content_chunks: list[bytes]) -> str:
    """Order-sensitive digest of one or more content blobs. A single chunk hashes
    to ``sha256(chunk)`` so the memory-index key is unchanged; multiple chunks (a
    code-index manifest + partitions) fold their hex hashes in order. A derivation
    change only invalidates local sidecars, which fail open and self-heal on the
    next read.
    """
    if len(content_chunks) == 1:
        return hashlib.sha256(content_chunks[0]).hexdigest()
    outer = hashlib.sha256()
    for chunk in content_chunks:
        outer.update(hashlib.sha256(chunk).hexdigest().encode("ascii"))
    return outer.hexdigest()


def _vcache_key(content_chunks: list[bytes], schema_bytes: bytes) -> str:
    """Cache identity = content AND schema (C-L1)."""
    return f"{_vcache_content_digest(content_chunks)}:{hashlib.sha256(schema_bytes).hexdigest()}"


def _vcache_hit(sidecar_path: Path, key: str, artifact_type: str) -> bool:
    """True only when *sidecar_path* records a prior success for exactly *key*.

    Fail-open (C1/C-L2): any error / missing file / mismatch → False → the caller
    runs the full validation.
    """
    try:
        data = json.loads(sidecar_path.read_bytes())
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("artifact_type") == artifact_type
        and data.get("validated_key") == key
    )


def _vcache_store(sidecar_path: Path, key: str, artifact_type: str) -> None:
    """Record a validation success atomically (C5) via the shared atomicio
    primitive. Non-fatal (C1, CH-24): a write failure just means the next read
    re-validates. Sidecar carries only the two-hash key (C4)."""
    payload = {"artifact_type": artifact_type, "validated_key": key}
    with contextlib.suppress(OSError):
        _atomic_write_text(sidecar_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _validate_cached(
    obj: object,
    content_chunks: list[bytes],
    schema_bytes: bytes,
    *,
    error_cls: type[Exception],
    label: str,
    sidecar_path: Path | None,
    artifact_type: str,
) -> None:
    """Validate *obj*, skipping the work when *sidecar_path* already records a pass
    for this (content, schema) key. ``sidecar_path=None`` forces a full, uncached
    validation (the incremental-update callbacks do this).

    ``content_chunks`` must be the exact bytes read from / to be written to disk so
    the key matches on the next read (C5 — no re-read, no TOCTOU).
    """
    if sidecar_path is not None:
        key = _vcache_key(content_chunks, schema_bytes)
        if _vcache_hit(sidecar_path, key, artifact_type):
            return  # unchanged content + unchanged schema already passed (C-L1/C-L2)
    _validate_against_schema(obj, schema_bytes, error_cls=error_cls, label=label)
    if sidecar_path is not None:
        _vcache_store(sidecar_path, key, artifact_type)  # only after success (C1/C-L2)
