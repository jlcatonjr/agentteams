"""test_resolver_requires_a_deployed_survivor.py — never delete the only copy.

The resolver removes a deployed file's unfenced section when its template now fences that
section. The survivor is supposed to be the fenced copy. Nothing checked that the fenced copy
existed **in the deployed file**.

`incoming` is read from the *fresh render*, so a template fence proves nothing about what is
on disk. On 2026-08-01 that deleted 331 lines from a deployed `security.md` — 363 lines to 32
— on the strength of an `invariant_core` fence the file had never received.

The guard added afterwards refuses when the removal span *encloses* a live fence. That is a
different property, and it does not fire here: on 2026-08-03, applying nine "equality-proved"
resolutions removed `## Rules` from `conflict-auditor.md` — five substantive rules, no
surviving copy anywhere in the file. The deployed file carries four fences
(`authority_sources_list`, `behavioral_spec_cross_check`, `memory_index_consultation`,
`typed_handoff_audit`) and has never had the `rules` fence the render supplies. The span
enclosed nothing, so the guard passed it through.

Caught by a verification gate that re-derived the proof from the **written result** rather
than the input, then reverted from git. The rule this file enforces is the one the guard's own
comment already stated but did not implement: *merge the fence in first; only then is the
unfenced copy a duplicate.*
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

#: Faithful to `conflict-auditor.template.md`, where the trailing `---` sits INSIDE the fence.
#: That detail is load-bearing: it is what makes the unfenced span equal the fence body and so
#: what let the removal be reported as `[equality]`. A fixture without it refuses for an
#: unrelated reason and proves nothing.
RULES = (
    "## Rules\n\n"
    "1. Log every finding — do not silently accept or resolve\n"
    "2. Route `SOURCE_DRIFT` to `@technical-validator` for verification\n\n"
    "---\n"
)


def _render_with_fence() -> str:
    """A fresh render that DOES fence the section."""
    return (
        "# Agent\n\n"
        "<!-- AGENTTEAMS:BEGIN rules v=1 -->\n"
        f"{RULES}"
        "<!-- AGENTTEAMS:END rules -->\n"
    )


def _deployed_without_the_fence() -> str:
    """A deployed file that predates the fence: one unfenced copy, and no `rules` fence."""
    return (
        "# Agent\n\n"
        "<!-- AGENTTEAMS:BEGIN other v=1 -->\n"
        "## Something Else\n\n"
        "Body.\n"
        "<!-- AGENTTEAMS:END other -->\n\n"
        f"{RULES}\n"
        "## Project-Specific Notes\n\nMine.\n"
    )


def test_no_deployed_fence_means_no_removal(tmp_path: Path) -> None:
    """The regression. Removing here deletes the only copy of the section."""
    deployed = tmp_path / "conflict-auditor.md"
    deployed.write_text(_deployed_without_the_fence(), encoding="utf-8")
    before = deployed.read_text(encoding="utf-8")

    new_text, resolved, skipped = rfc._resolve_file(deployed, _render_with_fence())

    assert not resolved, f"resolver proposed a removal with no surviving copy: {resolved}"
    if new_text is not None:
        assert "## Rules" in new_text, "the only copy of '## Rules' was deleted"
    assert deployed.read_text(encoding="utf-8") == before


def test_the_refusal_says_why(tmp_path: Path) -> None:
    """An operator has to be able to act on it: merge the fence in, then re-run."""
    deployed = tmp_path / "conflict-auditor.md"
    deployed.write_text(_deployed_without_the_fence(), encoding="utf-8")

    _new, _resolved, skipped = rfc._resolve_file(deployed, _render_with_fence())

    assert skipped, "a refusal must be reported, not silent"
    joined = " ".join(skipped).lower()
    assert "merge" in joined, f"refusal does not tell the operator what to do: {skipped}"


def test_a_deployed_fence_still_authorises_the_removal(tmp_path: Path) -> None:
    """Negative control. The tool must still do its job when a survivor genuinely exists."""
    deployed = tmp_path / "agent.md"
    deployed.write_text(
        "# Agent\n\n"
        "<!-- AGENTTEAMS:BEGIN rules v=1 -->\n"
        f"{RULES}"
        "<!-- AGENTTEAMS:END rules -->\n\n"
        f"{RULES}\n"
        "## Project-Specific Notes\n\nMine.\n",
        encoding="utf-8",
    )

    new_text, resolved, _skipped = rfc._resolve_file(deployed, _render_with_fence())

    assert resolved, "a genuine duplicate with a deployed survivor was not resolved"
    assert new_text is not None
    assert new_text.count("## Rules") == 1, "the fenced copy must survive exactly once"
    assert "<!-- AGENTTEAMS:BEGIN rules" in new_text
    assert "## Project-Specific Notes" in new_text, "operator content was destroyed"


def test_the_survivor_must_be_unambiguous(tmp_path: Path) -> None:
    """Two deployed fences carrying the heading: which is the survivor? Refuse rather than pick."""
    deployed = tmp_path / "agent.md"
    deployed.write_text(
        "# Agent\n\n"
        "<!-- AGENTTEAMS:BEGIN a v=1 -->\n" + RULES + "<!-- AGENTTEAMS:END a -->\n\n"
        "<!-- AGENTTEAMS:BEGIN b v=1 -->\n" + RULES + "<!-- AGENTTEAMS:END b -->\n\n"
        + RULES + "\n## Project-Specific Notes\n\nMine.\n",
        encoding="utf-8",
    )
    _new, resolved, _skipped = rfc._resolve_file(deployed, _render_with_fence())
    assert not resolved, "an ambiguous survivor must not authorise a removal"


def test_no_deployed_file_has_a_heading_the_resolver_would_orphan() -> None:
    """Live invariant over the real team, replacing a pin on one transient file.

    This began as a pin on `conflict-auditor.md`, which on 2026-08-03 carried an unfenced
    `## Rules` with no `rules` fence — the shape that let the resolver delete the only copy.
    That file was merged the same day and the fence arrived, so the pin discharged itself and
    went red exactly as its own message predicted.

    A pin on one file's defect expires when the defect is fixed. The property worth keeping is
    the general one: for every duplicated heading in the deployed team, a fenced survivor must
    exist — and where one does not, the resolver must refuse rather than remove.

    Not vacuous when the tree is clean: the scan asserts it examined a real team with real
    fences, so a broken walk cannot pass by finding nothing.
    """
    import pytest

    from agentteams.fences import _extract_fenced_regions

    agents = REPO_ROOT / ".claude/agents"
    if not agents.is_dir():
        pytest.skip("this repo's own .claude/agents is not present")

    examined = 0
    orphanable = []
    for path in sorted(agents.rglob("*.md")):
        if ".agentteams-backups" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        regions = _extract_fenced_regions(text)
        if not isinstance(regions, dict) or not regions:
            continue
        examined += 1
        for heading in rfc._duplicate_headings_in_file(text):
            if rfc._deployed_fence_carrying(text, heading) is None:
                orphanable.append(f"{path.relative_to(agents)}: {heading!r}")

    assert examined >= 20, (
        f"only {examined} fenced files examined — the walk regressed and this test would "
        "pass without checking anything"
    )
    assert not orphanable, (
        "deployed heading(s) whose removal would leave no fenced copy:\n  "
        + "\n  ".join(orphanable)
    )
