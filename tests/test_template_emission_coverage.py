"""test_template_emission_coverage.py — every template must be reachable.

``agentteams/templates/`` is packaged wholesale, so a template that no output
plan reaches still ships in the wheel and still gets swept by drift detection —
it just never renders into a team. Nothing detected that state: the existing
orphan advisory (``build_team._report_orphan_reference_docs``) runs on the
*output* side, finding emitted files the plan no longer produces, which is the
opposite direction.

This check exists because that gap hid a real defect.
``code-hygiene-mechanization.reference.md`` sat in ``templates/domain/`` as the
only domain template absent from ``output_plan.py``, and a session audit went on
to assert it "ships to consumers" as a finding's severity basis — a claim nothing
could have contradicted. It has since been refiled to ``references/``.

Direction of the two assertions:

* **template on disk → reachable by the plan.** An unreachable template is dead
  weight, or misfiled, or a registration someone forgot.
* **template named by the plan → present on disk.** A plan entry naming a
  nonexistent template is a render failure waiting for the right input.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from agentteams.frameworks import registry
from agentteams.output_plan import _plan_output_files

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "agentteams/templates"

# Templates that no output plan reaches, and why. A `dict` rather than a set so
# an entry cannot be added without stating a reason; `test_allowlist_has_no_stale_entries`
# keeps it from becoming a place failures go to die.
#: Shared reason for the three CSV header templates. One constant rather than a
#: "see above" cross-reference in each entry: a cross-reference stops being true
#: when the entry it points at changes, and `test_allowlist_reasons_are_substantive`
#: rejects it for that reason.
_CSV_TEMPLATES_ARE_KNOWN_DEAD = (
    "Known-dead, deliberately retained. agentteams/liaison_logs.py:51 records "
    "that the three *.csv.template files have zero readers anywhere in the "
    "package and that the *_HEADERS constant lists are the only source of truth "
    "for the header row — a drift risk, not a pattern to extend. Not pending "
    "registration: registering them would create the second source of truth the "
    "comment warns against."
)

_UNEMITTED_ALLOWLIST: dict[str, str] = {
    "universal/adjacent-repos-changelog.csv.template": _CSV_TEMPLATES_ARE_KNOWN_DEAD,
    "universal/adjacent-repos-coordination-log.csv.template": _CSV_TEMPLATES_ARE_KNOWN_DEAD,
    "universal/security-decisions.log.csv.template": _CSV_TEMPLATES_ARE_KNOWN_DEAD,
}


def _all_templates() -> set[str]:
    """Every template file under the templates tree, relative and POSIX."""
    return {
        p.relative_to(TEMPLATES_DIR).as_posix()
        for p in TEMPLATES_DIR.rglob("*.template*")
        if p.is_file()
    }


def _frameworks() -> list[str]:
    return list(getattr(registry, "FRAMEWORKS", None) or registry._ADAPTERS)


def _selectable_archetypes() -> list[str]:
    """Archetype slugs the analyzer can select, read from ``analyze.py`` itself.

    **Deriving these from the templates directory would make the check
    circular** — every file dropped into ``domain/`` would become a "valid
    archetype" and so trivially reachable, which is the first version of this
    check and it passed a planted orphan. The analyzer can only select an
    archetype whose slug some code path names, so the string literals in
    ``analyze.py`` are the independent source: keyword triggers, force-appends,
    implications, module-doc pairs and the always-included list.

    ``selected_archetypes`` in a project description is passed through
    unvalidated, so a hand-written override could name anything. That is not a
    reachability path worth honouring: the render would fail on the missing
    template.
    """
    source = (REPO_ROOT / "agentteams/analyze.py").read_text(encoding="utf-8")
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    domain = TEMPLATES_DIR / "domain"
    return sorted(
        slug
        for slug in (
            p.name[: -len(".template.md")]
            for p in domain.glob("*.template.md")
            if not p.name.endswith(
                (".doc.template.md", "-reference.template.md", ".reference.template.md")
            )
        )
        if slug in literals
    )


def _tool_categories() -> list[str]:
    """Tool categories the pipeline can produce, read from ``analyze.py``.

    ``_SPECIALIST_CATEGORIES`` is the closed set that earns a tool a doc, plus
    the ``"other"`` default. Also derived from source rather than from the
    ``tool-*.doc.template.md`` filenames, for the same anti-circularity reason.
    """
    from agentteams import analyze

    return sorted(analyze._SPECIALIST_CATEGORIES | {"other"})


def _plan_entries() -> list[dict[str, object]]:
    """Every plan entry the pipeline can produce, over every input axis."""
    tool_agents = [
        {"slug": f"tool-example-{cat}", "tool_category": cat} for cat in _tool_categories()
    ]
    entries: list[dict[str, object]] = []
    for framework in _frameworks():
        entries.extend(
            _plan_output_files(
                _selectable_archetypes(),
                tool_agents,
                [{"slug": "some-library"}],
                [{"slug": "some-component"}],
                framework,
            )
        )
    return entries


def _reachable_templates() -> set[str]:
    """Template paths the plan names *and* that exist on disk.

    A named-but-absent path is not reachability — the plan tolerates a missing
    ``tool-<category>.doc.template.md`` by falling back to
    ``tool-specific.doc.template.md``, so both are collected and only the ones
    present count.
    """
    on_disk = _all_templates()
    reached: set[str] = set()
    for entry in _plan_entries():
        for key in ("template", "fallback_template"):
            value = entry.get(key)
            if isinstance(value, str) and value in on_disk:
                reached.add(value)
    return reached


def test_every_template_is_reachable_from_the_output_plan() -> None:
    """No template is dead weight or misfiled without saying so."""
    orphans = sorted(_all_templates() - _reachable_templates() - set(_UNEMITTED_ALLOWLIST))
    assert not orphans, (
        f"Template(s) no output plan reaches: {orphans}. Either register them in "
        "agentteams/output_plan.py, refile them out of agentteams/templates/ if the "
        "content is repo-local, or add them to _UNEMITTED_ALLOWLIST with a reason."
    )


def test_every_planned_entry_resolves_to_a_template() -> None:
    """No plan entry can end up with nothing to render.

    An entry is satisfied by its ``template`` or, where it declares one, its
    ``fallback_template`` — that pairing is how an unknown tool category degrades
    to the generic doc instead of failing. An entry satisfied by neither is a
    render failure waiting for the right input.
    """
    on_disk = _all_templates()
    unresolvable = sorted(
        {
            str(entry.get("template"))
            for entry in _plan_entries()
            # A blank template marks a post-render artifact (graphs, SETUP-REQUIRED).
            if entry.get("template")
            and str(entry["template"]) not in on_disk
            and str(entry.get("fallback_template") or "") not in on_disk
        }
    )
    assert not unresolvable, (
        f"output_plan.py names template(s) with no file and no usable fallback: {unresolvable}"
    )


@pytest.mark.parametrize("rel", sorted(_UNEMITTED_ALLOWLIST))
def test_allowlist_has_no_stale_entries(rel: str) -> None:
    """An allowlisted template that became reachable, or vanished, must be delisted.

    Same discipline as ``test_length_allowlist_has_no_stale_entries``: the
    exemption list is the part of a check that rots, so it is checked too.
    """
    assert (TEMPLATES_DIR / rel).exists(), (
        f"_UNEMITTED_ALLOWLIST names {rel}, which no longer exists. Remove the entry."
    )
    assert rel not in _reachable_templates(), (
        f"{rel} is now reachable from the output plan. Remove it from "
        "_UNEMITTED_ALLOWLIST so the check covers it."
    )


def test_allowlist_reasons_are_substantive() -> None:
    """Guard the guard: a one-word reason defeats the point of using a dict."""
    thin = {rel: why for rel, why in _UNEMITTED_ALLOWLIST.items() if len(why.split()) < 8}
    assert not thin, f"_UNEMITTED_ALLOWLIST entries need a real reason: {sorted(thin)}"
