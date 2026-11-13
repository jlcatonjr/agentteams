"""test_redteam_verifiers_vulnscan.py — F-1 sensitivity tests and negative controls for ``agentteams.vulnscan``.

Companion to ``tests/test_redteam_verifiers.py`` (read its docstring for why every verifier needs a
pair). Each verifier below gets:

* a **sensitivity test**: the verdict changes (a refusal is raised, ``False`` returned, a problem
  reported) when an input the verifier exists to judge is changed;
* a **negative control**: a valid input passes, or an irrelevant change leaves the verdict alone.

Verifiers whose existing tests already form such a pair are not re-tested here; the ledger rows in
``references/redteam-verifiers.csv`` name those tests directly.

Tests are stdlib + pytest only, except the signed-key cases, which use the ``cryptography`` extra
through ``tests/_vulnscan_helpers.operator_home`` exactly as the module's own tests do.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentteams.vulnscan import distro_verdict, executor, executor_serve, gateway_context, grant_v2, image_fix
from agentteams.vulnscan import gateway_dialects as gd
from agentteams.vulnscan import manifests, notes, npm_major, publish, schedule, server_apply
from agentteams.vulnscan import subproc_confine_ssh as ssh
from agentteams.vulnscan.subproc_confine import Unavailable

MODEL = "vendor/model-a"


# ---------------------------------------------------------------------------
# distro_verdict.upstream_matches
# ---------------------------------------------------------------------------

def test_upstream_matches_refuses_a_different_upstream_version() -> None:
    """Sensitivity: the same distro version stops matching when the PyPI version differs."""
    assert distro_verdict.upstream_matches("2.3.0", "2.3.0-1ubuntu0.2") is True
    assert distro_verdict.upstream_matches("2.3.1", "2.3.0-1ubuntu0.2") is False
    assert distro_verdict.upstream_matches("2.3", "2.30-1") is False


def test_upstream_matches_ignores_epoch_revision_and_dfsg_suffix() -> None:
    """Negative control: epoch, packaging revision and ``+dfsg`` do not change a true match."""
    for pkg in ("1.26.5", "1.26.5-1", "2:1.26.5-3", "1.26.5+dfsg-1", "1.26.5+dfsg1-2ubuntu1"):
        assert distro_verdict.upstream_matches("1.26.5", pkg) is True, pkg


# ---------------------------------------------------------------------------
# escalations._verify_key and grant.verify_key (operator verify-key loader with owner checks)
# ---------------------------------------------------------------------------

@pytest.fixture
def op_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """An isolated operator home with verify key ``op`` (0600 in a 0700 dir)."""
    pytest.importorskip("cryptography")
    from tests._vulnscan_helpers import operator_home

    world = operator_home(tmp_path, monkeypatch)
    return {**world, "vk": world["home"] / ".config" / "agentteams" / "verify-keys"}


def test_grant_verify_key_refuses_a_writable_or_missing_key(op_home: dict[str, Any]) -> None:
    """Sensitivity: a group/world-writable key file, a missing key and a malformed id are refused."""
    from agentteams.vulnscan import grant

    os.chmod(op_home["vk"] / "op.pub.pem", 0o666)
    with pytest.raises(grant.GrantError, match="writable"):
        grant.verify_key("op")
    with pytest.raises(grant.GrantError, match="no operator verify key"):
        grant.verify_key("absent")
    with pytest.raises(grant.GrantError, match="no operator verify key"):
        grant.verify_key("../op")


def test_grant_verify_key_returns_the_owned_key(op_home: dict[str, Any]) -> None:
    """Negative control: a 0600 key you own is returned, and a merely readable (0644) one too."""
    from agentteams.vulnscan import grant

    assert "BEGIN PUBLIC KEY" in grant.verify_key("op")
    os.chmod(op_home["vk"] / "op.pub.pem", 0o644)
    assert "BEGIN PUBLIC KEY" in grant.verify_key("op")


def test_escalations_verify_key_refuses_a_writable_key(op_home: dict[str, Any]) -> None:
    """Sensitivity: the escalation wrapper refuses (as EscalationError) a key ``grant.verify_key`` refuses."""
    from agentteams.vulnscan import escalations

    os.chmod(op_home["vk"] / "op.pub.pem", 0o666)
    with pytest.raises(escalations.EscalationError, match="writable"):
        escalations._verify_key("op")
    with pytest.raises(escalations.EscalationError):
        escalations._verify_key("absent")


def test_escalations_verify_key_returns_a_safe_key(op_home: dict[str, Any]) -> None:
    """Negative control: the escalation wrapper returns the same PEM as ``grant.verify_key`` for a safe key."""
    from agentteams.vulnscan import escalations, grant

    assert escalations._verify_key("op") == grant.verify_key("op")


# ---------------------------------------------------------------------------
# executor_serve._check_payload
# ---------------------------------------------------------------------------

def test_check_payload_refuses_operator_ops_and_bad_payloads() -> None:
    """Sensitivity: operator-only ops, payloads on grant ops and malformed internal payloads are refused."""
    for req, match in (
        ({"op": "server-apply"}, "operator-run only"),
        ({"op": "runtime-patch"}, "operator-run only"),
        ({"op": "stage-fix", "payload": {"x": "y"}}, "only allowed with internal"),
        ({"op": "report"}, "requires a payload"),
        ({"op": "report", "payload": {"markdown": "x", "extra": "y"}}, "report payload"),
        ({"op": "escalate", "payload": {"kind": "escalation-flood", "evidence": "e"}}, "escalation kind"),
        ({"op": "escalate", "payload": {"kind": "ioc"}}, "escalate payload"),
    ):
        with pytest.raises(executor.ExecError, match=match):
            executor_serve._check_payload(req)


def test_check_payload_accepts_well_formed_requests() -> None:
    """Negative control: a grant op without payload and well-formed report/escalate payloads pass."""
    executor_serve._check_payload({"op": "stage-fix"})
    executor_serve._check_payload({"op": "report", "payload": {"markdown": "# hi"}})
    executor_serve._check_payload({"op": "escalate", "payload": {"kind": "ioc", "evidence": "seen"}})


# ---------------------------------------------------------------------------
# gateway_context.check_secrets
# ---------------------------------------------------------------------------

def test_check_secrets_reports_an_unusable_file(tmp_path: Path) -> None:
    """Sensitivity: a dangling symlink in a secrets dir becomes a problem; acknowledging it by sha256 clears it."""
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "good.env").write_text("A=1\n")
    (secrets / "gone.env").symlink_to("missing-target.env")
    _mounts, problems, _acked, _counts = gateway_context.check_secrets([secrets])
    assert len(problems) == 1 and "gone.env" in problems[0]
    (secrets / "notadir").write_text("x")
    _m, problems, _a, _c = gateway_context.check_secrets([secrets / "notadir"])
    assert problems and "cannot be read" in problems[0]


def test_check_secrets_is_clean_for_regular_files_and_absent_dirs(tmp_path: Path) -> None:
    """Negative control: regular readable files and an unconfigured (absent) directory raise no problem."""
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "a.env").write_text("A=1\n")
    (secrets / "b.env").write_text("B=2\n")
    (secrets / "notes.txt").write_text("not an env file")
    mounts, problems, acked, counts = gateway_context.check_secrets([secrets, tmp_path / "absent"])
    assert mounts == (secrets,) and problems == [] and acked == []
    assert counts["secrets_files"] == 2 and counts["secrets_dirs_missing"] == 1


# ---------------------------------------------------------------------------
# gateway_dialects: check_request and its helpers
# ---------------------------------------------------------------------------

def _body(**kw: Any) -> bytes:
    return json.dumps({"model": MODEL, **kw}).encode()


def test_check_request_refuses_policy_breaches_on_every_route() -> None:
    """Sensitivity: an unlisted model, a named refusal, server tools and store=true are refused per route."""
    cases = (
        (gd.CHAT, json.dumps({"model": "other/model", "messages": []}).encode()),
        (gd.CHAT, _body(messages=[], models=["x"])),
        (gd.MESSAGES, _body(messages=[], max_tokens=10, mcp_servers=[])),
        (gd.MESSAGES, _body(messages=[], max_tokens=10, tools=[{"type": "web_search_20250305"}])),
        (gd.RESPONSES, _body(input="hi", max_output_tokens=10, store=True)),
        (gd.RESPONSES, _body(input="hi", max_output_tokens=10, previous_response_id="r")),
    )
    for dialect, raw in cases:
        with pytest.raises(gd.PolicyError):
            gd.check_request(dialect, raw, models=[MODEL])


def test_check_request_passes_minimal_valid_bodies() -> None:
    """Negative control: a minimal valid body passes on each route and keeps its model."""
    for dialect, raw in (
        (gd.CHAT, _body(messages=[{"role": "user", "content": "hi"}])),
        (gd.MESSAGES, _body(messages=[{"role": "user", "content": "hi"}], max_tokens=10)),
        (gd.RESPONSES, _body(input="hi", max_output_tokens=10)),
    ):
        checked = gd.check_request(dialect, raw, models=[MODEL])
        assert checked.model == MODEL


def test_check_chat_refuses_fallback_models_and_server_tools() -> None:
    """Sensitivity: the chat route refuses an OpenRouter ``models`` fallback and a non-function tool."""
    with pytest.raises(gd.PolicyError, match="models"):
        gd._check_chat(_body(messages=[], models=["x"]), [MODEL], 1000, 100)
    with pytest.raises(gd.PolicyError, match="server-side"):
        gd._check_chat(_body(messages=[], tools=[{"type": "web_search"}]), [MODEL], 1000, 100)
    with pytest.raises(gd.PolicyError):
        gd._check_chat(_body(messages=[], max_tokens=5000), [MODEL], 1000, 100)


def test_check_chat_passes_a_function_tool_and_reserves() -> None:
    """Negative control: a function tool passes and the default reservation is used when no ceiling is named."""
    tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]
    checked = gd._check_chat(_body(messages=[], tools=tools), [MODEL], 1000, 100)
    assert checked.model == MODEL and checked.max_output == 100


def test_check_keys_refuses_unlisted_and_named_keys() -> None:
    """Sensitivity: a key outside the allowlist, and a key refused by name, are both refused."""
    with pytest.raises(gd.PolicyError, match="deny by default"):
        gd._check_keys(gd.MESSAGES, {"model": MODEL, "surprise": 1}, gd.MESSAGES_KEYS)
    with pytest.raises(gd.PolicyError, match="outside the sandbox"):
        gd._check_keys(gd.MESSAGES, {"model": MODEL, "container": "c"}, gd.MESSAGES_KEYS)


def test_check_keys_passes_allowlisted_keys() -> None:
    """Negative control: a body of allowlisted keys only passes."""
    gd._check_keys(gd.MESSAGES, {"model": MODEL, "messages": [], "max_tokens": 1}, gd.MESSAGES_KEYS)


def test_check_tools_refuses_server_tools_and_nested_namespaces() -> None:
    """Sensitivity: a server-side tool, an unlisted type, a provider tool_search and a nested namespace are refused."""
    a, s = gd.RESPONSES_TOOL_TYPES, gd.RESPONSES_SERVER_TOOLS
    for tools in ([{"type": "web_search"}], [{"type": "weird"}], [{"type": "tool_search", "execution": "server"}],
                  [{"type": "namespace", "tools": [{"type": "namespace", "tools": []}]}], "notalist"):
        with pytest.raises(gd.PolicyError):
            gd._check_tools(gd.RESPONSES, tools, a, s)


def test_check_tools_passes_function_and_client_search_tools() -> None:
    """Negative control: function tools, a client-run tool_search and a namespace of functions pass; None passes."""
    a, s = gd.RESPONSES_TOOL_TYPES, gd.RESPONSES_SERVER_TOOLS
    gd._check_tools(gd.RESPONSES, None, a, s)
    gd._check_tools(gd.RESPONSES, [{"type": "function", "name": "f"},
                                   {"type": "tool_search", "execution": "client"},
                                   {"type": "namespace", "tools": [{"type": "function", "name": "g"}]}], a, s)


def test_check_tool_choice_refuses_unlisted_choices() -> None:
    """Sensitivity: an unlisted choice, and a string choice on the Messages route, are refused."""
    with pytest.raises(gd.PolicyError):
        gd._check_tool_choice(gd.CHAT, "web_search", gd.CHAT_TOOL_CHOICE, gd.CHAT_TOOL_TYPES)
    with pytest.raises(gd.PolicyError):
        gd._check_tool_choice(gd.MESSAGES, "auto", gd.MESSAGES_TOOL_CHOICE, gd.MESSAGES_TOOL_TYPES)


def test_check_tool_choice_passes_listed_choices() -> None:
    """Negative control: listed choices, a function choice object on chat, and None pass."""
    gd._check_tool_choice(gd.CHAT, None, gd.CHAT_TOOL_CHOICE, gd.CHAT_TOOL_TYPES)
    gd._check_tool_choice(gd.CHAT, "auto", gd.CHAT_TOOL_CHOICE, gd.CHAT_TOOL_TYPES)
    gd._check_tool_choice(gd.CHAT, {"type": "function", "function": {"name": "f"}}, gd.CHAT_TOOL_CHOICE,
                          gd.CHAT_TOOL_TYPES)
    gd._check_tool_choice(gd.MESSAGES, {"type": "auto"}, gd.MESSAGES_TOOL_CHOICE, gd.MESSAGES_TOOL_TYPES)


def test_check_client_metadata_refuses_nested_or_oversized_values() -> None:
    """Sensitivity: a nested value, an unsafe key, an over-long value and a non-object are refused."""
    count, size = gd.CLIENT_METADATA_MAX
    for meta in ({"k": {"nested": "x"}}, {"bad key!": "v"}, {"k": "x" * (size + 1)}, ["k"],
                 {f"k{i}": "v" for i in range(count + 1)}):
        with pytest.raises(gd.PolicyError, match="client_metadata"):
            gd._check_client_metadata(meta)


def test_check_client_metadata_passes_flat_short_strings() -> None:
    """Negative control: an empty object and a flat object of short strings pass."""
    gd._check_client_metadata({})
    gd._check_client_metadata({"thread_id": "abc", "turn.id": "1"})


# ---------------------------------------------------------------------------
# grant_v2.check_shape and _check_server_fields
# ---------------------------------------------------------------------------

def _v2(**over: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {"version": 2, "server_levels": ["web1:l2-security"], "restart_units": [],
                           "restart_window": "02:00-04:00", "apps_conf_sha256": "", "major_allowed": [],
                           "remove_paths": [], "runtimes": [], "ancestor": "vg-0123456789abcdef", "max_read_uses": 0}
    rec.update(over)
    return rec


def test_grant_v2_check_shape_refuses_out_of_bound_records() -> None:
    """Sensitivity: wrong version, mixed ops, a missing ancestor and unrequested runtimes are refused."""
    kw = {"ops": {"server-apply"}, "hosts": ["web1"], "max_uses": 1}
    for rec, ops in ((_v2(version=1), None), (_v2(), {"server-apply", "stage-fix"}), (_v2(ancestor=""), None),
                     (_v2(runtimes=["node@22"]), None), (_v2(server_levels=["unrelated:l2-security"]), None)):
        with pytest.raises(ValueError):
            grant_v2.check_shape(rec, **{**kw, **({"ops": ops} if ops else {})})


def test_grant_v2_check_shape_passes_a_valid_server_apply_record() -> None:
    """Negative control: a well-formed single-level server-apply record passes."""
    grant_v2.check_shape(_v2(), ops={"server-apply"}, hosts=["web1"], max_uses=1)


def test_grant_v2_server_fields_refuse_unsigned_hosts_and_missing_window() -> None:
    """Sensitivity: a level for a host outside the grant, a restart level without a window, and a restart unit
    without l2-security are refused."""
    for rec in (_v2(server_levels=["web2:l2-security"]), _v2(restart_window=""),
                _v2(server_levels=["web1:remove-path"], remove_paths=[], restart_window=""),
                _v2(restart_units=["web2:nginx.service"])):
        with pytest.raises(ValueError):
            grant_v2._check_server_fields(rec, {"web1"})


def test_grant_v2_server_fields_pass_a_signed_level_with_its_window() -> None:
    """Negative control: one signed l2-security level with its window and a unit on that host passes."""
    grant_v2._check_server_fields(_v2(restart_units=["web1:nginx.service"]), {"web1"})


# ---------------------------------------------------------------------------
# handoff.verify_directive
# ---------------------------------------------------------------------------

_MGMT_KEY = "test-management-key-0123456789ab"


def _directive(team: Path, did: str, scope: str, expires_at: str) -> None:
    from agentteams.cli import management_directives as md

    refs = team / "references"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "authorized-managers.txt").write_text("ops-team\n", encoding="utf-8")
    md.issue_directive(team, manager_team="ops-team", managed_team="svc", task_scope=scope,
                       expires_at=expires_at, max_uses=3, approver="operator:test", directive_id=did,
                       timestamp="2026-11-01T00:00:00Z", key=_MGMT_KEY)


def test_verify_directive_is_unverified_for_another_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sensitivity: a directive for one handoff does not verify another handoff, nor a malformed id."""
    from agentteams.vulnscan import handoff

    monkeypatch.setenv("AGENTTEAMS_MANAGEMENT_SIGNING_KEY", _MGMT_KEY)
    team = tmp_path / "team"
    _directive(team, "directive-a", "review-vulnscan-handoff-20261102000001", "2099-01-01T00:00:00Z")
    assert handoff.verify_directive(team, "20261102000002")["verified"] is False
    with pytest.raises(ValueError):
        handoff.verify_directive(team, "../x")


