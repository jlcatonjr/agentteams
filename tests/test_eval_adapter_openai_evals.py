"""Tests for the OpenAI Evals adapter (Cluster A Phase 2, increment 3).

Structural / codegen checks only — openai/evals is not a test dependency. The
adapter must import and run without it and emit a well-formed Evals-shaped
definition that preserves the neutral suite's predicates.
"""

from __future__ import annotations

import json
import sys

from agentteams.eval_suite import FRAMEWORK_COUPLED_TOKENS, build_eval_suite
from agentteams.eval_adapters import openai_evals as adapter


def _manifest() -> dict:
    return {
        "project_name": "Data Pipeline",
        "framework": "copilot-vscode",
        "workstream_expert_slugs": ["ingest-expert", "transform-expert", "load-expert"],
        "components": [
            {"slug": "ingest", "cross_refs": []},
            {"slug": "transform", "cross_refs": ["ingest"]},
            {"slug": "load", "cross_refs": ["transform"]},
        ],
    }


def test_adapter_imports_without_openai_or_evals():
    assert "openai" not in sys.modules
    assert "evals" not in sys.modules


def test_definition_is_valid_json_and_round_trips():
    suite = build_eval_suite(_manifest())
    text = adapter.render_openai_evals_definition(suite)
    parsed = json.loads(text)
    assert parsed == adapter.build_openai_evals_definition(suite)


def test_definition_has_evals_registry_shape():
    d = adapter.build_openai_evals_definition(build_eval_suite(_manifest()))
    eval_name = d["eval"]
    assert eval_name == "data-pipeline-team-behavior"          # slugified, lowercased
    meta = d[eval_name]
    eval_id = meta["id"]
    assert meta["metrics"] == ["accuracy"]
    spec = d[eval_id]
    assert spec["class"] == adapter.STRUCTURAL_GRADER_CLASS
    assert spec["args"]["team_dir_env"] == "AGENTTEAMS_TEAM_DIR"


def test_one_sample_per_scenario_with_predicate_preserved():
    suite = build_eval_suite(_manifest())
    d = adapter.build_openai_evals_definition(suite)
    samples = d[d[d["eval"]]["id"]]["args"]["samples"]
    assert len(samples) == len(suite["scenarios"]) >= 4
    by_id = {s["scenario_id"]: s for s in samples}
    for sc in suite["scenarios"]:
        assert by_id[sc["id"]]["predicate"] == sc["predicate"]
        assert by_id[sc["id"]]["ideal"] == "True"


def test_adapter_does_not_mutate_or_decouple_neutral_suite():
    suite = build_eval_suite(_manifest())
    before = json.dumps(suite, sort_keys=True)
    adapter.build_openai_evals_definition(suite)
    assert json.dumps(suite, sort_keys=True) == before
    for tok in FRAMEWORK_COUPLED_TOKENS:
        assert tok not in before


def test_empty_suite_is_valid():
    d = adapter.build_openai_evals_definition(
        build_eval_suite({"project_name": "P", "framework": "claude"})
    )
    samples = d[d[d["eval"]]["id"]]["args"]["samples"]
    assert samples == []


def test_write_round_trips(tmp_path):
    suite = build_eval_suite(_manifest())
    out = adapter.write_openai_evals_definition(suite, tmp_path / "gen" / "team.evals.json")
    assert out.exists()
    json.loads(out.read_text(encoding="utf-8"))
