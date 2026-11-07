"""Whole-body migration: heading variants and the shrink-guard override (2026-11-06).

Two defects found regenerating agentteams' own pr-manager / pr-notifier, whose deployed files carried one
legacy ``content`` fence:

* an old section whose heading differs from the template's only in case or punctuation
  (``## Disposition Prompt (end-of-task)`` vs ``## Disposition prompt (end of task)``) was treated as a
  section the template lacks and carried out of the fence, beside the template's fenced copy: a stale duplicate;
* ``--shrink-policy=halt`` refused the migration and ``--shrink-allow FILE:content@<digest>`` could not release
  it, so the layout split had no reviewed way through.
"""

from __future__ import annotations

from agentteams.fences import _extract_fenced_regions, _merge_fenced_content
from agentteams.shrink_allow import key as allow_key
from agentteams.whole_body_migration import _hkey

_FM = "---\nname: PR Manager — Acme\ndescription: \"PR lifecycle for Acme\"\ntools: Read, Grep, Glob\n---\n"
_NOTES = "## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — project-specific rules for this agent.\n"
_REL = "pr-manager.md"


def _fence(sid: str, body: str) -> str:
    return f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{body}<!-- AGENTTEAMS:END {sid} -->\n"


def _deployed(extra: str = "") -> str:
    """A hand-adopted file: one whole-body fence whose headings differ in case/punctuation from the template."""
    return (_FM + "<!-- AGENTTEAMS:BEGIN content v=1 -->\n\n# PR Manager — Acme\n\nYou coordinate PRs.\n\n"
            "## Invariant Core\n\n1. **Never merge a PR.**\n\n"
            "## Disposition Prompt (end-of-task)\n\n| Choice | Routing |\n|---|---|\n| Commit + push to main | git |\n\n"
            "## Output Contract\n\n- Action type\n- PR number\n" + extra
            + "\n<!-- AGENTTEAMS:END content -->\n\n" + _NOTES)


def _render() -> str:
    return (_FM + "\n# PR Manager — Acme\n\nYou coordinate PRs.\n\n"
            + _fence("invariant_core", "## Invariant Core\n\n1. **Never merge a PR.** Human review is sovereign.\n") + "\n"
            + _fence("disposition_prompt", "## Disposition prompt (end of task)\n\n| Choice | Routing |\n|---|---|\n"
                     "| Merge locally into `dev` | git |\n") + "\n"
            + _fence("output_contract", "## Output contract\n\n- the action\n- the PR number and URL\n") + "\n"
            + _NOTES)


def _merge(deployed: str, **kw):
    kw.setdefault("twin_drop", True)          # a real run has a backup dir
    return _merge_fenced_content(_render(), deployed, rel_path=_REL, **kw)


def _content_key(deployed: str) -> str:
    regions = _extract_fenced_regions(deployed)
    assert isinstance(regions, dict)
    return allow_key(_REL, "content", regions["content"])


def test_heading_key_ignores_case_and_punctuation_but_not_level_or_words():
    assert _hkey("## Disposition Prompt (end-of-task)") == _hkey("## Disposition prompt (end of task)")
    assert _hkey("## Output Contract") == _hkey("## Output contract")
    assert _hkey("## Output Contract") != _hkey("### Output Contract")
    assert _hkey("## Output Contract") != _hkey("## Output Contract Extras")


def test_a_variant_heading_of_a_fenced_section_is_not_carried_as_a_duplicate():
    merged = _merge(_deployed()).merged_content
    heads = [x.strip() for x in merged.splitlines() if x.startswith("## ")]
    assert heads.count("## Disposition prompt (end of task)") == 1
    assert "## Disposition Prompt (end-of-task)" not in heads
    assert "## Output Contract" not in heads and heads.count("## Output contract") == 1
    assert "Commit + push to main" not in merged          # the stale row lives only in the .lost sidecar
    assert "content" not in _extract_fenced_regions(merged)


def test_a_section_with_different_words_is_still_carried():
    extra = "\n## Acme Review Rota\n\n- Reviews rotate weekly.\n"
    merged = _merge(_deployed(extra)).merged_content
    assert "## Acme Review Rota" in merged and "Reviews rotate weekly." in merged


def test_the_migration_notice_prints_its_override_entry():
    deployed = _deployed()
    result = _merge(deployed)
    assert result.migrated
    notice = next(n for n in result.shrink_notices if n.startswith("fence 'content':"))
    assert f"--shrink-allow {_content_key(deployed)}" in notice
    assert "content" not in result.shrink_overridden


def test_the_reviewed_entry_releases_the_migration_and_a_stale_one_does_not():
    deployed = _deployed()
    released = _merge(deployed, shrink_allow=frozenset({_content_key(deployed)}))
    assert "content" in released.shrink_overridden
    # Bound to the reviewed body: an entry for an edited body releases nothing.
    edited = _deployed("\n- Labels applied\n")
    stale = _merge(edited, shrink_allow=frozenset({_content_key(deployed)}))
    assert "content" not in stale.shrink_overridden


def test_heading_key_keeps_symbols_and_non_ascii_words_distinct():
    assert len({_hkey(h) for h in ("## C++", "## C#", "## C")}) == 3
    assert _hkey("## 日本語") != _hkey("## 中文")
    assert _hkey("## Ünïcode Überschrift") == _hkey("## ÜNÏCODE überschrift")
    # A heading that folds to nothing is keyed by its exact text: it matches only itself.
    assert _hkey("## ---") != _hkey("## ***") and _hkey("## ---") == _hkey("## ---")


def test_a_variant_user_region_heading_inside_the_old_fence_is_carried_not_dropped():
    """`## Project-specific notes` (no USER-EDITABLE marker) is still the project's region (@security, 2026-11-06)."""
    extra = "\n## Project-specific notes\n\n- Acme reviewers rotate weekly.\n"
    # The file has no notes outside the old fence, so the render keeps its own notes heading: the variant then
    # folds onto a heading the result already has, which is where an exact-text user-region check lost it.
    deployed = _deployed(extra).replace("\n" + _NOTES, "\n")
    merged = _merge(deployed).merged_content
    assert "Acme reviewers rotate weekly." in merged
