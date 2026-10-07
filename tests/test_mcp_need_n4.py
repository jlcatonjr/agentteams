"""MCP-need protocol phase N4: mcp_detect keeps the per-agent list and decides each agent under the switch,
and generation seeds a new need register with those rows (unverified brief hints)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agentteams import analyze, liaison_logs, mcp_need
from agentteams.mcp_detect import detect_mcp_candidates

REPO = Path(__file__).resolve().parent.parent
ON = {"write_policy": "orchestrator-only", "privilege_profile": "confined"}
HINT = {"integration": "Lean LSP", "used_by_components": ["lean", "orchestrator", "lean", "", "nonesuch"],
        "trust_tier": "first-party", "max_side_effect": "read", "stateful": True}
ROSTER = ["orchestrator", "security", "lean-expert"]
KW = {"write_policy": True, "roster": ROSTER, "components": ["lean"]}


def _decisions(hint: dict) -> dict[str, str]:
    hint = {k: v for k, v in hint.items() if v is not None or k != "max_side_effect"}
    cand = detect_mcp_candidates({"mcp_hints": [hint]}, **KW)[0]
    return {e["agent"]: e["decision"] for e in cand.per_agent}


def test_without_the_switch_the_entry_is_unchanged():
    entry = detect_mcp_candidates({"mcp_hints": [HINT]})[0].to_manifest_entry()
    assert set(entry) == {"candidate_id", "recommendation", "rationale", "signals"}


def test_components_map_to_their_expert_and_unknowns_are_ignored():
    cand = detect_mcp_candidates({"mcp_hints": [HINT]}, **KW)[0]
    assert cand.agents == ["lean-expert", "orchestrator"]                 # component -> <slug>-expert; deduplicated
    assert "'nonesuch'" in cand.rationale and "nonesuch" not in cand.agents  # never invented as an agent


def test_scoped_switch_off_for_this_framework_adds_nothing():
    cand = detect_mcp_candidates({"mcp_hints": [HINT], **ON}, write_policy=False, roster=ROSTER)[0]
    assert cand.per_agent is None and set(cand.to_manifest_entry()) == {
        "candidate_id", "recommendation", "rationale", "signals"}


@pytest.mark.parametrize("change, expected", [
    ({}, {"lean-expert": "USE_RUNNER_PATH", "orchestrator": "USE_CLI"}),          # unmeasured: wait (Q3)
    ({"max_side_effect": "write"}, {"lean-expert": "REFUSE", "orchestrator": "USE_CLI"}),          # Q1
    ({"max_side_effect": "destructive"}, {"lean-expert": "REFUSE", "orchestrator": "USE_CLI"}),    # Q1
    ({"trust_tier": "third-party-vetted"}, {"lean-expert": "DEFER_TO_SECURITY_REVIEW", "orchestrator": "USE_CLI"}),
    ({"trust_tier": None}, {"lean-expert": "DEFER_TO_SECURITY_REVIEW", "orchestrator": "USE_CLI"}),  # fail closed
    ({"max_side_effect": "launch"}, {"lean-expert": "DEFER_TO_SECURITY_REVIEW", "orchestrator": "USE_CLI"}),
    ({"max_side_effect": None}, {"lean-expert": "DEFER_TO_SECURITY_REVIEW", "orchestrator": "USE_CLI"}),
])
def test_per_agent_decisions_follow_the_protocol(change, expected):
    assert _decisions({**HINT, **change}) == expected


def test_manifest_under_the_switch_is_schema_valid():
    jsonschema = pytest.importorskip("jsonschema")
    desc = {"project_goal": "x" * 20, "project_name": "P", "components": [{"slug": "lean", "name": "L"}],
            "mcp_hints": [HINT], **ON}
    manifest = analyze.build_manifest(desc, framework="claude")
    assert manifest["mcp_candidates"][0]["agents"] == ["lean-expert", "orchestrator"]
    schema = json.loads((REPO / "agentteams" / "schemas" / "team-manifest.schema.json").read_text(encoding="utf-8"))
    item_schema = schema["properties"]["mcp_candidates"]
    jsonschema.validate(manifest["mcp_candidates"], item_schema)
    assert manifest["mcp_candidates"][0]["per_agent"]


def test_seed_rows_are_unverified_and_skip_the_orchestrator():
    manifest = {"mcp_candidates": [detect_mcp_candidates({"mcp_hints": [HINT]}, **KW)[0].to_manifest_entry()]}
    rows = mcp_need.seed_rows(manifest, "2026-10-07")
    assert [r["agent"] for r in rows] == ["lean-expert"]
    assert rows[0]["source"] == "brief-hint" and rows[0]["verified"] == "no" and rows[0]["status"] == "waiting"
    assert set(rows[0]) == set(mcp_need.MCP_NEEDS_HEADERS)


def test_register_is_seeded_only_when_created(tmp_path):
    rows = [dict.fromkeys(mcp_need.MCP_NEEDS_HEADERS, "") | {"id": "a-b", "agent": "b", "status": "waiting"}]
    liaison_logs.init_csv_stubs(tmp_path, mcp_needs=True, mcp_seed_rows=rows)
    path = tmp_path / mcp_need.MCP_NEEDS_CSV
    assert [r["id"] for r in csv.DictReader(path.open())] == ["a-b"]
    liaison_logs.init_csv_stubs(tmp_path, mcp_needs=True, mcp_seed_rows=rows + rows)   # existing: never touched
    assert [r["id"] for r in csv.DictReader(path.open())] == ["a-b"]
    other = tmp_path / "other"
    liaison_logs.init_csv_stubs(other, mcp_seed_rows=rows)                         # no register without the switch
    assert not (other / mcp_need.MCP_NEEDS_CSV).exists()


def test_reference_states_the_operator_settings():
    doc = mcp_need.reference_doc()
    assert "each pilot phase close-out" in doc and "aren't collected across" in doc
