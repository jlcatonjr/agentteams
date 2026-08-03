"""test_span_bounds_at_first_fence.py — a section ends where the managed content begins.

`_unfenced_section_span` ends a section at the next heading of the same-or-higher level. When
the fenced twin sits *below* the unfenced one, that next heading is the twin's own heading
**inside the fence**, so the span swallows every fence in between and the enclosure guard
refuses. In this repository's team that refused 8 collisions whose files already carried every
fence their render produces — while telling the operator to *"run `--update --merge` first"*,
an operation that would have changed nothing and which has twice destroyed content.

`security.md` is the shape:

    30  ## Invariant Core                   <- unfenced, ends at line 33
    34  <!-- BEGIN security_authority -->    ┐ enclosed by the span,
    46  <!-- BEGIN invariant_core v=2 -->    ┘ so the removal is refused
    47  ## Invariant Core                    <- the survivor

Bounding at the **first fence BEGIN after the heading** gives the true extent, and the removed
span then contains no fence by construction.

**Two independent guards, not one.** The bound alone is unsafe: a section that legitimately
continues past a fence would be truncated, and a distant fence would let the span swallow whole
sibling sections. So the span must also equal the deployed fence body exactly, and must contain
no heading of the same-or-higher level. Either failing means refusal.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "resolve_fence_collisions", REPO_ROOT / "scripts/resolve_fence_collisions.py"
)
assert _spec and _spec.loader
rfc = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("resolve_fence_collisions", rfc)
_spec.loader.exec_module(rfc)

CORE = "## Invariant Core\n\n> ⛔ **Do not modify or omit.** The immutable contract.\n"

FRESH = (
    "---\nname: A\ndescription: x\n---\n\n"
    "<!-- AGENTTEAMS:BEGIN authority v=1 -->\n"
    "Authority text.\n"
    "<!-- AGENTTEAMS:END authority -->\n\n"
    "<!-- AGENTTEAMS:BEGIN invariant_core v=2 -->\n"
    f"{CORE}"
    "<!-- AGENTTEAMS:END invariant_core -->\n"
)


def _deployed(*, tail: str = "") -> str:
    """The real shape: unfenced twin ABOVE, with a fence between it and its survivor."""
    return (
        "---\nname: A\ndescription: x\n---\n\n"
        f"{CORE}{tail}\n"
        "<!-- AGENTTEAMS:BEGIN authority v=1 -->\n"
        "Authority text.\n"
        "<!-- AGENTTEAMS:END authority -->\n\n"
        "<!-- AGENTTEAMS:BEGIN invariant_core v=2 -->\n"
        f"{CORE}"
        "<!-- AGENTTEAMS:END invariant_core -->\n\n"
        "## Project-Specific Notes\n\n- operator content that must survive.\n"
    )


def _run(tmp_path: Path, text: str):
    f = tmp_path / "security.md"
    f.write_text(text, encoding="utf-8")
    return rfc._resolve_file(f, FRESH)


def test_a_fence_between_the_twins_no_longer_blocks_the_removal(tmp_path: Path) -> None:
    """The 8. The section ends at the fence, not at its own twin's heading below it."""
    new_text, resolved, skipped = _run(tmp_path, _deployed())

    assert resolved, f"still refused: {skipped}"
    assert new_text is not None
    assert new_text.count("## Invariant Core") == 1, "exactly the fenced copy survives"
    assert "<!-- AGENTTEAMS:BEGIN invariant_core" in new_text
    assert "<!-- AGENTTEAMS:BEGIN authority" in new_text, "the intervening fence was destroyed"
    assert "Authority text." in new_text
    assert "operator content that must survive" in new_text


def test_a_section_that_continues_past_a_fence_is_refused(tmp_path: Path) -> None:
    """Audit finding 1. Bounding at the first fence would truncate this one.

    The equality test is what catches it: the bounded span is only part of the section, so it
    cannot equal the fence body.
    """
    text = _deployed(tail="\nMore of the section, written after the fence begins.\n")
    # Put prose AFTER the intervening fence that belongs to the unfenced section.
    text = text.replace(
        "<!-- AGENTTEAMS:END authority -->\n\n",
        "<!-- AGENTTEAMS:END authority -->\n\nStill part of the invariant core section.\n\n",
    )
    _new, resolved, skipped = _run(tmp_path, text)
    assert not resolved, f"truncated a section that continues past a fence: {resolved}"
    assert skipped


