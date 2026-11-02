"""P00: the dedicated OS-sandbox switch — ``effective_sandbox_state`` precedence, ``os_sandbox_requested`` per token,
``validate_os_sandbox``, the subtractive ``merge_profile_features`` and the advisory's ``requested`` keyword.

Plan: ``tmp/by-week/2026-W44/wasm-sandbox/P00-os-sandbox-switch.plan.md`` D2/D3. The central backward-compatibility
property: with no switch set, every twin answers exactly as its pre-P00 body did.
"""

from __future__ import annotations

import itertools

import pytest

from agentteams import host_features as hf
from agentteams import sandbox_state as ss
from agentteams.sandbox_state import (
    EXPLICIT_SANDBOX_TOKENS_KEY,
    OS_SANDBOX_CLI_KEY,
    effective_sandbox_state,
    os_sandbox_requested,
    validate_os_sandbox,
)

_PROFILES = (None, "cooperative", "confined", "exclusive")
_TOKENS = ("claude:sandbox", "goose:sandbox", "codex:sandbox", "grok:sandbox")


def _legacy_twin(manifest: dict, token: str | None) -> bool:
    """The pre-P00 bodies: the neutral test for ``None``, else ``token in host_features`` or a confining profile."""
    if token is None:
        return manifest.get("privilege_profile") in {"confined", "exclusive"} or any(
            str(t).endswith(":sandbox") for t in manifest.get("host_features") or [])
    return token in (manifest.get("host_features") or []) or manifest.get("privilege_profile") in {
        "confined", "exclusive"}


@pytest.mark.parametrize("profile", _PROFILES)
@pytest.mark.parametrize("tokens", [(), *((t,) for t in _TOKENS), ("claude:sandbox", "goose:sandbox")])
@pytest.mark.parametrize("token", [None, *_TOKENS])
def test_without_the_switch_every_twin_answers_as_before(profile, tokens, token):
    manifest = {"privilege_profile": profile, "host_features": ["claude:hooks", *tokens]}
    assert os_sandbox_requested(manifest, token) is _legacy_twin(manifest, token)
    assert ss.legacy_os_requested(manifest) is _legacy_twin(manifest, None)


@pytest.mark.parametrize("manifest, os_on, reason", [
    ({"privilege_profile": "confined"}, True, "profile:confined"),
    ({"privilege_profile": "exclusive"}, True, "profile:exclusive"),
    ({"privilege_profile": "cooperative"}, False, "profile:cooperative"),
    ({}, False, "profile:none"),
    ({"privilege_profile": "cooperative", "host_features": ["goose:sandbox"]}, True, "host-feature:goose:sandbox"),
    ({"privilege_profile": "confined", "os_sandbox": "off"}, False, "brief"),
    ({"privilege_profile": "cooperative", "os_sandbox": "on"}, True, "brief"),
    ({"privilege_profile": "confined", "os_sandbox": "on", OS_SANDBOX_CLI_KEY: "off"}, False, "cli"),
    ({"privilege_profile": "cooperative", "os_sandbox": "off", OS_SANDBOX_CLI_KEY: "on"}, True, "cli"),
    # argv beats the brief (Q1): an explicit --target-host-features token wins over os_sandbox "off".
    ({"privilege_profile": "confined", "os_sandbox": "off", EXPLICIT_SANDBOX_TOKENS_KEY: ["claude:sandbox"]},
     True, "host-feature:claude:sandbox"),
    ({"privilege_profile": "confined", OS_SANDBOX_CLI_KEY: "on", EXPLICIT_SANDBOX_TOKENS_KEY: ["claude:sandbox"]},
     True, "cli"),
])
def test_precedence(manifest, os_on, reason):
    state = effective_sandbox_state(manifest)
    assert (state.os, state.os_reason) == (os_on, reason)
    assert (state.wasm, state.wasm_reason, state.wasm_languages, dict(state.wasm_grants),
            state.model_substrate) == (False, "default-off", (), {}, None)
    assert ss.SandboxState.from_record(state.to_record(project_root=ss.Path(".")), project_root=ss.Path(".")) == state


def test_cli_off_with_an_explicit_token_is_refused():
    with pytest.raises(ValueError, match="conflicts"):
        effective_sandbox_state({OS_SANDBOX_CLI_KEY: "off", EXPLICIT_SANDBOX_TOKENS_KEY: ["goose:sandbox"]})


