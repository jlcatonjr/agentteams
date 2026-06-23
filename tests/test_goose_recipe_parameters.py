"""Phase-4a goose-native: opt-in Goose recipe `parameters`.

Covers the normalization (analyze._normalize_recipe_parameters), the opt-in
manifest passthrough (analyze.build_manifest), the YAML emission + validation
(frameworks.goose._emit_recipe / _validate_recipe_yaml), orchestrator-only
emission with the params<->{{ template }} coupling, the validator shape check,
and byte-identical additivity when no parameters are declared.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from agentteams.analyze import _normalize_recipe_parameters, build_manifest
from agentteams.frameworks.goose import (
    GooseAdapter,
    _emit_recipe,
    _validate_recipe_yaml,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SCHEMA_PATH = REPO_ROOT / "schemas" / "team-manifest.schema.json"

_ORCH_MD = """\
---
name: Orchestrator
description: Routes work
---

# Orchestrator

Routes all work.
"""


class TestNormalizeRecipeParameters:
    def test_absent_or_non_list_returns_empty(self):
        assert _normalize_recipe_parameters(None) == []
        assert _normalize_recipe_parameters("nope") == []
        assert _normalize_recipe_parameters({"key": "a"}) == []

    def test_drops_malformed_entries(self):
        raw = [
            {"key": "ok"},
            {"no_key": "x"},
            "not-a-dict",
            {"key": "   "},  # blank
            {"key": 123},    # non-string
        ]
        assert [p["key"] for p in _normalize_recipe_parameters(raw)] == ["ok"]

    def test_defaults_input_type_requirement_and_optional_default(self):
        out = _normalize_recipe_parameters([{"key": "a"}])
        assert out[0] == {
            "key": "a",
            "input_type": "string",
            "requirement": "optional",
            "default": "",  # goose: optional params must carry a default
        }

    def test_invalid_input_type_and_requirement_coerced(self):
        out = _normalize_recipe_parameters(
            [{"key": "a", "input_type": "bogus", "requirement": "weird"}]
        )
        assert out[0]["input_type"] == "string"
        assert out[0]["requirement"] == "optional"

    def test_required_param_has_no_default(self):
        out = _normalize_recipe_parameters([{"key": "a", "requirement": "required"}])
        assert "default" not in out[0]

    def test_optional_default_preserved_and_stringified(self):
        out = _normalize_recipe_parameters(
            [
                {"key": "s", "requirement": "optional", "default": "x"},
                {"key": "n", "input_type": "number", "requirement": "optional", "default": 5},
                {"key": "b", "input_type": "boolean", "requirement": "optional", "default": False},
            ]
        )
        by = {p["key"]: p for p in out}
        assert by["s"]["default"] == "x"
        assert by["n"]["default"] == "5"
        assert by["b"]["default"] == "false"  # bool lowercased for goose

    def test_file_param_coerced_required_with_no_default(self):
        out = _normalize_recipe_parameters(
            [{"key": "f", "input_type": "file", "requirement": "optional", "default": "x"}]
        )
        assert out[0]["requirement"] == "required"
        assert "default" not in out[0]

    def test_description_trimmed_and_optional(self):
        out = _normalize_recipe_parameters(
            [{"key": "a", "description": "  hi  "}, {"key": "b", "description": "   "}]
        )
        assert out[0]["description"] == "hi"
        assert "description" not in out[1]


class TestBuildManifestPassthrough:
    def test_added_when_declared(self):
        m = build_manifest(
            {"project_goal": "g", "recipe_parameters": [{"key": "a"}]}, framework="goose"
        )
        assert m["recipe_parameters"] == [
            {"key": "a", "input_type": "string", "requirement": "optional", "default": ""}
        ]

    def test_no_key_when_absent(self):
        m = build_manifest({"project_goal": "g"}, framework="goose")
        assert "recipe_parameters" not in m  # additivity: byte-identical manifest

    def test_no_key_when_all_malformed(self):
        m = build_manifest(
            {"project_goal": "g", "recipe_parameters": [{"no_key": 1}]}, framework="goose"
        )
        assert "recipe_parameters" not in m


class TestEmitRecipeParameters:
    def test_emit_block_validates(self):
        params = [
            {"key": "src", "input_type": "string", "requirement": "required", "description": "in"},
            {"key": "env", "input_type": "select", "requirement": "optional", "default": ""},
        ]
        y = _emit_recipe(
            title="T",
            description="D",
            instructions="body",
            extensions=["developer"],
            prompt="P {{ src }} {{ env }}",
            parameters=params,
        )
        assert "parameters:" in y
        assert '- key: "src"' in y
        assert 'requirement: "required"' in y
        assert _validate_recipe_yaml(y) == []

    def test_no_block_when_none(self):
        y = _emit_recipe(
            title="T", description="D", instructions="body", extensions=["developer"]
        )
        assert "parameters:" not in y

    def test_empty_list_emits_no_block(self):
        y = _emit_recipe(
            title="T",
            description="D",
            instructions="body",
            extensions=["developer"],
            parameters=[],
        )
        assert "parameters:" not in y

    def test_forbidden_tokens_in_description_do_not_false_trip(self):
        # A description containing envs:/context:/type: sse must not trip the
        # column-0-anchored forbidden-key guards (it is a quoted single-line scalar).
        params = [
            {
                "key": "k",
                "input_type": "string",
                "requirement": "optional",
                "default": "",
                "description": 'has envs: and context: and type: sse "quotes" too',
            }
        ]
        y = _emit_recipe(
            title="T",
            description="D",
            instructions="body",
            extensions=["developer"],
            prompt="P {{ k }}",
            parameters=params,
        )
        assert _validate_recipe_yaml(y) == []


class TestValidatorShapeCheck:
    def test_parameters_block_without_keys_is_flagged(self):
        bad = 'version: "1.0.0"\ntitle: "T"\ninstructions: |\n  body\nparameters:\nextensions:\n'
        violations = _validate_recipe_yaml(bad)
        assert any("parameters:" in v and "key:" in v for v in violations)


class TestOrchestratorEmission:
    @staticmethod
    def _manifest(**extra):
        base = {"project_name": "P", "output_files": [{"path": "alpha.agent.md"}]}
        base.update(extra)
        return base

    def test_orchestrator_gets_params_and_coupled_prompt_refs(self):
        adapter = GooseAdapter()
        manifest = self._manifest(
            recipe_parameters=[
                {"key": "src", "input_type": "string", "requirement": "required"},
                {"key": "env", "input_type": "select", "requirement": "optional", "default": ""},
            ]
        )
        recipe = adapter.render_agent_file(_ORCH_MD, "orchestrator", manifest)
        assert "parameters:" in recipe
        assert '- key: "src"' in recipe
        assert "Runtime inputs:" in recipe
        # Coupling: every {{ x }} template var has a matching declared key.
        template_vars = set(re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", recipe))
        assert template_vars == {"src", "env"}
        assert _validate_recipe_yaml(recipe) == []

    def test_non_orchestrator_recipe_has_no_params(self):
        adapter = GooseAdapter()
        manifest = self._manifest(
            recipe_parameters=[{"key": "src", "requirement": "required"}]
        )
        recipe = adapter.render_agent_file(_ORCH_MD, "alpha", manifest)
        assert "parameters:" not in recipe

    def test_orchestrator_without_params_is_unchanged(self):
        adapter = GooseAdapter()
        with_params = adapter.render_agent_file(
            _ORCH_MD, "orchestrator", self._manifest()
        )
        assert "parameters:" not in with_params
        assert "Runtime inputs:" not in with_params


class TestSchemaContract:
    def test_recipe_parameters_declared_and_validates(self):
        schema = json.loads(MANIFEST_SCHEMA_PATH.read_text())
        # Declared in the strict (additionalProperties:false) manifest schema, so the
        # new manifest key is contract-valid rather than rejected.
        assert "recipe_parameters" in schema["properties"]
        manifest = build_manifest(
            {
                "project_goal": "g",
                "recipe_parameters": [
                    {"key": "src", "requirement": "required", "description": "d"},
                    {"key": "env", "input_type": "select", "requirement": "optional", "default": "prod"},
                ],
            },
            framework="goose",
        )
        jsonschema.Draft7Validator(
            schema["properties"]["recipe_parameters"]
        ).validate(manifest["recipe_parameters"])