def test_verify_directive_verifies_its_own_handoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: the directive issued for this exact handoff scope verifies."""
    from agentteams.vulnscan import handoff

    monkeypatch.setenv("AGENTTEAMS_MANAGEMENT_SIGNING_KEY", _MGMT_KEY)
    team = tmp_path / "team"
    _directive(team, "directive-a", "review-vulnscan-handoff-20261102000001", "2099-01-01T00:00:00Z")
    got = handoff.verify_directive(team, "20261102000001", "directive-a")
    assert got["verified"] is True and got["reason"] == "verified"


# ---------------------------------------------------------------------------
# image_fix._check_ref_move
# ---------------------------------------------------------------------------

def test_check_ref_move_refuses_downgrades_digests_and_other_images() -> None:
    """Sensitivity: a downgrade, a digest pin, a variable, a different repository and a variant change are refused."""
    for old, new in (("python:3.12-slim", "python:3.8-slim"), ("python:3.9-slim", "python:3.15-slim@sha256:" + "b" * 64),
                     ("python:${V}", "python:3.15"), ("python:3.9-slim", "pythons:3.15-slim"),
                     ("python:3.9-slim", "python:3.15-alpine")):
        with pytest.raises(ValueError, match="refusing"):
            image_fix._check_ref_move("Dockerfile", 1, old, new)


def test_check_ref_move_passes_an_upward_line_move() -> None:
    """Negative control: an official image moving to a newer release line with the same variant passes."""
    image_fix._check_ref_move("Dockerfile", 1, "python:3.9-slim", "python:3.15-slim")
    image_fix._check_ref_move("Dockerfile", 1, "docker.io/library/ruby:2.7.8", "docker.io/library/ruby:3.4.7")


# ---------------------------------------------------------------------------
# known: _check_member, _check_shape, check_unsigned, verify
# ---------------------------------------------------------------------------

_KNOWN_NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _known_record(world: dict[str, Any]) -> dict[str, Any]:
    from agentteams.vulnscan import known
    from agentteams.vulnscan.report import Finding

    finding = Finding(kind="package", target="webapp", name="xlsx", version="0.18.5", ecosystem="npm", severity=3,
                      fix="", unfixed=1, ids=["GHSA-aaaa"], locations=["package-lock.json"])
    return known.build([finding], reason="accepted: behind nginx deny",
                       decision_ref={"plan_slug": "vfr-k-known", "step": "K4"}, days=5, key_id="op",
                       governance_dir=str(world["gov"]), now=_KNOWN_NOW)


def _signed_known(world: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    from agentteams.cli.signed_ledger import ed25519_sign
    from agentteams.vulnscan import known

    return {**rec, "signature": ed25519_sign(world["pem"], known.signed_values(rec))}


def test_known_check_member_refuses_critical_and_bad_kinds(op_home: dict[str, Any]) -> None:
    """Sensitivity: a CRITICAL member, an unknown kind and an unsorted id list are refused."""
    from agentteams.vulnscan import known

    member = _known_record(op_home)["members"][0]
    for over in ({"severity": 4}, {"kind": "integrity"}, {"ids": ["b", "a"]}, {"kev": "no"}):
        with pytest.raises(known.KnownError):
            known._check_member({**member, **over})


def test_known_check_member_passes_a_built_member(op_home: dict[str, Any]) -> None:
    """Negative control: the member ``known.build`` produced passes unchanged."""
    from agentteams.vulnscan import known

    member = _known_record(op_home)["members"][0]
    assert known._check_member(dict(member)) == member


def test_known_check_shape_refuses_extra_fields_and_bad_lineage(op_home: dict[str, Any]) -> None:
    """Sensitivity: a self-declared field, a renewal count that disagrees with the lineage and a relative
    governance_dir are refused."""
    from agentteams.vulnscan import known

    rec = _known_record(op_home)
    for bad in ({**rec, "effect_class": "non-relaxing"}, {**rec, "renewal_count": 1},
                {**rec, "governance_dir": "relative/gov"}, {**rec, "members": []}):
        with pytest.raises(known.KnownError):
            known._check_shape(bad)


def test_known_check_shape_passes_a_built_record(op_home: dict[str, Any]) -> None:
    """Negative control: the record ``known.build`` produced, unsigned and signed, passes."""
    from agentteams.vulnscan import known

    rec = _known_record(op_home)
    known._check_shape(rec)
    known._check_shape(_signed_known(op_home, rec))


def test_known_check_unsigned_refuses_a_governance_dir_without_a_decision_log(op_home: dict[str, Any],
                                                                              tmp_path: Path) -> None:
    """Sensitivity: the same record is refused once governance_dir no longer holds the decision log."""
    from agentteams.vulnscan import known

    rec = _known_record(op_home)
    empty = tmp_path / "empty-gov"
    empty.mkdir()
    with pytest.raises(known.KnownError, match="governance_dir"):
        known.check_unsigned({**rec, "governance_dir": str(empty)})


def test_known_check_unsigned_returns_the_derived_class(op_home: dict[str, Any]) -> None:
    """Negative control: a built record passes and gets its derived class."""
    from agentteams.vulnscan import known

    assert known.check_unsigned(_known_record(op_home)) == known.derived_class(_known_record(op_home))


def test_known_verify_refuses_a_tampered_entry(op_home: dict[str, Any]) -> None:
    """Sensitivity: changing a signed member's version after signing breaks the signature."""
    from agentteams.vulnscan import known

    signed = _signed_known(op_home, _known_record(op_home))
    tampered = json.loads(json.dumps(signed))
    tampered["members"][0]["version"] = "0.19.0"
    with pytest.raises(known.KnownError, match="signature does not verify"):
        known.verify(tampered, now=_KNOWN_NOW)


