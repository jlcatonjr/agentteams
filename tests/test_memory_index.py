"""Tests for F8 — additive lexical memory index + emission."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import pytest

import build_team
from agentteams.memory_index import (
    FALLBACK_POLICY,
    INDEX_FORMAT_VERSION,
    INDEX_WRITE_OWNER,
    MEMORY_INDEX_SCHEMA_VERSION,
    VECTOR_RUNTIME_MODE,
    build_memory_index,
    is_index_stale,
    query_index,
)

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "memory-index.schema.json"
EXAMPLES_DIR = REPO_ROOT / "examples"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# ------------------------------ pure unit ------------------------------

def test_build_memory_index_is_schema_valid(tmp_path):
    import jsonschema
    a = tmp_path / "alpha.md"
    a.write_text("# Alpha\n\nThis document describes drift detection and the audit pipeline.\n")
    b = tmp_path / "beta.md"
    b.write_text("# Beta\n\nReplay packet trajectory and behavioral drift detection.\n")
    idx = build_memory_index([a, b], project_name="P", framework="claude")
    jsonschema.Draft7Validator(_schema()).validate(idx)
    assert idx["N"] == 2
    assert "built_at" in idx
    assert idx["source_count"] == 2
    assert idx["memory_index_schema_version"] == MEMORY_INDEX_SCHEMA_VERSION
    assert idx["index_format_version"] == INDEX_FORMAT_VERSION
    assert idx["index_write_owner"] == INDEX_WRITE_OWNER
    assert idx["vector_runtime_mode"] == VECTOR_RUNTIME_MODE
    assert idx["fallback_policy"] == FALLBACK_POLICY
    assert idx["vector_model_id"] is None
    assert idx["vector_dim"] is None
    assert isinstance(idx["index_build_id"], str) and len(idx["index_build_id"]) == 64
    assert isinstance(idx["source_fingerprint"], str) and len(idx["source_fingerprint"]) == 64
    assert {d["title"] for d in idx["documents"]} == {"Alpha", "Beta"}
    assert all("source_mtime" in d for d in idx["documents"])
    assert all("source_hash" in d and len(d["source_hash"]) == 64 for d in idx["documents"])


def test_query_returns_ranked_documents(tmp_path):
    a = tmp_path / "drift.md"
    a.write_text("# Drift\n\nDrift detection compares template hashes to a baseline.\n" * 4)
    b = tmp_path / "handoff.md"
    b.write_text("# Handoff\n\nTyped handoff payloads validate against schemas.\n")
    idx = build_memory_index([a, b])
    hits = query_index(idx, "drift baseline template")
    assert hits, "BM25 returned no hits for terms present in the corpus"
    # Highest-scoring should be drift.md (drift terms repeat there).
    assert hits[0]["title"] == "Drift"
    # Stable, deterministic ordering: tie-break sorts by doc_id ascending.


def test_empty_source_list_yields_valid_empty_index():
    import jsonschema
    idx = build_memory_index([])
    jsonschema.Draft7Validator(_schema()).validate(idx)
    assert idx["N"] == 0
    assert idx["documents"] == []
    assert idx["postings"] == {}
    assert query_index(idx, "anything") == []


def test_missing_or_unreadable_sources_are_silently_skipped(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("# Real\nSome text content here.\n")
    idx = build_memory_index([real, tmp_path / "ghost.md"])
    assert idx["N"] == 1


def test_query_with_no_matching_terms_returns_empty(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("# X\n\nOne narrow topic.\n")
    idx = build_memory_index([p])
    assert query_index(idx, "completely unrelated cryptographic terms") == []


def test_is_index_stale_detects_newer_source(tmp_path):
    src = tmp_path / "daily.md"
    src.write_text("# Daily\n\nFirst entry.\n")
    idx = build_memory_index([src])
    assert not is_index_stale(idx, [src])

    # Force source mtime newer than build timestamp.
    time.sleep(1)
    now = time.time()
    os.utime(src, (now, now))
    assert is_index_stale(idx, [src])


def test_is_index_stale_detects_hash_mismatch(tmp_path):
    src = tmp_path / "daily.md"
    src.write_text("# Daily\n\nFirst entry.\n")
    idx = build_memory_index([src])
    assert not is_index_stale(idx, [src])

    # Simulate stale index metadata against unchanged timestamps.
    idx["documents"][0]["source_hash"] = "0" * 64
    assert is_index_stale(idx, [src])


# ----------------------------- writer -----------------------------

def test_write_memory_index_rejects_nonconforming(tmp_path, monkeypatch):
    import agentteams.memory_index as mi
    monkeypatch.setattr(mi, "build_memory_index",
                        lambda sources, **kw: {"artifact_type": "WRONG"})
    with pytest.raises(build_team.MemoryIndexError, match="schema validation"):
        build_team._write_memory_index({"project_name": "P"}, tmp_path)
    assert not (tmp_path / build_team.MEMORY_INDEX_REL_PATH).exists()


def test_memory_index_error_is_runtime_not_oserror():
    assert issubclass(build_team.MemoryIndexError, RuntimeError)
    assert not issubclass(build_team.MemoryIndexError, OSError)


# ---------------------- integration: additive + drift-excluded ----------------------

def _seed_gates(output_dir: Path, monkeypatch):
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", key)
    w = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": "wf-mi",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z", "max_uses": "9", "uses": "0",
        "approver": "t", "ticket_id": "MI", "reason_code": "T",
        "conditions_verified": "verified", "signature": "",
    }
    payload = "|".join(w[k] for k in [
        "waiver_id", "action_reviewed", "expires_at", "max_uses", "uses",
        "approver", "ticket_id", "reason_code", "conditions_verified"])
    w["signature"] = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        + ",".join(w[k] for k in [
            "timestamp", "waiver_id", "action_reviewed", "expires_at",
            "max_uses", "uses", "approver", "ticket_id", "reason_code",
            "conditions_verified", "signature"]) + "\n",
        encoding="utf-8",
    )


def test_generate_emits_drift_excluded_memory_index_additive(tmp_path, monkeypatch):
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")

    project_root = tmp_path
    output_dir = project_root / ".github" / "agents"
    _seed_gates(output_dir, monkeypatch)

    # Provide a durable work-summary corpus the index should pick up.
    ws = project_root / "workSummaries"
    ws.mkdir()
    (ws / "2026-05-19.md").write_text(
        "# Daily Summary\n\nDrift, handoff, eval-suite and memory-index work today.\n"
    )
    (project_root / "CHANGELOG.md").write_text("# Changelog\n\n- entry\n")

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0

    idx_path = output_dir / build_team.MEMORY_INDEX_REL_PATH
    assert idx_path.exists(), "memory index not emitted on generate path"
    import jsonschema
    idx = json.loads(idx_path.read_text())
    jsonschema.Draft7Validator(_schema()).validate(idx)
    assert idx["N"] >= 1, "index should contain at least the work-summary entry"
    assert idx["artifact_type"] == "memory-index"

    # Additive contract: the original work-summary docs are unchanged.
    assert (ws / "2026-05-19.md").read_text().startswith("# Daily Summary"), (
        "original work-summary document was modified by index emission"
    )

    # Drift-excluded by construction.
    log = json.loads((output_dir / "references" / "build-log.json").read_text())
    rel = build_team.MEMORY_INDEX_REL_PATH
    assert rel not in log.get("template_hashes", {})
    assert rel not in log.get("file_hashes", {})
    omap = log.get("output_files_map", [])
    paths = {f.get("path") for f in omap} if omap and isinstance(omap[0], dict) else set(omap)
    assert rel not in paths


def test_update_path_reemits_memory_index(tmp_path, monkeypatch):
    """F-7 (audit of F8 triggers): the --update emission sites are wired
    identically to the generate site but were untested. This pins re-emission
    after --update so a regression at either --update wiring is caught."""
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")

    project_root = tmp_path
    output_dir = project_root / ".github" / "agents"
    _seed_gates(output_dir, monkeypatch)

    ws = project_root / "workSummaries"
    ws.mkdir()
    (ws / "2026-05-19.md").write_text("# Daily\n\nFirst summary.\n")

    # Initial generate.
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0
    idx_path = output_dir / build_team.MEMORY_INDEX_REL_PATH
    assert idx_path.exists()
    initial_N = json.loads(idx_path.read_text())["N"]

    # Seed the destructive-gate PASS and add a new summary, then --update.
    (output_dir / "references" / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,t,overwrite,PASS,,verified\n",
        encoding="utf-8",
    )
    (ws / "2026-05-20.md").write_text("# Daily\n\nSecond summary, distinct content.\n")

    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ]) == 0

    assert idx_path.exists(), "memory-index must persist after --update"
    after = json.loads(idx_path.read_text())
    assert after["N"] == initial_N + 1, (
        "--update did not pick up the newly-added work summary "
        f"(N: {initial_N} -> {after['N']})"
    )


def test_existing_project_path_overrides_output_dir_inference(tmp_path, monkeypatch):
    """F-2 (audit of F8 triggers): _memory_index_sources must prefer
    manifest['existing_project_path'] over output_dir.parent.parent. A
    non-standard --output combined with a description-supplied project path
    must still find the operator's work summaries."""
    real_root = tmp_path / "real-project"
    (real_root / "workSummaries").mkdir(parents=True)
    (real_root / "workSummaries" / "alpha.md").write_text(
        "# Alpha\n\nReal-project decision history.\n"
    )

    # output_dir whose parent.parent is unrelated to real_root.
    output_dir = tmp_path / "elsewhere" / "agents-only"
    output_dir.mkdir(parents=True)

    manifest_without = {"project_name": "P", "framework": "claude"}
    manifest_with = {"project_name": "P", "framework": "claude",
                     "existing_project_path": str(real_root)}

    no_epp = build_team._memory_index_sources(manifest_without, output_dir)
    with_epp = build_team._memory_index_sources(manifest_with, output_dir)

    assert (real_root / "workSummaries" / "alpha.md") not in no_epp, (
        "control: inference from output_dir.parent.parent should NOT find real_root"
    )
    assert (real_root / "workSummaries" / "alpha.md") in with_epp, (
        "existing_project_path was not honored — F-2 trigger fix not applied"
    )


