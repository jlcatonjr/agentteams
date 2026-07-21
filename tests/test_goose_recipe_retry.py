"""Phase-4c goose-native: opt-in Goose recipe `retry` (self-validation).

Covers normalization (analyze._normalize_recipe_retry) incl. the safety clamps,
opt-in manifest passthrough, hand-built YAML emission + validation, forbidden-guard
non-collision for shell commands, orchestrator-only emission, the validator shape
check, and byte-identical additivity when none is declared. (String assertions, not
a YAML parser — the codebase intentionally has no YAML dependency.)
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from agentteams.analyze import _normalize_recipe_retry, build_manifest
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

_RETRY = {"max_retries": 3, "checks": [{"command": "pytest -q"}]}


class TestNormalizeRecipeRetry:
    def test_non_dict_returns_none(self):
        assert _normalize_recipe_retry(None) is None
        assert _normalize_recipe_retry("x") is None

    def test_no_valid_checks_returns_none(self):
        assert _normalize_recipe_retry({"max_retries": 3}) is None
        assert _normalize_recipe_retry({"checks": []}) is None
        assert _normalize_recipe_retry({"checks": [{"no": "cmd"}, {"command": "  "}]}) is None
        assert _normalize_recipe_retry({"checks": ["not-a-dict"]}) is None

    def test_valid_check_gets_shell_type(self):
        out = _normalize_recipe_retry({"checks": [{"command": "make test"}]})
        assert out["checks"] == [{"type": "shell", "command": "make test"}]

    def test_max_retries_clamped_and_defaulted(self):
        assert _normalize_recipe_retry({**_RETRY, "max_retries": 999})["max_retries"] == 10
        assert _normalize_recipe_retry({**_RETRY, "max_retries": 0})["max_retries"] == 1
        assert _normalize_recipe_retry({**_RETRY, "max_retries": -5})["max_retries"] == 1
        assert _normalize_recipe_retry({**_RETRY, "max_retries": "lots"})["max_retries"] == 3
        assert _normalize_recipe_retry({"checks": [{"command": "x"}]})["max_retries"] == 3

    def test_timeout_always_present_and_bounded(self):
        # absent -> default 300; never unbounded (design §6)
        assert _normalize_recipe_retry(_RETRY)["timeout_seconds"] == 300
        assert _normalize_recipe_retry({**_RETRY, "timeout_seconds": 99999})["timeout_seconds"] == 3600
        assert _normalize_recipe_retry({**_RETRY, "timeout_seconds": 0})["timeout_seconds"] == 1
        assert _normalize_recipe_retry({**_RETRY, "timeout_seconds": 45})["timeout_seconds"] == 45

    def test_optional_on_failure_fields(self):
        out = _normalize_recipe_retry(
            {**_RETRY, "on_failure": "  cleanup.sh  ", "on_failure_timeout_seconds": 60}
        )
        assert out["on_failure"] == "cleanup.sh"
        assert out["on_failure_timeout_seconds"] == 60
        bare = _normalize_recipe_retry(_RETRY)
        assert "on_failure" not in bare and "on_failure_timeout_seconds" not in bare


class TestBuildManifestPassthrough:
    def test_added_when_valid(self):
        m = build_manifest({"project_goal": "g", "recipe_retry": _RETRY}, framework="goose")
        assert m["recipe_retry"]["checks"] == [{"type": "shell", "command": "pytest -q"}]

    def test_no_key_when_absent(self):
        assert "recipe_retry" not in build_manifest({"project_goal": "g"}, framework="goose")

    def test_no_key_when_no_checks(self):
        m = build_manifest({"project_goal": "g", "recipe_retry": {"max_retries": 5}}, framework="goose")
        assert "recipe_retry" not in m


class TestEmitRecipeRetry:
    def test_emits_valid_retry_block(self):
        retry = {
            "max_retries": 5,
            "timeout_seconds": 30,
            "on_failure_timeout_seconds": 60,
            "checks": [{"type": "shell", "command": "pytest -q"}, {"type": "shell", "command": "ruff check ."}],
            "on_failure": "git checkout .",
        }
        y = _emit_recipe(
            title="T", description="D", instructions="body", extensions=["developer"], retry=retry
        )
        assert _validate_recipe_yaml(y) == []
        assert "\nretry:\n" in y
        assert "  max_retries: 5\n" in y  # unquoted int
        assert "  timeout_seconds: 30\n" in y
        assert "  on_failure_timeout_seconds: 60\n" in y
        assert '      command: "pytest -q"' in y
        assert '      command: "ruff check ."' in y
        assert '    - type: "shell"' in y
        assert '  on_failure: "git checkout ."' in y

    def test_no_block_when_none(self):
        y = _emit_recipe(title="T", description="D", instructions="body", extensions=["developer"])
        assert "retry:" not in y

    def test_empty_emits_no_block(self):
        y = _emit_recipe(
            title="T", description="D", instructions="body", extensions=["developer"], retry={}
        )
        assert "retry:" not in y

    def test_command_with_forbidden_tokens_does_not_false_trip(self):
        retry = {
            "max_retries": 2,
            "timeout_seconds": 10,
            "checks": [{"type": "shell", "command": "grep envs: f && echo type: sse && echo context:"}],
        }
        y = _emit_recipe(
            title="T", description="D", instructions="body", extensions=["developer"], retry=retry
        )
        assert _validate_recipe_yaml(y) == []
        assert '      command: "grep envs: f && echo type: sse && echo context:"' in y

    def test_command_with_quotes_and_newline_stays_one_scalar(self):
        retry = {
            "max_retries": 1,
            "timeout_seconds": 5,
            "checks": [{"type": "shell", "command": 'echo "hi"\nmodel: pwned'}],
        }
        y = _emit_recipe(
            title="T", description="D", instructions="body", extensions=["developer"], retry=retry
        )
        assert _validate_recipe_yaml(y) == []  # no injected `model:` key
        assert "\nmodel:" not in y  # newline collapsed -> no column-0 model: line
        assert '\\"hi\\"' in y  # quotes escaped within the single scalar


class TestValidatorShapeCheck:
    def test_retry_without_max_retries_flagged(self):
        bad = 'version: "1.0.0"\ntitle: "T"\ninstructions: |\n  b\nretry:\n  checks:\n    - type: "shell"\n      command: "x"\nextensions:\n'
        assert any("retry:" in v for v in _validate_recipe_yaml(bad))

    def test_retry_without_command_flagged(self):
        bad = 'version: "1.0.0"\ntitle: "T"\ninstructions: |\n  b\nretry:\n  max_retries: 3\nextensions:\n'
        assert any("retry:" in v for v in _validate_recipe_yaml(bad))


class TestOrchestratorEmission:
    @staticmethod
    def _manifest(**extra):
        base = {"project_name": "P", "output_files": [{"path": "alpha.agent.md"}]}
        base.update(extra)
        return base

    def test_orchestrator_gets_retry(self):
        adapter = GooseAdapter()
        norm = _normalize_recipe_retry(_RETRY)
        recipe = adapter.render_agent_file(_ORCH_MD, "orchestrator", self._manifest(recipe_retry=norm))
        assert "\nretry:\n" in recipe
        assert "  max_retries: 3\n" in recipe
        assert "  timeout_seconds: 300\n" in recipe  # bounded default injected
        assert _validate_recipe_yaml(recipe) == []

    def test_non_orchestrator_task_agent_gets_retry(self):
        # Gap 2 (goose-task-agent-structure-handoff-2026-07-20): retry generalizes
        # beyond the orchestrator too; a task-agent that declares recipe_retry emits
        # it (opt-in — an agent declaring none is unchanged).
        adapter = GooseAdapter()
        norm = _normalize_recipe_retry(_RETRY)
        recipe = adapter.render_agent_file(_ORCH_MD, "alpha", self._manifest(recipe_retry=norm))
        assert "retry:" in recipe

    def test_non_orchestrator_without_retry_unchanged(self):
        adapter = GooseAdapter()
        recipe = adapter.render_agent_file(_ORCH_MD, "alpha", self._manifest())
        assert "retry:" not in recipe

    def test_orchestrator_without_retry_unchanged(self):
        adapter = GooseAdapter()
        recipe = adapter.render_agent_file(_ORCH_MD, "orchestrator", self._manifest())
        assert "retry:" not in recipe


class TestSchemaContract:
    def test_recipe_retry_declared_and_validates(self):
        schema = json.loads(MANIFEST_SCHEMA_PATH.read_text())
        assert "recipe_retry" in schema["properties"]
        manifest = build_manifest({"project_goal": "g", "recipe_retry": _RETRY}, framework="goose")
        jsonschema.Draft7Validator(schema["properties"]["recipe_retry"]).validate(manifest["recipe_retry"])