@pytest.mark.parametrize("bad", [True, False, 1, 0, "OFF", "On", "yes", "", [], {}])
def test_invalid_values_fail_closed(bad):
    with pytest.raises(ValueError):
        validate_os_sandbox(bad)
    with pytest.raises(ValueError):
        effective_sandbox_state({"privilege_profile": "confined", "os_sandbox": bad})
    with pytest.raises(ValueError):
        effective_sandbox_state({"privilege_profile": "confined", OS_SANDBOX_CLI_KEY: bad})


def test_validate_accepts_absent_on_and_off():
    assert [validate_os_sandbox(v) for v in (None, "on", "off")] == [None, "on", "off"]


@pytest.mark.parametrize("token", [None, *_TOKENS])
def test_switch_off_turns_every_twin_off_and_on_turns_every_twin_on(token):
    for profile in _PROFILES:
        off = {"privilege_profile": profile, "os_sandbox": "off"}
        on = {"privilege_profile": profile, "os_sandbox": "on"}
        assert os_sandbox_requested(off, token) is False
        assert os_sandbox_requested(on, token) is True


def test_another_frameworks_token_is_not_this_frameworks_boundary():
    manifest = {"privilege_profile": "cooperative", "host_features": ["goose:sandbox"]}
    assert os_sandbox_requested(manifest) is True
    assert os_sandbox_requested(manifest, "goose:sandbox") is True
    assert os_sandbox_requested(manifest, "claude:sandbox") is False


def test_convert_path_manifest_without_host_features_is_on_for_confined():
    assert os_sandbox_requested({"privilege_profile": "confined"}, "claude:sandbox") is True


def test_switch_set_and_recorded_state(tmp_path):
    assert not ss.os_switch_set({"privilege_profile": "confined"})
    assert ss.os_switch_set({"os_sandbox": "off"}) and ss.os_switch_set({OS_SANDBOX_CLI_KEY: "on"})
    assert not ss.has_recorded_state(tmp_path)
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "build-log.json").write_text('{"schema_version": "1.5"}')
    assert not ss.has_recorded_state(tmp_path)
    (tmp_path / "references" / "build-log.json").write_text("not json")
    assert ss.has_recorded_state(tmp_path)  # unusable: fail toward governed
    rec = effective_sandbox_state({"os_sandbox": "off"}).to_record(project_root=tmp_path)
    (tmp_path / "references" / "build-log.json").write_text(ss.json.dumps({"sandbox_state": rec}))
    assert ss.has_recorded_state(tmp_path)
    assert ss.verifier_sandbox_state(None, tmp_path, tmp_path).os_reason == "brief"
    assert ss.verifier_sandbox_state({"os_sandbox": "on"}, tmp_path, tmp_path).os is True


def test_verifier_state_fails_closed_without_the_executor(tmp_path, monkeypatch):
    """Wave-3 @code-hygiene H6: a recorded WASM grant with no importable wasm_exec raises, never "no state"."""
    import json

    (tmp_path / "references").mkdir()
    rec = {"os": "on", "os_reason": "brief", "wasm": "on", "wasm_reason": "brief", "wasm_languages": ["python"],
           "wasm_grants": {"a": {"tokens": ["wasm:a:scratch"],
                                 "limits": {"fuel": 1, "max_memory_bytes": 65536, "timeout_s": 1}}},
           "model_substrate": None}
    (tmp_path / "references" / "build-log.json").write_text(json.dumps({"sandbox_state": rec}))
    assert ss.verifier_sandbox_state(None, tmp_path, tmp_path).wasm_grants["a"].mounts[0].guest == "/scratch"

    def missing():
        raise ImportError("no wasm_exec")
    monkeypatch.setattr(ss, "_wasm_exec", missing)
    with pytest.raises(ImportError, match="no wasm_exec"):
        ss.verifier_sandbox_state(None, tmp_path, tmp_path)


# --- merge_profile_features (D3) ---------------------------------------------------------------


