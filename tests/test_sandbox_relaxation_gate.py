"""The sandbox-relaxation gate (P15 D7): Ed25519 decision, exact effect, lineage, HALTs, cap, sunset, notice-only.

Ed25519 verification is stubbed (a payload hash) so the gate's own logic runs without the ``signing`` extra; one
end-to-end test signs for real and skips visibly when ``cryptography`` is absent.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentteams.cli import decision_log as dl
from agentteams.cli import exception_registry as er
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.cli import security_gate
from agentteams.cli.sandbox_effects import SANDBOX_RELAXATION_KINDS
from agentteams.sandbox_state import SandboxExceptions, SandboxState
from tests._sandbox_gate_helpers import _COLUMNS, _KEY_ID, _log, _row, _stub_sig, _team  # noqa: F401

_T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _st(*, os: bool = True, wasm: bool = False, grants: dict | None = None, substrate: str | None = None,
        os_reason: str = "brief") -> SandboxState:
    return SandboxState(os=os, os_reason=os_reason, wasm=wasm, wasm_reason="brief" if wasm else "default-off",
                        wasm_languages=("python",) if wasm else (), wasm_grants=grants or {},
                        model_substrate=substrate)


def _g(*reads: str, scratch: bool = False):
    mounts = [SimpleNamespace(host=f"/p/{r}", guest=f"/project/{r}", mode="ro") for r in reads]
    if scratch:
        mounts.append(SimpleNamespace(host="/p/s", guest="/scratch", mode="rw"))
    return SimpleNamespace(mounts=tuple(mounts), limits=None)


def _os_off() -> list[gate.Relaxation]:
    return gate.plan_relaxations(_st(), _st(os=False), framework_id="claude", profile_would_emit=True)


def _registry(team: Path) -> list[dict]:
    return er.list_exceptions(team)


# --- planning ----------------------------------------------------------------------------------------


def test_plan_covers_each_transition():
    old = _st(wasm=True, grants={"analyst": _g("data")}, substrate="on")
    new = _st(os=False, wasm=True, grants={"analyst": _g("data", "docs", scratch=True)}, substrate="off")
    exc = (SandboxExceptions(wasm_programs=frozenset({"runner-a", "runner-b"})),
           SandboxExceptions(native_exec_keep=frozenset({"git-operations"}), wasm_programs=frozenset({"runner-b"}),
                             os_programs=frozenset({"runner-a", "runner-c"})))
    got = {(r.action, r.grants, r.standing, r.continuing)
           for r in gate.plan_relaxations(old, new, framework_id="claude", profile_would_emit=True, exceptions=exc)}
    assert got == {
        ("sandbox-os-off:claude", (), True, False),
        ("sandbox-wasm-off:model-substrate", (), False, False),
        ("wasm-grant-widen:claude", ("wasm:analyst:read:docs", "wasm:analyst:scratch"), True, False),
        ("wasm-grant-widen:claude", ("wasm:analyst:read:data",), True, True),
        ("native-exec-keep:claude", ("native-exec:git-operations",), True, False),
        ("wasm-to-os-switch:runner-a", (), True, False),
        ("wasm-to-os-switch:runner-c", (), True, True),
    }


def test_wasm_off_is_not_standing_and_grants_vanish_with_it():
    got = gate.plan_relaxations(_st(wasm=True, grants={"a": _g("x")}), _st(), framework_id="goose",
                                profile_would_emit=True)
    assert [(r.action, r.standing) for r in got] == [("sandbox-wasm-off:goose", False)]


def test_os_off_cases():
    plan = gate.plan_relaxations
    assert [r.continuing for r in plan(None, _st(os=False), framework_id="claude", profile_would_emit=True)] == [False]
    assert plan(None, _st(os=False, os_reason="profile:cooperative"), framework_id="claude",
                profile_would_emit=True) == []
    assert plan(None, _st(os=False), framework_id="claude", profile_would_emit=False) == []
    assert [r.continuing for r in plan(_st(os=False), _st(os=False), framework_id="claude",
                                       profile_would_emit=True)] == [True]
    assert plan(_st(os=False), _st(os=False), framework_id="claude", profile_would_emit=False) == []


def test_fixed_exemption_is_never_a_keep_relaxation():
    # P13 never puts orchestrator / the team builder in native_exec_keep, so an empty set plans nothing.
    exc = (SandboxExceptions(), SandboxExceptions())
    assert gate.plan_relaxations(_st(wasm=True), _st(wasm=True), framework_id="claude", profile_would_emit=True,
                                 exceptions=exc) == []


def test_keep_only_counts_while_wasm_is_on():
    exc = (None, SandboxExceptions(native_exec_keep=frozenset({"a"})))
    assert gate.plan_relaxations(_st(), _st(), framework_id="claude", profile_would_emit=True, exceptions=exc) == []


# --- ungoverned --------------------------------------------------------------------------------------


def test_ungoverned_never_refuses_and_writes_nothing(tmp_path):
    team = _team(tmp_path, governed=None)
    got = gate.authorize(team, _os_off(), consume=True)
    assert got == [{"kind": "sandbox-os-off", "action": "sandbox-os-off:claude", "authorization": "notice-only"}]
    assert not (team / er.EXCEPTION_REGISTRY_REL).exists()


def test_ungoverned_skips_switch_candidates(tmp_path):
    team = _team(tmp_path, governed=False)
    r = gate.Relaxation("wasm-to-os-switch", "runner-a", continuing=True)
    assert gate.authorize(team, [r], consume=True) == []


# --- governed refusals --------------------------------------------------------------------------------


def test_governed_without_a_row_refuses_and_names_the_row_shape(tmp_path):
    team = _team(tmp_path)
    with pytest.raises(gate.SandboxRelaxationError, match=r"--sign-decision.*scope=sandbox-os-off:claude"):
        gate.authorize(team, _os_off(), consume=True)


def test_hmac_signed_pass_is_valid_but_insufficient(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude", scheme=""))
    with pytest.raises(gate.SandboxRelaxationError, match="INSUFFICIENT"):
        gate.authorize(team, _os_off(), consume=True)


def test_a_waiver_never_authorizes(tmp_path, stub_ed25519, monkeypatch):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude"))
    monkeypatch.setattr(security_gate, "_latest_security_waiver", lambda *a, **k: {"action_reviewed": "x"})
    with pytest.raises(gate.SandboxRelaxationError, match="waiver"):
        gate.authorize(team, _os_off(), consume=True)


@pytest.mark.parametrize("scope", ["sandbox-os-off", "sandbox-os-off:claude;sandbox-os-off:goose"])
def test_scope_must_be_exactly_the_action(tmp_path, stub_ed25519, scope):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude", scope=scope))
    with pytest.raises(gate.SandboxRelaxationError, match="scope=sandbox-os-off:claude exactly"):
        gate.authorize(team, _os_off(), consume=True)


def test_effect_relaxes_must_be_exactly_the_action(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude", relaxes="sandbox-os-off:claude;sandbox-os-off:goose"))
    with pytest.raises(gate.SandboxRelaxationError, match="effect_relaxes"):
        gate.authorize(team, _os_off(), consume=True)


def test_missing_lineage_refuses(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude", derives=""))
    with pytest.raises(gate.SandboxRelaxationError, match="derives_from"):
        gate.authorize(team, _os_off(), consume=True)


@pytest.mark.parametrize("grants,named", [
    ("wasm:analyst:read:data;wasm:analyst:scratch;wasm:analyst:read:etc",
     "extra in decision: ['wasm:analyst:read:etc']"),
    ("wasm:analyst:read:data", "missing from decision: ['wasm:analyst:scratch']"),
])
def test_widen_grants_must_match_exactly(tmp_path, stub_ed25519, grants, named):
    team = _team(tmp_path)
    _log(team, _row("wasm-grant-widen:claude", grants=grants))
    planned = gate.plan_relaxations(_st(wasm=True), _st(wasm=True, grants={"analyst": _g("data", scratch=True)}),
                                    framework_id="claude", profile_would_emit=True)
    with pytest.raises(gate.SandboxRelaxationError) as err:
        gate.authorize(team, planned, consume=True)
    assert named in str(err.value)


def test_keep_grants_must_match_exactly(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("native-exec-keep:claude", grants="native-exec:a;native-exec:b"))
    exc = (SandboxExceptions(), SandboxExceptions(native_exec_keep=frozenset({"a"})))
    planned = gate.plan_relaxations(_st(wasm=True), _st(wasm=True), framework_id="claude", profile_would_emit=True,
                                    exceptions=exc)
    with pytest.raises(gate.SandboxRelaxationError, match=r"extra in decision: \['native-exec:b'\]"):
        gate.authorize(team, planned, consume=True)


@pytest.mark.parametrize("kind", SANDBOX_RELAXATION_KINDS)
def test_a_halt_on_the_family_blocks_every_instance(tmp_path, stub_ed25519, kind):
    team = _team(tmp_path)
    grants = {"wasm-grant-widen": "wasm:a:scratch", "native-exec-keep": "native-exec:a"}.get(kind, "")
    qualifier = "a" if kind == "wasm-to-os-switch" else "claude"
    _log(team, _row(kind, verdict="HALT", sign=None), _row(f"{kind}:{qualifier}", grants=grants))
    r = gate.Relaxation(kind, qualifier, grants=tuple(grants.split(";")) if grants else ())
    with pytest.raises(gate.SandboxRelaxationError, match="HALT"):
        gate.authorize(team, [r], consume=True)


def test_an_instance_halt_blocks_its_siblings_too(tmp_path, stub_ed25519):
    # Rule 12: the family check over-blocks (a HALT on goose also stops claude). Over-blocking only.
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:goose", verdict="HALT", sign=None), _row("sandbox-os-off:claude"))
    with pytest.raises(gate.SandboxRelaxationError, match="HALT"):
        gate.authorize(team, _os_off(), consume=True)


def test_governed_detection_gv1_and_root_marker(tmp_path):
    team = _team(tmp_path, governed=None)
    (tmp_path / "signing-governed.marker").write_text("")  # a project-root marker is not read
    assert gate.authorize(team, _os_off(), consume=False)[0]["authorization"] == "notice-only"
    (team / "signing-governed.marker").write_text("")
    with pytest.raises(gate.SandboxRelaxationError, match="fail-closed"):
        gate.authorize(team, _os_off(), consume=False)


# --- governed success, standing, sunset, retire -------------------------------------------------------


def test_os_off_happy_path_registers_and_continues_then_lapses(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row("sandbox-os-off:claude"))
    [rec] = gate.authorize(team, _os_off(), consume=True, now=_T0)
    assert rec["authorization"] == "ed25519-decision" and rec["key_id"] == _KEY_ID
    assert rec["exception_id"].startswith("sandbox-os-off:claude@stub")
    assert "yes" in log.read_text()  # consumed
    [entry] = _registry(team)
    assert entry["action"] == "sandbox-os-off:claude" and entry["status"] == "active"
    assert entry["lineage"] == ["approved-baseline"] and entry["effect"] == {"relaxes": ["sandbox-os-off:claude"]}
    assert datetime.fromisoformat(entry["expires"]) == _T0 + timedelta(days=90)

    later = gate.plan_relaxations(_st(os=False), _st(os=False), framework_id="claude", profile_would_emit=True)
    [cont] = gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=30))
    assert cont == {"kind": "sandbox-os-off", "action": "sandbox-os-off:claude",
                    "authorization": "active-exception", "exception_id": entry["id"]}

    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=91))

    # A freshly signed decision replaces the lapsed exception (which is retired, not left lapsed).
    _log(team, _row("sandbox-os-off:claude", stamp="2027-02-01"))
    [again] = gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=91))
    assert again["authorization"] == "ed25519-decision" and again["exception_id"] != entry["id"]
    statuses = {e["id"]: e["status"] for e in _registry(team)}
    assert statuses == {entry["id"]: "sunset", again["exception_id"]: "active"}

    # Turning the OS sandbox back on retires the standing exception.
    assert gate.retire_on_tighten(team, _st(os=False), _st(), framework_id="claude", dry_run=True) == [
        again["exception_id"]]
    assert _registry(team)[-1]["status"] == "active"  # dry run wrote nothing
    gate.retire_on_tighten(team, _st(os=False), _st(), framework_id="claude", dry_run=False)
    assert {e["status"] for e in _registry(team)} == {"sunset"}


def test_dry_run_writes_nothing(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row("sandbox-os-off:claude"))
    before = log.read_bytes()
    [rec] = gate.authorize(team, _os_off(), consume=False, now=_T0)
    assert rec["authorization"] == "ed25519-decision"
    assert log.read_bytes() == before and not (team / er.EXCEPTION_REGISTRY_REL).exists()


def test_wasm_off_consumes_but_registers_nothing(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("sandbox-wasm-off:model-substrate"))
    planned = gate.plan_relaxations(_st(substrate="on"), _st(substrate="off"), framework_id="claude",
                                    profile_would_emit=True)
    [rec] = gate.authorize(team, planned, consume=True, now=_T0)
    assert rec["action"] == "sandbox-wasm-off:model-substrate" and "exception_id" not in rec
    assert _registry(team) == []


def test_widen_registers_exact_tokens_and_retires_when_removed(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("wasm-grant-widen:claude", grants="wasm:analyst:read:data;wasm:analyst:scratch"))
    new = _st(wasm=True, grants={"analyst": _g("data", scratch=True)})
    gate.authorize(team, gate.plan_relaxations(_st(wasm=True), new, framework_id="claude", profile_would_emit=True),
                   consume=True, now=_T0)
    [entry] = _registry(team)
    assert entry["effect"] == {"grants": ["wasm:analyst:read:data", "wasm:analyst:scratch"]}
    # Partly kept: the entry still covers what remains.
    smaller = _st(wasm=True, grants={"analyst": _g("data")})
    [cont] = gate.authorize(team, gate.plan_relaxations(new, smaller, framework_id="claude", profile_would_emit=True),
                            consume=True, now=_T0)
    assert cont["authorization"] == "active-exception"
    assert gate.retire_on_tighten(team, new, smaller, framework_id="claude", dry_run=False) == []
    assert gate.retire_on_tighten(team, smaller, _st(wasm=True), framework_id="claude", dry_run=False) == [entry["id"]]


def test_continuing_widen_with_no_exception_refuses(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team)
    st = _st(wasm=True, grants={"analyst": _g("data")})
    planned = gate.plan_relaxations(st, st, framework_id="claude", profile_would_emit=True)
    with pytest.raises(gate.SandboxRelaxationError, match="no exception records"):
        gate.authorize(team, planned, consume=True, now=_T0)


def test_pre_governance_os_off_continues_with_a_notice(tmp_path, capsys):
    team = _team(tmp_path)
    later = gate.plan_relaxations(_st(os=False), _st(os=False), framework_id="claude", profile_would_emit=True)
    [rec] = gate.authorize(team, later, consume=True, now=_T0)
    assert rec["authorization"] == "pre-governance"
    err = capsys.readouterr().err
    assert "SANDBOX RELAXATION: sandbox-os-off:claude predates sandbox governance" in err
    # W13R: this path reads no decision rows, so the notice must not offer signing as the remedy.
    assert "turning the sandbox back on ends it" in err and "sign a decision to record it" not in err


def test_runner_switch_is_standing_per_agent_and_retired_on_return(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("wasm-to-os-switch:runner-a"))
    old_exc = SandboxExceptions(wasm_programs=frozenset({"runner-a"}))
    switched = SandboxExceptions(os_programs=frozenset({"runner-a", "always-os"}))
    planned = gate.plan_relaxations(_st(), _st(), framework_id="claude", profile_would_emit=True,
                                    exceptions=(old_exc, switched))
    recs = gate.authorize(team, planned, consume=True, now=_T0)
    assert [(r["action"], r["authorization"]) for r in recs] == [("wasm-to-os-switch:runner-a", "ed25519-decision")]
    # Next render: the switched agent continues under its exception; an always-os agent is not a relaxation.
    again = gate.plan_relaxations(_st(), _st(), framework_id="claude", profile_would_emit=True,
                                  exceptions=(switched, switched))
    recs = gate.authorize(team, again, consume=True, now=_T0 + timedelta(days=1))
    assert [(r["action"], r["authorization"]) for r in recs] == [("wasm-to-os-switch:runner-a", "active-exception")]
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, again, consume=True, now=_T0 + timedelta(days=91))
    back = SandboxExceptions(wasm_programs=frozenset({"runner-a"}))
    assert gate.retire_on_tighten(team, _st(), _st(), framework_id="claude", exceptions=(switched, back),
                                  dry_run=False) == [recs[0]["exception_id"]]


def test_cap_binds_and_raising_it_admits(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude"), _row("wasm-to-os-switch:runner-a"))
    gate.authorize(team, _os_off(), consume=True, now=_T0)
    switch = [gate.Relaxation("wasm-to-os-switch", "runner-a")]
    log_before = (team / "references" / "security-decisions.log.csv").read_bytes()
    with pytest.raises(gate.SandboxRelaxationError, match="over the cap"):
        gate.authorize(team, switch, consume=True, now=_T0)
    assert (team / "references" / "security-decisions.log.csv").read_bytes() == log_before  # nothing consumed
    registry = er.load_registry(team)
    registry["cap"] = {"max_active_relaxing": 2}
    er.write_registry(team, registry)
    assert gate.authorize(team, switch, consume=True, now=_T0)[0]["authorization"] == "ed25519-decision"


def test_a_refusal_consumes_nothing_across_relaxations(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row("sandbox-os-off:claude"))
    before = log.read_bytes()
    both = [*_os_off(), gate.Relaxation("sandbox-wasm-off", "claude", standing=False)]
    with pytest.raises(gate.SandboxRelaxationError):
        gate.authorize(team, both, consume=True, now=_T0)
    assert log.read_bytes() == before


# --- end to end with real Ed25519 ----------------------------------------------------------------------


def test_real_ed25519_signature_clears_os_off(tmp_path):
    pytest.importorskip("cryptography", reason="the 'signing' extra is not installed")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from agentteams.cli import signed_ledger as sl

    team = _team(tmp_path)
    priv = Ed25519PrivateKey.generate()
    (team / "references" / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").write_text(
        priv.public_key().public_bytes(encoding=serialization.Encoding.PEM,
                                       format=serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    pem = priv.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8,
                             encryption_algorithm=serialization.NoEncryption()).decode()
    _log(team, _row("sandbox-os-off:claude", sign=lambda values: sl.ed25519_sign(pem, values)))
    [rec] = gate.authorize(team, _os_off(), consume=True, now=_T0)
    assert rec["authorization"] == "ed25519-decision"


# --- S1: a carried-over relaxation needs a present expiry and a re-verified decision binding ---------------------


def _registered_os_off(tmp_path: Path) -> tuple[Path, dict, list[gate.Relaxation]]:
    team = _team(tmp_path)
    _log(team, _row("sandbox-os-off:claude"))
    gate.authorize(team, _os_off(), consume=True, now=_T0)
    [entry] = _registry(team)
    later = gate.plan_relaxations(_st(os=False), _st(os=False), framework_id="claude", profile_would_emit=True)
    return team, entry, later


def _mutate_entry(team: Path, **changes) -> None:
    registry = er.load_registry(team)
    for key, value in changes.items():
        if value is None:
            registry["exceptions"][0].pop(key, None)
        else:
            registry["exceptions"][0][key] = value
    er.write_registry(team, registry)


def test_registered_entry_is_bound_to_its_decision_row(tmp_path, stub_ed25519):
    team, entry, later = _registered_os_off(tmp_path)
    log_rows = list(csv.DictReader((team / "references" / "security-decisions.log.csv").open(newline="")))
    assert entry["decision_sha256"] == gate._decision_binding(log_rows[-1])   # consumed cell excluded
    assert gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=1))[0]["authorization"] == \
        "active-exception"


@pytest.mark.parametrize("changes,why", [
    ({"expires": None}, "records no expiry"),
    ({"expires": ""}, "records no expiry"),
    ({"decision_sha256": None}, "is not bound to a decision row"),
    ({"decision_sha256": "ab" * 32}, "no longer in the decisions log"),
])
def test_carry_over_refuses_without_expiry_or_binding(tmp_path, stub_ed25519, capsys, changes, why):
    team, entry, later = _registered_os_off(tmp_path)
    _mutate_entry(team, **changes)
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=1))
    assert why in capsys.readouterr().err
    assert _registry(team)[0]["status"] == "active"   # a refusal writes nothing


def test_carry_over_refuses_when_the_bound_row_no_longer_verifies(tmp_path, stub_ed25519, capsys, monkeypatch):
    team, entry, later = _registered_os_off(tmp_path)
    # The row is still there and the binding still matches, but its signature no longer verifies (key rotated
    # out / forged row): re-verification through the decision log refuses it.
    monkeypatch.setattr(dl, "verify_by_scheme", lambda *a, **k: False)
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=1))
    assert "no longer verifies" in capsys.readouterr().err


def test_carry_over_refuses_when_the_bound_row_was_rewritten(tmp_path, stub_ed25519, capsys):
    team, entry, later = _registered_os_off(tmp_path)
    path = team / "references" / "security-decisions.log.csv"
    text = path.read_text()
    path.write_text(text.replace("sandbox-os-off:claude,PASS", "sandbox-os-off:claude,CONDITIONAL PASS"))
    with pytest.raises(gate.SandboxRelaxationError, match="has lapsed"):
        gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=1))
    assert "no longer in the decisions log" in capsys.readouterr().err


def test_unbound_entry_is_replaced_by_a_fresh_signed_decision(tmp_path, stub_ed25519):
    team, entry, later = _registered_os_off(tmp_path)
    _mutate_entry(team, decision_sha256=None)
    _log(team, _row("sandbox-os-off:claude", stamp="2026-11-02"))
    [rec] = gate.authorize(team, later, consume=True, now=_T0 + timedelta(days=1))
    assert rec["authorization"] == "ed25519-decision" and rec["exception_id"] != entry["id"]
    statuses = {e["id"]: e["status"] for e in _registry(team)}
    assert statuses == {entry["id"]: "sunset", rec["exception_id"]: "active"}
