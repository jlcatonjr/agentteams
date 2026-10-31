"""P01 D4/D6 and METAPLAN R1: the ``wasm_sandbox`` manifest field, byte-identity without it, R1, and the D6 notes.

* No block and no flag: the manifest carries no ``wasm_sandbox`` key, and every example brief renders on every
  framework exactly as with the pre-P01 WASM half (a constant "off"), on Linux and macOS.
* A block: ``manifest["wasm_sandbox"]`` validates against ``team-manifest.schema.json``; the shipped WASM example
  brief validates against ``project-description.schema.json``.
* WASM on (brief or ``--wasm-sandbox on``) turns on the R1 control-plane write protection through
  ``_control_plane_protect``; off leaves it off.
* D6: ``wasm-without-os-sandbox`` when the OS sandbox is off, ``wasm-only-host`` on a host with no OS sandbox
  (forced win32), nothing while WASM is off; printed and recorded in ``advisories``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import wasm_config as wc
from agentteams.frameworks._control_plane_protect import control_plane_for, protect_control_plane
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.sandbox_state import SandboxState, effective_sandbox_state

jsonschema = pytest.importorskip("jsonschema")

REPO = Path(__file__).resolve().parent.parent
BRIEFS = sorted((REPO / "examples").glob("*/brief.json"))
BRIEF = REPO / "examples" / "software-project" / "brief.json"
WASM_BRIEF = REPO / "examples" / "software-project" / "wasm-sandbox.brief.json"
SCHEMAS = REPO / "agentteams" / "schemas"
_FRAMEWORKS = ("claude", "goose", "codex", "grok", "copilot-vscode", "copilot-cli", "agents-md")
_CP_RULES = ("Edit(/.agentteams/**)", "Edit(/.claude/agents/**)")


def _brief(**overrides) -> dict:
    return {**json.loads(BRIEF.read_text(encoding="utf-8")), **overrides}


def _extras(manifest: dict, framework: str) -> dict[str, str]:
    return dict(FRAMEWORKS[framework]().extra_output_files(manifest))


@pytest.fixture(params=["linux", "darwin"])
def platform(request, monkeypatch):
    monkeypatch.setattr(sys, "platform", request.param)
    return request.param


def _manifest_or_none(brief: dict, framework: str) -> dict | None:
    """Build the manifest, or None for a variant this framework refuses (e.g. write_policy on goose)."""
    try:
        return analyze.build_manifest(dict(brief), framework=framework)
    except ValueError as exc:
        assert "write_policy" in str(exc)
        return None


@pytest.mark.parametrize("brief_path", BRIEFS, ids=[p.parent.name for p in BRIEFS])
@pytest.mark.parametrize("framework", _FRAMEWORKS)
def test_no_block_renders_byte_identical_to_the_pre_p01_wasm_half(brief_path, framework, platform, monkeypatch):
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    assert "wasm_sandbox" not in brief
    variants = [brief, {**brief, "privilege_profile": "confined"}, {**brief, "write_policy": "orchestrator-only",
                                                                        "privilege_profile": "confined"}]

    def render() -> list[tuple[str, dict[str, str]]]:
        out = []
        for b in variants:
            m = _manifest_or_none(b, framework)
            if m is not None:
                assert "wasm_sandbox" not in m
                out.append((json.dumps(m, sort_keys=True, default=str), _extras(m, framework)))
        return out

    new = render()
    monkeypatch.setattr(wc, "wasm_state_fields", lambda manifest: (False, "default-off", (), {}))
    monkeypatch.setattr(wc, "wasm_switch", lambda manifest: (False, "default-off"))
    assert new and new == render()


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def test_manifest_field_validates_against_the_team_manifest_schema():
    sub = _schema("team-manifest.schema.json")["properties"]["wasm_sandbox"]
    brief = json.loads(WASM_BRIEF.read_text(encoding="utf-8"))
    for block in (brief["wasm_sandbox"], {}, {"enabled": False, "model_substrate": "off", "native_exec": "keep",
                                              "native_exec_keep": ["security"], "limits": {"memory_mib": 1}}):
        m = analyze.build_manifest({**brief, "wasm_sandbox": block}, framework="claude")
        jsonschema.validate(m["wasm_sandbox"], sub)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**m["wasm_sandbox"], "network": True}, sub)


def test_example_brief_validates_and_carries_the_normalised_block():
    brief = json.loads(WASM_BRIEF.read_text(encoding="utf-8"))
    jsonschema.validate(brief, _schema("project-description.schema.json"))
    m = analyze.build_manifest(brief, framework="claude")
    assert m["wasm_sandbox"]["agents"] == {"primary-producer": {"read_paths": ["docs", "src"], "scratch": True}}
    assert (m["wasm_sandbox"]["native_exec"], m["wasm_sandbox"]["native_exec_keep"]) == ("replace", [])


@pytest.mark.parametrize("block", [
    {"enabled": "yes"}, {"network": True}, {"agents": {"ghost": {"scratch": True}}}, {"limits": {"fuel": 0}},
    {"agents": {"primary-producer": {"read_paths": [".git"]}}}, None,
])
def test_schema_and_code_refuse_the_same_bad_blocks(block):
    brief = _brief(wasm_sandbox=block)
    with pytest.raises(ValueError):
        analyze.build_manifest(brief, framework="claude")
    if block is not None and "agents" not in block:  # slug / path rules are code-only (the schema has no roster)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(brief, _schema("project-description.schema.json"))


# --- R1: WASM on turns on the control-plane write protection -------------------------------------------------------

@pytest.mark.parametrize("framework", ["claude", "goose", "codex"])
def test_brief_wasm_on_protects_the_control_plane(framework, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    m = analyze.build_manifest(_brief(privilege_profile="confined", wasm_sandbox={"enabled": True}),
                               framework=framework)
    assert protect_control_plane(m) and control_plane_for(m) == ()
    off = analyze.build_manifest(_brief(privilege_profile="confined", wasm_sandbox={"enabled": False}),
                                 framework=framework)
    assert not protect_control_plane(off)


def test_claude_settings_carry_the_r1_rules_under_brief_and_cli_on(tmp_path):
    for brief, cli in ((_brief(privilege_profile="confined", wasm_sandbox={"enabled": True}), None),
                       (_brief(privilege_profile="confined"), "on")):
        m = analyze.build_manifest(brief, framework="claude")
        if cli is not None:
            m[wc.WASM_SANDBOX_CLI_KEY] = cli
        data = json.loads(_extras(m, "claude")["../settings.hooks.example.json"])
        assert all(r in data["permissions"]["deny"] for r in _CP_RULES)
        assert ".agentteams" in data["sandbox"]["filesystem"]["denyWrite"]
    m = analyze.build_manifest(_brief(privilege_profile="confined", wasm_sandbox={"enabled": True}),
                               framework="claude")
    m[wc.WASM_SANDBOX_CLI_KEY] = "off"
    data = json.loads(_extras(m, "claude")["../settings.hooks.example.json"])
    assert not any(r in data["permissions"]["deny"] for r in _CP_RULES)


def test_example_brief_renders_with_grants_when_the_root_is_known(tmp_path):
    m = analyze.build_manifest(json.loads(WASM_BRIEF.read_text(encoding="utf-8")), framework="claude")
    m[PROJECT_ROOT_KEY] = tmp_path.resolve()
    assert effective_sandbox_state(m).wasm_grants["primary-producer"].mounts[-1].guest == "/scratch"
    assert "../settings.hooks.example.json" in _extras(m, "claude")


# --- D6 composition notes -------------------------------------------------------------------------------------------

def _state(*, os: bool, wasm: bool) -> SandboxState:
    return SandboxState(os=os, os_reason="brief" if not os else "profile:confined", wasm=wasm,
                        wasm_reason="brief" if wasm else "default-off", wasm_languages=("python",) if wasm else ())


def test_composition_advisories():
    codes = lambda s, **kw: [a["code"] for a in wc.composition_advisories(s, "claude", **kw)]  # noqa: E731
    assert codes(_state(os=True, wasm=False), platform="linux") == []
    assert codes(_state(os=False, wasm=False), platform="linux") == []
    assert codes(_state(os=True, wasm=True), platform="linux") == []
    assert codes(_state(os=False, wasm=True), platform="linux") == ["wasm-without-os-sandbox"]
    assert codes(_state(os=True, wasm=True), platform="win32") == ["wasm-only-host"]
    assert codes(_state(os=True, wasm=True), mechanism=False) == ["wasm-only-host"]
    assert [a["code"] for a in wc.composition_advisories(_state(os=True, wasm=True), "codex", platform="win32")] == [
        "wasm-only-host"]
    assert codes(_state(os=False, wasm=True), platform="win32") == ["wasm-without-os-sandbox", "wasm-only-host"]
    msg = wc.composition_advisories(_state(os=False, wasm=True), "claude", platform="linux")[0]["message"]
    assert "WASM limits guest code only" in msg and "enforced" not in msg


def test_advisories_are_printed_and_recorded_by_finalize(tmp_path, monkeypatch, capsys):
    from agentteams import host_features
    from agentteams.cli.artifacts import finalize_privilege_wiring
    from tests._sandbox_gate_helpers import _team

    monkeypatch.setattr(host_features, "os_sandbox_mechanism_available", lambda platform=None: True)
    team = _team(tmp_path, governed=False)
    m = analyze.build_manifest(_brief(privilege_profile="cooperative", wasm_sandbox={"enabled": True}),
                               framework="claude")
    finalize_privilege_wiring(m, [], "claude", tmp_path, team_dir=team)
    assert "wasm-without-os-sandbox" in [a["code"] for a in m.get("advisories", [])]
    out = capsys.readouterr()
    assert "WASM confinement is ON" in out.out and "WASM limits guest code only" in out.err

    monkeypatch.setattr(host_features, "os_sandbox_mechanism_available", lambda platform=None: False)
    m = analyze.build_manifest(_brief(privilege_profile="cooperative", wasm_sandbox={"enabled": True}),
                               framework="claude")
    finalize_privilege_wiring(m, [], "claude", tmp_path, team_dir=team)
    assert "wasm-only-host" in [a["code"] for a in m["advisories"]]
