"""agentteams #508: configurable model and reasoning effort per role, on every provider."""

from __future__ import annotations

import argparse
import json

import pytest

from agentteams import agent_runtime as ar
from agentteams.cli.agent_runtime_switch import add_agent_runtime_arguments, apply_to_description
from agentteams.frameworks._claude_model import inject_session_model
from agentteams.frameworks.claude import ClaudeAdapter
from agentteams.frameworks.copilot_cli import CopilotCLIAdapter
from agentteams.frameworks.copilot_vscode import CopilotVSCodeAdapter

_COPILOT_SRC = ('---\nname: Security\ndescription: "d"\nuser-invocable: false\ntools: [\'read\']\n'
                'model: ["Claude Opus 4.8 (copilot)"]\n---\nbody\n')


def _manifest(block: dict) -> dict:
    return {"agent_runtime": ar.to_manifest(ar.parse({"agent_runtime": block}))}


# --- parsing and validation ------------------------------------------------------------------------------------------

def test_defaults_are_medium_for_both_roles_and_no_models():
    rt = ar.parse({})
    for role in ar.ROLES:
        assert rt.roles[role].effort == {"default": "medium"}
        assert rt.roles[role].model == {}
        assert rt.roles[role].effort_explicit is False


def test_a_bare_model_string_means_claude_and_copilot_covers_both_surfaces():
    rt = ar.parse({"agent_runtime": {"orchestrator": {"model": "fable"},
                                     "subagents": {"model": {"copilot": "GPT-5 (copilot)", "copilot-cli": "gpt-5"}}}})
    assert rt.roles["orchestrator"].model == {"claude": "fable"}
    assert rt.roles["subagents"].model == {"copilot-vscode": "GPT-5 (copilot)", "copilot-cli": "gpt-5"}


@pytest.mark.parametrize("model", [
    {"codex": "openai/gpt-5"}, {"codex": "https://evil.example/v1"}, {"claude": "opus\nmodel_provider: x"},
    {"goose": "a b"}, {"copilot": 'x", "y'}, {"openrouter": "m"},
])
def test_a_model_that_could_route_a_provider_elsewhere_is_refused(model):
    with pytest.raises(ar.AgentRuntimeError):
        ar.parse({"agent_runtime": {"subagents": {"model": model}}})


@pytest.mark.parametrize("effort", ["minimal", "ultra", "MEDIUM", "", 3])
def test_a_non_neutral_bare_effort_is_refused(effort):
    with pytest.raises(ar.AgentRuntimeError):
        ar.parse({"agent_runtime": {"orchestrator": {"effort": effort}}})


def test_a_provider_level_is_reachable_through_the_object_form_and_checked_per_provider():
    rt = ar.parse({"agent_runtime": {"subagents": {"effort": {"default": "high", "codex": "ultra"}}}})
    assert rt.roles["subagents"].effort == {"default": "high", "codex": "ultra"}
    with pytest.raises(ar.AgentRuntimeError, match="claude takes"):
        ar.parse({"agent_runtime": {"subagents": {"effort": {"claude": "ultra"}}}})


def test_unknown_keys_and_bad_slugs_are_refused():
    for block in ({"orchestrator": {"effort": "low", "temperature": 1}}, {"extra": {}},
                  {"agents": {"Not A Slug": {"effort": "low"}}}):
        with pytest.raises(ar.AgentRuntimeError):
            ar.parse({"agent_runtime": block})


# --- resolution ------------------------------------------------------------------------------------------------------

def test_precedence_is_agent_override_then_role_then_default():
    rt = ar.parse({"agent_runtime": {"subagents": {"effort": "high"}, "agents": {"security": {"effort": "max"}}}})
    assert ar.effort_for(rt, "security", "claude") == "max"
    assert ar.effort_for(rt, "navigator", "claude") == "high"
    assert ar.effort_for(rt, "orchestrator", "claude") == "medium"
    assert ar.explicit_effort(rt, "navigator") and not ar.explicit_effort(rt, "orchestrator")


