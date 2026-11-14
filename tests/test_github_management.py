"""Agent-run GitHub management (brief ``git_workflow.agent_github_management``, on by default; 2026-11-14).

Pins: the option's default and validation; that ``false`` renders the earlier text byte-identically; that the
approval wording reaches every owned template's fenced region; that the security rows it must not touch survive;
and the code-side guards (directive denylist, constitutional-gate pattern).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agentteams import github_management as gm
from agentteams.cli.management_directives import scope_is_allowed
from agentteams.render import resolve_placeholders

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "agentteams" / "templates"
OWNED = {
    "universal/git-operations.template.md": "GITHUB_MGMT_GITOPS_RULE",
    "domain/pr-manager.template.md": "GITHUB_MGMT_PR_CLEARANCE",
    "universal/security.template.md": "GITHUB_MGMT_SECURITY_ROWS",
    "universal/integration-branch.reference.template.md": "GITHUB_MGMT_RELEASE_STEP",
}


@pytest.mark.parametrize("brief", [{}, {"git_workflow": {}}, {"git_workflow": {"branch_model": "integration"}},
                                   {"git_workflow": {"agent_github_management": True}}])
def test_on_by_default(brief):
    assert gm.enabled(brief)
    assert all(gm.placeholders(brief)[n] for n in gm.PLACEHOLDERS)


def test_false_turns_it_off():
    assert gm.placeholders({"git_workflow": {"agent_github_management": False}}) == gm.default_placeholders()


@pytest.mark.parametrize("block", [{"agent_github_management": "yes"}, {"agent_github_management": 1}, "on"])
def test_malformed_values_are_refused(block):
    with pytest.raises(ValueError):
        gm.enabled({"git_workflow": block})


def test_schema_declares_the_option_default_true():
    schema = json.loads((ROOT / "agentteams" / "schemas" / "project-description.schema.json").read_text())
    prop = schema["properties"]["git_workflow"]["properties"]["agent_github_management"]
    assert prop["type"] == "boolean" and prop["default"] is True


@pytest.mark.parametrize("template, name", OWNED.items())
def test_off_renders_the_template_without_the_placeholder(template, name):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    assert text.count("{" + name + "}") == 1 and re.search(r"[^\n]\{" + name + r"\}\n", text)
    rendered = resolve_placeholders(text, gm.default_placeholders())
    assert rendered == text.replace("{" + name + "}", "")


@pytest.mark.parametrize("template, name", OWNED.items())
def test_the_placeholder_sits_inside_a_fence(template, name):
    """``--update --merge`` replaces fenced regions, so the rule reaches existing teams only from inside one."""
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    if "AGENTTEAMS:BEGIN" not in text:               # emit wraps an unfenced agent file whole (content v=1)
        return
    before = text.split("{" + name + "}", 1)[0]
    assert before.rfind("AGENTTEAMS:BEGIN") > before.rfind("AGENTTEAMS:END")


def test_approval_wording_is_strict():
    text = gm.placeholders({})["GITHUB_MGMT_GITOPS_RULE"]
    for needle in ("live, in-session", "PR number and head SHA", "If the head moves, ask again",
                   "no standing or blanket approval", "signed management directive", "--match-head-commit <sha>",
                   "Never `--admin`", "--apply <fingerprint>", "never call the branch-protection API directly",
                   "release-required-checks:<fingerprint>", "Sandbox Operator Handoff"):
        assert needle in text, needle
    assert "main" not in re.sub(r"`[^`]*`", "", text).split()   # no hardcoded default branch


def test_pr_manager_still_never_merges():
    text = (TEMPLATES / "domain/pr-manager.template.md").read_text(encoding="utf-8")
    assert "2. **Never merge a PR.**" in text
    assert "You still never merge." in gm.placeholders({})["GITHUB_MGMT_PR_CLEARANCE"]


def test_security_rows_it_must_not_touch_survive_verbatim():
    """@security condition 4: vulnscan merges stay operator-run; branch deletion keeps its Ed25519 grant."""
    text = (TEMPLATES / "universal/security.template.md").read_text(encoding="utf-8")
    assert "and never merges. Merging stays with the operator, who runs the daily run's machine-local merge digest" in text
    assert "`--branch-post-merge … --apply` (runs under an operator Ed25519 `branch-delete` grant" in text


@pytest.mark.parametrize("scope", ["merge release PR 488", "switch required-checks on main",
                                   "update branch protection", "edit ruleset"])
def test_a_management_directive_can_never_authorize_these(scope):
    assert not scope_is_allowed(scope)


def _gate_pattern() -> str:
    src = (TEMPLATES / "universal/hooks/constitutional-gate.py").read_text(encoding="utf-8")
    m = re.search(r'\("branch-protection-write",\s*(r".*?")\s*\n\s*(r".*?"),', src, re.S)
    return eval(m.group(1)) + eval(m.group(2))  # noqa: S307 (two raw-string literals from our own template)


@pytest.mark.parametrize("cmd, gated", [
    ("gh api -X PATCH repos/o/r/branches/main/protection/required_status_checks --input -", True),
    ("gh api repos/o/r/rulesets --method POST", True),
    ("curl -X PUT https://api.github.com/repos/o/r/branches/main/protection", True),
    ("python3 -m agentteams.release_required_checks --pr 5 --apply 04f5a5b7daff", True),
    ("gh api repos/o/r/branches/main/protection/required_status_checks", False),
    ("python3 -m agentteams.release_required_checks --pr 5", False),
])
def test_the_gate_prompts_on_protection_writes(cmd, gated):
    assert bool(re.search(_gate_pattern(), cmd)) is gated


def test_update_prints_a_notice_only_the_first_time(tmp_path):
    """@security condition 2: the update output says what changed, once, when the rule first lands."""
    new = "x\n" + gm.placeholders({})["GITHUB_MGMT_GITOPS_RULE"]
    (tmp_path / "git-operations.agent.md").write_text("x\n", encoding="utf-8")
    assert "live approval naming the PR and head SHA" in gm.update_notice([("git-operations.agent.md", new)], tmp_path)
    (tmp_path / "git-operations.agent.md").write_text(new, encoding="utf-8")
    assert gm.update_notice([("git-operations.agent.md", new)], tmp_path) is None
    assert gm.update_notice([("new.agent.md", new)], tmp_path) is None          # a first render is not an update
