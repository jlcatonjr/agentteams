"""#456: the render-time gate for re-pinning a weaker gate verdict policy (the seventh relaxation kind, Rule 15).

A project's ``.agentteams/gate-policy.json`` turns constitutional-gate ``ask`` classes into ``deny``. Re-pinning a
weaker policy than the recorded one (a class no longer denied, ``fail_closed_tools`` turned off, or the policy
removed) is the one-shot ``gate-policy-weaken:<framework_id>`` relaxation: in a governed workspace it needs the
operator's Ed25519-signed decision row, consumed once, with no standing exception; ungoverned, it is a notice only.
Ed25519 verification is stubbed (``stub_ed25519``) as in ``test_mcp_approval_auto_gate.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.cli import exception_registry as er
from agentteams.cli import sandbox_relaxation_gate as gate
from agentteams.cli.effect_classifier import (
    is_sandbox_relaxation,
    requires_operator_signature,
)
from agentteams.cli.management_directives import scope_is_allowed
from agentteams.cli.os_sandbox_switch import engage_relaxation_gate, gate_exceptions
from agentteams.cli.sandbox_effects import (
    GATE_POLICY_WEAKEN,
    SANDBOX_RELAXATION_KINDS,
    names_sandbox_relaxation,
    relaxation_kind_of,
)
from agentteams.cli.write_root_policy import warn_sandbox_relaxation
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.gate_policy import GATE_POLICY_REL, RecordedPolicy
from agentteams.sandbox_state import (
    SandboxExceptions,
    SandboxState,
    effective_sandbox_state,
    previous_sandbox_exceptions,
)
from tests._sandbox_gate_helpers import _KEY_ID, _log, _row, _team

_FW = "claude"
_ACTION = f"{GATE_POLICY_WEAKEN}:{_FW}"
_SHA_A, _SHA_B = "a" * 64, "b" * 64
_REPO = Path(__file__).resolve().parent.parent
_BRIEF = json.loads((_REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))


def _st() -> SandboxState:
    return SandboxState(os=True, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=(),
                        wasm_grants={}, model_substrate=None)


def _pol(*deny: str, closed: bool = False, sha: str = _SHA_A) -> RecordedPolicy:
    return RecordedPolicy(sha, frozenset(deny), closed)


def _exc(policy: RecordedPolicy | None) -> SandboxExceptions:
    return SandboxExceptions(gate_policy=policy)


def _plan(old: RecordedPolicy | None, new: RecordedPolicy | None, *, old_record: bool = True) -> list:
    return gate.plan_relaxations(_st(), _st(), framework_id=_FW, profile_would_emit=True,
                                 exceptions=(_exc(old) if old_record else None, _exc(new)))


_STRICT = _pol("db-delete", "pr-merge", closed=True)
_WEAKER = _pol("db-delete", closed=True, sha=_SHA_B)


# --- vocabulary, classifier, denylist ------------------------------------------------------------------------------


def test_seventh_kind_and_its_action_id():
    assert GATE_POLICY_WEAKEN == "gate-policy-weaken" and SANDBOX_RELAXATION_KINDS[-1] == GATE_POLICY_WEAKEN
    assert relaxation_kind_of(_ACTION) == GATE_POLICY_WEAKEN
    assert gate.Relaxation(GATE_POLICY_WEAKEN, _FW, standing=False).action == _ACTION


@pytest.mark.parametrize("text", [_ACTION, "gate policy weakened", "gate_policy loosening", "GATE-POLICY relaxation",
                                  "weaken gate policy", "loosen the gate policy", "relax_gate_policy for ci",
                                  "gate-policy-weaker"])
def test_free_text_names_the_relaxation(text):
    assert names_sandbox_relaxation(text)


@pytest.mark.parametrize("text", ["gate policy tighten", "add a gate-policy", "read the gate policy",
                                  "strengthen the gate policy", "gate-policy-strict", "subgate-policy-weaken"])
def test_free_text_does_not_over_match(text):
    assert not names_sandbox_relaxation(text)


def test_classifier_and_directive_denylist_recognize_the_kind():
    row = {"action_reviewed": "routine-label", "effect_relaxes": _ACTION}
    assert is_sandbox_relaxation(row, kind="decision") and requires_operator_signature(row, kind="decision")
    assert requires_operator_signature({"scope": "loosen the gate policy"}, kind="decision")
    assert not scope_is_allowed(_ACTION)
    assert not scope_is_allowed("gate policy weakening")


# --- the recorded state ---------------------------------------------------------------------------------------------


def test_record_is_backward_compatible_and_strict():
    assert "gate_policy" not in SandboxExceptions().to_record()  # no policy is not written: old records unchanged
    rec = _exc(_STRICT).to_record()
    assert rec["gate_policy"] == {"sha256": _SHA_A, "deny": ["db-delete", "pr-merge"], "fail_closed_tools": "on"}
    assert SandboxExceptions.from_record(rec) == _exc(_STRICT)
    old = {"native_exec_keep": [], "wasm_programs": [], "os_programs": []}  # a pre-#456 record
    assert SandboxExceptions.from_record(old) == SandboxExceptions()
    assert SandboxExceptions.from_record({"native_exec_keep": [], "wasm_programs": [],
                                          "mcp_approval_auto": "on"}) == SandboxExceptions(mcp_approval_auto=True)
    good = rec["gate_policy"]
    for bad in (None, "on", [], {}, {**good, "preset": "strict"}, {k: v for k, v in good.items() if k != "deny"},
                {**good, "sha256": "A" * 64}, {**good, "sha256": _SHA_A + "\n"}, {**good, "sha256": "a" * 63},
                {**good, "deny": "db-delete"}, {**good, "deny": ["db-delete", "db-delete"]}, {**good, "deny": [7]},
                {**good, "deny": ["DB Delete"]}, {**good, "fail_closed_tools": True}, {**good, "fail_closed_tools": "yes"}):
        assert SandboxExceptions.from_record({**old, "gate_policy": bad}) is None, bad


def test_record_validates_against_the_team_manifest_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_REPO / "agentteams" / "schemas" / "team-manifest.schema.json").read_text())["properties"]
    for value in (_exc(_STRICT), _exc(None), _exc(_pol())):
        jsonschema.Draft7Validator(schema["sandbox_exceptions"]).validate(value.to_record())
    jsonschema.Draft7Validator(schema["sandbox_relaxations"]).validate(
        [{"kind": GATE_POLICY_WEAKEN, "action": _ACTION, "authorization": "ed25519-decision"}])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema["sandbox_exceptions"]).validate(
            {"native_exec_keep": [], "wasm_programs": [], "gate_policy": {"sha256": _SHA_A, "deny": []}})


# --- planning -------------------------------------------------------------------------------------------------------


def _actions(plan: list) -> list[tuple[str, bool, bool]]:
    return [(r.action, r.standing, r.continuing) for r in plan]


def test_tighten_equal_and_first_policy_plan_nothing():
    assert _plan(_STRICT, _STRICT) == []                                         # equal
    assert _plan(_STRICT, _pol("db-delete", "pr-merge", closed=True, sha=_SHA_B)) == []   # same classes, new bytes
    assert _plan(_WEAKER, _STRICT) == []                                         # tighten
    assert _plan(_pol(), _pol("db-delete")) == [] and _plan(_pol(), _pol(closed=True)) == []
    assert _plan(None, _STRICT) == [] and _plan(None, _STRICT, old_record=False) == []  # first policy
    assert _plan(None, None) == []


@pytest.mark.parametrize("old,new", [
    (_STRICT, _WEAKER),                                         # a class no longer denied
    (_STRICT, _pol("db-delete", "pr-merge", sha=_SHA_B)),       # fail_closed_tools true -> false
    (_STRICT, None),                                            # the recorded policy's file is gone
    (_STRICT, _pol("db-delete", "fs-delete", closed=True, sha=_SHA_B)),  # swaps a class: still drops one
], ids=["class-dropped", "fail-closed-off", "removed", "swapped"])
def test_weakenings_plan_one_one_shot_relaxation(old, new):
    assert _actions(_plan(old, new)) == [(_ACTION, False, False)]


def test_gate_inputs_carry_the_policy_and_engage_the_gate(tmp_path):
    m = analyze.build_manifest(_BRIEF, framework=_FW)
    m[PROJECT_ROOT_KEY] = tmp_path.resolve()
    state = effective_sandbox_state(m)
    assert gate_exceptions(m, state, tmp_path, None) is None  # no policy, nothing recorded: byte-identical build-log
    _write_policy(tmp_path, ["db-delete"], preset="strict", closed=True)
    previous, new = gate_exceptions(m, state, tmp_path, None)
    assert previous is None and new.gate_policy is not None and new.gate_policy.preset is None
    assert {"db-delete", "rm-recursive-force"} <= new.gate_policy.deny and new.gate_policy.fail_closed_tools
    # A recorded policy engages the gate even after the file is removed, and that removal plans a weakening.
    (tmp_path / GATE_POLICY_REL).unlink()
    previous, new = gate_exceptions(m, state, tmp_path, _exc(_STRICT))
    assert previous == _exc(_STRICT) and new.gate_policy is None


# --- ungoverned: a notice, never a refusal -------------------------------------------------------------------------


@pytest.mark.parametrize("governed", [None, False])
def test_ungoverned_is_notice_only_and_writes_nothing(tmp_path, governed):
    team = _team(tmp_path, governed=governed)
    assert gate.authorize(team, _plan(_STRICT, _WEAKER), consume=True) == [
        {"kind": GATE_POLICY_WEAKEN, "action": _ACTION, "authorization": "notice-only"}]
    assert not (team / er.EXCEPTION_REGISTRY_REL).exists()
    [line] = warn_sandbox_relaxation(_st(), _st(), framework_id=_FW, exceptions=(_exc(_STRICT), _exc(_WEAKER)))
    assert "SANDBOX RELAXATION" in line and "no longer denies ['pr-merge']" in line
    [line] = warn_sandbox_relaxation(_st(), _st(), framework_id=_FW, exceptions=(_exc(_STRICT), _exc(None)))
    assert "gate verdict policy removed" in line
    [line] = warn_sandbox_relaxation(_st(), _st(), framework_id=_FW, exceptions=(
        _exc(_STRICT), _exc(_pol("db-delete", "pr-merge", sha=_SHA_B))))
    assert "fail_closed_tools turned off" in line
    assert warn_sandbox_relaxation(_st(), _st(), framework_id=_FW, exceptions=(_exc(_WEAKER), _exc(_STRICT))) == []


# --- governed -------------------------------------------------------------------------------------------------------


def test_governed_without_a_row_refuses_naming_the_action_and_how_to_sign(tmp_path):
    team = _team(tmp_path)
    with pytest.raises(gate.SandboxRelaxationError) as err:
        gate.authorize(team, _plan(_STRICT, _WEAKER), consume=True)
    message = str(err.value)
    assert f"'{_ACTION}' relaxes a sandbox and this workspace is governed" in message
    assert f"--sign-decision): action_reviewed={_ACTION}, scope={_ACTION}, effect_relaxes={_ACTION}" in message
    assert "keep or restore the recorded .agentteams/gate-policy.json" in message


def test_governed_signed_row_allows_once_and_is_consumed(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    [rec] = gate.authorize(team, _plan(_STRICT, _WEAKER), consume=True)
    assert rec["authorization"] == "ed25519-decision" and rec["key_id"] == _KEY_ID
    assert "exception_id" not in rec                       # one-shot: no standing exception ...
    assert not (team / er.EXCEPTION_REGISTRY_REL).exists() or er.list_exceptions(team) == []  # ... and no sunset
    assert "yes" in log.read_text()                         # consumed
    with pytest.raises(gate.SandboxRelaxationError, match="no unconsumed decision"):  # spent: a second weakening refuses
        gate.authorize(team, _plan(_STRICT, _WEAKER), consume=True)


def test_dry_run_checks_but_consumes_nothing(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    log = _log(team, _row(_ACTION))
    gate.authorize(team, _plan(_STRICT, None), consume=False)
    assert "yes" not in log.read_text()


def test_hmac_only_row_is_valid_but_insufficient(tmp_path, stub_ed25519):
    team = _team(tmp_path)
    _log(team, _row(_ACTION, scheme=""))
    with pytest.raises(gate.SandboxRelaxationError, match="INSUFFICIENT"):
        gate.authorize(team, _plan(_STRICT, _WEAKER), consume=True)


@pytest.mark.parametrize("halted", [_ACTION, GATE_POLICY_WEAKEN, "op-root"])
def test_an_unretracted_halt_refuses(tmp_path, stub_ed25519, halted):
    """A HALT on the instance, the family, or the signed row's lineage ancestor blocks it (C-2)."""
    team = _team(tmp_path)
    ancestor = {"timestamp": "2026-10-02", "requesting_agent": "security", "action_reviewed": "op-root",
                "verdict": "PASS", "conditions_verified": "verified"}
    _log(team, ancestor, _row(halted, verdict="HALT", sign=None, derives=""), _row(_ACTION, derives="op-root"))
    with pytest.raises(gate.SandboxRelaxationError, match="HALT"):
        gate.authorize(team, _plan(_STRICT, _WEAKER), consume=True)


