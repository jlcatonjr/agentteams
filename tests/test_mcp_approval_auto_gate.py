"""WP-C2: the render-time gate for ``codex:wasm-auto-approve`` (the sixth sandbox-relaxation kind, Rule 15).

Turning on the opt-in host feature makes the shared ``agentteams_wasm`` Codex table run ``run_wasm`` without the
per-call approval prompt. That is the ``mcp-approval-auto:codex`` relaxation: in a governed workspace it needs the
operator's Ed25519-signed decision, exactly like the other five kinds; ungoverned, it is a notice only (WP-C as
shipped). Ed25519 verification is stubbed (``stub_ed25519``) as in ``test_sandbox_relaxation_gate.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.cli import exception_registry as er
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.cli.effect_classifier import is_sandbox_relaxation, requires_operator_signature
from agentteams.cli.management_directives import scope_is_allowed
from agentteams.cli.os_sandbox_switch import engage_relaxation_gate, gate_exceptions
from agentteams.cli.sandbox_effects import MCP_APPROVAL_AUTO, names_sandbox_relaxation, relaxation_kind_of
from agentteams.cli.write_root_policy import warn_sandbox_relaxation
from agentteams.sandbox_state import (
    SandboxExceptions,
    SandboxState,
    effective_sandbox_state,
    mcp_approval_auto,
    previous_sandbox_exceptions,
)
from agentteams.sandbox_state import _BUILD_LOG_REL
from tests._sandbox_gate_helpers import _KEY_ID, _log, _row, _team

_T0 = datetime(2026, 11, 9, tzinfo=UTC)
_ACTION = "mcp-approval-auto:codex"
_TOKEN = "codex:wasm-auto-approve"
_REPO = Path(__file__).resolve().parent.parent
_BRIEF = json.loads((_REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))


def _st(*, wasm: bool = True) -> SandboxState:
    return SandboxState(os=True, os_reason="brief", wasm=wasm, wasm_reason="brief" if wasm else "default-off",
                        wasm_languages=("python",) if wasm else (), wasm_grants={}, model_substrate=None)


def _exc(auto: bool) -> SandboxExceptions:
    return SandboxExceptions(mcp_approval_auto=auto)


def _plan(old: SandboxExceptions | None, new: SandboxExceptions, framework_id: str = "codex") -> list:
    return gate.plan_relaxations(_st(), _st(), framework_id=framework_id, profile_would_emit=True,
                                 exceptions=(old, new))


def _manifest(*, token: bool = True, framework: str = "codex", wasm: bool = True) -> dict:
    block = {"enabled": wasm, "native_exec_keep": []} if wasm else {"enabled": False}
    m = analyze.build_manifest({**_BRIEF, "wasm_sandbox": block}, framework=framework)
    m["host_features"] = [*(m.get("host_features") or []), *([_TOKEN] if token else [])]
    return m


# --- vocabulary, classifier, denylist ------------------------------------------------------------------------------


def test_sixth_kind_and_its_action_id():
    assert MCP_APPROVAL_AUTO == "mcp-approval-auto"
    assert relaxation_kind_of(_ACTION) == MCP_APPROVAL_AUTO
    assert gate.Relaxation(MCP_APPROVAL_AUTO, "codex").action == _ACTION


@pytest.mark.parametrize("text", [_ACTION, "codex-wasm-auto-approve:agentteams", "codex:wasm-auto-approve",
                                  "MCP approval auto", "auto_approval for run_wasm"])
def test_free_text_names_the_relaxation(text):
    assert names_sandbox_relaxation(text)


def test_classifier_and_directive_denylist_recognize_the_kind():
    row = {"action_reviewed": "routine-label", "effect_relaxes": _ACTION}
    assert is_sandbox_relaxation(row, kind="decision") and requires_operator_signature(row, kind="decision")
    assert requires_operator_signature({"scope": "codex-wasm-auto-approve:agentteams"}, kind="decision")
    assert not scope_is_allowed(_ACTION)
    assert not scope_is_allowed("enable codex wasm-auto-approve")


# --- the recorded state ---------------------------------------------------------------------------------------------


def test_record_is_backward_compatible():
    assert "mcp_approval_auto" not in SandboxExceptions().to_record()  # off is not written: old records unchanged
    assert _exc(True).to_record()["mcp_approval_auto"] == "on"
    assert SandboxExceptions.from_record(_exc(True).to_record()) == _exc(True)
    old = {"native_exec_keep": [], "wasm_programs": [], "os_programs": []}  # a pre-WP-C2 record
    assert SandboxExceptions.from_record(old) == _exc(False)
    assert SandboxExceptions.from_record({**old, "mcp_approval_auto": "off"}) == _exc(False)
    for bad in (True, "ON", "yes", None):
        assert SandboxExceptions.from_record({**old, "mcp_approval_auto": bad}) is None


def test_record_validates_against_the_team_manifest_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_REPO / "agentteams" / "schemas" / "team-manifest.schema.json").read_text())["properties"]
    for value in (_exc(True), _exc(False)):
        jsonschema.Draft7Validator(schema["sandbox_exceptions"]).validate(value.to_record())
    jsonschema.Draft7Validator(schema["sandbox_relaxations"]).validate(
        [{"kind": MCP_APPROVAL_AUTO, "action": _ACTION, "authorization": "ed25519-decision"}])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema["sandbox_exceptions"]).validate(
            {"native_exec_keep": [], "wasm_programs": [], "mcp_approval_auto": True})


def test_effective_only_for_codex_with_wasm_on_and_the_token():
    assert mcp_approval_auto(_manifest())
    assert not mcp_approval_auto(_manifest(token=False))
    assert not mcp_approval_auto(_manifest(wasm=False))
    assert not mcp_approval_auto(_manifest(framework="claude"))


def test_gate_inputs_carry_it_and_engage_the_gate(tmp_path):
    m = _manifest()
    assert gate_exceptions(m, effective_sandbox_state(m), tmp_path, None) == (None, _exc(True))
    m = _manifest(token=False)
    assert gate_exceptions(m, effective_sandbox_state(m), tmp_path, None) is None  # byte-identical build-log


# --- planning -------------------------------------------------------------------------------------------------------


def test_plan_new_continuing_and_off():
    assert [(r.action, r.standing, r.continuing) for r in _plan(None, _exc(True))] == [(_ACTION, True, False)]
    assert [(r.action, r.continuing) for r in _plan(_exc(False), _exc(True))] == [(_ACTION, False)]
    assert [(r.action, r.continuing) for r in _plan(_exc(True), _exc(True))] == [(_ACTION, True)]
    assert _plan(_exc(True), _exc(False)) == [] and _plan(None, _exc(False)) == []


@pytest.mark.parametrize("damage", ["bad-json", "symlink", "malformed-record", "non-dict"])
def test_unusable_previous_build_log_plans_it_as_new_and_refuses(tmp_path, damage):
    """@security F2 (2026-11-09): a corrupt, symlinked or malformed build log never makes auto-approval standing."""
    team = _team(tmp_path)
    log = team / _BUILD_LOG_REL
    log.parent.mkdir(parents=True, exist_ok=True)
    if damage == "bad-json":
        log.write_text("{not json", encoding="utf-8")
    elif damage == "symlink":
        real = tmp_path / "elsewhere.json"
        real.write_text(json.dumps({"sandbox_exceptions": {"mcp_approval_auto": "on"}}), encoding="utf-8")
        log.symlink_to(real)
    elif damage == "malformed-record":
        log.write_text(json.dumps({"sandbox_exceptions": {"mcp_approval_auto": ["on"], "keep": 7}}), encoding="utf-8")
    else:
        log.write_text(json.dumps({"sandbox_exceptions": "on"}), encoding="utf-8")
    previous = previous_sandbox_exceptions(team)
    assert previous is None or previous.mcp_approval_auto is False
    plan = _plan(previous, _exc(True))
    assert [(r.action, r.continuing) for r in plan] == [(_ACTION, False)]
    with pytest.raises(gate.SandboxRelaxationError, match="this workspace is governed"):
        gate.authorize(team, plan, consume=True)


# --- ungoverned: unchanged from WP-C (a notice, never a refusal) -----------------------------------------------------


@pytest.mark.parametrize("governed", [None, False])
def test_ungoverned_is_notice_only_and_writes_nothing(tmp_path, governed, capsys):
    team = _team(tmp_path, governed=governed)
    assert gate.authorize(team, _plan(None, _exc(True)), consume=True) == [
        {"kind": MCP_APPROVAL_AUTO, "action": _ACTION, "authorization": "notice-only"}]
    assert not (team / er.EXCEPTION_REGISTRY_REL).exists()
    [line] = warn_sandbox_relaxation(_st(), _st(), framework_id="codex", exceptions=(None, _exc(True)))
    assert "SANDBOX RELAXATION" in line and "agentteams_wasm" in line and _TOKEN in line
    assert warn_sandbox_relaxation(_st(), _st(), framework_id="codex", exceptions=(_exc(True), _exc(True))) == []


# --- governed -------------------------------------------------------------------------------------------------------


def test_governed_without_a_row_refuses_naming_the_action_and_how_to_sign(tmp_path):
    team = _team(tmp_path)
    with pytest.raises(gate.SandboxRelaxationError) as err:
        gate.authorize(team, _plan(None, _exc(True)), consume=True)
    message = str(err.value)
    assert f"'{_ACTION}' relaxes a sandbox and this workspace is governed" in message
    assert f"--sign-decision): action_reviewed={_ACTION}, scope={_ACTION}, effect_relaxes={_ACTION}" in message
    assert f"drop the {_TOKEN} host feature" in message


def test_hmac_only_row_is_valid_but_insufficient(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row(_ACTION, scheme=""))
    with pytest.raises(gate.SandboxRelaxationError, match="INSUFFICIENT"):
        gate.authorize(team, _plan(None, _exc(True)), consume=True)


@pytest.mark.parametrize("halted", [_ACTION, MCP_APPROVAL_AUTO, "op-root"])
def test_an_unretracted_halt_refuses(tmp_path, stub_ed25519, halted):
    """A HALT on the instance, the family, or the signed row's lineage ancestor blocks it (C-2)."""
    team = _team(tmp_path)
    ancestor = {"timestamp": "2026-10-02", "requesting_agent": "security", "action_reviewed": "op-root",
                "verdict": "PASS", "conditions_verified": "verified"}
    _log(team, ancestor, _row(halted, verdict="HALT", sign=None, derives=""), _row(_ACTION, derives="op-root"))
    with pytest.raises(gate.SandboxRelaxationError, match="HALT"):
        gate.authorize(team, _plan(None, _exc(True)), consume=True)


