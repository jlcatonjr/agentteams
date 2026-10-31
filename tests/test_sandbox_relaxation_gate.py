"""The sandbox-relaxation gate (P15 D7): Ed25519 decision, exact effect, lineage, HALTs, cap, sunset, notice-only.

Ed25519 verification is stubbed (a payload hash) so the gate's own logic runs without the ``signing`` extra; one
end-to-end test signs for real and skips visibly when ``cryptography`` is absent.
"""

from __future__ import annotations

import csv
import hashlib
import json
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

_KEY_ID = "op-2026"
_T0 = datetime(2026, 11, 1, tzinfo=UTC)
_COLUMNS = ["timestamp", "requesting_agent", "action_reviewed", "verdict", "conditions", "conditions_verified",
            "scope", "derives_from", "effect_class", "effect_grants", "effect_targets", "effect_relaxes",
            "effect_destructive", "effect_cross_repo", "effect_bulk", "sig_scheme", "key_id", "signature"]


def _stub_sig(values) -> str:
    return "stub" + hashlib.sha256("|".join(values).encode()).hexdigest()


@pytest.fixture
def stub_ed25519(monkeypatch):
    monkeypatch.setattr(dl, "verify_by_scheme",
                        lambda scheme, values, signature, public_pem=None: signature == _stub_sig(values))


def _team(tmp_path: Path, *, governed: bool | None = True) -> Path:
    team = tmp_path / ".claude" / "agents"
    (team / "references" / "authorized-verify-keys").mkdir(parents=True)
    (team / "references" / "authorized-verify-keys" / f"{_KEY_ID}.pub.pem").write_text("stub-key")
    if governed is not None:
        (team / "references" / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": governed}))
    return team


def _row(action: str, *, scope: str | None = None, relaxes: str | None = None, grants: str = "",
         scheme: str = "ed25519", derives: str = "approved-baseline", verdict: str = "PASS", stamp: str = "2026-10-31",
         sign=_stub_sig) -> dict[str, str]:
    row = {c: "" for c in _COLUMNS} | {
        "timestamp": stamp, "requesting_agent": "security", "action_reviewed": action, "verdict": verdict,
        "conditions_verified": "verified", "scope": action if scope is None else scope, "derives_from": derives,
        "effect_grants": grants, "effect_relaxes": (action if not grants else "") if relaxes is None else relaxes,
        "sig_scheme": scheme, "key_id": _KEY_ID,
    }
    if verdict == "PASS":
        row["signature"] = sign(dl._decision_signature_values(row)) if scheme == "ed25519" else "ab" * 32
    return row


def _log(team: Path, *rows: dict[str, str]) -> Path:
    """Append rows to the decisions log (created with an ancestor row the lineage can name)."""
    path = team / "references" / "security-decisions.log.csv"
    existing: list[dict[str, str]] = []
    fields = list(_COLUMNS)
    if path.exists():
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            fields = list(reader.fieldnames or fields)
            existing = list(reader)
    else:
        existing = [{c: "" for c in _COLUMNS} | {"timestamp": "2026-10-01", "requesting_agent": "security",
                                                 "action_reviewed": "approved-baseline", "verdict": "PASS",
                                                 "conditions_verified": "verified"}]
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows([*existing, *rows])
    return path


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
    assert "SANDBOX RELAXATION: sandbox-os-off:claude predates sandbox governance" in capsys.readouterr().err


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
