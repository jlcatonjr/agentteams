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
import os
import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.frameworks.claude import (
    ClaudeAdapter,
    _build_sandbox_block,
    _exclusive_read_deny_paths,
    _inject_sandbox_block,
    _read_template_asset,
    _sandbox_feature_enabled,
)
from agentteams.host_features import (
    DEFAULT_PRIVILEGE_PROFILE,
    HostFeatureError,
    expand_privilege_profile,
    merge_profile_features,
    validate,
    validate_privilege_profile,
)


# --------------------------------------------------------------------------
# host_features: token + profile expansion
# --------------------------------------------------------------------------

def test_claude_sandbox_token_is_valid():
    validate("claude:sandbox")  # native claude confinement — the only real sandbox emitter


def test_bridge_sandbox_token_is_rejected():
    # C-4 (2026-08-26): a bridge never emits a sandbox block (privilege scoping is a native,
    # workspace-scoped non-goal of a bridge), so these tokens must FAIL rather than validate and
    # silently confine nothing. Requesting bridge confinement now errors loudly.
    for ns in ("bridge:copilot-vscode-to-claude", "bridge:copilot-cli-to-claude"):
        with pytest.raises(HostFeatureError):
            validate(f"{ns}:sandbox")


def test_sandbox_token_rejected_for_non_sandbox_namespaces():
    # P1-1 (2026-08-27): goose gained a real `sandbox` feature — the macOS Seatbelt
    # confinement emitter (frameworks/_goose_sandbox_emit.py) — so `goose:sandbox` now
    # VALIDATES (the token records the confinement request on every platform; only
    # ENFORCEMENT is macOS-gated). Phase 1a (2026-10-08): codex gained `codex:sandbox`, which
    # gates the operator-run `.codex/confined-run.example.sh` (Codex inside the neutral
    # launcher). copilot still has no sandbox emitter, so its `:sandbox` token must still fail
    # loudly rather than validate and confine nothing.
    validate("goose:sandbox")  # now valid — macOS-enforced goose confinement
    validate("codex:sandbox")  # valid — Codex run through the neutral launcher
    for ns in ("copilot-vscode", "copilot-cli"):
        with pytest.raises(HostFeatureError):
            validate(f"{ns}:sandbox")


def test_expand_privilege_profile():
    assert expand_privilege_profile("cooperative") == []
    # 2026-W39: a missing profile defaults to "confined" (sandbox-on), so None expands to
    # the sandbox token — NOT [] (that is now only the explicit-cooperative opt-out).
    assert expand_privilege_profile(None) == ["claude:sandbox"]
    assert expand_privilege_profile("confined") == ["claude:sandbox"]
    assert expand_privilege_profile("exclusive") == ["claude:sandbox"]
    # Unknown profile must never silently grant confinement.
    assert expand_privilege_profile("bogus") == []
    # P1-1: the expansion is framework-aware. goose unions its OWN sandbox token; every
    # other framework (and a missing framework) keeps the historical claude:sandbox; codex
    # (Phase 1a) unions codex:sandbox.
    # This is a platform-independent REQUEST — enforceability is a separate decision
    # (see is_sandbox_capable), so the token is the same on macOS and Linux.
    assert expand_privilege_profile("confined", "goose") == ["goose:sandbox"]
    assert expand_privilege_profile("exclusive", "goose") == ["goose:sandbox"]
    assert expand_privilege_profile("confined", "codex") == ["codex:sandbox"]
    assert expand_privilege_profile("exclusive", "codex") == ["codex:sandbox"]
    assert expand_privilege_profile("confined", "copilot-vscode") == ["claude:sandbox"]
    assert expand_privilege_profile("confined", "claude") == ["claude:sandbox"]


def test_validate_privilege_profile_normalizes_and_rejects():
    # CC-6: None/"" default to the DEFAULT_PRIVILEGE_PROFILE — "confined" as of 2026-W39
    # (a missing profile is not a typo, it is the sandbox-on default)...
    assert validate_privilege_profile(None) == "confined"
    assert validate_privilege_profile("") == "confined"
    assert validate_privilege_profile("cooperative") == "cooperative"
    assert validate_privilege_profile("confined") == "confined"
    assert validate_privilege_profile("exclusive") == "exclusive"
    # ...but a typo'd/unknown value fails closed rather than downgrading to unconfined.
    with pytest.raises(ValueError, match="unknown privilege_profile"):
        validate_privilege_profile("exclusve")


def test_build_manifest_rejects_unknown_privilege_profile():
    # CC-6: the parse boundary hard-errors so a typo cannot ship an unconfined team
    # that looks confined.
    with pytest.raises(ValueError, match="unknown privilege_profile"):
        analyze.build_manifest(
            {"project_goal": "x", "project_name": "T", "privilege_profile": "confind"},
            framework="claude",
        )


# --------------------------------------------------------------------------
# P1-2: fail-closed on an unenforceable host
# --------------------------------------------------------------------------

def test_p1_2_fail_closed_raises_on_unenforceable_host(monkeypatch):
    import sys

    from agentteams.cli.artifacts import (
        PrivilegeConfinementError,
        resolve_host_features_and_advise,
    )

    # Windows: no emittable boundary for codex OR goose → the fail-closed invariant holds.
    monkeypatch.setattr(sys, "platform", "win32")
    for fw in ("codex", "goose"):
        with pytest.raises(PrivilegeConfinementError, match="fail-closed"):
            resolve_host_features_and_advise({"privilege_profile": "confined"}, [], fw,
                                             allow_unenforced=False)

    # Linux: the framework-neutral bwrap launcher enforces for ANY framework → never raises
    # (enforcement IS available). claude uses its native settings-block sandbox (no advisory);
    # every other framework gets a NON-FATAL manual-wire advisory (the launcher must be wrapped),
    # never the fatal unenforced-host one.
    monkeypatch.setattr(sys, "platform", "linux")
    # Simulate a Linux host that HAS the enforcing mechanism (bwrap + userns); otherwise the
    # C3 live probe on this test host (which lacks bwrap) would correctly report
    # 'privilege-profile-mechanism-unavailable' instead of the manual-wire advisory.
    monkeypatch.setattr(
        "agentteams.host_features.os_sandbox_mechanism_available", lambda platform=None: True
    )
    for fw in ("codex", "goose"):
        m = {"privilege_profile": "confined"}
        resolve_host_features_and_advise(m, [], fw, allow_unenforced=False)  # must not raise
        codes = [a["code"] for a in m.get("advisories", [])]
        assert codes == ["privilege-profile-linux-launcher-manual-wire"], f"{fw}: {codes}"
    m = {"privilege_profile": "confined"}
    resolve_host_features_and_advise(m, [], "claude", allow_unenforced=False)  # must not raise
    assert not m.get("advisories")  # claude: native settings-block boundary, no manual-wire notice

    # macOS: goose enforces via its native Seatbelt path (no raise, resolves the token, no
    # advisory). codex/copilot now ALSO enforce via the emitted launcher's build_macos branch
    # (2026-W36) → no raise, a NON-FATAL manual-wire advisory (the launcher must be wrapped;
    # enforcement-UNVERIFIED until mac-escape-tests.sh passes on-host). No macOS fail-closed here.
    monkeypatch.setattr(sys, "platform", "darwin")
    gm = {"privilege_profile": "confined"}
    resolve_host_features_and_advise(gm, [], "goose", allow_unenforced=False)
    assert gm["host_features"] == ["goose:sandbox"]
    assert not gm.get("advisories")
    cm = {"privilege_profile": "confined"}
    resolve_host_features_and_advise(cm, [], "codex", allow_unenforced=False)  # must NOT raise now
    assert cm["host_features"] == ["codex:sandbox"]  # Phase 1a: codex's own token
    assert [a["code"] for a in cm.get("advisories", [])] == [
        "privilege-profile-macos-launcher-manual-wire"
    ]


