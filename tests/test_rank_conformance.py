"""Tests for the AP-2 rank-conformance validator (agentteams.rank_conformance).

Mirrors tests/test_agent_tool_scopes.py in intent: the shipped team is the
regression guard against a false-positive wall — every live .claude/agents/*.md
file MUST be clean under the seeded policy. Synthetic cases exercise the
finding rule (over-grant flagged, override cleared, workstream ceiling).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.rank_conformance import (
    agents_missing_capability_key,
    check_rank_conformance,
    disposition_exit_code,
    rank_for,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

# A Claude-shape front matter using a `tools:` scalar (canonical vocab is parsed
# via capability_map.canonical_tools_for_claude → claude tool names).
_FM = """---
name: {slug}
description: synthetic test agent
tools: {tools}
model: sonnet
---

Body.
"""


def _agent(slug: str, claude_tools: str) -> str:
    return _FM.format(slug=slug, tools=claude_tools)


def test_rank_for_derives_all_four_ranks():
    assert rank_for("orchestrator") == "orchestrator"
    assert rank_for("navigator") == "governance"  # in GOVERNANCE_AGENTS
    assert rank_for("pipeline-core-expert") == "workstream-expert"
    assert rank_for("primary-producer") == "domain"  # fallthrough


def test_governance_agent_with_edit_and_no_override_is_flagged():
    # 'navigator' is governance; its ceiling omits edit and it has no override.
    fm = _agent("navigator", "Read, Edit, Grep")
    findings = check_rank_conformance({"navigator.md": fm}, ".md")
    assert len(findings) == 1
    f = findings[0]
    assert f.category == "RANK_CONFORMANCE"
    assert f.code == "AP2_RANK_CAPABILITY_EXCEEDED"
    assert f.severity == "warning"
    assert f.file == "navigator.md"
    assert "edit" in f.description


def test_override_agent_with_edit_is_clean():
    # 'cleanup' is governance with a recorded {edit} override.
    fm = _agent("cleanup", "Read, Edit, Grep, Bash")
    findings = check_rank_conformance({"cleanup.md": fm}, ".md")
    assert findings == []


def test_workstream_expert_with_execute_is_flagged():
    # workstream-expert ceiling is {read, search, agent}; execute is over-grant.
    fm = _agent("pipeline-core-expert", "Read, Grep, Bash")
    findings = check_rank_conformance({"pipeline-core-expert.md": fm}, ".md")
    assert len(findings) == 1
    assert "execute" in findings[0].description


# --- SP-04: opt-in strict (blocking) disposition ---------------------------


def test_disposition_default_warn_only_exits_zero_even_with_findings():
    # --check-rank (strict=False) never fails the build, even on an over-grant.
    fm = _agent("navigator", "Read, Edit, Grep")
    findings = check_rank_conformance({"navigator.md": fm}, ".md")
    assert findings  # there IS a finding
    assert disposition_exit_code(findings, strict=False) == 0


def test_disposition_strict_exits_nonzero_on_finding():
    # --check-rank-strict blocks when any agent exceeds its ceiling.
    fm = _agent("navigator", "Read, Edit, Grep")
    findings = check_rank_conformance({"navigator.md": fm}, ".md")
    assert disposition_exit_code(findings, strict=True) == 1


def test_disposition_strict_exits_zero_when_clean():
    # No findings → strict passes.
    assert disposition_exit_code([], strict=True) == 0


def test_disposition_strict_shipped_team_is_clean():
    # The blocking gate must pass on the live team (no false wall).
    file_map = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(SHIPPED_AGENTS_DIR.glob("*.md"))
    }
    findings = check_rank_conformance(file_map, ".md")
    assert disposition_exit_code(findings, strict=True) == 0, (
        f"shipped team would fail the strict rank gate: "
        f"{[f.file for f in findings]}"
    )


# --- SP-04: dispatch wiring in run_standalone_modes (integration) -----------


def _rank_args(*, strict: bool):
    """A minimal args stub for the run_standalone_modes dispatch, with every
    pre-check-rank mode off so the check-rank branch is what runs."""
    from types import SimpleNamespace

    return SimpleNamespace(
        restore_backup=None,
        scan_security=False,
        check_budget=False,
        check_rank=not strict,  # strict implies the branch; warn-only sets check_rank
        check_rank_strict=strict,
    )


def test_dispatch_strict_returns_nonzero_on_over_grant(tmp_path):
    from agentteams.cli.standalone_modes import run_standalone_modes

    (tmp_path / "navigator.md").write_text(
        _agent("navigator", "Read, Edit, Grep"), encoding="utf-8"
    )
    rc = run_standalone_modes(
        _rank_args(strict=True), {"framework": "claude"}, {}, tmp_path, tmp_path
    )
    assert rc == 1


def test_dispatch_strict_returns_zero_when_clean(tmp_path):
    from agentteams.cli.standalone_modes import run_standalone_modes

    # governance ceiling includes read+search; "Read, Grep" is within it.
    (tmp_path / "navigator.md").write_text(
        _agent("navigator", "Read, Grep"), encoding="utf-8"
    )
    rc = run_standalone_modes(
        _rank_args(strict=True), {"framework": "claude"}, {}, tmp_path, tmp_path
    )
    assert rc == 0


def test_dispatch_warn_only_returns_zero_despite_over_grant(tmp_path):
    from agentteams.cli.standalone_modes import run_standalone_modes

    (tmp_path / "navigator.md").write_text(
        _agent("navigator", "Read, Edit, Grep"), encoding="utf-8"
    )
    rc = run_standalone_modes(
        _rank_args(strict=False), {"framework": "claude"}, {}, tmp_path, tmp_path
    )
    assert rc == 0


# --- W19: detect agents that declare NO capability key ----------------------

_NO_TOOLS_FM = """---
name: {slug}
description: synthetic test agent
model: sonnet
---

