"""P00 D8: ``--check-wiring`` reports brief/live disagreement for claude, goose and grok; it never writes live config.

* claude: brief ``os_sandbox: off`` + live ``sandbox.enabled`` -> ``DRIFT`` (ok False), no live value echoed; the
  reverse (on, unmerged) keeps today's warning; with WASM on, missing control-plane rules ->
  ``WASM CONTROL PLANE NOT MERGED`` and ``verify_control_plane_merged`` returns False with the rule names.
* goose: the same DRIFT, plus the user-global sentence (also on the reverse case).
* grok (new verifier): a managed ``[permission]`` or ``.grok/sandbox.toml`` while off -> DRIFT.
* A cooperative team without the switch keeps its pre-P00 result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentteams import sandbox_state as ss
from agentteams.frameworks._goose_sandbox_emit import verify_goose_sandbox_wiring
from agentteams.frameworks._grok_sandbox_emit import verify_grok_sandbox_wiring
from agentteams.frameworks.claude import verify_control_plane_merged, verify_sandbox_wiring

_SECRET = "sk-live-do-not-echo"


def _live(root: Path, data: dict) -> None:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({**data, "env": {"KEY": _SECRET}}))


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_claude_brief_off_with_live_enabled_is_drift_and_echoes_nothing(tmp_path):
    _live(tmp_path, {"sandbox": {"enabled": True, "filesystem": {"allowWrite": ["/secret/root"]}}})
    before = _snapshot(tmp_path)
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "confined", "os_sandbox": "off"})
    assert not ok and msgs[0].startswith("DRIFT: the brief turns the OS sandbox off")
    assert not any(_SECRET in m or "/secret/root" in m for m in msgs)
    assert _snapshot(tmp_path) == before  # read-only


def test_claude_brief_off_with_live_off_is_ok(tmp_path):
    _live(tmp_path, {})
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "confined", "os_sandbox": "off"})
    assert ok and "OS sandbox is off for this team by brief" in msgs[0]


def test_claude_cli_off_reads_the_transient_key(tmp_path):
    _live(tmp_path, {"sandbox": {"enabled": True}})
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "confined", ss.OS_SANDBOX_CLI_KEY: "off"})
    assert not ok and "DRIFT" in msgs[0]


def test_claude_cooperative_without_the_switch_is_unchanged(tmp_path):
    _live(tmp_path, {"sandbox": {"enabled": True}})
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "cooperative"})
    assert ok and msgs == ["no emitted settings example found — nothing to verify."]
    assert verify_sandbox_wiring(tmp_path) == (ok, msgs)


def test_claude_off_from_the_build_log_record(tmp_path):
    _live(tmp_path, {"sandbox": {"enabled": True}})
    team = tmp_path / ".claude" / "agents" / "references"
    team.mkdir(parents=True)
    rec = ss.effective_sandbox_state({"os_sandbox": "off"}).to_record(project_root=tmp_path)
    (team / "build-log.json").write_text(json.dumps({"sandbox_state": rec}))
    ok, msgs = verify_sandbox_wiring(tmp_path)
    assert not ok and "DRIFT" in msgs[0]


def test_claude_on_and_unmerged_keeps_the_existing_warning(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.hooks.example.json").write_text(json.dumps({"sandbox": {"enabled": True}}))
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "cooperative", "os_sandbox": "on"})
    assert not ok and "unmerged boundary" in msgs[0]


def test_control_plane_merged(tmp_path):
    _live(tmp_path, {"sandbox": {"enabled": True, "filesystem": {"denyWrite": [".agentteams"]}},
                     "permissions": {"deny": ["Edit(/.agentteams/**)"]}})
    merged, missing = verify_control_plane_merged(tmp_path, (".mcp.json",))
    assert not merged
    assert missing == ["sandbox.filesystem.denyWrite .claude", "permissions.deny Edit(/.claude/agents/**)",
                       "sandbox.filesystem.denyWrite .mcp.json", "permissions.deny Edit(/.mcp.json)"]
    _live(tmp_path, {"sandbox": {"filesystem": {"denyWrite": [".agentteams", ".claude"]}},
                     "permissions": {"deny": ["Edit(/.agentteams/**)", "Edit(/.claude/agents/**)"]}})
    assert verify_control_plane_merged(tmp_path) == (True, [])


@pytest.mark.parametrize("shell_deny", [".claude", ".claude/agents"])
def test_control_plane_needs_the_claude_shell_denywrite(tmp_path, shell_deny):
    """P13 C1: the Edit deny covers only the built-in tools; the shell must be write-denied `.claude` (or its
    `agents/` dir) too, or a sandboxed Bash re-adds a narrowed subagent's shell."""
    perms = {"deny": ["Edit(/.agentteams/**)", "Edit(/.claude/agents/**)"]}
    _live(tmp_path, {"sandbox": {"filesystem": {"denyWrite": [".agentteams"]}}, "permissions": perms})
    assert verify_control_plane_merged(tmp_path) == (False, ["sandbox.filesystem.denyWrite .claude"])
    _live(tmp_path, {"sandbox": {"filesystem": {"denyWrite": [".agentteams", shell_deny]}}, "permissions": perms})
    assert verify_control_plane_merged(tmp_path) == (True, [])


