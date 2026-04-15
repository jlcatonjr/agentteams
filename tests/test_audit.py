"""
Tests for src/audit.py
"""

from __future__ import annotations

import pytest
from pathlib import Path
from agentteams.audit import (
    AuditFinding,
    AuditResult,
    run_post_audit,
    print_audit_report,
    _check_unresolved_placeholders,
    _check_unresolved_manual_placeholders,
    _check_yaml_front_matter,
    _check_project_name_consistency,
    _check_required_agents_present,
    _check_workstream_expert_coverage,
    _check_invariant_core_present,
    _check_return_handoff_present,
    _check_readonly_tool_declarations,
    _check_dangling_agent_slugs,
    _check_ch14_inline_data_blocks,
    _check_ch20_duplicate_descriptions,
    _build_ai_context,
    _load_files_from_disk,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_AGENT_CONTENT = """\
---
name: Orchestrator — TestProject
description: "Coordinates all agents."
user-invokable: false
tools: ['read', 'edit']
model: ["Claude Sonnet 4.6 (copilot)"]
---

# Orchestrator — TestProject

You coordinate the team.
"""

_MINIMAL_MANIFEST = {
    "project_name": "TestProject",
    "framework": "copilot-vscode",
    "agent_slug_list": ["orchestrator", "adversarial", "conflict-auditor"],
    "components": [],
    "auto_resolved_placeholders": {},
}


def _file_map(*pairs: tuple[str, str]) -> dict[str, str]:
    return dict(pairs)


# ---------------------------------------------------------------------------
# _check_unresolved_placeholders
# ---------------------------------------------------------------------------

def test_unresolved_placeholder_detects_stray_token():
    file_map = {"orchestrator.agent.md": "Some text {STRAY_TOKEN} here."}
    findings = _check_unresolved_placeholders(file_map)
    assert any(f.code == "UNRESOLVED_PLACEHOLDER" and "STRAY_TOKEN" in f.description for f in findings)


def test_unresolved_placeholder_ignores_safe_tokens():
    file_map = {"nav.agent.md": "Use {UPPER_SNAKE_CASE} for constants and {TODO} markers."}
    findings = _check_unresolved_placeholders(file_map)
    assert not findings


def test_unresolved_placeholder_skips_setup_required():
    file_map = {"SETUP-REQUIRED.md": "Search for {MISSING_TOKEN} and fill in."}
    findings = _check_unresolved_placeholders(file_map)
    assert not findings


def test_unresolved_placeholder_deduplicates_per_file():
    file_map = {"agent.md": "{SAME_TOKEN} used here and also {SAME_TOKEN} again."}
    findings = _check_unresolved_placeholders(file_map)
    codes = [f.code for f in findings if f.code == "UNRESOLVED_PLACEHOLDER"]
    # Only one finding per unique token per file, not one per occurrence
    assert len(codes) == 1


def test_unresolved_placeholder_clean_file_returns_empty():
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    findings = _check_unresolved_placeholders(file_map)
    assert not findings


def test_unresolved_manual_placeholder_detects_manual_token():
    file_map = {"ref-pandas-reference.md": "Docs: {MANUAL:TOOL_DOCS_URL}"}
    findings = _check_unresolved_manual_placeholders(file_map)
    assert any(f.code == "UNRESOLVED_MANUAL_PLACEHOLDER" for f in findings)


def test_unresolved_manual_placeholder_skips_setup_required():
    file_map = {"SETUP-REQUIRED.md": "Search for {MANUAL:TOOL_DOCS_URL}."}
    findings = _check_unresolved_manual_placeholders(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# _check_yaml_front_matter
# ---------------------------------------------------------------------------

def test_yaml_missing_front_matter_flagged_as_error():
    file_map = {"navigator.agent.md": "# Navigator\n\nNo front matter here.\n"}
    findings = _check_yaml_front_matter(file_map)
    assert any(f.code == "YAML_MISSING" and f.severity == "error" for f in findings)


def test_yaml_unclosed_block_flagged_as_error():
    # No second '---' to close the front matter block
    file_map = {"bad.agent.md": "---\nname: Bad\ndescription: missing closing marker\n"}
    findings = _check_yaml_front_matter(file_map)
    assert any(f.code == "YAML_MALFORMED" and f.severity == "error" for f in findings)


def test_yaml_missing_field_flagged_as_warning():
    content = "---\nname: Agent — Project\ndescription: desc\ntools: []\n---\n# body"
    file_map = {"agent.agent.md": content}
    findings = _check_yaml_front_matter(file_map)
    # 'model' key is missing → one warning
    assert any(f.code == "YAML_MISSING_FIELD" and "model" in f.description for f in findings)


def test_yaml_valid_file_returns_no_findings():
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    findings = _check_yaml_front_matter(file_map)
    assert not findings


def test_yaml_skips_reference_files():
    file_map = {"references/ref-tool.md": "# Tool Reference\n\nNo YAML here.\n"}
    findings = _check_yaml_front_matter(file_map)
    assert not findings


def test_yaml_skips_non_agent_files():
    file_map = {"SETUP-REQUIRED.md": "# SETUP\n\nNo YAML needed.\n"}
    findings = _check_yaml_front_matter(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# _check_project_name_consistency
# ---------------------------------------------------------------------------

def test_project_name_mismatch_flagged():
    content = "---\nname: Orchestrator — WrongProject\ndescription: x\ntools: []\nmodel: []\n---\n"
    file_map = {"orchestrator.agent.md": content}
    manifest = {**_MINIMAL_MANIFEST, "project_name": "TestProject"}
    findings = _check_project_name_consistency(file_map, manifest)
    assert any(f.code == "PROJECT_NAME_MISMATCH" for f in findings)


def test_project_name_match_no_finding():
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    manifest = {**_MINIMAL_MANIFEST, "project_name": "TestProject"}
    findings = _check_project_name_consistency(file_map, manifest)
    assert not findings


def test_project_name_skips_when_manifest_name_empty():
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    manifest = {**_MINIMAL_MANIFEST, "project_name": ""}
    findings = _check_project_name_consistency(file_map, manifest)
    assert not findings


def test_project_name_skips_when_manifest_name_is_manual():
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    manifest = {**_MINIMAL_MANIFEST, "project_name": "{MANUAL:PROJECT_NAME}"}
    findings = _check_project_name_consistency(file_map, manifest)
    assert not findings


def test_project_name_comparison_is_case_insensitive():
    content = "---\nname: Orchestrator — testproject\ndescription: x\ntools: []\nmodel: []\n---\n"
    file_map = {"orchestrator.agent.md": content}
    manifest = {**_MINIMAL_MANIFEST, "project_name": "TestProject"}
    findings = _check_project_name_consistency(file_map, manifest)
    assert not findings


# ---------------------------------------------------------------------------
# _check_required_agents_present
# ---------------------------------------------------------------------------

def test_missing_orchestrator_flagged_as_error():
    file_map = {
        "adversarial.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
    }
    findings = _check_required_agents_present(file_map, _MINIMAL_MANIFEST)
    assert any(
        f.code == "MISSING_REQUIRED_AGENT" and "orchestrator" in f.description and f.severity == "error"
        for f in findings
    )


def test_missing_adversarial_flagged_as_error():
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
    }
    findings = _check_required_agents_present(file_map, _MINIMAL_MANIFEST)
    assert any(f.code == "MISSING_REQUIRED_AGENT" and "adversarial" in f.description for f in findings)


def test_all_required_agents_present_no_finding():
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        "adversarial.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
    }
    findings = _check_required_agents_present(file_map, _MINIMAL_MANIFEST)
    assert not findings


def test_required_agents_ignores_reference_files():
    # A file like references/orchestrator-notes.md should NOT count as the orchestrator agent
    file_map = {
        "references/orchestrator-notes.md": "# Notes",
        "adversarial.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
    }
    findings = _check_required_agents_present(file_map, _MINIMAL_MANIFEST)
    assert any(f.code == "MISSING_REQUIRED_AGENT" and "orchestrator" in f.description for f in findings)


# ---------------------------------------------------------------------------
# _check_workstream_expert_coverage
# ---------------------------------------------------------------------------

def test_missing_workstream_expert_flagged():
    manifest = {
        **_MINIMAL_MANIFEST,
        "components": [{"slug": "pipeline-core"}],
    }
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    findings = _check_workstream_expert_coverage(file_map, manifest)
    assert any(
        f.code == "MISSING_WORKSTREAM_EXPERT" and "pipeline-core" in f.description
        for f in findings
    )


def test_workstream_expert_present_no_finding():
    manifest = {
        **_MINIMAL_MANIFEST,
        "components": [{"slug": "pipeline-core"}],
    }
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        "pipeline-core-expert.agent.md": _VALID_AGENT_CONTENT,
    }
    findings = _check_workstream_expert_coverage(file_map, manifest)
    assert not findings


