"""test_module_doc_ratchet.py — the undocumented-module count may fall, never rise.

`docs_src/api-reference/` documents the package module by module, and 20 modules have no page.
That is debt, not a defect: writing 20 reference pages is real work and the count has grown
mostly by modules being *carved out* of documented ones under CH-07 — `unfenced` from `fences`,
`audit_types` from `audit`, `graph_inputs` from `graph`, `standalone_modes` from `cli/generate`.

So: a ratchet, in the same shape as `BROAD_EXCEPT_BASELINE`, `LENGTH_ALLOWLIST` and the
unfenced-constraint baseline. A new module arrives documented, or it does not arrive.

**What a passing run does NOT establish.** It says no *new* module is undocumented. It says
nothing about the 20 that are: each is public surface a reader cannot look up.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG = REPO_ROOT / "agentteams"
DOCS = REPO_ROOT / "docs_src" / "api-reference"

#: Modules with no `docs_src/api-reference/` page. Verified 2026-08-03, after documenting
#: `template_pins` and `front_matter_reconcile` — so this count arrived falling, not merely
#: frozen. LOWER it as pages land; never add an entry to make a failure go away.
_UNDOCUMENTED: frozenset[str] = frozenset({
    "advisory", "ai_bad_habits", "atomicio", "audit_agent_contract", "audit_types",
    "backup", "budget", "capability_hints", "errors", "front_matter_merge",
    "graph_inputs", "interop_helpers", "recipe_fields", "security_feed_render",
    "stale_detector", "stale_remediate", "svg_render", "tool_metadata_catalog",
    "unfenced", "vscode_tasks", "yaml_frontmatter",
})


def _undocumented() -> set[str]:
    modules = {p.stem for p in PKG.glob("*.py") if not p.stem.startswith("_")}
    documented = {p.stem.replace("-", "_") for p in DOCS.glob("*.md")}
    return modules - documented


def test_no_new_module_arrives_undocumented() -> None:
    """The ratchet."""
    new = _undocumented() - _UNDOCUMENTED
    assert not new, (
        f"module(s) added without a docs_src/api-reference/ page: {sorted(new)}. Add one — the "
        "page name uses hyphens where the module uses underscores — or add the module to "
        "_UNDOCUMENTED with a reason."
    )


def test_the_baseline_has_no_stale_entries() -> None:
    """A module that gained a page must leave the baseline, or it hides the next regression."""
    stale = sorted(_UNDOCUMENTED - _undocumented())
    assert not stale, (
        f"these are documented now and should leave _UNDOCUMENTED: {stale}"
    )


def test_the_measurement_is_not_vacuous() -> None:
    """A glob regression would empty both sides and pass forever."""
    modules = {p.stem for p in PKG.glob("*.py") if not p.stem.startswith("_")}
    pages = list(DOCS.glob("*.md"))
    assert len(modules) >= 40, f"only {len(modules)} modules found; the package walk regressed"
    assert len(pages) >= 40, f"only {len(pages)} api-reference pages found; the doc walk regressed"
