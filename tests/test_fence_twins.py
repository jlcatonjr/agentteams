"""Unfenced pre-fencing twins: drop only on proof, keep and flag everything else.

A team rendered before its template fenced a section carries that section unfenced; the merge
then adds the fenced block and keeps the old copy (everything outside a fence is preserved). These
tests pin what ``agentteams.fence_twins`` may delete: a twin equal (whitespace aside) to a past
template rendering resolved with today's placeholders, or to the fenced copy itself — nothing else.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams import emit, fence_twins
from agentteams.fence_twins import TwinSource
from agentteams.fences import _extract_fenced_regions, _merge_fenced_content

FIXTURES = Path(__file__).parent / "fixtures" / "fence_twins"
_SHIPPED_CORPUS = fence_twins.load_corpus  # captured before the autouse fixture swaps it

TEMPLATE = (
    "# X — {PROJECT_NAME}\n\n"
    "<!-- AGENTTEAMS:BEGIN rules v=1 -->\n## Rules\n\n- New rule A for {PROJECT_NAME}.\n"
    "<!-- AGENTTEAMS:END rules -->\n\n"
    "<!-- AGENTTEAMS:BEGIN kept v=1 -->\n## Kept\n\nfenced.\n<!-- AGENTTEAMS:END kept -->\n"
)
OLD_SECTION = "## Rules\n\n- Old rule for {PROJECT_NAME}: you must never skip the review step here.\n\n"
CORPUS = {"domain/x.template.md": {"## Rules": [
    {"commit": "abc1234def", "path": "templates/domain/x.template.md", "text": OLD_SECTION}]}}
SOURCE = TwinSource("domain/x.template.md", {"PROJECT_NAME": "Acme"})
NOTES = "## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — project rules.\n"
RENDER = fence_twins.resolve_placeholders(TEMPLATE, SOURCE.placeholders) + "\n" + NOTES


def _deployed(twin: str = OLD_SECTION, notes: str = NOTES) -> str:
    """The render plus an unfenced pre-fencing twin sitting just above the notes region."""
    twin = fence_twins.resolve_placeholders(twin, SOURCE.placeholders)
    return fence_twins.resolve_placeholders(TEMPLATE, SOURCE.placeholders) + "\n" + twin + notes


@pytest.fixture(autouse=True)
def _synthetic_lineage(tmp_path, monkeypatch):
    """Point the module at a synthetic template library and corpus."""
    tdir = tmp_path / "templates"
    (tdir / "domain").mkdir(parents=True)
    (tdir / "domain" / "x.template.md").write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(fence_twins, "_TEMPLATES_DIR", tdir)
    monkeypatch.setattr(fence_twins, "load_corpus", lambda *a, **k: CORPUS)
    return tdir


def _merge(existing: str, render: str = RENDER, source: TwinSource | None = SOURCE, drop: bool = True):
    return _merge_fenced_content(render, existing, rel_path="x.agent.md", twin_source=source, twin_drop=drop)


# --- drop on proof -----------------------------------------------------------------------------

def test_a_twin_identical_to_a_historical_render_is_dropped():
    res = _merge(_deployed())
    assert "Old rule for Acme" not in res.merged_content
    assert res.merged_content.count("## Rules") == 1
    assert NOTES in res.merged_content, "the user region after the twin must survive"
    assert "rules" in res.twins_dropped and "Old rule for Acme" in res.twins_dropped["rules"]
    notice = " ".join(res.duplicate_section_notices)
    assert "twin dropped" in notice and "abc1234def" in notice
    assert "retired by template" in notice, "a dropped rule line the template retired must be named"


def test_whitespace_differences_do_not_block_a_drop():
    res = _merge(_deployed(twin=OLD_SECTION.replace("\n\n- Old", "\n- Old").replace("here.", "here.   ")))
    assert "rules" in res.twins_dropped


def test_a_twin_identical_to_the_fenced_copy_is_dropped_without_any_source():
    twin = "## Rules\n\n- New rule A for Acme.\n\n"
    res = _merge(_deployed(twin=twin), source=None)
    assert res.merged_content.count("New rule A for Acme") == 1
    assert "identical copy" in " ".join(res.duplicate_section_notices)


def test_dropped_twin_goes_to_a_lost_sidecar_through_emit(tmp_path):
    out = tmp_path / "agents"
    out.mkdir()
    (out / "x.agent.md").write_text(_deployed(), encoding="utf-8")
    backup = tmp_path / "backup"
    result = emit.emit_all([("x.agent.md", RENDER)], output_dir=out, merge=True, yes=True,
                           backup_path=backup, twin_sources={"x.agent.md": SOURCE})
    assert "Old rule for Acme" not in (out / "x.agent.md").read_text(encoding="utf-8")
    sidecar = backup / "x.agent.md.lost.unfenced-rules.md"
    assert sidecar.exists() and "Old rule for Acme" in sidecar.read_text(encoding="utf-8")
    assert any("recovery:" in n and "unfenced-rules" in n for n in result.notices)


def test_a_second_merge_is_a_no_op():
    first = _merge(_deployed()).merged_content
    second = _merge(first)
    assert second.merged_content == first
    assert not second.twins_dropped and not second.twins_kept
    assert second.duplicate_section_notices == []


# --- keep on doubt -----------------------------------------------------------------------------

@pytest.mark.parametrize("twin", [
    OLD_SECTION.replace("review step", "review steps"),                       # one-word edit
    OLD_SECTION + "- CH-08 Every claim needs evidence; never skip it ok.\n\n",  # hand-added rows
])
def test_an_edited_twin_is_kept_and_flagged(twin):
    res = _merge(_deployed(twin=twin))
    assert "Old rule for Acme" in res.merged_content
    assert res.twins_kept == ["## Rules"] and not res.twins_dropped
    assert fence_twins.KEPT_PREFIX in " ".join(res.duplicate_section_notices)


def test_a_twin_with_no_following_boundary_is_kept():
    deployed = fence_twins.resolve_placeholders(TEMPLATE + "\n" + OLD_SECTION, SOURCE.placeholders)
    res = _merge(deployed, render=fence_twins.resolve_placeholders(TEMPLATE, SOURCE.placeholders))
    assert "Old rule for Acme" in res.merged_content
    assert "no following heading" in " ".join(res.duplicate_section_notices)


def test_a_heading_that_appears_twice_unfenced_is_kept():
    res = _merge(_deployed(twin=OLD_SECTION + OLD_SECTION))
    assert res.merged_content.count("Old rule for Acme") == 2
    assert "appears 2 times unfenced" in " ".join(res.duplicate_section_notices)


def test_a_changed_placeholder_value_keeps_the_twin():
    deployed = _deployed().replace("Old rule for Acme", "Old rule for AcmeCorp")
    res = _merge(deployed)
    assert "Old rule for AcmeCorp" in res.merged_content and res.twins_kept == ["## Rules"]


def test_the_adapter_guard_refuses_a_transformed_section():
    """When the fresh fenced body is not the template section resolved, past renders are not comparable."""
    render = RENDER.replace("- New rule A for Acme.", "- New rule A for Acme (adapter-rewritten).")
    res = _merge(_deployed(), render=render)
    assert "Old rule for Acme" in res.merged_content
    assert "adapter or enrichment" in " ".join(res.duplicate_section_notices)


def test_no_backup_dir_means_nothing_is_dropped():
    res = _merge(_deployed(), drop=False)
    assert "Old rule for Acme" in res.merged_content
    assert "dropping is disabled" in " ".join(res.duplicate_section_notices)


def test_a_span_holding_a_user_editable_region_is_kept():
    twin = OLD_SECTION + "> ⚙️ **USER-EDITABLE** — mine.\n\n"
    res = _merge(_deployed(twin=twin))
    assert "Old rule for Acme" in res.merged_content
    assert "user-editable" in " ".join(res.duplicate_section_notices)


def test_text_after_the_twin_without_a_heading_is_never_taken():
    """An operator note appended under the twin (no heading) is part of its span, so equality fails."""
    res = _merge(_deployed(twin=OLD_SECTION + "Operator note: keep the old rule too.\n\n"))
    assert "Operator note" in res.merged_content and "Old rule for Acme" in res.merged_content


def test_nested_twins_of_two_fences_are_refused():
    template = TEMPLATE.replace("<!-- AGENTTEAMS:BEGIN kept v=1 -->\n## Kept",
                                "<!-- AGENTTEAMS:BEGIN kept v=1 -->\n### Sub")
    render = fence_twins.resolve_placeholders(template, SOURCE.placeholders) + "\n" + NOTES
    deployed = render.replace(NOTES, "## Rules\n\n- New rule A for Acme.\n\n### Sub\n\nfenced.\n\n" + NOTES)
    res = _merge(deployed, render=render, source=None)
    assert "overlaps" in " ".join(res.duplicate_section_notices)
    assert deployed.count("### Sub") == res.merged_content.count("### Sub")


def test_detection_runs_on_a_file_already_carrying_both_copies():
    """The original notice fired only while the fence was being added; afterwards it went silent."""
    affected = _deployed(twin=OLD_SECTION.replace("review step", "review gate"))
    res = _merge(affected)
    assert res.sections_added == []
    assert res.twins_kept == ["## Rules"]


def test_headings_inside_a_code_block_are_not_twins():
    twin = "```\n## Rules\n```\n\n"
    res = _merge(_deployed(twin="## Notes on code\n\n" + twin))
    assert res.twins_kept == [] and not res.twins_dropped
    assert "```\n## Rules\n```" in res.merged_content


# --- Goose: the instructions block is indented two spaces --------------------------------------

def test_goose_researchteam_fixture_drops_template_stub_and_keeps_hand_rows(monkeypatch):
    """researchteam's technical-validator recipe: a bare ``## Invariant Core`` stub from the
    pre-fencing template (droppable) and an ``## Accuracy Rules`` twin carrying hand-written
    CH-08..10 rows (must be kept). Real shipped corpus; frozen copy of the current template."""
    monkeypatch.setattr(fence_twins, "load_corpus", _SHIPPED_CORPUS)
    monkeypatch.setattr(fence_twins, "_TEMPLATES_DIR", FIXTURES / "templates")
    recipe = (FIXTURES / "researchteam-technical-validator.goose.yaml").read_text(encoding="utf-8")
    source = TwinSource("domain/technical-validator.template.md", {"PROJECT_NAME": "ResearchTeam"})
    res = _merge_fenced_content(recipe, recipe, rel_path="technical-validator.yaml",
                                twin_source=source, twin_drop=True)
    merged = res.merged_content
    assert list(res.twins_dropped) == ["invariant_core"]
    assert "## Accuracy Rules" in res.twins_kept
    assert "**CH-08**" in merged and "**CH-10**" in merged, "hand-written rows must survive"
    assert merged.count("  ## Invariant Core\n") == 1
    # The YAML stays a recipe: everything outside the instructions block is byte-identical, and every
    # line inside it keeps its two-space indent.
    head, body = merged.split("instructions: |\n", 1)
    assert head == recipe.split("instructions: |\n", 1)[0]
    assert body.split("\nextensions:")[1] == recipe.split("\nextensions:")[1]
    assert all(not line.strip() or line.startswith("  ") for line in body.split("\nextensions:")[0].splitlines())
    again = _merge_fenced_content(merged, merged, rel_path="technical-validator.yaml",
                                  twin_source=source, twin_drop=True)
    assert again.merged_content == merged and not again.twins_dropped


def test_goose_duplicate_detection_reads_the_dedented_block():
    recipe = (FIXTURES / "researchteam-technical-validator.goose.yaml").read_text(encoding="utf-8")
    from agentteams.fences import _detect_duplicate_sections

    notices = _detect_duplicate_sections(recipe, [])
    assert any("'## Accuracy Rules'" in n for n in notices)


def test_dedented_view_is_identity_for_markdown():
    assert fence_twins.dedented_view(RENDER) == RENDER


def test_fence_leading_headings_reads_the_first_line_only():
    leads = fence_twins.fence_leading_headings(RENDER)
    assert leads == {"## Rules": ["rules"], "## Kept": ["kept"]}
    assert isinstance(_extract_fenced_regions(RENDER), dict)


# --- review follow-ups: refusal coverage, dry-run fidelity ----------------------------------------

@pytest.mark.parametrize("extra", [
    "## Project-Specific Rules mention: Project-Specific\n\n",
    "'''\n\n",
    "Quoted marker: <!-- AGENTTEAMS-BRIDGE:BEGIN x -->\n\n",
])
def test_spans_holding_user_regions_delimiters_or_markers_are_kept(extra):
    res = _merge(_deployed(twin=OLD_SECTION.replace("\n\n- Old", "\n\n" + extra + "- Old")))
    assert "Old rule for Acme" in res.merged_content and res.twins_kept == ["## Rules"]


def test_a_heading_leading_two_fences_is_kept():
    render = RENDER.replace("## Kept\n\nfenced.", "## Rules\n\nfenced.")
    res = _merge(_deployed().replace("## Kept\n\nfenced.", "## Rules\n\nfenced."), render=render, source=None)
    assert "leads 2 fences" in " ".join(res.duplicate_section_notices)


def test_a_fence_missing_from_the_render_is_kept():
    render = RENDER.replace("<!-- AGENTTEAMS:BEGIN rules v=1 -->\n## Rules\n\n- New rule A for Acme.\n"
                            "<!-- AGENTTEAMS:END rules -->\n", "")
    res = _merge(_deployed(), render=render)
    assert "Old rule for Acme" in res.merged_content


def test_a_corrupt_corpus_keeps_every_twin(tmp_path, monkeypatch):
    bad = tmp_path / "corpus.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _SHIPPED_CORPUS.__wrapped__(bad) == {}
    monkeypatch.setattr(fence_twins, "load_corpus", lambda *a, **k: {})
    assert _merge(_deployed()).twins_kept == ["## Rules"]


@pytest.mark.parametrize("twin_drop, dropped", [(True, True), (False, False)])
def test_dry_run_previews_what_the_real_run_will_do(tmp_path, twin_drop, dropped):
    out = tmp_path / "agents"
    out.mkdir()
    (out / "x.agent.md").write_text(_deployed(), encoding="utf-8")
    result = emit.emit_all([("x.agent.md", RENDER)], output_dir=out, merge=True, yes=True, dry_run=True,
                           twin_sources={"x.agent.md": SOURCE}, twin_drop=twin_drop)
    assert any(fence_twins.DROPPED_PREFIX + ":" in n for n in result.notices) is dropped