def test_memory_index_sources_include_docs_src_references_and_plan(tmp_path):
    root = tmp_path / "project"
    (root / "workSummaries").mkdir(parents=True)
    (root / "workSummaries" / "daily.md").write_text("# Daily\n\nSummary.\n")
    (root / "docs_src").mkdir()
    (root / "docs_src" / "guide.md").write_text("# Guide\n\nHow to update.\n")
    (root / "references").mkdir()
    (root / "references" / "policy.md").write_text("# Policy\n\nReference text.\n")
    (root / "build-team-plan.md").write_text("# Plan\n\nArchitecture notes.\n")
    output_dir = root / ".github" / "agents"
    output_dir.mkdir(parents=True)

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(root),
    }
    sources = build_team._memory_index_sources(manifest, output_dir)
    source_set = {str(p) for p in sources}

    assert str(root / "workSummaries" / "daily.md") in source_set
    assert str(root / "docs_src" / "guide.md") in source_set
    assert str(root / "references" / "policy.md") in source_set
    assert str(root / "build-team-plan.md") in source_set


def test_query_index_cli_mode_returns_results(tmp_path):
    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)

    src = tmp_path / "daily.md"
    src.write_text("# Daily\n\nSecurity gate overwrite guidance was updated.\n")
    idx = build_memory_index([src], project_name="P", framework="copilot-vscode")
    (index_dir / "memory-index.json").write_text(json.dumps(idx), encoding="utf-8")

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(tmp_path),
    }
    rc = build_team._run_query_index(manifest, output_dir, "security overwrite guidance", 3)
    assert rc == 0


