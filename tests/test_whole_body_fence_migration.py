"""Fencing a previously-unfenced template must not duplicate its sections.

**The real blocker behind ~19 templates, and it was mislogged.** A template with no fences gets
its whole body wrapped in a single `content` fence at emit time. The moment that template gains a
named section the render stops being wrapped — so a team generated *before* the split has
`{content}` on disk while the render has `{invariant_core, ...}`.

Merging those naively appended the named section *alongside* the stale `content` block. Measured
2026-07-31, the result was an agent file carrying **two contradictory copies** of its
"⛔ Do not modify or omit" contract — worse than not updating at all.

The reason recorded at the time was "nesting is illegal". That was wrong: the nesting error came
from a boundary bug in the fencing pass (a section's END marker landing inside the following
fence), which was fixed separately. Adding a fence actually *suppresses* the wrapper, so nesting
never occurs — the duplication does.

**Why wholesale replacement is safe**, and not a shortcut: everything inside a `content` fence is
template-owned by definition and already overwritten on every merge, so nothing a project authored
can live there. Content *outside* it is untouched, exactly as in an ordinary merge.

That last sentence was the stated invariant from the start and the code did not honour it. Taking
`new_rendered` verbatim also took the *render's* out-of-fence tail, so an operator's
`## Project-Specific Notes` — the one region emit advertises as "preserved verbatim across
`agentteams --update --merge`" — was silently replaced by the render's empty boilerplate. It only
bites files whose template goes from fenceless to fenced, which is precisely the migration this
module exists for, and precisely the sweep that would have run it across a fleet. Fixed by
splitting both sides at the final END marker and keeping the disk's tail; guarded below.
"""

from __future__ import annotations

from agentteams.emit import _normalize_generated_content
from agentteams.fences import (
    _extract_fenced_regions,
    _is_whole_body_migration,
    _merge_fenced_content,
)

_UNFENCED = "---\nname: A\n---\n\n## Invariant Core\n\nOLD text.\n\n## Other\n\nkeep me\n"
_FENCED = (
    "---\nname: A\n---\n\n"
    "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
    "## Invariant Core\n\nNEW text.\n"
    "<!-- AGENTTEAMS:END invariant_core -->\n\n"
    "## Other\n\nkeep me\n"
)


def _deployed() -> str:
    """What a team generated BEFORE its template was fenced looks like on disk."""
    return _normalize_generated_content("a.agent.md", _UNFENCED)


def _render() -> str:
    return _normalize_generated_content("a.agent.md", _FENCED)


# --- the wrapper's actual behaviour ----------------------------------------

def test_an_unfenced_template_gets_the_whole_body_wrapper():
    assert "AGENTTEAMS:BEGIN content" in _deployed()


def test_adding_a_fence_suppresses_the_wrapper():
    """This is why nesting never actually occurs — the originally-logged reason was wrong."""
    assert "AGENTTEAMS:BEGIN content" not in _render()
    assert list(_extract_fenced_regions(_render())) == ["invariant_core"]


# --- the migration ---------------------------------------------------------

def test_the_section_is_not_duplicated():
    """The defect itself: two copies of the same section, one stale."""
    merged = _merge_fenced_content(_render(), _deployed()).merged_content
    assert merged.count("## Invariant Core") == 1


def test_the_template_text_wins_and_the_stale_copy_goes():
    merged = _merge_fenced_content(_render(), _deployed()).merged_content
    assert "NEW text" in merged
    assert "OLD text" not in merged
    assert "AGENTTEAMS:BEGIN content" not in merged


def test_body_content_outside_the_new_fence_survives():
    """`## Other` lived inside the old wrapper and is not yet fenced — it must not vanish."""
    merged = _merge_fenced_content(_render(), _deployed()).merged_content
    assert "## Other" in merged and "keep me" in merged


def _deployed_with_operator_notes() -> str:
    """A deployed file where the operator has written in the USER-EDITABLE region.

    The region sits *after* the whole-body wrapper's END marker, which is exactly the ground the
    migration used to overwrite.
    """
    return (
        _deployed().rstrip("\n")
        + "\n\n## Project-Specific Notes\n\n- Never delete anything under `vendor/`.\n"
    )


