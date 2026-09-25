"""Sandbox-integration hardening battery (2026-W39 audit).

Each test cites the audit finding it pins (C/H/M/L ids from
tmp/by-week/2026-W39/sandbox-audit-findings.md — @security F#, @adversarial SEV#,
@code-hygiene RANK#, @technical-validator live checks). These encode the DESIRED behavior
of the deepened sandbox integration; a failing test here is an unfixed defect.

Scoped-but-unfixed items are marked xfail(strict=True) so they are tracked and will trip
the day they are fixed (turning into a reminder to un-xfail), never silently pass.
"""
from __future__ import annotations

import sys

import pytest

from agentteams.frameworks._goose_sandbox_emit import (
    _build_seatbelt_profile,
    _seatbelt_egress_rule,
    _seatbelt_path_expr,
)

IS_MAC = sys.platform == "darwin"


# ---------------------------------------------------------------------------
# H2 — _seatbelt_path_expr must fail closed on porous/malformed inputs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "/",            # bare root -> (subpath "/") re-allows the whole FS
    "//",
    "/../",
    "..",           # traversal
    "../x",
    "x/../../etc",
    "a\nb",         # newline splits the s-expression
    "a\tb",
    "a\rb",
    'a"b',          # quote breaks the string literal
    "a\\b",         # backslash escape ambiguity
])
def test_seatbelt_path_expr_rejects_porous_inputs(bad):
    assert _seatbelt_path_expr(bad) is None


@pytest.mark.parametrize("good,expected_fragment", [
    (".", '(subpath (param "WORKSPACE_ROOT"))'),
    ("./src", '(string-append (param "WORKSPACE_ROOT") "/src")'),
    ("src", '(string-append (param "WORKSPACE_ROOT") "/src")'),
    ("/abs/x", '(subpath "/abs/x")'),
    ("~/.ssh", '(string-append (param "HOME_DIR") "/.ssh")'),
])
def test_seatbelt_path_expr_accepts_safe_inputs(good, expected_fragment):
    out = _seatbelt_path_expr(good)
    assert out is not None and expected_fragment in out


# ---------------------------------------------------------------------------
# M2 / F5 — _build_seatbelt_profile fails CLOSED on unrepresentable paths
# ---------------------------------------------------------------------------

def test_profile_raises_on_unrepresentable_write_root():
    with pytest.raises(ValueError, match="write_roots"):
        _build_seatbelt_profile(["/"], None, None)
    with pytest.raises(ValueError, match="write_roots"):
        _build_seatbelt_profile(["../escape"], None, None)


def test_profile_raises_on_unrepresentable_deny_read():
    with pytest.raises(ValueError, match="protected_read_paths"):
        _build_seatbelt_profile(["."], deny_read=['~/a"b'], egress_endpoint=None)


def test_profile_empty_write_roots_falls_back_to_workspace_never_open():
    # None/empty INPUT is the documented default, not an error: fall back to WORKSPACE_ROOT.
    prof = _build_seatbelt_profile(None, None, None)
    assert '(subpath (param "WORKSPACE_ROOT"))' in prof
    assert "(deny file-write*)" in prof


# ---------------------------------------------------------------------------
# TV — egress endpoint validity (Seatbelt remote ip accepts only localhost/*)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ok", ["localhost", "localhost:8888", "*", "*:443"])
def test_egress_rule_accepts_loopback_forms(ok):
    assert _seatbelt_egress_rule(ok) == ok


@pytest.mark.parametrize("bad", [
    "127.0.0.1:8888", "1.2.3.4:443", "api.openai.com:443",
    "localhost:notaport", 'localhost"x', "localhost\n", None, "",
])
def test_egress_rule_rejects_unloadable_forms(bad):
    assert _seatbelt_egress_rule(bad) is None


# ---------------------------------------------------------------------------
# C1 — goose_egress_proxy is REACHABLE: brief -> manifest -> emitted profile
# ---------------------------------------------------------------------------

def test_goose_egress_proxy_is_wired_through_build_manifest():
    from agentteams import analyze
    m = analyze.build_manifest(
        {"project_goal": "x", "privilege_profile": "exclusive",
         "goose_egress_proxy": "localhost:8888"},
        framework="goose",
    )
    assert m.get("goose_egress_proxy") == "localhost:8888"


