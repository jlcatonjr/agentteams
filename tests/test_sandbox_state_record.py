"""SandboxState (METAPLAN §2.1), its build-log record, the token<->Mount mapping, and the previous state (P15 D5)."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
from pathlib import Path

import pytest

from agentteams import sandbox_state as ss

_HAS_WASM_EXEC = importlib.util.find_spec("agentteams.wasm_exec") is not None


@pytest.fixture
def wx():
    """The real executor (P02 is merged). A sys.modules stand-in would not reach ``from agentteams import
    wasm_exec`` once the real module is imported elsewhere in the run, so tests would compare two Grant classes."""
    from agentteams import wasm_exec
    return wasm_exec


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".claude" / "agents" / "references").mkdir(parents=True)
    return root.resolve()


def _grant(wx, root: Path, agent: str, *reads: str, scratch: bool = False):
    mounts = [wx.Mount(host=str(root / r), guest=f"/project/{r}", mode="ro") for r in sorted(reads)]
    if scratch:
        mounts.append(wx.Mount(host=str(root / ss.SCRATCH_REL / agent), guest="/scratch", mode="rw"))
    return wx.Grant(mounts=tuple(mounts), limits=wx.Limits(fuel=10, max_memory_bytes=65536, timeout_s=5))


def _state(**over) -> ss.SandboxState:
    base = dict(os=True, os_reason="profile:confined", wasm=True, wasm_reason="brief",
                wasm_languages=("module", "python"), wasm_grants={}, model_substrate=None)
    return ss.SandboxState(**{**base, **over})


# --- the dataclass shape ---------------------------------------------------------------------------


def test_fields_equal_metaplan_2_1_exactly():
    assert [f.name for f in dataclasses.fields(ss.SandboxState)] == [
        "os", "os_reason", "wasm", "wasm_reason", "wasm_languages", "wasm_grants", "model_substrate"]
    assert dataclasses.fields(ss.SandboxState)[5].type == "Mapping[str, Grant]"


def test_frozen_including_grants():
    state = _state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.os = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        state.wasm_grants["x"] = None  # type: ignore[index]


def test_conservative_is_was_on():
    c = ss.SandboxState.conservative()
    assert (c.os, c.wasm, c.os_reason, c.wasm_reason, dict(c.wasm_grants), c.model_substrate) == (
        True, True, "unknown", "unknown", {}, "on")


# --- token <-> Mount ---------------------------------------------------------------------------------


def test_grant_tokens_from_mounts(tmp_path, wx):
    root = _root(tmp_path)
    grant = _grant(wx, root, "analyst", "data/raw", "docs", scratch=True)
    assert ss.grant_tokens("analyst", grant, project_root=root) == {
        "wasm:analyst:read:data/raw", "wasm:analyst:read:docs", "wasm:analyst:scratch"}
    limits_only = wx.Grant(mounts=(), limits=wx.Limits(fuel=10**12, max_memory_bytes=2**31, timeout_s=3600))
    assert ss.grant_tokens("analyst", limits_only, project_root=root) == frozenset()  # limits are not a widening


@pytest.mark.parametrize("mount", [
    lambda wx, root: wx.Mount(host=str(root / "x"), guest="/src", mode="ro"),
    lambda wx, root: wx.Mount(host=str(root / "x"), guest="/lib", mode="ro"),
    lambda wx, root: wx.Mount(host=str(root / "x"), guest="/project/x", mode="rw"),
    lambda wx, root: wx.Mount(host="/etc", guest="/project/etc", mode="ro"),
    lambda wx, root: wx.Mount(host=str(root / "elsewhere"), guest="/scratch", mode="rw"),
    lambda wx, root: wx.Mount(host=str(root / "x"), guest="/project/../x", mode="ro"),
])
def test_non_grant_mounts_are_refused(tmp_path, wx, mount):
    root = _root(tmp_path)
    # Refused at either layer: the real executor rejects most of these when the Mount/Grant is built
    # (WasmExecError); grant_tokens rejects the rest (ValueError). Neither may yield tokens.
    with pytest.raises((ValueError, wx.WasmExecError)):
        grant = wx.Grant(mounts=(mount(wx, root),), limits=wx.Limits(1, 65536, 1))
        ss.grant_tokens("analyst", grant, project_root=root)


def test_grant_from_tokens_round_trip_and_refusals(tmp_path, wx):
    root = _root(tmp_path)
    grant = _grant(wx, root, "analyst", "data/raw", scratch=True)
    tokens = ss.grant_tokens("analyst", grant, project_root=root)
    assert ss.grant_from_tokens("analyst", tokens, grant.limits, project_root=root) == grant
    with pytest.raises(ValueError):
        ss.grant_from_tokens("analyst", ["wasm:other:scratch"], grant.limits, project_root=root)
    (root / "outside-link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        ss.grant_from_tokens("analyst", ["wasm:analyst:read:outside-link"], grant.limits, project_root=root)


# --- the record --------------------------------------------------------------------------------------


def test_record_round_trip_with_grants_and_substrate(tmp_path, wx):
    root = _root(tmp_path)
    state = _state(wasm_grants={"analyst": _grant(wx, root, "analyst", "data/raw", scratch=True)},
                   model_substrate="on")
    rec = state.to_record(project_root=root)
    assert rec["wasm_grants"] == {"analyst": {
        "tokens": ["wasm:analyst:read:data/raw", "wasm:analyst:scratch"],
        "limits": {"fuel": 10, "max_memory_bytes": 65536, "timeout_s": 5}}}
    assert str(tmp_path) not in json.dumps(rec)  # project-relative only
    assert ss.SandboxState.from_record(json.loads(json.dumps(rec)), project_root=root) == state


@pytest.mark.skipif(not _HAS_WASM_EXEC, reason="agentteams.wasm_exec (P02) is not merged yet; re-run after wave 1")
def test_record_round_trip_with_the_real_wasm_exec(tmp_path):
    from agentteams import wasm_exec

    root = _root(tmp_path)
    grant = ss.grant_from_tokens("analyst", ["wasm:analyst:read:data", "wasm:analyst:scratch"],
                                 wasm_exec.DEFAULT_LIMITS, project_root=root)
    state = _state(wasm_grants={"analyst": grant})
    assert ss.SandboxState.from_record(state.to_record(project_root=root), project_root=root) == state


def test_record_without_grants_needs_no_wasm_exec(tmp_path):
    root = _root(tmp_path)
    state = _state(os=False, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=())
    assert ss.SandboxState.from_record(state.to_record(project_root=root), project_root=root) == state


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(os=True),
    lambda r: r.update(os_reason="Brief With Spaces"),
    lambda r: r.update(wasm_languages=["python", "module"]),
    lambda r: r.update(wasm_languages=["rust"]),
    lambda r: r.update(model_substrate="maybe"),
    lambda r: r.update(extra=1),
    lambda r: r.pop("wasm"),
    lambda r: r.update(wasm_grants={"Bad Slug": {"tokens": [], "limits": {}}}),
    lambda r: r.update(wasm_grants={"a": {"tokens": ["wasm:a:read:../x"],
                                          "limits": {"fuel": 1, "max_memory_bytes": 1, "timeout_s": 1}}}),
    lambda r: r.update(wasm_grants={"a": {"tokens": [], "limits": {"fuel": True, "max_memory_bytes": 1,
                                                                   "timeout_s": 1}}}),
])
def test_malformed_record_parses_to_none(tmp_path, wx, mutate):
    root = _root(tmp_path)
    rec = _state().to_record(project_root=root)
    mutate(rec)
    assert ss.SandboxState.from_record(rec, project_root=root) is None


# --- the previous state --------------------------------------------------------------------------------


def _log(root: Path, **keys) -> Path:
    path = root / ".claude" / "agents" / "references" / "build-log.json"
    path.write_text(json.dumps({"schema_version": "1.6", **keys}), encoding="utf-8")
    return root / ".claude" / "agents"


def test_no_record_and_no_evidence_is_none(tmp_path):
    root = _root(tmp_path)
    team = _log(root)
    assert ss.previous_sandbox_state(team, root, "claude") is None
    assert ss.previous_sandbox_exceptions(team) is None


def test_record_is_read_back(tmp_path):
    root = _root(tmp_path)
    state = _state(os=False, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=())
    team = _log(root, sandbox_state=state.to_record(project_root=root))
    assert ss.previous_sandbox_state(team, root, "claude") == state


def test_malformed_record_is_conservative(tmp_path):
    root = _root(tmp_path)
    team = _log(root, sandbox_state={"os": "sideways"})
    assert ss.previous_sandbox_state(team, root, "claude") == ss.SandboxState.conservative()


def test_symlinked_or_corrupt_build_log_is_conservative(tmp_path):
    root = _root(tmp_path)
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"sandbox_state": None}))
    team = root / ".claude" / "agents"
    (team / "references" / "build-log.json").symlink_to(real)
    assert ss.previous_sandbox_state(team, root, "claude") == ss.SandboxState.conservative()
    (team / "references" / "build-log.json").unlink()
    (team / "references" / "build-log.json").write_text("{not json")
    assert ss.previous_sandbox_state(team, root, "claude") == ss.SandboxState.conservative()
    assert ss.previous_sandbox_exceptions(team) == ss.SandboxExceptions()


@pytest.mark.parametrize("framework,evidence", [("goose", ".goose/sandbox.sb"),
                                                ("codex", ".codex/confined-run.example.sh"),
                                                ("grok", ".grok/sandbox.toml"),
                                                ("claude", "sandbox/confine-run.sh")])
def test_os_evidence_ors_into_a_recorded_off(tmp_path, framework, evidence):
    root = _root(tmp_path)
    state = _state(os=False, os_reason="brief", wasm=False, wasm_reason="default-off", wasm_languages=())
    team = _log(root, sandbox_state=state.to_record(project_root=root))
    (root / evidence).parent.mkdir(parents=True, exist_ok=True)
    (root / evidence).write_text("x")
    prev = ss.previous_sandbox_state(team, root, framework)
    assert prev.os is True and prev.os_reason == "brief" and prev.wasm is False


def test_other_frameworks_evidence_does_not_count(tmp_path):
    root = _root(tmp_path)
    team = _log(root)
    (root / ".goose").mkdir()
    (root / ".goose" / "sandbox.sb").write_text("x")
    assert ss.previous_sandbox_state(team, root, "codex") is None


def test_claude_live_settings_evidence(tmp_path):
    root = _root(tmp_path)
    team = _log(root)
    (root / ".claude" / "settings.json").write_text(json.dumps({"sandbox": {"enabled": "true"}}))
    assert ss.previous_sandbox_state(team, root, "claude") is None  # booleans only
    (root / ".claude" / "settings.json").write_text(json.dumps({"sandbox": {"enabled": True}}))
    prev = ss.previous_sandbox_state(team, root, "claude")
    assert prev.os is True and prev.os_reason == "unknown"


def test_claude_emitted_example_evidence(tmp_path):
    """Wave-2 S1: a team that emitted a sandbox block but never merged it still had a boundary."""
    root = _root(tmp_path)
    team = _log(root)
    example = root / ".claude" / "settings.hooks.example.json"
    example.write_text(json.dumps({"hooks": {}}))  # emitted for every claude team: no sandbox, no evidence
    assert ss.previous_sandbox_state(team, root, "claude") is None
    example.write_text(json.dumps({"hooks": {}, "sandbox": {"enabled": True}}))
    assert ss.previous_sandbox_state(team, root, "claude").os is True
    assert ss.previous_sandbox_state(team, root, "goose") is None  # claude's evidence only


def test_claude_example_beside_a_non_canonical_team_dir_counts_but_never_outside_the_project(tmp_path):
    root = _root(tmp_path)
    team = root / "agents-elsewhere" / "team"
    (team / "references").mkdir(parents=True)
    (team.parent / "settings.hooks.example.json").write_text(json.dumps({"sandbox": {"enabled": True}}))
    assert ss.previous_sandbox_state(team, root, "claude").os is True
    (tmp_path / "settings.hooks.example.json").write_text(json.dumps({"sandbox": {"enabled": True}}))
    assert ss.previous_sandbox_state(root, root, "claude") is None  # the project root's parent is never read


def test_wasm_and_substrate_evidence(tmp_path):
    root = _root(tmp_path)
    team = _log(root)
    (root / ".agentteams" / "wasm").mkdir(parents=True)
    (root / ".agentteams" / "wasm" / "installed.json").write_text("{}")
    (root / ".agentteams" / "sandbox-state.json").write_text("{broken")
    prev = ss.previous_sandbox_state(team, root, "claude")
    assert (prev.os, prev.wasm, prev.model_substrate) == (False, True, "on")
    (root / ".agentteams" / "sandbox-state.json").write_text(json.dumps({"model_substrate_wasm": "off"}))
    assert ss.previous_sandbox_state(team, root, "claude").model_substrate is None


def test_sandbox_exceptions_round_trip_and_previous(tmp_path):
    exc = ss.SandboxExceptions(native_exec_keep=frozenset({"git-operations"}), wasm_programs=frozenset({"a", "b"}),
                               os_programs=frozenset({"c"}))
    assert ss.SandboxExceptions.from_record(exc.to_record()) == exc
    assert ss.SandboxExceptions.from_record({"native_exec_keep": [], "wasm_programs": ["a"]}) == ss.SandboxExceptions(
        wasm_programs=frozenset({"a"}))
    assert ss.SandboxExceptions.from_record({"native_exec_keep": ["Bad"], "wasm_programs": []}) is None
    root = _root(tmp_path)
    assert ss.previous_sandbox_exceptions(_log(root, sandbox_exceptions=exc.to_record())) == exc
    assert ss.previous_sandbox_exceptions(_log(root, sandbox_exceptions={"x": 1})) == ss.SandboxExceptions()


def test_keep_tokens_only_while_wasm_is_on():
    exc = ss.SandboxExceptions(native_exec_keep=frozenset({"git-operations"}))
    assert ss.keep_tokens(_state(), exc) == {"native-exec:git-operations"}
    assert ss.keep_tokens(_state(wasm=False), exc) == frozenset()
    assert ss.keep_tokens(None, exc) == frozenset() and ss.keep_tokens(_state(), None) == frozenset()


# --- the build-log record (D9) -------------------------------------------------------------------------


def _run_log(tmp_path: Path, **extra) -> dict:
    from agentteams.emit import EmitResult
    from build_team import _write_run_log

    team = tmp_path / ".claude" / "agents"
    team.mkdir(parents=True, exist_ok=True)
    manifest = {"project_name": "T", "framework": "claude", "project_type": "software",
                "selected_archetypes": [], "components": [], "agent_slug_list": [], "governance_agents": [],
                "output_files": [], "manual_required_placeholders": [], **extra}
    _write_run_log(manifest, EmitResult(dry_run=False), team, {})
    return json.loads((team / "references" / "build-log.json").read_text())


def test_build_log_unchanged_without_sandbox_keys(tmp_path):
    log = _run_log(tmp_path)
    assert log["schema_version"] == "1.5" and "sandbox_state" not in log and "sandbox_exceptions" not in log


def test_build_log_records_and_reads_back_the_state(tmp_path):
    root = tmp_path.resolve()
    state = _state(os=False, os_reason="cli", model_substrate="off")
    exc = ss.SandboxExceptions(native_exec_keep=frozenset({"git-operations"}))
    log = _run_log(tmp_path, sandbox_state=state.to_record(project_root=root), sandbox_exceptions=exc.to_record())
    assert log["schema_version"] == "1.6"
    team = tmp_path / ".claude" / "agents"
    assert ss.previous_sandbox_state(team, root, "claude") == state
    assert ss.previous_sandbox_exceptions(team) == exc


# --- the team-manifest schema (D10) --------------------------------------------------------------------


def test_records_validate_against_the_team_manifest_schema(tmp_path, wx):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((Path(__file__).resolve().parents[1] / "agentteams" / "schemas"
                         / "team-manifest.schema.json").read_text())["properties"]
    root = _root(tmp_path)
    state = _state(wasm_grants={"analyst": _grant(wx, root, "analyst", "data/raw", scratch=True)},
                   model_substrate="off")
    jsonschema.Draft7Validator(schema["sandbox_state"]).validate(state.to_record(project_root=root))
    jsonschema.Draft7Validator(schema["sandbox_state"]).validate(
        ss.SandboxState.conservative().to_record(project_root=root))
    jsonschema.Draft7Validator(schema["sandbox_exceptions"]).validate(ss.SandboxExceptions().to_record())
    from agentteams.cli.sandbox_effects import SANDBOX_RELAXATION_KINDS

    assert schema["sandbox_relaxations"]["items"]["properties"]["kind"]["enum"] == list(SANDBOX_RELAXATION_KINDS)
    jsonschema.Draft7Validator(schema["sandbox_relaxations"]).validate([
        {"kind": "sandbox-os-off", "action": "sandbox-os-off:claude", "authorization": "ed25519-decision",
         "exception_id": "sandbox-os-off:claude@abcd", "decision_date": "2026-10-31", "key_id": "op",
         "signature_prefix": "abcd"},
        {"kind": "wasm-to-os-switch", "action": "wasm-to-os-switch:runner-a", "authorization": "notice-only"}])
    bad = state.to_record(project_root=root)
    bad["wasm_grants"]["analyst"]["tokens"].append("native-exec:analyst")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema["sandbox_state"]).validate(bad)
