"""Regression tests: the CLI-tool-discovery and skill-generation references are wired into
Workflow 0's capability-gap check, emitted for every framework, and the Goose capabilities
file cross-links the generic methodology doc."""
from __future__ import annotations

from pathlib import Path

import pytest

_EXPECTED = Path("examples/software-project/expected")
pytestmark = pytest.mark.skipif(
    not (_EXPECTED / "orchestrator.agent.md").exists(),
    reason="software-project example snapshot not present",
)


def _orch() -> str:
    return (_EXPECTED / "orchestrator.agent.md").read_text(encoding="utf-8")


def _cli_discovery_reference() -> str:
    return (_EXPECTED / "references" / "cli-tool-discovery.reference.md").read_text(
        encoding="utf-8"
    )


def _skill_generation_reference() -> str:
    return (_EXPECTED / "references" / "skill-generation.reference.md").read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# orchestrator.template.md — Workflow 0 wiring
# ---------------------------------------------------------------------------

def test_capability_gap_check_is_inside_the_available_workflows_fence():
    """Must be inside the fenced region so --update --merge propagates it to existing teams."""
    orch = _orch()
    begin = orch.index("<!-- AGENTTEAMS:BEGIN available_workflows")
    end = orch.index("<!-- AGENTTEAMS:END available_workflows")
    check = orch.index("**Capability gap check.**")
    assert begin < check < end


def test_workflow_0_references_both_new_docs():
    orch = _orch()
    workflow_0 = orch.index("### Workflow 0: Request Intake and Problem Framing")
    workflow_0a = orch.index("### Workflow 0A: Parallelization Analysis")
    workflow_0_text = orch[workflow_0:workflow_0a]
    assert "references/cli-tool-discovery.reference.md" in workflow_0_text
    assert "references/skill-generation.reference.md" in workflow_0_text


def test_workflow_0_steps_are_sequentially_numbered_after_insertion():
    # The capability-gap check was inserted as step 3, shifting the remaining steps.
    orch = _orch()
    workflow_0 = orch.index("### Workflow 0: Request Intake and Problem Framing")
    workflow_0a = orch.index("### Workflow 0A: Parallelization Analysis")
    workflow_0_text = orch[workflow_0:workflow_0a]
    for n in range(1, 9):
        assert f"\n{n}. " in workflow_0_text, f"missing numbered step {n}"


# ---------------------------------------------------------------------------
# references/cli-tool-discovery.reference.md — content + always-emitted
# ---------------------------------------------------------------------------

def test_cli_discovery_reference_is_emitted():
    content = _cli_discovery_reference()
    assert content.strip()


def test_cli_discovery_reference_covers_path_help_man_and_install():
    content = _cli_discovery_reference()
    assert "`which <cmd>`" in content or "which <cmd>" in content
    assert "$PATH" in content
    assert "--help" in content
    assert "man <cmd>" in content
    assert "strings <path>" in content
    assert "brew install" in content
    assert "sudo apt-get install" in content


def test_cli_discovery_reference_routes_installs_through_security_rule_s4():
    content = _cli_discovery_reference()
    assert "Rule S-4" in content
    assert "HALT" in content
    # Must not overclaim coverage security.template.md doesn't actually have
    # (conflict-auditor finding: no Mandatory Review Triggers row exists yet
    # for installs/sudo) — the doc should name that gap honestly.
    assert "no dedicated row" in content


def test_cli_discovery_reference_links_to_skill_generation():
    content = _cli_discovery_reference()
    assert "references/skill-generation.reference.md" in content


def test_cli_discovery_reference_separates_run_clearance_from_persistence_safety():
    """S-4 (run clearance) and S-9 (persistence safety) must be layered, not conflated —
    first-draft-audit regression: an earlier draft plan tried to claim S-4 'already covers'
    the persistence decision, which would repeat the discredited S-4-overclaim mistake."""
    content = _cli_discovery_reference()
    assert "Rule S-9" in content
    assert "separate decision from running it once" in content


# ---------------------------------------------------------------------------
# references/skill-generation.reference.md — content + concrete trigger/output
# ---------------------------------------------------------------------------

def test_skill_generation_reference_is_emitted():
    content = _skill_generation_reference()
    assert content.strip()


def test_skill_generation_reference_has_concrete_trigger_and_plan_tie_in():
    content = _skill_generation_reference()
    # Concrete trigger, not vague aspiration (first-draft audit finding).
    assert "Attempted, not suspected" in content
    # Reuses this project's own mandatory-plan mechanism rather than inventing a new one.
    assert "tmp/by-week/YYYY-Www" in content
    assert ".steps.csv" in content


def test_skill_generation_reference_has_security_audit_gate():
    content = _skill_generation_reference()
    assert "Rule S-9" in content
    # Cross-references the log by name; the schema itself is stated once, authoritatively,
    # in security.template.md's Rule S-9 (see test_pathway_safety_verification.py) — not
    # duplicated here.
    assert "security-decisions.log.csv" in content
    assert "blocks both the one-time use and persistence" in content
    assert "CONDITIONAL PASS" in content
    assert "conditions_verified" in content