def test_frameworks_without_an_effort_surface_write_none_and_say_so():
    rt = ar.parse({})
    for fw in ("copilot-cli", "codex", "goose", "grok", "agents-md", "wasm"):
        assert ar.effort_for(rt, "navigator", fw) is None
        assert any("is not written for " + fw in line for line in ar.report_lines(rt, fw))
    assert ar.report_lines(rt, "claude") == []
    assert ar.report_lines(rt, "copilot-vscode") == []


def test_a_configured_model_a_framework_does_not_write_is_reported():
    rt = ar.parse({"agent_runtime": {"subagents": {"model": {"codex": "gpt-5", "goose": "m", "grok": "g"}}}})
    for fw in ("codex", "goose", "grok"):
        assert ar.model_for(rt, "navigator", fw) is not None
        assert any("model is not written" in line for line in ar.report_lines(rt, fw))


def test_a_review_critical_agent_below_the_default_is_warned_by_override_or_by_role():
    rt = ar.parse({"agent_runtime": {"agents": {"security": {"effort": "low", "model": "haiku"}}}})
    lines = ar.report_lines(rt, "claude", ["security", "navigator"])
    assert any("@security (review-critical governance) runs at effort low" in line for line in lines)
    assert any("@security (review-critical governance) has its own model" in line for line in lines)
    role_low = ar.parse({"agent_runtime": {"subagents": {"effort": "low"}}})  # @security condition 2026-11-15
    lines = ar.report_lines(role_low, "claude", ["security", "adversarial", "navigator"])
    assert sum("runs at effort low" in line for line in lines) == 2
    assert not any("navigator" in line for line in lines)
    assert not ar.report_lines(ar.parse({}), "claude", ["security"])


def test_a_governance_model_is_warned_on_copilot_cli_too():
    rt = ar.parse({"agent_runtime": {"agents": {"security": {"model": {"copilot": "GPT-5"}}}}})
    assert any("has its own model" in line for line in ar.report_lines(rt, "copilot-cli", ["security"]))


def test_a_per_agent_override_covers_only_the_providers_it_names():
    rt = ar.parse({"agent_runtime": {"subagents": {"effort": "high"}, "agents": {"navigator": {"effort": {"codex": "ultra"}}}}})
    assert ar.effort_for(rt, "navigator", "claude") == "high"
    assert ar.effort_for(rt, "navigator", "copilot-vscode") == "high"
    assert ar.explicit_effort(rt, "navigator", "claude")  # the role is explicit
    rt = ar.parse({"agent_runtime": {"agents": {"navigator": {"effort": {"codex": "ultra"}}}}})
    assert not ar.explicit_effort(rt, "navigator", "claude") and ar.effort_for(rt, "navigator", "claude") == "medium"


def test_the_copilot_family_key_works_in_an_effort_object_and_a_specific_key_wins():
    rt = ar.parse({"agent_runtime": {"subagents": {"effort": {"copilot-vscode": "low", "copilot": "high"}}}})
    assert rt.roles["subagents"].effort["copilot-vscode"] == "low" and rt.roles["subagents"].effort["copilot-cli"] == "high"


def test_orchestrator_max_is_not_a_session_setting_and_is_reported():
    rt = ar.parse({"agent_runtime": {"orchestrator": {"effort": "max"}}})
    assert ar.session_effort(rt, "claude") is None
    assert ar.effort_for(rt, "orchestrator", "claude") == "max"
    assert any("takes no max" in line for line in ar.report_lines(rt, "claude"))


@pytest.mark.parametrize("record", [
    "x", {"roles": [], "agents": {}}, {"roles": {"orchestrator": {"effort": {"claude": "ultra"}}}},
    {"roles": {"subagents": {"model": {"codex": "openai/gpt-5"}}}}, {"extra": 1},
])
def test_a_malformed_or_unchecked_manifest_record_is_refused(record):
    with pytest.raises(ar.AgentRuntimeError):
        ar.from_manifest({"agent_runtime": record})