def test_claude_wasm_on_reports_an_unmerged_control_plane(tmp_path, monkeypatch):
    real = ss.effective_sandbox_state
    monkeypatch.setattr(ss, "effective_sandbox_state", lambda m: ss.SandboxState(
        os=real(m).os, os_reason=real(m).os_reason, wasm=True, wasm_reason="brief", wasm_languages=("python",)))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.hooks.example.json").write_text(json.dumps({"sandbox": {"enabled": True}}))
    _live(tmp_path, {"sandbox": {"enabled": True, "allowUnsandboxedCommands": False}})
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "confined"})
    assert not ok and any(m.startswith("WASM CONTROL PLANE NOT MERGED: ") for m in msgs)


# --- goose ---------------------------------------------------------------------------------------


def test_goose_off_with_live_enabled_is_drift_with_the_user_global_note(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"GOOSE_SANDBOX: 1\nOPENAI_API_KEY: {_SECRET}\n")
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, {"privilege_profile": "confined", "os_sandbox": "off"},
                                           live_config_path=cfg)
    assert not ok and msgs[0].startswith("DRIFT") and "user-global" in msgs[0] and _SECRET not in msgs[0]


def test_goose_off_with_live_off_is_ok(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("GOOSE_SANDBOX: 0\n")
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, {"privilege_profile": "confined", "os_sandbox": "off"},
                                           live_config_path=cfg)
    assert ok and "nothing to verify" in msgs[0]


def test_goose_reverse_case_carries_the_user_global_note(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    (tmp_path / ".goose").mkdir()
    (tmp_path / ".goose" / "sandbox.sb").write_text("(deny file-write*)\n")
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, {"privilege_profile": "confined"},
                                           live_config_path=tmp_path / "absent.yaml")
    assert not ok and any("unmerged boundary" in m and "user-global" in m for m in msgs)


# --- grok ----------------------------------------------------------------------------------------


@pytest.fixture
def grok_root(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    return tmp_path


def _grok_config(root: Path, *, permission: bool) -> None:
    from agentteams.grok_config_emit import emit_grok_config

    emit_grok_config(servers=[], features=["grok:sandbox"] if permission else [], output_root=root)


def test_grok_off_with_managed_permission_and_profile_is_drift(grok_root):
    _grok_config(grok_root, permission=True)
    (grok_root / ".grok" / "sandbox.toml").write_text("x")
    ok, msgs = verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined", "os_sandbox": "off"})
    assert not ok and len(msgs) == 2 and all(m.startswith("DRIFT") for m in msgs)
    assert "[permission]" in msgs[0] and ".grok/sandbox.toml" in msgs[1]


def test_grok_off_clean_is_ok(grok_root):
    ok, msgs = verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined", "os_sandbox": "off"})
    assert ok and "nothing to verify" in msgs[0]


def _grok_gate(root: Path) -> None:
    hooks = root / ".grok" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "agentteams-gate.json").write_text('{"hooks": {"PreToolUse": [{"hooks": [{"type": "command", '
                                                '"command": "./agentteams-gate-shim.py"}]}]}}')
    (hooks / "agentteams-gate-shim.py").write_text("#")
    (hooks / "constitutional-gate.py").write_text("#")


def test_grok_on_needs_both_files(grok_root):
    ok, msgs = verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined"})
    assert not ok and len(msgs) == 3
    _grok_config(grok_root, permission=True)
    (grok_root / ".grok" / "sandbox.toml").write_text("x")
    _grok_gate(grok_root)
    ok, msgs = verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined"})
    assert ok and msgs[0].startswith("OK")


def test_grok_without_a_manifest_is_neutral(grok_root):
    assert verify_grok_sandbox_wiring(grok_root)[0] is True


# --- control_plane_io.stubs_enabled (row 18; Rule-12 regression (e)) ----------------------------


