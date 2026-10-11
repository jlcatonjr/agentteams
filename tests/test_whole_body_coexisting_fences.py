"""A legacy whole-body ``content`` fence is retired even when the file carries other fences too.

**The defect (2026-10-11 fleet audit, round 2).** The migration fired only when ``content`` was the
file's ONLY fence. Deployed quality-auditor, tool-doc-researcher and conflict-resolution files also carry
``memory_index_consultation`` (an earlier merge appended it), so the merge added ``invariant_core``,
``output_format`` and the rest beside the stale block: every section twice, both copies inside fences, in
``.github/agents`` markdown and Goose recipes alike, across 12 repositories. The duplicate notice then
claimed the old copy was "unfenced", and fleet filed it as a shrink notice.

The fixtures are anonymised excerpts of the real before/after files (ohio-collectors' and a no-remote
research repo's quality-auditor), with bodies shortened.
"""

from __future__ import annotations

from agentteams import fence_twins, fleet
from agentteams.fence_twins import TwinSource
from agentteams.fences import (
    _is_whole_body_migration,
    _merge_fenced_content,
    _shrink_notice_lines,
    unfenced_lines,
)
from agentteams.whole_body_migration import DUPLICATE_PREFIX, applies, duplicate_notices

_FM = (
    "---\n"
    "name: Quality Auditor — Acme\n"
    "description: \"Read-only audit agent for Acme\"\n"
    "tools: ['read', 'search']\n"
    "handoffs:\n"
    "  - label: Route Corrections to Primary Producer\n"
    "    agent: primary-producer\n"
    "    prompt: \"Audit findings attached.\"\n"
    "    send: false\n"
    "  - label: Hand-written Acme Escalation\n"
    "    agent: acme-frontend\n"
    "    prompt: \"Escalate UI findings.\"\n"
    "    send: false\n"
    "---\n"
)
_INTRO = "# Quality Auditor — Acme\n\nYou perform read-only quality audits on deliverables in Acme.\n\n---\n\n"
_OLD_SECTIONS = (
    "## Invariant Core\n\n> ⛔ **Do not modify or omit.**\n\n"
    "## Defect Taxonomy\n\n| Code | Category |\n|------|----------|\n| **Q-STR** | Structural |\n\n"
    "## Output Format\n\nReturn a ranked findings list.\n\n"
    "## Boundary Rules\n\n- **Read-only.** Do not edit any deliverable file.\n"
)
_MEMORY_OLD = (
    "<!-- AGENTTEAMS:BEGIN memory_index_consultation v=3 -->\n"
    "## Memory-index consultation\n\nConsult the index (old wording).\n"
    "<!-- AGENTTEAMS:END memory_index_consultation -->\n"
)
_MEMORY_NEW = (
    "<!-- AGENTTEAMS:BEGIN memory_index_consultation v=4 -->\n"
    "## Memory-index consultation\n\nConsult the index (new wording).\n"
    "<!-- AGENTTEAMS:END memory_index_consultation -->\n"
)
_NOTES = (
    "## Project-Specific Notes\n\n"
    "> ⚙️ **USER-EDITABLE** — project-specific rules, overrides, and extensions for this agent.\n\n"
    "- Acme audits run on Fridays.\n"
)


def _fence(sid: str, body: str) -> str:
    return f"<!-- AGENTTEAMS:BEGIN {sid} v=1 -->\n{body}<!-- AGENTTEAMS:END {sid} -->\n"


def _deployed(sections: str = _OLD_SECTIONS, notes: str = _NOTES) -> str:
    """The deployed shape: front matter, the whole-body fence, an appended memory fence, notes."""
    return _FM + "<!-- AGENTTEAMS:BEGIN content v=1 -->\n" + _INTRO + sections + \
        "<!-- AGENTTEAMS:END content -->\n\n" + _MEMORY_OLD + "\n" + notes


def _render() -> str:
    """The current template's render: named sections, memory fence in the middle, boilerplate notes."""
    fm = _FM.replace(
        "  - label: Hand-written Acme Escalation\n    agent: acme-frontend\n"
        "    prompt: \"Escalate UI findings.\"\n    send: false\n", "")
    return (
        fm + "\n" + _INTRO
        + _fence("invariant_core", "## Invariant Core\n\n> ⛔ **Do not modify or omit.** (v2)\n") + "\n"
        + _fence("defect_taxonomy", "## Defect Taxonomy\n\n| Code | Category |\n|------|----------|\n"
                 "| **Q-STR** | Structural |\n| **Q-LGC** | Logical |\n") + "\n"
        + _MEMORY_NEW + "\n"
        + _fence("output_format", "## Output Format\n\nReturn a ranked findings list, HIGH first.\n") + "\n"
        + _fence("boundary_rules", "## Boundary Rules\n\n- **Read-only.** Do not edit any deliverable file.\n")
        + "\n## Project-Specific Notes\n\n> ⚙️ **USER-EDITABLE** — project-specific rules.\n"
    )