Body.
"""


def test_agents_missing_capability_key_flags_keyless_agent():
    keyless = _NO_TOOLS_FM.format(slug="navigator")
    keyed = _agent("cleanup", "Read, Grep")
    missing = agents_missing_capability_key(
        {"navigator.md": keyless, "cleanup.md": keyed}, ".md"
    )
    assert missing == ["navigator"]


def test_agents_missing_capability_key_empty_when_all_declare():
    missing = agents_missing_capability_key({"cleanup.md": _agent("cleanup", "Read")}, ".md")
    assert missing == []


def test_agents_missing_capability_key_ignores_non_agent_files():
    assert agents_missing_capability_key({"references/x.md": "no front matter"}, ".md") == []


def test_agent_with_no_capability_key_is_skipped():
    fm = "---\nname: x\ndescription: y\nmodel: sonnet\n---\nBody.\n"
    assert check_rank_conformance({"x.md": fm}, ".md") == []


def test_non_agent_files_are_ignored():
    fm = _agent("navigator", "Read, Edit")
    file_map = {
        "references/some.md": fm,          # excluded: references/
        "SETUP-REQUIRED.md": fm,           # excluded: non-agent file
        "notes.txt": fm,                   # excluded: wrong extension
    }
    assert check_rank_conformance(file_map, ".md") == []


@pytest.mark.skipif(not SHIPPED_AGENTS_DIR.exists(), reason="deployed .claude/ agent instance absent from this checkout (public release / CI)")
def test_shipped_team_is_clean_under_seeded_policy():
    """REGRESSION: the live shipped team must produce zero findings.

    Guards the false-positive wall — any addition to the shipped team that
    over-grants relative to rank (or a policy regression) fails here.
    """
    assert SHIPPED_AGENTS_DIR.is_dir(), f"missing shipped agents dir: {SHIPPED_AGENTS_DIR}"
    file_map = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted(SHIPPED_AGENTS_DIR.glob("*.md"))
    }
    assert file_map, "no shipped .claude/agents/*.md files found"
    findings = check_rank_conformance(file_map, ".md")
    assert findings == [], "shipped team not clean: " + "; ".join(
        f"{f.file}: {f.description}" for f in findings
    )


# --------------------------------------------------------------------------
# C-3 (2026-08-26): framework-aware dispatch — the rank check is honest about
# which frameworks it can and cannot check (no more silent Claude-only pass).
# --------------------------------------------------------------------------

from agentteams.rank_conformance import (  # noqa: E402
    agents_missing_capability_key,
    rank_check_not_checkable_notice,
    _parser_for,
)


def _copilot_agent(slug: str, tools: str) -> str:
    return f"---\nname: {slug}\ndescription: t\ntools: {tools}\n---\nBody.\n"


def test_copilot_vscode_overgrant_is_detected_not_skipped():
    # navigator (governance, no 'edit' in its ceiling, no override) declaring 'edit' exceeds its
    # rank; with framework-aware dispatch the copilot-vscode parser is used, so this is FLAGGED —
    # previously silently skipped (the Claude-only-parser enforcement-theater gap).
    fm = _copilot_agent("navigator", "['read', 'edit', 'search']")
    findings = check_rank_conformance({"navigator.agent.md": fm}, ".agent.md", "copilot-vscode")
    assert findings, "copilot-vscode over-grant must be detected, not skipped (C-3)"


def test_codex_framework_is_not_checkable_no_findings_and_a_notice():
    # An over-granting agent, but framework=codex has no tool-scope parser → no findings (can't
    # check) AND an explicit not-checkable notice (never a silent clean pass).
    fm = _agent("navigator", "Read, Edit, Grep")  # would be flagged under claude
    assert check_rank_conformance({"navigator.md": fm}, ".md", "codex") == []
    notice = rank_check_not_checkable_notice("codex", ".md")
    assert notice and "NOT CHECKABLE" in notice


def test_not_checkable_framework_is_exit_neutral_under_strict():
    # A not-checkable framework must NOT block a strict run — the gap is the tool's, not the agent's.
    findings = check_rank_conformance({"x.yaml": "recipe"}, ".yaml", "goose")
    assert findings == []
    assert disposition_exit_code(findings, strict=True) == 0


def test_not_checkable_agents_are_not_reported_keyless():
    # W19 must not falsely report every goose/codex agent as keyless (no parser ≠ no key).
    assert agents_missing_capability_key({"x.yaml": "recipe"}, ".yaml", "goose") == []


def test_checkable_framework_still_reports_genuinely_keyless():
    fm = "---\nname: x\ndescription: t\n---\nBody.\n"  # claude agent, no tools:
    assert agents_missing_capability_key({"x.md": fm}, ".md", "claude") == ["x"]


def test_backward_compat_no_framework_falls_back_to_ext_heuristic():
    # Existing callers pass no framework; .md must still parse as claude.
    fm = _agent("navigator", "Read, Edit, Grep")
    assert check_rank_conformance({"navigator.md": fm}, ".md") == check_rank_conformance(
        {"navigator.md": fm}, ".md", "claude"
    )
    assert _parser_for(None, ".md")[1] and _parser_for(None, ".agent.md")[1]
