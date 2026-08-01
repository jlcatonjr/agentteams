"""Fencing a section a deployed team predates leaves two copies. Say so.

`_is_whole_body_migration` solved this for the case where the deployed file's *only* fence is the
whole-body `content` wrap: everything inside it is template-owned by definition, so replacing it
wholesale can lose nothing.

The partial case is the sibling it did not cover. A team generated when a template had three
fences, merged against a template that now has seven, gets the four new blocks *added* while its
own unfenced copies of those same sections are preserved unconditionally. Two copies, one stale.
Measured 2026-08-01 across this project's own `.claude/` team: 14 duplicated sections in 5 files,
four of them duplicated "⛔ Do not modify or omit" contracts — the same shape the whole-body
docstring calls "worse than not updating at all".

**Detection only, deliberately.** The whole-body case can be auto-resolved because the content
was provably template-owned. Here it never was: the section has never sat inside a fence, so a
project may have edited it believing it was theirs. Auto-replacing would be the same class of
silent out-of-fence destruction fixed in `fences.py` on the same day. Naming each collision lets
an operator look at it and decide.
"""

from __future__ import annotations

from agentteams.fences import _merge_fenced_content

_DEPLOYED = (
    "---\nname: A\n---\n\n"
    "<!-- AGENTTEAMS:BEGIN kept v=1 -->\n## Kept\n\nfenced already.\n<!-- AGENTTEAMS:END kept -->\n\n"
    "## Invariant Core\n\nthe stale unfenced copy.\n"
)

_RENDER = (
    "---\nname: A\n---\n\n"
    "<!-- AGENTTEAMS:BEGIN kept v=1 -->\n## Kept\n\nfenced already.\n<!-- AGENTTEAMS:END kept -->\n\n"
    "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n## Invariant Core\n\nthe template's copy.\n"
    "<!-- AGENTTEAMS:END invariant_core -->\n"
)


def test_the_collision_is_reported():
    result = _merge_fenced_content(_RENDER, _DEPLOYED)
    assert result.sections_added == ["invariant_core"]
    joined = " ".join(result.duplicate_section_notices)
    assert "## Invariant Core" in joined
    assert "invariant_core" in joined, "the notice must name the fence that caused the collision"


def test_both_copies_are_still_present_because_nothing_was_auto_resolved():
    """The notice is the whole remediation. Choosing for the operator is how content gets lost."""
    merged = _merge_fenced_content(_RENDER, _DEPLOYED).merged_content
    assert merged.count("## Invariant Core") == 2
    assert "the stale unfenced copy." in merged
    assert "the template's copy." in merged


def test_no_notice_when_nothing_was_added():
    """An ordinary merge that only replaces existing fences cannot create a duplicate."""
    result = _merge_fenced_content(_DEPLOYED, _DEPLOYED)
    assert not result.sections_added
    assert result.duplicate_section_notices == []


def test_no_notice_when_the_added_section_is_genuinely_new():
    """A fence with no unfenced counterpart on disk is the normal, quiet case."""
    render = _DEPLOYED.replace(
        "## Invariant Core\n\nthe stale unfenced copy.\n",
        "## Invariant Core\n\nthe stale unfenced copy.\n\n"
        "<!-- AGENTTEAMS:BEGIN brand_new v=1 -->\n## Brand New\n\nnovel.\n"
        "<!-- AGENTTEAMS:END brand_new -->\n",
    )
    result = _merge_fenced_content(render, _DEPLOYED)
    assert result.sections_added == ["brand_new"]
    assert result.duplicate_section_notices == []


def test_the_whole_body_migration_path_is_unaffected():
    """That case auto-resolves and must not also emit a collision notice for its own result."""
    deployed = "---\nname: A\n---\n\n<!-- AGENTTEAMS:BEGIN content v=1 -->\n## X\n\nold.\n<!-- AGENTTEAMS:END content -->\n"
    render = "---\nname: A\n---\n\n<!-- AGENTTEAMS:BEGIN x v=1 -->\n## X\n\nnew.\n<!-- AGENTTEAMS:END x -->\n"
    result = _merge_fenced_content(render, deployed)
    assert result.merged_content.count("## X") == 1
    assert result.duplicate_section_notices == []