@pytest.mark.parametrize("framework", [None, "claude", "goose", "codex", "grok", "copilot-vscode", "agents-md"])
@pytest.mark.parametrize("profile", _PROFILES)
def test_merge_without_the_switch_is_unchanged(framework, profile):
    explicit = ["claude:hooks", "goose:sandbox"]
    golden = list(explicit)
    for tok in hf.expand_privilege_profile(profile, framework):
        if tok not in golden:
            golden.append(tok)
    assert hf.merge_profile_features(explicit, profile, framework) == golden
    assert hf.merge_profile_features(explicit, profile, framework, os_sandbox=None) == golden


@pytest.mark.parametrize("framework", ["claude", "goose", "codex", "grok"])
def test_merge_off_strips_explicit_and_implied_sandbox_tokens(framework):
    merged = hf.merge_profile_features(["claude:hooks", "goose:sandbox"], "exclusive", framework, os_sandbox="off")
    assert merged == ["claude:hooks"]


@pytest.mark.parametrize("framework, token", [("claude", "claude:sandbox"), ("goose", "goose:sandbox"),
                                              ("codex", "codex:sandbox"), ("grok", "grok:sandbox"),
                                              ("copilot-cli", "claude:sandbox")])
def test_merge_on_adds_the_frameworks_token_under_cooperative(framework, token):
    assert hf.merge_profile_features([], "cooperative", framework, os_sandbox="on") == [token]
    assert hf.merge_profile_features([token], "confined", framework, os_sandbox="on").count(token) == 1


# --- privilege_profile_advisory(requested=...) -------------------------------------------------


def test_advisory_requested_false_returns_none_even_where_unenforceable():
    assert hf.privilege_profile_advisory("confined", "goose", ["goose:sandbox"], platform="win32") is not None
    assert hf.privilege_profile_advisory("confined", "goose", [], platform="win32", requested=False) is None


def test_advisory_requested_true_under_cooperative():
    adv = hf.privilege_profile_advisory("cooperative", "goose", [], platform="win32", requested=True)
    assert adv is not None and adv["code"] == "privilege-profile-unenforced-host"


def test_advisory_recognises_the_grok_token():
    adv = hf.privilege_profile_advisory("cooperative", "grok", ["grok:sandbox"], platform="win32")
    assert adv is not None and "grok:sandbox" in adv["message"]


def test_matrix_profile_by_switch_by_token_never_raises():
    for profile, switch, toks in itertools.product(_PROFILES, (None, "on", "off"), ((), ("claude:sandbox",))):
        manifest = {"privilege_profile": profile, "host_features": list(toks)}
        if switch is not None:
            manifest["os_sandbox"] = switch
        state = effective_sandbox_state(manifest)
        assert state.os is (switch == "on" if switch else _legacy_twin(manifest, None))


# --- P01: the WASM half ----------------------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY  # noqa: E402
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY, normalize_wasm_sandbox  # noqa: E402
from agentteams.wasm_exec import DEFAULT_LIMITS, Grant, Limits, Mount  # noqa: E402

_SLUGS = ["orchestrator", "primary-producer", "navigator"]


def _wasm_manifest(block: dict | None, root: Path | None, cli: str | None = None) -> dict:
    m: dict = {"privilege_profile": "confined", "agent_slug_list": list(_SLUGS)}
    if block is not None:
        m["wasm_sandbox"] = normalize_wasm_sandbox(block, agent_slugs=_SLUGS).to_record()
    if root is not None:
        m[PROJECT_ROOT_KEY] = Path(root).resolve()
    if cli is not None:
        m[WASM_SANDBOX_CLI_KEY] = cli
    return m


_GRANT = {"agents": {"primary-producer": {"read_paths": ["docs", "src/app"], "scratch": True}}}


def test_wasm_defaults_off_without_block_or_flag():
    s = effective_sandbox_state({"privilege_profile": "confined"})
    assert (s.wasm, s.wasm_reason, s.wasm_languages, dict(s.wasm_grants)) == (False, "default-off", (), {})


def test_cli_beats_the_brief_both_ways(tmp_path):
    assert effective_sandbox_state(_wasm_manifest({"enabled": False}, tmp_path, "on")).wasm_reason == "cli"
    assert effective_sandbox_state(_wasm_manifest({"enabled": False}, tmp_path, "on")).wasm is True
    off = effective_sandbox_state(_wasm_manifest({"enabled": True, **_GRANT}, tmp_path, "off"))
    assert (off.wasm, off.wasm_reason, dict(off.wasm_grants)) == (False, "cli", {})
    on = effective_sandbox_state(_wasm_manifest(None, tmp_path, "on"))
    assert (on.wasm, on.wasm_languages, dict(on.wasm_grants)) == (True, ("module", "python", "javascript"), {})


