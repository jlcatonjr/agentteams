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
    # ENFORCEMENT is macOS-gated). codex/copilot still have no sandbox emitter, so their
    # `:sandbox` token must still fail loudly rather than validate and confine nothing.
    validate("goose:sandbox")  # now valid — macOS-enforced goose confinement
    for ns in ("codex", "copilot-vscode", "copilot-cli"):
        with pytest.raises(HostFeatureError):
            validate(f"{ns}:sandbox")


def test_expand_privilege_profile():
    assert expand_privilege_profile("cooperative") == []
    assert expand_privilege_profile(None) == []
    assert expand_privilege_profile("confined") == ["claude:sandbox"]
    assert expand_privilege_profile("exclusive") == ["claude:sandbox"]
    # Unknown profile must never silently grant confinement.
    assert expand_privilege_profile("bogus") == []
    # P1-1: the expansion is framework-aware. goose unions its OWN sandbox token; every
    # other framework (and a missing framework) keeps the historical claude:sandbox.
    # This is a platform-independent REQUEST — enforceability is a separate decision
    # (see is_sandbox_capable), so the token is the same on macOS and Linux.
    assert expand_privilege_profile("confined", "goose") == ["goose:sandbox"]
    assert expand_privilege_profile("exclusive", "goose") == ["goose:sandbox"]
    assert expand_privilege_profile("confined", "codex") == ["claude:sandbox"]
    assert expand_privilege_profile("confined", "claude") == ["claude:sandbox"]


def test_validate_privilege_profile_normalizes_and_rejects():
    # CC-6: None/"" default to cooperative (a missing profile is not a typo)...
    assert validate_privilege_profile(None) == "cooperative"
    assert validate_privilege_profile("") == "cooperative"
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

    # Linux: the framework-neutral bwrap launcher enforces for ANY framework → never raises.
    monkeypatch.setattr(sys, "platform", "linux")
    for fw in ("codex", "goose", "claude"):
        m = {"privilege_profile": "confined"}
        resolve_host_features_and_advise(m, [], fw, allow_unenforced=False)  # must not raise
        assert not m.get("advisories")

    # macOS: goose enforces via Seatbelt (no raise, resolves the token); codex has no macOS
    # boundary and still fails closed.
    monkeypatch.setattr(sys, "platform", "darwin")
    gm = {"privilege_profile": "confined"}
    resolve_host_features_and_advise(gm, [], "goose", allow_unenforced=False)
    assert gm["host_features"] == ["goose:sandbox"]
    assert not gm.get("advisories")
    with pytest.raises(PrivilegeConfinementError, match="fail-closed"):
        resolve_host_features_and_advise({"privilege_profile": "confined"}, [], "codex",
                                         allow_unenforced=False)


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
        "filesystem": {
            "allowWrite": ["."],
            # D-3: the control plane is denied even inside the write root (deny-over-allow).
            "denyWrite": [
                "references/agent-privilege.json",
                ".claude/hooks/constitutional-gate.py",
            ],
        },
        "allowUnsandboxedCommands": False,
    }
    assert _build_sandbox_block(["./a"])["filesystem"]["allowWrite"] == ["./a"]


def test_build_sandbox_block_denywrite_protects_the_switch():
    """D-3: every emitted sandbox block denies in-sandbox writes to the enforce-signing
    switch (which lives inside the write root, unlike the .claude/-auto-protected files)."""
    for roots, deny_read in ((None, None), (["."], ["~/.ssh"]), (["./src"], None)):
        fs = _build_sandbox_block(roots, deny_read)["filesystem"]
        assert "references/agent-privilege.json" in fs["denyWrite"], (
            "the enforce_decision_signing switch must be write-denied in every profile"
        )
        # denyWrite must not accidentally also block the legitimate write roots.
        assert fs["allowWrite"] and "." not in fs["denyWrite"]


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

# --------------------------------------------------------------------------
# P3 — read-exclusion (exclusive profile) + P2×P3 + P3b advisory
# --------------------------------------------------------------------------

def test_build_sandbox_block_confined_has_no_denyread():
    # confined stays byte-identical (no denyRead/allowRead) — the exact-equality contract.
    block = _build_sandbox_block(["."], None)
    assert "denyRead" not in block["filesystem"]
    assert "allowRead" not in block["filesystem"]


def test_build_sandbox_block_exclusive_adds_denyread_and_allowread():
    block = _build_sandbox_block(["."], ["~/.ssh", "~/sibling"])
    fs = block["filesystem"]
    assert fs["denyRead"] == ["~/.ssh", "~/sibling"]
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
    assert "denyRead" not in fs_conf


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
    assert not ok and any("write roots differ" in m for m in msgs)


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
    hook = _emitted_hook({"host_features": ["claude:sandbox"], "privilege_profile": "exclusive"})
    assert _fail_closed_flag(hook) == "True"


def test_cc2_confined_emits_fail_closed_hook():
    hook = _emitted_hook({"host_features": ["claude:sandbox"], "privilege_profile": "confined"})
    assert _fail_closed_flag(hook) == "True"


