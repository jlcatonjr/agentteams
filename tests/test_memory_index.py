"""Tests for F8 — additive lexical memory index + emission."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import build_team
from agentteams.memory_index import (
    MEMORY_INDEX_SCHEMA_VERSION,
    build_memory_index,
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
    assert idx["memory_index_schema_version"] == MEMORY_INDEX_SCHEMA_VERSION
    assert {d["title"] for d in idx["documents"]} == {"Alpha", "Beta"}


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
