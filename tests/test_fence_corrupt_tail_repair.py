"""test_fence_corrupt_tail_repair.py — merge must REPAIR a duplicate-append fence corruption.

History: a section (Workflow 0B, "Coordinated concurrency") was manually spliced into
`parallelization.reference.md` *outside* the `content` fence, leaving a second, orphaned
`AGENTTEAMS:END content` marker after it (BEGIN=1, END=2). `_extract_fenced_regions` tolerates the
orphan END, so `_merge_fenced_content` preserved the out-of-fence block as if it were user content
and re-emitted the imbalance on every `--update --merge`. The researchteam autosync fence-pairing
gate (`validate_agentteams_update`) correctly rejected the regenerated file — blocking the sync in
SocialScienceHumanities on 2026-09-01. 31 deployed copies across the fleet carried the same latent
corruption.

`_strip_corrupt_fence_tail` removes exactly that signature — an out-of-fence block immediately
closed by an END with no matching open BEGIN — and nothing else. These tests pin that behaviour so
the corruption cannot silently return, and confirm legitimate out-of-fence content is preserved.
"""
from __future__ import annotations

import re

from agentteams.fences import _merge_fenced_content, _strip_corrupt_fence_tail
from agentteams.unfenced import _FENCE_BEGIN_RE, _FENCE_END_RE


def _counts(s: str) -> tuple[int, int]:
    return len(_FENCE_BEGIN_RE.findall(s)), len(_FENCE_END_RE.findall(s))


# The exact corruption shape: one fence closed after 0A, then 0B out-of-fence, then an orphan END.
_CORRUPT = (
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
    "# Parallelization Reference\n\n"
    "## Execution contract (Workflow 0A)\n"
    "serialize on overlap.\n"
    "<!-- AGENTTEAMS:END content -->\n\n"
    "## Coordinated concurrency for overlapping work\n"
    "opt-in coordinate groups.\n"
    "<!-- AGENTTEAMS:END content -->\n"
)

# The canonical render: a single fence carrying both 0A and 0B.
_FRESH = (
    "<!-- AGENTTEAMS:BEGIN content v=1 -->\n"
    "# Parallelization Reference\n\n"
    "## Execution contract (Workflow 0A)\n"
    "serialize on overlap.\n\n"
    "## Coordinated concurrency for overlapping work\n"
    "opt-in coordinate groups.\n"
    "<!-- AGENTTEAMS:END content -->\n"
)


def test_strip_removes_orphan_end_terminated_tail():
    assert _counts(_CORRUPT) == (1, 2)
    repaired = _strip_corrupt_fence_tail(_CORRUPT)
    assert _counts(repaired) == (1, 1)
    # The out-of-fence duplicate (and its orphan END) is gone.
    assert repaired.count("## Coordinated concurrency") == 0


def test_strip_is_noop_on_well_formed_input():
    assert _strip_corrupt_fence_tail(_FRESH) == _FRESH


def test_strip_preserves_legitimate_out_of_fence_content():
    # A trailing user paragraph NOT terminated by an orphan END must survive byte-for-byte.
    legit = _FRESH + "\nHand-authored project note outside any fence.\n"
    assert _strip_corrupt_fence_tail(legit) == legit
    # Out-of-fence content between two valid fences is also preserved.
    two = (
        "<!-- AGENTTEAMS:BEGIN a v=1 -->\nx\n<!-- AGENTTEAMS:END a -->\n"
        "user text between fences\n"
        "<!-- AGENTTEAMS:BEGIN b v=1 -->\ny\n<!-- AGENTTEAMS:END b -->\n"
    )
    assert _strip_corrupt_fence_tail(two) == two


def test_merge_repairs_corruption_end_to_end():
    mr = _merge_fenced_content(_FRESH, _CORRUPT)
    assert not mr.has_errors
    assert _counts(mr.merged_content) == (1, 1)
    # 0B appears exactly once (inside the fence, from the fresh render) — not duplicated.
    assert mr.merged_content.count("## Coordinated concurrency") == 1


def test_merge_well_formed_unchanged_balance():
    mr = _merge_fenced_content(_FRESH, _FRESH)
    assert not mr.has_errors
    assert _counts(mr.merged_content) == (1, 1)
    assert mr.merged_content.count("## Coordinated concurrency") == 1