@pytest.mark.parametrize("kwargs,match", [
    ({"scope": "gate-policy-weaken"}, f"scope={_ACTION} exactly"),
    ({"scope": f"{GATE_POLICY_WEAKEN}:codex"}, f"scope={_ACTION} exactly"),
    ({"relaxes": f"{_ACTION};sandbox-os-off:{_FW}"}, f"effect_relaxes={_ACTION} exactly"),
])
def test_the_row_must_name_this_action_exactly(tmp_path, stub_ed25519, kwargs, match):
    team = _team(tmp_path)
    _log(team, _row(_ACTION, **kwargs))
    with pytest.raises(gate.SandboxRelaxationError, match=match):
        gate.authorize(team, _plan(_STRICT, _WEAKER), consume=True)


def test_governed_tighten_or_equal_needs_nothing(tmp_path):
    team = _team(tmp_path)
    assert gate.authorize(team, _plan(_WEAKER, _STRICT), consume=True) == []
    assert gate.authorize(team, _plan(_STRICT, _STRICT), consume=True) == []


# --- end to end through the render's gate engagement -----------------------------------------------------------------


def _write_policy(root: Path, deny: list[str], *, preset: str | None = None, closed: bool = False) -> None:
    path = root / GATE_POLICY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    body: dict = {"version": 1, "deny": deny, "fail_closed_tools": closed}
    if preset is not None:
        body["preset"] = preset
    path.write_text(json.dumps(body), encoding="utf-8")


