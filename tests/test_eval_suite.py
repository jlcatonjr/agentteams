"""Tests for the framework-neutral eval-suite (Cluster A Phase 2, increment 1).

Pins:
- the schema is a valid Draft-07 schema;
- ``build_eval_suite`` is a pure manifest->dict that conforms to the schema;
- the framework-neutral contract (NO Inspect AI / OpenAI Evals DSL tokens);
- ``_write_eval_suite`` validates at runtime and writes nothing on failure;
- the emitted suite is excluded from drift artifacts (parity with the receipt).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import build_team
from agentteams.eval_suite import (
    FRAMEWORK_COUPLED_TOKENS,
    WORKER_GOVERNANCE_TRIAD,
    build_eval_suite,
)

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "eval-suite.schema.json"
EXAMPLES_DIR = REPO_ROOT / "examples"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _manifest() -> dict:
    """Minimal manifest with the keys build_eval_suite reads."""
    return {
        "project_name": "DataPipeline",
        "framework": "copilot-vscode",
        "workstream_expert_slugs": ["ingest-expert", "transform-expert", "load-expert"],
        "components": [
            {"slug": "ingest", "cross_refs": []},
            {"slug": "transform", "cross_refs": ["ingest"]},
            {"slug": "load", "cross_refs": ["transform"]},
        ],
    }


def test_schema_is_valid_draft7():
    import jsonschema
    jsonschema.Draft7Validator.check_schema(_schema())


def test_build_eval_suite_conforms_to_schema():
    import jsonschema
    suite = build_eval_suite(_manifest())
    jsonschema.Draft7Validator(_schema()).validate(suite)
    assert suite["artifact_type"] == "eval-suite"
    assert suite["generated_from"] == "manifest"
    cats = {s["category"] for s in suite["scenarios"]}
    assert {"routing", "handoff", "governance"} <= cats
    # handoff chain for 'load' must be ingest? no — transform -> load (load's
    # cross_ref is transform); chain is upstream-experts + the component expert.
    load_handoff = next(s for s in suite["scenarios"] if s["id"] == "handoff-load")
    assert load_handoff["predicate"]["chain"] == ["transform-expert", "load-expert"]
    assert load_handoff["predicate"]["returns_to"] == "orchestrator"
    gov = next(s for s in suite["scenarios"] if s["id"] == "governance-ingest-triad-and-return")
    assert gov["predicate"]["agents_contains_all"] == WORKER_GOVERNANCE_TRIAD


def test_build_eval_suite_no_components_is_still_valid():
    import jsonschema
    suite = build_eval_suite({"project_name": "P", "framework": "claude"})
    jsonschema.Draft7Validator(_schema()).validate(suite)
    assert suite["scenarios"] == []


def test_eval_suite_is_framework_neutral():
    """Phase 0 hard requirement: no Inspect AI / OpenAI Evals DSL tokens."""
    blob = json.dumps(build_eval_suite(_manifest()))
    for tok in FRAMEWORK_COUPLED_TOKENS:
        assert tok not in blob, f"framework-coupled token leaked: {tok!r}"


def test_write_eval_suite_rejects_nonconforming(tmp_path, monkeypatch):
    monkeypatch.setattr(
        build_team, "_write_eval_suite", build_team._write_eval_suite
    )
    # Force an invalid suite (artifact_type is a const in the schema).
    import agentteams.eval_suite as _es
    monkeypatch.setattr(_es, "build_eval_suite", lambda m: {"artifact_type": "WRONG"})

    with pytest.raises(build_team.EvalSuiteError, match="schema validation"):
        build_team._write_eval_suite({"project_name": "P"}, tmp_path)
    assert not (tmp_path / build_team.EVAL_SUITE_REL_PATH).exists()


def test_eval_suite_error_is_runtime_not_oserror():
    assert issubclass(build_team.EvalSuiteError, RuntimeError)
    assert not issubclass(build_team.EvalSuiteError, OSError)


def _seed_gates(output_dir: Path, monkeypatch):
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", key)
    w = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": "wf-es",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z", "max_uses": "9", "uses": "0",
        "approver": "t", "ticket_id": "ES", "reason_code": "T",
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


def test_generate_emits_eval_suite_increment_1b(tmp_path, monkeypatch):
    """F2 increment 1b: the eval suite is emitted on the GENERATE path too
    (increment 1 was --update-only), schema-valid and drift-excluded."""
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")
    output_dir = tmp_path / ".github" / "agents"
    _seed_gates(output_dir, monkeypatch)
    # Plain generate — NO --update.
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0

    suite_path = output_dir / build_team.EVAL_SUITE_REL_PATH
    assert suite_path.exists(), "eval suite not emitted on the generate path"
    import jsonschema
    suite = json.loads(suite_path.read_text())
    jsonschema.Draft7Validator(_schema()).validate(suite)
    assert suite["artifact_type"] == "eval-suite"
    assert suite["scenarios"], "expected non-empty scenarios for data-pipeline"

    # Drift-excluded by construction (parity with --update emission).
    log = json.loads((output_dir / "references" / "build-log.json").read_text())
    rel = build_team.EVAL_SUITE_REL_PATH
    assert rel not in log.get("template_hashes", {})
    assert rel not in log.get("file_hashes", {})
    omap = log.get("output_files_map", [])
    paths = {f.get("path") for f in omap} if omap and isinstance(omap[0], dict) else set(omap)
    assert rel not in paths


def test_update_emits_drift_excluded_eval_suite(tmp_path, monkeypatch):
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")
    output_dir = tmp_path / ".github" / "agents"
    _seed_gates(output_dir, monkeypatch)
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--yes", "--no-scan", "--security-offline",
    ]) == 0
    (output_dir / "references" / "security-decisions.log.csv").write_text(
        "timestamp,requesting_agent,action_reviewed,verdict,conditions,conditions_verified\n"
        "2026-05-03T00:00:00Z,t,overwrite,PASS,,verified\n", encoding="utf-8")
    assert build_team.main([
        "--description", str(brief), "--output", str(output_dir),
        "--update", "--yes", "--no-scan", "--security-offline",
    ]) == 0

    suite_path = output_dir / build_team.EVAL_SUITE_REL_PATH
    assert suite_path.exists(), "eval suite not emitted on --update"
    import jsonschema
    suite = json.loads(suite_path.read_text())
    jsonschema.Draft7Validator(_schema()).validate(suite)

    # Drift-excluded by construction (parity with the delivery receipt).
    log = json.loads((output_dir / "references" / "build-log.json").read_text())
    rel = build_team.EVAL_SUITE_REL_PATH
    assert rel not in log.get("template_hashes", {})
    assert rel not in log.get("file_hashes", {})
    omap = log.get("output_files_map", [])
    paths = {f.get("path") for f in omap} if omap and isinstance(omap[0], dict) else set(omap)
    assert rel not in paths
