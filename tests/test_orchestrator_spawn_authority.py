"""Tests for the Orchestrator Spawn-Authority & Query Funnel feature (Phases 1-2).

Covers the fenced-propagation touchpoints so `--update --merge` carries the feature to existing
teams and `--init` seeds it into new ones:
  - the shipped reference is planned for every framework;
  - the escalation-ledger CSV stub is created with the right header;
  - the orchestrator template's fenced routing/workflow edits + version bumps are present;
  - Workflow 9 step 4 is role-conditional (the peer/initiator path is preserved);
  - repo-liaison Protocol 3 gains an additive delegate branch (funnel + refusal);
  - NO new unfenced constitutional rule is added (the ratchet forbids it — the feature is
    fenced-only), and the host-capability claims are tracked in framework_research;
  - the eval-suite carries the funnel scenario.

These assert TEXT PRESENCE / structure, not resolve-first *behavior* — a known, deliberate limit
of structural enforcement for a governance rule (see the plan's QF-4/QF-8).
"""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "agentteams" / "templates" / "universal"
ORCH = TEMPLATES / "orchestrator.template.md"
LIAISON = TEMPLATES / "repo-liaison.template.md"
REFERENCE = TEMPLATES / "orchestrator-spawn-authority.reference.template.md"
COPILOT_INSTR = REPO / "agentteams" / "templates" / "copilot-instructions.template.md"


# --------------------------------------------------------------------------- #
# Pipeline registration
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "framework", ["copilot-vscode", "copilot-cli", "claude", "goose", "agents-md"]
)
def test_spawn_authority_reference_planned_for_every_framework(framework):
    from agentteams.output_plan import _plan_output_files

    files = _plan_output_files(
        archetypes=["quality-auditor"],
        tool_agents=[],
        reference_tools=[],
        components=[],
        framework=framework,
    )
    by_path = {f["path"]: f for f in files}
    path = "references/orchestrator-spawn-authority.reference.md"
    assert path in by_path, f"{path} missing for framework={framework}"
    assert by_path[path]["type"] == "reference"


def test_escalation_ledger_stub_created_with_header(tmp_path):
    from agentteams import liaison_logs

    created = liaison_logs.init_csv_stubs(tmp_path)
    assert liaison_logs.ESCALATION_LOG_CSV in created
    stub = tmp_path / liaison_logs.ESCALATION_LOG_CSV
    assert stub.exists()
    header = stub.read_text(encoding="utf-8").splitlines()[0]
    for col in ("prime", "delegate_repo", "resolution_mode", "needs_user_review"):
        assert col in header
    # idempotent: a second call does not recreate it
    assert liaison_logs.ESCALATION_LOG_CSV not in liaison_logs.init_csv_stubs(tmp_path)


# --------------------------------------------------------------------------- #
# Orchestrator template — fenced edits + version bumps
# --------------------------------------------------------------------------- #

def test_orchestrator_routing_and_workflow_versions_bumped():
    text = ORCH.read_text(encoding="utf-8")
    assert "<!-- AGENTTEAMS:BEGIN routing_table_rows v=3 -->" in text
    assert "<!-- AGENTTEAMS:BEGIN available_workflows v=4 -->" in text


def test_orchestrator_has_funnel_routing_row_and_workflows():
    text = ORCH.read_text(encoding="utf-8")
    assert "`@orchestrator` → Workflow 12" in text
    assert "`@orchestrator` → Workflow 13" in text
    assert "### Workflow 12: Spawn-Authority Query Funnel" in text
    assert "### Workflow 13: Spawn a Scoped Child Orchestrator (in-repo)" in text


def test_workflow9_step4_is_role_conditional_and_preserves_peer_path():
    text = ORCH.read_text(encoding="utf-8")
    # role-conditional edit present
    assert "Role-conditional surfacing" in text
    # the peer/initiator/standalone path still surfaces to the user (not removed)
    assert "initiator / peer / standalone" in text