def test_no_components_returns_no_finding():
    manifest = {**_MINIMAL_MANIFEST, "components": []}
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    findings = _check_workstream_expert_coverage(file_map, manifest)
    assert not findings


# ---------------------------------------------------------------------------
# run_post_audit (integration)
# ---------------------------------------------------------------------------

def test_run_post_audit_clean_team_returns_no_errors():
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        "adversarial.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
    }
    rendered = list(file_map.items())
    manifest = {**_MINIMAL_MANIFEST, "components": []}
    result = run_post_audit(Path("/nonexistent"), manifest, rendered_files=rendered, ai_audit=False)
    assert not result.has_errors


def test_run_post_audit_missing_required_agent_returns_error():
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        # adversarial and conflict-auditor missing
    }
    rendered = list(file_map.items())
    manifest = {**_MINIMAL_MANIFEST, "components": []}
    result = run_post_audit(Path("/nonexistent"), manifest, rendered_files=rendered, ai_audit=False)
    assert result.has_errors


def test_run_post_audit_manual_placeholder_returns_warning():
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        "adversarial.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
        "references/ref-pandas-reference.md": "Docs: {MANUAL:TOOL_DOCS_URL}",
    }
    rendered = list(file_map.items())
    manifest = {**_MINIMAL_MANIFEST, "components": []}
    result = run_post_audit(Path("/nonexistent"), manifest, rendered_files=rendered, ai_audit=False)
    assert not result.has_errors
    assert result.has_warnings
    assert any(f.code == "UNRESOLVED_MANUAL_PLACEHOLDER" for f in result.static_findings)


