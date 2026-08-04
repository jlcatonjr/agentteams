"""test_published_artifacts_have_checks.py — every published contract needs something checking it.

Three artifacts drifted from their contracts in a single session, each found by ad-hoc
investigation rather than by any standing check:

1. `doc_site_config_file` was read by `analyze.build_manifest` but absent from
   `project-description.schema.json`, which sets `additionalProperties: false` — so this
   module's own brief never validated, because nothing validated it.
2. The self brief gained a guard; the **example** briefs did not, and one was found violating
   the same schema two ways.
3. The man page went stale when two flags were added — caught by CI, which is the one place a
   check already existed.

The pattern is not three unlucky files. It is that nothing enumerates the project's published
artifacts, so a new one arrives without a freshness check and nobody notices until it drifts.
The man-page and API-doc-signature checks exist for exactly this reason, and both were added
*after* a drift was found.

**The list is derived, never declared.** An enumeration written by hand is itself a published
artifact that goes stale — the failure being fixed, one level up. Artifacts are found by what
they are: a `.json` in `schemas/`, a brief in `examples/`, the man page.

**A ratchet, not a gate.** Two schemas currently have nothing checking them. That is recorded
as debt which may fall and must not grow, in the same shape as the other ratchets in this suite.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Schemas with no test or script referencing them, verified 2026-08-03.
#: LOWER this as coverage lands; never add to it to make a failure go away.
#:
#: Being listed here does not mean the schema is unused — it means no test validates any real
#: artifact against it, so a change to either could not be caught.
_UNCHECKED_SCHEMAS: frozenset[str] = frozenset({
    "agent-cai.schema.json",
    "post-production-audit-closure-gate-status.schema.json",
})


def _checking_corpus() -> str:
    """Everything that could plausibly hold a check: the test suite and the scripts."""
    parts = []
    for directory in ("tests", "scripts"):
        for path in sorted((REPO_ROOT / directory).glob("*.py")):
            # This file names the uncovered schemas in `_UNCHECKED_SCHEMAS`, so including it
            # would make every baseline entry look covered by its own baseline — the guard
            # certifying itself. Caught by `test_the_unchecked_baseline_has_no_stale_entries`
            # failing on arrival.
            if path.name == Path(__file__).name:
                continue
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _published_schemas() -> list[str]:
    return sorted(p.name for p in (REPO_ROOT / "schemas").glob("*.json"))


def test_no_schema_loses_its_check() -> None:
    """The ratchet. A published schema that nothing references cannot catch its own drift."""
    corpus = _checking_corpus()
    schemas = _published_schemas()
    assert len(schemas) >= 10, f"only {len(schemas)} schemas found — the walk regressed"

    unchecked = {s for s in schemas if s not in corpus}
    new = unchecked - _UNCHECKED_SCHEMAS
    assert not new, (
        f"schema(s) with nothing checking them: {sorted(new)}. A published contract that no "
        "test validates any real artifact against cannot catch its own drift — which is how "
        "`doc_site_config_file` sat unnoticed in project-description.schema.json. Add a check, "
        "or add it to _UNCHECKED_SCHEMAS with a reason."
    )


def test_the_unchecked_baseline_has_no_stale_entries() -> None:
    """Keep the ratchet honest: an entry that gained a check must be removed."""
    corpus = _checking_corpus()
    stale = sorted(s for s in _UNCHECKED_SCHEMAS if s in corpus)
    assert not stale, (
        f"these now have checks and should leave _UNCHECKED_SCHEMAS: {stale}. A baseline that "
        "outlives its reason leaves room for the regression it was meant to catch."
    )


def test_every_example_brief_is_covered_by_the_brief_guard() -> None:
    """The specific gap that shipped a stale example: the self brief was guarded, examples were not."""
    guard = (REPO_ROOT / "tests" / "test_self_brief_validates.py").read_text(encoding="utf-8")
    assert "examples" in guard and "glob" in guard, (
        "the brief guard no longer enumerates example briefs; a stale example could ship again"
    )
    briefs = sorted((REPO_ROOT / "examples").glob("*/brief.json"))
    assert len(briefs) >= 3, f"only {len(briefs)} example briefs found — the walk regressed"


def test_the_man_page_check_still_exists() -> None:
    """The one artifact that already had a freshness check, and the reason this file exists.

    CI regenerates `agentteams.1` and diffs it. If that step is ever removed, the man page joins
    the class of artifacts that drift silently.
    """
    workflows = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
    )
    assert "agentteams.man" in workflows, (
        "CI no longer regenerates the man page; it can now drift from the parser unnoticed"
    )
    assert (REPO_ROOT / "agentteams.1").is_file()


def test_api_reference_pages_are_signature_checked() -> None:
    """`docs_src/api-reference/` is published documentation with a real drift history."""
    check = REPO_ROOT / "tests" / "test_api_doc_signatures.py"
    assert check.is_file(), "the API-doc signature check is gone; those pages can drift again"
    pages = sorted((REPO_ROOT / "docs_src" / "api-reference").glob("*.md"))
    assert len(pages) >= 20, f"only {len(pages)} api-reference pages — the walk regressed"
