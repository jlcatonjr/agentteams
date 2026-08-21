"""Tests for the workspace privilege-scoping feature (Stage 1).

Covers the opt-in ``claude:sandbox`` host feature and ``privilege_profile``:
  * token validation in host_features
  * privilege_profile → feature-token expansion and union precedence
  * build_manifest carrying profile/roots
  * the emitted settings.hooks.example.json sandbox block (on/off, shape, roots)
  * the non-sandbox-host advisory
"""

from __future__ import annotations

import json

import pytest

from agentteams import analyze
from agentteams.frameworks.claude import (
    ClaudeAdapter,
    _build_sandbox_block,
    _inject_sandbox_block,
    _read_template_asset,
    _sandbox_feature_enabled,
)
from agentteams.host_features import (
    HostFeatureError,
    expand_privilege_profile,
    merge_profile_features,
    validate,
)


# --------------------------------------------------------------------------
# host_features: token + profile expansion
# --------------------------------------------------------------------------

def test_claude_sandbox_token_is_valid():
    validate("claude:sandbox")  # must not raise
    validate("bridge:copilot-vscode-to-claude:sandbox")
    validate("bridge:copilot-cli-to-claude:sandbox")


def test_sandbox_token_rejected_for_non_sandbox_namespaces():
    for ns in ("goose", "codex", "copilot-vscode", "copilot-cli"):
        with pytest.raises(HostFeatureError):
            validate(f"{ns}:sandbox")


def test_expand_privilege_profile():
    assert expand_privilege_profile("cooperative") == []
    assert expand_privilege_profile(None) == []
    assert expand_privilege_profile("confined") == ["claude:sandbox"]
    assert expand_privilege_profile("exclusive") == ["claude:sandbox"]
    # Unknown profile must never silently grant confinement.
    assert expand_privilege_profile("bogus") == []


def test_merge_profile_features_union_is_idempotent_and_order_preserving():
    assert merge_profile_features([], "confined") == ["claude:sandbox"]
    assert merge_profile_features(["claude:sandbox"], "confined") == ["claude:sandbox"]
    assert merge_profile_features(["claude:hooks"], "confined") == [
        "claude:hooks",
        "claude:sandbox",
    ]


def test_cooperative_does_not_strip_explicit_sandbox_token():
    # A directly-requested claude:sandbox survives a cooperative profile.
    assert merge_profile_features(["claude:sandbox"], "cooperative") == ["claude:sandbox"]


# --------------------------------------------------------------------------
# analyze.build_manifest
# --------------------------------------------------------------------------

def test_build_manifest_default_profile_is_cooperative():
    m = analyze.build_manifest({"project_goal": "x"}, framework="claude")
    assert m["privilege_profile"] == "cooperative"
    assert "workspace_write_roots" not in m


def test_build_manifest_carries_profile_and_roots():
    desc = {
        "project_goal": "x",
        "privilege_profile": "confined",
        "workspace_write_roots": ["./src", "./docs"],
    }
    m = analyze.build_manifest(desc, framework="claude")
    assert m["privilege_profile"] == "confined"
    assert m["workspace_write_roots"] == ["./src", "./docs"]


# --------------------------------------------------------------------------
# emission: the sandbox block
# --------------------------------------------------------------------------

def test_sandbox_feature_enabled_gate():
    assert _sandbox_feature_enabled({"host_features": ["claude:sandbox"]}) is True
    assert _sandbox_feature_enabled({"host_features": ["claude:hooks"]}) is False
    assert _sandbox_feature_enabled({}) is False


def test_build_sandbox_block_shape_and_defaults():
    assert _build_sandbox_block(None) == {
        "enabled": True,
        "filesystem": {"allowWrite": ["."]},
        "allowUnsandboxedCommands": False,
    }
    assert _build_sandbox_block(["./a"])["filesystem"]["allowWrite"] == ["./a"]


def test_extra_output_files_emits_sandbox_when_enabled():
    files = dict(ClaudeAdapter().extra_output_files({"host_features": ["claude:sandbox"]}))
    d = json.loads(files["../settings.hooks.example.json"])
    assert d["sandbox"]["enabled"] is True
    assert d["sandbox"]["allowUnsandboxedCommands"] is False
    assert d["sandbox"]["filesystem"]["allowWrite"] == ["."]
    # hooks block is preserved alongside the sandbox block
    assert "hooks" in d
    # the hook script always ships
    assert "../hooks/constitutional-gate.py" in files
    # comment explains the inert-until-merged wiring
    assert any("claude:sandbox" in line for line in d["_comment"])