def test_p1_2_allow_flag_degrades_to_advisory(monkeypatch):
    import sys

    from agentteams.cli.artifacts import resolve_host_features_and_advise

    # Windows: unenforceable for codex + goose → advisory persisted under the allow flag.
    monkeypatch.setattr(sys, "platform", "win32")
    for fw in ("codex", "goose"):
        m = {"privilege_profile": "exclusive"}
        resolve_host_features_and_advise(m, [], fw, allow_unenforced=True)
        assert "privilege-profile-unenforced-host" in [
            a["code"] for a in m.get("advisories", [])
        ]

    # Linux: enforced framework-neutrally → no advisory even under the allow flag.
    monkeypatch.setattr(sys, "platform", "linux")
    for fw in ("codex", "goose"):
        m = {"privilege_profile": "exclusive"}
        resolve_host_features_and_advise(m, [], fw, allow_unenforced=True)
        assert "privilege-profile-unenforced-host" not in [
            a["code"] for a in m.get("advisories", [])
        ]


def test_p1_2_enforceable_host_never_raises_even_fail_closed():
    from agentteams.cli.artifacts import resolve_host_features_and_advise

    manifest = {"privilege_profile": "confined"}
    # claude CAN enforce → no raise, no advisory, sandbox token present.
    resolve_host_features_and_advise(manifest, [], "claude", allow_unenforced=False)
    assert manifest["host_features"] == ["claude:sandbox"]
    assert not manifest.get("advisories")


def test_p1_2_cooperative_never_fails_closed():
    from agentteams.cli.artifacts import resolve_host_features_and_advise

    manifest = {"privilege_profile": "cooperative"}
    # No confinement requested → fail-closed posture is a no-op on any host.
    resolve_host_features_and_advise(manifest, [], "goose", allow_unenforced=False)
    assert manifest.get("host_features") == []


# --------------------------------------------------------------------------
# enforce_decision_signing switch (agent-position axis)
# --------------------------------------------------------------------------

def test_enforce_decision_signing_defaults_on_and_carries_to_manifest():
    m = analyze.build_manifest({"project_goal": "x", "project_name": "T"}, framework="claude")
    assert m["enforce_decision_signing"] is True  # default ON
    m2 = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T", "enforce_decision_signing": False},
        framework="claude",
    )
    assert m2["enforce_decision_signing"] is False  # explicit opt-out honored


def test_agent_privilege_config_emitted_with_switch_value(tmp_path):
    import json as _json
    from agentteams.cli.artifacts import _write_agent_privilege_config

    m = analyze.build_manifest({"project_goal": "x", "project_name": "T"}, framework="claude")
    path = _write_agent_privilege_config(m, tmp_path)
    assert path == tmp_path / "references" / "agent-privilege.json"
    assert _json.loads(path.read_text())["enforce_decision_signing"] is True
    # A manifest with no switch (older shape) emits nothing rather than a bogus default.
    assert _write_agent_privilege_config({}, tmp_path / "empty") is None


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

def test_build_manifest_default_profile_is_confined():
    # 2026-W39: sandbox is ENABLED BY DEFAULT — a brief that omits privilege_profile
    # resolves to "confined" (was "cooperative"). workspace_write_roots stays absent when
    # unset (the emitter defaults it to ["."]).
    m = analyze.build_manifest({"project_goal": "x"}, framework="claude")
    assert m["privilege_profile"] == "confined"
    assert "workspace_write_roots" not in m


def test_build_manifest_cooperative_opt_out():
    # The explicit opt-out still yields the no-boundary posture.
    m = analyze.build_manifest(
        {"project_goal": "x", "privilege_profile": "cooperative"}, framework="claude"
    )
    assert m["privilege_profile"] == "cooperative"


def test_build_manifest_records_profile_explicitness():
    # The fail-closed gate flip (claude._apply_fail_closed_policy) keys off this flag, so a
    # defaulted confined stays inert-until-merged. Omitted → False; set (even to confined) → True.
    assert analyze.build_manifest({"project_goal": "x"}, framework="claude")[
        "privilege_profile_explicit"] is False
    assert analyze.build_manifest(
        {"project_goal": "x", "privilege_profile": "confined"}, framework="claude"
    )["privilege_profile_explicit"] is True


def test_schema_default_matches_runtime_default():
    # CH-05 / SSOT: the schema's documentation `default` must equal the single runtime default
    # constant, or the schema silently lies if the constant flips again.
    import json

    schema = json.loads(
        (Path(__file__).resolve().parents[1]
         / "agentteams/schemas/project-description.schema.json").read_text()
    )
    assert (
        schema["properties"]["privilege_profile"]["default"]
        == DEFAULT_PRIVILEGE_PROFILE
    )


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
    assert _build_sandbox_block(None, platform="linux") == {
        "enabled": True,
        "filesystem": {
            "allowWrite": ["."],
            # D-3: the control plane is denied even inside the write root (deny-over-allow).
            "denyWrite": [
                ".claude/agents/references/agent-privilege.json",
                ".claude/hooks/constitutional-gate.py",
                ".claude/agents/references/authorized-verify-keys",
                # F-4: the whole config dir, so it cannot be renamed away and replaced.
                ".claude",
            ],
            # F-1: the operator signing-key dir is read-denied in EVERY block, with no allowRead.
            "denyRead": ["~/.config/agentteams/keys"],
        },
        "allowUnsandboxedCommands": False,
        # Fail closed (2026-09-30): without this, Claude Code silently runs every command
        # unsandboxed when its sandbox cannot start (measured on Linux with socat missing).
        "failIfUnavailable": True,
    }
    assert _build_sandbox_block(["./a"])["filesystem"]["allowWrite"] == ["./a"]


