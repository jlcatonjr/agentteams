"""Tests for the Inspect AI adapter (Cluster A Phase 2, increment 2).

Structural / codegen checks only — Inspect AI is not a test dependency and is
not installed in .venv-ci. The adapter is a code generator: it must import
and run without inspect_ai, and emit syntactically valid Inspect AI source.
"""

from __future__ import annotations

import ast
import json

import pytest

from agentteams.eval_suite import FRAMEWORK_COUPLED_TOKENS, build_eval_suite
from agentteams.eval_adapters import inspect_ai as adapter


def _manifest() -> dict:
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


def test_adapter_module_imports_without_inspect_ai():
    """The adapter is codegen; importing it must not require inspect_ai
    (it is not installed in .venv-ci). Importing already happened at module
    top; assert inspect_ai did not get pulled in transitively."""
    import sys
    assert "inspect_ai" not in sys.modules, (
        "adapter import must not load inspect_ai (framework lives in generated text only)"
    )


def test_rendered_module_is_valid_python():
    src = adapter.render_inspect_ai_module(build_eval_suite(_manifest()))
    ast.parse(src)  # raises SyntaxError on failure


def test_one_task_per_scenario_with_unique_idents():
    suite = build_eval_suite(_manifest())
    src = adapter.render_inspect_ai_module(suite)
    tree = ast.parse(src)
    task_fns = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and any(
            (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "task")
            for d in n.decorator_list
        )
    ]
    assert len(task_fns) == len(suite["scenarios"]) >= 4
    idents = [n.name for n in task_fns]
    assert len(idents) == len(set(idents)), f"duplicate task idents: {idents}"


def test_generated_module_handles_all_four_predicate_kinds():
    src = adapter.render_inspect_ai_module(build_eval_suite(_manifest()))
    for kind in (
        "frontmatter-list-contains-all",
        "agent-count",
        "handoff-chain",
        "frontmatter-and-body",
    ):
        assert kind in src, f"generated _check does not handle {kind!r}"


def test_adapter_does_not_mutate_or_decouple_the_neutral_suite():
    suite = build_eval_suite(_manifest())
    before = json.dumps(suite, sort_keys=True)
    adapter.render_inspect_ai_module(suite)
    after = json.dumps(suite, sort_keys=True)
    assert before == after, "adapter mutated the neutral suite"
    # The adapter's INPUT must stay framework-neutral.
    for tok in FRAMEWORK_COUPLED_TOKENS:
        assert tok not in before, f"neutral suite leaked framework token {tok!r}"


def test_write_inspect_ai_module_round_trips(tmp_path):
    suite = build_eval_suite(_manifest())
    out = adapter.write_inspect_ai_module(suite, tmp_path / "gen" / "team_eval.py")
    assert out.exists()
    ast.parse(out.read_text(encoding="utf-8"))


def test_empty_suite_renders_valid_module():
    src = adapter.render_inspect_ai_module(
        build_eval_suite({"project_name": "P", "framework": "claude"})
    )
    tree = ast.parse(src)
    # preamble defines the scorer/solver even with zero scenarios
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"structural_scorer", "noop_solver", "_check", "_frontmatter"} <= names
