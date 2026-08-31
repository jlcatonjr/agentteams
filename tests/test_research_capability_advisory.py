"""The research-capability advisory in agentteams.analyze.

Motivating defect (measured 2026-07-30): `research-analyst` is gated on an explicit
`capabilities: ["research_verification"]` opt-in, and NO inspected team declared it — including
two literature-review teams that produce bibliographies. The external-retrieval quality gate
therefore governed a path no generated agent could take.

The fix is an advisory, not an auto-enable: selecting `research-analyst` pulls a real runtime
dependency (`agentteams[research]`) into the generated project, which is exactly why the flag is
explicit opt-in. Flipping it here would override a documented decision rather than surface it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams.analyze import build_manifest, classify_project_type

_ADVISORY_CODE = "research-capability-unset"
_RESEARCH_GOAL = "research hypothesis experiment academic study with citations"


def _manifest(goal: str, capabilities: list[str] | None = None) -> dict:
    description = {"project_name": "X", "project_goal": goal, "deliverables": ["d"]}
    if capabilities is not None:
        description["capabilities"] = capabilities
    return build_manifest(description)


def _codes(manifest: dict) -> list[str]:
    return [a["code"] for a in manifest.get("advisories", [])]


def test_research_project_without_the_capability_gets_an_advisory():
    manifest = _manifest(_RESEARCH_GOAL)
    assert classify_project_type({"project_goal": _RESEARCH_GOAL}) == "research"
    assert _ADVISORY_CODE in _codes(manifest)


def test_the_advisory_says_what_to_change():
    """An advisory that names a problem without naming the fix just relocates the puzzle."""
    manifest = _manifest(_RESEARCH_GOAL)
    message = next(a["message"] for a in manifest["advisories"] if a["code"] == _ADVISORY_CODE)
    assert "research_verification" in message
    assert "capabilities" in message
    assert "agentteams[research]" in message, "the dependency consequence must be disclosed"


def test_declaring_the_capability_clears_the_advisory():
    manifest = _manifest(_RESEARCH_GOAL, ["research_verification"])
    assert _ADVISORY_CODE not in _codes(manifest)
    assert "research-analyst" in manifest["selected_archetypes"]


def test_the_advisory_does_not_auto_enable_the_archetype():
    """The advisory surfaces the decision; it must not make it."""
    manifest = _manifest(_RESEARCH_GOAL)
    assert "research-analyst" not in manifest["selected_archetypes"]


@pytest.mark.parametrize("goal", [
    "a python module with an api and classes",          # software
    "an etl pipeline over a csv dataset",               # data-pipeline
    "documentation readme and a user guide",            # documentation
])
def test_non_research_projects_get_no_advisory(goal):
    assert _ADVISORY_CODE not in _codes(_manifest(goal))


def test_manifests_without_advisories_omit_the_key_entirely():
    """Absent rather than empty: an empty list reads as 'checked and found nothing', which is
    a different claim from 'this manifest predates advisories'."""
    manifest = _manifest("a python module with an api and classes")
    assert "advisories" not in manifest


def test_advisories_conform_to_the_manifest_schema():
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "team-manifest.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    advisories = schema["properties"]["advisories"]
    assert advisories["type"] == "array"
    item = advisories["items"]
    assert set(item["required"]) == {"code", "message"}
    assert item["additionalProperties"] is False

    manifest = _manifest(_RESEARCH_GOAL)
    for entry in manifest["advisories"]:
        assert set(entry) == {"code", "message"}
        assert entry["code"] and entry["message"]