@pytest.mark.parametrize("framework", ["claude", "goose", "copilot-vscode", "copilot-cli", "codex", "grok"])
def test_stubs_never_fewer_than_before_the_switch(framework):
    from agentteams.control_plane_io import stubs_enabled

    def legacy(m):  # the body at c4930051
        return "enforce_decision_signing" in m or ss.legacy_os_requested(m)

    for profile in ("cooperative", "confined", "exclusive"):
        for switch in (None, "on", "off"):
            for signing in (False, True):
                for tokens in ([], [f"{framework}:sandbox"]):
                    m = {"privilege_profile": profile, "host_features": tokens}
                    if switch:
                        m["os_sandbox"] = switch
                    if signing:
                        m["enforce_decision_signing"] = True
                    if legacy(m):
                        assert stubs_enabled(framework, m), m
                    if switch == "on":
                        assert stubs_enabled(framework, m), m
    assert not stubs_enabled("agents-md", {"privilege_profile": "confined"})


# --- wave-2 review: S2 (a profile-derived off after a recorded boundary), H2 (UNREADABLE), H3 (None-safe) -------


def _record(root: Path, manifest: dict) -> None:
    team = root / ".claude" / "agents" / "references"
    team.mkdir(parents=True, exist_ok=True)
    rec = ss.effective_sandbox_state(manifest).to_record(project_root=root)
    (team / "build-log.json").write_text(json.dumps({"sandbox_state": rec}))


def test_claude_cooperative_after_a_recorded_boundary_is_drift_not_cooperative_ok(tmp_path):
    _live(tmp_path, {"sandbox": {"enabled": True}})
    _record(tmp_path, {"privilege_profile": "confined"})
    (tmp_path / ".claude" / "settings.hooks.example.json").write_text(json.dumps({}))
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "cooperative"})
    assert not ok and msgs[0].startswith("DRIFT: the OS sandbox was relaxed to off (profile:cooperative)")
    assert not any("(cooperative)." in m for m in msgs)


def test_claude_cooperative_after_a_recorded_boundary_with_live_off_says_relaxed(tmp_path):
    _live(tmp_path, {})
    _record(tmp_path, {"privilege_profile": "cooperative"})  # the S1 gate records the off state
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "cooperative"})
    assert ok and msgs[0].startswith("RELAXED: the OS sandbox was on for this team and is now off")
    assert verify_sandbox_wiring(tmp_path)[1][0].startswith("RELAXED")  # from the record alone


def test_goose_cooperative_after_a_recorded_boundary_is_drift(tmp_path):
    team = tmp_path / ".goose" / "recipes" / "references"
    team.mkdir(parents=True)
    (team / "build-log.json").write_text(json.dumps({"sandbox_state": ss.effective_sandbox_state(
        {"privilege_profile": "confined"}).to_record(project_root=tmp_path)}))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("GOOSE_SANDBOX: 1\n")
    ok, msgs = verify_goose_sandbox_wiring(tmp_path, {"privilege_profile": "cooperative"}, live_config_path=cfg)
    assert not ok and msgs[0].startswith("DRIFT: the OS sandbox was relaxed to off")


@pytest.mark.parametrize("text", ["{not json", "[1, 2]"])
def test_claude_off_with_unreadable_live_settings_is_not_ok(tmp_path, text):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(text)
    ok, msgs = verify_sandbox_wiring(tmp_path, {"privilege_profile": "confined", "os_sandbox": "off"})
    assert not ok and msgs[0].startswith("UNREADABLE") and "nothing to verify" not in msgs[0]
    merged, missing = verify_control_plane_merged(tmp_path)
    assert not merged and missing == ["UNREADABLE .claude/settings.json"]


def test_drift_helpers_accept_none_and_non_states():
    from agentteams.frameworks import _sandbox_drift as d

    assert d.off_message(None) == "OS sandbox is off for this team; nothing to verify."
    assert d.off_message("not-a-state") == d.off_message(None)
    assert not d.switched_off(None, previous_boundary=True) and not d.switched_off(object())
    off = ss.effective_sandbox_state({"privilege_profile": "cooperative"})
    assert not d.switched_off(off) and d.switched_off(off, previous_boundary=True)
    assert d.goose_control_plane_ok(None, "", lambda p: p, [])


# --- IM item 03 (G-22-1): the gate hook and permissions.deny must be merged, not only the sandbox block ------