def _render(team: Path, root: Path) -> dict:
    m = analyze.build_manifest(_BRIEF, framework=_FW)
    m[PROJECT_ROOT_KEY] = root.resolve()
    engage_relaxation_gate(m, effective_sandbox_state(m), _FW, root, team_dir=team, dry_run=False)
    (team / "references" / "build-log.json").write_text(json.dumps(
        {k: m[k] for k in ("sandbox_state", "sandbox_exceptions") if k in m}))
    return m


def test_render_gate_records_refuses_a_weakening_then_allows_it_once(tmp_path, stub_ed25519):
    root = tmp_path / "proj"
    team = _team(root)
    _write_policy(root, ["db-delete", "pr-merge"], closed=True)
    m = _render(team, root)  # a first policy is a tightening: recorded, nothing relaxed
    assert m["sandbox_relaxations"] == []
    assert m["sandbox_exceptions"]["gate_policy"]["deny"] == ["db-delete", "pr-merge"]
    assert previous_sandbox_exceptions(team).gate_policy.deny == {"db-delete", "pr-merge"}

    _write_policy(root, ["db-delete"], closed=True)
    with pytest.raises(gate.SandboxRelaxationError, match=_ACTION):
        _render(team, root)
    assert previous_sandbox_exceptions(team).gate_policy.deny == {"db-delete", "pr-merge"}  # refused: nothing recorded

    _log(team, _row(_ACTION))
    m = _render(team, root)
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [(_ACTION, "ed25519-decision")]
    assert m["sandbox_exceptions"]["gate_policy"]["deny"] == ["db-delete"]

    m = _render(team, root)  # the weaker policy is now the baseline: no standing exception to re-check
    assert m["sandbox_relaxations"] == []

    (root / GATE_POLICY_REL).unlink()  # removing the recorded policy is a weakening too (the row is spent)
    with pytest.raises(gate.SandboxRelaxationError, match=_ACTION):
        _render(team, root)


