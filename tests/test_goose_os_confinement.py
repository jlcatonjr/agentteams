"""Tests for P1-1: OS-enforced (macOS Seatbelt) confinement for the Goose framework.

Covers the goose sandbox emitter (``frameworks/_goose_sandbox_emit.py``) and its wiring
verifier, the goose adapter's ``extra_output_files`` integration, and the honest
platform-gated / fail-closed posture (macOS emits a boundary; Linux/Windows advise).

The emitter is pure (no shell-out), so emission tests do not require Goose installed. The
verifier's build detection DOES probe the host, so those assertions stay tolerant of
whether ``goose``/``sandbox-exec`` are present.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams.frameworks.goose import GooseAdapter
from agentteams.frameworks._goose_sandbox_emit import (
    _build_seatbelt_profile,
    _goose_read_deny_paths,
    _goose_sandbox_feature_enabled,
    _seatbelt_path_expr,
    goose_sandbox_output_files,
    verify_goose_sandbox_wiring,
)
from agentteams.frameworks._sandbox_emit import _DEFAULT_PROTECTED_READ_PATHS

IS_MAC = sys.platform == "darwin"


# ---------------------------------------------------------------------------
# request gate
# ---------------------------------------------------------------------------

def test_feature_enabled_reads_both_sources():
    assert _goose_sandbox_feature_enabled({"host_features": ["goose:sandbox"]}) is True
    assert _goose_sandbox_feature_enabled({"privilege_profile": "confined"}) is True
    assert _goose_sandbox_feature_enabled({"privilege_profile": "exclusive"}) is True
    assert _goose_sandbox_feature_enabled({"host_features": ["goose:mcp"]}) is False
    assert _goose_sandbox_feature_enabled({"privilege_profile": "cooperative"}) is False
    assert _goose_sandbox_feature_enabled({}) is False


# ---------------------------------------------------------------------------
# path expression helper
# ---------------------------------------------------------------------------

def test_seatbelt_path_expr_forms():
    assert _seatbelt_path_expr(".") == '(subpath (param "WORKSPACE_ROOT"))'
    assert _seatbelt_path_expr("./src") == '(subpath (string-append (param "WORKSPACE_ROOT") "/src"))'
    assert _seatbelt_path_expr("/abs/x") == '(subpath "/abs/x")'
    assert _seatbelt_path_expr("~/.ssh") == '(subpath (string-append (param "HOME_DIR") "/.ssh"))'
    # Fail closed on a quote-bearing path rather than emit an unescaped/porous rule.
    assert _seatbelt_path_expr('a"b') is None


# ---------------------------------------------------------------------------
# profile content
# ---------------------------------------------------------------------------

def _active_rules(profile: str) -> list[str]:
    """Non-comment rule lines (Seatbelt comments begin with ``;;``)."""
    return [ln.strip() for ln in profile.splitlines() if ln.strip() and not ln.strip().startswith(";;")]


def test_confined_profile_denies_writes_and_network_but_not_reads():
    prof = _build_seatbelt_profile(["."], deny_read=None, egress_endpoint=None)
    assert "(allow default)" in prof
    assert "(deny file-write*)" in prof
    assert '(subpath (param "WORKSPACE_ROOT"))' in prof
    # deny-all network by default (Seatbelt file-denies do not cover sockets).
    assert "(deny network*)" in prof
    # no ACTIVE network allow rule (a comment may mention the syntax) -> isolated, not open.
    assert not any(r.startswith("(allow network*") for r in _active_rules(prof))
    # confined carries NO read-exclusion.
    assert "(deny file-read*" not in prof


def test_exclusive_profile_adds_read_exclusion_of_defaults_plus_siblings():
    siblings = ["/scratch/agent-b", "~/work/agent-c"]
    prof = _build_seatbelt_profile(
        ["."], deny_read=list(_DEFAULT_PROTECTED_READ_PATHS) + siblings, egress_endpoint=None
    )
    assert "(deny file-read*" in prof
    # every default protected path is present as a HOME_DIR-joined subpath (no ~ no-op risk).
    for p in _DEFAULT_PROTECTED_READ_PATHS:
        rest = p[2:]
        assert f'(subpath (string-append (param "HOME_DIR") "/{rest}"))' in prof
    # sibling agent scratch roots are denied too (absolute + home-relative forms).
    assert '(subpath "/scratch/agent-b")' in prof
    assert '(subpath (string-append (param "HOME_DIR") "/work/agent-c"))' in prof


def test_egress_proxy_flag_reallows_exactly_one_endpoint():
    prof = _build_seatbelt_profile(["."], deny_read=None, egress_endpoint="127.0.0.1:8888")
    assert "(deny network*)" in prof
    assert '(allow network* (remote ip "127.0.0.1:8888"))' in prof
    assert any(r.startswith("(allow network*") for r in _active_rules(prof))
    # absent proxy => deny-all, never open (no ACTIVE allow rule).
    prof2 = _build_seatbelt_profile(["."], deny_read=None, egress_endpoint=None)
    assert not any(r.startswith("(allow network*") for r in _active_rules(prof2))


def test_multiple_write_roots_are_all_confined():
    prof = _build_seatbelt_profile(["./src", "./docs"], None, None)
    assert '(subpath (string-append (param "WORKSPACE_ROOT") "/src"))' in prof
    assert '(subpath (string-append (param "WORKSPACE_ROOT") "/docs"))' in prof


def test_empty_write_roots_never_yields_no_writable_root():
    # A profile that denies every write would break the harness while LOOKING confined;
    # fall back to the workspace root explicitly (fail safe, not open).
    prof = _build_seatbelt_profile([], None, None)
    assert '(subpath (param "WORKSPACE_ROOT"))' in prof


# ---------------------------------------------------------------------------
# read-deny path assembly
# ---------------------------------------------------------------------------

def test_read_deny_paths_only_for_exclusive():
    assert _goose_read_deny_paths({"privilege_profile": "confined"}) is None
    assert _goose_read_deny_paths({"privilege_profile": "cooperative"}) is None
    deny = _goose_read_deny_paths(
        {"privilege_profile": "exclusive", "protected_read_paths": ["/scratch/sib"]}
    )
    assert deny is not None
    assert "/scratch/sib" in deny
    for p in _DEFAULT_PROTECTED_READ_PATHS:
        assert p in deny


# ---------------------------------------------------------------------------
# emission gating (platform-aware, fail-closed)
# ---------------------------------------------------------------------------

def _confined_goose_manifest(profile: str = "confined") -> dict:
    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T", "privilege_profile": profile},
        framework="goose",
    )
    m["host_features"] = ["goose:sandbox"]
    return m


@pytest.mark.skipif(not IS_MAC, reason="OS enforcement for goose is macOS-only")
def test_emits_profile_and_config_on_macos():
    files = dict(goose_sandbox_output_files(_confined_goose_manifest()))
    assert "../sandbox.sb" in files
    assert "../config.yaml.agentteams.example" in files
    assert "(deny file-write*)" in files["../sandbox.sb"]


def test_no_emission_when_not_requested():
    assert goose_sandbox_output_files({"privilege_profile": "cooperative"}) == []


def test_no_emission_off_macos(monkeypatch):
    # Simulate Linux/Windows: even with confinement requested, emit NOTHING (honest
    # fail-closed — the CLI advisory covers it). Never ship a non-enforcing profile.
    monkeypatch.setattr(sys, "platform", "linux")
    assert goose_sandbox_output_files(_confined_goose_manifest()) == []


def test_config_example_is_inert_and_never_a_live_path():
    files = dict(_build_config_files())
    cfg = files["../config.yaml.agentteams.example"]
    assert "GOOSE_SANDBOX: 1" in cfg
    assert "EXAMPLE ONLY" in cfg or "INERT" in cfg
    # Ships an example; must not instruct clobbering the live config.
    assert "never" in cfg.lower()


def _build_config_files():
    from agentteams.frameworks._goose_sandbox_emit import _build_config_example

    return [
        ("../config.yaml.agentteams.example", _build_config_example(["."], exclusive=False)),
    ]


# ---------------------------------------------------------------------------
# adapter integration
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not IS_MAC, reason="OS enforcement for goose is macOS-only")
def test_extra_output_files_includes_sandbox_on_macos():
    files = dict(GooseAdapter().extra_output_files(_confined_goose_manifest()))
    assert "../sandbox.sb" in files
    # additive: the pre-existing goose artifacts are still emitted.
    assert "../../.goosehints" in files


def test_extra_output_files_unchanged_for_cooperative():
    m = analyze.build_manifest(
        {"project_goal": "x", "project_name": "T"}, framework="goose"
    )
    files = dict(GooseAdapter().extra_output_files(m))
    assert "../sandbox.sb" not in files
    assert "../../.goosehints" in files  # normal emission intact


# ---------------------------------------------------------------------------
# wiring verifier
# ---------------------------------------------------------------------------

def _emit_into(root: Path, manifest: dict) -> None:
    agents_dir = root / ".goose" / "recipes"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in goose_sandbox_output_files(manifest):
        target = (agents_dir / rel).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


@pytest.mark.skipif(not IS_MAC, reason="OS enforcement for goose is macOS-only")
def test_verify_flags_unmerged_boundary(tmp_path):
    m = _confined_goose_manifest()
    _emit_into(tmp_path, m)
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, m, live_config_path=tmp_path / "none.yaml")
    assert ok is False
    assert any("unmerged boundary" in x for x in msgs)


@pytest.mark.skipif(not IS_MAC, reason="OS enforcement for goose is macOS-only")
def test_verify_passes_when_merged_and_never_leaks_secrets(tmp_path):
    m = _confined_goose_manifest()
    _emit_into(tmp_path, m)
    live = tmp_path / "config.yaml"
    live.write_text("GOOSE_SANDBOX: 1\nOPENAI_API_KEY: super-secret-value\n", encoding="utf-8")
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, m, live_config_path=live)
    # ok depends on host build detection; the wiring itself must be recognized as merged.
    assert not any("unmerged boundary" in x for x in msgs)
    # secret-safe: the live config value is NEVER echoed.
    assert not any("super-secret-value" in x for x in msgs)


def test_verify_cooperative_is_nothing_to_verify(tmp_path):
    ok, msgs = verify_goose_sandbox_wiring(
        tmp_path, {"framework": "goose", "privilege_profile": "cooperative"}
    )
    assert ok is True
    assert any("nothing to verify" in x for x in msgs)


def test_verify_linux_is_exit_neutral_not_a_clean_pass(monkeypatch, tmp_path):
    # Linux/Windows: honest exit-neutral notice, never a misleading clean pass.
    monkeypatch.setattr(sys, "platform", "linux")
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, _confined_goose_manifest())
    assert ok is True  # exit-neutral (does not fail CI)...
    assert any("NOT ENFORCEABLE HERE" in x for x in msgs)  # ...but not a clean pass.
    assert any("container" in x for x in msgs)  # outside-in guidance


@pytest.mark.skipif(not IS_MAC, reason="OS enforcement for goose is macOS-only")
def test_verify_detects_porous_profile_missing_network_deny(tmp_path):
    m = _confined_goose_manifest()
    _emit_into(tmp_path, m)
    # Tamper: remove the network deny -> the verifier must catch the porousness.
    prof = tmp_path / ".goose" / "sandbox.sb"
    prof.write_text(prof.read_text().replace("(deny network*)", ";; removed"), encoding="utf-8")
    live = tmp_path / "config.yaml"
    live.write_text("GOOSE_SANDBOX: 1\n", encoding="utf-8")
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, m, live_config_path=live)
    assert ok is False
    assert any("deny network*" in x for x in msgs)