def test_feature_carries_no_new_unfenced_constitutional_rule():
    """The repo ratchet forbids new unfenced constraints (they don't propagate on --update
    --merge). The funnel's operative content must live in the fenced routing rows + Workflows
    12/13 + the shipped reference, NOT as a numbered constitutional rule. This mirrors the repo's
    own `test_no_rule_18_added_to_constitutional_rules` guard."""
    text = ORCH.read_text(encoding="utf-8")
    region = text[text.index("### Constitutional Rules"):
                  text.index("<!-- AGENTTEAMS:BEGIN authority_hierarchy")]
    assert "\n18." not in region and "18. " not in region
    # Funnel routing rows live INSIDE the fenced routing_table_rows block.
    fence = text[text.index("<!-- AGENTTEAMS:BEGIN routing_table_rows"):
                 text.index("<!-- AGENTTEAMS:END routing_table_rows")]
    assert "Workflow 12" in fence and "Workflow 13" in fence


# --------------------------------------------------------------------------- #
# repo-liaison — additive Protocol 3 delegate branch
# --------------------------------------------------------------------------- #

def test_repo_liaison_protocols_bumped_and_delegate_branch_additive():
    text = LIAISON.read_text(encoding="utf-8")
    assert "<!-- AGENTTEAMS:BEGIN protocols v=2 -->" in text
    # delegate branch: funnel up to prime
    assert "route the Coordination Request **up to that prime**" in text
    # peer path preserved
    assert "surface the request to the user" in text.lower() or "surface the request to the user" in text
    # refusal branch present
    assert "refused and reported as a peer conflict" in text


# --------------------------------------------------------------------------- #
# Seeds + reference content
# --------------------------------------------------------------------------- #

def test_copilot_instructions_gains_no_unfenced_rule():
    """Same ratchet: the new-team instruction template must not gain a new unfenced numbered
    constitutional rule for this feature — the reference + orchestrator fences carry it."""
    text = COPILOT_INSTR.read_text(encoding="utf-8")
    assert "11. **Spawner-authority" not in text


def test_reference_covers_carveout_refusal_and_depth_budget():
    text = REFERENCE.read_text(encoding="utf-8")
    assert "Carve-out (for questions)" in text
    assert "Refusal (for directives)" in text
    assert "one flat team layer beneath it" in text
    # honest framing: runtime enforces only no-direct-user-contact
    assert "output returns to its **spawner**" in text


def test_host_capability_claims_tracked_and_cross_linked():
    """The external (non-repo-verifiable) host facts the funnel relies on are recorded as tracked
    claims in framework_research, and the shipped reference points readers at that tracker rather
    than asserting them as authoritative. This is the consumer that keeps the claims from being
    dead prose and pins the reference<->tracker cross-link so neither drifts silently."""
    from agentteams.framework_research import HOST_CAPABILITY_CLAIMS

    assert {"subagent_spawn_depth", "subagent_output_containment", "no_nested_teams"} <= set(
        HOST_CAPABILITY_CLAIMS
    )
    for key, entry in HOST_CAPABILITY_CLAIMS.items():
        assert entry["status"] == "external-unverified-in-repo", key
        assert entry["source_url"].startswith("https://"), key
        assert entry["claim"].strip(), key
    # the reference names framework_research as the tracking home for these host facts
    assert "framework_research" in REFERENCE.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Eval suite scenario
# --------------------------------------------------------------------------- #

def test_eval_suite_carries_funnel_scenario():
    from agentteams.eval_suite import build_eval_suite

    suite = build_eval_suite({"project_name": "X", "framework": "claude",
                              "workstream_expert_slugs": [], "components": []})
    ids = {s["id"] for s in suite["scenarios"]}
    assert "escalation-delegate-query-funnels-to-prime" in ids
    scen = next(s for s in suite["scenarios"]
                if s["id"] == "escalation-delegate-query-funnels-to-prime")
    assert scen["category"] == "escalation"
    assert scen["predicate"]["kind"] == "frontmatter-and-body"