def test_the_manifest_record_round_trips_and_a_missing_one_means_defaults():
    rt = ar.parse({"agent_runtime": {"subagents": {"effort": "high", "model": "sonnet"},
                                     "agents": {"navigator": {"effort": "low"}}}})
    assert ar.from_manifest({"agent_runtime": ar.to_manifest(rt)}) == rt
    assert ar.from_manifest({}) == ar.parse({})


# --- adapters ---------------------------------------------------------------------------------------------------------

def test_claude_agent_files_get_effort_and_a_configured_model():
    out = ClaudeAdapter().render_agent_file(_COPILOT_SRC, "security",
                                            _manifest({"subagents": {"effort": "high", "model": "sonnet"}}))
    assert "\nmodel: sonnet\neffort: high\n" in out
    default = ClaudeAdapter().render_agent_file(_COPILOT_SRC, "orchestrator", {})
    assert "\nmodel: claude-fable-5-2\neffort: medium\n" in default


def test_claude_keeps_a_source_effort_unless_the_brief_sets_one():
    src = _COPILOT_SRC.replace("---\nbody", "effort: low\n---\nbody")
    assert "\neffort: low\n" in ClaudeAdapter().render_agent_file(src, "security", {})
    out = ClaudeAdapter().render_agent_file(src, "security", _manifest({"subagents": {"effort": "xhigh"}}))
    assert "\neffort: xhigh\n" in out and "effort: low" not in out


def test_copilot_vscode_writes_reasoning_effort_and_the_cli_does_not():
    m = _manifest({"subagents": {"effort": "high", "model": {"copilot": "GPT-5 (copilot)"}}})
    vs = CopilotVSCodeAdapter().render_agent_file(_COPILOT_SRC, "security", m)
    assert 'model: ["GPT-5 (copilot)"]' in vs and "reasoning-effort: high" in vs
    cli = CopilotCLIAdapter().render_agent_file(_COPILOT_SRC, "security", m)
    assert 'model: ["GPT-5 (copilot)"]' in cli and "reasoning-effort" not in cli  # one model shape on both surfaces


def test_copilot_without_a_configured_model_keeps_the_template_model():
    vs = CopilotVSCodeAdapter().render_agent_file(_COPILOT_SRC, "security", {})
    assert 'model: ["Claude Opus 4.8 (copilot)"]' in vs and "reasoning-effort: medium" in vs


