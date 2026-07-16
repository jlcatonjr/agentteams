"""Tests for the memory-index validation cache (perf: skip re-validating an
unchanged index on the ``--query-index`` hot path).

Covers the invariants required by SEC-CM-2026-07-16-AGENTTEAMS-VCACHE-001
(@security C0-C6 + repo-liaison C-L1..C-L7):
  - cache hit skips the (expensive) schema validation
  - a changed index OR a changed schema forces a re-validate (C-L1)
  - only a *successful* validation is cached; failures are never cached (C-L2)
  - fail-open: a missing/corrupt/mismatched sidecar → full validation (C1)
  - a stale/mismatched sidecar can never mask an invalid index
  - the on-disk index write is atomic (no torn file / leftover .tmp)
"""
from __future__ import annotations

import json

import jsonschema as _js
import pytest

from agentteams.cli import artifacts
from agentteams.memory_index import build_memory_index

VCACHE_REL = "references/memory-index.vcache"
INDEX_REL = "references/memory-index.json"


@pytest.fixture(autouse=True)
def _clear_validator_cache():
    artifacts._SCHEMA_VALIDATOR_CACHE.clear()
    yield
    artifacts._SCHEMA_VALIDATOR_CACHE.clear()


def _valid_index() -> dict:
    # Empty source list ⇒ an empty-but-schema-valid index.
    return build_memory_index([], project_name="t", framework="claude")


def _install_index(out, index) -> "artifacts.Path":
    p = out / INDEX_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return out


def test_read_validates_and_writes_sidecar(tmp_path):
    out = _install_index(tmp_path, _valid_index())
    assert not (out / VCACHE_REL).exists()
    idx = artifacts._read_memory_index(out)
    assert idx["artifact_type"] == "memory-index"
    data = json.loads((out / VCACHE_REL).read_bytes())
    assert data["artifact_type"] == "memory-index-vcache"
    # sidecar carries only the two-hash key — no machine-specific data (C4)
    assert set(data) == {"artifact_type", "validated_key"}
    assert data["validated_key"].count(":") == 1


def test_cache_hit_skips_validation(tmp_path, monkeypatch):
    out = _install_index(tmp_path, _valid_index())
    calls = {"n": 0}
    real_cls = _js.Draft7Validator

    class CountingValidator:
        def __init__(self, schema):
            self._v = real_cls(schema)

        def validate(self, *a, **k):
            calls["n"] += 1
            return self._v.validate(*a, **k)

    monkeypatch.setattr(_js, "Draft7Validator", CountingValidator)
    artifacts._read_memory_index(out)  # cold → 1 validation
    assert calls["n"] == 1
    artifacts._read_memory_index(out)  # warm → cache hit, no validation
    artifacts._read_memory_index(out)
    assert calls["n"] == 1


def test_changed_index_content_revalidates(tmp_path):
    out = _install_index(tmp_path, _valid_index())
    artifacts._read_memory_index(out)  # warm the cache for the valid bytes
    # overwrite with a different (now invalid) payload — key changes → miss → validate → reject
    (out / INDEX_REL).write_text('{"artifact_type": "memory-index"}', encoding="utf-8")
    with pytest.raises(artifacts.MemoryIndexError):
        artifacts._read_memory_index(out)


def test_schema_change_forces_revalidation(tmp_path, monkeypatch):
    out = _install_index(tmp_path, _valid_index())
    artifacts._read_memory_index(out)  # warm for schema A
    key1 = json.loads((out / VCACHE_REL).read_bytes())["validated_key"]

    # Point the validator at a byte-different but still-valid schema copy
    # (simulates a package upgrade). Same index bytes, different schema hash.
    schema = json.loads(artifacts._memory_index_schema_path().read_bytes())
    schema["description"] = schema.get("description", "") + " (test variant)"
    alt = tmp_path / "alt-schema.json"
    alt.write_text(json.dumps(schema), encoding="utf-8")
    monkeypatch.setattr(artifacts, "_memory_index_schema_path", lambda: alt)

    artifacts._read_memory_index(out)  # schema hash changed → miss → re-validate → new key
    key2 = json.loads((out / VCACHE_REL).read_bytes())["validated_key"]
    assert key1 != key2


def test_failopen_on_corrupt_sidecar(tmp_path):
    out = _install_index(tmp_path, _valid_index())
    vc = out / VCACHE_REL
    vc.parent.mkdir(parents=True, exist_ok=True)
    vc.write_text("{ this is not valid json", encoding="utf-8")
    idx = artifacts._read_memory_index(out)  # must NOT raise — treat as miss, validate, pass
    assert idx["artifact_type"] == "memory-index"
    assert json.loads(vc.read_bytes())["artifact_type"] == "memory-index-vcache"


def test_invalid_index_rejected_and_no_sidecar_written(tmp_path):
    out = _install_index(tmp_path, {"artifact_type": "memory-index"})  # missing required fields
    with pytest.raises(artifacts.MemoryIndexError):
        artifacts._read_memory_index(out)
    assert not (out / VCACHE_REL).exists()  # never cache a failure (C-L2)


def test_stale_sidecar_does_not_mask_invalid_index(tmp_path):
    out = _install_index(tmp_path, {"artifact_type": "memory-index"})  # invalid
    vc = out / VCACHE_REL
    vc.parent.mkdir(parents=True, exist_ok=True)
    # A sidecar whose key does NOT match this index's (bytes, schema) pair.
    vc.write_text(
        json.dumps({"artifact_type": "memory-index-vcache", "validated_key": "dead:beef"}),
        encoding="utf-8",
    )
    with pytest.raises(artifacts.MemoryIndexError):  # mismatch → miss → validate → reject
        artifacts._read_memory_index(out)


def test_write_path_atomic_and_caches(tmp_path):
    manifest = {"project_name": "t", "framework": "claude"}
    path = artifacts._write_memory_index(manifest, tmp_path)
    assert path.exists()
    assert not (tmp_path / "references/memory-index.json.tmp").exists()  # no torn write
    assert (tmp_path / VCACHE_REL).exists()  # freshly-written bytes cached
    # a subsequent read of those exact bytes is a cache hit and still returns a valid index
    idx = artifacts._read_memory_index(tmp_path)
    assert idx["artifact_type"] == "memory-index"


def test_incremental_callback_still_validates(tmp_path):
    # _validate_memory_index_schema is the incremental path's callback (no cache).
    artifacts._validate_memory_index_schema(_valid_index())  # valid → no raise
    with pytest.raises(artifacts.MemoryIndexError):
        artifacts._validate_memory_index_schema({"artifact_type": "memory-index"})
