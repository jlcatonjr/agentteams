"""METAPLAN R1 (P00 D11): the ``.agentteams`` control plane is session-write-denied whenever WASM is on.

``protect_control_plane = protect_ledger or state.wasm``. WASM is off until P01 lands, so these tests drive the WASM
half by patching :func:`agentteams.sandbox_state.effective_sandbox_state`; the emitter wiring is real.

* claude: ``.agentteams`` denyWrite + ``Edit(/.agentteams/**)`` + ``Edit(/.claude/agents/**)``; the runner-protocol
  ledger ``Read`` denies and ``.agentteams-status`` stay on ``protect_ledger`` only (Rule-12 carve-out); each rule
  once when both hold; WASM off and no write policy is byte-identical.
* goose: ``.agentteams`` and ``.goose/recipes`` in the Seatbelt deny block, and ``--protect`` in the Linux runner.
* codex: the ``extra_protected`` MCP config paths are ``--protect``-ed beside the unconditional ``.agentteams``.
* The launcher bytes are untouched.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import sandbox_state as ss
from agentteams.frameworks._control_plane_protect import protect_control_plane, wasm_protected_config_paths
from agentteams.frameworks._goose_sandbox_emit import _build_linux_goose_runner, _build_seatbelt_profile, _seatbelt_path_expr
from agentteams.frameworks import _control_plane_protect as cpp
from agentteams.frameworks import _sandbox_emit as se
from agentteams.frameworks._sandbox_emit import _build_sandbox_block, _inject_sandbox_block
from agentteams.frameworks.registry import FRAMEWORKS

REPO = Path(__file__).resolve().parent.parent
BRIEF = REPO / "examples" / "software-project" / "brief.json"
EXAMPLE = (REPO / "agentteams" / "templates" / "universal" / "hooks" / "settings.hooks.example.json").read_text()
LAUNCHER = REPO / "agentteams" / "templates" / "universal" / "sandbox" / "confine-run.sh"
_CP_RULES = ["Edit(/.agentteams/**)", "Edit(/.claude/agents/**)"]
_LEDGER_ONLY = ["Read(/.agentteams/**)", "Read(/.agentteams-queue/**)", "Edit(/.agentteams-status/**)"]


def _settings(**kw) -> dict:
    return json.loads(_inject_sandbox_block(EXAMPLE, None, **kw))


@pytest.fixture
def wasm_on(monkeypatch):
    real = ss.effective_sandbox_state

    def with_wasm(manifest):
        s = real(manifest)
        return ss.SandboxState(os=s.os, os_reason=s.os_reason, wasm=True, wasm_reason="brief",
                               wasm_languages=("python",))

    monkeypatch.setattr(ss, "effective_sandbox_state", with_wasm)


def test_wasm_on_claude_emits_the_control_plane_rules_without_the_ledger_reads():
    data = _settings(control_plane_protect=())
    assert ".agentteams" in data["sandbox"]["filesystem"]["denyWrite"]
    assert ".agentteams-status" not in data["sandbox"]["filesystem"]["denyWrite"]
    deny = data["permissions"]["deny"]
    assert all(r in deny for r in _CP_RULES) and not any(r in deny for r in _LEDGER_ONLY)


def test_orchestrator_only_plus_wasm_emits_each_rule_once_and_is_byte_identical_to_the_ledger_render():
    ledger = _settings(protect_ledger=True)
    both = _settings(protect_ledger=True, control_plane_protect=())
    assert ledger == both
    deny = both["permissions"]["deny"]
    assert all(deny.count(r) == 1 for r in [*_CP_RULES, *_LEDGER_ONLY])
    assert both["sandbox"]["filesystem"]["denyWrite"].count(".agentteams") == 1


def test_wasm_off_no_policy_is_unchanged():
    assert _settings() == _settings(control_plane_protect=None)
    assert _build_sandbox_block(None) == _build_sandbox_block(None, control_plane_protect=None)


def test_extra_protected_paths_reach_claude_goose_and_codex(monkeypatch, wasm_on):
    data = _settings(control_plane_protect=(".mcp.json",))
    assert ".mcp.json" in data["sandbox"]["filesystem"]["denyWrite"] and "Edit(/.mcp.json)" in data["permissions"]["deny"]
    profile = _build_seatbelt_profile(None, control_plane_protect=(".goose/x.yaml",))
    assert _seatbelt_path_expr(".goose/x.yaml") in profile
    monkeypatch.setattr(sys, "platform", "linux")
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined"}
    manifest = analyze.build_manifest(brief, framework="codex")
    manifest["wasm_wiring"] = {"protected_config_paths": [".codex/config.toml"]}
    runner = dict(FRAMEWORKS["codex"]().extra_output_files(manifest))["../confined-run.example.sh"]
    assert 'for p in .codex/config.toml; do [ -e "$ROOT/$p" ] && PROTECT+=( --protect "$ROOT/$p" ); done' in runner


def test_goose_profile_denies_agentteams_and_recipes_under_wasm_only():
    plain = _build_seatbelt_profile(None)
    cp = _build_seatbelt_profile(None, control_plane_protect=())
    for rel in (".agentteams", ".goose/recipes"):
        assert _seatbelt_path_expr(rel) not in plain and _seatbelt_path_expr(rel) in cp
    assert _seatbelt_path_expr(".agentteams-status") not in cp
    assert _build_seatbelt_profile(None, protect_ledger=True) == _build_seatbelt_profile(
        None, protect_ledger=True, control_plane_protect=())


def test_manifest_wiring_uses_the_effective_wasm_state(monkeypatch, wasm_on):
    monkeypatch.setattr(sys, "platform", "linux")
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined"}
    manifest = analyze.build_manifest(brief, framework="claude")
    assert protect_control_plane(manifest)
    data = json.loads(dict(FRAMEWORKS["claude"]().extra_output_files(manifest))["../settings.hooks.example.json"])
    assert all(r in data["permissions"]["deny"] for r in _CP_RULES)
    goose = analyze.build_manifest(brief, framework="goose")
    runner = _build_linux_goose_runner(goose)
    assert 'for p in .agentteams; do [ -e "$REPO_ROOT/$p" ] && PROTECT+=( --protect "$REPO_ROOT/$p" ); done' in runner
    assert '${PROTECT[@]+"${PROTECT[@]}"}' in runner


def test_without_wasm_the_goose_runner_is_unchanged():
    brief = {**json.loads(BRIEF.read_text(encoding="utf-8")), "privilege_profile": "confined"}
    assert "PROTECT" not in _build_linux_goose_runner(analyze.build_manifest(brief, framework="goose"))


@pytest.mark.parametrize("bad", [["/etc/passwd"], ["../x"], ["a/../b"], "x.json", [".mcp json"]])
def test_protected_config_paths_fail_closed(bad):
    with pytest.raises(ValueError):
        wasm_protected_config_paths({"wasm_wiring": {"protected_config_paths": bad}})


def test_control_plane_constants_match_the_emitter():
    assert (cpp.LEDGER_DIR_REL, cpp.STATUS_DIR_REL) == (se.LEDGER_DIR_REL, se.STATUS_DIR_REL)
    assert cpp.control_plane_for({"privilege_profile": "confined"}) is None
    assert cpp.control_plane_for({"write_policy": "orchestrator-only"}) == ()


def test_launcher_bytes_unchanged():
    # Pinned by integrity.py; P00 must not touch it (A.6). The hash is re-derived from the base commit's bytes.
    import subprocess

    base = subprocess.run(["git", "show", "c4930051:agentteams/templates/universal/sandbox/confine-run.sh"],
                          cwd=REPO, capture_output=True)
    if base.returncode != 0:
        pytest.skip("base commit not available in this checkout")
    assert hashlib.sha256(LAUNCHER.read_bytes()).hexdigest() == hashlib.sha256(base.stdout).hexdigest()
