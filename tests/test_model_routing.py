"""Tests for F6 — cost / model-routing contract (default-OFF)."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import build_team
from agentteams import drift as _drift
from agentteams.model_routing import (
    MODEL_TIERS,
    ROUTING_SCHEMA_VERSION,
    agent_tier,
    build_routing_contract,
)

REPO_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = REPO_ROOT / "schemas" / "model-routing.schema.json"
EXAMPLES_DIR = REPO_ROOT / "examples"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _manifest() -> dict:
    return {
        "project_name": "DataPipeline",
        "framework": "copilot-vscode",
        "governance_agents": ["navigator", "security", "adversarial"],
        "agent_slug_list": [
            "orchestrator", "navigator", "security", "adversarial",
            "primary-producer", "ingest-expert", "load-expert",
        ],
    }


# ------------------------------ pure unit ------------------------------

def test_tier_rule_governance_is_cheap_else_primary():
    m = _manifest()
    assert agent_tier("navigator", m) == "cheap"
    assert agent_tier("adversarial", m) == "cheap"
    assert agent_tier("orchestrator", m) == "primary"
    assert agent_tier("ingest-expert", m) == "primary"
    assert agent_tier("unknown-agent", m) == "primary"  # conservative


def test_build_routing_contract_is_schema_valid_and_framework_neutral():
    import jsonschema
    c = build_routing_contract(_manifest())
    jsonschema.Draft7Validator(_schema()).validate(c)
    assert c["artifact_type"] == "model-routing"
    assert c["routing_schema_version"] == ROUTING_SCHEMA_VERSION
    assert tuple(c["tiers"]) == MODEL_TIERS
    # NO concrete model strings anywhere in the contract.
    for token in ("Claude", "gpt", "sonnet", "haiku", "opus", "litellm"):
        assert token.lower() not in json.dumps(c).lower(), (
            f"framework-coupled token {token!r} leaked into the neutral contract"
        )


def test_assignments_cover_every_agent_in_slug_list():
    m = _manifest()
    c = build_routing_contract(m)
    assert [a["agent"] for a in c["assignments"]] == m["agent_slug_list"]


def test_write_model_routing_rejects_nonconforming(tmp_path, monkeypatch):
    import agentteams.model_routing as mr
    # Force a non-conforming contract (artifact_type is a const).
    monkeypatch.setattr(mr, "build_routing_contract", lambda m: {"artifact_type": "WRONG"})
    with pytest.raises(build_team.ModelRoutingError, match="schema validation"):
        build_team._write_model_routing({"project_name": "P"}, tmp_path)
    assert not (tmp_path / build_team.MODEL_ROUTING_REL_PATH).exists()


def test_model_routing_error_is_runtime_not_oserror():
    assert issubclass(build_team.ModelRoutingError, RuntimeError)
    assert not issubclass(build_team.ModelRoutingError, OSError)


# ------------------------- default-OFF integration -------------------------

def _seed_gates(output_dir: Path, monkeypatch, *, ticket: str):
    refs = output_dir / "references"
    refs.mkdir(parents=True, exist_ok=True)
    key = "integration-waiver-key"
    monkeypatch.setenv("AGENTTEAMS_WAIVER_SIGNING_KEY", key)
    w = {
        "timestamp": "2026-05-03T00:00:00Z", "waiver_id": f"wf-{ticket}",
        "action_reviewed": "security-intel-freshness",
        "expires_at": "2099-01-01T00:00:00Z", "max_uses": "9", "uses": "0",
        "approver": "t", "ticket_id": ticket, "reason_code": "T",
        "conditions_verified": "verified", "signature": "",
    }
    payload = "|".join(w[k] for k in [
        "waiver_id", "action_reviewed", "expires_at", "max_uses", "uses",
        "approver", "ticket_id", "reason_code", "conditions_verified"])
    w["signature"] = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    (refs / "security-waivers.log.csv").write_text(
        "timestamp,waiver_id,action_reviewed,expires_at,max_uses,uses,approver,"
        "ticket_id,reason_code,conditions_verified,signature\n"
        + ",".join(w[k] for k in [
            "timestamp", "waiver_id", "action_reviewed", "expires_at",
            "max_uses", "uses", "approver", "ticket_id", "reason_code",
            "conditions_verified", "signature"]) + "\n",
        encoding="utf-8",
    )


def _run_generate(brief: Path, out: Path, monkeypatch, *, cost_routing: bool, ticket: str):
    _seed_gates(out, monkeypatch, ticket=ticket)
    args = ["--description", str(brief), "--output", str(out),
            "--yes", "--no-scan", "--security-offline"]
    if cost_routing:
        args.append("--cost-routing")
    rc = build_team.main(args)
    assert rc == 0, f"generate failed (cost_routing={cost_routing}): rc={rc}"


def test_default_off_emits_no_routing_artifact_and_is_byte_identical(tmp_path, monkeypatch):
    """R1 — the headline guarantee: no flag ⇒ no artifact ⇒ generated agent
    files are byte-identical to a no-F6-flag run."""
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")

    off_dir = tmp_path / "off" / ".github" / "agents"
    on_dir = tmp_path / "on" / ".github" / "agents"

    _run_generate(brief, off_dir, monkeypatch, cost_routing=False, ticket="OFF")
    _run_generate(brief, on_dir, monkeypatch, cost_routing=True, ticket="ON")

    # OFF: no model-routing artifact
    assert not (off_dir / build_team.MODEL_ROUTING_REL_PATH).exists(), (
        "default-OFF must NOT emit references/model-routing.json"
    )
    # ON: artifact present
    on_artifact = on_dir / build_team.MODEL_ROUTING_REL_PATH
    assert on_artifact.exists(), "--cost-routing must emit references/model-routing.json"

    # Representative agent file is byte-identical between OFF and ON runs
    # (the flag must NOT alter rendered agent files).
    for name in ("orchestrator.agent.md", "navigator.agent.md",
                 "ingest-expert.agent.md"):
        off_path = off_dir / name
        on_path = on_dir / name
        if not off_path.exists():
            continue
        assert on_path.read_bytes() == off_path.read_bytes(), (
            f"--cost-routing altered rendered file {name!r} (byte-identical guarantee broken)"
        )


def test_cost_routing_on_emits_valid_drift_excluded_contract(tmp_path, monkeypatch):
    brief = EXAMPLES_DIR / "data-pipeline" / "brief.json"
    if not brief.exists():
        pytest.skip("data-pipeline brief not found")
    out = tmp_path / ".github" / "agents"
    _run_generate(brief, out, monkeypatch, cost_routing=True, ticket="MRON")

    import jsonschema
    contract = json.loads((out / build_team.MODEL_ROUTING_REL_PATH).read_text())
    jsonschema.Draft7Validator(_schema()).validate(contract)
    # Governance agents present → at least one cheap assignment.
    tiers = {a["agent"]: a["tier"] for a in contract["assignments"]}
    assert tiers.get("navigator") == "cheap"
    assert tiers.get("orchestrator") == "primary"

    # Drift-excluded by construction (parity with eval-suite / delivery receipt).
    log = json.loads((out / "references" / "build-log.json").read_text())
    rel = build_team.MODEL_ROUTING_REL_PATH
    assert rel not in log.get("template_hashes", {})
    assert rel not in log.get("file_hashes", {})
    omap = log.get("output_files_map", [])
    paths = {f.get("path") for f in omap} if omap and isinstance(omap[0], dict) else set(omap)
    assert rel not in paths