@pytest.mark.parametrize("plat", ["linux", "darwin"])
def test_build_sandbox_block_fails_closed_where_claude_enforces(plat):
    assert _build_sandbox_block(None, platform=plat)["failIfUnavailable"] is True
    assert _build_sandbox_block(["."], ["~/.ssh"], platform=plat)["failIfUnavailable"] is True


@pytest.mark.parametrize("plat", ["win32", "cygwin"])
def test_build_sandbox_block_is_advisory_on_native_windows(plat):
    # Claude Code has no OS sandbox on native Windows: failIfUnavailable there would stop it
    # starting at all, so the block stays advisory (the host_features Windows advisory says so).
    block = _build_sandbox_block(None, platform=plat)
    assert "failIfUnavailable" not in block
    assert block["enabled"] is True and block["allowUnsandboxedCommands"] is False


def test_emitted_settings_example_documents_fail_closed():
    ex = _read_template_asset("hooks/settings.hooks.example.json")
    comment = " ".join(json.loads(_inject_sandbox_block(ex, None))["_comment"])
    assert "failIfUnavailable" in comment and "socat" in comment
    assert "VERIFIED end-to-end on 2026-09-30" in comment
    # The Linux verdict is bounded by its precondition: never drop the AppArmor caveat.
    assert "bwrap-userns-restrict" in comment and "/etc/apparmor.d/bwrap" in comment


def test_build_sandbox_block_denywrite_protects_the_switch():
    """D-3: every emitted sandbox block denies in-sandbox writes to the enforce-signing
    switch (which lives inside the write root, unlike the .claude/-auto-protected files)."""
    for roots, deny_read in ((None, None), (["."], ["~/.ssh"]), (["./src"], None)):
        fs = _build_sandbox_block(roots, deny_read)["filesystem"]
        assert ".claude/agents/references/agent-privilege.json" in fs["denyWrite"], (
            "the enforce_decision_signing switch must be write-denied in every profile"
        )
        # denyWrite must not accidentally also block the legitimate write roots.
        assert fs["allowWrite"] and "." not in fs["denyWrite"]


def test_extra_output_files_emits_sandbox_when_enabled():
    files = dict(ClaudeAdapter().extra_output_files({"host_features": ["claude:sandbox"]}))
    d = json.loads(files["../settings.hooks.example.json"])
    assert d["sandbox"]["enabled"] is True
    assert d["sandbox"]["allowUnsandboxedCommands"] is False
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        assert d["sandbox"]["failIfUnavailable"] is True
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

# --------------------------------------------------------------------------
# P3 — read-exclusion (exclusive profile) + P2×P3 + P3b advisory
# --------------------------------------------------------------------------

def test_build_sandbox_block_confined_denies_only_the_signing_keys():
    # F-1 changed the old "confined has no denyRead" contract deliberately: confined now carries
    # the signing-key denyRead, and still NO allowRead (no over-confinement, no re-open).
    block = _build_sandbox_block(["."], None)
    assert block["filesystem"]["denyRead"] == ["~/.config/agentteams/keys"]
    assert "allowRead" not in block["filesystem"]


def test_build_sandbox_block_exclusive_adds_denyread_and_allowread():
    block = _build_sandbox_block(["."], ["~/.ssh", "~/sibling"])
    fs = block["filesystem"]
    # profile read-exclusion first, the signing-key dir appended after it
    assert fs["denyRead"] == ["~/.ssh", "~/sibling", "~/.config/agentteams/keys"]
    # P2×P3: write roots re-opened for read so a granted write target stays readable.
    assert fs["allowRead"] == fs["allowWrite"] == ["."]


def test_exclusive_read_deny_paths_only_for_exclusive():
    assert _exclusive_read_deny_paths({"privilege_profile": "confined"}) is None
    assert _exclusive_read_deny_paths({"privilege_profile": "cooperative"}) is None
    deny = _exclusive_read_deny_paths({"privilege_profile": "exclusive"})
    assert deny and "~/.ssh" in deny  # default set present
    assert "~/.azure" in deny  # P3-7: cloud-provider cred added to the default set
    # P3-7: routine-agent-work auth identities are deliberately NOT in the default
    # (they would break the gh/git toolchains the shipped PR agents use).
    assert "~/.config/gh" not in deny
    assert "~/.netrc" not in deny
    deny2 = _exclusive_read_deny_paths(
        {"privilege_profile": "exclusive", "protected_read_paths": ["~/sibling", "~/.ssh"]}
    )
    assert "~/sibling" in deny2 and deny2.count("~/.ssh") == 1  # operator path added, deduped


def test_exclusive_emits_denyread_confined_does_not():
    ex = dict(ClaudeAdapter().extra_output_files(
        {"host_features": ["claude:sandbox"], "privilege_profile": "exclusive"}
    ))
    fs_ex = json.loads(ex["../settings.hooks.example.json"])["sandbox"]["filesystem"]
    assert "denyRead" in fs_ex and fs_ex["allowRead"] == fs_ex["allowWrite"]

    conf = dict(ClaudeAdapter().extra_output_files(
        {"host_features": ["claude:sandbox"], "privilege_profile": "confined"}
    ))
    fs_conf = json.loads(conf["../settings.hooks.example.json"])["sandbox"]["filesystem"]
    assert fs_conf["denyRead"] == ["~/.config/agentteams/keys"]  # F-1 only
    assert "allowRead" not in fs_conf


def test_p2xp3_granted_path_stays_readable():
    # A granted foreign write path (in workspace_write_roots) must be in allowRead so
    # it isn't shadowed by a denyRead of the sibling workspace.
    manifest = {
        "host_features": ["claude:sandbox"], "privilege_profile": "exclusive",
        "workspace_write_roots": [".", "/abs/sibling/shared"],
        "protected_read_paths": ["/abs/sibling"],
    }
    fs = json.loads(dict(ClaudeAdapter().extra_output_files(manifest))[
        "../settings.hooks.example.json"])["sandbox"]["filesystem"]
    assert "/abs/sibling/shared" in fs["allowRead"]
    assert "/abs/sibling" in fs["denyRead"]


# --- SP-01 / P3-3: opt-in expanduser-resolved absolute denyRead paths -------


def test_resolve_deny_read_abspath_off_by_default_keeps_tilde():
    # Default (flag absent) is byte-identical: ~/ paths stay ~/-relative.
    deny = _exclusive_read_deny_paths({"privilege_profile": "exclusive"})
    assert deny and any(p.startswith("~/") for p in deny)
    assert not any(os.path.isabs(p) for p in deny)


def test_resolve_deny_read_abspath_resolves_all_entries():
    # Opt-in resolves every ~/ entry to an expanduser'd absolute path — no ~ left,
    # so enforcement no longer depends on Claude Code expanding ~ before the OS deny.
    deny = _exclusive_read_deny_paths(
        {"privilege_profile": "exclusive", "resolve_deny_read_abspath": True}
    )
    assert deny
    assert all(os.path.isabs(p) for p in deny)
    assert not any(p.startswith("~") for p in deny)
    assert os.path.abspath(os.path.expanduser("~/.ssh")) in deny