def test_the_settings_example_carries_effort_level_and_configured_models():
    example = '{"hooks": {}}'
    data = json.loads(inject_session_model(example))
    assert data["effortLevel"] == "medium"
    data = json.loads(inject_session_model(example, _manifest({"orchestrator": {"model": "fable", "effort": "high"},
                                                               "subagents": {"model": "sonnet"}})))
    assert (data["model"], data["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], data["effortLevel"]) == ("fable", "sonnet", "high")
    data = json.loads(inject_session_model(example, _manifest({"orchestrator": {"effort": "max"}})))
    assert "effortLevel" not in data
    assert any("takes" in c or "max" in c for c in data["_comment"])


def test_an_existing_example_value_is_kept():
    data = json.loads(inject_session_model('{"effortLevel": "low", "model": "haiku"}'))
    assert (data["effortLevel"], data["model"]) == ("low", "haiku")


# --- flags ------------------------------------------------------------------------------------------------------------

def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_agent_runtime_arguments(parser)
    return parser.parse_args(list(argv))


def test_flags_override_the_brief_for_the_run_and_say_they_are_not_kept():
    desc = {"agent_runtime": {"subagents": {"effort": "low", "model": {"claude": "haiku"}}}}
    notices = apply_to_description(desc, _args("--subagent-effort", "high", "--subagent-model", "copilot=GPT-5",
                                               "--orchestrator-effort", "codex=ultra"))
    rt = ar.parse(desc)
    assert rt.roles["subagents"].effort == {"default": "high"}
    assert rt.roles["subagents"].model == {"claude": "haiku", "copilot-vscode": "GPT-5", "copilot-cli": "GPT-5"}
    assert rt.roles["orchestrator"].effort == {"default": "medium", "codex": "ultra"}
    assert len(notices) == 1 and "not kept" in notices[0]


def test_a_flag_wins_over_every_brief_value_it_covers():
    desc = {"agent_runtime": {"subagents": {"effort": {"default": "low", "claude": "low"},
                                            "model": {"copilot-cli": "old", "copilot-vscode": "old"}}}}
    apply_to_description(desc, _args("--subagent-effort", "high", "--subagent-model", "copilot=NEW"))
    rt = ar.parse(desc)
    assert ar.effort_for(rt, "navigator", "claude") == "high"
    assert ar.model_for(rt, "navigator", "copilot-cli") == "NEW" and ar.model_for(rt, "navigator", "copilot-vscode") == "NEW"


def test_no_flags_leave_the_brief_untouched():
    desc = {"project_name": "x"}
    assert apply_to_description(desc, _args()) == [] and "agent_runtime" not in desc


def test_a_bad_flag_value_fails_validation_before_render():
    from agentteams import ingest

    desc = {"project_goal": "goal"}
    apply_to_description(desc, _args("--orchestrator-model", "codex=openai/gpt-5"))
    assert any("not a codex model name" in e for e in ingest.validate(desc))


# --- schema -----------------------------------------------------------------------------------------------------------

def test_the_brief_schema_accepts_the_documented_block():
    jsonschema = pytest.importorskip("jsonschema")
    from importlib import resources

    schema = json.loads(resources.files("agentteams").joinpath("schemas/project-description.schema.json").read_text())
    goal = "A project goal long enough for the schema's minimum length check."
    block = {"orchestrator": {"effort": "medium", "model": {"claude": "claude-fable-5-2"}},
             "subagents": {"effort": {"default": "medium", "copilot": "high", "codex": "ultra"}, "model": "opus"},
             "agents": {"security": {"effort": "high"}}}
    jsonschema.validate({"project_goal": goal, "agent_runtime": block}, schema)
    ar.parse({"agent_runtime": block})  # the code accepts what the schema accepts
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"project_goal": goal, "agent_runtime": {"orchestrator": {"temperature": 1}}}, schema)


# --- --update / --merge (front-matter merge; operator ruling Q2 Option A) ---------------------------------------------

def _fm(**keys: str) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in keys.items()) + "---\nbody\n"


def test_update_adds_a_missing_effort_line_when_a_baseline_exists():
    from agentteams.front_matter_merge import _merge_front_matter

    rendered = _fm(name="n", tools="Read", model="opus", effort="medium")
    disk = _fm(name="n", tools="Read", model="opus")
    merged, applied, proposals = _merge_front_matter(rendered, disk, {"name": "n", "tools": "Read", "model": "opus"})
    assert merged.get("effort") == "medium" and any("'effort'" in a for a in applied) and not proposals


def test_update_keeps_a_hand_edited_effort_and_reports_it():
    from agentteams.front_matter_merge import _merge_front_matter

    rendered = _fm(name="n", tools="Read", effort="high")
    disk = _fm(name="n", tools="Read", effort="low")
    merged, _applied, proposals = _merge_front_matter(rendered, disk, {"name": "n", "tools": "Read", "effort": "medium"})
    assert merged["effort"] == "low" and any("'effort'" in p and "Kept yours" in p for p in proposals)


def test_update_never_writes_a_changed_model_only_proposes_it():
    from agentteams.front_matter_merge import _merge_front_matter

    rendered = _fm(name="n", tools="Read", model="sonnet", effort="medium")
    disk = _fm(name="n", tools="Read", model="opus", effort="medium")
    merged, _applied, proposals = _merge_front_matter(rendered, disk,
                                                      {"name": "n", "tools": "Read", "model": "opus", "effort": "medium"})
    assert merged["model"] == "opus" and any("'model' is a capability declaration" in p for p in proposals)
