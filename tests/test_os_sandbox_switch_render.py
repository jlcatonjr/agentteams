"""P00: ``os_sandbox`` toggles every framework's OS boundary independently of ``privilege_profile``.

Plan: ``tmp/by-week/2026-W44/wasm-sandbox/P00-os-sandbox-switch.plan.md`` (D3, D4, D5, inventory rows 1-18).

* ``confined`` + ``os_sandbox: off`` emits no boundary on any framework (no Claude ``sandbox`` block, no Seatbelt
  profile, no runner, no Grok profile, no neutral launcher) but keeps the verify-key sentinel and the
  control-plane stubs (Rule 12, row 18) and a fail-open gate hook.
* ``cooperative`` + ``os_sandbox: on`` emits them.
* With no switch, every example brief renders byte-identically to the pre-P00 predicates (the backward-compatibility
  proof over the shipped fixtures).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import sandbox_state as ss
from agentteams.control_plane_io import stubs_enabled
from agentteams.frameworks._sandbox_emit import VERIFY_KEY_STORE_SENTINEL_REL
from agentteams.frameworks.registry import FRAMEWORKS

REPO = Path(__file__).resolve().parent.parent
BRIEFS = sorted((REPO / "examples").glob("*/brief.json"))
BRIEF = REPO / "examples" / "software-project" / "brief.json"
_FRAMEWORKS = ("claude", "goose", "codex", "grok", "copilot-vscode", "copilot-cli", "agents-md")
_BOUNDARY_BASENAMES = {"confine-run.sh", "mac-escape-tests.sh", "sandbox.sb", "config.yaml.agentteams.example",
                       "confined-run.example.sh", "sandbox.toml"}
_FAIL_CLOSED = re.compile(r"^_FAIL_CLOSED_ON_ERROR = True$", re.MULTILINE)


def _extras(brief: dict, framework: str) -> dict[str, str]:
    manifest = analyze.build_manifest(dict(brief), framework=framework)
    return dict(FRAMEWORKS[framework]().extra_output_files(manifest))


def _boundary(files: dict[str, str]) -> set[str]:
    found = {p for p in files if p.rsplit("/", 1)[-1] in _BOUNDARY_BASENAMES}
    settings = files.get("../settings.hooks.example.json")
    if settings is not None and "sandbox" in json.loads(settings):
        found.add("claude sandbox block")
    return found


@pytest.fixture(params=["linux", "darwin"])
def platform(request, monkeypatch):
    monkeypatch.setattr(sys, "platform", request.param)
    return request.param


@pytest.mark.parametrize("framework", _FRAMEWORKS)
@pytest.mark.parametrize("profile", ["confined", "exclusive"])
def test_confined_plus_off_emits_no_boundary_but_keeps_sentinel_and_stubs(framework, profile, platform):
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": profile}
    on = _extras(brief, framework)
    off = _extras({**brief, "os_sandbox": "off"}, framework)
    assert _boundary(on), f"{framework}/{platform}: the profile alone should emit a boundary"
    assert _boundary(off) == set()
    assert (VERIFY_KEY_STORE_SENTINEL_REL in off) is (VERIFY_KEY_STORE_SENTINEL_REL in on)  # agents-md has none
    manifest = analyze.build_manifest({**brief, "os_sandbox": "off"}, framework=framework)
    if framework != "agents-md":
        assert stubs_enabled(framework, manifest), "row 18: the switch must never remove the stubs"
    hook = off.get("../hooks/constitutional-gate.py")
    if hook is not None:
        assert not _FAIL_CLOSED.search(hook)  # no boundary: nothing for a crash to drop (D5)


@pytest.mark.parametrize("framework", _FRAMEWORKS)
def test_cooperative_plus_on_emits_the_boundary(framework, platform):
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "cooperative"}
    assert _boundary(_extras(brief, framework)) == set()
    on = _extras({**brief, "os_sandbox": "on"}, framework)
    assert _boundary(on)
    hook = on.get("../hooks/constitutional-gate.py")
    if hook is not None:
        assert _FAIL_CLOSED.search(hook)  # an explicit os_sandbox "on" is the explicit opt-in (Q2)


def test_gate_hook_flip_needs_explicit_opt_in_and_honours_fallback_fail_open(platform):
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    brief.pop("privilege_profile", None)
    assert not _FAIL_CLOSED.search(_extras(brief, "claude")["../hooks/constitutional-gate.py"])  # defaulted confined
    assert _FAIL_CLOSED.search(_extras({**brief, "privilege_profile": "confined"}, "claude")["../hooks/constitutional-gate.py"])
    manifest = analyze.build_manifest({**brief, "os_sandbox": "on"}, framework="claude")
    manifest["fallback_fail_open"] = True
    assert not _FAIL_CLOSED.search(dict(FRAMEWORKS["claude"]().extra_output_files(manifest))["../hooks/constitutional-gate.py"])


def test_cli_override_beats_the_brief(platform):
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined", "os_sandbox": "on"}
    manifest = analyze.build_manifest(dict(brief), framework="claude")
    manifest[ss.OS_SANDBOX_CLI_KEY] = "off"
    assert _boundary(dict(FRAMEWORKS["claude"]().extra_output_files(manifest))) == set()


def test_grok_config_permission_follows_the_merged_tokens():
    from agentteams.grok_config_emit import grok_sandbox_enabled
    from agentteams.host_features import merge_profile_features

    assert grok_sandbox_enabled(merge_profile_features([], "confined", "grok"))
    assert not grok_sandbox_enabled(merge_profile_features(["grok:sandbox"], "confined", "grok", os_sandbox="off"))
    assert grok_sandbox_enabled(merge_profile_features([], "cooperative", "grok", os_sandbox="on"))


def test_grok_off_render_leaves_an_existing_managed_block_with_the_notice(tmp_path):
    from agentteams.grok_config_emit import emit_grok_config

    assert emit_grok_config(servers=[], features=["grok:sandbox"], output_root=tmp_path).permission_written
    result = emit_grok_config(servers=[], features=[], output_root=tmp_path)
    assert any("left in place" in n for n in result.notices)


def test_invalid_brief_value_fails_closed():
    with pytest.raises(ValueError, match="os_sandbox"):
        analyze.build_manifest({**json.loads(BRIEF.read_text(encoding="utf-8")), "os_sandbox": "OFF"},
                               framework="claude")


def test_manifest_carries_os_sandbox_only_when_set():
    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    assert "os_sandbox" not in analyze.build_manifest(dict(brief), framework="claude")
    assert analyze.build_manifest({**brief, "os_sandbox": "off"}, framework="claude")["os_sandbox"] == "off"


# --- backward compatibility over the shipped fixtures ------------------------------------------


def _legacy_requested(manifest: dict, token: str | None = None) -> bool:
    if token is None:
        return manifest.get("privilege_profile") in {"confined", "exclusive"} or any(
            str(t).endswith(":sandbox") for t in manifest.get("host_features") or [])
    return token in (manifest.get("host_features") or []) or manifest.get("privilege_profile") in {
        "confined", "exclusive"}


def _legacy_state(manifest: dict) -> ss.SandboxState:
    return ss.SandboxState(os=_legacy_requested(manifest), os_reason="legacy", wasm=False, wasm_reason="default-off",
                           wasm_languages=())


@pytest.mark.parametrize("brief_path", BRIEFS, ids=[p.parent.name for p in BRIEFS])
@pytest.mark.parametrize("framework", _FRAMEWORKS)
def test_no_switch_renders_byte_identical_to_the_pre_p00_predicates(brief_path, framework, platform, monkeypatch):
    """Every example brief, every framework, Linux and macOS: the rewired twins produce the same bytes as the
    pre-P00 bodies (the full render was also compared against c4930051 by hand; see the plan's notes)."""
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    assert "os_sandbox" not in brief
    variants = [brief, {**brief, "privilege_profile": "cooperative"}, {**brief, "privilege_profile": "exclusive"},
                {**brief, "privilege_profile": "confined"}]
    new = [_extras(b, framework) for b in variants]
    monkeypatch.setattr(ss, "os_sandbox_requested", _legacy_requested)
    monkeypatch.setattr(ss, "effective_sandbox_state", _legacy_state)
    old = [_extras(b, framework) for b in variants]
    assert new == old


def test_a_switch_only_flip_names_the_switch_in_the_crash_reason():
    from agentteams.frameworks.claude import _PROFILE_CRASH_REASON, _SWITCH_CRASH_REASON

    brief = json.loads(BRIEF.read_text(encoding="utf-8"))
    switch = _extras({**brief, "privilege_profile": "cooperative", "os_sandbox": "on"}, "claude")
    profile = _extras({**brief, "privilege_profile": "confined"}, "claude")
    hook = switch["../hooks/constitutional-gate.py"]
    assert _SWITCH_CRASH_REASON in hook and _PROFILE_CRASH_REASON not in hook
    assert _PROFILE_CRASH_REASON in profile["../hooks/constitutional-gate.py"]


@pytest.mark.parametrize("framework", _FRAMEWORKS)
@pytest.mark.parametrize("profile", ["confined", "cooperative", "exclusive"])
def test_no_record_and_no_evidence_records_nothing_on_any_profile_change(framework, profile, platform, tmp_path):
    """Wave-2 S1 keeps the byte-identity carve-out exactly: with no ``sandbox_state`` recorded and no boundary on
    disk, no profile (including a move to ``cooperative``) engages the gate or writes a record."""
    from agentteams.cli.artifacts import finalize_privilege_wiring

    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": profile}
    manifest = analyze.build_manifest(brief, framework=framework)
    team = tmp_path / "team"
    (team / "references").mkdir(parents=True)
    finalize_privilege_wiring(manifest, [], framework, tmp_path, team_dir=team)
    assert "sandbox_state" not in manifest and "sandbox_relaxations" not in manifest
