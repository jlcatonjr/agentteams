"""The constitution's GitHub management section (operator request, 2026-11-14): on by default, allocated by role,
never consent to a gate prompt, and no capability widened."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import analyze, github_management as gm

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "examples" / "software-project" / "brief.json"
TEMPLATES = ROOT / "agentteams" / "templates"


def _manifest(**extra) -> dict:
    description = {**json.loads(BRIEF.read_text(encoding="utf-8")), **extra}
    return analyze.build_manifest(description, framework="claude")


def test_the_section_is_on_by_default() -> None:
    assert gm.enabled({}) is True
    body = _manifest()["auto_resolved_placeholders"][gm.PLACEHOLDER]
    assert body == gm.section_body({})
    assert "authorized for this team by default" in body


def test_false_renders_the_off_notice() -> None:
    body = _manifest(github_management=False)["auto_resolved_placeholders"][gm.PLACEHOLDER]
    assert "not authorized by default" in body and "goes to the operator first" in body


def test_the_section_allocates_by_role_and_keeps_the_gate() -> None:
    body = gm.section_body({})
    assert "`@git-operations` runs git and the `gh` writes" in body
    assert "read-only `gh` queries itself" in body
    assert "never merges" in body
    assert "never answers or pre-approves a constitutional-gate prompt" in body
    assert "is not\n  operator consent" in body or "is not operator consent" in " ".join(body.split())
    for floor in ("force-push", "release/*", "signing", "cross-repository"):
        assert floor in body


def test_no_tools_line_widens() -> None:
    """Part A changes no capability: the git, orchestrator and PR agents keep their declared tools."""
    expected = {
        "universal/git-operations.template.md": "tools: ['read', 'execute', 'search']",
        "domain/pr-manager.template.md": "tools: ['read', 'search', 'execute']",
        "domain/pr-notifier.template.md": "tools: ['read', 'search']",
    }
    for rel, line in expected.items():
        text = (TEMPLATES / rel).read_text(encoding="utf-8")
        tools = re.search(r"^tools:.*$", text, re.MULTILINE).group(0)
        assert tools.replace('"', "'").replace("','", "', '") == line, rel


@pytest.mark.parametrize("fence", ["github_management_rule", "dev_merge_rule"])
def test_both_sections_are_fenced_template_authoritative_and_listed(fence) -> None:
    from agentteams.fences import _TEMPLATE_AUTHORITATIVE_FENCES

    text = (TEMPLATES / "copilot-instructions.template.md").read_text(encoding="utf-8")
    assert f"<!-- AGENTTEAMS:BEGIN {fence} v=1 -->" in text and f"<!-- AGENTTEAMS:END {fence} -->" in text
    assert fence in _TEMPLATE_AUTHORITATIVE_FENCES
    assert re.search(rf"^\| {fence}\s*\|", text, re.MULTILINE)


def test_the_git_agent_prefers_gh_when_authenticated() -> None:
    text = (TEMPLATES / "universal" / "git-operations.template.md").read_text(encoding="utf-8")
    assert "Use the `gh` CLI when it is installed and authenticated" in text
    assert "treat `gh` only as an optional fallback" not in text
    merge_ref = (TEMPLATES / "universal" / "github-workflows-merge.reference.template.md").read_text(encoding="utf-8")
    assert "agentteams --merge-pr <N>" in merge_ref and "## Pull-Request Merges" in merge_ref


@pytest.mark.parametrize("value", ["false", 0, None, "true"])
def test_a_non_boolean_flag_is_refused(value) -> None:
    with pytest.raises(ValueError):
        gm.enabled({"github_management": value})