def test_security_template_states_the_real_current_log_schema():
    """The schema lives once, authoritatively, in security.template.md's Rule S-9 — not
    duplicated in skill-generation.reference.md. Real current schema, not the template's own
    documented 'legacy' 6-column form (first-draft-audit regression: an earlier draft plan
    cited the stale schema)."""
    security_template = (
        Path("agentteams/templates/universal/security.template.md")
        .read_text(encoding="utf-8")
    )
    assert (
        "date,plan_slug,step,decision,status,conditions,conditions_verified,evidence,owner"
        in security_template
    )


# ---------------------------------------------------------------------------
# references/skill-generation.reference.md — worked example: a page fetch can't render
# (tmp/by-week/2026-W30/web-browsing-playwright-cli.plan.md)
# ---------------------------------------------------------------------------

def test_skill_generation_reference_has_browser_worked_example():
    content = _skill_generation_reference()
    assert "Worked example: a page `fetch` can't render" in content


def test_skill_generation_browser_example_states_tiered_approach_not_a_single_tool():
    content = _skill_generation_reference()
    assert "Tier 1" in content
    assert "Tier 2" in content


def test_skill_generation_browser_example_states_pattern_before_python_specific_tool():
    """First-draft-audit finding: this file is cross-framework AND cross-language, so it must not
    presuppose every generated project has (or wants) a Python runtime -- state the general
    pattern first, offer agentteams.research.browser as one concrete instance of it, not the only
    path."""
    content = _skill_generation_reference()
    tier2 = content[content.index("Tier 2"):content.index("Neither tier reaches it")]
    assert "pattern" in tier2.lower()
    assert "If this project is Python-based" in tier2
    assert "If this project is not Python-based" in tier2


def test_skill_generation_browser_example_names_per_request_guard_not_just_url_check():
    """Adversarial-audit-driven correction: a single check of the original URL is not sufficient
    for a real browser (redirects + page-initiated JS requests) -- the doc must say so, not just
    gesture at "safety"."""
    content = _skill_generation_reference()
    assert "re-applies this project's own URL-safety check to **every** request" in content
    assert "not just the one URL a caller typed in" in content


def test_skill_generation_browser_example_documents_headed_flag_semantics_honestly():
    content = _skill_generation_reference()
    assert "--headed" in content
    assert "it changes nothing about what the calling agent itself" in content


def test_skill_generation_browser_example_no_mcp_content():
    """Explicit user correction mid-session: Playwright is managed via CLI, not MCP. Checks the
    WHOLE worked-example section (not just the Tier 2 slice, per code-review's own narrower first
    draft) -- the property being guarded is "no MCP leakage anywhere in this section", not just
    within one subsection of it."""
    content = _skill_generation_reference()
    start = content.index("Worked example: a page `fetch` can't render")
    end = content.index("## Security audit gate")
    section = content[start:end]
    assert "MCP" not in section
    assert "mcp_servers" not in section
    assert "goose:mcp" not in section


def test_skill_generation_browser_example_pins_exact_install_pathway():
    """A typo in the extra name or the browser-binary install step would silently mislead every
    future reader of this generated doc -- pin the exact strings, not just "some install
    guidance exists"."""
    content = _skill_generation_reference()
    assert "agentteams[browser]" in content
    assert "playwright install chromium" in content


def test_skill_generation_browser_example_routes_absence_back_to_the_plan_mechanism():
    content = _skill_generation_reference()
    assert "Neither tier reaches it, or the browser tier isn't installed yet" in content
    assert "capability gap this protocol exists for" in content


# ---------------------------------------------------------------------------
# _plan_output_files — both new docs are unconditional (every framework), type: reference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "framework", ["copilot-vscode", "copilot-cli", "claude", "goose", "agents-md"]
)
def test_both_references_are_planned_for_every_framework(framework):
    from agentteams.output_plan import _plan_output_files

    files = _plan_output_files(
        archetypes=["quality-auditor"],
        tool_agents=[],
        reference_tools=[],
        components=[],
        framework=framework,
    )
    by_path = {f["path"]: f for f in files}
    for path in (
        "references/cli-tool-discovery.reference.md",
        "references/skill-generation.reference.md",
    ):
        assert path in by_path, f"{path} missing for framework={framework}"
        assert by_path[path]["type"] == "reference"


@pytest.mark.parametrize(
    "framework", ["copilot-vscode", "copilot-cli", "claude", "goose", "agents-md"]
)
def test_external_retrieval_quality_gate_reference_planned_for_every_framework(framework):
    """tmp/by-week/2026-W30/external-retrieval-quality-gate.plan.md: unconditional, like
    cli-tool-discovery/skill-generation above -- content-enricher.agent.md (which links to
    this reference) is itself emitted unconditionally for every team, so gating this file on
    archetype presence (the first implementation draft's plan) would leave a dangling
    reference in the common case of a team with content-enricher but neither
    research-analyst nor tool-doc-researcher."""
    from agentteams.output_plan import _plan_output_files

    files = _plan_output_files(
        archetypes=["quality-auditor"],
        tool_agents=[],
        reference_tools=[],
        components=[],
        framework=framework,
    )
    by_path = {f["path"]: f for f in files}
    path = "references/external-retrieval-quality-gate.reference.md"
    assert path in by_path, f"{path} missing for framework={framework}"
    assert by_path[path]["type"] == "reference"
