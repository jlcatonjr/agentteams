"""The instance-update agent proposes and cannot write, structurally.

This agent's whole subject is changing *other* repositories' deployed agentteams instances. Two
constraints were established in the 2026-07-31 design audit, and both are properties of the file
rather than instructions inside it — because an instruction is not a control, and the guarantee has
to survive a persuasive argument for making an exception.

1. **No write tool.** Its front-matter grant is `['read', 'search']`, which the Claude adapter maps
   to `Read, Grep, Glob`. It cannot apply an edit even if convinced it should. This matters
   specifically for capability keys: the deterministic merge never auto-applies `tools:` because
   widening a grant unattended is privilege escalation, and an agent that could apply its own
   proposals would route straight around that.

2. **Never generated into a consumer.** It is gated on an explicit `instance_maintenance`
   capability and is never inferred from project text. Shipping it into every team would hand each
   one an agent whose subject is editing that team's own instruction files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.analyze import build_manifest
from agentteams.frameworks.claude import _map_allowed_tools
from agentteams.yaml_frontmatter import parse_yaml_front_matter

_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "agentteams" / "templates" / "domain" / "agentteams-updater.template.md"
)

_BRIEF = {
    "project_name": "Demo",
    "project_goal": "build things",
    "deliverable_type": "x",
    "output_format": "y",
    "description": "a software project",
}


@pytest.fixture(scope="module")
def body() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


# --- constraint 1: it cannot write ----------------------------------------

def test_the_tool_grant_contains_no_write_capability(body):
    yaml_text, _ = parse_yaml_front_matter(body)
    tools_line = next(ln for ln in yaml_text.splitlines() if ln.startswith("tools:"))
    for forbidden in ("edit", "execute", "write", "todo"):
        assert forbidden not in tools_line, (
            f"agentteams-updater's tool grant includes '{forbidden}'. Proposal-only has to be a "
            "property of the grant, not a sentence in the prose."
        )


def test_the_claude_mapping_is_read_only(body):
    """The grant must still be read-only after per-framework translation."""
    allowed = _map_allowed_tools(body)
    for forbidden in ("Write", "Edit", "Bash", "NotebookEdit"):
        assert forbidden not in allowed, f"Claude mapping grants {forbidden}: {allowed}"
    assert "Read" in allowed, "it still has to be able to read the target"


def test_the_contract_says_the_restriction_is_structural(body):
    """A future editor widening the grant should hit the reason, not just the rule."""
    assert "You have no file-writing tool" in body
    assert "privilege escalation" in body


def test_it_refuses_targets_that_are_not_version_controlled(body):
    assert "not under version control" in body
    assert "A refusal is a successful outcome" in body


# --- constraint 2: it never ships into a consumer -------------------------

def test_it_is_absent_without_the_explicit_capability():
    assert "agentteams-updater" not in build_manifest(dict(_BRIEF))["selected_archetypes"]


def test_no_project_text_can_summon_it():
    """Gated, not inferred — the wording that would trip a keyword trigger must not."""
    chatty = dict(_BRIEF, description=(
        "a project about updating deployed agentteams instances, agent updates, "
        "instance maintenance and template migration"
    ))
    assert "agentteams-updater" not in build_manifest(chatty)["selected_archetypes"]


def test_the_explicit_capability_selects_it():
    manifest = build_manifest(dict(_BRIEF, capabilities=["instance_maintenance"]))
    assert "agentteams-updater" in manifest["selected_archetypes"]


def test_an_unrelated_archetype_override_does_not_drop_it():
    """Force-appended after the override path, like research-analyst."""
    manifest = build_manifest(dict(
        _BRIEF,
        capabilities=["instance_maintenance"],
        selected_archetypes=["primary-producer"],
    ))
    assert "agentteams-updater" in manifest["selected_archetypes"]


# --- the judgment scope it exists for -------------------------------------

def test_it_names_the_three_cases_the_merge_refuses(body):
    """Without these it is a second deterministic merge, which would be worse than none."""
    assert "Capability proposals" in body
    assert "Both-sides conflicts" in body
    assert "Intentional divergence vs. stale drift" in body


def test_it_defers_to_the_deterministic_merge_first(body):
    """The proposal must let a reader run --update --merge and stop reading."""
    assert "What the deterministic merge already handles" in body
    assert "Do not propose anything in this list" in body


def test_every_proposal_carries_its_own_falsification(body):
    # Whitespace-normalised: the phrase is line-wrapped in the template, and re-wrapping prose
    # to suit a substring check is the wrong direction.
    flat = " ".join(body.lower().split())
    assert "what would show the recommendation is wrong" in flat


def test_ambiguity_is_a_reportable_outcome(body):
    """A forced classification is worse than an honest "I could not tell"."""
    flat = " ".join(body.split())
    assert "Ambiguous, not proposed" in flat
    assert "This section being non-empty is normal" in flat
