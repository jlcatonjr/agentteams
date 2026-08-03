"""test_trailing_collision_equality.py — bounding a trailing duplicate at EOF, provably.

`_unfenced_section_span` returns `"trailing"` when no heading of the same-or-higher level
follows the unfenced occurrence. The section then runs to end-of-file, and bounding it there
would take any trailing operator content with it — `## Project-Specific Notes` is exactly the
region the fence machinery exists to protect. Refusing is correct in general.

But six deployed reference files carried a trailing duplicate that is **byte-identical to the
fenced copy directly above it**:

    <!-- AGENTTEAMS:BEGIN operational_integration v=1 -->
    ## Operational integration            <- fenced
    ...
    <!-- AGENTTEAMS:END operational_integration -->

    ## Operational integration            <- unfenced twin, runs to EOF

When the text from the unfenced heading to EOF equals the deployed fenced body, removing it
deletes nothing that does not survive inside the fence. That is a proof, local to the file,
and it needs no `--trust-provenance`, no `_file_is_pristine`, and no git ref.

**Against the DEPLOYED fence, never the incoming render.** The pre-existing bug class in this
script is precisely that confusion: `incoming` comes from the fresh render, so a template
fence proves nothing about what is on disk. A file whose live fence has drifted from the
template must still refuse.
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
sys.modules["resolve_fence_collisions"] = rfc
_spec.loader.exec_module(rfc)

BODY = (
    "1. Refresh judgement against these primary sources during security reviews.\n"
    "2. Route high-priority platform gaps into `@security` review gates before execution.\n"
)
HEADING = "## Operational integration"


def _deployed(tail: str, *, fenced_body: str = BODY) -> str:
    return (
        "# Reference\n\n"
        "<!-- AGENTTEAMS:BEGIN operational_integration v=1 -->\n"
        f"{HEADING}\n\n{fenced_body}"
        "<!-- AGENTTEAMS:END operational_integration -->\n\n"
        f"{tail}"
    )


# --------------------------------------------------------------------------------------
# The proof authorises
# --------------------------------------------------------------------------------------


def test_an_identical_trailing_duplicate_is_provable() -> None:
    text = _deployed(f"{HEADING}\n\n{BODY}")
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is True


def test_the_span_then_bounds_at_end_of_file() -> None:
    """The proof is only useful if the span it authorises removes exactly the twin."""
    text = _deployed(f"{HEADING}\n\n{BODY}")
    span = rfc._trailing_span(text, HEADING)
    assert not isinstance(span, str), span
    start, end = span
    assert end == len(text)
    assert rfc._norm(text[start:end]) == rfc._norm(f"{HEADING}\n\n{BODY}")
    survivor = text[:start]
    assert survivor.count(HEADING) == 1, "the fenced copy must survive"


# --------------------------------------------------------------------------------------
# The proof refuses — these are the reasons it is a proof and not a heuristic
# --------------------------------------------------------------------------------------


def test_extra_trailing_text_refuses() -> None:
    """"Trailing" guarantees no same-or-higher HEADING follows — not that no TEXT follows.

    A note appended under the duplicate carries no heading of its own, so the span still
    reports `trailing`, and bounding at EOF would delete it. The equality test is what
    catches this: the tail no longer matches the fence.
    """
    text = _deployed(f"{HEADING}\n\n{BODY}\nOperator note: keep this line.\n")
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is False


def test_project_specific_notes_below_the_duplicate_refuses() -> None:
    """The region the whole fence design protects. `## Project-Specific Notes` is `##`, so it
    would normally terminate the span — this plants it as a deeper heading, where it does not,
    to check the equality test rather than the heading walk."""
    text = _deployed(f"{HEADING}\n\n{BODY}\n### Project-Specific Notes\n\nMine.\n")
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is False


def test_a_drifted_deployed_fence_refuses() -> None:
    """The audit's first finding. The fence on disk, not the template, is the authority.

    Here the trailing copy matches what a *template* might render while the live fence says
    something else. Removing the twin would lose the only copy of the deployed wording.
    """
    text = _deployed(
        f"{HEADING}\n\n{BODY}",
        fenced_body="1. A locally amended obligation that the template does not carry.\n",
    )
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is False


def test_a_differing_word_refuses() -> None:
    text = _deployed(f"{HEADING}\n\n{BODY.replace('Refresh', 'Reconsider')}")
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is False


def test_no_fenced_twin_refuses() -> None:
    """Nothing to prove against: a lone trailing section is not a duplicate at all."""
    text = f"# Reference\n\n{HEADING}\n\n{BODY}"
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is False


def test_two_fenced_twins_refuse() -> None:
    """Ambiguous: which fence is the survivor? Refuse rather than pick."""
    text = (
        "# Reference\n\n"
        "<!-- AGENTTEAMS:BEGIN a v=1 -->\n"
        f"{HEADING}\n\n{BODY}"
        "<!-- AGENTTEAMS:END a -->\n\n"
        "<!-- AGENTTEAMS:BEGIN b v=1 -->\n"
        f"{HEADING}\n\n{BODY}"
        "<!-- AGENTTEAMS:END b -->\n\n"
        f"{HEADING}\n\n{BODY}"
    )
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is False


def test_an_unparseable_deployed_file_refuses() -> None:
    """A file whose fences do not parse tells us nothing; it must not be resolved."""
    text = (
        "# Reference\n\n"
        "<!-- AGENTTEAMS:BEGIN operational_integration v=1 -->\n"
        f"{HEADING}\n\n{BODY}"
        f"\n{HEADING}\n\n{BODY}"
    )  # BEGIN with no END
    assert rfc._trailing_duplicates_a_deployed_fence(text, HEADING) is False


# --------------------------------------------------------------------------------------
# Anti-vacuity against the real tree
# --------------------------------------------------------------------------------------


def test_the_six_measured_reference_files_prove() -> None:
    """Pin the six that prompted this, measured 2026-08-03 against `.claude/agents`.

    Skipped rather than relaxed when the team is absent: a fabricated pass here would hide
    the exact regression this file guards.
    """
    import pytest

    cases = [
        ("references/adjacent-repos.md", "## Retired Entries"),
        ("references/framework-watch.reference.md", "## Operational Integration Process"),
        ("references/security-linux-hardening.reference.md", "## Operational integration"),
        ("references/security-macos-hardening.reference.md", "## Operational integration"),
        (
            "references/security-vulnerability-watch.reference.md",
            "## Operational Integration Process",
        ),
        ("references/security-windows-hardening.reference.md", "## Operational integration"),
    ]
    agents = REPO_ROOT / ".claude/agents"
    if not agents.is_dir():
        pytest.skip("this repo's own .claude/agents is not present")

    bad = []
    for rel, heading in cases:
        path = agents / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # The duplicates were resolved on 2026-08-03, so the expected steady state is now
        # "exactly one copy, inside a fence". Either outcome is correct; a heading with no
        # fenced copy is not.
        if rfc._trailing_duplicates_a_deployed_fence(text, heading):
            continue
        if rfc._deployed_fence_carrying(text, heading) is None:
            bad.append(f"{rel}: no fenced copy of {heading!r} survives")
        elif len(rfc._unfenced_starts(text, heading)) > 1:
            bad.append(f"{rel}: {heading!r} still duplicated outside a fence and no longer proves")
    assert not bad, "trailing duplicates left in a bad state:\n  " + "\n  ".join(bad)