def test_resolve_deny_read_abspath_end_to_end_emits_abspaths_and_swapped_comment():
    manifest = {
        "host_features": ["claude:sandbox"],
        "privilege_profile": "exclusive",
        "resolve_deny_read_abspath": True,
    }
    example = dict(ClaudeAdapter().extra_output_files(manifest))[
        "../settings.hooks.example.json"
    ]
    doc = json.loads(example)
    deny = doc["sandbox"]["filesystem"]["denyRead"]
    assert all(os.path.isabs(p) for p in deny)
    # Comment swapped to the abspath variant (host-specific portability warning),
    # not the ~-expansion silent-no-op warning.
    comment_text = "\n".join(doc["_comment"])
    assert "resolved to ABSOLUTE paths" in comment_text  # abspath-variant marker
    # the ~-expansion RISK comment (default variant) is gone:
    assert "EVERY entry here is a silent" not in comment_text


def test_resolve_deny_read_abspath_flows_from_description_to_manifest():
    manifest = analyze.build_manifest(
        {
            "project_goal": "x",
            "project_name": "T",
            "privilege_profile": "exclusive",
            "resolve_deny_read_abspath": True,
        },
        framework="claude",
    )
    assert manifest.get("resolve_deny_read_abspath") is True


def test_resolve_deny_read_abspath_absent_from_manifest_by_default():
    manifest = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T", "privilege_profile": "exclusive"},
        framework="claude",
    )
    assert "resolve_deny_read_abspath" not in manifest  # byte-identical default


# --- SP-10 / P1-3: verify_sandbox_wiring (emitted-vs-live merge check) -------


def _write_claude(tmp_path, *, example=None, live=None):
    cdir = tmp_path / ".claude"
    cdir.mkdir(parents=True, exist_ok=True)
    if example is not None:
        (cdir / "settings.hooks.example.json").write_text(json.dumps(example), encoding="utf-8")
    if live is not None:
        (cdir / "settings.json").write_text(json.dumps(live), encoding="utf-8")
    return tmp_path


def _sandbox(enabled=True, unsandboxed=False, roots=("."),):
    return {
        "enabled": enabled,
        "filesystem": {"allowWrite": list(roots)},
        "allowUnsandboxedCommands": unsandboxed,
    }