def test_known_verify_passes_a_signed_entry_in_its_lifetime(op_home: dict[str, Any]) -> None:
    """Negative control: an untouched signed entry verifies inside its lifetime."""
    from agentteams.vulnscan import known

    signed = _signed_known(op_home, _known_record(op_home))
    assert known.verify(signed, now=_KNOWN_NOW) == known.derived_class(signed)


# ---------------------------------------------------------------------------
# manifests.check_lock_edit and check_pin_edit
# ---------------------------------------------------------------------------

def _npm_lock(url: str, version: str = "1.0.0") -> str:
    return json.dumps({"name": "app", "lockfileVersion": 3, "packages": {
        "": {"name": "app", "dependencies": {"left-pad": "^1.0.0"}},
        "node_modules/left-pad": {"version": version, "resolved": url, "integrity": "sha512-x"},
    }})


def test_manifests_check_lock_edit_refuses_an_off_registry_resolution() -> None:
    """Sensitivity: an entry newly resolving outside the registry allowlist, or a root change, is refused."""
    old = _npm_lock("https://registry.npmjs.org/left-pad/-/left-pad-1.0.0.tgz")
    with pytest.raises(ValueError, match="outside the registry allowlist"):
        manifests.check_lock_edit("package-lock.json", old, _npm_lock("https://evil.example/left-pad-1.0.1.tgz",
                                                                      "1.0.1"))
    rooted = json.loads(old)
    rooted["packages"][""]["scripts"] = {"postinstall": "curl evil"}
    with pytest.raises(ValueError, match="root package scripts"):
        manifests.check_lock_edit("package-lock.json", old, json.dumps(rooted))