def test_run_post_audit_no_ai_when_ai_audit_false():
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    rendered = list(file_map.items())
    result = run_post_audit(Path("/nonexistent"), _MINIMAL_MANIFEST, rendered_files=rendered, ai_audit=False)
    assert result.ai_report is None
    assert result.ai_available is False


# ---------------------------------------------------------------------------
# print_audit_report (smoke test)
# ---------------------------------------------------------------------------

def test_print_audit_report_clean_result(capsys):
    result = AuditResult()
    print_audit_report(result)
    captured = capsys.readouterr()
    assert "clean" in captured.out
    assert "CLEARED" in captured.out


def test_print_audit_report_with_error(capsys):
    result = AuditResult(static_findings=[
        AuditFinding("PRESUPPOSITION", "MISSING_REQUIRED_AGENT", "error", "(team)", "Missing orchestrator")
    ])
    print_audit_report(result)
    captured = capsys.readouterr()
    assert "ERRORS FOUND" in captured.out
    assert "MISSING_REQUIRED_AGENT" in captured.out


def test_print_audit_report_with_warning(capsys):
    result = AuditResult(static_findings=[
        AuditFinding("CONFLICT", "YAML_MISSING_FIELD", "warning", "nav.agent.md", "Missing 'model'")
    ])
    print_audit_report(result)
    captured = capsys.readouterr()
    assert "WARNINGS" in captured.out


def test_print_audit_report_ai_unavailable_shows_hint(capsys):
    result = AuditResult(ai_available=False)
    print_audit_report(result)
    captured = capsys.readouterr()
    assert "copilot" in captured.out


# ---------------------------------------------------------------------------
# _load_files_from_disk
# ---------------------------------------------------------------------------