def test_extra_output_files_no_sandbox_when_disabled_is_backward_compatible():
    files = dict(ClaudeAdapter().extra_output_files({"host_features": []}))
    d = json.loads(files["../settings.hooks.example.json"])
    assert "sandbox" not in d


def test_extra_output_files_emits_sandbox_from_profile_without_token():
    # F-2/F-4 regression: the emitter must be self-sufficient. On the convert.py /
    # render_pipeline.py paths the profile→host_features union never runs, so a
    # confined manifest reaches the emitter with an EMPTY host_features. The sandbox
    # must still emit — the emitter reads privilege_profile directly.
    manifest = {"host_features": [], "privilege_profile": "confined"}
    files = dict(ClaudeAdapter().extra_output_files(manifest))
    d = json.loads(files["../settings.hooks.example.json"])
    assert d["sandbox"]["enabled"] is True


def test_sandbox_feature_enabled_reads_profile_directly():
    assert _sandbox_feature_enabled({"privilege_profile": "confined"}) is True
    assert _sandbox_feature_enabled({"privilege_profile": "exclusive"}) is True
    assert _sandbox_feature_enabled({"privilege_profile": "cooperative"}) is False


def test_extra_output_files_respects_custom_write_roots():
    manifest = {"host_features": ["claude:sandbox"], "workspace_write_roots": ["./src"]}
    files = dict(ClaudeAdapter().extra_output_files(manifest))
    d = json.loads(files["../settings.hooks.example.json"])
    assert d["sandbox"]["filesystem"]["allowWrite"] == ["./src"]


def test_inject_sandbox_block_raises_loud_on_malformed_json():
    # F-3: fail loud, not open. Confinement was requested; silently shipping a
    # hooks-only example would hand the operator an unconfined team.
    with pytest.raises(ValueError):
        _inject_sandbox_block("{ not json", None)
    with pytest.raises(ValueError):
        _inject_sandbox_block("[]", None)  # valid JSON, but not an object


def test_emitted_settings_example_is_valid_json():
    ex = _read_template_asset("hooks/settings.hooks.example.json")
    out = _inject_sandbox_block(ex, None)
    json.loads(out)  # must not raise


# --------------------------------------------------------------------------
# advisory: unenforceable profile on a non-sandbox host
# --------------------------------------------------------------------------

def test_advisory_none_for_cooperative_or_claude():
    from agentteams.host_features import privilege_profile_advisory

    assert privilege_profile_advisory("cooperative", "goose") is None
    assert privilege_profile_advisory("confined", "claude") is None
    assert privilege_profile_advisory("exclusive", "claude") is None
    assert privilege_profile_advisory(None, "goose") is None


def test_advisory_fires_for_confinement_on_non_sandbox_host():
    from agentteams.host_features import privilege_profile_advisory

    for framework in ("goose", "codex", "copilot-vscode", "copilot-cli"):
        for profile in ("confined", "exclusive"):
            adv = privilege_profile_advisory(profile, framework)
            assert adv is not None
            assert adv["code"] == "privilege-profile-unenforced-host"
            assert "ADVISORY ONLY" in adv["message"]


def test_integration_confined_brief_flows_to_emitted_sandbox_block():
    # F-4: end-to-end through the real CLI wiring helper, not just isolated units.
    # description → build_manifest → resolve_host_features_and_advise → emitter.
    from agentteams.cli.artifacts import resolve_host_features_and_advise

    m = analyze.build_manifest({"project_goal": "demo", "privilege_profile": "confined"}, framework="claude")
    resolve_host_features_and_advise(m, [], "claude")
    assert "claude:sandbox" in m["host_features"]
    files = dict(ClaudeAdapter().extra_output_files(m))
    d = json.loads(files["../settings.hooks.example.json"])
    assert d["sandbox"]["enabled"] is True


def test_integration_confined_on_goose_persists_advisory():
    from agentteams.cli.artifacts import resolve_host_features_and_advise

    m = analyze.build_manifest({"project_goal": "d", "privilege_profile": "confined"}, framework="goose")
    resolve_host_features_and_advise(m, [], "goose")
    assert any(a["code"] == "privilege-profile-unenforced-host" for a in m.get("advisories", []))


def test_advisory_fires_for_direct_token_on_non_sandbox_host():
    # Conflict-A: a directly-passed claude:sandbox token on a non-Claude host must
    # warn too, even when privilege_profile is cooperative/unset.
    from agentteams.host_features import privilege_profile_advisory

    adv = privilege_profile_advisory("cooperative", "goose", ["claude:sandbox"])
    assert adv is not None
    assert adv["code"] == "privilege-profile-unenforced-host"
    # ...but a claude:sandbox token on the claude host is fine
    assert privilege_profile_advisory("cooperative", "claude", ["claude:sandbox"]) is None
