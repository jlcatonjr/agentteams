"""Tests for --adopt-orphans: registering pre-existing custom agents into the
team roster without regenerating their files (agentteams.analyze.adopt_orphan_agents
+ copilot_vscode _get_team_slugs)."""

from __future__ import annotations

from agentteams import analyze
from agentteams.frameworks.copilot_vscode import _get_team_slugs


def _manifest():
    return analyze.build_manifest(
        {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "alpha", "name": "A"}]}
    )


def test_adopt_adds_to_roster_and_placeholders():
    m = _manifest()
    before = list(m["agent_slug_list"])
    before_domain = list(m["domain_agent_slugs"])
    newly = analyze.adopt_orphan_agents(m, ["legacy-custom", "nginx"])
    assert newly == ["legacy-custom", "nginx"]
    assert "legacy-custom" in m["agent_slug_list"] and "nginx" in m["agent_slug_list"]
    assert m["adopted_agents"] == ["legacy-custom", "nginx"]
    # UPPERCASE placeholder key (resolve_placeholders matches {AGENT_SLUG_LIST})
    assert "legacy-custom" in m["auto_resolved_placeholders"]["AGENT_SLUG_LIST"]
    # roster grew by exactly the adopted slugs
    assert set(m["agent_slug_list"]) == set(before) | {"legacy-custom", "nginx"}
    # adopted agents are NOT folded into domain_agent_slugs (avoid mislabeling
    # bespoke agents as standard domain archetypes)
    assert m["domain_agent_slugs"] == before_domain


def test_adopted_agents_conforms_to_manifest_schema():
    import json
    from pathlib import Path
    import jsonschema

    m = _manifest()
    analyze.adopt_orphan_agents(m, ["legacy-custom"])
    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "schemas" / "team-manifest.schema.json").read_text()
    )
    # adopted_agents must be a declared property (schema is additionalProperties:false)
    assert "adopted_agents" in schema["properties"]
    jsonschema.Draft7Validator(schema["properties"]["adopted_agents"]).validate(m["adopted_agents"])


def test_adopt_never_touches_output_files():
    m = _manifest()
    before_outputs = [f["path"] for f in m["output_files"]]
    analyze.adopt_orphan_agents(m, ["legacy-custom"])
    after_outputs = [f["path"] for f in m["output_files"]]
    assert before_outputs == after_outputs  # adopted agent's file is NOT scheduled for emit


def test_adopt_skips_already_present():
    m = _manifest()
    present = m["agent_slug_list"][0]
    newly = analyze.adopt_orphan_agents(m, [present, "fresh-one"])
    assert newly == ["fresh-one"]
    # no duplicate of the already-present slug
    assert m["agent_slug_list"].count(present) == 1


def test_adopt_empty_is_noop():
    m = _manifest()
    snapshot = list(m["agent_slug_list"])
    assert analyze.adopt_orphan_agents(m, []) == []
    assert m["agent_slug_list"] == snapshot
    assert "adopted_agents" not in m or m.get("adopted_agents") == []


def test_team_slugs_includes_adopted_for_yaml_filtering():
    m = _manifest()
    analyze.adopt_orphan_agents(m, ["legacy-custom"])
    slugs = _get_team_slugs(m)
    # adopted agent must be a valid cross-ref target so the adapter keeps it in
    # the orchestrator's agents:/handoffs: lists (else it would be filtered out)
    assert "legacy-custom" in slugs
    # and it must NOT have been added as an emitted output file
    assert not any("legacy-custom" in f["path"] for f in m["output_files"])
