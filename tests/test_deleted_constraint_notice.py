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