@pytest.mark.parametrize("kwargs,match", [
    ({"scope": "codex-wasm-auto-approve:agentteams"}, f"scope={_ACTION} exactly"),
    ({"relaxes": "codex-mcp-approval-prompt:agentteams_wasm"}, f"effect_relaxes={_ACTION} exactly"),
    ({"relaxes": f"{_ACTION};sandbox-os-off:codex"}, f"effect_relaxes={_ACTION} exactly"),
])
def test_the_repo_shipping_decision_is_not_a_consumer_authorization(tmp_path, stub_ed25519, kwargs, match):
    """The operator's row that ships the feature (scope codex-wasm-auto-approve:agentteams) does not clear a
    consumer's render: the row must name the gate's action id exactly, in scope and in effect_relaxes."""
    team = _team(tmp_path)
    _log(team, _row(_ACTION, **kwargs))
    with pytest.raises(gate.SandboxRelaxationError, match=match):
        gate.authorize(team, _plan(None, _exc(True)), consume=True)


def test_signed_row_allows_registers_continues_lapses_and_retires(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    [rec] = gate.authorize(team, _plan(None, _exc(True)), consume=True, now=_T0)
    assert rec["authorization"] == "ed25519-decision" and rec["key_id"] == _KEY_ID
    assert rec["exception_id"].startswith(f"{_ACTION}@stub")
    assert "yes" in log.read_text()  # consumed
    [entry] = er.list_exceptions(team)
    assert entry["action"] == _ACTION and entry["kind"] == MCP_APPROVAL_AUTO and entry["status"] == "active"
    assert entry["effect"] == {"relaxes": [_ACTION]} and entry["lineage"] == ["approved-baseline"]
    assert len(entry["decision_sha256"]) == 64
    assert datetime.fromisoformat(entry["expires"]) == _T0 + timedelta(days=gate.SANDBOX_EXCEPTION_TTL_DAYS)

    later = _plan(_exc(True), _exc(True))
    [cont] = gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=30))
    assert cont == {"kind": MCP_APPROVAL_AUTO, "action": _ACTION, "authorization": "active-exception",
                    "exception_id": entry["id"]}
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=91))

    # Another framework's render, or a render that keeps the token, retires nothing.
    assert gate.retire_on_tighten(team, _st(), _st(), framework_id="claude", exceptions=(_exc(True), _exc(False)),
                                  dry_run=False) == []
    assert gate.retire_on_tighten(team, _st(), _st(), framework_id="codex", exceptions=(_exc(True), _exc(True)),
                                  dry_run=False) == []
    # Dropping the token retires the standing exception.
    assert gate.retire_on_tighten(team, _st(), _st(), framework_id="codex", exceptions=(_exc(True), _exc(False)),
                                  dry_run=True) == [entry["id"]]
    assert er.list_exceptions(team)[0]["status"] == "active"  # dry run wrote nothing
    assert gate.retire_on_tighten(team, _st(), _st(), framework_id="codex", exceptions=(_exc(True), _exc(False)),
                                  dry_run=False) == [entry["id"]]
    assert er.list_exceptions(team)[0]["status"] == "sunset"


