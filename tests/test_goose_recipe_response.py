"""Phase-4b goose-native: opt-in Goose recipe `response` (structured output).

Covers normalization (analyze._normalize_recipe_response), opt-in manifest
passthrough, YAML emission of the json_schema as inline JSON + validation,
forbidden-guard non-collision for JSON property names, orchestrator-only emission,
the validator shape check, and byte-identical additivity when none is declared.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from agentteams.analyze import _normalize_recipe_response, build_manifest
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

_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string"}, "score": {"type": "number"}},
    "required": ["verdict"],
}


def _json_schema_from_recipe(recipe: str) -> dict:
    """Extract and parse the inline-JSON `json_schema:` value from a recipe."""
    m = re.search(r"^\s+json_schema:\s*(\{.*\})\s*$", recipe, re.MULTILINE)
    assert m, f"no json_schema line in:\n{recipe}"
    return json.loads(m.group(1))


class TestNormalizeRecipeResponse:
    def test_non_dict_or_empty_returns_none(self):
        assert _normalize_recipe_response(None) is None
        assert _normalize_recipe_response("nope") is None
        assert _normalize_recipe_response({}) is None
        assert _normalize_recipe_response([{"type": "object"}]) is None

    def test_dict_with_type_kept(self):
        assert _normalize_recipe_response(_SCHEMA) == _SCHEMA

    def test_dict_without_type_rejected(self):
        assert _normalize_recipe_response({"properties": {"a": {"type": "string"}}}) is None

    def test_non_string_or_empty_type_rejected(self):
        assert _normalize_recipe_response({"type": ""}) is None
        assert _normalize_recipe_response({"type": 123}) is None

    def test_double_wrap_unwrapped(self):
        wrapped = {"json_schema": _SCHEMA}
        assert _normalize_recipe_response(wrapped) == _SCHEMA


class TestBuildManifestPassthrough:
    def test_added_when_valid(self):
        m = build_manifest(
            {"project_goal": "g", "recipe_response": _SCHEMA}, framework="goose"
        )
        assert m["recipe_response"] == _SCHEMA

    def test_no_key_when_absent(self):
        m = build_manifest({"project_goal": "g"}, framework="goose")
        assert "recipe_response" not in m

    def test_no_key_when_invalid(self):
        m = build_manifest(
            {"project_goal": "g", "recipe_response": {"no": "type"}}, framework="goose"
        )
        assert "recipe_response" not in m


class TestEmitRecipeResponse:
    def test_emits_inline_json_that_roundtrips_and_validates(self):
        y = _emit_recipe(
            title="T",
            description="D",
            instructions="body",
            extensions=["developer"],
            response=_SCHEMA,
        )
        assert "response:" in y
        assert _json_schema_from_recipe(y) == _SCHEMA  # round-trips
        assert _validate_recipe_yaml(y) == []

    def test_no_block_when_none(self):
        y = _emit_recipe(
            title="T", description="D", instructions="body", extensions=["developer"]
        )
        assert "response:" not in y

    def test_empty_dict_emits_no_block(self):
        y = _emit_recipe(
            title="T",
            description="D",
            instructions="body",
            extensions=["developer"],
            response={},
        )
        assert "response:" not in y

    def test_forbidden_token_property_names_do_not_false_trip(self):
        # Properties literally named model/context/envs and a "type":"sse" value must
        # not trip the column-0-anchored forbidden guards (they sit mid-line in JSON).
        schema = {
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "context": {"type": "string"},
                "envs": {"type": "string"},
                "kind": {"const": "sse"},
            },
        }
        y = _emit_recipe(
            title="T",
            description="D",
            instructions="body",
            extensions=["developer"],
            response=schema,
        )
        assert _validate_recipe_yaml(y) == []
        assert _json_schema_from_recipe(y) == schema

    def test_description_with_special_chars_roundtrips(self):
        schema = {"type": "object", "properties": {"x": {"description": 'a "quote" and: colon'}}}
        y = _emit_recipe(
            title="T", description="D", instructions="body", extensions=["developer"],
            response=schema,
        )
        assert _validate_recipe_yaml(y) == []
        assert _json_schema_from_recipe(y) == schema


class TestValidatorShapeCheck:
    def test_response_without_json_schema_flagged(self):
        bad = 'version: "1.0.0"\ntitle: "T"\ninstructions: |\n  body\nresponse:\nextensions:\n'
        violations = _validate_recipe_yaml(bad)
        assert any("response:" in v and "json_schema" in v for v in violations)

    def test_response_in_instructions_block_not_flagged(self):
        # A 'response:' line indented inside the instructions block must not match the
        # column-0-anchored check (it is part of the literal scalar, not a recipe key).
        ok = 'version: "1.0.0"\ntitle: "T"\ninstructions: |\n  Return a response: now\nextensions:\n'
        assert _validate_recipe_yaml(ok) == []


class TestOrchestratorEmission:
    @staticmethod
    def _manifest(**extra):
        base = {"project_name": "P", "output_files": [{"path": "alpha.agent.md"}]}
        base.update(extra)
        return base

    def test_orchestrator_gets_response(self):
        adapter = GooseAdapter()
        recipe = adapter.render_agent_file(
            _ORCH_MD, "orchestrator", self._manifest(recipe_response=_SCHEMA)
        )
        assert "response:" in recipe
        assert _json_schema_from_recipe(recipe) == _SCHEMA
        assert _validate_recipe_yaml(recipe) == []

    def test_non_orchestrator_has_no_response(self):
        adapter = GooseAdapter()
        recipe = adapter.render_agent_file(
            _ORCH_MD, "alpha", self._manifest(recipe_response=_SCHEMA)
        )
        assert "response:" not in recipe

    def test_orchestrator_without_response_unchanged(self):
        adapter = GooseAdapter()
        recipe = adapter.render_agent_file(_ORCH_MD, "orchestrator", self._manifest())
        assert "response:" not in recipe


class TestSchemaContract:
    def test_recipe_response_declared_and_validates(self):
        schema = json.loads(MANIFEST_SCHEMA_PATH.read_text())
        assert "recipe_response" in schema["properties"]
        manifest = build_manifest(
            {"project_goal": "g", "recipe_response": _SCHEMA}, framework="goose"
        )
        jsonschema.Draft7Validator(
            schema["properties"]["recipe_response"]
        ).validate(manifest["recipe_response"])
