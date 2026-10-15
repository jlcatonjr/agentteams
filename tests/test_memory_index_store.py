"""Tests for the memory index's on-disk layout: compact lines and native size-capped shards.

Covers ``agentteams.memory_index_store`` and its wiring: round trip (single file and sharded),
determinism, the size cap, fail-safe reads of a missing / corrupt / conflicted / tampered shard,
backward compatibility with legacy ``indent=2`` single-file indexes, migration in both directions,
Windows line endings, the scanner exemption, fleet volatility, and the recall MCP server.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import memory_index_store as st
from agentteams.cli import artifacts
from agentteams.errors import MemoryIndexError
from agentteams.memory_index import build_memory_index, query_index

INDEX_REL = "references/memory-index.json"
_WORDS = (
    "deploy collector schedule retry backoff credential rotation audit finding weekly summary "
    "database migration approval email pipeline review security incident governance handoff"
).split()


@pytest.fixture(autouse=True)
def _clear_validator_cache():
    artifacts._SCHEMA_VALIDATOR_CACHE.clear()
    yield
    artifacts._SCHEMA_VALIDATOR_CACHE.clear()


def _project(tmp_path: Path, n_docs: int = 40) -> tuple[Path, Path, dict]:
    """A project with *n_docs* work summaries; returns (root, agents dir, manifest)."""
    ws = tmp_path / "workSummaries"
    ws.mkdir(parents=True)
    for i in range(n_docs):
        words = " ".join(_WORDS[(i + j) % len(_WORDS)] for j in range(40))
        body = "\n\n".join(f"Paragraph {p} of doc {i} covers {words} item{i}x{p}." for p in range(4))
        (ws / f"day-{i:03d}.md").write_text(f"# Day {i}\n\n{body}\n", encoding="utf-8")
    agents = tmp_path / ".github" / "agents"
    (agents / "references").mkdir(parents=True)
    manifest = {"project_name": "P", "framework": "claude", "existing_project_path": str(tmp_path)}
    return tmp_path, agents, manifest


def _index(tmp_path: Path, n_docs: int = 40) -> dict:
    root, _, _ = _project(tmp_path, n_docs)
    return build_memory_index(sorted((root / "workSummaries").glob("*.md")), project_name="P",
                              framework="claude", root=root)


def _parts(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if st.is_part_file_name(p.name))


# --- layout and round trip -------------------------------------------------------------


def test_compact_layout_is_same_object_and_smaller(tmp_path):
    index = _index(tmp_path)
    text = st.serialize_index(index)
    assert json.loads(text) == index
    assert len(text) < len(json.dumps(index, indent=2)) * 0.75
    # one line per stored paragraph, so line-based scanners still see one passage per line
    assert all(json.dumps(p) in text.splitlines() or json.dumps(p) + "," in text.splitlines()
               for p in index["documents"][0]["paragraphs"])


def test_small_index_stays_single_file(tmp_path):
    index = _index(tmp_path, n_docs=3)
    enc = st.encode_memory_index(index, max_shard_bytes=10_000_000)
    assert not enc.sharded and [n for n, _ in enc.files] == ["memory-index.json"]
    path = tmp_path / "out" / "memory-index.json"
    st.write_encoded_index(path, enc)
    loaded, chunks = st.read_memory_index_payload(path)
    assert loaded == index and chunks == [path.read_bytes()]


def test_sharded_round_trip_and_identical_queries(tmp_path):
    index = _index(tmp_path)
    enc = st.encode_memory_index(index, max_shard_bytes=4096)
    assert enc.sharded
    path = tmp_path / "out" / "memory-index.json"
    st.write_encoded_index(path, enc)
    assert len(_parts(path.parent)) >= 3
    manifest = json.loads(path.read_text())
    assert manifest["artifact_type"] == st.SHARDS_ARTIFACT_TYPE
    loaded, _ = st.read_memory_index_payload(path)
    assert loaded == index
    for strategy in ("lexical", "vector"):
        for q in ("deploy retry", "credential rotation audit", "item7x2", "governance handoff"):
            assert query_index(loaded, q, k=10, strategy=strategy) == query_index(
                index, q, k=10, strategy=strategy)


def test_every_shard_respects_the_cap_and_is_valid_json(tmp_path):
    index = _index(tmp_path)
    cap = 4096
    enc = st.encode_memory_index(index, max_shard_bytes=cap)
    for name, text in enc.files[:-1]:
        data = json.loads(text)
        assert data["artifact_type"] == st.PART_ARTIFACT_TYPE
        items = len(data["documents"]) + len(data["postings"])
        assert len(text.encode()) <= cap or items == 1, name


def test_encoding_is_deterministic(tmp_path):
    index = _index(tmp_path)
    a = st.encode_memory_index(index, max_shard_bytes=4096)
    b = st.encode_memory_index(json.loads(json.dumps(index)), max_shard_bytes=4096)
    assert a == b
    out1, out2 = tmp_path / "o1" / "memory-index.json", tmp_path / "o2" / "memory-index.json"
    st.write_encoded_index(out1, a)
    st.write_encoded_index(out2, b)
    assert [p.read_bytes() for p in sorted(out1.parent.iterdir())] == [
        p.read_bytes() for p in sorted(out2.parent.iterdir())]


def test_cap_from_env(monkeypatch):
    monkeypatch.delenv(st.MAX_SHARD_BYTES_ENV, raising=False)
    assert st.max_shard_bytes_from_env() == st.DEFAULT_MAX_SHARD_BYTES
    assert st.DEFAULT_MAX_SHARD_BYTES < 50_000_000  # under GitHub's warning threshold
    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "65536")
    assert st.max_shard_bytes_from_env() == 65536
    for bad in ("5", "not-a-number", "-1"):  # a misconfigured cap fails loudly, never silently
        monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, bad)
        with pytest.raises(MemoryIndexError, match=st.MAX_SHARD_BYTES_ENV):
            st.max_shard_bytes_from_env()


@pytest.mark.parametrize("cap", [0, -5, True, 1023, "4096"])
def test_encode_rejects_bad_caps(tmp_path, cap):
    with pytest.raises(ValueError):
        st.encode_memory_index(_index(tmp_path, n_docs=2), max_shard_bytes=cap)


def test_encode_and_part_names_reject_bad_input():
    with pytest.raises(TypeError):
        st.encode_memory_index([], max_shard_bytes=4096)  # type: ignore[arg-type]
    for bad in (True, 0, 10_000):
        with pytest.raises(ValueError):
            st.part_file_name(bad)


# --- fail-safe reads -------------------------------------------------------------------


def _sharded(tmp_path: Path) -> Path:
    path = tmp_path / "out" / "memory-index.json"
    st.write_encoded_index(path, st.encode_memory_index(_index(tmp_path), max_shard_bytes=4096))
    return path


def test_missing_shard_fails_with_rebuild_hint(tmp_path):
    path = _sharded(tmp_path)
    _parts(path.parent)[1].unlink()
    with pytest.raises(MemoryIndexError, match=r"part-0002\.json is missing.*--refresh-index"):
        st.read_memory_index_payload(path)


@pytest.mark.parametrize("mutate", [
    lambda b: b[:-10],                                            # truncated
    lambda b: b.replace(b"deploy", b"DEPLOY", 1),                # same size, different bytes
    lambda b: b"<<<<<<< HEAD\n" + b + b"=======\n>>>>>>> theirs\n",  # merge conflict
])
def test_corrupt_shard_fails_safe(tmp_path, mutate):
    path = _sharded(tmp_path)
    part = _parts(path.parent)[0]
    part.write_bytes(mutate(part.read_bytes()))
    with pytest.raises(MemoryIndexError, match=r"part-0001\.json does not match.*--refresh-index"):
        st.read_memory_index_payload(path)


def test_conflicted_manifest_fails_safe(tmp_path):
    path = _sharded(tmp_path)
    path.write_text("<<<<<<< HEAD\n" + path.read_text() + "=======\n>>>>>>> theirs\n")
    with pytest.raises(MemoryIndexError, match="--refresh-index"):
        st.read_memory_index_payload(path)


@pytest.mark.parametrize("bad_file", ["../../etc/passwd", "memory-index.part-0009.json",
                                      "/abs/memory-index.part-0001.json", "memory-index.part-٠٠٠١.json"])
def test_manifest_cannot_name_other_files(tmp_path, bad_file):
    path = _sharded(tmp_path)
    manifest = json.loads(path.read_text())
    manifest["parts"][0]["file"] = bad_file
    path.write_text(json.dumps(manifest))
    with pytest.raises(MemoryIndexError, match="entry 1 is malformed"):
        st.read_memory_index_payload(path)


def test_symlinked_shard_is_refused_on_the_cli_path(tmp_path):
    """A committed shard that is a symlink out of the index directory is never followed,
    even when its target's bytes match the manifest."""
    path = _sharded(tmp_path)
    part = _parts(path.parent)[0]
    outside = tmp_path / "outside.json"
    outside.write_bytes(part.read_bytes())
    part.unlink()
    part.symlink_to(outside)
    with pytest.raises(MemoryIndexError, match="resolves outside the index directory"):
        st.read_memory_index_payload(path)


