"""A new fenced section must land where the render puts it, not at the end of the file.

**The defect.** Every section present in the fresh render but absent on disk was appended with
``merged.rstrip("\\n") + "\\n\\n" + block`` — the absolute end of the file, regardless of its
position in the render. A template author adding a gate step meant to run *before* an existing
instruction therefore got correct placement on a fresh build and a silently inverted execution
order on ``--update --merge``.

**Why it is the keystone for updatability.** Measured 2026-07-31, 723 lines of template-owned
prose (``Invariant Core``, ``Workflow``, ``Trigger Conditions``) sit outside any fence across this
project's own 34 agent files, and therefore never update. The remedy is to fence them — but
``fence_inject`` no-ops on any already-fenced file, so the only route into a deployed team is a
template gaining a fence and that fence merging into the right place. Until this worked, fencing
the templates would have helped only teams generated from scratch.
"""

from __future__ import annotations

import re

from agentteams.fences import _insert_section_at_render_position, _merge_fenced_content


def _fence(sid: str, body: str) -> str:
    return f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{body}\n<!-- AGENTTEAMS:END {sid} -->\n"


def _order(text: str) -> list[str]:
    return re.findall(r"AGENTTEAMS:BEGIN (\w+)", text)


# --- placement ------------------------------------------------------------

def test_a_new_middle_section_lands_in_the_middle():
    """The motivating case: a gate step that must run BEFORE an existing instruction."""
    on_disk = "---\nname: A\n---\n\n" + _fence("intro", "i") + "\n" + _fence("handoff", "h")
    rendered = ("---\nname: A\n---\n\n" + _fence("intro", "i") + "\n"
                + _fence("gate", "g") + "\n" + _fence("handoff", "h"))

    result = _merge_fenced_content(rendered, on_disk)

    assert _order(result.merged_content) == ["intro", "gate", "handoff"]
    assert "gate" in result.sections_added


def test_a_new_first_section_lands_first():
    on_disk = "---\nname: A\n---\n\n" + _fence("body", "b")
    rendered = "---\nname: A\n---\n\n" + _fence("preamble", "p") + "\n" + _fence("body", "b")

    merged = _merge_fenced_content(rendered, on_disk).merged_content

    assert _order(merged) == ["preamble", "body"]
    assert merged.index("preamble") < merged.index("BEGIN body")


def test_a_new_last_section_still_lands_last():
    on_disk = "---\nname: A\n---\n\n" + _fence("intro", "i")
    rendered = "---\nname: A\n---\n\n" + _fence("intro", "i") + "\n" + _fence("outro", "o")

    assert _order(_merge_fenced_content(rendered, on_disk).merged_content) == ["intro", "outro"]


def test_several_new_sections_all_land_in_render_order():
    on_disk = "---\nname: A\n---\n\n" + _fence("a", "1") + "\n" + _fence("d", "4")
    rendered = ("---\nname: A\n---\n\n" + _fence("a", "1") + "\n" + _fence("b", "2") + "\n"
                + _fence("c", "3") + "\n" + _fence("d", "4"))

    assert _order(_merge_fenced_content(rendered, on_disk).merged_content) == ["a", "b", "c", "d"]


# --- what must NOT change -------------------------------------------------

def test_unfenced_content_between_sections_is_preserved_in_place():
    """Insertion must splice, never rewrite the surrounding prose."""
    on_disk = ("---\nname: A\n---\n\n" + _fence("intro", "i")
               + "\nUSER PROSE THE PROJECT WROTE\n\n" + _fence("handoff", "h"))
    rendered = ("---\nname: A\n---\n\n" + _fence("intro", "i") + "\n"
                + _fence("gate", "g") + "\n" + _fence("handoff", "h"))

    merged = _merge_fenced_content(rendered, on_disk).merged_content

    assert "USER PROSE THE PROJECT WROTE" in merged
    assert _order(merged) == ["intro", "gate", "handoff"]


def test_existing_sections_are_never_reordered():
    """A project that deliberately arranged its file keeps that arrangement."""
    on_disk = "---\nname: A\n---\n\n" + _fence("handoff", "h") + "\n" + _fence("intro", "i")
    rendered = ("---\nname: A\n---\n\n" + _fence("intro", "i") + "\n"
                + _fence("gate", "g") + "\n" + _fence("handoff", "h"))

    order = _order(_merge_fenced_content(rendered, on_disk).merged_content)

    assert order.index("handoff") < order.index("intro"), "existing order must survive"
    assert "gate" in order


# --- fallbacks ------------------------------------------------------------

def test_no_anchor_appends_and_says_so():
    """Last resort must be visible, not silent — that silence was the original defect."""
    merged, notice = _insert_section_at_render_position(
        "---\nname: A\n---\n\nplain prose, no fences at all\n",
        "solo", _fence("solo", "s"), ["solo"],
    )
    assert "BEGIN solo" in merged
    assert notice and "appended at end of file" in notice


def test_a_section_absent_from_the_render_order_still_appends():
    merged, notice = _insert_section_at_render_position(
        "---\nname: A\n---\n\n" + _fence("a", "1"), "x", _fence("x", "9"), ["a"],
    )
    assert _order(merged) == ["a", "x"]
    assert notice is None


def test_insertion_after_a_predecessor_beats_insertion_before_a_successor():
    """Preference order matters: anchoring to what comes before is the stabler choice."""
    on_disk = "---\nname: A\n---\n\n" + _fence("a", "1") + "\n" + _fence("c", "3")
    merged, _ = _insert_section_at_render_position(
        on_disk, "b", _fence("b", "2"), ["a", "b", "c"]
    )
    assert _order(merged) == ["a", "b", "c"]
    assert merged.index("BEGIN b") > merged.index("END a")


def test_the_inserted_block_keeps_its_markers_intact():
    merged, _ = _insert_section_at_render_position(
        "---\nname: A\n---\n\n" + _fence("a", "1"), "b", _fence("b", "2"), ["a", "b"]
    )
    assert "<!-- AGENTTEAMS:BEGIN b v=1 -->" in merged
    assert "<!-- AGENTTEAMS:END b -->" in merged
    assert merged.count("BEGIN b") == 1