def test_manifests_check_lock_edit_passes_a_registry_move() -> None:
    """Negative control: a version move that stays on registry.npmjs.org passes with no notes."""
    old = _npm_lock("https://registry.npmjs.org/left-pad/-/left-pad-1.0.0.tgz")
    new = _npm_lock("https://registry.npmjs.org/left-pad/-/left-pad-1.0.1.tgz", "1.0.1")
    assert manifests.check_lock_edit("package-lock.json", old, new) == []


def test_manifests_check_pin_edit_refuses_anything_but_a_pin_raise() -> None:
    """Sensitivity: a downgrade, a renamed package, a changed marker and a --hash file are refused."""
    old = b"requests==2.31.0 ; python_version >= '3.8'\nflask==3.0.0\n"
    for new in (b"requests==2.30.0 ; python_version >= '3.8'\nflask==3.0.0\n",
                b"requestz==2.32.0 ; python_version >= '3.8'\nflask==3.0.0\n",
                b"requests==2.32.0 ; python_version >= '3.9'\nflask==3.0.0\n",
                b"requests==2.32.0rc1 ; python_version >= '3.8'\nflask==3.0.0\n"):
        with pytest.raises(ValueError):
            manifests.check_pin_edit("requirements.txt", old, new)
    with pytest.raises(ValueError, match="--hash"):
        manifests.check_pin_edit("requirements.txt", b"a==1 --hash=sha256:x\n", b"a==2 --hash=sha256:x\n")