def test_a_sibling_section_between_the_twins_survives(tmp_path: Path) -> None:
    """A sibling heading between the copies must not be swallowed.

    This resolves through the ordinary path rather than the new bound —
    `_unfenced_section_span` already ends the section at the sibling heading, so no fence is
    enclosed and the re-bound never runs. Asserting a refusal here would have been asserting
    the wrong mechanism: what matters is that the sibling survives, not which branch ran.
    """
    text = (
        "---\nname: A\ndescription: x\n---\n\n"
        f"{CORE}\n"
        "## An Unrelated Sibling Section\n\nIts content.\n\n"
        "<!-- AGENTTEAMS:BEGIN invariant_core v=2 -->\n"
        f"{CORE}"
        "<!-- AGENTTEAMS:END invariant_core -->\n\n"
        "## Project-Specific Notes\n\nMine.\n"
    )
    new_text, resolved, _skipped = _run(tmp_path, text)
    assert resolved, "an ordinary duplicate above a sibling section stopped resolving"
    assert new_text is not None
    assert "## An Unrelated Sibling Section" in new_text, "a sibling section was swallowed"
    assert "Its content." in new_text
    assert new_text.count("## Invariant Core") == 1


def test_the_bound_refuses_a_span_containing_a_sibling_heading() -> None:
    """Audit finding 2, tested directly on the helper.

    Defence in depth: a section cannot contain a heading of its own level or higher, so a
    distant first fence must not let the span swallow one. Tested at the unit rather than
    through `_resolve_file`, because the ordinary span bound stops at a sibling first — the
    guard is a floor under a path that is currently hard to reach, not a reachable branch.
    Claiming otherwise via an integration test would be asserting a mechanism that does not run.
    """
    text = (
        "## Invariant Core\n\nbody\n\n"
        "## A Sibling\n\nmore\n\n"
        "<!-- AGENTTEAMS:BEGIN x v=1 -->\nfenced\n<!-- AGENTTEAMS:END x -->\n"
    )
    assert rfc._span_bounded_at_first_fence(text, "## Invariant Core", 0) is None

    without_sibling = (
        "## Invariant Core\n\nbody\n\n"
        "<!-- AGENTTEAMS:BEGIN x v=1 -->\nfenced\n<!-- AGENTTEAMS:END x -->\n"
    )
    span = rfc._span_bounded_at_first_fence(without_sibling, "## Invariant Core", 0)
    assert span is not None, "the guard also rejected the case it is supposed to allow"
    assert "AGENTTEAMS" not in without_sibling[span[0]:span[1]], "the span reached the fence"


def test_the_bound_returns_none_when_no_fence_follows() -> None:
    text = "## Invariant Core\n\nbody, and nothing managed after it.\n"
    assert rfc._span_bounded_at_first_fence(text, "## Invariant Core", 0) is None


def test_a_differing_copy_is_still_refused(tmp_path: Path) -> None:
    """The 7. A superseded unfenced copy is not equal, so the bound alone must not resolve it."""
    text = _deployed().replace(
        "> ⛔ **Do not modify or omit.** The immutable contract.\n"
        "\n<!-- AGENTTEAMS:BEGIN authority",
        "> ⛔ **Do not modify or omit.** The OLD superseded wording.\n"
        "\n<!-- AGENTTEAMS:BEGIN authority",
        1,
    )
    _new, resolved, skipped = _run(tmp_path, text)
    assert not resolved, "a differing copy was resolved by the new bound"
    assert skipped


def test_the_refusal_does_not_recommend_a_merge_when_the_fence_is_present(tmp_path: Path) -> None:
    """Audit finding: one instruction for four situations, wrong for 15 of 19.

    Telling an operator to run `--update --merge` for a file that already carries the fence
    points them at the operation that destroyed content twice, to fix nothing.
    """
    text = _deployed().replace("The immutable contract.", "Superseded wording.", 1)
    _new, _resolved, skipped = _run(tmp_path, text)
    assert skipped
    for s in skipped:
        assert "--update --merge" not in s, (
            f"refusal recommends a merge for a file that already has the fence: {s}"
        )