def _recipe(body: str) -> str:
    """Wrap a markdown body as a Goose recipe's ``instructions: |`` block."""
    indented = "".join(("  " + line) if line.strip() else line for line in body.splitlines(keepends=True))
    return ('version: "1.0.0"\ntitle: "Quality Auditor — Acme"\ndescription: "Read-only audit agent"\n'
            "instructions: |\n" + indented + "extensions:\n  - type: builtin\n    name: developer\n")


def _body(text: str) -> str:
    return text[text.index("---\n", 4) + 4:]


def _headings(text: str) -> list[str]:
    return [line.strip() for line in fence_twins.dedented_view(text).splitlines() if line.strip().startswith("## ")]


# --- the predicate -----------------------------------------------------------------------------------

def test_the_predicate_accepts_content_beside_fences_the_render_still_has():
    assert applies({"content": "", "memory_index_consultation": ""},
                   {"invariant_core": "", "memory_index_consultation": ""})
    assert _is_whole_body_migration({"content": "", "memory_index_consultation": ""},
                                    {"invariant_core": "", "memory_index_consultation": ""})


def test_the_predicate_refuses_a_second_orphaned_fence_and_an_unsplit_render():
    assert not applies({"content": "", "retired": ""}, {"invariant_core": ""})
    assert not applies({"content": "", "memory_index_consultation": ""}, {"content": "", "memory_index_consultation": ""})
    assert not applies({"content": ""}, {})


# --- markdown -----------------------------------------------------------------------------------------

def test_markdown_every_section_appears_once_and_content_is_gone():
    result = _merge_fenced_content(_render(), _deployed())
    merged = result.merged_content
    assert result.migrated
    assert "AGENTTEAMS:BEGIN content" not in merged
    heads = _headings(merged)
    assert len(heads) == len(set(heads)), heads
    assert result.duplicate_section_notices == []
    assert "(v2)" in merged and "Q-LGC" in merged, "the template's new sections land"


def test_markdown_the_coexisting_fence_is_updated_in_place_not_duplicated():
    merged = _merge_fenced_content(_render(), _deployed()).merged_content
    assert merged.count("BEGIN memory_index_consultation") == 1
    assert "(new wording)" in merged and "(old wording)" not in merged


def test_markdown_front_matter_and_notes_are_the_projects():
    """The old branch replaced everything up to the last fence, front matter included."""
    merged = _merge_fenced_content(_render(), _deployed()).merged_content
    assert "Hand-written Acme Escalation" in merged
    assert "Acme audits run on Fridays." in merged
    assert merged.count("## Project-Specific Notes") == 1


def test_markdown_the_migration_is_reported_and_the_old_body_kept_for_the_sidecar():
    result = _merge_fenced_content(_render(), _deployed())
    notice = result.shrink_notices[0]
    assert notice.startswith("fence 'content': this file predates its template being split")
    assert "memory_index_consultation" in notice and "updated in place" in notice
    assert result.sections_orphaned[0] == "content"
    assert set(result.sections_added) == {"invariant_core", "defect_taxonomy", "output_format", "boundary_rules"}
    assert "## Defect Taxonomy" in result.lost_fence_bodies["content"]


def test_markdown_is_idempotent():
    once = _merge_fenced_content(_render(), _deployed()).merged_content
    again = _merge_fenced_content(_render(), once)
    assert again.merged_content == once
    assert not again.migrated
    assert again.duplicate_section_notices == []


# --- goose --------------------------------------------------------------------------------------------

def _goose_deployed(sections: str = _OLD_SECTIONS) -> str:
    return _recipe(_body(_deployed(sections)))


def _goose_render() -> str:
    return _recipe(_body(_render()))


def test_goose_every_section_appears_once_and_the_recipe_keeps_its_shape():
    result = _merge_fenced_content(_goose_render(), _goose_deployed(), rel_path="quality-auditor.yaml")
    merged = result.merged_content
    assert result.migrated
    assert "AGENTTEAMS:BEGIN content" not in merged
    heads = _headings(merged)
    assert len(heads) == len(set(heads)), heads
    assert merged.count("BEGIN memory_index_consultation") == 1
    assert merged.startswith('version: "1.0.0"\ntitle: "Quality Auditor — Acme"')
    assert merged.rstrip().endswith("name: developer")
    block = merged[merged.index("instructions: |\n") + len("instructions: |\n"):merged.index("extensions:")]
    assert all(not line.strip() or line.startswith("  ") for line in block.splitlines()), "a line left the block"
    assert "Acme audits run on Fridays." in merged