def test_stale_cleanup_skips_directories(tmp_path):
    path = _sharded(tmp_path)
    squatter = path.parent / st.part_file_name(9999)
    squatter.mkdir()
    st.write_encoded_index(path, st.encode_memory_index(_index(tmp_path / "again"),
                                                         max_shard_bytes=4096))
    assert squatter.is_dir()


def test_orphan_parts_are_reported(tmp_path, monkeypatch, capsys):
    _, agents, manifest = _project(tmp_path)
    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "4096")
    artifacts._write_memory_index(manifest, agents)
    index_path = agents / INDEX_REL
    assert st.orphan_part_files(index_path) == []
    # An older writer replaces the manifest with a single file and leaves the parts.
    index = artifacts._read_memory_index(agents)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    assert len(st.orphan_part_files(index_path)) >= 3
    artifacts._read_memory_index(agents)
    assert "unused memory-index.part-" in capsys.readouterr().err


def test_crlf_checkout_still_verifies(tmp_path):
    """Windows / core.autocrlf rewrites LF to CRLF; the sha256 is taken over CR-stripped bytes."""
    path = _sharded(tmp_path)
    expected, _ = st.read_memory_index_payload(path)
    for p in [path, *_parts(path.parent)]:
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert st.read_memory_index_payload(path)[0] == expected