@pytest.mark.skipif(not IS_MAC, reason="goose Seatbelt emits on macOS only")
def test_exclusive_goose_manifest_emits_egress_allow_and_network_deny():
    from agentteams import analyze
    from agentteams.frameworks._goose_sandbox_emit import goose_sandbox_output_files
    m = analyze.build_manifest(
        {"project_goal": "x", "privilege_profile": "exclusive",
         "goose_egress_proxy": "localhost:8888"},
        framework="goose",
    )
    files = dict(goose_sandbox_output_files(m))
    prof = files["../sandbox.sb"]
    assert "(deny network*)" in prof
    assert '(allow network* (remote ip "localhost:8888"))' in prof


@pytest.mark.skipif(not IS_MAC, reason="goose Seatbelt emits on macOS only")
def test_confined_goose_manifest_leaves_network_open():
    from agentteams import analyze
    from agentteams.frameworks._goose_sandbox_emit import goose_sandbox_output_files
    m = analyze.build_manifest(
        {"project_goal": "x", "privilege_profile": "confined"}, framework="goose"
    )
    prof = dict(goose_sandbox_output_files(m))["../sandbox.sb"]
    assert "(deny network*)" not in prof
    # control-plane deny-write present even for confined.
    assert "agent-privilege.json" in prof


# ---------------------------------------------------------------------------
# TV live enforcement (macOS): the goose confined profile actually confines
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not IS_MAC, reason="Seatbelt enforcement is macOS-only")
def test_live_confined_profile_enforces(tmp_path):
    import os
    import shutil
    import subprocess

    if not shutil.which("sandbox-exec"):
        pytest.skip("sandbox-exec not available")
    ws = tmp_path / "ws"
    ws.mkdir()
    prof = tmp_path / "sandbox.sb"
    prof.write_text(_build_seatbelt_profile(["."], None, None), encoding="utf-8")
    home = os.path.expanduser("~")

    def run(cmd):
        return subprocess.run(
            ["sandbox-exec", "-D", f"WORKSPACE_ROOT={ws}", "-D", f"HOME_DIR={home}",
             "-f", str(prof), "/bin/sh", "-c", cmd],
            capture_output=True, text=True,
        ).returncode

    # write inside workspace -> allowed
    assert run(f"echo ok > {ws}/inside.txt") == 0
    assert (ws / "inside.txt").exists()
    # write OUTSIDE workspace AND outside the ephemeral /private/tmp,/private/var/folders
    # carve-out — a file under the real $HOME — must be DENIED. (tmp_path itself lives under
    # /private/var/folders, which the profile intentionally re-allows, so it cannot be the
    # discriminator here.)
    outside = os.path.join(home, f".agentteams_sandbox_probe_{os.getpid()}")
    try:
        assert run(f"echo x > {outside}") != 0
        assert not os.path.exists(outside), "write escaped confinement to $HOME"
    finally:
        if os.path.exists(outside):  # only if a regression let it through
            os.remove(outside)


@pytest.mark.skipif(not IS_MAC, reason="Seatbelt enforcement is macOS-only")
def test_live_profile_loads_under_sandbox_exec(tmp_path):
    # A regression for the TV finding: the emitted profile must actually LOAD (a bad rule
    # makes sandbox-exec exit 65 before running anything). true(1) under the profile => loads.
    import shutil
    import subprocess

    if not shutil.which("sandbox-exec"):
        pytest.skip("sandbox-exec not available")
    # (write_roots, deny_read, egress, deny_network) — every emitted variant must LOAD.
    variants = [
        (["."], None, None, False),                 # confined (network open)
        (["."], None, None, True),                  # exclusive, isolated
        (["."], None, "localhost:8888", True),      # exclusive + loopback egress (the TV case)
        (["."], ["~/.ssh"], None, True),            # exclusive + read-exclusion
    ]
    for wr, dr, ep, dn in variants:
        prof = tmp_path / "p.sb"
        prof.write_text(
            _build_seatbelt_profile(wr, deny_read=dr, egress_endpoint=ep, deny_network=dn),
            encoding="utf-8",
        )
        rc = subprocess.run(
            ["sandbox-exec", "-D", f"WORKSPACE_ROOT={tmp_path}", "-D", f"HOME_DIR={tmp_path}",
             "-f", str(prof), "/usr/bin/true"],
            capture_output=True, text=True,
        ).returncode
        assert rc == 0, f"profile variant egress={ep!r} deny_network={dn} failed to load (rc={rc})"


# ---------------------------------------------------------------------------
# H4 — launcher emit fails LOUD (not silent []) when the asset is missing
# ---------------------------------------------------------------------------