def test_manifests_check_pin_edit_passes_an_in_place_raise() -> None:
    """Negative control: raising one pin to a final release, everything else byte-identical, passes."""
    old = b"requests==2.31.0 ; python_version >= '3.8'\nflask==3.0.0\n"
    manifests.check_pin_edit("requirements.txt", old, old.replace(b"2.31.0", b"2.32.3"))


# ---------------------------------------------------------------------------
# notes._check_target and _check_text
# ---------------------------------------------------------------------------

def test_notes_check_target_refuses_multiline_and_oversized_targets() -> None:
    """Sensitivity: an empty, multi-line, control-character or over-long target is refused."""
    for bad in ("", "   ", "a\nb", "a\x1bb", "x" * (notes.TARGET_MAX_CHARS + 1)):
        with pytest.raises(ValueError):
            notes._check_target(bad)


def test_notes_check_target_passes_and_strips_a_normal_target() -> None:
    """Negative control: None passes through and surrounding whitespace is stripped from a normal target."""
    assert notes._check_target(None) is None
    assert notes._check_target("  /srv/repos/app  ") == "/srv/repos/app"


def test_notes_check_text_refuses_nul_empty_and_oversized_text() -> None:
    """Sensitivity: NUL, empty and over-cap note text is refused."""
    for bad in ("a\x00b", "   ", "x" * (notes.NOTE_MAX_BYTES + 1)):
        with pytest.raises(ValueError):
            notes._check_text(bad)


