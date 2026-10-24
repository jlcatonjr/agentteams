"""Layer-2 brief keys validate strictly (A4).

Briefs of researchteam-derived projects carry keys of their own (``layer2_profile``, ``frozen_surfaces``,
``researchteam_notes``, ``governance_agents``, ``license``, ``methodology_coverage``). The schema sets
``additionalProperties: false``, so before these were declared every such brief, the researchteam scaffold
template included, failed strict validation. The fixtures below copy only the *shape* of four such briefs
(an upstream scaffold and three derived projects); their values are placeholders.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "agentteams" / "schemas"
                     / "project-description.schema.json").read_text(encoding="utf-8"))

LAYER2_KEYS = ("layer2_profile", "frozen_surfaces", "researchteam_notes", "governance_agents", "license",
               "methodology_coverage")

_BASE = {"project_goal": "A placeholder goal for a schema test fixture.", "deliverables": ["Report"]}

FIXTURES = {
    "scaffold-template": {**_BASE, "project_name": "example-project", "layer2_profile": "scholarly",
                          "enforce_decision_signing": True},
    "derived-proofs": {**_BASE, "layer2_profile": "scholarly", "researchteam_notes": {"exclude": ["notes-agent"]},
                       "methodology_coverage": {"units_dir": "reports/units", "map_path": "map.md"},
                       "frozen_surfaces": ["codex", "goose"], "capabilities": ["research_verification"]},
    "derived-framework": {**_BASE, "license": "Apache-2.0", "frozen_surfaces": ["codex"],
                          "governance_agents": ["navigator", "security", "code-hygiene", "conflict-auditor"]},
    "derived-teaching": {**_BASE, "layer2_profile": "scholarly", "enforce_decision_signing": True},
}


def _errors(brief: dict) -> list[str]:
    return [e.message for e in jsonschema.Draft7Validator(SCHEMA).iter_errors(brief)]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_layer2_shaped_briefs_validate_strictly(name: str) -> None:
    assert _errors(FIXTURES[name]) == []


def test_every_layer2_key_is_declared_and_documented() -> None:
    props = SCHEMA["properties"]
    for key in LAYER2_KEYS:
        assert key in props, key
        assert "Layer-2" in props[key]["description"], key
    assert SCHEMA["additionalProperties"] is False
    assert any("not allowed" in m for m in _errors({**_BASE, "layer2_unknown": 1}))   # still strict otherwise


def test_frozen_surface_ids_match_the_rule_15_contract() -> None:
    from agentteams.cli.frozen_surfaces import FROZEN_SURFACE_PATHS

    assert set(SCHEMA["properties"]["frozen_surfaces"]["items"]["enum"]) == set(FROZEN_SURFACE_PATHS)


@pytest.mark.parametrize(("key", "value"), [
    ("frozen_surfaces", ["codex", "codex"]), ("frozen_surfaces", ["vim"]), ("frozen_surfaces", "codex"),
    ("layer2_profile", "Scholarly Profile"), ("layer2_profile", 3),
    ("researchteam_notes", {"exclude": "notes-agent"}), ("researchteam_notes", ["notes-agent"]),
    ("governance_agents", ["Not A Slug"]), ("license", ""), ("license", {"spdx": "MIT"}),
    ("methodology_coverage", {"units_dir": 1}), ("methodology_coverage", "reports"),
])
def test_layer2_keys_are_typed(key: str, value: object) -> None:
    brief = copy.deepcopy(FIXTURES["derived-proofs"])
    brief[key] = value
    assert _errors(brief), (key, value)