def test_linux_launcher_missing_asset_fails_loud(monkeypatch):
    from agentteams.frameworks import _linux_sandbox_emit as lse
    monkeypatch.setattr(lse, "_read_sandbox_asset", lambda *_a, **_k: "")
    with pytest.raises(FileNotFoundError, match="launcher asset"):
        lse.linux_sandbox_output_files({"privilege_profile": "confined"}, platform="linux")


def test_macos_launcher_missing_asset_fails_loud(monkeypatch):
    from agentteams.frameworks import _linux_sandbox_emit as lse
    monkeypatch.setattr(lse, "_read_sandbox_asset", lambda *_a, **_k: "")
    with pytest.raises(FileNotFoundError, match="launcher asset"):
        lse.macos_sandbox_output_files({"privilege_profile": "confined"}, platform="darwin")


def test_launcher_not_requested_still_returns_empty(monkeypatch):
    # cooperative (not requested) must NOT raise even if the asset is missing.
    from agentteams.frameworks import _linux_sandbox_emit as lse
    monkeypatch.setattr(lse, "_read_sandbox_asset", lambda *_a, **_k: "")
    assert lse.linux_sandbox_output_files({"privilege_profile": "cooperative"}, platform="linux") == []


# ---------------------------------------------------------------------------
# F2 — claude on native Windows is NOT silent (advisory), but stays non-fatal
# ---------------------------------------------------------------------------

def test_claude_native_windows_gets_nonfatal_advisory():
    from agentteams.host_features import privilege_profile_advisory
    adv = privilege_profile_advisory("confined", "claude", ["claude:sandbox"], platform="win32")
    assert adv is not None
    assert adv["code"] == "privilege-profile-claude-native-windows-advisory"
    # non-fatal: only 'privilege-profile-unenforced-host' fail-closes.
    assert adv["code"] != "privilege-profile-unenforced-host"


def test_claude_linux_macos_no_advisory():
    from agentteams.host_features import privilege_profile_advisory
    assert privilege_profile_advisory("confined", "claude", ["claude:sandbox"], platform="linux") is None
    assert privilege_profile_advisory("confined", "claude", ["claude:sandbox"], platform="darwin") is None


# ---------------------------------------------------------------------------
# M8 — schema enum stays in sync with VALID_PRIVILEGE_PROFILES
# ---------------------------------------------------------------------------

def test_schema_enum_matches_valid_profiles():
    import json
    from pathlib import Path
    from agentteams.host_features import VALID_PRIVILEGE_PROFILES
    schema = json.loads(
        (Path(__file__).resolve().parents[1]
         / "agentteams/schemas/project-description.schema.json").read_text()
    )
    assert set(schema["properties"]["privilege_profile"]["enum"]) == set(VALID_PRIVILEGE_PROFILES)


# ---------------------------------------------------------------------------
# H5 / F4 — verify_sandbox_wiring catches a merge that drops denyWrite/denyRead
# ---------------------------------------------------------------------------

def _write_claude_settings(root, example_fs, live_fs):
    import json
    cd = root / ".claude"
    cd.mkdir(parents=True, exist_ok=True)
    (cd / "settings.hooks.example.json").write_text(
        json.dumps({"sandbox": {"enabled": True, "allowUnsandboxedCommands": False,
                                "filesystem": example_fs}}), encoding="utf-8")
    (cd / "settings.json").write_text(
        json.dumps({"sandbox": {"enabled": True, "allowUnsandboxedCommands": False,
                                "filesystem": live_fs}}), encoding="utf-8")


def test_verify_wiring_flags_dropped_denywrite(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    ex = {"allowWrite": ["."], "denyWrite": ["references/agent-privilege.json",
                                             ".claude/hooks/constitutional-gate.py"]}
    live = {"allowWrite": ["."], "denyWrite": []}  # operator dropped the control-plane deny
    _write_claude_settings(tmp_path, ex, live)
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert ok is False
    assert any("denyWrite" in m for m in msgs)


def test_verify_wiring_flags_shortened_denyread(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    ex = {"allowWrite": ["."], "denyRead": ["~/.ssh", "~/.aws"]}
    live = {"allowWrite": ["."], "denyRead": ["~/.ssh"]}  # dropped ~/.aws
    _write_claude_settings(tmp_path, ex, live)
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert ok is False
    assert any("denyRead" in m for m in msgs)


def test_verify_wiring_ok_when_fully_merged(tmp_path):
    from agentteams.frameworks.claude import verify_sandbox_wiring
    fs = {"allowWrite": ["."], "denyWrite": ["references/agent-privilege.json"],
          "denyRead": ["~/.ssh"]}
    _write_claude_settings(tmp_path, fs, dict(fs))
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert ok is True
    assert any("OK" in m for m in msgs)


# ---------------------------------------------------------------------------
# M4 — privilege_profile_explicit invariant across manifest shapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brief,expect_explicit", [
    ({"project_goal": "x"}, False),                              # defaulted confined
    ({"project_goal": "x", "privilege_profile": "confined"}, True),
    ({"project_goal": "x", "privilege_profile": "exclusive"}, True),
    ({"project_goal": "x", "privilege_profile": "cooperative"}, True),
])
def test_build_manifest_always_records_explicitness(brief, expect_explicit):
    from agentteams import analyze
    m = analyze.build_manifest(brief, framework="claude")
    assert "privilege_profile_explicit" in m
    assert m["privilege_profile_explicit"] is expect_explicit