def test_query_index_cli_mode_returns_1_when_no_hits(tmp_path):
    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)

    src = tmp_path / "daily.md"
    src.write_text("# Daily\n\nOne narrow topic only.\n")
    idx = build_memory_index([src], project_name="P", framework="copilot-vscode")
    (index_dir / "memory-index.json").write_text(json.dumps(idx), encoding="utf-8")

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(tmp_path),
    }
    rc = build_team._run_query_index(manifest, output_dir, "unrelated cryptographic terms", 3)
    assert rc == 1


def test_query_index_cli_mode_auto_refreshes_when_stale(tmp_path):
    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)

    ws = tmp_path / "workSummaries"
    ws.mkdir()
    src = ws / "day.md"
    src.write_text("# Daily\n\nOriginal summary text only.\n")

    # Seed an index that does not contain the future query term.
    idx = build_memory_index([src], project_name="P", framework="copilot-vscode")
    (index_dir / "memory-index.json").write_text(json.dumps(idx), encoding="utf-8")

    # Modify source after index write so the query path sees a stale artifact.
    src.write_text("# Daily\n\nUpdated content now includes autorefresh-token.\n")

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(tmp_path),
    }
    rc = build_team._run_query_index(manifest, output_dir, "autorefresh-token", 3)
    assert rc == 0

    refreshed = json.loads((index_dir / "memory-index.json").read_text(encoding="utf-8"))
    hits = query_index(refreshed, "autorefresh-token", k=3)
    assert hits, "expected persisted memory index to be refreshed before query serving"


