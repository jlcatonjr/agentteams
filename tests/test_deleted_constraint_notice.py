"""A deleted constitutional rule is now reported. It was not, and the silence was structural.

Report §4.1: an unfenced rule can be deleted from a generated team and no `--update --merge` will
restore it. §4.2 is the half that makes §4.1 invisible: `_detect_unfenced_drift` returns nothing
unless the file is *unmodified since generation*, and a tampered file is modified by definition.
The one mechanism that would surface the deletion was switched off in exactly the case where a
deletion happened.

The fix is not to drop that guard. Reporting every prose divergence on every edited file is how a
notice gets muted — the guard's own docstring makes that argument and it is right. The trigger is
narrowed instead: report only when a **constraint-bearing** line from the template's unfenced
prose is missing from the deployed file.

`CONSTRAINT_BEARING_RE` and `unfenced_lines` both live in `fences` so this notice and
`test_fence_coverage_policy`'s ratchet cannot disagree about what a rule is or where to look for
one. Consolidating them surfaced a latent bug in both former copies: the front-matter walk
re-entered front-matter mode on a *three-line* block's closing `---`, so the walk yielded nothing
at all. Real templates have long front matter and never hit it; a minimal fixture does.
"""

from __future__ import annotations

from agentteams.fences import _merge_fenced_content

_TEMPLATE = (
    "---\nname: A\n---\n\n"
    "<!-- AGENTTEAMS:BEGIN core v=1 -->\n## Core\n\nfenced and self-healing.\n"
    "<!-- AGENTTEAMS:END core -->\n\n"
    "## Constitutional Rules\n\n"
    "1. **Security first** — destructive operations must never proceed without clearance.\n\n"
    "This section explains how routing works and which agent handles each content area.\n"
)
_RULE = "1. **Security first** — destructive operations must never proceed without clearance.\n"


def _notices(disk: str) -> list[str]:
    return _merge_fenced_content(_TEMPLATE, disk).deleted_constraint_notices


def test_a_deleted_rule_is_reported():
    """The §4.1 case. The file is modified, which is precisely when the old notice went quiet."""
    notices = _notices(_TEMPLATE.replace(_RULE, ""))
    assert notices, "a deleted constitutional rule must not vanish silently"
    assert "Security first" in notices[0]
    assert "preserved-forever" in notices[0], "the notice must say why a merge will not fix it"


def test_an_intact_file_is_silent():
    assert _notices(_TEMPLATE) == []


def test_ordinary_prose_edits_are_silent():
    """The property that keeps this worth reading. Non-rule prose is never tracked."""
    reworded = _TEMPLATE.replace(
        "This section explains how routing works and which agent handles each content area.",
        "Routing is described here, per content area and owning agent.",
    )
    assert _notices(reworded) == []


def test_a_project_appending_its_own_rule_is_silent():
    """Extension is the sanctioned case. This looks for template text going missing, never arriving."""
    assert _notices(_TEMPLATE + "\n2. **Project rule** — never deploy on a Friday.\n") == []


def test_only_unfenced_template_text_is_tracked():
    """Fenced content is restored by the merge, so its absence is self-healing and not a finding."""
    without_fenced = _TEMPLATE.replace(
        "<!-- AGENTTEAMS:BEGIN core v=1 -->\n## Core\n\nfenced and self-healing.\n"
        "<!-- AGENTTEAMS:END core -->\n\n",
        "",
    )
    assert _notices(without_fenced) == [], "a missing fenced region is not a deleted rule"


def test_many_deletions_report_a_count_rather_than_a_wall():
    template = _TEMPLATE + "".join(
        f"{n}. **Rule {n}** — this operation must never run unattended.\n" for n in range(2, 8)
    )
    notices = _merge_fenced_content(template, _TEMPLATE.replace(_RULE, "")).deleted_constraint_notices
    assert len(notices) <= 4, "a wholesale rewrite must not produce a wall of text"
    assert any("further constraint-bearing" in n for n in notices)


# ---------------------------------------------------------------------------
# The numbered constitutional rules must be tracked by SHAPE, not vocabulary
#
# `_detect_deleted_constraints` exists precisely because `_detect_unfenced_drift` goes
# silent on modified files, and a tampered file is modified by definition. But its
# keyword set — ⛔ | read-only | PRIORITY LEVEL | HALT | MUST NOT | never — matched only
# 2 of the orchestrator's 17 constitutional rules. The 15 it missed include:
#
#   1. `@security` before destructive operations
#   2. `@code-hygiene` before merging code
#  11. Cross-repository writes require `@repo-liaison` + `@security`
#
# Rule 1 is the rule the entire enforced stack rests on. It says "require", not "never",
# so deleting it was neither restored (unfenced by design) nor reported (not tracked) —
# the exact §4.1 + §4.2 compound the security review describes.
#
# Tracking by SHAPE rather than by adding words: a numbered, bolded rule in the
# Constitutional Rules list is structurally identifiable, and its disappearance is
# unambiguous. Broadening the vocabulary instead would have traded a false-negative for
# false-positives on ordinary prose, which is what keeps this notice readable.
# ---------------------------------------------------------------------------


def test_deleting_a_numbered_constitutional_rule_is_reported():
    """Rule 1 is the case that matters, and it carries none of the keywords."""
    from agentteams.fences import _detect_deleted_constraints

    template = (
        "# Orchestrator\n\n"
        "### Constitutional Rules (Non-Negotiable)\n\n"
        "1. **`@security` before destructive operations** — File deletions, bulk edits, "
        "and any irreversible action require `@security` clearance first.\n"
        "2. **`@code-hygiene` before merging code** — Any code change session adding files "
        "must pass a hygiene audit before merge.\n"
    )
    tampered = (
        "# Orchestrator\n\n"
        "### Constitutional Rules (Non-Negotiable)\n\n"
        "2. **`@code-hygiene` before merging code** — Any code change session adding files "
        "must pass a hygiene audit before merge.\n"
        "\nan unrelated operator edit, so the file is modified\n"
    )
    notices = _detect_deleted_constraints(template, tampered)
    assert notices, (
        "deleting Constitutional Rule 1 produced no notice. It is unfenced by design, so "
        "nothing restores it; if nothing reports it either, the deletion is invisible."
    )
    assert any("security" in n for n in notices), f"notice does not name the rule: {notices}"


def test_rewording_ordinary_prose_stays_silent():
    """The property that keeps the notice worth reading."""
    from agentteams.fences import _detect_deleted_constraints

    template = (
        "# Orchestrator\n\n"
        "## Overview\n\nThis agent coordinates the team and routes work to specialists.\n"
    )
    reworded = (
        "# Orchestrator\n\n"
        "## Overview\n\nThis agent coordinates the team, routing work to the specialists.\n"
    )
    assert not _detect_deleted_constraints(template, reworded), (
        "ordinary prose rewording fired the notice — that is how a notice gets muted"
    )