def test_a_forged_on_record_does_not_make_it_standing(tmp_path, stub_ed25519):
    """A previous record claiming auto-approval was already on carries nothing over without a bound exception."""
    team = _team(tmp_path)
    _log(team)
    with pytest.raises(gate.SandboxRelaxationError, match="no exception was ever recorded"):
        gate.authorize(team, _plan(_exc(True), _exc(True)), consume=True, now=_T0)


# --- end to end through the render's gate engagement -----------------------------------------------------------------


def _render(team: Path, root: Path, **kwargs) -> dict:
    m = _manifest(**kwargs)
    engage_relaxation_gate(m, effective_sandbox_state(m), "codex", root, team_dir=team, dry_run=False)
    (team / "references" / "build-log.json").write_text(json.dumps(
        {k: m[k] for k in ("sandbox_state", "sandbox_exceptions") if k in m}))
    return m


def test_render_gate_refuses_then_allows_carries_over_and_retires(tmp_path, stub_ed25519):
    team = _team(tmp_path / "proj")
    root = tmp_path / "proj"
    m = _render(team, root, token=False)  # WP-C off: nothing is relaxed and no gate input is recorded
    assert m["sandbox_relaxations"] == [] and previous_sandbox_exceptions(team) is None

    with pytest.raises(gate.SandboxRelaxationError, match=_ACTION):
        _render(team, root)
    assert previous_sandbox_exceptions(team) is None  # the refused render recorded nothing

    _log(team, _row(_ACTION))
    m = _render(team, root)
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [(_ACTION, "ed25519-decision")]
    assert m["sandbox_exceptions"]["mcp_approval_auto"] == "on"

    m = _render(team, root)
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [(_ACTION, "active-exception")]

    m = _render(team, root, token=False)  # dropping the token retires the exception
    assert m["sandbox_relaxations"] == [] and "mcp_approval_auto" not in m["sandbox_exceptions"]
    assert [e["status"] for e in er.list_exceptions(team)] == ["sunset"]


def test_render_gate_ungoverned_is_unchanged(tmp_path):
    team = _team(tmp_path / "proj", governed=False)
    m = _render(team, tmp_path / "proj")
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [(_ACTION, "notice-only")]
    assert not (team / er.EXCEPTION_REGISTRY_REL).exists()
