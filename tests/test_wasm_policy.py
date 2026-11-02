"""WASM plan P14: ``agentteams.wasm_policy`` — the render-time ``run_wasm`` / native-exec text.

* Off (absent, ``off``, or the ``wasm`` framework): every function is the identity and both placeholders are ``""``.
* Eligibility is P01's ``wasm_eligible`` on the tools before P13's narrowing; the module defines none of its own.
* The section states the agent's grant (project-relative paths, limits), the per-host wiring (P11), and what
  happened to the shell (the before/after difference, never a recomputed policy); read-only auditors get nothing,
  ``@security`` gets a review section with no usage text.
* :func:`verify_claims` refuses a render whose text and P11's per-agent wiring disagree.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from agentteams import analyze, wasm_policy
from agentteams.cli import sandbox_effects as fx
from agentteams.wasm_config import WASM_SANDBOX_CLI_KEY

REPO = Path(__file__).resolve().parent.parent
BRIEF = json.loads((REPO / "examples" / "software-project" / "brief.json").read_text(encoding="utf-8"))
SHELL = "---\nname: X\ndescription: d\ntools: ['read', 'search', 'execute']\n---\n\n# X\n\nbody\n"
NARROWED = SHELL.replace(", 'execute'", "")
AUDITOR = "---\nname: A\ndescription: d\ntools: ['read', 'search']\n---\n\n# A\n\nbody\n"
FENCED = SHELL.replace("body\n", "<!-- AGENTTEAMS:BEGIN content v=1 -->\nbody\n<!-- AGENTTEAMS:END content -->\n")


def _manifest(framework: str = "claude", *, wasm: str | None = "on", **brief: object) -> dict:
    manifest = analyze.build_manifest({**BRIEF, **brief}, framework=framework)
    if wasm is not None:
        manifest[WASM_SANDBOX_CLI_KEY] = wasm
    return manifest


# --- off -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("wasm", [None, "off"])
def test_identity_and_empty_placeholders_while_off(wasm):
    m = _manifest(wasm=wasm)
    assert wasm_policy.apply(SHELL, "cleanup", m, "claude") == SHELL
    assert wasm_policy.apply_instructions("# I\n", m, "claude") == "# I\n"
    wasm_policy.populate_placeholders(m)
    rows = m["auto_resolved_placeholders"]
    assert rows[wasm_policy.SECURITY_ROWS_PLACEHOLDER] == rows[wasm_policy.ROUTING_ROWS_PLACEHOLDER] == ""
    assert wasm_policy.CLAIMS_KEY not in m and "wasm_wiring" not in m
    wasm_policy.verify_claims(m, "claude")  # nothing to check


def test_analyze_defaults_both_placeholders_to_empty():
    rows = analyze.build_manifest(dict(BRIEF), framework="claude")["auto_resolved_placeholders"]
    assert rows["WASM_SECURITY_TRIGGER_ROWS"] == rows["WASM_ROUTING_ROWS"] == ""


def test_the_wasm_runtime_framework_is_left_alone():
    m = _manifest("wasm")
    assert not wasm_policy.enabled(m, "wasm")
    assert wasm_policy.apply(SHELL, "cleanup", m, "wasm") == SHELL
    wasm_policy.populate_placeholders(m)
    assert m["auto_resolved_placeholders"][wasm_policy.ROUTING_ROWS_PLACEHOLDER] == ""


# --- who gets what ---------------------------------------------------------------------------------------------------

def test_eligible_agent_gets_one_agent_section():
    out = wasm_policy.apply(SHELL, "cleanup", _manifest(), "claude")
    assert out.startswith(SHELL) and out.count("## WASM Sandbox:") == 1
    assert "## WASM Sandbox: Run Code Through `run_wasm`" in out and "`mcp__agentteams_wasm__run_wasm`" in out
    for phrase in ("Bash, git, npm, pip", "no network", "Output is data (C-4)", "compute only", "--wasm-install"):
        assert phrase in out


@pytest.mark.parametrize("slug", ["adversarial", "conflict-auditor", "code-hygiene", "quality-auditor",
                                  "technical-validator", "auth-module-expert"])
def test_read_only_auditors_get_nothing(slug):
    m = _manifest()
    assert wasm_policy.apply(AUDITOR, slug, m, "claude") == AUDITOR
    # even an auditor-named file that holds a shell gets no orchestrator/security text
    assert "Reviewing Grants" not in wasm_policy.apply(SHELL, slug, m, "claude")


def test_security_gets_review_text_and_no_usage_text():
    out = wasm_policy.apply(AUDITOR, "security", _manifest(), "claude")
    assert "## WASM Sandbox: Reviewing Grants and Relaxations" in out
    for kind in (fx.WASM_GRANT_WIDEN, fx.NATIVE_EXEC_KEEP, fx.SANDBOX_WASM_OFF, fx.WASM_TO_OS_SWITCH):
        assert f"`{kind}`" in out
    assert "Run Code Through" not in out and "Your grant" not in out and "Ed25519" in out


def test_orchestrator_gets_routing_section_with_the_laundering_rule():
    out = wasm_policy.apply(SHELL, "orchestrator", _manifest(), "claude")
    assert "## WASM Sandbox: Routing Code and Grants" in out and "Run Code Through" not in out
    assert "Never launder code." in out and "fixed exemption" in out
    assert "`@git-operations`, `@navigator`" in out  # D12 default native_exec_keep


def test_instructions_section_names_the_session_example():
    out = wasm_policy.apply_instructions("# I\n", _manifest(), "claude")
    assert "Routing Code and Grants" in out and ".claude/mcp.agentteams-wasm.example.json" in out


# --- grant and host text ---------------------------------------------------------------------------------------------

def test_widened_grant_is_stated_with_project_relative_paths_and_limits():
    m = _manifest(wasm_sandbox={"enabled": True, "agents": {"cleanup": {"read_paths": ["src", "docs"],
                                                                        "scratch": True}}})
    out = wasm_policy.apply(SHELL, "cleanup", m, "claude")
    assert "read-only `docs/` (as `/project/docs`), `src/` (as `/project/src`)" in out
    assert "`.agentteams/wasm/scratch/cleanup/`" in out and "64 MiB" in out and "120 s" in out
    assert "/Users/" not in out and "/home/" not in out


def test_codex_states_the_shared_compute_only_instance():
    out = wasm_policy.apply(SHELL, "cleanup", _manifest("codex"), "codex")
    assert "shared compute-only instance" in out and "mcp__agentteams_wasm" not in out


@pytest.mark.parametrize("framework, status", [("copilot-vscode", "example-only"), ("grok", "unverified-format"),
                                               ("agents-md", "instructions-only")])
def test_hosts_without_run_wasm_get_the_advisory_wording(framework, status):
    out = wasm_policy.apply(SHELL, "cleanup", _manifest(framework), framework)
    assert "## WASM Sandbox: You Have No `run_wasm`" in out and f"`{status}`" in out
    assert "advisory only" in out and "Run Code Through" not in out


def test_goose_wired_recipes_carry_the_delegate_caveat():
    out = wasm_policy.apply(SHELL, "cleanup", _manifest("goose", goose_tool_scoping="grant"), "goose")
    assert "Run Code Through" in out and "As a delegate you inherit" in out


# --- native shell wording --------------------------------------------------------------------------------------------

def test_narrowed_agent_is_told_its_shell_was_removed():
    out = wasm_policy.apply(NARROWED, "cleanup", _manifest(), "claude", before=SHELL)
    assert "**Your native shell:** removed." in out and "only way to run code" in out


def test_kept_agent_is_told_why():
    out = wasm_policy.apply(SHELL, "git-operations", _manifest(), "claude")
    assert "operator exception (`native_exec_keep`" in out
    keep_all = _manifest(wasm_sandbox={"enabled": True, "native_exec": "keep", "native_exec_keep": []})
    assert "kept (`native_exec: keep`" in wasm_policy.apply(SHELL, "cleanup", keep_all, "claude")


def test_builder_keeps_its_shell_and_has_no_per_agent_copy():
    m = _manifest()
    out = wasm_policy.apply(SHELL, "team-builder", m, "claude", builder=True)
    assert "You Have No `run_wasm`" in out and "fixed exemption: the team builder" in out
    assert "gets no per-agent `agentteams_wasm` copy" in out
    assert "Run Code Through" in wasm_policy.apply(SHELL, "team-builder", _manifest("codex"), "codex", builder=True)


def test_an_agent_without_tools_is_never_offered_anything():
    bare = "---\nname: X\ndescription: d\n---\n\nbody\n"
    assert wasm_policy.apply(bare, "cleanup", _manifest(), "claude") == bare


# --- fences ----------------------------------------------------------------------------------------------------------

def test_unfenced_body_gets_a_plain_section_and_fenced_body_its_own_fence():
    m = _manifest()
    assert "AGENTTEAMS:BEGIN wasm_sandbox" not in wasm_policy.apply(SHELL, "cleanup", m, "claude")
    out = wasm_policy.apply(FENCED, "cleanup", m, "claude")
    assert "<!-- AGENTTEAMS:BEGIN wasm_sandbox v=1 -->\n## WASM Sandbox:" in out
    assert out.rstrip().endswith("<!-- AGENTTEAMS:END wasm_sandbox -->")


# --- claims vs wiring ------------------------------------------------------------------------------------------------

def test_verify_claims_refuses_text_without_wiring_and_wiring_without_text():
    m = _manifest()
    wasm_policy.begin(m)
    wasm_policy.apply(SHELL, "cleanup", m, "claude")
    with pytest.raises(ValueError, match=r"run_wasm stated for \['cleanup'\] but not wired"):
        wasm_policy.verify_claims(m, "claude")
    m["wasm_wiring"]["frameworks"]["claude"]["agents"] = ["cleanup", "navigator"]
    with pytest.raises(ValueError, match=r"wired for \['navigator'\] without its section"):
        wasm_policy.verify_claims(m, "claude")
    m["wasm_wiring"]["frameworks"]["claude"]["agents"] = ["cleanup"]
    wasm_policy.verify_claims(m, "claude")


def test_a_json_loaded_claims_record_is_ignored():
    m = _manifest()
    m[wasm_policy.CLAIMS_KEY] = {"navigator": True}  # not the private type: replaced, never trusted
    wasm_policy.verify_claims(m, "claude")


# --- placeholders and reference doc ----------------------------------------------------------------------------------

def test_placeholders_while_on():
    m = _manifest()
    wasm_policy.populate_placeholders(m)
    rows = m["auto_resolved_placeholders"][wasm_policy.SECURITY_ROWS_PLACEHOLDER]
    assert rows.startswith("\n| ") and rows.count("\n| ") == 3
    for name in ("agentteams-wasm-mcp.py", "wasm_exec.py", "wasm_guests.py", "_sandbox_profiles.py",
                 ".agentteams/wasm/", "native_exec_keep", fx.WASM_TO_OS_SWITCH):
        assert name in rows
    routing = m["auto_resolved_placeholders"][wasm_policy.ROUTING_ROWS_PLACEHOLDER]
    assert routing.count("\n| ") == 2 and "`@security` first" in routing


def test_both_placeholders_are_documented_and_placed_at_row_ends():
    doc = (REPO / "agentteams" / "templates" / "PLACEHOLDER-CONVENTIONS.md").read_text(encoding="utf-8")
    assert "`{WASM_ROUTING_ROWS}`" in doc and "`{WASM_SECURITY_TRIGGER_ROWS}`" in doc
    tpl = REPO / "agentteams" / "templates" / "universal"
    sec = (tpl / "security.template.md").read_text(encoding="utf-8")
    orch = (tpl / "orchestrator.template.md").read_text(encoding="utf-8")
    assert "(C-4 derived content) |{WASM_SECURITY_TRIGGER_ROWS}\n" in sec
    assert "|{ADOPTED_AGENT_ROUTING_ROWS}{WASM_ROUTING_ROWS}\n<!-- AGENTTEAMS:END routing_table_rows -->" in orch


def test_reference_doc_restates_the_manifest():
    m = _manifest(wasm_sandbox={"enabled": True, "agents": {"cleanup": {"read_paths": ["src"]}}})
    doc = wasm_policy.reference_doc(m, "claude")
    assert doc.startswith("# WASM Sandbox Reference") and "`@cleanup`" in doc and "`src/`" in doc
    assert "native_exec: replace" in doc and "--wasm-install" in doc and "C-4" in doc


# --- single source of eligibility -------------------------------------------------------------------------------------

def test_wasm_policy_defines_no_eligibility_of_its_own():
    tree = ast.parse((REPO / "agentteams" / "wasm_policy.py").read_text(encoding="utf-8"))
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not literals & {"execute", "Bash", "shell"}  # tool classes are wasm_config's to read
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module ==
                "agentteams.wasm_config" for a in n.names}
    assert "wasm_eligible" in imported
    assert not any(isinstance(n, ast.ImportFrom) and n.module and "wasm_enforcement" in n.module
                   for n in ast.walk(tree))