_GATE_CMD = 'python3 "${CLAUDE_PROJECT_DIR}/.claude/hooks/constitutional-gate.py"'
_MATCHER = "Write|Edit|NotebookEdit|MultiEdit|Bash"
_DENY = ["Read(~/.config/agentteams/keys/**)", "Edit(/.agentteams/**)"]
_SANDBOX = {"enabled": True, "allowUnsandboxedCommands": False, "filesystem": {"allowWrite": ["."]}}


def _example(root: Path, *, sandbox: bool = True) -> None:
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    ex = {"hooks": {"PreToolUse": [{"matcher": _MATCHER, "hooks": [{"type": "command", "command": _GATE_CMD}]}]},
          "permissions": {"deny": list(_DENY)}}
    if sandbox:
        ex["sandbox"] = dict(_SANDBOX)
    (root / ".claude" / "settings.hooks.example.json").write_text(json.dumps(ex))


def _gate(matcher: str = _MATCHER) -> dict:
    return {"PreToolUse": [{"matcher": matcher, "hooks": [{"type": "command", "command": _GATE_CMD}]}]}


def test_g22_1_sandbox_only_live_settings_no_longer_pass(tmp_path):
    """The repro: only the sandbox block merged printed 'OK … merged' and exited 0 on 70b2c4aa."""
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as verify_emitted_sandbox_merged

    _example(tmp_path)
    _live(tmp_path, {"sandbox": dict(_SANDBOX)})
    ok, msgs = verify_emitted_sandbox_merged(tmp_path)
    assert not ok
    text = " ".join(msgs)
    assert "constitutional gate is NOT wired" in text and "permissions.deny is missing 2" in text
    assert _SECRET not in text


def test_g22_1_everything_merged_passes(tmp_path):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as verify_emitted_sandbox_merged

    _example(tmp_path)
    _live(tmp_path, {"sandbox": dict(_SANDBOX), "hooks": _gate(), "permissions": {"deny": [*_DENY, "Extra(x)"]}})
    ok, msgs = verify_emitted_sandbox_merged(tmp_path)
    assert ok, msgs
    assert any("gate hook and the permission denies are merged" in m for m in msgs)


@pytest.mark.parametrize("live_extra, needle", [
    ({"hooks": _gate("Write|Edit"), "permissions": {"deny": list(_DENY)}}, "constitutional gate is NOT wired"),
    ({"hooks": _gate(), "permissions": {"deny": _DENY[1:]}}, "missing 1 emitted rule"),
    ({"hooks": {"PreToolUse": [{"matcher": _MATCHER, "hooks": [{"type": "command", "command": "echo hi"}]}]},
      "permissions": {"deny": list(_DENY)}}, "constitutional gate is NOT wired"),
])
def test_g22_1_a_narrower_matcher_a_missing_read_deny_or_another_hook_fails(tmp_path, live_extra, needle):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as verify_emitted_sandbox_merged

    _example(tmp_path)
    _live(tmp_path, {"sandbox": dict(_SANDBOX), **live_extra})
    ok, msgs = verify_emitted_sandbox_merged(tmp_path)
    assert not ok and any(needle in m for m in msgs), msgs


def test_g22_1_a_wildcard_matcher_covers_the_emitted_tools(tmp_path):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as verify_emitted_sandbox_merged

    _example(tmp_path)
    _live(tmp_path, {"sandbox": dict(_SANDBOX), "hooks": _gate("*"), "permissions": {"deny": list(_DENY)}})
    assert verify_emitted_sandbox_merged(tmp_path)[0]


def test_g22_1_cooperative_team_is_warned_not_failed(tmp_path):
    """Warn-first: a cooperative team passed before; it now sees the warning but keeps exit 0."""
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as verify_emitted_sandbox_merged

    _example(tmp_path, sandbox=False)
    _live(tmp_path, {})
    ok, msgs = verify_emitted_sandbox_merged(tmp_path)
    assert ok
    assert any("constitutional gate is NOT wired" in m for m in msgs)



# --- IM item 03 on Grok: the managed [permission] deny rules and the advisory gate files are checked by content ------


def _drop_one_grok_deny(root: Path) -> str:
    from agentteams.grok_config_emit import GROK_CONFIG_REL, grok_permission_table

    cfg = root / GROK_CONFIG_REL
    rule = grok_permission_table([])["deny"][0]
    text = cfg.read_text(encoding="utf-8")
    assert f'"{rule}"' in text
    cfg.write_text(text.replace(f'"{rule}",', "", 1).replace(f'"{rule}"', "", 1), encoding="utf-8")
    return rule