def test_render_gate_ungoverned_is_notice_only(tmp_path):
    root = tmp_path / "proj"
    team = _team(root, governed=False)
    _write_policy(root, ["db-delete"], closed=True)
    _render(team, root)
    _write_policy(root, ["db-delete"], closed=False)
    m = _render(team, root)
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [(_ACTION, "notice-only")]
    assert m["sandbox_exceptions"]["gate_policy"]["fail_closed_tools"] == "off"


# --- fail closed without a build-log record: the digest the installed gate pins --------------------------------------


def _install_gate(root: Path, line: str | None, hooks_rel: str = ".claude/hooks") -> None:
    hooks = root / hooks_rel
    hooks.mkdir(parents=True, exist_ok=True)
    body = "import sys\n" + (f"{line}\n" if line is not None else "") + "def main():\n    return 0\n"
    (hooks / "constitutional-gate.py").write_text(body, encoding="utf-8")


def _policy_sha(root: Path) -> str:
    import hashlib

    return hashlib.sha256((root / GATE_POLICY_REL).read_bytes()).hexdigest()


def _drop_record(team: Path) -> None:
    (team / "references" / "build-log.json").unlink(missing_ok=True)


@pytest.mark.parametrize("line,expected", [
    (None, None),                                        # a gate that predates #456: nothing pinned
    ('_GATE_POLICY_SHA256 = ""', None),                  # pins no policy
    (f'_GATE_POLICY_SHA256 = "{_SHA_A}"', _SHA_A),
    ('_GATE_POLICY_SHA256 = "nothex"', "malformed"),
    (f'_GATE_POLICY_SHA256 = "{_SHA_A}"\n_GATE_POLICY_SHA256 = ""', "malformed"),
])
def test_installed_gate_digests(tmp_path, line, expected):
    from agentteams.cli.os_sandbox_switch import installed_gate_digests

    assert installed_gate_digests(tmp_path) == frozenset()   # no gate installed
    _install_gate(tmp_path, line)
    assert installed_gate_digests(tmp_path) == (frozenset({expected}) if expected else frozenset())
    _install_gate(tmp_path, f'_GATE_POLICY_SHA256 = "{_SHA_B}"', ".grok/hooks")   # every framework's gate is read
    assert installed_gate_digests(tmp_path) == frozenset({_SHA_B, *([expected] if expected else [])})


