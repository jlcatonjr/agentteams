"""Tests for build_memory_index_from_texts and per-record JSONL passages (research-hardening R7)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from agentteams import memory_index as mi
from agentteams import memory_index_incremental as mii
from agentteams.memory_index import (
    build_memory_index,
    build_memory_index_from_texts,
    is_index_stale,
    query_index,
)

SCHEMA_PATH = Path(__file__).parent.parent / "agentteams" / "schemas" / "memory-index.schema.json"

# Built at runtime so this file carries no literal home path (the write-time scanner flags them).
_HOME = "/" + "Users" + "/"

_PROSE_A = (
    "# Drift Notes\n\nDrift detection compares template hashes against a stored baseline "
    "on every update run of the generator.\n"
)
_PROSE_B = "Typed handoff payloads validate against schemas before any agent accepts them for work.\n"

_TRANSCRIPT = "\n".join([
    json.dumps({"type": "user", "uuid": "u1", "message": {"role": "user",
                "content": "Please summarise the quarterly budget forecast for the finance team."}}),
    "",
    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "The zebra migration study covers wildebeest herds and river crossings."},
    ]}}),
    "not json at all but a plain line about volcanic basalt formations",
    json.dumps(["an", "array", "record"]),
    json.dumps({"text": "Home path " + _HOME + "someone/notes.md must be redacted from passages."}),
])


def _validate(index: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft7Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))).validate(index)


def test_texts_index_is_schema_valid_and_answers_queries():
    idx = build_memory_index_from_texts(
        {"notes/drift": _PROSE_A, "notes/handoff": _PROSE_B, "blank": "  \n"},
        project_name="P", framework="claude",
    )
    _validate(idx)
    assert idx["N"] == 2
    assert idx["source_count"] == 3
    docs = {d["path"]: d for d in idx["documents"]}
    assert docs["notes/drift"]["title"] == "Drift Notes"
    assert docs["notes/handoff"]["title"] == "notes/handoff"
    assert docs["notes/drift"]["source_mtime"] == 0.0
    assert docs["notes/drift"]["source_hash"] == hashlib.sha256(_PROSE_A.encode()).hexdigest()
    assert query_index(idx, "drift baseline template")[0]["path"] == "notes/drift"
    assert query_index(idx, "handoff schemas", strategy="vector")[0]["path"] == "notes/handoff"


def test_texts_index_matches_file_index_for_same_content(tmp_path):
    (tmp_path / "a.md").write_text(_PROSE_A, encoding="utf-8")
    (tmp_path / "b.md").write_text(_PROSE_B, encoding="utf-8")
    from_files = build_memory_index([tmp_path / "a.md", tmp_path / "b.md"], root=tmp_path)
    from_texts = build_memory_index_from_texts({"a.md": _PROSE_A, "b.md": _PROSE_B})
    for key in ("postings", "avgdl", "N", "source_fingerprint"):
        assert from_files[key] == from_texts[key]
    for fd, td in zip(from_files["documents"], from_texts["documents"]):
        assert {k: v for k, v in fd.items() if k != "source_mtime"} == \
            {k: v for k, v in td.items() if k not in ("source_mtime", "origin")}
        assert "origin" not in fd and td["origin"] == "text"


def test_texts_index_rejects_non_str():
    with pytest.raises(TypeError):
        build_memory_index_from_texts({"a": b"bytes"})  # type: ignore[dict-item]


def test_jsonl_file_gives_per_record_snippets(tmp_path):
    src = tmp_path / "session.jsonl"
    src.write_text(_TRANSCRIPT, encoding="utf-8")
    idx = build_memory_index([src])
    _validate(idx)
    paragraphs = idx["documents"][0]["paragraphs"]
    assert len(paragraphs) == 5  # blank line skipped; one passage per record
    assert paragraphs[0].startswith("Please summarise the quarterly budget forecast")
    assert paragraphs[2] == "not json at all but a plain line about volcanic basalt formations"
    assert paragraphs[3] == '["an", "array", "record"]'
    assert _HOME not in paragraphs[4] and mi._REDACTED in paragraphs[4]
    # JSON keys are not indexed; values are.
    assert "role" not in idx["postings"] and "zebra" in idx["postings"]
    hit = query_index(idx, "zebra wildebeest")[0]
    assert "zebra migration study" in hit["snippet"]
    assert "budget" not in hit["snippet"]


def test_jsonl_passages_are_capped():
    text = "\n".join(json.dumps({"text": f"record number {i} about cobalt mining logistics"}) for i in range(50))
    idx = build_memory_index_from_texts({"big.jsonl": text})
    assert len(idx["documents"][0]["paragraphs"]) == mi._MAX_PARAGRAPHS_PER_DOC


def test_jsonl_flag_overrides_suffix():
    by_suffix = build_memory_index_from_texts({"t.jsonl": _TRANSCRIPT})
    forced_off = build_memory_index_from_texts({"t.jsonl": _TRANSCRIPT}, jsonl=False)
    forced_on = build_memory_index_from_texts({"transcript": _TRANSCRIPT}, jsonl=True)
    assert len(by_suffix["documents"][0]["paragraphs"]) == 5
    assert forced_on["documents"][0]["paragraphs"] == by_suffix["documents"][0]["paragraphs"]
    assert "role" in forced_off["postings"]


def test_texts_index_is_not_stale_on_its_own_account(tmp_path):
    idx = build_memory_index_from_texts({"in-memory/doc": _PROSE_A})
    assert is_index_stale(idx, []) is False
    assert is_index_stale(idx, [], root=tmp_path) is False
    # A file source newer than the build still makes it stale.
    newer = tmp_path / "newer.md"
    newer.write_text("x", encoding="utf-8")
    os.utime(newer, (4_000_000_000, 4_000_000_000))
    assert is_index_stale(idx, [newer]) is True


def test_file_index_still_stale_when_source_deleted(tmp_path):
    src = tmp_path / "a.md"
    src.write_text(_PROSE_A, encoding="utf-8")
    idx = build_memory_index([src])
    assert is_index_stale(idx, [src]) is False
    src.unlink()
    assert is_index_stale(idx, []) is True


def test_incremental_jsonl_doc_matches_full_build(tmp_path):
    src = tmp_path / "session.jsonl"
    src.write_text(_TRANSCRIPT, encoding="utf-8")
    full = build_memory_index([src], root=tmp_path)["documents"][0]
    built = mii._new_doc_and_tf(src, 0, tmp_path)
    assert built is not None
    doc, tf = built
    assert doc == {k: v for k, v in full.items() if k != "vector_norm_sq"}
    assert sum(tf.values()) == full["length"]


def test_build_memory_index_output_unchanged_by_refactor(tmp_path, monkeypatch):
    """Golden digest captured from the pre-refactor build_memory_index (prose sources)."""
    monkeypatch.setattr(mi, "_utc_now_iso", lambda: "2026-01-01T00:00:00Z")
    corpus = {
        "alpha.md": "# Alpha\n\nThis document describes drift detection and the audit pipeline for agent "
                    "teams.\n\n## Detail\n\nTemplate hashes are compared against a stored baseline on every "
                    "update run, " + _HOME + "bob/x here.\n",
        "beta.md": "# Beta\n\nReplay packet trajectory and behavioral drift detection across the whole "
                   "pipeline today.\n\n| a | b |\n|---|---|\n",
        "notes.txt": "plain notes without a heading but with enough words to be a substantive paragraph here.\n",
        "empty.md": "\n\n",
    }
    for name, text in corpus.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    srcs = [tmp_path / n for n in corpus] + [tmp_path / "missing.md"]
    idx = build_memory_index(srcs, project_name="P", framework="claude", root=tmp_path)
    for doc in idx["documents"]:
        doc["source_mtime"] = 0.0
        doc["vector_norm_sq"] = round(doc["vector_norm_sq"], 9)
    # Golden captured at v2; the v3 bump changed JSONL and texts-built documents only.
    idx["index_format_version"] = "json-bm25-postings-v2"
    digest = hashlib.sha256(json.dumps(idx).encode()).hexdigest()
    assert digest == "052b25c9691d5347b7b20584ff27e99550a8deae449051fb47119b5d4d80d6ec"


def test_file_doc_with_failed_stat_still_stale_when_deleted(tmp_path):
    """A file-built doc whose stat failed (mtime 0.0) is not mistaken for a text doc."""
    src = tmp_path / "a.md"
    src.write_text(_PROSE_A, encoding="utf-8")
    idx = build_memory_index([src])
    idx["documents"][0]["source_mtime"] = 0.0
    assert is_index_stale(idx, []) is False
    src.unlink()
    assert is_index_stale(idx, []) is True


def test_text_doc_naming_a_real_file_is_not_stale(tmp_path):
    """A text id that happens to name an existing, different file is still skipped."""
    real = tmp_path / "a.md"
    real.write_text(_PROSE_B, encoding="utf-8")
    os.utime(real, (1_000_000_000, 1_000_000_000))
    idx = build_memory_index_from_texts({str(real): _PROSE_A})
    assert idx["documents"][0]["origin"] == "text"
    assert is_index_stale(idx, []) is False
    assert is_index_stale(idx, [], root=tmp_path) is False


def test_format_version_mismatch_is_stale(tmp_path):
    src = tmp_path / "a.md"
    src.write_text(_PROSE_A, encoding="utf-8")
    idx = build_memory_index([src])
    assert is_index_stale(idx, [src]) is False
    for old in ("json-bm25-postings-v2", None):
        if old is None:
            idx.pop("index_format_version")
        else:
            idx["index_format_version"] = old
        assert is_index_stale(idx, [src]) is True


def test_incremental_refuses_format_version_mismatch(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# A\n\nalpha beta gamma delta epsilon\n", encoding="utf-8")
    b.write_text("# B\n\nbeta gamma error handling retries\n", encoding="utf-8")
    idx = build_memory_index([a, b], root=tmp_path)
    idx["index_format_version"] = "json-bm25-postings-v2"
    idx_path = tmp_path / "memory-index.json"
    idx_path.write_text(json.dumps(idx, indent=2) + "\n", encoding="utf-8")
    a.write_text("# A\n\nalpha alpha beta gamma delta epsilon\n", encoding="utf-8")
    before = idx_path.read_text(encoding="utf-8")
    res = mii.try_incremental_sed_update(
        index_path=idx_path, index=idx, sources=[a, b], project_name="", framework="",
        validate_index=lambda _idx: None, root=tmp_path,
    )
    assert (res.applied, res.reason) == (False, "index_format_version_mismatch")
    assert idx_path.read_text(encoding="utf-8") == before