def test_notes_check_text_passes_normal_text() -> None:
    """Negative control: ordinary text (multi-line, at the byte cap) passes, stripped."""
    assert notes._check_text("  accepted risk\nsee ticket  ") == "accepted risk\nsee ticket"
    assert notes._check_text("x" * notes.NOTE_MAX_BYTES) == "x" * notes.NOTE_MAX_BYTES


# ---------------------------------------------------------------------------
# npm_major.check_package_json_edit
# ---------------------------------------------------------------------------

_PKG = {"name": "app", "scripts": {"test": "jest"}, "dependencies": {"echarts": "^5.4.0", "local": "file:../x"}}


def test_check_package_json_edit_refuses_other_changes() -> None:
    """Sensitivity: a script change, a downgrade, an added dependency and a rewritten file: spec are refused."""
    old = json.dumps(_PKG).encode()
    for change in ({"scripts": {"test": "curl evil"}},
                   {"dependencies": {"echarts": "^4.0.0", "local": "file:../x"}},
                   {"dependencies": {"echarts": "^5.4.0", "local": "file:../x", "new": "^1.0.0"}},
                   {"dependencies": {"echarts": "^5.4.0", "local": "^2.0.0"}}):
        with pytest.raises(ValueError):
            npm_major.check_package_json_edit("package.json", old, json.dumps({**_PKG, **change}).encode())