def test_write_memory_index_incremental_sed_single_doc_success_without_full_rebuild(
    tmp_path, monkeypatch
):
    import agentteams.memory_index as mi

    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)

    ws = tmp_path / "workSummaries"
    ws.mkdir()
    src = ws / "day.md"
    src.write_text("# Daily\n\nalpha beta beta\n", encoding="utf-8")

    initial = build_memory_index([src], project_name="P", framework="copilot-vscode")
    idx_path = index_dir / "memory-index.json"
    idx_path.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")

    # Same vocabulary terms, different frequencies => eligible incremental patch.
    src.write_text("# Daily\n\nalpha alpha beta\n", encoding="utf-8")

    monkeypatch.setenv("AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED", "1")

    def _should_not_full_rebuild(*args, **kwargs):
        raise AssertionError("full rebuild should not be called for eligible incremental update")

    monkeypatch.setattr(mi, "build_memory_index", _should_not_full_rebuild)

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(tmp_path),
    }
    build_team._write_memory_index(manifest, output_dir)

    updated = json.loads(idx_path.read_text(encoding="utf-8"))
    hits = query_index(updated, "alpha", k=3)
    assert hits


def test_write_memory_index_incremental_sed_falls_back_on_term_set_change(
    tmp_path, monkeypatch
):
    import agentteams.memory_index as mi

    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)

    ws = tmp_path / "workSummaries"
    ws.mkdir()
    src = ws / "day.md"
    src.write_text("# Daily\n\nalpha beta beta\n", encoding="utf-8")

    initial = build_memory_index([src], project_name="P", framework="copilot-vscode")
    idx_path = index_dir / "memory-index.json"
    idx_path.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")

    # Introduce a new vocabulary term => incremental gate should fallback.
    src.write_text("# Daily\n\nalpha beta gamma\n", encoding="utf-8")

    monkeypatch.setenv("AGENTTEAMS_MEMORY_INDEX_INCREMENTAL_SED", "1")

    original_build = mi.build_memory_index
    calls = {"n": 0}

    def _counting_build(*args, **kwargs):
        calls["n"] += 1
        return original_build(*args, **kwargs)

    monkeypatch.setattr(mi, "build_memory_index", _counting_build)

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(tmp_path),
    }
    build_team._write_memory_index(manifest, output_dir)
    assert calls["n"] == 1, "expected fallback full rebuild when term set changes"

    updated = json.loads(idx_path.read_text(encoding="utf-8"))
    hits = query_index(updated, "gamma", k=3)
    assert hits