def test_operator_content_outside_the_wrapper_survives():
    """The regression: out-of-fence content is the project's, and a structural rewrite is still a merge.

    `## Other` (above) survives only because the render happens to carry it. This is the case the
    render does *not* carry — the operator's own text — and it must survive on the merge rule, not
    on a coincidence.
    """
    merged = _merge_fenced_content(_render(), _deployed_with_operator_notes()).merged_content
    assert "Never delete anything under" in merged
    assert merged.count("## Project-Specific Notes") == 1
    assert "NEW text" in merged, "the template update must still land"


def test_the_migration_is_reported_not_silent():
    """A structural rewrite the operator did not ask for must say so."""
    result = _merge_fenced_content(_render(), _deployed())
    notice = " ".join(result.shrink_notices)
    assert "predates its template being split" in notice
    assert "template-owned" in notice, "the notice must say why the replacement was safe"
    assert result.sections_orphaned == ["content"]
    assert result.sections_added == ["invariant_core"]


# --- it must not fire on anything else -------------------------------------

def test_an_ordinary_merge_is_untouched():
    """Both sides already fenced with named sections — normal path, no migration."""
    on_disk = _FENCED.replace("NEW text", "PROJECT text")
    result = _merge_fenced_content(_render(), on_disk)
    assert result.sections_orphaned == []
    assert "NEW text" in result.merged_content


def test_a_file_still_wrapped_on_both_sides_is_not_migrated():
    """Template still unfenced ⇒ both sides have `content` ⇒ ordinary fenced merge."""
    result = _merge_fenced_content(_deployed(), _deployed())
    assert result.sections_orphaned == []


def test_the_predicate_requires_content_alone_on_disk():
    """A file with `content` PLUS a named fence is not a pre-split file."""
    assert _is_whole_body_migration({"content": ""}, {"invariant_core": ""}) is True
    assert _is_whole_body_migration({"content": "", "other": ""}, {"invariant_core": ""}) is False
    assert _is_whole_body_migration({"content": ""}, {"content": ""}) is False
    assert _is_whole_body_migration({"content": ""}, {}) is False
    assert _is_whole_body_migration({"invariant_core": ""}, {"invariant_core": ""}) is False


# ---------------------------------------------------------------------------
# A file that cannot be parsed must not be wrapped
#
# `_extract_fenced_regions` returns `dict[str, str] | str`, where a str is an ERROR
# message. The guard tested only `isinstance(dict)`, so a parse failure fell through to
# the whole-body wrap — compounding the fault and renaming it. That is why the
# conflict-auditor render truncation surfaced as "Nested fence not allowed" about a
# template containing no nesting: the wrap put the file's own fences inside `content`,
# two steps downstream of the real defect.
# ---------------------------------------------------------------------------


def test_malformed_content_is_not_wrapped(recwarn):
    """A parse error passes through untouched, and says why."""
    from agentteams.fences import _extract_fenced_regions

    # Unbalanced: an opened fence that never closes — the shape the truncated render had.
    malformed = (
        "---\nname: a\n---\n\n"
        "<!-- AGENTTEAMS:BEGIN one v=1 -->\nbody\n<!-- AGENTTEAMS:END one -->\n\n"
        "<!-- AGENTTEAMS:BEGIN two v=1 -->\ndangling\n"
    )
    assert isinstance(_extract_fenced_regions(malformed), str), (
        "fixture must be malformed, or this test proves nothing"
    )

    out = _normalize_generated_content("a.agent.md", malformed)
    assert out == malformed, (
        "malformed content was rewritten; wrapping a file whose fences are already "
        "broken compounds the fault and reports it as a different error"
    )
    assert "AGENTTEAMS:BEGIN content" not in out, "the whole-body wrap was applied anyway"

    messages = [str(w.message) for w in recwarn]
    assert any("two" in m or "END" in m or "fence" in m.lower() for m in messages), (
        f"the real parse error was not surfaced; warnings were {messages}"
    )


def test_wellformed_paths_are_unaffected():
    """The two legitimate cases keep their behaviour."""
    bare = "# Title\n\nbody\n"
    assert _normalize_generated_content("a.md", bare).startswith("<!-- AGENTTEAMS:BEGIN content")

    fenced = "<!-- AGENTTEAMS:BEGIN x v=1 -->\nbody\n<!-- AGENTTEAMS:END x -->\n"
    assert _normalize_generated_content("a.md", fenced) == fenced