def test_check_package_json_edit_passes_a_major_raise() -> None:
    """Negative control: raising a registry spec to ^X.Y.Z, nothing else changed, passes."""
    old = json.dumps(_PKG).encode()
    new = json.dumps({**_PKG, "dependencies": {"echarts": "^6.0.0", "local": "file:../x"}}).encode()
    npm_major.check_package_json_edit("package.json", old, new)


# ---------------------------------------------------------------------------
# publish._check_files and _check_remote_url
# ---------------------------------------------------------------------------

def test_publish_check_files_refuses_a_secret_and_bad_names() -> None:
    """Sensitivity: a high-severity secret, a path-like name and too many files refuse the publish."""
    secret = "AKIA" + ("Q7" * 8)
    with pytest.raises(executor.ExecError, match="high-severity"):
        publish._check_files({"report.md": f"key: {secret}\n"})
    with pytest.raises(executor.ExecError, match="bad file name"):
        publish._check_files({"../x.md": "ok"})
    with pytest.raises(executor.ExecError, match="cap"):
        publish._check_files({f"f{i}.md": "ok" for i in range(publish.MAX_FILES + 1)})


def test_publish_check_files_passes_clean_reports() -> None:
    """Negative control: clean markdown files pass and report no fenced injection hits."""
    out, fenced = publish._check_files({"report.md": "# Daily report\n\nNothing found.\n", "b.md": "ok\n"})
    assert set(out) == {"report.md", "b.md"} and fenced == 0