def test_torn_read_is_retried_once(tmp_path, monkeypatch):
    path = _sharded(tmp_path)
    expected, _ = st.read_memory_index_payload(path)
    real = st._assemble
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise MemoryIndexError("torn")
        return real(*args, **kwargs)

    monkeypatch.setattr(st, "_assemble", flaky)
    assert st.read_memory_index_payload(path)[0] == expected and calls["n"] == 2


def test_manifest_is_rejected_by_the_single_file_schema(tmp_path):
    """A reader that predates sharding validates memory-index.json against the index schema;
    the manifest must fail that, not pass as an empty index."""
    from agentteams.cli.schema_cache import _schema_path, _validate_against_schema

    manifest = json.loads(_sharded(tmp_path).read_text())
    with pytest.raises(MemoryIndexError):
        _validate_against_schema(manifest, _schema_path("memory-index.schema.json").read_bytes(),
                                 error_cls=MemoryIndexError, label="memory index")


# --- CLI wiring: backward compatibility, migration, validation cache --------------------


def test_legacy_single_file_index_still_reads(tmp_path):
    root, agents, manifest = _project(tmp_path, n_docs=5)
    index = build_memory_index(sorted((root / "workSummaries").glob("*.md")), project_name="P",
                               framework="claude", root=root)
    (agents / INDEX_REL).write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    assert st.is_legacy_layout(agents / INDEX_REL)
    assert artifacts._read_memory_index(agents) == index


def test_migration_legacy_to_shards_and_back(tmp_path, monkeypatch):
    root, agents, manifest = _project(tmp_path)
    index_path = agents / INDEX_REL
    index = build_memory_index(sorted((root / "workSummaries").glob("*.md")), project_name="P",
                               framework="claude", root=root)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "4096")
    artifacts._write_memory_index(manifest, agents)
    parts = _parts(index_path.parent)
    assert len(parts) >= 3 and not st.is_legacy_layout(index_path)
    sharded = artifacts._read_memory_index(agents)
    assert sharded["N"] == index["N"] and sharded["postings"] == index["postings"]

    # Shrinking the corpus below the cap returns to one file and removes every stale part.
    for p in sorted((root / "workSummaries").glob("*.md"))[2:]:
        p.unlink()
    monkeypatch.delenv(st.MAX_SHARD_BYTES_ENV)
    artifacts._write_memory_index(manifest, agents)
    assert _parts(index_path.parent) == []
    assert json.loads(index_path.read_text())["artifact_type"] == "memory-index"
    assert artifacts._read_memory_index(agents)["N"] == 2


def test_stale_parts_removed_when_shard_count_drops(tmp_path, monkeypatch):
    root, agents, manifest = _project(tmp_path)
    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "4096")
    artifacts._write_memory_index(manifest, agents)
    before = len(_parts(agents / "references"))
    unrelated = agents / "references" / "memory-index.part-notes.json"
    unrelated.write_text("{}")
    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "16384")
    artifacts._write_memory_index(manifest, agents)
    after = _parts(agents / "references")
    assert 0 < len(after) < before
    assert [p.name for p in after] == [st.part_file_name(i) for i in range(1, len(after) + 1)]
    assert unrelated.exists()  # only exact shard names are ever removed


