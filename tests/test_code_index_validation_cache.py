"""Tests for the code-index validation cache (perf: skip re-validating an
unchanged manifest + partitions on the ``--query-code`` hot path).

Reuses the primitive proven for the memory index, extended to a MULTI-FILE key
(the manifest AND every partition), so a change to ANY file forces a
re-validate. Mirrors the memory-index invariants from
``test_memory_index_validation_cache.py``:
  - a cache hit skips the (expensive) schema validation of manifest + partitions
  - a changed manifest OR a changed partition OR a changed schema re-validates
  - only a *successful* validation is cached; failures are never cached (C-L2)
  - fail-open: a missing/corrupt/mismatched sidecar -> full validation (C1)
  - a stale/mismatched sidecar can never mask an invalid partition
"""
from __future__ import annotations

import json

import jsonschema as _js
import pytest

from agentteams.cli import artifacts

VCACHE_REL = artifacts.CODE_INDEX_VCACHE_REL_PATH
CODE_INDEX_REL = artifacts.CODE_INDEX_REL_DIR


@pytest.fixture(autouse=True)
def _clear_validator_cache():
    artifacts._SCHEMA_VALIDATOR_CACHE.clear()
    yield
    artifacts._SCHEMA_VALIDATOR_CACHE.clear()


def _build_index(tmp_path) -> "artifacts.Path":
    """Write a deterministic, empty-but-schema-valid code index to <out>.

    ``existing_project_path`` points at an empty dir so no local scripts / API
    imports are collected — the three partitions are empty and stable. No
    ``.vcache`` is written by the build (only :func:`_read_code_index` writes it).
    """
    root = tmp_path / "proj"
    root.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    manifest = {
        "project_name": "t",
        "framework": "claude",
        "existing_project_path": str(root),
    }
    artifacts._write_code_index(manifest, out)
    return out


def _partition_path(out) -> "artifacts.Path":
    """Path of one on-disk partition file (via the manifest's declared file)."""
    cache_dir = out / CODE_INDEX_REL
    man = json.loads((cache_dir / "manifest.json").read_bytes())
    name, meta = next(iter(man["partitions"].items()))
    return cache_dir / meta.get("file", f"{name}.json")


def test_read_validates_and_writes_sidecar(tmp_path):
    out = _build_index(tmp_path)
    assert not (out / VCACHE_REL).exists()
    data = artifacts._read_code_index(out)
    assert set(data) == {"manifest", "partitions"}
    sidecar = json.loads((out / VCACHE_REL).read_bytes())
    assert sidecar["artifact_type"] == "code-index-vcache"
    # sidecar carries only the two-hash key — no machine-specific data (C4)
    assert set(sidecar) == {"artifact_type", "validated_key"}
    assert sidecar["validated_key"].count(":") == 1


def test_cache_hit_skips_validation(tmp_path, monkeypatch):
    out = _build_index(tmp_path)  # built before patching, so its build validates freely
    # The build above cached a real Draft7Validator; drop it so the cold read
    # below constructs (and counts through) the CountingValidator.
    artifacts._SCHEMA_VALIDATOR_CACHE.clear()
    calls = {"n": 0}
    real_cls = _js.Draft7Validator

    class CountingValidator:
        def __init__(self, schema):
            self._v = real_cls(schema)

        def validate(self, *a, **k):
            calls["n"] += 1
            return self._v.validate(*a, **k)

    monkeypatch.setattr(_js, "Draft7Validator", CountingValidator)
    artifacts._read_code_index(out)  # cold → validates manifest + N partitions
    cold = calls["n"]
    assert cold >= 2  # manifest + at least one partition
    artifacts._read_code_index(out)  # warm → cache hit, no validation
    artifacts._read_code_index(out)
    assert calls["n"] == cold


def test_changed_partition_content_revalidates(tmp_path):
    out = _build_index(tmp_path)
    artifacts._read_code_index(out)  # warm the cache
    # Corrupt one partition to a parseable-but-invalid payload — its bytes change
    # → multi-file key changes → miss → re-validate → reject.
    _partition_path(out).write_text('{"artifact_type": "code-index"}', encoding="utf-8")
    with pytest.raises(artifacts.CodeIndexError):
        artifacts._read_code_index(out)


def test_changed_manifest_content_revalidates(tmp_path):
    out = _build_index(tmp_path)
    artifacts._read_code_index(out)  # warm the cache
    # Rewrite the manifest to an invalid payload — manifest bytes are part of the
    # key, so the key changes → miss → re-validate → reject.
    (out / CODE_INDEX_REL / "manifest.json").write_text(
        '{"artifact_type": "code-index"}', encoding="utf-8"
    )
    with pytest.raises(artifacts.CodeIndexError):
        artifacts._read_code_index(out)


def test_schema_change_forces_revalidation(tmp_path, monkeypatch):
    out = _build_index(tmp_path)
    artifacts._read_code_index(out)
    key1 = json.loads((out / VCACHE_REL).read_bytes())["validated_key"]

    # A byte-different but still-valid schema (simulates a package upgrade):
    # same content bytes, different schema hash → key changes → re-validate.
    schema = json.loads(artifacts._code_index_schema_bytes())
    schema["description"] = schema.get("description", "") + " (test variant)"
    variant = (json.dumps(schema) + "\n").encode("utf-8")
    monkeypatch.setattr(artifacts, "_code_index_schema_bytes", lambda: variant)

    artifacts._read_code_index(out)
    key2 = json.loads((out / VCACHE_REL).read_bytes())["validated_key"]
    assert key1 != key2


def test_failopen_on_corrupt_sidecar(tmp_path):
    out = _build_index(tmp_path)
    vc = out / VCACHE_REL
    vc.parent.mkdir(parents=True, exist_ok=True)
    vc.write_text("{ this is not valid json", encoding="utf-8")
    data = artifacts._read_code_index(out)  # must NOT raise — treat as miss, validate, pass
    assert set(data) == {"manifest", "partitions"}
    assert json.loads(vc.read_bytes())["artifact_type"] == "code-index-vcache"


def test_invalid_partition_rejected_and_no_sidecar_written(tmp_path):
    out = _build_index(tmp_path)
    (out / VCACHE_REL).unlink(missing_ok=True)  # start with no sidecar
    _partition_path(out).write_text('{"artifact_type": "code-index"}', encoding="utf-8")
    with pytest.raises(artifacts.CodeIndexError):
        artifacts._read_code_index(out)
    assert not (out / VCACHE_REL).exists()  # never cache a failure (C-L2)


def test_stale_sidecar_does_not_mask_invalid_partition(tmp_path):
    out = _build_index(tmp_path)
    _partition_path(out).write_text('{"artifact_type": "code-index"}', encoding="utf-8")
    vc = out / VCACHE_REL
    vc.parent.mkdir(parents=True, exist_ok=True)
    # A sidecar whose key does NOT match this (manifest+partitions, schema) pair.
    vc.write_text(
        json.dumps({"artifact_type": "code-index-vcache", "validated_key": "dead:beef"}),
        encoding="utf-8",
    )
    with pytest.raises(artifacts.CodeIndexError):  # mismatch → miss → validate → reject
        artifacts._read_code_index(out)