def test_symlinked_gate_counts_as_no_digest(tmp_path):
    from agentteams.cli.os_sandbox_switch import installed_gate_digests

    real = tmp_path / "elsewhere.py"
    real.write_text(f'_GATE_POLICY_SHA256 = "{_SHA_A}"\n', encoding="utf-8")
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    (tmp_path / ".claude" / "hooks" / "constitutional-gate.py").symlink_to(real)
    assert installed_gate_digests(tmp_path) == frozenset()


def _fallback_plan(pinned: str | None, new: RecordedPolicy | None) -> list:
    old = None if pinned is None else SandboxExceptions(pinned_digests=frozenset({pinned}))
    return gate.plan_relaxations(_st(), _st(), framework_id=_FW, profile_would_emit=True,
                                 exceptions=(old, _exc(new)))


def test_plan_from_the_pinned_digest():
    assert _fallback_plan(None, _STRICT) == [] and _fallback_plan(None, None) == []      # first render
    assert _fallback_plan(_SHA_A, _STRICT) == []                                         # same digest
    assert _actions(_fallback_plan(_SHA_B, _STRICT)) == [(_ACTION, False, False)]        # differs: unprovable
    assert _actions(_fallback_plan(_SHA_A, None)) == [(_ACTION, False, False)]           # policy gone
    assert _actions(_fallback_plan("malformed", _STRICT)) == [(_ACTION, False, False)]   # malformed sentinel
    # A recorded policy wins over the pinned digest: a tightening stays a tightening.
    assert gate.plan_relaxations(_st(), _st(), framework_id=_FW, profile_would_emit=True, exceptions=(
        SandboxExceptions(gate_policy=_WEAKER, pinned_digests=frozenset({_SHA_B})), _exc(_STRICT))) == []
    # Several digests: a weakening unless the new sha256 equals every unexplained one.
    two = SandboxExceptions(pinned_digests=frozenset({_SHA_A, _SHA_B}))
    assert _actions(gate.plan_relaxations(_st(), _st(), framework_id=_FW, profile_would_emit=True,
                                          exceptions=(two, _exc(_STRICT)))) == [(_ACTION, False, False)]
    # Another team dir's record is judged semantically, like the own one.
    other = SandboxExceptions(other_policies=frozenset({_STRICT}), pinned_digests=frozenset({_SHA_A}))
    assert gate.plan_relaxations(_st(), _st(), framework_id=_FW, profile_would_emit=True, exceptions=(
        other, _exc(_pol("db-delete", "pr-merge", "fs-delete", closed=True, sha=_SHA_B)))) == []
    assert _actions(gate.plan_relaxations(_st(), _st(), framework_id=_FW, profile_would_emit=True,
                                          exceptions=(other, _exc(_WEAKER)))) == [(_ACTION, False, False)]