def test_sharded_read_hits_validation_cache(tmp_path, monkeypatch):
    _, agents, manifest = _project(tmp_path)
    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "4096")
    artifacts._write_memory_index(manifest, agents)  # write records the sidecar
    import agentteams.cli.schema_cache as sc

    calls = {"n": 0}
    real = sc._validate_against_schema

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(sc, "_validate_against_schema", counting)
    artifacts._read_memory_index(agents)
    assert calls["n"] == 0
    part = _parts(agents / "references")[0]
    part.write_bytes(part.read_bytes() + b" ")
    with pytest.raises(MemoryIndexError):
        artifacts._read_memory_index(agents)


def test_incremental_sed_path_only_runs_on_legacy_layout(tmp_path, monkeypatch):
    _, agents, manifest = _project(tmp_path, n_docs=3)
    artifacts._write_memory_index(manifest, agents)
    assert not st.is_legacy_layout(agents / INDEX_REL)
    import agentteams.memory_index_incremental as inc

    def boom(**_kwargs):
        raise AssertionError("sed path must not run on the compact layout")

    monkeypatch.setattr(inc, "try_incremental_sed_update", boom)
    monkeypatch.setenv("AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED", "1")
    artifacts._write_memory_index(manifest, agents)


# --- scanner, fleet, recall ------------------------------------------------------------


def test_scanner_exempts_only_listed_unmodified_shards(tmp_path, monkeypatch):
    """Rule 12: the shard exemption must not hand out 9999 exempt names. Only a part the
    sibling manifest lists, with its current sha256, skips the path/entropy detectors."""
    from agentteams.scan import _is_operational_json

    _, agents, manifest = _project(tmp_path)
    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "4096")
    artifacts._write_memory_index(manifest, agents)
    refs = agents / "references"
    listed = _parts(refs)
    assert listed and all(_is_operational_json(p, agents) for p in listed)

    planted = refs / st.part_file_name(9000)  # matches the pattern, not in the manifest
    planted.write_text("{}")
    assert not _is_operational_json(planted, agents)
    listed[0].write_bytes(listed[0].read_bytes() + b"\n")  # edited after the write
    assert not _is_operational_json(listed[0], agents)
    assert not _is_operational_json(refs / "memory-index.part-1.json", agents)
    elsewhere = agents / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "memory-index.json").write_bytes((refs / "memory-index.json").read_bytes())
    (elsewhere / listed[1].name).write_bytes(listed[1].read_bytes())
    assert not _is_operational_json(elsewhere / listed[1].name, agents)


def test_scanner_and_store_agree_on_part_names():
    from agentteams.scan_operational import _MEMORY_INDEX_PART_RE

    for name in ("memory-index.part-0001.json", "memory-index.part-9999.json",
                 "memory-index.part-1.json", "memory-index.part-٠٠٠١.json",
                 "memory-index.part-0001.json.orig", "xmemory-index.part-0001.json"):
        assert bool(_MEMORY_INDEX_PART_RE.fullmatch(name)) == st.is_part_file_name(name), name


def test_incremental_sed_skip_is_announced(tmp_path, monkeypatch, capsys):
    _, agents, manifest = _project(tmp_path, n_docs=3)
    artifacts._write_memory_index(manifest, agents)
    monkeypatch.setenv("AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED", "1")
    artifacts._write_memory_index(manifest, agents)
    assert "Incremental memory-index update skipped (compact/sharded layout)" in capsys.readouterr().out


def test_fleet_volatile_flag_already_covers_a_sharded_index():
    """Fleet needs no shard rule: every sharded write rewrites memory-index.json too, and that
    file's volatile-tracked REVIEW flag already surfaces the change; non-.md files get no
    user-editable audit either way."""
    from agentteams.fleet import _VOLATILE_SUFFIXES

    assert "references/memory-index.json" in _VOLATILE_SUFFIXES


def test_recall_mcp_reads_sharded_index_and_confines_parts(tmp_path, monkeypatch):
    from agentteams.mcp_servers import recall
    from agentteams.mcp_servers._stdio import ToolError

    root, agents, manifest = _project(tmp_path)
    monkeypatch.setenv(st.MAX_SHARD_BYTES_ENV, "4096")
    artifacts._write_memory_index(manifest, agents)
    out = recall.call("query_index", {"query": "deploy retry", "k": 3}, root)
    assert out["hits"]

    part = _parts(agents / "references")[0]
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_bytes(part.read_bytes())
    part.unlink()
    part.symlink_to(outside)
    with pytest.raises(ToolError, match="outside"):
        recall.call("query_index", {"query": "deploy", "k": 3}, root)
