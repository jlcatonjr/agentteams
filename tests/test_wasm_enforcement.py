"""WASM plan P13: the native-exec policy, its per-provider verdict (``wasm_enforcement``) and the Rule-12 carve-outs.

* Policy: ``replace`` default; the fixed exemption is keyed on the orchestrator *template* (a renamed agent is not
  exempt); ``native_exec_keep`` (D12 default navigator + git-operations) keeps; ``replace`` falls back to keep where
  this host has no ``run_wasm`` (listed, never silent); ``deny`` strips anyway.
* Verdict: ``enforced`` / ``conditional`` only with the OS sandbox on and the R1 control-plane rules merged
  (``verify_control_plane_merged`` + ``control_plane_advisory``); otherwise ``advisory`` with the reason. The
  top-level session is always advisory. The record validates against ``team-manifest.schema.json``.
* Governance: every ``native_exec_keep`` entry, and every eligible agent under ``native_exec: keep``, is a
  ``native-exec-keep`` gate input (``gate_keep_set``); the ``replace`` fallback is not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentteams import analyze
from agentteams import wasm_enforcement as we
from agentteams.cli.render_pipeline import _build_final_rendered
from agentteams.frameworks import _control_plane_protect as cpp
from agentteams.frameworks._write_roots import PROJECT_ROOT_KEY
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.sandbox_state import OS_SANDBOX_CLI_KEY
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
SHELL = "---\nname: X\ndescription: \"d\"\ntools: ['read', 'search', 'execute']\n---\n\nbody\n"


def _manifest(framework: str = "claude", *, wasm: str | None = "on", os_on: bool | None = None,
              **brief: object) -> dict:
    manifest = analyze.build_manifest({**BRIEF, **brief}, framework=framework)
    if wasm is not None:
        manifest[WASM_SANDBOX_CLI_KEY] = wasm
    if os_on is not None:
        manifest[OS_SANDBOX_CLI_KEY] = "on" if os_on else "off"
    return manifest


def _render(framework: str, manifest: dict) -> dict[str, str]:
    return dict(_build_final_rendered(manifest, FRAMEWORKS[framework](), "x"))


# --- policy ----------------------------------------------------------------------------------------------------------

def test_policy_is_keep_while_wasm_is_off_and_replace_by_default_when_on():
    assert we.exec_policy(_manifest(wasm=None), "cleanup") == "keep"
    assert we.exec_policy(_manifest(wasm="off"), "cleanup") == "keep"
    assert we.exec_policy(_manifest(), "cleanup") == "replace"


def test_default_keep_list_and_exempt_roles():
    m = _manifest()
    assert we.exec_policy(m, "navigator") == we.exec_policy(m, "git-operations") == "keep"
    assert we.role_exempt(m, "orchestrator") and we.exec_policy(m, "orchestrator") == "keep"
    assert we.agent_action(m, "orchestrator", "claude", ["execute"]) == we.EXEMPT


def test_an_agent_renamed_orchestrator_is_not_exempt():
    # Rule 12 (carve-out 7): the exemption keys on the orchestrator template, not on the slug.
    m = _manifest()
    for spec in m["output_files"]:
        if spec["path"].endswith("orchestrator.agent.md"):
            spec["template"] = "universal/cleanup.template.md"
    assert not we.role_exempt(m, "orchestrator")
    assert we.agent_action(m, "orchestrator", "claude", ["read", "execute"]) == we.NARROW
    assert not we.role_exempt(m, "cleanup")     # a slug alone never exempts


def test_only_eligible_agents_get_an_action():
    m = _manifest()
    assert we.agent_action(m, "security", "claude", ["read", "search"]) is None
    assert we.agent_action(m, "cleanup", "claude", ["read", "edit", "execute"]) == we.NARROW


@pytest.mark.parametrize("framework, action", [("claude", we.NARROW), ("codex", we.NARROW), ("grok", we.FALLBACK),
                                               ("copilot-vscode", we.FALLBACK), ("copilot-cli", we.FALLBACK),
                                               ("agents-md", we.FALLBACK)])
def test_replace_narrows_only_where_run_wasm_is_wired(framework, action):
    m = _manifest(framework)
    assert we.agent_action(m, "cleanup", framework, ["read", "execute"]) == action


def test_goose_replace_narrows_in_grant_mode_and_falls_back_in_legacy():
    assert we.agent_action(_manifest("goose", goose_tool_scoping="grant"), "cleanup", "goose", ["execute"]) == we.NARROW
    assert we.agent_action(_manifest("goose"), "cleanup", "goose", ["execute"]) == we.FALLBACK


def test_deny_strips_without_run_wasm_but_not_on_agents_md():
    m = _manifest("grok", wasm=None, wasm_sandbox={"enabled": True, "native_exec": "deny"})
    assert we.agent_action(m, "cleanup", "grok", ["execute"]) == we.NARROW
    m = _manifest("agents-md", wasm=None, wasm_sandbox={"enabled": True, "native_exec": "deny"})
    assert we.agent_action(m, "cleanup", "agents-md", ["execute"]) == we.FALLBACK


def test_apply_canonical_removes_only_execute_and_is_identity_when_off():
    m = _manifest()
    content = "---\nname: X\ndescription: \"d\"\ntools: ['read', 'execute', 'retrieval']\n---\n\nbody\n"
    out = we.apply_canonical(content, "cleanup", m, "claude")
    assert "tools: ['read', 'retrieval']" in out and out.endswith("\n\nbody\n")   # carve-out 2: retrieval kept
    assert we.apply_canonical(content, "cleanup", _manifest(wasm=None), "claude") == content
    assert we.apply_canonical(content, "cleanup", _manifest(wasm="off"), "claude") == content


def test_apply_canonical_refuses_a_block_tools_list_while_on():
    bad = "---\nname: X\ntools:\n  - execute\n---\nbody\n"
    with pytest.raises(ValueError, match="one-line"):
        we.apply_canonical(bad, "cleanup", _manifest(), "claude")


def test_wiring_tools_restores_execute_only_from_the_in_process_record():
    m = _manifest()
    we.apply_canonical(SHELL, "cleanup", m, "claude")
    assert "execute" in we.wiring_tools(m, "cleanup", ["Read"])
    assert we.wiring_tools(m, "security", ["Read"]) == ["Read"]
    forged = _manifest()
    forged[we.ACTIONS_KEY] = {"security": we.NARROW}   # what a JSON-loaded manifest could carry
    assert we.wiring_tools(forged, "security", ["Read"]) == ["Read"]


def test_begin_resets_the_record():
    m = _manifest()
    we.apply_canonical(SHELL, "cleanup", m, "claude")
    we.begin(m)
    assert we.actions(m) == {}


# --- governance input ------------------------------------------------------------------------------------------------

def test_gate_keep_set_is_the_keep_list_and_empty_when_off():
    assert we.gate_keep_set(_manifest()) == {"git-operations", "navigator"}
    assert we.gate_keep_set(_manifest(wasm="off")) == frozenset()
    m = _manifest(wasm=None, wasm_sandbox={"enabled": True, "native_exec_keep": []})
    assert we.gate_keep_set(m) == frozenset()


def test_native_exec_keep_makes_every_eligible_non_exempt_agent_a_keep_effect():
    m = _manifest(wasm=None, wasm_sandbox={"enabled": True, "native_exec": "keep", "native_exec_keep": []})
    keep = we.gate_keep_set(m)
    assert {"cleanup", "navigator", "git-operations"} <= keep
    assert "orchestrator" not in keep and "security" not in keep   # exempt; not eligible


def test_the_replace_fallback_is_not_a_keep_effect():
    assert we.gate_keep_set(_manifest("grok")) == {"git-operations", "navigator"}


def test_gate_exceptions_carry_the_expanded_keep_set():
    from agentteams.cli.os_sandbox_switch import gate_exceptions
    from agentteams.sandbox_state import effective_sandbox_state

    m = _manifest(wasm=None, wasm_sandbox={"enabled": True, "native_exec": "keep", "native_exec_keep": []})
    _prev, new = gate_exceptions(m, effective_sandbox_state(m), REPO, None)
    assert "cleanup" in new.native_exec_keep


# --- verdict ---------------------------------------------------------------------------------------------------------

def _merged_claude_settings(root: Path, manifest: dict) -> None:
    control = cpp.control_plane_for(manifest)
    settings = {"sandbox": {"enabled": True, "filesystem": {"denyWrite": list(cpp.write_denies(False, control))}},
                "permissions": {"deny": cpp.permission_rules(False, control)}}
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")


def _rendered_claude(tmp_path: Path, **kw: object) -> dict:
    m = _manifest("claude", **kw)
    m[PROJECT_ROOT_KEY] = tmp_path
    _render("claude", m)
    return m


def test_claude_is_enforced_only_with_os_on_and_the_control_plane_merged(tmp_path):
    m = _rendered_claude(tmp_path, os_on=True)
    row = we.enforcement_report(m, "claude")
    assert row["subagents"] == "advisory" and row["reason"].startswith("control-plane rules not merged")
    _merged_claude_settings(tmp_path, m)
    row = we.enforcement_report(m, "claude")
    assert row["subagents"] == "enforced" and row["control_plane_merged"] and row["os_sandbox"]
    assert row["top_level"] == "advisory" and "cleanup" in row["changed_agents"]
    assert row["kept_native"] == ["git-operations", "navigator"] and row["exempt"] == ["orchestrator"]


def test_os_sandbox_off_is_advisory_even_when_merged(tmp_path):
    m = _rendered_claude(tmp_path, os_on=False)
    _merged_claude_settings(tmp_path, m)
    row = we.enforcement_report(m, "claude")
    assert row["subagents"] == "advisory" and row["reason"] == "os sandbox off" and not row["os_sandbox"]
    assert any("OS sandbox off" in line for line in we.notices("claude", row))


def test_an_unreadable_live_settings_file_is_advisory(tmp_path):
    m = _rendered_claude(tmp_path, os_on=True)
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    assert we.enforcement_report(m, "claude")["subagents"] == "advisory"


def test_a_partially_merged_control_plane_names_what_is_missing(tmp_path):
    m = _rendered_claude(tmp_path, os_on=True)
    _merged_claude_settings(tmp_path, m)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    data["permissions"]["deny"].remove("Edit(/.claude/agents/**)")
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    row = we.enforcement_report(m, "claude")
    assert row["subagents"] == "advisory" and "Edit(/.claude/agents/**)" in row["reason"]


def test_codex_is_conditional_only_with_its_gate_and_never_from_prompt_approval():
    m = _manifest("codex", os_on=True)
    _render("codex", m)
    row = we.enforcement_report(m, "codex")
    assert row["subagents"] == "conditional" and "confined-run.example.sh" in row["mechanism"]
    assert "unknown tool names pass" in row["mechanism"]
    plain = _manifest("codex", os_on=False)
    _render("codex", plain)
    row = we.enforcement_report(plain, "codex")
    # No launcher (OS off): the narrowed role declaration is written, not enforced; the configured prompt default
    # (default_tools_approval_mode) counts for nothing.
    assert row["subagents"] == "declared" and row["reason"] == "written, not enforced by the host"


@pytest.mark.parametrize("framework", ["grok", "copilot-vscode", "copilot-cli", "agents-md"])
def test_hosts_without_run_wasm_are_advisory_with_the_fallback_listed(framework):
    m = _manifest(framework)
    _render(framework, m)
    row = we.enforcement_report(m, framework)
    assert row["subagents"] == "advisory" and "cleanup" in row["fallback"] and not row["changed_agents"]
    assert we.notices(framework, row)[0].startswith(f"[WASM-ENFORCEMENT] {framework}: ADVISORY ONLY")


def test_grok_deny_is_declared_not_enforced():
    m = _manifest("grok", wasm=None, wasm_sandbox={"enabled": True, "native_exec": "deny"})
    _render("grok", m)
    row = we.enforcement_report(m, "grok")
    assert row["subagents"] == "declared" and row["changed_agents"]


def test_copilot_deny_stays_advisory_without_a_verified_control_plane():
    m = _manifest("copilot-vscode", wasm=None, os_on=True, wasm_sandbox={"enabled": True, "native_exec": "deny"})
    _render("copilot-vscode", m)
    row = we.enforcement_report(m, "copilot-vscode")
    assert row["subagents"] == "advisory" and row["changed_agents"]


def test_goose_delegates_are_always_advisory():
    m = _manifest("goose", goose_tool_scoping="grant")
    _render("goose", m)
    row = we.enforcement_report(m, "goose")
    assert row["delegates"] == "advisory" and "delegates ADVISORY" in we.notices("goose", row)[0]


def test_nothing_is_reported_while_wasm_is_off():
    m = _manifest(wasm="off")
    assert we.enforcement_report(m, "claude") is None and we.notices("claude", None) == []
    we.record(m, "claude")
    assert we.KEY not in m


def test_report_records_prints_and_advises(capsys):
    m = _manifest("grok")
    _render("grok", m)
    we.report(m, "grok")
    err = capsys.readouterr().err
    assert err.count("[WASM-ENFORCEMENT]") == 2 and "exempt (fixed): orchestrator" in err
    assert m[we.KEY]["grok"]["subagents"] == "advisory"
    assert any(a["code"] == "wasm-enforcement-advisory" for a in m["advisories"])


def test_planned_actions_match_the_render():
    m = _manifest("claude")
    _render("claude", m)
    assert we.planned_actions(_manifest("claude"), "claude") == we.actions(m)


@pytest.mark.parametrize("framework", ["claude", "codex", "goose", "grok", "copilot-vscode", "agents-md"])
def test_the_record_validates_against_the_schema(framework, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "agentteams" / "schemas" / "team-manifest.schema.json").read_text(encoding="utf-8"))
    m = _manifest(framework, goose_tool_scoping="grant") if framework == "goose" else _manifest(framework)
    _render(framework, m)
    we.record(m, framework, project_root=tmp_path)
    jsonschema.validate({we.KEY: m[we.KEY]}, {**schema, "required": []})
    bad = {framework: {**m[we.KEY][framework], "subagents": "ENFORCED-ish"}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({we.KEY: bad}, {**schema, "required": []})
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({we.KEY: {framework: {**m[we.KEY][framework], "extra": 1}}}, {**schema, "required": []})


def test_new_modules_are_integrity_pinned():
    from agentteams.integrity import ENFORCEMENT_MODULES

    assert "agentteams/wasm_enforcement.py" in ENFORCEMENT_MODULES
    assert "agentteams/frameworks/_codex_wasm_gate.py" in ENFORCEMENT_MODULES