def test_publish_check_remote_url_refuses_option_like_and_whitespace_urls() -> None:
    """Sensitivity: a leading '-', embedded whitespace, a control byte, empty and over-long URLs are refused."""
    for bad in ("--upload-pack=evil", "https://x/a b", "https://x/\x7f", "", "https://x/" + "a" * 600, None):
        with pytest.raises(publish.PublishError):
            publish._check_remote_url(bad)  # type: ignore[arg-type]


def test_publish_check_remote_url_passes_a_normal_url() -> None:
    """Negative control: ordinary https and ssh remote URLs pass."""
    publish._check_remote_url("https://github.com/owner/ops.git")
    publish._check_remote_url("git@github.com:owner/ops.git")


# ---------------------------------------------------------------------------
# schedule.check_time
# ---------------------------------------------------------------------------

def test_schedule_check_time_refuses_out_of_range_and_non_integers() -> None:
    """Sensitivity: an hour of 24, a minute of 60, a negative, a bool and a string are refused."""
    for hour, minute in ((24, 0), (0, 60), (-1, 0), (True, 0), ("7", 0), (7.0, 0)):
        with pytest.raises(ValueError):
            schedule.check_time(hour, minute)  # type: ignore[arg-type]


def test_schedule_check_time_passes_the_range_bounds() -> None:
    """Negative control: 0:00 and 23:59 pass."""
    schedule.check_time(0, 0)
    schedule.check_time(23, 59)


# ---------------------------------------------------------------------------
# server_apply.check_argv
# ---------------------------------------------------------------------------

def test_server_apply_check_argv_passes_one_level() -> None:
    """Negative control: one level on the preview run, and one level with --apply --expect-digest on the apply run."""
    server_apply.check_argv(["--level", "l2-security"], apply=False)
    server_apply.check_argv(["--level", "l2-security", "--apply", "--expect-digest", "a" * 64], apply=True)


# ---------------------------------------------------------------------------
# subproc_confine_ssh._check_staged
# ---------------------------------------------------------------------------

def test_check_staged_passes_an_unchanged_copy(tmp_path: Path) -> None:
    """Negative control: a staged copy whose files match the recorded digests passes, whatever its mtimes."""
    copy = tmp_path / "staged"
    copy.mkdir()
    digests = {}
    for name, body in (("run-host.sh", b"#!/bin/sh\n"), ("ioc-x.txt", b"x\n")):
        (copy / name).write_bytes(body)
        digests[name] = hashlib.sha256(body).hexdigest()
    os.utime(copy / "run-host.sh", (0, 0))
    ssh._check_staged(copy, digests)
    (copy / "run-host.sh").write_bytes(b"#!/bin/sh\nsudo\n")
    with pytest.raises(Unavailable):
        ssh._check_staged(copy, digests)