def test_goose_is_idempotent():
    once = _merge_fenced_content(_goose_render(), _goose_deployed(), rel_path="quality-auditor.yaml").merged_content
    again = _merge_fenced_content(_goose_render(), once, rel_path="quality-auditor.yaml")
    assert again.merged_content == once and not again.migrated


def test_goose_hand_written_text_in_a_now_fenced_section_is_flagged():
    edited = _OLD_SECTIONS.replace("Return a ranked findings list.\n",
                                   "Return a ranked findings list.\n\nAcme: always cite the ticket id.\n")
    result = _merge_fenced_content(_goose_render(), _goose_deployed(edited), rel_path="quality-auditor.yaml")
    assert "NOT KEPT" in result.shrink_notices[0] and "'## Output Format'" in result.shrink_notices[0]
    assert "always cite the ticket id" in result.lost_fence_bodies["content"]
    assert _headings(result.merged_content).count("## Output Format") == 1


# --- hand-written text inside the legacy block -----------------------------------------------------------

def test_a_section_the_template_does_not_have_is_carried_out_unfenced():
    sections = _OLD_SECTIONS + "\n## Acme Escalation Ladder\n\n1. Page the on-call auditor.\n"
    result = _merge_fenced_content(_render(), _deployed(sections))
    merged = result.merged_content
    assert "## Acme Escalation Ladder" in merged and "Page the on-call auditor." in merged
    assert "carried '## Acme Escalation Ladder'" in result.shrink_notices[0]
    assert any("Page the on-call auditor." in line for line in unfenced_lines(merged)), "carried unfenced"


def test_a_user_editable_region_inside_the_legacy_block_is_preserved():
    sections = _OLD_SECTIONS + (
        "\n## Acme Rules\n\n> ⚙️ **USER-EDITABLE** — Acme-only rules.\n\n- Never audit drafts.\n")
    result = _merge_fenced_content(_render(), _deployed(sections))
    assert "Never audit drafts." in result.merged_content
    assert "'## Acme Rules'" in result.shrink_notices[0]


def test_an_edited_copy_of_a_now_fenced_section_is_not_kept_but_named_and_saved():
    edited = _OLD_SECTIONS.replace("- **Read-only.** Do not edit any deliverable file.\n",
                                   "- **Read-only.** Do not edit any deliverable file.\n- Acme: never audit drafts.\n")
    result = _merge_fenced_content(_render(), _deployed(edited))
    assert "Acme: never audit drafts." not in result.merged_content, "keeping it would recreate the duplicate"
    assert "NOT KEPT" in result.shrink_notices[0] and "'## Boundary Rules'" in result.shrink_notices[0]
    assert "Acme: never audit drafts." in result.lost_fence_bodies["content"]


def test_an_edited_section_the_template_leaves_unfenced_keeps_the_deployed_copy():
    deployed = _deployed().replace("You perform read-only quality audits on deliverables in Acme.",
                                   "You perform read-only quality audits on deliverables in Acme, weekly.")
    result = _merge_fenced_content(_render(), deployed)
    assert "deliverables in Acme, weekly." in result.merged_content
    assert result.merged_content.count("# Quality Auditor — Acme") == 1
    assert "kept the deployed copy of '# Quality Auditor — Acme'" in result.shrink_notices[0]


def test_a_section_holding_a_managed_marker_is_never_re_emitted():
    sections = _OLD_SECTIONS + "\n## Quoted\n\nSee <!-- AGENTTEAMS-BRIDGE:BEGIN x v=1 --> here.\n"
    result = _merge_fenced_content(_render(), _deployed(sections))
    assert "AGENTTEAMS-BRIDGE:BEGIN x" not in result.merged_content
    assert "NOT KEPT" in result.shrink_notices[0] and "'## Quoted'" in result.shrink_notices[0]


def test_a_past_template_rendering_of_a_now_fenced_section_is_dropped_quietly(monkeypatch):
    """Lineage proof: the old Output Format equals what an earlier template emitted, so it is not flagged."""
    edited = _OLD_SECTIONS.replace("Return a ranked findings list.\n", "Return a ranked list (2025 wording).\n")
    corpus = {"domain/quality-auditor.template.md": {"## Output Format": [
        {"commit": "abc", "path": "t", "text": "## Output Format\n\nReturn a ranked list (2025 wording).\n\n"}],
        "## Invariant Core": [{"commit": "abc", "path": "t", "text": "## Invariant Core\n\n> ⛔ **Do not modify or omit.**\n"}]}}
    monkeypatch.setattr(fence_twins, "load_corpus", lambda *a, **k: corpus)
    source = TwinSource("domain/quality-auditor.template.md", {"PROJECT_NAME": "Acme"}, "quality-auditor.agent.md")
    result = _merge_fenced_content(_render(), _deployed(edited), twin_source=source)
    assert "NOT KEPT" not in result.shrink_notices[0]
    assert "2025 wording" not in result.merged_content