def test_grok_a_permission_block_missing_an_emitted_deny_fails(grok_root):
    _grok_config(grok_root, permission=True)
    (grok_root / ".grok" / "sandbox.toml").write_text("x")
    _grok_gate(grok_root)
    assert verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined"})[0]
    rule = _drop_one_grok_deny(grok_root)
    ok, msgs = verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined"})
    assert not ok and any("missing 1 emitted deny rule" in m and rule in m for m in msgs), msgs


def test_grok_a_missing_profile_or_gate_file_fails(grok_root):
    _grok_config(grok_root, permission=True)
    _grok_gate(grok_root)
    ok, msgs = verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined"})
    assert not ok and any("sandbox.toml is missing" in m for m in msgs)
    (grok_root / ".grok" / "sandbox.toml").write_text("x")
    (grok_root / ".grok" / "hooks" / "agentteams-gate-shim.py").unlink()
    ok, msgs = verify_grok_sandbox_wiring(grok_root, {"privilege_profile": "confined"})
    assert not ok and any("advisory Grok gate is incomplete" in m for m in msgs)


# --- @security conditions on the Claude check -----------------------------------------------------------------


def _full_live(**over) -> dict:
    return {"sandbox": dict(_SANDBOX), "hooks": _gate(), "permissions": {"deny": list(_DENY)}, **over}


def test_disable_all_hooks_fails_in_settings_or_local(tmp_path):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as check

    _example(tmp_path)
    _live(tmp_path, _full_live(disableAllHooks=True))
    ok, msgs = check(tmp_path)
    assert not ok and any("disableAllHooks" in m for m in msgs)
    _live(tmp_path, _full_live())
    (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps({"disableAllHooks": True}))
    assert not check(tmp_path)[0]


@pytest.mark.parametrize("command", [
    'echo constitutional-gate.py',
    'python3 /tmp/x/constitutional-gate.py',
    _GATE_CMD + " || true",
])
def test_only_the_exact_gate_command_counts(tmp_path, command):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as check

    _example(tmp_path)
    hooks = {"PreToolUse": [{"matcher": _MATCHER, "hooks": [{"type": "command", "command": command}]}]}
    _live(tmp_path, _full_live(hooks=hooks))
    ok, msgs = check(tmp_path)
    assert not ok and any("constitutional gate is NOT wired" in m for m in msgs)


@pytest.mark.parametrize("matcher, covered", [
    (_MATCHER + "|[", False),          # does not compile
    ("Write |Edit|NotebookEdit|MultiEdit|Bash", False),   # padded: matches nothing as a regex
    (None, False),
    (".*", True),
    ("(Write|Edit|NotebookEdit|MultiEdit|Bash)", True),
    ("Write|Edit", False),
])
def test_matchers_are_read_as_regexes(tmp_path, matcher, covered):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as check

    _example(tmp_path)
    hooks = {"PreToolUse": [{"matcher": matcher, "hooks": [{"type": "command", "command": _GATE_CMD}]}]}
    _live(tmp_path, _full_live(hooks=hooks))
    assert check(tmp_path)[0] is covered


def test_the_gate_is_required_even_when_the_example_omits_it(tmp_path):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as check

    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "settings.hooks.example.json").write_text(json.dumps({"sandbox": dict(_SANDBOX)}))
    _live(tmp_path, {"sandbox": dict(_SANDBOX)})
    ok, msgs = check(tmp_path)
    assert not ok and any("constitutional gate is NOT wired" in m for m in msgs)


def test_a_malformed_live_deny_list_does_not_crash():
    from agentteams.frameworks._claude_wiring_check import gate_and_deny_merged

    ex = {"permissions": {"deny": list(_DENY)}}
    for bad in ([{"x": 1}, *_DENY[:1]], "Read(x)", None):
        ok, msgs = gate_and_deny_merged(ex, {"hooks": _gate(), "permissions": {"deny": bad}})
        assert not ok and any("permissions.deny is missing" in m for m in msgs)


def test_a_switched_off_team_still_gets_the_gate_warnings(tmp_path):
    from agentteams.frameworks._claude_wiring_check import gate_warnings

    _example(tmp_path, sandbox=False)
    _live(tmp_path, {})
    assert any("constitutional gate is NOT wired" in m for m in gate_warnings(tmp_path))


def test_an_unreadable_live_settings_is_reported_as_unreadable(tmp_path):
    from agentteams.frameworks.claude import _verify_emitted_sandbox_merged as check

    _example(tmp_path, sandbox=False)
    (tmp_path / ".claude" / "settings.json").write_text("{not json")
    ok, msgs = check(tmp_path)
    assert ok and any("unreadable or invalid JSON" in m for m in msgs)