def test_grants_are_inert_while_off(tmp_path):
    s = effective_sandbox_state(_wasm_manifest({"enabled": False, **_GRANT}, tmp_path))
    assert (s.wasm, s.wasm_reason, s.wasm_languages, dict(s.wasm_grants)) == (False, "brief", (), {})


def test_on_grants_have_the_exact_r9_mounts(tmp_path):
    root = tmp_path.resolve()
    s = effective_sandbox_state(_wasm_manifest({"enabled": True, "languages": ["python"], **_GRANT}, tmp_path))
    assert (s.wasm, s.wasm_reason, s.wasm_languages) == (True, "brief", ("python",))
    assert set(s.wasm_grants) == {"primary-producer"}
    assert s.wasm_grants["primary-producer"] == Grant(mounts=(
        Mount(str(root / "docs"), "/project/docs", "ro"),
        Mount(str(root / "src/app"), "/project/src/app", "ro"),
        Mount(str(root / ".agentteams/wasm/scratch/primary-producer"), "/scratch", "rw"),
    ), limits=DEFAULT_LIMITS)


def test_non_default_limits_give_every_slug_an_entry_without_tokens(tmp_path):
    block = {"enabled": True, "limits": {"memory_mib": 128, "timeout_s": 30}, **_GRANT}
    s = effective_sandbox_state(_wasm_manifest(block, tmp_path))
    limits = Limits(DEFAULT_LIMITS.fuel, 128 << 20, 30)
    assert set(s.wasm_grants) == set(_SLUGS)
    assert s.wasm_grants["navigator"] == Grant(mounts=(), limits=limits)
    assert all(g.limits == limits for g in s.wasm_grants.values())
    assert ss.state_tokens(s) == {"wasm:primary-producer:read:docs", "wasm:primary-producer:read:src/app",
                                  "wasm:primary-producer:scratch"}


def test_unknown_project_root_with_a_mount_fails_closed():
    with pytest.raises(ValueError, match="project root is unknown"):
        effective_sandbox_state(_wasm_manifest({"enabled": True, **_GRANT}, None))
    # Limits only (no mount) need no root.
    s = effective_sandbox_state(_wasm_manifest({"enabled": True, "limits": {"timeout_s": 9}}, None))
    assert all(not g.mounts for g in s.wasm_grants.values())


def test_symlink_escape_is_rechecked_under_the_real_root(tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "docs").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        effective_sandbox_state(_wasm_manifest({"enabled": True, **_GRANT}, tmp_path / "p"))


def test_os_half_needs_no_project_root_even_with_wasm_grants():
    m = _wasm_manifest({"enabled": True, **_GRANT}, None)
    assert ss.os_switch_state(m) == (True, "profile:confined")
    assert os_sandbox_requested(m, "claude:sandbox") is True


def test_wasm_record_round_trip(tmp_path):
    s = effective_sandbox_state(_wasm_manifest({"enabled": True, "limits": {"fuel": 5}, **_GRANT}, tmp_path))
    rec = s.to_record(project_root=tmp_path)
    assert rec["wasm"] == "on" and rec["wasm_grants"]["primary-producer"]["tokens"] == [
        "wasm:primary-producer:read:docs", "wasm:primary-producer:read:src/app", "wasm:primary-producer:scratch"]
    assert rec["wasm_grants"]["navigator"] == {"tokens": [], "limits": {
        "fuel": 5, "max_memory_bytes": DEFAULT_LIMITS.max_memory_bytes, "timeout_s": DEFAULT_LIMITS.timeout_s}}
    assert ss.SandboxState.from_record(rec, project_root=tmp_path) == s


@pytest.mark.parametrize("bad", ["ON", True, 1])
def test_bad_wasm_cli_value_fails_closed(bad, tmp_path):
    with pytest.raises(ValueError, match="--wasm-sandbox"):
        effective_sandbox_state(_wasm_manifest(None, tmp_path, bad))
