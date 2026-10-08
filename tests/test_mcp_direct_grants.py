"""Operator-signed direct-write grants (R5): verified, bound to the brief, capped, operator-owned, runner-enforced."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from agentteams import mcp_direct_grants as G  # noqa: E402
from agentteams import proposal_runner as R  # noqa: E402
from agentteams import proposals as P  # noqa: E402
from agentteams.cli import mcp_grant_commands as C  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    for name in P.KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(P, "_FILE_KEY", None)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(G, "GRANTS_DIR", str(home / "mcp-grants"))
    monkeypatch.setattr(G, "VERIFY_KEYS_DIR", str(home / "verify-keys"))
    keydir = tmp_path / "keys"
    keydir.mkdir(mode=0o700)
    key = keydir / "proposal-ledger.key"
    key.write_text("k" * 64)
    key.chmod(0o600)
    monkeypatch.setattr(P, "KEY_DIR", str(keydir))
    monkeypatch.setenv(P.KEY_FILE_ENV, str(key))


def _brief(**over):
    brief = {"project_name": "P", "project_goal": "Direct grant tests.", "write_policy": "orchestrator-only",
             "agent_policies": {"producer": {"write_scopes": ["src/"]}},
             "proposal_gates": {"pyflakes": {"glob": "src/**.py", "argv": ["/usr/bin/true", "{file}"]}},
             "mcp_grants": {"producer": {"tools": ["write_file", "read_file_hashed"], "approval": "direct"}}}
    brief.update(over)
    return brief


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / "brief.json").write_text(json.dumps(_brief()))
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "b"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root


@pytest.fixture
def operator_key(tmp_path):
    pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                     serialization.NoEncryption())
    path = tmp_path / "operator.pem"
    path.write_bytes(pem)
    return path


@pytest.fixture(autouse=True)
def _operator_flow(monkeypatch):
    """The operator signing flow, with its interactive and location gates stubbed (the digest review is the
    operator's; these tests cover what is signed and checked)."""
    from agentteams.cli import operator_signing as O

    monkeypatch.setattr(O, "presign_integrity_check", lambda: True)
    monkeypatch.setattr(O, "_location_gate", lambda roots, allow: False)
    monkeypatch.setattr(O, "_confirm", lambda digest, confirm: True)


def _sign(root, key, agent="producer", **extra):
    import os

    os.environ["AGENTTEAMS_DECISION_ED25519_KEYFILE"] = str(key)
    try:
        args = argparse.Namespace(sign_mcp_direct_grant=agent, key_id="op1", grant_days=7, max_writes=3,
                                  confirm_review_sha256=None, allow_checkout_signing=False,
                                  description=str(root / "brief.json"), project=str(root), **extra)
        return C.run_sign_mcp_direct_grant(args)
    finally:
        os.environ.pop("AGENTTEAMS_DECISION_ED25519_KEYFILE", None)


def _policy(root):
    return P.load_policy(json.loads((root / "brief.json").read_text()), brief_rel="brief.json")


def test_a_signed_grant_verifies_and_activates(project, operator_key):
    assert _sign(project, operator_key) == 0
    brief = json.loads((project / "brief.json").read_text())
    grants, problems, sha = G.active_grants(project, _policy(project), brief)
    assert list(grants) == ["producer"] and problems == [] and sha
    assert not G.grants_file_for(project).is_relative_to(project)


@pytest.mark.parametrize("field, value", [("max_writes", 400), ("tools", ["write_file"]), ("agent", "reviewer"),
                                          ("expires", "2999-01-01T00:00:00+00:00"), ("approval", "staged")])
def test_any_edited_field_breaks_the_signature_or_binding(project, operator_key, field, value):
    _sign(project, operator_key)
    path = G.grants_file_for(project)
    records = json.loads(path.read_text())
    records[0][field] = value
    path.write_text(json.dumps(records))
    grants, problems, _ = G.active_grants(project, _policy(project), json.loads((project / "brief.json").read_text()))
    assert grants == {} and problems


def test_a_grant_stops_working_when_the_brief_drifts(project, operator_key):
    _sign(project, operator_key)
    drifted = _brief(agent_policies={"producer": {"write_scopes": ["src/", "docs/"]}})
    (project / "brief.json").write_text(json.dumps(drifted))
    grants, problems, _ = G.active_grants(project, _policy(project), drifted)
    assert grants == {} and any("write_scopes no longer matches" in p for p in problems)


def test_unbounded_direct_grants_are_refused_at_signing(project, operator_key):
    (project / "brief.json").write_text(json.dumps(_brief(proposal_gates={})))  # a directory scope and no gate
    assert _sign(project, operator_key) == 1
    assert not G.grants_file_for(project).exists()


def test_the_aggregate_cap_fails_closed(project, operator_key, monkeypatch):
    _sign(project, operator_key)
    monkeypatch.setattr(G, "MAX_ACTIVE", 0)
    grants, problems, _ = G.active_grants(project, _policy(project), json.loads((project / "brief.json").read_text()))
    assert grants == {} and any("exceed the cap" in p for p in problems)


def test_a_grant_that_is_too_long_or_expired_is_refused(project, operator_key):
    _sign(project, operator_key)
    record = json.loads(G.grants_file_for(project).read_text())[0]
    brief = json.loads((project / "brief.json").read_text())
    with pytest.raises(P.ProposalError, match="expired"):
        G.verify(record, _policy(project), brief, now=datetime.now(UTC) + timedelta(days=8))


def test_the_grants_file_must_be_operator_owned(project, operator_key):
    _sign(project, operator_key)
    G.grants_file_for(project).chmod(0o666)
    with pytest.raises(P.ProposalError, match="writable"):
        G.read_grants(project)


def test_revocation_removes_the_grant(project, operator_key):
    _sign(project, operator_key)
    args = argparse.Namespace(revoke_mcp_direct_grant="producer", project=str(project))
    assert C.run_revoke_mcp_direct_grant(args) == 0
    assert G.read_grants(project)[0] == []


# --- the runner -------------------------------------------------------------------------------------------------


def _runner(project):
    return R.Runner(project, project / "brief.json", _policy(project))


def _direct(runner, nonce, content, base):
    art = {"kind": "change-proposal", "dispatch": nonce, "path": "src/a.py", "rationale": "r", "content": content,
           "base_sha256": base}
    rid = R.enqueue(runner.root, {"kind": "apply-direct", "artifact": art, "via_agent": "producer"}, channel="mcp")
    runner.serve_once()
    return R.wait_result(runner.root, rid, timeout=5)


def test_the_runner_honours_a_verified_grant_and_its_write_cap(project, operator_key):
    _sign(project, operator_key)
    runner = _runner(project)
    try:
        assert runner.policy.direct_agents == frozenset({"producer"})
        rid = R.enqueue(project, {"kind": "issue-dispatch", "agent": "producer"})
        runner.serve_once()
        nonce = R.wait_result(project, rid, timeout=5)["result"]["nonce"]
        rows = [json.loads(line) for line in (project / P.DISPATCH_REL).read_text().splitlines()]
        assert any(r.get("max_uses") == G.DIRECT_MAX_USES for r in rows)  # @security C6
        content = "x = 1\n"
        for i in range(3):
            base = hashlib.sha256(content.encode()).hexdigest()
            content = f"x = {i + 2}\n"
            assert _direct(runner, nonce, content, base)["ok"]
        base = hashlib.sha256(content.encode()).hexdigest()
        spent = _direct(runner, nonce, "x = 99\n", base)
        assert not spent["ok"] and "used its 3 writes" in spent["error"]
        assert (project / "src/a.py").read_text() == content
        assert '"grant"' in (project / P.LEDGER_REL).read_text()
    finally:
        runner.close()


def test_without_a_grant_direct_is_refused(project):
    runner = _runner(project)
    try:
        nonce = P.issue_dispatch(project, "producer")
        result = _direct(runner, nonce, "y\n", hashlib.sha256(b"x = 1\n").hexdigest())
        assert not result["ok"] and "no verified direct-write grant" in result["error"]
    finally:
        runner.close()


def test_changing_the_grants_file_needs_a_restart(project, operator_key):
    runner = _runner(project)
    try:
        _sign(project, operator_key)  # added while the runner runs
        with pytest.raises(R.RunnerError, match="direct-grants file changed"):
            runner.serve_once()
    finally:
        runner.close()


def test_claude_edit_tools_are_denied_the_operator_grant_stores():
    from agentteams.frameworks import _sandbox_emit as se

    rules = se.permission_deny_rules("claude")
    for d in (se.MCP_GRANTS_DIR, se.OPERATOR_VERIFY_KEYS_DIR):
        assert f"Edit({d})" in rules and f"Edit({d}/**)" in rules


# --- R5 review conditions -----------------------------------------------------------------------------------


def test_the_signed_payload_keeps_lists_distinct():
    """Condition 1: ["a,b"] and ["a", "b"] no longer sign the same bytes."""
    base = {f: "x" for f in G._FIELDS}
    one = G.signed_values({**base, "write_scopes": ["a,b"]})
    two = G.signed_values({**base, "write_scopes": ["a", "b"]})
    assert one != two and one[0] == G.PURPOSE_TAG


def test_editing_a_gates_definition_breaks_the_grant(project, operator_key):
    """Condition 1: gates are bound by definition, so a gate edited to always pass voids the grant."""
    _sign(project, operator_key)
    weakened = _brief(proposal_gates={"pyflakes": {"glob": "src/**.py", "argv": ["/usr/bin/true", "--always"]}})
    (project / "brief.json").write_text(json.dumps(weakened))
    grants, problems, _ = G.active_grants(project, _policy(project), weakened)
    assert grants == {} and any("gates no longer matches" in p for p in problems)


def test_a_broken_ledger_disables_direct_writes(project, operator_key):
    """Condition 3: counts come from a verified ledger; a tampered one turns direct off rather than reset it."""
    _sign(project, operator_key)
    runner = _runner(project)
    runner.close()
    ledger = project / P.LEDGER_REL
    ledger.write_text(ledger.read_text() + '{"action": "forged"}\n')
    runner = _runner(project)
    try:
        assert runner.direct_grants == {} and any("doesn't verify" in p for p in runner.grant_problems)
    finally:
        runner.close()


def test_a_nonce_with_default_limits_cannot_write_directly(project, operator_key):
    """Condition 4: a nonce issued with the wider default limits is refused for direct writes."""
    _sign(project, operator_key)
    runner = _runner(project)
    try:
        wide = P.issue_dispatch(project, "producer")  # default 24 h / 25 uses
        result = _direct(runner, wide, "y\n", hashlib.sha256(b"x = 1\n").hexdigest())
        assert not result["ok"] and result["error"] == P._CHANNEL_REFUSAL  # uniform: no liveness oracle
    finally:
        runner.close()


def test_apply_direct_requires_a_grant(project):
    """Condition 5."""
    from agentteams import proposal_staging as S

    with pytest.raises(P.ProposalError, match="needs a verified grant"):
        S.apply_direct({"kind": "change-proposal"}, root=project, policy=_policy(project), grant=None)


def test_signing_refuses_without_a_confirmed_digest(project, operator_key, monkeypatch):
    """Through the operator flow: nothing is signed or saved unless the review digest is confirmed."""
    from agentteams.cli import operator_signing as O

    monkeypatch.setattr(O, "_confirm", lambda digest, confirm: False)
    assert _sign(project, operator_key) == 1
    assert not G.grants_file_for(project).exists()