def test_load_files_from_disk_reads_md_and_csv(tmp_path):
    (tmp_path / "agent.agent.md").write_text("# Agent", encoding="utf-8")
    (tmp_path / "conflict-log.csv").write_text("date,file\n", encoding="utf-8")
    (tmp_path / "build.json").write_text("{}", encoding="utf-8")  # should be excluded

    file_map = _load_files_from_disk(tmp_path)
    assert "agent.agent.md" in file_map
    assert "conflict-log.csv" in file_map
    assert "build.json" not in file_map


def test_load_files_from_disk_nonexistent_dir_returns_empty(tmp_path):
    file_map = _load_files_from_disk(tmp_path / "does_not_exist")
    assert file_map == {}


# ---------------------------------------------------------------------------
# _build_ai_context
# ---------------------------------------------------------------------------

def test_build_ai_context_includes_project_name():
    file_map = {"orchestrator.agent.md": _VALID_AGENT_CONTENT}
    manifest = {**_MINIMAL_MANIFEST, "project_name": "MyApp"}
    context = _build_ai_context(file_map, manifest)
    assert "MyApp" in context


def test_build_ai_context_respects_limit():
    # Create many files to exceed the character limit
    file_map = {
        f"agent-{i}.agent.md": _VALID_AGENT_CONTENT
        for i in range(200)
    }
    manifest = {**_MINIMAL_MANIFEST, "project_name": "BigProject"}
    context = _build_ai_context(file_map, manifest)
    assert len(context) < 20_000  # sanity check: well under any model limit


# ---------------------------------------------------------------------------
# _check_invariant_core_present (AR-01)
# ---------------------------------------------------------------------------

_AGENT_WITH_INVARIANT = """\
---
name: Auditor — TestProject
description: "Does things."
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6 (copilot)"]
---

# Auditor

> ⛔ **Do not modify or omit.**

## Core Responsibilities

1. Check things
"""

def test_invariant_core_missing_flagged():
    file_map = {"navigator.agent.md": _VALID_AGENT_CONTENT}
    findings = _check_invariant_core_present(file_map)
    assert any(f.code == "AR_MISSING_INVARIANT_CORE" for f in findings)


def test_invariant_core_present_no_finding():
    file_map = {"auditor.agent.md": _AGENT_WITH_INVARIANT}
    findings = _check_invariant_core_present(file_map)
    assert not findings


def test_invariant_core_skips_reference_files():
    file_map = {"references/data.md": "# Data\n\nNo invariant needed.\n"}
    findings = _check_invariant_core_present(file_map)
    assert not findings