def test_fallback_is_never_recorded():
    assert SandboxExceptions(pinned_digests=frozenset({_SHA_A})).to_record() == SandboxExceptions().to_record()


def test_fallback_notice_names_the_pinned_digest():
    [line] = warn_sandbox_relaxation(_st(), _st(), framework_id=_FW, exceptions=(
        SandboxExceptions(pinned_digests=frozenset({_SHA_B})), _exc(_STRICT)))
    assert ("the previous policy is unknown (no build-log record); the installed gate records sha256 "
            f"{_SHA_B[:12]}") in line
    [line] = warn_sandbox_relaxation(_st(), _st(), framework_id=_FW, exceptions=(
        SandboxExceptions(pinned_digests=frozenset({"malformed"})), _exc(None)))
    assert "the previous policy is unknown (no build-log record)" in line and "malformed" in line


@pytest.mark.parametrize("damage", ["deleted", "corrupt", "stripped"])
def test_missing_record_with_a_different_pinned_digest_refuses_when_governed(tmp_path, damage):
    root = tmp_path / "proj"
    team = _team(root)
    _write_policy(root, ["db-delete", "pr-merge"], closed=True)
    _install_gate(root, f'_GATE_POLICY_SHA256 = "{_policy_sha(root)}"')
    _render(team, root)                                         # recorded and pinned
    log = team / "references" / "build-log.json"
    record = json.loads(log.read_text())
    if damage == "deleted":
        log.unlink()
    elif damage == "corrupt":                                   # only the gate inputs are damaged
        log.write_text(json.dumps({**record, "sandbox_exceptions": {"gate_policy": "on"}}))
    else:
        log.write_text(json.dumps({k: v for k, v in record.items() if k != "sandbox_exceptions"}))
    _write_policy(root, ["db-delete"], closed=True)             # then weakened, with the record gone
    with pytest.raises(gate.SandboxRelaxationError, match=_ACTION):
        _render(team, root)


