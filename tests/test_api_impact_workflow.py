"""The API Impact Review is wired into the orchestrator template and always emitted."""

from __future__ import annotations

from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "agentteams" / "templates"
ORCH = (TEMPLATES / "universal" / "orchestrator.template.md").read_text(encoding="utf-8")
REF = (TEMPLATES / "universal" / "api-impact.reference.template.md").read_text(encoding="utf-8")


def test_sub_protocol_gate_and_routing_row_are_fenced():
    begin, end = ORCH.index("<!-- AGENTTEAMS:BEGIN available_workflows"), ORCH.index("<!-- AGENTTEAMS:END available_workflows")
    assert begin < ORCH.index("### API Impact Review") < end
    assert begin < ORCH.index("6b. **API impact (closeout gate") < end
    rbegin, rend = ORCH.index("<!-- AGENTTEAMS:BEGIN routing_table_rows"), ORCH.index("<!-- AGENTTEAMS:END routing_table_rows")
    assert rbegin < ORCH.index("`@orchestrator` → API Impact Review") < rend
    assert "Workflow 15" not in ORCH  # a named sub-protocol, not a numbered workflow


def test_sub_protocol_runs_review_update_audit_revise_record_in_order():
    proto = ORCH[ORCH.index("### API Impact Review"):ORCH.index("### Sandbox Operator Handoff")]
    steps = ["**Scope.**", "**Review and update the API.**", "**Audit the related infrastructure.**",
             "**Revise**", "**Record.**"]
    positions = [proto.index(s) for s in steps]
    assert positions == sorted(positions)
    assert "API review pending" in proto and "Operator Decision Query" in proto


def test_reference_is_fenced_emitted_and_states_its_limits():
    from agentteams import output_plan
    assert "<!-- AGENTTEAMS:BEGIN api_impact v=2 -->" in REF and "<!-- AGENTTEAMS:END api_impact -->" in REF
    assert "self-attestation" in REF and "Python only" in REF.replace("Python\nonly", "Python only")
    assert "squash merge" in REF
    assert "references/api-impact.reference.md" in Path(output_plan.__file__).read_text(encoding="utf-8")
