"""Tests for the AP-2 rank-conformance validator (agentteams.rank_conformance).

Mirrors tests/test_agent_tool_scopes.py in intent: the shipped team is the
regression guard against a false-positive wall — every live .claude/agents/*.md
file MUST be clean under the seeded policy. Synthetic cases exercise the
finding rule (over-grant flagged, override cleared, workstream ceiling).
"""

from __future__ import annotations

from pathlib import Path

from agentteams.rank_conformance import check_rank_conformance, rank_for

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
