"""Merge-mode adoption (``--update --adopt-orphans``): the gated, append-only ``agents:`` extension.

Covers the 2026-10-05 ``@security`` design-review conditions: governed workspaces only, a signed
decision (never a waiver) whose ``scope`` is exactly the action and whose ``effect_grants`` equal
exactly this run's ``agents:<slug>`` set, single consumption, append-only + idempotent writes that
leave everything else byte-identical, and rows-only carry-forward.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agentteams import adopted_agents, analyze
from agentteams.cli import adopt_merge_gate as gate
from agentteams.cli import decision_log as dl

pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams.cli import signed_ledger as sl  # noqa: E402

pytestmark = pytest.mark.usefixtures("signing_preapproved")

_KEY_ID = "op-2026"
_ORCH = """---
name: Orchestrator — T
description: "Coordinates."
tools: ['read', 'edit', 'agent']
agents:
  - primary-producer
  - security
model: ["Claude Opus 4.8 (copilot)"]
---

# Orchestrator

<!-- AGENTTEAMS:BEGIN routing_table_rows v=3 -->
| a | `@primary-producer` | b |
<!-- AGENTTEAMS:END routing_table_rows -->

## Project-Specific Notes

Hand-written note that must survive byte-identically.
"""
# The legacy header the gate reads, plus the signing columns (unchained: prev_digest is opt-in).
_COLUMNS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions",
            "conditions_verified", "scope", "derives_from", "effect_class", "effect_grants",
            "effect_targets", "effect_relaxes", "effect_destructive", "effect_cross_repo",
            "effect_bulk", "sig_scheme", "key_id", "signature"]


def _team(root: Path, *, governed: bool = True) -> str:
    refs = root / "references"
    (refs / "authorized-verify-keys").mkdir(parents=True)
    (refs / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": governed}))
    priv = Ed25519PrivateKey.generate()
    (refs / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").write_text(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
    )
    (root / "orchestrator.agent.md").write_text(_ORCH, encoding="utf-8")
    for slug in ("lean-prover", "tactic-planner"):
        (root / f"{slug}.agent.md").write_text(f"---\nname: {slug}\ndescription: Stage.\n---\n")
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _clearance(root: Path, private_pem: str, *, grants: str, scope: str = gate.ACTION,
               action: str = gate.ACTION, sign: bool = True) -> None:
    ancestor = {c: "" for c in _COLUMNS} | {
        "timestamp": "2026-10-01", "action_reviewed": "approved-adoption", "verdict": "PASS",
        "conditions_verified": "verified", "requesting_agent": "security",
    }
    row = {c: "" for c in _COLUMNS} | {
        "timestamp": "2026-10-05", "action_reviewed": action, "verdict": "PASS", "scope": scope,
        "conditions_verified": "verified", "requesting_agent": "security",
        "derives_from": "approved-adoption",
        "effect_grants": grants, "sig_scheme": "ed25519", "key_id": _KEY_ID,
    }
    if sign:
        row["signature"] = sl.ed25519_sign(private_pem, dl._decision_signature_values(row))
    with (root / "references" / "security-decisions.log.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUMNS)
        w.writeheader()
        w.writerows([ancestor, row])


_SLUGS = ["lean-prover", "tactic-planner"]
_GRANTS = "agents:lean-prover;agents:tactic-planner"


# --- the front-matter union --------------------------------------------------------------------


def test_union_is_append_only_and_leaves_everything_else_byte_identical():
    new, added = gate.union_agents(_ORCH, ["tactic-planner", "lean-prover", "security"])
    assert added == ["lean-prover", "tactic-planner"]  # sorted; existing "security" not re-added
    assert new == _ORCH.replace("  - security\n", "  - security\n  - lean-prover\n  - tactic-planner\n")
    assert gate.existing_agents(new) == ["primary-producer", "security", "lean-prover", "tactic-planner"]


def test_union_is_idempotent():
    once, _ = gate.union_agents(_ORCH, _SLUGS)
    twice, added = gate.union_agents(once, _SLUGS)
    assert twice == once and added == []


def test_no_agents_list_means_rows_only(tmp_path):
    claude = "---\nname: Orchestrator\ndescription: d\ntools: Read, Task\n---\nbody\n"
    assert gate.existing_agents(claude) is None
    (tmp_path / "orchestrator.md").write_text(claude)
    assert gate.plan(tmp_path, ["x"], ".md") is None
    with pytest.raises(gate.AdoptMergeError):
        gate.union_agents(claude, ["x"])


def test_malformed_slug_and_symlink_refused(tmp_path):
    with pytest.raises(gate.AdoptMergeError, match="malformed"):
        gate.union_agents(_ORCH, ["Bad Slug"])
    _team(tmp_path)
    (tmp_path / "linked.agent.md").symlink_to(tmp_path / "lean-prover.agent.md")
    with pytest.raises(gate.AdoptMergeError, match="symlink"):
        gate.plan(tmp_path, ["linked"], ".agent.md")


# --- the gate ----------------------------------------------------------------------------------


def test_cleared_run_appends_records_and_consumes(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS)
    applied = gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    assert applied.to_add == _SLUGS and applied.clearance["key_id"] == _KEY_ID
    text = (tmp_path / "orchestrator.agent.md").read_text()
    assert gate.existing_agents(text)[-2:] == _SLUGS
    assert text.split("## Project-Specific Notes")[1] == _ORCH.split("## Project-Specific Notes")[1]
    rec = list(csv.DictReader((tmp_path / gate.RECORD_REL).open()))
    assert rec[0]["added"] == "lean-prover;tactic-planner" and rec[0]["clearance_date"] == "2026-10-05"
    # Idempotent: nothing left to add, so no clearance is needed or spent and the file is unchanged.
    assert gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False) is None
    assert (tmp_path / "orchestrator.agent.md").read_text() == text


def test_clearance_is_single_use(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants="agents:lean-prover")
    gate.run(tmp_path, ["lean-prover"], ".agent.md", dry_run=False)
    with pytest.raises(gate.AdoptMergeError, match="no unconsumed"):
        gate.run(tmp_path, ["tactic-planner"], ".agent.md", dry_run=False)


def test_refused_without_clearance(tmp_path):
    _team(tmp_path)
    with pytest.raises(gate.AdoptMergeError, match="no unconsumed"):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    assert (tmp_path / "orchestrator.agent.md").read_text() == _ORCH


def test_refused_outside_a_governed_workspace(tmp_path):
    pem = _team(tmp_path, governed=False)
    _clearance(tmp_path, pem, grants=_GRANTS)
    with pytest.raises(gate.AdoptMergeError, match="signing-governed"):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)


@pytest.mark.parametrize("grants, needle", [
    ("agents:lean-prover", "missing from clearance: ['agents:tactic-planner']"),          # subset
    (_GRANTS + ";agents:extra", "extra in clearance: ['agents:extra']"),                 # superset
])
def test_effect_grants_must_match_exactly(tmp_path, grants, needle):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=grants)
    with pytest.raises(gate.AdoptMergeError) as exc:
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    assert needle in str(exc.value)
    assert (tmp_path / "orchestrator.agent.md").read_text() == _ORCH


def test_scope_must_be_exactly_the_action(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS, scope="")
    with pytest.raises(gate.AdoptMergeError, match="scope=adopt-orphans-merge"):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)


def test_overwrite_clearance_does_not_authorize_and_vice_versa(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS, action="overwrite", scope="overwrite")
    with pytest.raises(gate.AdoptMergeError):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    _clearance(tmp_path, pem, grants=_GRANTS)
    assert dl._action_matches(gate.ACTION, "overwrite") is False


def test_unsigned_or_tampered_clearance_refused(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS, sign=False)
    with pytest.raises(gate.AdoptMergeError):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    # A signed row for one slug, edited to grant two: the signature no longer verifies.
    _clearance(tmp_path, pem, grants="agents:lean-prover")
    log = tmp_path / "references" / "security-decisions.log.csv"
    log.write_text(log.read_text().replace("agents:lean-prover,", f"{_GRANTS},"))
    with pytest.raises(gate.AdoptMergeError):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    assert (tmp_path / "orchestrator.agent.md").read_text() == _ORCH


def test_dry_run_reports_exact_slugs_without_consuming(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS)
    planned = gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=True)
    report = planned.as_report()
    assert report["agents_to_add"] == _SLUGS and report["clearance"]["status"] == "cleared"
    assert (tmp_path / "orchestrator.agent.md").read_text() == _ORCH
    assert "consumed" not in (tmp_path / "references" / "security-decisions.log.csv").read_text()
    # Without a clearance the dry run still lists the slugs to sign.
    (tmp_path / "references" / "security-decisions.log.csv").unlink()
    report = gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=True).as_report()
    assert report["agents_to_add"] == _SLUGS and report["clearance"]["status"] == "not cleared"


# --- carry-forward never reaches the roster (condition 5) -------------------------------------


def test_rows_only_helper_never_touches_the_roster():
    m = analyze.build_manifest({"project_goal": "x" * 20, "project_name": "P",
                                "components": [{"slug": "alpha", "name": "A"}]})
    roster = list(m["agent_slug_list"])
    adopted_agents.set_adopted_rows(m, ["lean-prover"], {}, agent_dir="a", agent_ext=".md")
    assert m["agent_slug_list"] == roster and "adopted_agents" not in m
    assert "`@lean-prover` *(adopted)*" in m["auto_resolved_placeholders"]["ADOPTED_AGENT_ROUTING_ROWS"]


def test_parser_accepts_update_merge_adopt_and_refuses_bare_adopt():
    from agentteams.cli.parser import _build_parser
    from agentteams.cli.parser_validate import _validate_option_combinations

    parser = _build_parser()
    ok = parser.parse_args(["--description", "b.json", "--update", "--adopt-orphans"])
    _validate_option_combinations(parser, ok)
    bare = parser.parse_args(["--description", "b.json", "--adopt-orphans"])
    with pytest.raises(SystemExit):
        _validate_option_combinations(parser, bare)


@pytest.mark.parametrize("agents_value", [
    "agents: ['primary-producer', 'security']\n",       # flow list
    "agents: []\n",                                      # empty flow list
    "agents:\n  - primary-producer  # main\n  - security\n",  # commented item
])
def test_unsupported_agents_shapes_refuse_rather_than_fall_back(tmp_path, agents_value):
    text = _ORCH.replace("agents:\n  - primary-producer\n  - security\n", agents_value)
    with pytest.raises(gate.AdoptMergeError, match="refusing to edit"):
        gate.existing_agents(text)
    pem = _team(tmp_path)
    (tmp_path / "orchestrator.agent.md").write_text(text)
    _clearance(tmp_path, pem, grants=_GRANTS)
    with pytest.raises(gate.AdoptMergeError):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    assert "consumed" not in (tmp_path / "references" / "security-decisions.log.csv").read_text()


def test_crlf_orchestrator_refused_before_clearance(tmp_path):
    with pytest.raises(gate.AdoptMergeError, match="CRLF"):
        gate.existing_agents(_ORCH.replace("\n", "\r\n"))


def test_backup_failure_does_not_spend_the_clearance(tmp_path, monkeypatch):
    import agentteams.backup as backup

    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS)
    monkeypatch.setattr(backup, "backup_output_dir", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(gate.AdoptMergeError, match="backup"):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    assert "consumed" not in (tmp_path / "references" / "security-decisions.log.csv").read_text()
    assert (tmp_path / "orchestrator.agent.md").read_text() == _ORCH


def test_any_waiver_for_the_action_refuses(tmp_path, monkeypatch):
    from agentteams.cli import security_gate

    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS)
    monkeypatch.setattr(security_gate, "_latest_security_waiver", lambda *a, **k: {"action": gate.ACTION})
    with pytest.raises(gate.AdoptMergeError, match="waiver"):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)


def test_aggregate_cap_of_zero_refuses(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS)
    (tmp_path / "references" / "exception-registry.json").write_text(
        json.dumps({"version": 1, "cap": {"max_active_relaxing": 0}, "exceptions": []})
    )
    with pytest.raises(gate.AdoptMergeError):
        gate.run(tmp_path, _SLUGS, ".agent.md", dry_run=False)
    assert "consumed" not in (tmp_path / "references" / "security-decisions.log.csv").read_text()


def test_trust_root_slug_is_non_eligible(tmp_path):
    pem = _team(tmp_path)
    slug = "security-approvers"
    (tmp_path / f"{slug}.agent.md").write_text(f"---\nname: {slug}\n---\n")
    _clearance(tmp_path, pem, grants=f"agents:{slug}")
    with pytest.raises(gate.AdoptMergeError, match="governance/trust"):
        gate.run(tmp_path, [slug], ".agent.md", dry_run=False)
    assert (tmp_path / "orchestrator.agent.md").read_text() == _ORCH


def test_apply_rechecks_after_clearance(tmp_path):
    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS)
    planned = gate.plan(tmp_path, _SLUGS, ".agent.md")
    gate.authorize(tmp_path, planned, consume=True)
    # The file gains one of the slugs between clearance and write: refuse rather than write a different set.
    orch = tmp_path / "orchestrator.agent.md"
    orch.write_text(gate.union_agents(orch.read_text(), ["lean-prover"])[0])
    with pytest.raises(gate.AdoptMergeError, match="changed after clearance"):
        gate.apply(planned)


def test_adopt_clearance_does_not_clear_overwrite_at_the_gate(tmp_path):
    from agentteams.cli import security_gate

    pem = _team(tmp_path)
    _clearance(tmp_path, pem, grants=_GRANTS)
    with pytest.raises(RuntimeError):
        security_gate._assert_destructive_action_allowed(tmp_path, action="overwrite", consume=False)


def test_symlinked_orchestrator_refused(tmp_path):
    _team(tmp_path)
    real = tmp_path / "real.md"
    real.write_text(_ORCH)
    (tmp_path / "orchestrator.agent.md").unlink()
    (tmp_path / "orchestrator.agent.md").symlink_to(real)
    with pytest.raises(gate.AdoptMergeError, match="symlinked orchestrator"):
        gate.plan(tmp_path, _SLUGS, ".agent.md")
