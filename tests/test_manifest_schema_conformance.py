"""`build_manifest` output must validate against `schemas/team-manifest.schema.json`.

**Why this file exists.** The schema declares `additionalProperties: false` at the top level, and
for an unknown length of time the real manifest emitted four fields the schema never declared
(`existing_project_path`, `governance_agents`, `code_index_extra_dirs`,
`memory_index_extra_dirs`). Strict validation of a real manifest failed, and nothing noticed.

It went unnoticed because every existing schema test validates a hand-written **fixture**. A
fixture only contains fields someone remembered to put in it, so by construction it can never
exercise the gap between what the schema declares and what the generator actually emits. This
file closes that specific hole: it validates the output of `build_manifest` itself.

The descriptions below deliberately span the branches that add or omit optional keys, because a
field that only appears under a condition is exactly the field most likely to be missing from the
schema.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.analyze import build_manifest

jsonschema = pytest.importorskip(
    "jsonschema", reason="jsonschema is a dev-only dependency; schema conformance is opt-in"
)

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "team-manifest.schema.json"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


# (id, description) — each chosen to exercise a different conditional branch of build_manifest.
_DESCRIPTIONS = [
    (
        "minimal-software",
        {"project_name": "Minimal", "project_goal": "a python module with an api",
         "deliverables": ["module"]},
    ),
    (
        "existing-project-path",  # flips existing_project_path from null to str
        {"project_name": "Existing", "project_goal": "a python module with an api",
         "deliverables": ["module"], "existing_project_path": "/tmp/example"},
    ),
    (
        "research-with-advisory",  # emits the `advisories` array
        {"project_name": "Research", "project_goal": "research hypothesis experiment academic study",
         "deliverables": ["paper"]},
    ),
    (
        "research-capability-enabled",  # suppresses the advisory, adds research-analyst
        {"project_name": "ResearchOn", "project_goal": "research hypothesis experiment academic study",
         "deliverables": ["paper"], "capabilities": ["research_verification"]},
    ),
    (
        "data-pipeline",
        {"project_name": "Pipeline", "project_goal": "an etl pipeline over a csv dataset",
         "deliverables": ["pipeline"]},
    ),
    (
        "documentation",
        {"project_name": "Docs", "project_goal": "documentation readme and a user guide",
         "deliverables": ["docs"]},
    ),
    (
        "retrieval-declared",  # populates retrieval_integration beyond its defaults
        {"project_name": "Retrieval", "project_goal": "a python module with an api",
         "deliverables": ["module"],
         "retrieval_integration": {
             "mode": "lexical-index",
             "query_entrypoints": ["services/query.py"],
             "maintenance_entrypoints": ["scripts/reindex.py"],
             "trigger_sources": ["scheduler"],
             "source_of_truth": ["warehouse"],
             "staleness_slo_minutes": 30,
         }},
    ),
]


@pytest.mark.parametrize("case_id,description", _DESCRIPTIONS, ids=[c[0] for c in _DESCRIPTIONS])
def test_built_manifest_validates_against_the_schema(case_id, description, schema):
    manifest = build_manifest(description)
    jsonschema.validate(manifest, schema)


@pytest.mark.parametrize("framework", ["copilot-vscode", "copilot-cli", "claude", "goose"])
def test_built_manifest_validates_for_every_framework(framework, schema):
    """`framework` is a schema enum; every adapter's manifest must satisfy it."""
    manifest = build_manifest(
        {"project_name": "FW", "project_goal": "a python module with an api",
         "deliverables": ["module"]},
        framework=framework,
    )
    assert manifest["framework"] == framework
    jsonschema.validate(manifest, schema)


def test_every_emitted_key_is_declared(schema):
    """The direct form of the assertion, with a failure message that names the offenders.

    `jsonschema.validate` above already catches this via `additionalProperties: false`, but its
    error text buries the field names inside a full schema dump. This states the problem plainly.
    """
    declared = set(schema["properties"])
    emitted: set[str] = set()
    for _, description in _DESCRIPTIONS:
        emitted |= set(build_manifest(description))
    undeclared = sorted(emitted - declared)
    assert not undeclared, (
        f"build_manifest emits fields the schema does not declare: {undeclared}. "
        f"The schema sets additionalProperties:false, so a real manifest fails strict "
        f"validation until each is declared in schemas/team-manifest.schema.json."
    )


def test_declared_but_unemitted_fields_are_conditional_not_dead(schema):
    """The converse must NOT be enforced as equality — and this records why.

    Several declared fields are absent from every manifest above (`mcp_candidates`,
    `mcp_servers`, `adopted_agents`, `recipe_*`). That is correct: each is emitted only when the
    project description opts into it. A test asserting declared == emitted would "fix" this by
    deleting real, reachable schema, so the assertion here is only that the unemitted set stays
    within the known-conditional list.
    """
    known_conditional = {
        "adopted_agents", "mcp_candidates", "mcp_servers",
        "recipe_parameters", "recipe_response", "recipe_retry",
        "advisories",  # emitted only when an advisory fires
        # Set by the CLI layer (generate.py) after build_manifest returns: the token
        # list is assembled from --target-host-features + privilege_profile expansion.
        "host_features",
        # Emitted only when a confined/exclusive project overrides the default
        # workspace roots; build_manifest omits it otherwise.
        "workspace_write_roots",
        # Emitted only when an exclusive project supplies extra P3a read-exclusion
        # paths; build_manifest omits it otherwise.
        "protected_read_paths",
        # P3-3 opt-in: emitted only when the description sets it true; build_manifest
        # omits it otherwise so the default exclusive block stays byte-identical.
        "resolve_deny_read_abspath",
        # CC-2 opt-out: set by the CLI layer (generate.py) from --allow-fallback-fail-open
        # after build_manifest returns; emitted only when true.
        "fallback_fail_open",
    }
    emitted: set[str] = set()
    for _, description in _DESCRIPTIONS:
        emitted |= set(build_manifest(description))
    unemitted = set(schema["properties"]) - emitted
    unexpected = sorted(unemitted - known_conditional)
    assert not unexpected, (
        f"schema declares fields no case emits and that are not known-conditional: {unexpected}. "
        f"Either add a case that exercises them, or remove them from the schema if dead."
    )