def test_manifest_with_confined_but_no_flag_stays_fail_open():
    # The safe-direction guard: a manifest carrying confined WITHOUT the explicit flag (e.g. a
    # lossy round-trip) must NOT flip the live gate fail-closed (audit SEV-3#1 / F7).
    from agentteams.frameworks.claude import _apply_fail_closed_policy, _FAIL_CLOSED_SENTINEL
    hook = "x\n" + _FAIL_CLOSED_SENTINEL + "\ny\n"
    out = _apply_fail_closed_policy(hook, {"privilege_profile": "confined"})  # no explicit key
    assert out == hook  # unchanged => fail-open preserved


# ===========================================================================
# C2 — pinned-sync projection preserves confinement (privilege round-trip)
# ===========================================================================

def test_read_brief_privilege_captures_confining_profile(tmp_path):
    import json as _json
    from agentteams import multi_sync as ms
    d = tmp_path / ".agentteams"
    d.mkdir(parents=True)
    (d / "brief.json").write_text(_json.dumps({
        "project_name": "T", "privilege_profile": "confined",
        "workspace_write_roots": ["."], "goose_egress_proxy": "localhost:8888",
    }), encoding="utf-8")
    priv = ms._read_brief_privilege(tmp_path)
    assert priv["privilege_profile"] == "confined"
    assert priv["privilege_profile_explicit"] is True
    assert priv["goose_egress_proxy"] == "localhost:8888"


def test_read_brief_privilege_cooperative_is_empty(tmp_path):
    import json as _json
    from agentteams import multi_sync as ms
    d = tmp_path / ".agentteams"; d.mkdir(parents=True)
    (d / "brief.json").write_text(_json.dumps({"privilege_profile": "cooperative"}), encoding="utf-8")
    assert ms._read_brief_privilege(tmp_path) == {}
    # no brief at all -> empty
    assert ms._read_brief_privilege(tmp_path / "nope") == {}


@pytest.mark.skipif(not IS_MAC, reason="goose Seatbelt emits on macOS only")
def test_emit_privilege_artifacts_writes_goose_sandbox(tmp_path):
    from agentteams import multi_sync as ms
    (tmp_path / ".goose" / "recipes").mkdir(parents=True)
    written = ms._emit_privilege_artifacts(
        tmp_path, "goose", {"privilege_profile": "confined", "privilege_profile_explicit": True},
        dry_run=False,
    )
    assert (tmp_path / ".goose" / "sandbox.sb").is_file()
    assert any("sandbox.sb" in w for w in written)
    # NARROW: .goosehints must NOT be emitted by the privilege pass (no unrelated churn).
    assert not (tmp_path / ".goosehints").exists()


def test_emit_privilege_artifacts_noop_for_cooperative(tmp_path):
    from agentteams import multi_sync as ms
    assert ms._emit_privilege_artifacts(tmp_path, "goose", None, dry_run=False) == []
    assert ms._emit_privilege_artifacts(
        tmp_path, "goose", {"privilege_profile": "cooperative"}, dry_run=False) == []


@pytest.mark.skipif(not IS_MAC, reason="goose Seatbelt emits on macOS only")
def test_sync_init_projects_confinement_boundary(tmp_path):
    # Headline C2 regression: a confined pinned-sync team emits its boundary after sync_init.
    import json as _json
    from agentteams import multi_sync as ms
    # minimal claude pin agent
    ad = tmp_path / ".claude" / "agents"
    ad.mkdir(parents=True)
    (ad / "orchestrator.md").write_text(
        "---\nname: orchestrator\ndescription: The orchestrator.\n---\n\n# Orchestrator\n\nBody.\n",
        encoding="utf-8",
    )
    bd = tmp_path / ".agentteams"; bd.mkdir(parents=True)
    (bd / "brief.json").write_text(_json.dumps({
        "project_name": "T", "privilege_profile": "confined",
    }), encoding="utf-8")
    ms.sync_init(tmp_path, pin="claude", frameworks=["claude", "goose"])
    # goose boundary emitted
    assert (tmp_path / ".goose" / "sandbox.sb").is_file()
    # privilege persisted in the canonical hub
    team = _json.loads((tmp_path / ".agentteams" / "canonical" / "team.cai.json").read_text())
    assert team.get("privilege", {}).get("privilege_profile") == "confined"