def test_missing_record_with_the_same_pinned_digest_needs_nothing(tmp_path):
    root = tmp_path / "proj"
    team = _team(root)
    _write_policy(root, ["db-delete"], closed=True)
    _install_gate(root, f'_GATE_POLICY_SHA256 = "{_policy_sha(root)}"')
    m = _render(team, root)
    assert m["sandbox_relaxations"] == [] and m["sandbox_exceptions"]["gate_policy"]["deny"] == ["db-delete"]


def test_missing_record_and_no_installed_gate_is_a_first_render(tmp_path):
    root = tmp_path / "proj"
    team = _team(root)
    _write_policy(root, ["db-delete"], closed=True)
    m = _render(team, root)
    assert m["sandbox_relaxations"] == []


def test_deleting_the_record_cannot_hide_a_removed_policy(tmp_path, stub_ed25519):
    root = tmp_path / "proj"
    team = _team(root)
    _write_policy(root, ["db-delete"], closed=True)
    _install_gate(root, f'_GATE_POLICY_SHA256 = "{_policy_sha(root)}"')
    _render(team, root)
    _drop_record(team)
    (root / GATE_POLICY_REL).unlink()
    with pytest.raises(gate.SandboxRelaxationError, match=_ACTION):
        _render(team, root)
    _log(team, _row(_ACTION))
    m = _render(team, root)
    assert [(r["action"], r["authorization"]) for r in m["sandbox_relaxations"]] == [(_ACTION, "ed25519-decision")]


def _render_as(framework: str, team: Path, root: Path) -> dict:
    m = analyze.build_manifest(_BRIEF, framework=framework)
    m[PROJECT_ROOT_KEY] = root.resolve()
    engage_relaxation_gate(m, effective_sandbox_state(m), framework, root, team_dir=team, dry_run=False)
    (team / "references" / "build-log.json").write_text(json.dumps(
        {k: m[k] for k in ("sandbox_state", "sandbox_exceptions") if k in m}))
    return m


@pytest.mark.parametrize("first,second", [("claude", "grok"), ("grok", "claude")])
def test_a_framework_switch_cannot_hide_a_removed_policy(tmp_path, stub_ed25519, first, second):
    """@security F2: pinned under one framework, removed, re-rendered under another: still a weakening."""
    root = tmp_path / "proj"
    _team(root)                                                 # .claude/agents: keys + governed switch
    teams = {"claude": root / ".claude" / "agents", "grok": root / ".grok" / "agents"}
    for team in teams.values():
        (team / "references").mkdir(parents=True, exist_ok=True)
        (team / "references" / "agent-privilege.json").write_text(json.dumps({"enforce_decision_signing": True}))
    _write_policy(root, ["db-delete"], closed=True)
    _install_gate(root, f'_GATE_POLICY_SHA256 = "{_policy_sha(root)}"', f".{first}/hooks")
    _render_as(first, teams[first], root)
    (root / GATE_POLICY_REL).unlink()
    with pytest.raises(gate.SandboxRelaxationError, match=f"{GATE_POLICY_WEAKEN}:{second}"):
        _render_as(second, teams[second], root)
    _drop_record(teams[first])                                  # the gate alone still pins it
    with pytest.raises(gate.SandboxRelaxationError, match=f"{GATE_POLICY_WEAKEN}:{second}"):
        _render_as(second, teams[second], root)
