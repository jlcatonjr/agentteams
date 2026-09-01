"""test_docs_nav_completeness.py — every published page must be reachable from the nav.

The 2026-08-06 audit found seven pages that build and deploy but appear in no navigation:
`interoperability.md`, the Claude/Copilot privilege pages and cheat sheet, and the API pages
for `feature-inventory`, `template-pins` and `front-matter-reconcile`.

`interoperability.md` was the sharp case. Three pages send readers to it — `index.md` says
"Open the dedicated **Interoperability tab**" — and no such tab existed. The last two
compounded the CLI-Reference gap: `--pin-templates` and `--reconcile-front-matter` were
missing from the flag docs *and* their API pages were off the nav, so both features were
effectively undiscoverable on the published site.

MkDocs only *warns* about this, and a warning in a build log is not a check.

**Exclusions are derived, never declared.** `assets/feature-summary-table.md` is a legitimate
`pymdownx.snippets` include, not an orphan — so this file finds snippet targets by scanning
for `--8<--` references rather than listing them. A declared exclusion list would be one more
artifact to go stale, and would invite weakening the test on its first failure instead of
fixing the nav.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MKDOCS = REPO_ROOT / "mkdocs.yml"
DOCS_SRC = REPO_ROOT / "docs_src"

#: Pages deliberately excluded from the nav for a reason other than being a snippet target.
#: Snippet includes are detected, not listed — see the module docstring.
_INTENTIONALLY_UNNAVIGATED: frozenset[str] = frozenset({
    # An internal production-readiness STATUS report for the security-guide handbook (line
    # counts, write-scan/conformance status) — maintainer-facing meta, not reader content, so
    # it deliberately stays out of the reader nav rather than cluttering it. It still builds and
    # deploys; this declares the exclusion instead of leaving it a silent derived gap.
    "agentteams-security-guide/production-readiness-report.md",
})


def _nav_targets() -> set[str]:
    """Every `.md` path referenced from mkdocs.yml's nav, as a docs_src-relative string."""
    return set(re.findall(r":\s*([A-Za-z0-9_\-/]+\.md)\s*$", MKDOCS.read_text(encoding="utf-8"), re.M))


def _all_pages() -> set[str]:
    return {str(p.relative_to(DOCS_SRC)) for p in DOCS_SRC.rglob("*.md")}


def _snippet_targets() -> set[str]:
    """Pages pulled into another page by `--8<--`. Reachable as content, not as nav entries."""
    found: set[str] = set()
    for page in DOCS_SRC.rglob("*.md"):
        text = page.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'--8<--\s+"docs_src/([^"]+)"', text):
            found.add(m.group(1))
        for m in re.finditer(r"--8<--\s+'docs_src/([^']+)'", text):
            found.add(m.group(1))
    return found


def test_every_page_is_reachable() -> None:
    """The ratchet."""
    unreachable = sorted(
        _all_pages() - _nav_targets() - _snippet_targets() - _INTENTIONALLY_UNNAVIGATED
    )
    assert not unreachable, (
        f"{len(unreachable)} page(s) build and deploy but appear in no navigation: "
        f"{unreachable}. Add each to mkdocs.yml `nav:` — or, if it is content included "
        "elsewhere, include it with `--8<--` so it is detected as a snippet."
    )


def test_nav_points_at_nothing_missing() -> None:
    """The other direction: a nav entry with no file is a 404 in the published site."""
    missing = sorted(_nav_targets() - _all_pages())
    assert not missing, f"mkdocs.yml nav references file(s) that do not exist: {missing}"


def test_the_ratchet_has_no_stale_entries() -> None:
    stale = sorted(p for p in _INTENTIONALLY_UNNAVIGATED if p in _nav_targets())
    assert not stale, f"these are in the nav now and should leave the baseline: {stale}"


def test_snippet_detection_actually_finds_something() -> None:
    """Guards the derived exclusion.

    If the `--8<--` scan silently stopped matching, its result would be empty, every snippet
    target would read as unreachable, and the likely repair would be to add them to
    `_INTENTIONALLY_UNNAVIGATED` — converting a derived exclusion into a declared one and
    losing the property the docstring claims.
    """
    snippets = _snippet_targets()
    assert snippets, "no `--8<--` includes found — the snippet scan regressed"
    for target in snippets:
        assert (DOCS_SRC / target).is_file(), f"snippet include points at a missing file: {target}"


def test_the_measurement_is_not_vacuous() -> None:
    """A glob or regex regression would empty both sides and pass forever."""
    assert len(_all_pages()) > 50, "docs_src page walk regressed"
    assert len(_nav_targets()) > 50, "mkdocs.yml nav parse regressed"


#: api-reference pages that are legitimately not expected on docs_src/api-reference/index.md's
#: own tables — index.md documenting itself would be circular, and the coverage-gaps writeup
#: names missing-page modules by module name (not by a link to a page that doesn't exist), so it
#: can't satisfy a link-based check for pages that DO exist.
_INDEX_SELF_EXCLUDED: frozenset[str] = frozenset({"api-reference/index.md"})


def test_every_api_reference_page_is_linked_from_the_index() -> None:
    """The ratchet, one layer above test_every_page_is_reachable.

    That test proves every page is in mkdocs.yml's nav — the site won't 404. It says nothing
    about docs_src/api-reference/index.md's own hand-curated tables, which is the page a reader
    actually browses to get oriented. The 2026-08-14 audit found 13 real, accurate, fully-navved
    pages with zero mentions anywhere in index.md — present on the site, invisible unless you
    already knew the filename: the six-module multi-framework pinned-sync family (multi_sync,
    sync_pin, sync_baseline, sync_classifier, canonical, capability_map), found by manual grep,
    plus seven more (bridge-pair-docs, bridge-skills, codex-mcp-emit, toml-write, feature-audit,
    integrity, team-package) that this test itself caught the first time it ran. mkdocs's nav
    check could not have caught any of them; it isn't the same claim.
    """
    index_text = (DOCS_SRC / "api-reference" / "index.md").read_text(encoding="utf-8")
    api_nav_pages = {
        p for p in _nav_targets() if p.startswith("api-reference/") and p not in _INDEX_SELF_EXCLUDED
    }
    # Match the actual link target, `](name.md`, not bare substring containment: several page
    # names are substrings of others in this corpus (`emit.md` inside `mcp-emit.md`, `mcp-emit.md`
    # inside `codex-mcp-emit.md`, `baseline.md` inside `sync-baseline.md`) — a bare `in` check
    # would let a dropped row for one hide behind a surviving link to the other.
    unlinked = sorted(
        p for p in api_nav_pages if f"]({Path(p).name}" not in index_text
    )
    assert not unlinked, (
        f"{len(unlinked)} api-reference page(s) are in mkdocs.yml's nav but never linked from "
        f"docs_src/api-reference/index.md, so a reader browsing the index can't discover them: "
        f"{unlinked}. Add each to an index.md table (or to the 'Coverage gaps' list if it "
        "genuinely has no home yet)."
    )


def test_interoperability_has_the_tab_three_pages_promise() -> None:
    """Regression pin for the audit's sharpest instance.

    `index.md` tells the reader to open a dedicated tab. A top-level nav entry is what makes
    that sentence true; burying the page inside a group would not.
    """
    assert "interoperability.md" in _nav_targets()
    nav = MKDOCS.read_text(encoding="utf-8")
    assert re.search(r"^  - [^:]*: interoperability\.md\s*$", nav, re.M), (
        "interoperability.md must be a TOP-LEVEL nav entry (a tab) — docs_src/index.md "
        "directs readers to the 'dedicated Interoperability tab'."
    )
