"""The collision resolver deletes content, so what authorises each delete is the subject here.

`scripts/resolve_fence_collisions.py` removes a deployed file's pre-fencing copy of a section its
template now fences. The estimate that sized the job — "35 of 40 are short, single-occurrence and
unfenced" — is a heuristic, and a heuristic must never be what permits a write. The proof is per
collision: the deployed unfenced section must equal the incoming fenced body once whitespace is
collapsed.

The refusals matter more than the resolutions. Each of these was a way to destroy content:

- A trailing section runs to end-of-file, so deleting it would also take `## Project-Specific
  Notes` — the region `_split_at_last_fence_end` exists to protect, destroyed by the tool built to
  clean up after it.
- A heading appearing twice makes the boundaries ambiguous.
- A deployed copy that differs from the template's is exactly the case a human must look at.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_resolver", Path(__file__).resolve().parents[1] / "scripts" / "resolve_fence_collisions.py"
)
resolver = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(resolver)


#: A deployed file that collides always has SOME fences — that is what "partial adoption" means.
#: With none at all the merge bails as a legacy file before collision detection ever runs, so a
#: fixture without `kept` would test nothing. Found by this test failing on its first draft.
_KEPT_FENCE = (
    "<!-- AGENTTEAMS:BEGIN kept v=1 -->\n## Kept\n\nalready fenced.\n"
    "<!-- AGENTTEAMS:END kept -->\n\n"
)

_FRESH = (
    "---\nname: A\ndescription: x\n---\n\n"
    + _KEPT_FENCE +
    "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
    "## Invariant Core\n\nThe contract. Do not modify.\n"
    "<!-- AGENTTEAMS:END invariant_core -->\n\n"
    "## Tail\n\ntrailing template section.\n"
)


def _deployed(core_body: str, *, trailing: str = "## Tail\n\ntrailing.\n") -> str:
    return (
        "---\nname: A\ndescription: x\n---\n\n"
        + _KEPT_FENCE +
        f"## Invariant Core\n\n{core_body}\n\n"
        f"{trailing}"
        "\n## Project-Specific Notes\n\n- operator content that must survive.\n"
    )


def _run(tmp_path: Path, deployed_text: str, fresh: str = _FRESH):
    f = tmp_path / "a.agent.md"
    f.write_text(deployed_text, encoding="utf-8")
    return resolver._resolve_file(f, fresh)


def test_an_identical_pre_fencing_copy_is_removed(tmp_path):
    new_text, resolved, skipped = _run(tmp_path, _deployed("The contract. Do not modify."))
    assert resolved == ["## Invariant Core"], (resolved, skipped)
    assert new_text is not None
    assert new_text.count("## Invariant Core") == 0, "the unfenced copy is gone"
    assert "operator content that must survive" in new_text


def test_whitespace_differences_do_not_block_a_resolution(tmp_path):
    _, resolved, _ = _run(tmp_path, _deployed("The   contract.\n   Do not modify."))
    assert resolved == ["## Invariant Core"]


def test_a_differing_copy_is_refused_and_reported(tmp_path):
    new_text, resolved, skipped = _run(tmp_path, _deployed("The contract. Modified by the project."))
    assert resolved == []
    assert new_text is None, "nothing may be written when nothing was proved"
    assert any("differs from the template" in s for s in skipped), skipped


def test_a_trailing_section_is_refused(tmp_path):
    """The sharp one: bounding at EOF would delete the operator's region below it."""
    trailing_only = (
        "---\nname: A\ndescription: x\n---\n\n"
        + _KEPT_FENCE +
        "## Invariant Core\n\nThe contract. Do not modify.\n"
        "\n- operator content with no following heading, which a bound-at-EOF would delete.\n"
    )
    new_text, resolved, skipped = _run(tmp_path, trailing_only)
    assert resolved == []
    assert new_text is None
    assert any("trailing at end-of-file" in s or "cannot bound" in s for s in skipped), skipped


def test_a_duplicated_heading_is_refused(tmp_path):
    doubled = _deployed("The contract. Do not modify.").replace(
        "## Tail\n\ntrailing.\n", "## Invariant Core\n\nanother copy.\n\n## Tail\n\ntrailing.\n"
    )
    _, resolved, skipped = _run(tmp_path, doubled)
    assert resolved == []
    assert any("cannot bound" in s for s in skipped), skipped


def test_a_file_with_no_collision_is_untouched(tmp_path):
    """Already-clean files must produce no plan at all, not an empty rewrite."""
    new_text, resolved, skipped = _run(tmp_path, _FRESH)
    assert (new_text, resolved, skipped) == (None, [], [])


@pytest.mark.parametrize("norm_in, norm_out", [
    ("a   b\n\nc", "a b c"),
    ("  leading and trailing  ", "leading and trailing"),
])
def test_normalisation_is_whitespace_only(norm_in, norm_out):
    """No case folding, no punctuation stripping — a real difference cannot be normalised away."""
    assert resolver._norm(norm_in) == norm_out
    assert resolver._norm("Do not modify.") != resolver._norm("do not modify")