def test_invariant_core_skips_non_agent_files():
    file_map = {"SETUP-REQUIRED.md": "# Setup\n\nNo invariant here.\n"}
    findings = _check_invariant_core_present(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# _check_return_handoff_present (AR-02)
# ---------------------------------------------------------------------------

_AGENT_WITH_RETURN_HANDOFF = """\
---
name: Navigator — TestProject
description: "Routes things."
user-invokable: false
tools: ['read']
model: ["Claude Sonnet 4.6 (copilot)"]
handoffs:
  - label: Return to Orchestrator
    agent: orchestrator
    prompt: "Done."
    send: false
---

# Navigator

> ⛔ **Do not modify or omit.**
"""

def test_return_handoff_missing_flagged():
    content = _AGENT_WITH_INVARIANT  # has no handoffs block
    file_map = {"auditor.agent.md": content}
    findings = _check_return_handoff_present(file_map)
    assert any(f.code == "AR_MISSING_RETURN_HANDOFF" for f in findings)


def test_return_handoff_present_no_finding():
    file_map = {"navigator.agent.md": _AGENT_WITH_RETURN_HANDOFF}
    findings = _check_return_handoff_present(file_map)
    assert not findings


def test_return_handoff_skips_orchestrator_itself():
    # The orchestrator.agent.md file doesn't need a return handoff to itself
    file_map = {"orchestrator.agent.md": _AGENT_WITH_INVARIANT}
    findings = _check_return_handoff_present(file_map)
    assert not any(f.code == "AR_MISSING_RETURN_HANDOFF" for f in findings)


def test_return_handoff_skips_reference_files():
    file_map = {"references/ref-tool.md": "# Tool\n\nNo handoff here.\n"}
    findings = _check_return_handoff_present(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# _check_readonly_tool_declarations (AR-03)
# ---------------------------------------------------------------------------

def test_readonly_tool_violation_detected():
    content = """\
---
name: Reader — TestProject
description: "x"
user-invokable: false
tools: ['read', 'edit', 'search']
model: ["x"]
---

# Reader

You are **read-only**: you do not write code, modify files, or execute commands.

> ⛔ **Do not modify or omit.**
"""
    file_map = {"reader.agent.md": content}
    findings = _check_readonly_tool_declarations(file_map)
    assert any(f.code == "AR_READONLY_TOOL_VIOLATION" and f.severity == "error" for f in findings)


def test_readonly_tool_no_violation_when_clean():
    content = """\
---
name: Reader — TestProject
description: "x"
user-invokable: false
tools: ['read', 'search']
model: ["x"]
---

# Reader

You are **read-only**: you do not write code, modify files, or execute commands.

> ⛔ **Do not modify or omit.**
"""
    file_map = {"reader.agent.md": content}
    findings = _check_readonly_tool_declarations(file_map)
    assert not findings


def test_readonly_tool_skips_agents_not_declaring_readonly():
    # Agent doesn't say "read-only" in body → not subject to this check
    content = """\
---
name: Producer — TestProject
description: "x"
user-invokable: false
tools: ['read', 'edit', 'execute']
model: ["x"]
---

# Producer

You write code and modify files.

> ⛔ **Do not modify or omit.**
"""
    file_map = {"producer.agent.md": content}
    findings = _check_readonly_tool_declarations(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# _check_dangling_agent_slugs (AR-04)
# ---------------------------------------------------------------------------

def test_dangling_slug_detected():
    content = """\
---
name: Agent — TestProject
description: "x"
user-invokable: false
tools: ['read']
model: ["x"]
agents: ['nonexistent-agent']
---

# Agent

> ⛔ **Do not modify or omit.**
"""
    file_map = {"some-agent.agent.md": content}
    findings = _check_dangling_agent_slugs(file_map)
    assert any(f.code == "AR_DANGLING_AGENT_SLUG" and "nonexistent-agent" in f.description for f in findings)


def test_no_dangling_slug_when_file_exists():
    content = """\
---
name: Agent — TestProject
description: "x"
user-invokable: false
tools: ['read']
model: ["x"]
agents: ['other-agent']
---

# Agent

> ⛔ **Do not modify or omit.**
"""
    file_map = {
        "some-agent.agent.md": content,
        "other-agent.agent.md": _AGENT_WITH_INVARIANT,
    }
    findings = _check_dangling_agent_slugs(file_map)
    assert not any(f.code == "AR_DANGLING_AGENT_SLUG" for f in findings)


def test_dangling_slug_skips_reference_files():
    file_map = {"references/ref.md": "agents: ['missing-agent']"}
    findings = _check_dangling_agent_slugs(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# _check_ch14_inline_data_blocks (CH-14)
# ---------------------------------------------------------------------------

def _make_large_table(rows: int) -> str:
    lines = [
        "---",
        "name: Agent — TestProject",
        "description: x",
        "user-invokable: false",
        "tools: ['read']",
        "model: [x]",
        "---",
        "",
        "# Agent",
        "",
        "> ⛔ **Do not modify or omit.**",
        "",
        "## Data Section",
        "",
        "| Col A | Col B |",
        "| --- | --- |",
    ]
    for i in range(rows):
        lines.append(f"| row {i} | value {i} |")
    return "\n".join(lines)


def test_ch14_large_table_outside_invariant_flagged():
    file_map = {"agent.agent.md": _make_large_table(15)}
    findings = _check_ch14_inline_data_blocks(file_map)
    assert any(f.code == "CH14_INLINE_DATA_BLOCK" for f in findings)


def test_ch14_small_table_not_flagged():
    file_map = {"agent.agent.md": _make_large_table(5)}
    findings = _check_ch14_inline_data_blocks(file_map)
    assert not findings


def test_ch14_skips_reference_files():
    # Reference files are expected to contain large tables
    big_content = "\n".join(f"| row {i} | value {i} |" for i in range(20))
    file_map = {"references/big-ref.md": big_content}
    findings = _check_ch14_inline_data_blocks(file_map)
    assert not findings


def test_ch14_skips_non_agent_files():
    big_content = "\n".join(f"| row {i} | value {i} |" for i in range(20))
    file_map = {"SETUP-REQUIRED.md": big_content}
    findings = _check_ch14_inline_data_blocks(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# _check_ch20_duplicate_descriptions (CH-20)
# ---------------------------------------------------------------------------

def test_ch20_duplicate_description_flagged():
    content_a = '---\nname: A — P\ndescription: "Coordinates the team."\ntools: []\nmodel: []\n---\n'
    content_b = '---\nname: B — P\ndescription: "Coordinates the team."\ntools: []\nmodel: []\n---\n'
    file_map = {"agent-a.agent.md": content_a, "agent-b.agent.md": content_b}
    findings = _check_ch20_duplicate_descriptions(file_map)
    assert any(f.code == "CH20_DUPLICATE_DESCRIPTION" for f in findings)


def test_ch20_unique_descriptions_no_finding():
    content_a = '---\nname: A — P\ndescription: "Coordinates agents."\ntools: []\nmodel: []\n---\n'
    content_b = '---\nname: B — P\ndescription: "Audits conflicts."\ntools: []\nmodel: []\n---\n'
    file_map = {"agent-a.agent.md": content_a, "agent-b.agent.md": content_b}
    findings = _check_ch20_duplicate_descriptions(file_map)
    assert not findings


def test_ch20_skips_reference_files():
    desc_line = 'description: "Same description."'
    file_map = {
        "references/ref-a.md": desc_line,
        "agent-b.agent.md": f"---\nname: B — P\n{desc_line}\ntools: []\nmodel: []\n---\n",
    }
    # Only one agent file has this description → no duplicate
    findings = _check_ch20_duplicate_descriptions(file_map)
    assert not findings


# ---------------------------------------------------------------------------
# run_post_audit: agent_refactor_findings and code_hygiene_findings populated
# ---------------------------------------------------------------------------

def test_run_post_audit_populates_agent_refactor_findings():
    # File with no invariant core — should produce an AR finding
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        "adversarial.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
        "navigator.agent.md": _VALID_AGENT_CONTENT,  # no ⛔ → AR warning
    }
    rendered = list(file_map.items())
    result = run_post_audit(Path("/nonexistent"), _MINIMAL_MANIFEST, rendered_files=rendered, ai_audit=False)
    assert any(f.code == "AR_MISSING_INVARIANT_CORE" for f in result.agent_refactor_findings)


def test_run_post_audit_populates_code_hygiene_findings():
    big_table_file = _make_large_table(15)
    file_map = {
        "orchestrator.agent.md": _VALID_AGENT_CONTENT,
        "adversarial.agent.md": _VALID_AGENT_CONTENT,
        "conflict-auditor.agent.md": _VALID_AGENT_CONTENT,
        "data-agent.agent.md": big_table_file,
    }
    rendered = list(file_map.items())
    result = run_post_audit(Path("/nonexistent"), _MINIMAL_MANIFEST, rendered_files=rendered, ai_audit=False)
    assert any(f.code == "CH14_INLINE_DATA_BLOCK" for f in result.code_hygiene_findings)


def test_print_audit_report_shows_all_three_sections(capsys):
    result = AuditResult(
        static_findings=[
            AuditFinding("CONFLICT", "UNRESOLVED_PLACEHOLDER", "warning", "a.agent.md", "x"),
        ],
        agent_refactor_findings=[
            AuditFinding("AGENT_REFACTOR", "AR_MISSING_INVARIANT_CORE", "warning", "b.agent.md", "x"),
        ],
        code_hygiene_findings=[
            AuditFinding("CODE_HYGIENE", "CH14_INLINE_DATA_BLOCK", "warning", "c.agent.md", "x"),
        ],
    )
    print_audit_report(result)
    captured = capsys.readouterr()
    assert "Conflict + Presupposition Audit" in captured.out
    assert "Agent-Refactor Spec Compliance" in captured.out
    assert "Code Hygiene Audit" in captured.out