def test_cc2_cooperative_stays_fail_open():
    hook = _emitted_hook({"privilege_profile": "cooperative"})
    assert _fail_closed_flag(hook) == "False"


def test_cc2_optout_keeps_fail_open_even_for_exclusive():
    hook = _emitted_hook(
        {
            "host_features": ["claude:sandbox"],
            "privilege_profile": "exclusive",
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

    # codex/copilot expose no per-framework OS sandbox, but Linux enforces framework-neutrally
    # (the emitted bwrap launcher wraps any process). So they advise only OFF Linux (macOS/Windows).
    for framework in ("codex", "copilot-vscode", "copilot-cli"):
        for profile in ("confined", "exclusive"):
            assert privilege_profile_advisory(profile, framework, platform="linux") is None
            for plat in ("darwin", "win32"):
                adv = privilege_profile_advisory(profile, framework, platform=plat)
                assert adv is not None
                assert adv["code"] == "privilege-profile-unenforced-host"
                assert "ADVISORY ONLY" in adv["message"]
    # goose is OS-enforceable on macOS (Seatbelt) AND Linux (the neutral launcher); it fires the
    # advisory only on Windows. Exercise all three branches deterministically via the override.
    for profile in ("confined", "exclusive"):
        assert privilege_profile_advisory(profile, "goose", platform="darwin") is None
        assert privilege_profile_advisory(profile, "goose", platform="linux") is None
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
    # Linux-neutral flip: goose is enforced on macOS (Seatbelt) AND Linux (neutral launcher),
    # so its direct token advises only on Windows. codex is enforced framework-neutrally on
    # Linux, so its direct token advises only off Linux (macOS/Windows).
    from agentteams.host_features import privilege_profile_advisory

    # goose: no advisory on macOS or Linux; advisory on Windows.
    assert (
        privilege_profile_advisory("cooperative", "goose", ["goose:sandbox"], platform="darwin")
        is None
    )
    assert (
        privilege_profile_advisory("cooperative", "goose", ["goose:sandbox"], platform="linux")
        is None
    )
    adv = privilege_profile_advisory("cooperative", "goose", ["goose:sandbox"], platform="win32")
    assert adv is not None
    assert adv["code"] == "privilege-profile-unenforced-host"
    # codex: enforced framework-neutrally on Linux (no advisory); advisory on Windows.
    assert (
        privilege_profile_advisory("cooperative", "codex", ["claude:sandbox"], platform="linux")
        is None
    )
    adv2 = privilege_profile_advisory("cooperative", "codex", ["claude:sandbox"], platform="win32")
    assert adv2 is not None
    assert adv2["code"] == "privilege-profile-unenforced-host"
    # ...and a claude:sandbox token on the claude framework is fine on any platform.
    assert privilege_profile_advisory("cooperative", "claude", ["claude:sandbox"]) is None


# ---------------------------------------------------------------------------
# D-3 Linux robustness (found on the testLinux VM, 2026-08-26): every path the
# emitted sandbox denyWrite names MUST be a file agentteams actually emits for a
# confined/exclusive team — else on Linux bwrap fails to bind the missing path and
# the WHOLE sandbox fails to initialize (macOS Seatbelt tolerates a missing deny
# path; bwrap does not). This guards against a future refactor silently dropping an
# emission and leaving a dangling denyWrite (the Linux-fragile partial state).
# ---------------------------------------------------------------------------

def test_every_denywrite_control_file_is_emitted(tmp_path):
    from agentteams.frameworks._sandbox_emit import _PROTECTED_WRITE_PATHS
    from agentteams.cli.artifacts import _write_agent_privilege_config, AGENT_PRIVILEGE_REL_PATH

    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T", "privilege_profile": "confined"},
        framework="claude",
    )
    # 1) the enforce_decision_signing switch is emitted (default-on manifest).
    switch = _write_agent_privilege_config(m, tmp_path)
    assert switch is not None and switch.as_posix().endswith(AGENT_PRIVILEGE_REL_PATH)

    # 2) the constitutional-gate hook is emitted by the claude adapter (../hooks/... -> .claude/hooks/).
    emitted = dict(ClaudeAdapter().extra_output_files({"host_features": ["claude:sandbox"]}))
    hook_targets = {
        rel.replace("../", ".claude/") for rel in emitted  # normalize the agents-dir-relative path
    }

    # 3) EVERY denyWrite entry must be covered by an actual emission — no dangling deny path.
    covered = {AGENT_PRIVILEGE_REL_PATH} | hook_targets
    for deny_path in _PROTECTED_WRITE_PATHS:
        assert deny_path in covered, (
            f"denyWrite names {deny_path!r} but nothing emits it — on Linux bwrap cannot bind a "
            f"missing deny path and the sandbox fails to initialize (D-3 fragility). "
            f"Emit the file or remove it from _PROTECTED_WRITE_PATHS."
        )


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