def test_read_memory_index_rejects_schema_invalid_shape(tmp_path):
    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)
    # Missing required keys such as "documents" and "postings".
    (index_dir / "memory-index.json").write_text('{"N": 1, "avgdl": 1.0}', encoding="utf-8")

    with pytest.raises(build_team.MemoryIndexError, match="schema validation"):
        build_team._read_memory_index(output_dir)


def test_run_query_index_raises_memoryindexerror_on_invalid_shape(tmp_path):
    output_dir = tmp_path / ".github" / "agents"
    index_dir = output_dir / "references"
    index_dir.mkdir(parents=True)
    (index_dir / "memory-index.json").write_text('{"N": 1, "avgdl": 1.0}', encoding="utf-8")

    manifest = {
        "project_name": "P",
        "framework": "copilot-vscode",
        "existing_project_path": str(tmp_path),
    }
    with pytest.raises(build_team.MemoryIndexError, match="schema validation"):
        build_team._run_query_index(manifest, output_dir, "drift detection", 3)


def test_main_refresh_index_writes_index_without_emit(tmp_path):
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")

    project_root = tmp_path
    output_dir = project_root / ".github" / "agents"
    (project_root / "workSummaries").mkdir()
    (project_root / "workSummaries" / "day.md").write_text(
        "# Daily\n\nRefresh index mode test content.\n"
    )
    (project_root / "README.md").write_text("# Readme\n\nProject details.\n")

    rc = build_team.main([
        "--description", str(brief),
        "--output", str(output_dir),
        "--refresh-index",
        "--yes",
        "--no-scan",
        "--security-offline",
    ])
    assert rc == 0
    assert (output_dir / "references" / "memory-index.json").exists()


def test_main_query_index_mode_reads_existing_index(tmp_path):
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")

    project_root = tmp_path
    output_dir = project_root / ".github" / "agents"
    (project_root / "workSummaries").mkdir()
    (project_root / "workSummaries" / "day.md").write_text(
        "# Daily\n\nTyped handoff payload validation details.\n"
    )
    (project_root / "README.md").write_text("# Readme\n\nProject details.\n")

    assert build_team.main([
        "--description", str(brief),
        "--output", str(output_dir),
        "--refresh-index",
        "--yes",
        "--no-scan",
        "--security-offline",
    ]) == 0

    rc = build_team.main([
        "--description", str(brief),
        "--output", str(output_dir),
        "--query-index", "typed handoff payload",
        "--query-k", "3",
        "--yes",
        "--no-scan",
        "--security-offline",
    ])
    assert rc == 0


def test_navigator_template_carries_nested_index_protocol_with_fallback():
    """F8 nested protocol (audit Correction 3): the navigator template tells
    the agent to query the memory index first, then open the referenced
    document, then filesystem search — AND to fall back gracefully when the
    index is absent or its snippets do not answer."""
    nav = (REPO_ROOT / "agentteams" / "templates" / "universal" / "navigator.template.md").read_text()
    # Reference the artifact path (the actual file the navigator queries).
    assert "memory-index.json" in nav
    # Fallback wording must be present (absent/stale-index guard).
    assert ("absent" in nav.lower() or "missing" in nav.lower() or
            "not exist" in nav.lower() or "if the index" in nav.lower()), (
        "navigator template must include absent/stale-index fallback wording"
    )
    # Open-the-document step must be present (the "nested" part).
    assert "open" in nav.lower() and "document" in nav.lower()


