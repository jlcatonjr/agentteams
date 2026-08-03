"""test_pairing_requires_own_heading.py — a sub-heading inside a fence is not that fence's twin.

`_deployed_fence_carrying` asks whether *some fenced body contains* the heading. That is the
right question for **safety** — "will a copy survive this removal?" — and it is what stops the
resolver deleting a section with no survivor.

It is the wrong question for **pairing**. `invariant_core` contains several sub-headings, so an
unfenced `### Core Responsibilities` gets compared against the entire `invariant_core` body.
The comparison cannot match and the collision can never resolve, so it sits in the review
report forever. Three of this repository's ten reported collisions were this, and a review
report that is 30% permanent noise is one people stop reading — which is how the genuine
defects went unread earlier in the week.

**Two predicates, two purposes, deliberately not merged.** Narrowing the safety predicate to
fix the noise would re-open the data-loss hole closed the same morning: a real survivor that
happens not to lead its fence would stop counting, and the removal would proceed. The pairing
predicate is strictly narrower and is used only to decide what is *comparable*.
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

FENCED = (
    "<!-- AGENTTEAMS:BEGIN invariant_core v=1 -->\n"
    "## Invariant Core\n\n"
    "> ⛔ Do not modify.\n\n"
    "### Core Responsibilities\n\n"
    "- audit things\n"
    "<!-- AGENTTEAMS:END invariant_core -->\n"
)

DEPLOYED = "# Agent\n\n" + FENCED + "\n### Core Responsibilities\n\n- audit things\n"


def test_a_contained_subheading_does_not_pair_with_the_fence() -> None:
    """The noise. `### Core Responsibilities` lives inside invariant_core; it is not its twin."""
    assert rfc._fence_whose_first_heading_is(DEPLOYED, "### Core Responsibilities") is None


def test_the_fences_own_heading_does_pair() -> None:
    assert rfc._fence_whose_first_heading_is(DEPLOYED, "## Invariant Core") is not None


def test_the_safety_predicate_is_unchanged() -> None:
    """The load-bearing property: a contained heading still counts as having a survivor.

    If this ever returns None, a removal that should be refused would be allowed — the exact
    regression that deleted `## Rules` from conflict-auditor.md on 2026-08-03.
    """
    assert rfc._deployed_fence_carrying(DEPLOYED, "### Core Responsibilities") is not None
    assert rfc._deployed_fence_carrying(DEPLOYED, "## Invariant Core") is not None


def test_no_fence_means_no_pairing_and_no_survivor() -> None:
    text = "# Agent\n\n## Rules\n\n- one\n"
    assert rfc._fence_whose_first_heading_is(text, "## Rules") is None
    assert rfc._deployed_fence_carrying(text, "## Rules") is None


def test_two_fences_leading_with_the_same_heading_refuse() -> None:
    """Ambiguous pairing: which is the twin? Refuse rather than choose."""
    text = (
        "# Agent\n\n"
        "<!-- AGENTTEAMS:BEGIN a v=1 -->\n## Rules\n\n- one\n<!-- AGENTTEAMS:END a -->\n\n"
        "<!-- AGENTTEAMS:BEGIN b v=1 -->\n## Rules\n\n- one\n<!-- AGENTTEAMS:END b -->\n\n"
        "## Rules\n\n- one\n"
    )
    assert rfc._fence_whose_first_heading_is(text, "## Rules") is None


def test_no_containment_artifact_is_ever_treated_as_a_collision() -> None:
    """The invariant over the real team, stated so it survives the tree being clean.

    This first read `assert paired > 0` — a pin on the tree *having* duplicates — and went red
    the moment they were all resolved. That is the **second** discharged pin this session; the
    first was `test_the_real_deployed_conflict_auditor_is_refused`, which asserted a file still
    lacked a fence and failed when the fence arrived.

    The lesson is not to write the assertion more cleverly, it is that a real-tree test must
    assert a *property that holds in both states*. Here: no heading that merely sits nested
    inside a fenced block may be treated as a duplicated section. On a tree with duplicates that
    excludes the artifacts; on a clean tree it holds trivially — and the synthetic fixtures above
    carry the behavioural weight either way.

    Anti-vacuity is on the WALK, not on the defect: the scan must reach a real fenced team.
    """
    import pytest

    from agentteams.fences import _extract_fenced_regions

    agents = REPO_ROOT / ".claude/agents"
    if not agents.is_dir():
        pytest.skip("this repo's own .claude/agents is not present")

    fenced_files = 0
    violations = []
    for path in sorted(agents.rglob("*.md")):
        if ".agentteams-backups" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        regions = _extract_fenced_regions(text)
        if not isinstance(regions, dict) or not regions:
            continue
        fenced_files += 1
        for heading in rfc._duplicate_headings_in_file(text):
            has_survivor = rfc._deployed_fence_carrying(text, heading) is not None
            pairs = rfc._fence_whose_first_heading_is(text, heading) is not None
            if has_survivor and not pairs:
                violations.append(f"{path.relative_to(agents)}: {heading!r}")

    assert fenced_files >= 20, (
        f"only {fenced_files} fenced files scanned — the walk regressed and this test would "
        "pass without examining the team"
    )
    assert not violations, (
        "containment artifact(s) still reachable as collisions:\n  " + "\n  ".join(violations)
    )