# ===========================================================================
# C3 — live-host mechanism probe surfaces "emittable but not enforceable here"
# ===========================================================================

def test_mechanism_probe_nonlive_platform_is_none():
    from agentteams.host_features import os_sandbox_mechanism_available
    other = "win32" if sys.platform != "win32" else "linux"
    assert os_sandbox_mechanism_available(other) is None


def test_mechanism_probe_live_matches_host():
    import shutil
    from agentteams.host_features import os_sandbox_mechanism_available
    live = os_sandbox_mechanism_available()  # None arg => live platform
    if sys.platform == "darwin":
        assert live is (shutil.which("sandbox-exec") is not None)
    elif sys.platform.startswith("linux"):
        assert live in (True, False)  # depends on bwrap/userns
    else:
        assert live is None


def test_advisory_fires_when_mechanism_absent():
    from agentteams.host_features import privilege_profile_advisory
    adv = privilege_profile_advisory(
        "confined", "goose", ["goose:sandbox"], platform="linux", mechanism_available=False)
    assert adv is not None and adv["code"] == "privilege-profile-mechanism-unavailable"
    # non-fatal (only 'privilege-profile-unenforced-host' fail-closes).
    assert adv["code"] != "privilege-profile-unenforced-host"


def test_advisory_no_mechanism_warning_when_present():
    from agentteams.host_features import privilege_profile_advisory
    adv = privilege_profile_advisory(
        "confined", "goose", ["goose:sandbox"], platform="linux", mechanism_available=True)
    # mechanism present => falls through to the enforceable-but-manual-wire advisory, NOT the
    # mechanism-unavailable one.
    assert adv is None or adv["code"] != "privilege-profile-mechanism-unavailable"


def test_claude_excluded_from_mechanism_advisory():
    from agentteams.host_features import privilege_profile_advisory
    adv = privilege_profile_advisory(
        "confined", "claude", ["claude:sandbox"], platform="linux", mechanism_available=False)
    # claude's boundary is Claude Code's own sandbox, not our launcher -> not flagged here.
    assert adv is None or adv["code"] != "privilege-profile-mechanism-unavailable"


def test_read_brief_privilege_propagates_defaulted_confined(tmp_path):
    # adversarial closeout #1: a brief that OMITS privilege_profile accepts the confined
    # DEFAULT -> sync must still propagate a boundary (explicit=False keeps the hook fail-open).
    import json as _json
    from agentteams import multi_sync as ms
    d = tmp_path / ".agentteams"; d.mkdir(parents=True)
    (d / "brief.json").write_text(_json.dumps({"project_name": "T"}), encoding="utf-8")  # no key
    priv = ms._read_brief_privilege(tmp_path)
    assert priv.get("privilege_profile") == "confined"
    assert priv.get("privilege_profile_explicit") is False


def test_confined_goose_profile_verifies_ok_without_network_deny(tmp_path):
    # adversarial closeout #2: a healthy CONFINED goose profile (no deny network*) must NOT be
    # reported broken by the wiring verifier (network-deny is exclusive-only).
    from agentteams.frameworks._goose_sandbox_emit import (
        _build_seatbelt_profile, verify_goose_sandbox_wiring,
    )
    if not IS_MAC:
        pytest.skip("goose wiring verify is macOS-oriented")
    recipes = tmp_path / ".goose" / "recipes"; recipes.mkdir(parents=True)
    (tmp_path / ".goose" / "sandbox.sb").write_text(
        _build_seatbelt_profile(["."], None, None), encoding="utf-8")  # confined
    live = tmp_path / "config.yaml"; live.write_text("GOOSE_SANDBOX: 1\n", encoding="utf-8")
    ok, msgs = verify_goose_sandbox_wiring(
        tmp_path, {"privilege_profile": "confined"}, live_config_path=live)
    # must NOT fail for a missing (deny network*) — that token is exclusive-only now.
    assert not any("deny network*" in m for m in msgs)