def test_wiring_no_example_is_nothing_to_verify(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert ok and "nothing to verify" in msgs[0]


def test_wiring_cooperative_example_no_confinement(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    _write_claude(tmp_path, example={"hooks": {}})  # no sandbox block
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert ok and "no sandbox confinement" in msgs[0]


def test_wiring_emitted_but_not_merged_fails(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    _write_claude(tmp_path, example={"sandbox": _sandbox()})  # no live settings.json
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert not ok and "unmerged boundary" in msgs[0]


def test_wiring_live_lacks_enabled_sandbox_fails(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    _write_claude(tmp_path, example={"sandbox": _sandbox()}, live={"hooks": {}})
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert not ok and "NOT enforced" in msgs[0]


def test_wiring_escape_hatch_open_fails(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    _write_claude(
        tmp_path,
        example={"sandbox": _sandbox()},
        live={"sandbox": _sandbox(unsandboxed=True)},
    )
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert not ok and any("allowUnsandboxedCommands" in m for m in msgs)


def test_wiring_write_roots_mismatch_fails(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    _write_claude(
        tmp_path,
        example={"sandbox": _sandbox(roots=["."])},
        live={"sandbox": _sandbox(roots=["./src"])},
    )
    ok, msgs = verify_sandbox_wiring(tmp_path)
    # follow-up #2: emitted "." is WIDER than live "./src" -> the widening message, never "re-merge"
    assert not ok and any("WIDENS allowWrite by ['.']" in m for m in msgs)
    assert not any("re-merge so the boundary matches" in m for m in msgs)
    assert not any("./src" in m for m in msgs)  # live values are never echoed


def test_wiring_write_roots_narrowing_says_re_merge(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring

    _write_claude(
        tmp_path,
        example={"sandbox": _sandbox(roots=["./src"])},
        live={"sandbox": _sandbox(roots=["./src", "/srv/other"])},
    )
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert not ok and any("write roots differ" in m for m in msgs)
    assert not any("WIDENS" in m or "/srv/other" in m for m in msgs)


def test_wiring_correctly_merged_passes(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    block = _sandbox()
    _write_claude(tmp_path, example={"sandbox": block}, live={"sandbox": dict(block)})
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert ok and any(m.startswith("OK:") for m in msgs)


def _wiring_args():
    from types import SimpleNamespace
    return SimpleNamespace(
        restore_backup=None, scan_security=False, check_budget=False,
        check_rank=False, check_rank_strict=False, check_wiring=True,
    )


def test_check_wiring_dispatch_returns_nonzero_when_unmerged(tmp_path):
    from agentteams.cli.standalone_modes import run_standalone_modes
    _write_claude(tmp_path, example={"sandbox": _sandbox()})  # emitted, not merged
    rc = run_standalone_modes(_wiring_args(), {"framework": "claude"}, {}, tmp_path, tmp_path)
    assert rc == 1


@pytest.mark.parametrize("merged,expected", [(False, 1), (True, 0)])
def test_check_wiring_dispatch_maps_the_default_agents_dir_to_the_project(tmp_path, merged, expected):
    """Regression (check-wiring-passes-agents-dir): --output is <project>/.claude/agents in real use."""
    from agentteams.cli.standalone_modes import run_standalone_modes
    block = _sandbox()
    _write_claude(tmp_path, example={"sandbox": block}, live={"sandbox": dict(block)} if merged else None)
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    rc = run_standalone_modes(_wiring_args(), {"framework": "claude"}, {}, agents, agents)
    assert rc == expected


def test_check_wiring_dispatch_returns_zero_when_merged(tmp_path):
    from agentteams.cli.standalone_modes import run_standalone_modes
    block = _sandbox()
    _write_claude(tmp_path, example={"sandbox": block}, live={"sandbox": dict(block)})
    rc = run_standalone_modes(_wiring_args(), {"framework": "claude"}, {}, tmp_path, tmp_path)
    assert rc == 0


# --- CC-2: profile-dependent fail-closed constitutional-gate hook -----------

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _emitted_hook(manifest):
    files = dict(ClaudeAdapter().extra_output_files(manifest))
    return files["../hooks/constitutional-gate.py"]


def _fail_closed_flag(hook):
    # Anchor to the module-level assignment line (col 0), not the docstring's backtick
    # mention of the constant.
    import re

    m = re.search(r"^_FAIL_CLOSED_ON_ERROR = (True|False)$", hook, re.MULTILINE)
    assert m, "fail-closed constant not found in emitted hook"
    return m.group(1)


def test_cc2_exclusive_emits_fail_closed_hook():
    # EXPLICIT exclusive → fail-closed (privilege_profile_explicit True; 2026-W39 gate).
    hook = _emitted_hook({
        "host_features": ["claude:sandbox"], "privilege_profile": "exclusive",
        "privilege_profile_explicit": True,
    })
    assert _fail_closed_flag(hook) == "True"


def test_cc2_confined_emits_fail_closed_hook():
    # EXPLICIT confined → fail-closed.
    hook = _emitted_hook({
        "host_features": ["claude:sandbox"], "privilege_profile": "confined",
        "privilege_profile_explicit": True,
    })
    assert _fail_closed_flag(hook) == "True"


def test_cc2_defaulted_confined_stays_fail_open():
    # 2026-W39: confined is now the DEFAULT. A team that never set the field (explicit False)
    # must NOT get its live gate hook flipped to fail-closed on a routine --update — the
    # default flip is inert-until-merged. The disruptive fail-closed flip requires explicit
    # opt-in. (build_manifest with no privilege_profile yields exactly this shape.)
    hook = _emitted_hook({
        "host_features": ["claude:sandbox"], "privilege_profile": "confined",
        "privilege_profile_explicit": False,
    })
    assert _fail_closed_flag(hook) == "False"


def test_cc2_cooperative_stays_fail_open():
    hook = _emitted_hook({"privilege_profile": "cooperative"})
    assert _fail_closed_flag(hook) == "False"


def test_cc2_optout_keeps_fail_open_even_for_exclusive():
    hook = _emitted_hook(
        {
            "host_features": ["claude:sandbox"],
            "privilege_profile": "exclusive",
            "privilege_profile_explicit": True,
            "fallback_fail_open": True,
        }
    )
    assert _fail_closed_flag(hook) == "False"


def _load_hook_module():
    import importlib.util

    path = _REPO_ROOT / "agentteams/templates/universal/hooks/constitutional-gate.py"
    spec = importlib.util.spec_from_file_location("cc2_hook_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _boom():
    raise RuntimeError("boom")


def test_cc2_hook_entrypoint_fail_closed_denies_on_crash(monkeypatch, capsys):
    mod = _load_hook_module()
    mod._FAIL_CLOSED_ON_ERROR = True
    monkeypatch.setattr(mod, "main", _boom)
    with pytest.raises(SystemExit) as ei:
        mod._entrypoint()
    assert ei.value.code == 0  # _decide emits a deny decision and exits 0 (the block contract)
    out = capsys.readouterr().out
    assert '"permissionDecision": "deny"' in out
    assert "failing closed" in out


def test_cc2_hook_entrypoint_fail_open_reraises_on_crash(monkeypatch):
    mod = _load_hook_module()
    mod._FAIL_CLOSED_ON_ERROR = False
    monkeypatch.setattr(mod, "main", _boom)
    with pytest.raises(RuntimeError):
        mod._entrypoint()  # fail-open: the error propagates (harness treats it as allow)


def test_p3b_inbound_hardening_advisory():
    from agentteams import analyze
    from agentteams.cli.artifacts import finalize_privilege_wiring
    import tempfile
    from pathlib import Path

    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T", "privilege_profile": "exclusive"},
        framework="claude",
    )
    with tempfile.TemporaryDirectory() as d:
        finalize_privilege_wiring(m, [], "claude", Path(d))
    assert any(a["code"] == "privilege-profile-exclusive-inbound-hardening"
               for a in m.get("advisories", []))
    # confined does not get the P3b advisory
    m2 = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T", "privilege_profile": "confined"},
        framework="claude",
    )
    with tempfile.TemporaryDirectory() as d:
        finalize_privilege_wiring(m2, [], "claude", Path(d))
    assert not any(a["code"] == "privilege-profile-exclusive-inbound-hardening"
                   for a in m2.get("advisories", []))


def test_advisory_none_for_cooperative_or_claude():
    from agentteams.host_features import privilege_profile_advisory

    assert privilege_profile_advisory("cooperative", "goose") is None
    assert privilege_profile_advisory("confined", "claude") is None
    assert privilege_profile_advisory("exclusive", "claude") is None
    assert privilege_profile_advisory(None, "goose") is None


def test_advisory_fires_for_confinement_on_non_sandbox_host():
    from agentteams.host_features import privilege_profile_advisory

    # codex/copilot expose no per-framework OS sandbox. On Linux AND macOS they ARE enforceable via
    # the neutral launcher, but it is manual-wire → a NON-FATAL manual-wire advisory (not the fatal
    # unenforced-host one). Only on Windows/other is there no boundary → the fatal advisory.
    for framework in ("codex", "copilot-vscode", "copilot-cli"):
        for profile in ("confined", "exclusive"):
            # Pin mechanism_available=True: this asserts the platform-BRANCH advisory, not the
            # host's live bwrap probe. On a Linux host lacking bwrap (e.g. GitHub ubuntu runners)
            # the C3 probe would correctly return 'privilege-profile-mechanism-unavailable' first,
            # masking the manual-wire branch this test exercises.
            lin = privilege_profile_advisory(
                profile, framework, platform="linux", mechanism_available=True
            )
            assert lin is not None and lin["code"] == "privilege-profile-linux-launcher-manual-wire"
            assert "must" in lin["message"].lower() and "confine-run.sh" in lin["message"]
            # macOS (2026-W36): enforceable-but-manual via build_macos → NON-FATAL manual-wire
            # advisory carrying the honest residuals + the enforcement-UNVERIFIED gate.
            mac = privilege_profile_advisory(profile, framework, platform="darwin")
            assert mac is not None
            assert mac["code"] == "privilege-profile-macos-launcher-manual-wire"
            assert "must" in mac["message"].lower() and "confine-run.sh" in mac["message"]
            assert "UNCAPPED" in mac["message"] and "ENFORCEMENT-UNVERIFIED" in mac["message"]
            # Windows: no emittable boundary → the fatal unenforced-host advisory.
            win = privilege_profile_advisory(profile, framework, platform="win32")
            assert win is not None
            assert win["code"] == "privilege-profile-unenforced-host"
            assert "ADVISORY ONLY" in win["message"]
    # goose is enforced on macOS (Seatbelt, no advisory) and Linux (neutral launcher, manual-wire
    # advisory); only Windows gets the fatal unenforced-host advisory.
    for profile in ("confined", "exclusive"):
        assert privilege_profile_advisory(profile, "goose", platform="darwin") is None
        lin = privilege_profile_advisory(
            profile, "goose", platform="linux", mechanism_available=True
        )
        assert lin is not None and lin["code"] == "privilege-profile-linux-launcher-manual-wire"
        adv = privilege_profile_advisory(profile, "goose", platform="win32")
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


def test_integration_confined_on_goose_reflects_platform(monkeypatch):
    # Linux-neutral flip: a confined goose team is enforced on BOTH macOS (Seatbelt) and Linux
    # (the framework-neutral bwrap launcher); only Windows keeps the honest fail-closed advisory.
    import sys

    from agentteams.cli.artifacts import resolve_host_features_and_advise

    for plat, enforced in (("darwin", True), ("linux", True), ("win32", False)):
        monkeypatch.setattr(sys, "platform", plat)
        m = analyze.build_manifest(
            {"project_goal": "d", "privilege_profile": "confined"}, framework="goose"
        )
        resolve_host_features_and_advise(m, [], "goose", allow_unenforced=True)
        has_advisory = any(
            a["code"] == "privilege-profile-unenforced-host" for a in m.get("advisories", [])
        )
        assert has_advisory is (not enforced), f"{plat}: expected enforced={enforced}"


def test_advisory_fires_for_direct_token_on_non_sandbox_host():
    # Conflict-A: a directly-passed sandbox token on a host that cannot OS-enforce it must
    # warn too, even when privilege_profile is cooperative/unset.
    # Linux-neutral model: a direct token on goose/codex is enforced on macOS (goose Seatbelt) and
    # Linux (neutral launcher → NON-FATAL manual-wire advisory); Windows gets the fatal
    # unenforced-host advisory. claude is fine everywhere (native sandbox).
    from agentteams.host_features import privilege_profile_advisory

    # goose: no advisory on macOS; manual-wire on Linux; unenforced-host on Windows.
    assert (
        privilege_profile_advisory("cooperative", "goose", ["goose:sandbox"], platform="darwin")
        is None
    )
    # Pin mechanism_available=True: assert the platform branch, not the host's live bwrap probe
    # (a bwrap-less Linux host would return 'mechanism-unavailable' first, masking manual-wire).
    glin = privilege_profile_advisory(
        "cooperative", "goose", ["goose:sandbox"], platform="linux", mechanism_available=True
    )
    assert glin is not None and glin["code"] == "privilege-profile-linux-launcher-manual-wire"
    adv = privilege_profile_advisory("cooperative", "goose", ["goose:sandbox"], platform="win32")
    assert adv is not None
    assert adv["code"] == "privilege-profile-unenforced-host"
    # codex: manual-wire on Linux; unenforced-host on Windows. Its own codex:sandbox token counts
    # as a request, and the advisory names the Codex wrapper and the instruction-level label.
    clin = privilege_profile_advisory(
        "cooperative", "codex", ["codex:sandbox"], platform="linux", mechanism_available=True
    )
    assert clin is not None and clin["code"] == "privilege-profile-linux-launcher-manual-wire"
    assert ".codex/confined-run.example.sh" in clin["message"]
    assert "instruction-level" in clin["message"]
    cmac = privilege_profile_advisory(
        "cooperative", "codex", ["codex:sandbox"], platform="darwin", mechanism_available=True
    )
    assert cmac is not None and cmac["code"] == "privilege-profile-macos-launcher-manual-wire"
    assert ".codex/confined-run.example.sh" in cmac["message"]
    adv2 = privilege_profile_advisory("cooperative", "codex", ["codex:sandbox"], platform="win32")
    assert adv2 is not None
    assert adv2["code"] == "privilege-profile-unenforced-host"
    # ...and a claude:sandbox token on the claude framework is fine on any platform (native sandbox).
    assert privilege_profile_advisory("cooperative", "claude", ["claude:sandbox"]) is None
    assert privilege_profile_advisory("cooperative", "claude", ["claude:sandbox"], platform="linux") is None


# ---------------------------------------------------------------------------
# D-3 Linux robustness (found on the testLinux VM, 2026-08-26): every path the
# emitted sandbox denyWrite names MUST be a file agentteams actually emits for a
# confined/exclusive team — else on Linux bwrap fails to bind the missing path and
# the WHOLE sandbox fails to initialize (macOS Seatbelt tolerates a missing deny
# path; bwrap does not). This guards against a future refactor silently dropping an
# emission and leaving a dangling denyWrite (the Linux-fragile partial state).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("framework", ["claude", "goose"])
def test_every_denywrite_control_file_is_emitted(tmp_path, monkeypatch, framework):
    import posixpath
    import sys

    from agentteams.frameworks._sandbox_emit import protected_write_paths
    from agentteams.frameworks.goose import GooseAdapter
    from agentteams.cli.artifacts import _write_agent_privilege_config, AGENT_PRIVILEGE_REL_PATH

    # The goose Seatbelt profile (and so its deny) is emitted on darwin only.
    monkeypatch.setattr(sys, "platform", "darwin")
    adapter = ClaudeAdapter() if framework == "claude" else GooseAdapter()
    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T", "privilege_profile": "confined"},
        framework=framework,
    )
    # 1) the enforce_decision_signing switch is emitted (default-on manifest).
    switch = _write_agent_privilege_config(m, tmp_path)
    assert switch is not None and switch.as_posix().endswith(AGENT_PRIVILEGE_REL_PATH)

    # 2) the adapter's own emissions (gate hook, verify-key store sentinel, ...). They are
    # AGENTS-dir-relative; denyWrite paths are PROJECT-root-relative. Translate every emission
    # through the adapter's own agents dir — comparing the two frames directly is what let a bare
    # `references/agent-privilege.json` (a path nothing writes) pass here.
    root = Path("/proj")
    agents_rel = adapter.get_agents_dir(root).relative_to(root).as_posix()
    emitted = [
        posixpath.normpath(f"{agents_rel}/{rel}") for rel, _ in adapter.extra_output_files(m)
    ]
    emitted.append(f"{agents_rel}/{AGENT_PRIVILEGE_REL_PATH}")

    # 3) EVERY deny entry must be covered by an actual emission — no dangling deny path. A
    # DIRECTORY deny (the verify-key store) is covered by an emitted file inside it.
    deny_paths = protected_write_paths(framework)
    assert f"{agents_rel}/references/authorized-verify-keys" in deny_paths
    for deny_path in deny_paths:
        if framework == "goose" and deny_path == ".claude/hooks/constitutional-gate.py":
            # Pre-existing: the goose Seatbelt deny names the Claude gate hook, which a goose
            # team does not emit. Seatbelt tolerates a missing path (only bwrap does not).
            continue
        covered = any(p == deny_path or p.startswith(deny_path + "/") for p in emitted)
        assert covered, (
            f"denyWrite names {deny_path!r} but nothing emits it — on Linux bwrap cannot bind a "
            f"missing deny path and the sandbox fails to initialize (D-3 fragility). "
            f"Emit the file or remove it from _PROTECTED_WRITE_PATHS."
        )


# ---------------------------------------------------------------------------
# Decision-signing verify-key store (2026-09-30): write-denied, kept existing by a frozen
# sentinel emitted in the SAME branch as the deny. Closes key PLANTING only (F-1 open).
# ---------------------------------------------------------------------------

_STORE_SENTINEL = "references/authorized-verify-keys/README.md"


def test_verify_key_store_deny_is_locked_to_the_reader_in_both_frames():
    from agentteams.cli import decision_log
    from agentteams.frameworks._sandbox_emit import (
        VERIFY_KEY_STORE_SENTINEL_REL,
        _PROTECTED_WRITE_PATHS,
        protected_write_paths,
    )
    from agentteams.frameworks.goose import GooseAdapter

    root = Path("/proj")
    for fw, adapter in (("claude", ClaudeAdapter()), ("goose", GooseAdapter())):
        agents_rel = adapter.get_agents_dir(root).relative_to(root).as_posix()
        assert f"{agents_rel}/{decision_log._VERIFY_KEY_STORE_REL}" in protected_write_paths(fw)
    assert VERIFY_KEY_STORE_SENTINEL_REL == f"{decision_log._VERIFY_KEY_STORE_REL}/README.md"
    block = _build_sandbox_block(None)
    assert ".claude/agents/references/authorized-verify-keys" in block["filesystem"]["denyWrite"]
    assert [*_PROTECTED_WRITE_PATHS, ".claude"] == block["filesystem"]["denyWrite"]


def test_seatbelt_control_plane_denies_the_verify_key_store_after_the_workspace_allow():
    from agentteams.frameworks._goose_sandbox_emit import _build_seatbelt_profile

    prof = _build_seatbelt_profile(["."], deny_read=None, egress_endpoint=None)
    expr = ('(subpath (string-append (param "WORKSPACE_ROOT") '
            '"/.goose/recipes/references/authorized-verify-keys"))')
    assert expr in prof
    cp = prof.index(";; --- Control-plane protection")
    assert prof.index(expr) > cp > prof.index('(subpath (param "WORKSPACE_ROOT"))')


def test_verify_key_store_sentinel_is_frozen_and_never_a_pem():
    import hashlib

    from agentteams.frameworks._sandbox_emit import (
        VERIFY_KEY_STORE_SENTINEL_REL,
        VERIFY_KEY_STORE_SENTINEL_TEXT,
    )

    assert VERIFY_KEY_STORE_SENTINEL_REL == _STORE_SENTINEL
    assert not VERIFY_KEY_STORE_SENTINEL_REL.endswith(".pem")
    # FROZEN: once confined, the store is write-denied, so a changed sentinel would make an
    # in-sandbox --update fail on a read-only path. Do not update this digest casually.
    assert hashlib.sha256(VERIFY_KEY_STORE_SENTINEL_TEXT.encode()).hexdigest() == (
        "8f3466d172a12a200c7c70ad9ae88f75efa1ef0d9362823dd2a513936d4ec6c0"
    )


def test_load_verify_key_treats_a_sentinel_only_store_as_absent(tmp_path):
    from agentteams.cli.decision_log import _load_verify_key
    from agentteams.frameworks._sandbox_emit import VERIFY_KEY_STORE_SENTINEL_TEXT

    def _err(root: Path, key_id: str) -> str:
        with pytest.raises(RuntimeError) as exc:
            _load_verify_key(root, key_id)
        return str(exc.value)

    absent = tmp_path / "absent"
    absent.mkdir()
    present = tmp_path / "present"
    (present / "references" / "authorized-verify-keys").mkdir(parents=True)
    (present / _STORE_SENTINEL).write_text(VERIFY_KEY_STORE_SENTINEL_TEXT, encoding="utf-8")
    for key_id in ("op1", "README", "README.md"):
        assert "is unavailable" in _err(present, key_id)
        assert _err(present, key_id) == _err(absent, key_id)


def test_claude_deny_never_ships_without_the_sentinel(monkeypatch):
    """2026-09-30 contract: the sentinel ships with EVERY claude team (base adapter); the deny
    only with the sandbox. The invariant that matters, deny implies sentinel, still holds."""
    import agentteams.frameworks.claude as claude_mod

    def _emit(manifest):
        files = dict(ClaudeAdapter().extra_output_files(manifest))
        example = files.get("../settings.hooks.example.json")
        deny = example is not None and "sandbox" in json.loads(example)
        return deny, _STORE_SENTINEL in files

    assert _emit({"host_features": ["claude:sandbox"]}) == (True, True)
    assert _emit({"privilege_profile": "cooperative", "host_features": []}) == (False, True)
    real = claude_mod._read_template_asset
    for missing in ("hooks/constitutional-gate.py", "hooks/settings.hooks.example.json"):
        monkeypatch.setattr(
            claude_mod, "_read_template_asset", lambda rel, m=missing: "" if rel == m else real(rel)
        )
        assert _emit({"host_features": ["claude:sandbox"]}) == (False, True), missing
    assert not any(p.endswith(".pem") for p, _ in ClaudeAdapter().extra_output_files(
        {"host_features": ["claude:sandbox"]}))


def test_goose_sentinel_ships_on_every_platform(monkeypatch):
    """2026-09-30 regression: the sentinel shipped only with the darwin Seatbelt profile, so a
    confined goose team on Linux was refused by its own launcher. It now ships with every goose
    team from the base adapter on every platform; the Seatbelt profile stays darwin-only."""
    import sys

    from agentteams.frameworks._goose_sandbox_emit import goose_sandbox_output_files
    from agentteams.frameworks.goose import GooseAdapter

    for profile in ("confined", "cooperative"):
        m = {"privilege_profile": profile, "framework": "goose"}
        if profile == "confined":
            m["host_features"] = ["goose:sandbox"]
        for plat in ("linux", "win32", "darwin"):
            monkeypatch.setattr(sys, "platform", plat)
            rels = [r for r, _ in GooseAdapter().extra_output_files(m)]
            assert _STORE_SENTINEL in rels, (profile, plat)
            assert len(rels) == len(set(rels)), (profile, plat, rels)
            sb = dict(goose_sandbox_output_files(m))
            assert ("../sandbox.sb" in sb) == (plat == "darwin" and profile == "confined")
            assert _STORE_SENTINEL not in sb  # single source: the base adapter


def test_pinned_sync_projects_the_sentinel_with_the_deny(tmp_path):
    from agentteams import multi_sync as ms

    written = ms._emit_privilege_artifacts(
        tmp_path, "claude", {"privilege_profile": "confined"}, dry_run=False
    )
    assert any(w.endswith("settings.hooks.example.json") for w in written)
    assert (tmp_path / ".claude" / "agents" / _STORE_SENTINEL).is_file()


def test_prune_never_removes_the_verify_key_store(tmp_path, monkeypatch):
    """--prune deletes only planned ``output_files`` the new manifest dropped; the store sentinel is
    an adapter extra (never planned), so neither it nor an operator key is ever a prune target."""
    import build_team
    from agentteams import drift
    from agentteams.cli import security_gate
    from agentteams.frameworks._sandbox_emit import VERIFY_KEY_STORE_SENTINEL_TEXT

    def _manifest(profile: str) -> dict:
        return analyze.build_manifest(
            {"project_goal": "x", "project_name": "T", "privilege_profile": profile},
            framework="claude",
        )

    confined, cooperative = _manifest("confined"), _manifest("cooperative")
    for m in (confined, cooperative):
        assert not any("authorized-verify-keys" in f["path"] for f in m["output_files"])

    out = tmp_path / ".claude" / "agents"
    store = out / "references" / "authorized-verify-keys"
    store.mkdir(parents=True)
    (out / _STORE_SENTINEL).write_text(VERIFY_KEY_STORE_SENTINEL_TEXT, encoding="utf-8")
    (store / "op1.pub.pem").write_text("operator key\n", encoding="utf-8")
    dropped = {"path": "dropped.agent.md", "template": "x", "type": "agent"}
    (out / "dropped.agent.md").write_text("gone\n", encoding="utf-8")
    old_log = {"output_files_map": [*confined["output_files"], dropped], "template_hashes": {},
               "agent_slug_list": confined.get("agent_slug_list", [])}
    templates = Path(__file__).resolve().parents[1] / "agentteams" / "templates"
    for new in (confined, cooperative):  # incl. sandbox switched OFF (the sentinel no longer emitted)
        report = drift.compute_structural_diff(old_log, new, templates)
        assert report.removed_files
        assert not any("authorized-verify-keys" in f["path"] for f in report.removed_files)
    monkeypatch.setattr(security_gate, "_assert_destructive_action_allowed", lambda *a, **k: None)
    assert build_team._prune_removed_files(report.removed_files, out, True, False) == 0
    assert not (out / "dropped.agent.md").exists()  # the prune really ran
    assert (out / _STORE_SENTINEL).read_text(encoding="utf-8") == VERIFY_KEY_STORE_SENTINEL_TEXT
    assert (store / "op1.pub.pem").read_text(encoding="utf-8") == "operator key\n"


def test_bridge_does_not_propagate_a_sandbox_block():
    # C-4 non-propagation (2026-08-26): privilege scoping is native-only and an explicit bridge
    # non-goal. A bridge namespace cannot carry claude:sandbox (rejected at validate — see
    # test_bridge_sandbox_token_is_rejected), so an emitted team whose host_features are
    # bridge/non-sandbox tokens produces NO sandbox block — the boundary is never propagated
    # across a bridge into a foreign repo.
    files = dict(ClaudeAdapter().extra_output_files(
        {"host_features": ["bridge:copilot-vscode-to-claude:subagents", "claude:hooks"]}
    ))
    example = files.get("../settings.hooks.example.json")
    if example is not None:
        assert "sandbox" not in json.loads(example), "a bridge/non-sandbox team must not emit a sandbox block"
    # And the feature gate itself is off for such a manifest.
    assert _sandbox_feature_enabled(
        {"host_features": ["bridge:copilot-vscode-to-claude:subagents"]}
    ) is False


def test_goose_control_plane_denies_the_goose_switch():
    """The goose Seatbelt profile protects the switch where a goose team writes it."""
    from agentteams.cli.artifacts import AGENT_PRIVILEGE_REL_PATH
    from agentteams.frameworks._sandbox_emit import protected_write_paths
    from agentteams.frameworks.goose import GooseAdapter

    root = Path("/proj")
    agents_rel = GooseAdapter().get_agents_dir(root).relative_to(root).as_posix()
    assert protected_write_paths("goose")[0] == f"{agents_rel}/{AGENT_PRIVILEGE_REL_PATH}"


def test_sandbox_deny_path_mismatch_warns_for_a_nondefault_output(tmp_path, capsys):
    """@security condition: a team outside the default agents dir keeps its switch where the
    emitted denyWrite does not look, so say so."""
    from agentteams.cli.generate_helpers import _warn_sandbox_deny_path_mismatch

    m = {"framework": "claude", "host_features": ["claude:sandbox"]}
    _warn_sandbox_deny_path_mismatch(m, tmp_path / ".claude" / "agents")
    assert capsys.readouterr().err == ""
    _warn_sandbox_deny_path_mismatch(m, tmp_path / "custom-agents")
    assert "not protected" in capsys.readouterr().err
    _warn_sandbox_deny_path_mismatch({"framework": "claude"}, tmp_path / "custom-agents")
    assert capsys.readouterr().err == ""  # no sandbox, nothing to warn about


def test_live_sandbox_without_fail_if_unavailable_gets_an_update_notice(tmp_path, capsys, monkeypatch):
    """PR-A (2026-09-30): a merged sandbox block lacking failIfUnavailable fails OPEN, so
    generate/update names the one-line fix. Never edits settings.json."""
    from agentteams.cli import generate_helpers
    from agentteams.cli.generate_helpers import _warn_live_sandbox_fails_open

    monkeypatch.setattr(sys, "platform", "linux")
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    live = tmp_path / ".claude" / "settings.json"
    m = {"framework": "claude"}

    assert _warn_live_sandbox_fails_open(m, agents) is False  # no settings.json
    live.write_text(json.dumps({"sandbox": {"enabled": True, "allowUnsandboxedCommands": False}}))
    before = live.read_text()
    assert _warn_live_sandbox_fails_open(m, agents) is True
    err = capsys.readouterr().err
    assert '"failIfUnavailable": true' in err and "FAILS OPEN" in err
    assert live.read_text() == before  # read-only
    assert _warn_live_sandbox_fails_open({"framework": "goose"}, agents) is False

    live.write_text(json.dumps({"sandbox": {"enabled": True, "failIfUnavailable": True}}))
    assert _warn_live_sandbox_fails_open(m, agents) is False
    live.write_text(json.dumps({"sandbox": {"enabled": False}}))
    assert _warn_live_sandbox_fails_open(m, agents) is False
    live.write_text("{ not json")
    assert _warn_live_sandbox_fails_open(m, agents) is False

    live.write_text(json.dumps({"sandbox": {"enabled": True}}))
    monkeypatch.setattr(sys, "platform", "win32")  # advisory-only block there: no notice
    assert _warn_live_sandbox_fails_open(m, agents) is False

    # rc8: wired into the every-run hook (generate/--update/--dry-run/--check), not the write path;
    # the path is printed project-relative (no absolute home path).
    monkeypatch.setattr(sys, "platform", "linux")
    capsys.readouterr()
    generate_helpers._apply_sibling_team_denies({"framework": "claude"}, tmp_path, agents)
    err = capsys.readouterr().err
    assert "FAILS OPEN" in err and str(tmp_path) not in err