def test_shrink_policy_allow_still_reports_the_migration_and_writes_the_sidecar(tmp_path):
    result = _merge_fenced_content(_render(), _deployed())
    lines = _shrink_notice_lines("quality-auditor.agent.md", result, "allow", tmp_path)
    assert any("predates its template" in line for line in lines)
    assert (tmp_path / "quality-auditor.agent.md.lost.content.md").is_file()


# --- inside-fence duplicates are reported, accurately ---------------------------------------------------

def test_inside_fence_duplicates_are_reported_without_claiming_an_unfenced_copy():
    """The staged fleet shape: the predicate failed and both copies sat inside fences."""
    text = _deployed() + _fence("invariant_core", "## Invariant Core\n\nnew\n")
    notices = duplicate_notices(text, [])
    assert notices and all(n.startswith(DUPLICATE_PREFIX) for n in notices)
    assert any("'## Invariant Core'" in n and "every copy inside a fence (content, invariant_core)" in n
               for n in notices)
    assert not any("unfenced" in n for n in notices)
    added = duplicate_notices(text, ["invariant_core"])
    assert added and not any("carried this section unfenced" in n for n in added)


def test_a_file_the_predicate_refuses_still_surfaces_its_duplicates():
    """A second orphaned fence blocks the migration; the ordinary merge then names the duplicates."""
    deployed = _deployed() + "\n" + _fence("retired_thing", "## Retired\n\nx\n")
    result = _merge_fenced_content(_render(), deployed)
    assert not result.migrated
    assert any("every copy inside a fence" in n for n in result.duplicate_section_notices)


def test_a_heading_repeated_inside_one_fence_is_the_templates_own_structure():
    text = _fence("a", "## A\n\n### Step\n\nx\n\n### Step\n\ny\n")
    assert duplicate_notices(text, []) == []


def test_fleet_files_duplicates_as_their_own_review_class(tmp_path):
    out = ("    Notice: quality-auditor.agent.md: duplicate section '## Invariant Core': appears 2x, every copy "
           "inside a fence (content, invariant_core) — two template-owned blocks carry the same section.\n")
    cls = fleet._classify(tmp_path, "HEAD", "", out)
    assert cls["duplicates"] and cls["shrink_notices"] == []


def test_a_section_the_render_splits_around_a_fence_is_not_duplicated():
    """ohio's conflict-resolution: the render's ``## Decision Framework`` runs on after the memory fence.

    The deployed copy (kept: it differs from the template) already holds Steps 3-4, so the render's
    continuation after the fence must not add them a second time.
    """
    old = ("## Invariant Core\n\n> ⛔ **Do not modify or omit.**\n\n## Decision Framework\n\n### Step 1\n\nRead.\n\n"
           "### Step 3\n\nApply.\n\nAcme: log every ESCALATE in the tracker.\n")
    deployed = (_FM + "<!-- AGENTTEAMS:BEGIN content v=1 -->\n" + _INTRO + old + "<!-- AGENTTEAMS:END content -->\n\n"
                + _MEMORY_OLD + "\n" + _NOTES)
    render = (_FM + "\n" + _INTRO + _fence("invariant_core", "## Invariant Core\n\n> ⛔ **Do not modify or omit.**\n")
              + "\n## Decision Framework\n\n### Step 1\n\nRead.\n\n" + _MEMORY_NEW + "\n### Step 3\n\nApply.\n\n"
              + _fence("rules", "## Rules\n\n1. Read both sides.\n") + "\n" + _NOTES)
    result = _merge_fenced_content(render, deployed)
    heads = [h for h in fence_twins.dedented_view(result.merged_content).splitlines() if h.startswith("#")]
    assert heads.count("### Step 3") == 1, heads
    assert "Acme: log every ESCALATE in the tracker." in result.merged_content
    assert "kept the deployed copy of '## Decision Framework'" in result.shrink_notices[0]


def test_a_renamed_project_title_is_matched_by_position_not_duplicated():
    """A derived repo's deployed recipe carried the upstream project name in its title section."""
    deployed = _deployed().replace("# Quality Auditor — Acme", "# Quality Auditor — Upstream")
    result = _merge_fenced_content(_render(), deployed)
    titles = [h for h in result.merged_content.splitlines() if h.startswith("# Quality Auditor")]
    assert len(titles) == 1, titles