# ---------------------- I2: per-paragraph storage ----------------------

def test_build_memory_index_stores_paragraphs_per_document(tmp_path):
    """I2: each document entry must include a non-empty paragraphs list."""
    p = tmp_path / "rich.md"
    p.write_text(
        "# Rich Document\n\n"
        "First paragraph contains unique information about drift detection.\n\n"
        "Second paragraph describes audit pipeline and behavioral replay.\n\n"
        "Third paragraph covers template hash comparison for baseline drift.\n"
    )
    idx = build_memory_index([p])
    doc = idx["documents"][0]
    assert "paragraphs" in doc, "document entry missing 'paragraphs' field (I2)"
    assert isinstance(doc["paragraphs"], list)
    assert len(doc["paragraphs"]) >= 2, "expected at least 2 substantive paragraphs"
    # No paragraph should be empty.
    assert all(s.strip() for s in doc["paragraphs"])


def test_paragraphs_are_substantive_not_headings_or_badges(tmp_path):
    """I2: _extract_paragraphs must skip headings, badges, tables, boilerplate."""
    p = tmp_path / "mixed.md"
    p.write_text(
        "# Heading\n\n"
        "[![badge](https://img.shields.io/badge/test-passing-green)]\n\n"
        "| col1 | col2 |\n|------|------|\n| a    | b    |\n\n"
        "All notable changes to this project will be documented in this file.\n\n"
        "This paragraph has enough words to be substantive and carries real content "
        "about the enrichment pipeline and tool catalog.\n"
    )
    idx = build_memory_index([p])
    paras = idx["documents"][0]["paragraphs"]
    joined = " ".join(paras)
    assert "badge" not in joined, "badge line should not appear in paragraphs"
    assert "col1" not in joined, "table content should not appear in paragraphs"
    assert "All notable" not in joined, "CHANGELOG boilerplate should not appear in paragraphs"
    assert "enrichment" in joined, "substantive paragraph should be retained"


def test_paragraphs_capped_at_max(tmp_path):
    """I2: paragraphs are capped at _MAX_PARAGRAPHS_PER_DOC (20)."""
    from agentteams.memory_index import _MAX_PARAGRAPHS_PER_DOC

    paragraphs = "\n\n".join(
        f"Paragraph number {i} contains enough distinct substantive words to qualify."
        for i in range(30)
    )
    p = tmp_path / "long.md"
    p.write_text(f"# Long\n\n{paragraphs}\n")
    idx = build_memory_index([p])
    assert len(idx["documents"][0]["paragraphs"]) <= _MAX_PARAGRAPHS_PER_DOC


def test_schema_version_is_1_3():
    assert MEMORY_INDEX_SCHEMA_VERSION == "1.3"


def test_schema_requires_paragraphs_field(tmp_path):
    """Schema 1.2 must require the paragraphs field; validation fails if absent."""
    import jsonschema
    p = tmp_path / "a.md"
    p.write_text("# A\n\nContent here.\n")
    idx = build_memory_index([p])
    # Remove paragraphs to trigger schema violation.
    for doc in idx["documents"]:
        doc.pop("paragraphs", None)
    with pytest.raises(jsonschema.ValidationError):
        import jsonschema
        jsonschema.Draft7Validator(
            json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        ).validate(idx)


# ---------------------- I9: multi-snippet results ----------------------

def test_query_index_returns_snippets_list(tmp_path):
    """I9: every query result must include snippets: list[str] with ≥ 1 entry."""
    a = tmp_path / "a.md"
    a.write_text(
        "# Alpha\n\n"
        "Security audit pipeline covers drift detection baseline.\n\n"
        "Behavioral replay validates handoff payloads against schema.\n"
    )
    idx = build_memory_index([a])
    hits = query_index(idx, "security drift baseline")
    assert hits, "expected at least one result"
    for hit in hits:
        assert "snippets" in hit, "result missing 'snippets' field (I9)"
        assert isinstance(hit["snippets"], list)
        assert len(hit["snippets"]) >= 1
        # snippet (legacy alias) must equal snippets[0].
        assert hit["snippet"] == hit["snippets"][0]


