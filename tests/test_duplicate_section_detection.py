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

**Detection by default; resolution only on proof (2026-10-10).** The section has never sat inside a
fence, so a project may have edited it believing it was theirs. The merge on its own (no template
source, no backup dir: the defaults here) keeps both copies and names the collision.
`agentteams.fence_twins` drops a twin only when its text provably came from a template — see
`tests/test_fence_twins.py` — and its notice then replaces this one for that heading.

Detection is no longer gated on a fence being added: a file that already carries both copies is
reported on every merge, not only the first.
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
    """An ordinary merge that only replaces existing fences, with no unfenced twin, stays quiet."""
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
    result = _merge_fenced_content(render, deployed, twin_drop=True)  # a real run, with a backup dir
    assert result.merged_content.count("## X") == 1
    assert result.duplicate_section_notices == []


def test_an_already_affected_file_is_still_reported():
    """Once the fence was added, the old notice never fired again; the twin is now re-flagged."""
    merged = _merge_fenced_content(_RENDER, _DEPLOYED).merged_content
    again = _merge_fenced_content(_RENDER, merged)
    assert again.sections_added == []
    joined = " ".join(again.duplicate_section_notices)
    assert "## Invariant Core" in joined and "invariant_core" in joined
    assert again.merged_content.count("## Invariant Core") == 2, "detection alone deletes nothing"


def test_a_heading_repeated_only_in_unfenced_prose_is_quiet_without_an_add():
    """Without an add, only the inside-and-outside-a-fence residue is reported."""
    from agentteams.fences import _detect_duplicate_sections

    text = "## Notes\n\na\n\n## Notes\n\nb\n"
    assert _detect_duplicate_sections(text, []) == []
    assert _detect_duplicate_sections(text, ["x"]) != []
