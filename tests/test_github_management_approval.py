"""What counts as the operator's approval for actions that guard the default branch (rows 811/816; agentteams-2a).

The GitHub management section (``github_management``, on by default) names the approval; three placeholders carry it
into fenced regions of ``@pr-manager``, ``@security`` and the integration-branch reference. Pins: off renders those
regions unchanged; the wording is strict; the security rows that must not move survive; code-side guards hold.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams import github_management as gm
from agentteams.cli.management_directives import scope_is_allowed
from agentteams.render import resolve_placeholders

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "agentteams" / "templates"
OWNED = {
    "domain/pr-manager.template.md": gm.PR_CLEARANCE,
    "universal/security.template.md": gm.SECURITY_ROWS,
    "universal/integration-branch.reference.template.md": gm.RELEASE_STEP,
}
ROWS = (gm.PR_CLEARANCE, gm.SECURITY_ROWS, gm.RELEASE_STEP)


def test_on_by_default_and_off_blanks_the_rows():
    assert all(gm.placeholders({})[n] for n in ROWS)
    assert all(gm.placeholders({"github_management": False})[n] == "" for n in ROWS)


@pytest.mark.parametrize("template, name", OWNED.items())
def test_off_renders_the_template_without_the_placeholder(template, name):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    assert text.count("{" + name + "}") == 1 and re.search(r"[^\n]\{" + name + r"\}\n", text)
    off = gm.placeholders({"github_management": False})
    assert resolve_placeholders(text, {name: off[name]}) == text.replace("{" + name + "}", "")


@pytest.mark.parametrize("template, name", OWNED.items())
def test_the_placeholder_sits_inside_a_fence(template, name):
    """``--update --merge`` replaces fenced regions, so the rule reaches existing teams only from inside one."""
    before = (TEMPLATES / template).read_text(encoding="utf-8").split("{" + name + "}", 1)[0]
    assert before.rfind("AGENTTEAMS:BEGIN") > before.rfind("AGENTTEAMS:END")


def test_no_new_constraint_lines_in_git_operations():
    """The rule lives in fenced sections only (test_unfenced_constraint_ratchet measures template source)."""
    text = (TEMPLATES / "universal/git-operations.template.md").read_text(encoding="utf-8")
    assert "GITHUB_MGMT" not in text


def test_approval_wording_is_strict():
    raw = gm.placeholders({})[gm.PLACEHOLDER]
    text = " ".join(raw.split())                 # wording, not line wrapping
    for needle in ("live, in-session", "PR number and head SHA", "If the head moves, ask again",
                   "no standing or blanket approval", "signed management directive", "--match-head-commit <sha>",
                   "at least one check run has registered", "a local merge pushed there", "`--admin`", "--apply <fingerprint>",
                   "never call the branch-protection or ruleset API directly", "release-required-checks:<fingerprint>",
                   "never reaches these branches"):
        assert needle in text, needle
    assert gm.MARKER in raw


def test_pr_manager_still_never_merges():
    text = (TEMPLATES / "domain/pr-manager.template.md").read_text(encoding="utf-8")
    assert "2. **Never merge a PR.**" in text
    assert gm.placeholders({})[gm.PR_CLEARANCE].endswith("You still never merge.")


def test_security_rows_it_must_not_touch_survive_verbatim():
    """@security condition 4 (row 811): vulnscan merges stay operator-run; branch deletion keeps its Ed25519 grant."""
    text = (TEMPLATES / "universal/security.template.md").read_text(encoding="utf-8")
    assert "and never merges. Merging stays with the operator, who runs the daily run's machine-local merge digest" in text
    assert "`--branch-post-merge … --apply` (runs under an operator Ed25519 `branch-delete` grant" in text


@pytest.mark.parametrize("scope", ["merge release PR 488", "switch required-checks on main",
                                   "update branch protection", "edit ruleset"])
def test_a_management_directive_can_never_authorize_these(scope):
    assert not scope_is_allowed(scope)


def _gate_pattern() -> str:
    import ast

    tree = ast.parse((TEMPLATES / "universal/hooks/constitutional-gate.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Tuple) and node.elts and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "branch-protection-write"):
            return node.elts[1].value
    raise AssertionError("branch-protection-write category missing")


@pytest.mark.parametrize("cmd, gated", [
    ("gh api -X PATCH repos/o/r/branches/main/protection/required_status_checks --input -", True),
    ("gh api repos/o/r/rulesets --method POST", True),
    ("gh api repos/o/r/rulesets -f name=x", True),                       # a body makes gh POST without -X
    ("curl -X PUT https://api.github.com/repos/o/r/branches/main/protection", True),
    ("gh api graphql -f query='mutation{updateBranchProtectionRule(input:{})}'", True),
    ("gh api graphql -f query='mutation{deleteRepositoryRuleset(input:{})}'", True),
    ("python3 -m agentteams.release_required_checks --pr 5 --apply 04f5a5b7daff", True),
    ("python3 -m agentteams.release_required_checks --pr 5 --ap 04f5a5b7daff", True),
    ("gh api repos/o/r/branches/main/protection/required_status_checks", False),
    ("gh api repos/o/r/branches/main/protection", False),
    ("python3 -m agentteams.release_required_checks --pr 5", False),
])
def test_the_gate_prompts_on_protection_writes(cmd, gated):
    assert bool(re.search(_gate_pattern(), cmd)) is gated


def test_the_gate_pattern_stays_fast_on_a_worst_case_line():
    """The hook caps a line at 8192 characters; this pattern must not backtrack badly on one (cf. the pr-merge 50 s)."""
    import time

    pat = re.compile(_gate_pattern())
    for line in ("gh api " + " -X" * 2730, "gh api " + "/protection" * 744, "curl " + " --request=" * 818):
        start = time.perf_counter()
        pat.search(line[:8192])
        assert time.perf_counter() - start < 0.5, line[:40]


def test_argparse_never_accepts_an_abbreviated_apply(capsys):
    from agentteams import release_required_checks as rrc

    with pytest.raises(SystemExit):
        rrc.run(["--repo", "o/r", "--pr", "5", "--appl", "abc"])


@pytest.mark.parametrize("scope", ["run release_required_checks", "required checks switch", "Branch_Protection"])
def test_the_denylist_folds_underscores_and_spaces(scope):
    assert not scope_is_allowed(scope)


def test_benign_scopes_still_pass():
    assert scope_is_allowed("draft-weekly-report")


def test_update_prints_a_notice_only_the_first_time(tmp_path):
    new = "x\n" + gm.placeholders({})[gm.PLACEHOLDER]
    (tmp_path / "CLAUDE.md").write_text("x\n", encoding="utf-8")
    assert "live approval naming the PR and head SHA" in gm.update_notice([("CLAUDE.md", new)], tmp_path)
    (tmp_path / "CLAUDE.md").write_text(new, encoding="utf-8")
    assert gm.update_notice([("CLAUDE.md", new)], tmp_path) is None
    assert gm.update_notice([("new.md", new)], tmp_path) is None