def test_query_index_snippets_are_query_relevant(tmp_path):
    """I9: dynamic passage scoring returns the most query-relevant paragraph."""
    p = tmp_path / "mixed.md"
    p.write_text(
        "# Mixed Topics\n\n"
        "Introduction paragraph without the target terms we are looking for.\n\n"
        "This paragraph discusses enrichment tool catalog and pipeline registration "
        "in detail with many relevant terms for the enrichment query.\n\n"
        "Closing paragraph about unrelated housekeeping items only.\n"
    )
    idx = build_memory_index([p])
    hits = query_index(idx, "enrichment tool catalog pipeline")
    assert hits
    # The enrichment paragraph must be in snippets[0] — it's the most relevant.
    assert "enrichment" in hits[0]["snippets"][0].lower(), (
        "Dynamic passage scoring should surface the enrichment paragraph first"
    )


def test_query_index_snippets_multi_paragraph(tmp_path):
    """I9: when a document has multiple relevant paragraphs, snippets has up to 3."""
    p = tmp_path / "multi.md"
    p.write_text(
        "# Multi\n\n"
        "First relevant paragraph covers drift detection and memory index integration.\n\n"
        "Second relevant paragraph also covers drift detection behavioral baseline.\n\n"
        "Third relevant paragraph examines drift detection audit logs.\n\n"
        "Fourth paragraph is unrelated housekeeping content about file paths.\n"
    )
    idx = build_memory_index([p])
    hits = query_index(idx, "drift detection baseline")
    assert hits
    # We have 3 drift paragraphs; snippets should return up to 3 distinct entries.
    assert len(hits[0]["snippets"]) >= 1
    from agentteams.memory_index import _SNIPPETS_PER_HIT
    assert len(hits[0]["snippets"]) <= _SNIPPETS_PER_HIT


def test_query_index_backward_compat_with_11_index(tmp_path):
    """I2/I9 backward compat: a schema-1.1 index (no paragraphs field) returns
    results where snippet == snippets[0] == the stored static snippet."""
    p = tmp_path / "old.md"
    p.write_text("# Old\n\nLegacy content about drift detection pipeline.\n")
    idx = build_memory_index([p])
    # Simulate a 1.1 index by stripping paragraphs.
    for doc in idx["documents"]:
        doc.pop("paragraphs", None)
    idx["memory_index_schema_version"] = "1.1"

    hits = query_index(idx, "drift detection pipeline")
    assert hits, "backward compat: 1.1 index should still return results"
    hit = hits[0]
    assert "snippets" in hit
    assert len(hit["snippets"]) == 1
    assert hit["snippet"] == hit["snippets"][0]
    assert hit["snippet"]  # non-empty


def test_query_index_vector_strategy_returns_results(tmp_path):
    a = tmp_path / "drift.md"
    a.write_text("# Drift\n\nDrift detection compares template hashes to a baseline.\n" * 4)
    b = tmp_path / "handoff.md"
    b.write_text("# Handoff\n\nTyped handoff payloads validate against schemas.\n")

    idx = build_memory_index([a, b])
    hits = query_index(idx, "drift baseline template", strategy="vector")

    assert hits, "vector strategy returned no hits for present terms"
    assert hits[0]["title"] == "Drift"


def test_query_index_rejects_unknown_strategy(tmp_path):
    a = tmp_path / "alpha.md"
    a.write_text("# Alpha\n\nOne document with content.\n")
    idx = build_memory_index([a])

    with pytest.raises(ValueError, match="Unknown query strategy"):
        query_index(idx, "alpha content", strategy="unknown")
