"""A dropped twin must not take the template's unfenced blocks with it (2026-10-13 fleet audit).

navigator's pre-fencing ``## Invariant Core`` carried ``### Project Structure`` and ``### Workstream →
Source File Mapping``. Today's template fences only the head of that section and emits those two
blocks after the fence, unfenced. The twin pass bounded the twin at the next fence marker, so the
whole old section (blocks included) was dropped, and the merge never re-adds unfenced template text:
MarketForge, the Tracers goose recipe, sc_collectors / vk-address-enrichment ``.claude`` and
ProjectRepositories lost ``### Project Structure``. Where an edited Project Structure made the
whole twin unprovable, the twin was kept and navigator carried two ``## Invariant Core`` sections.

The whole-body migration lost the same blocks another way: once a deployed title replaced the
render's, every continuation of the title section after a fence was skipped, ``### Project
Structure`` included (portal, data-quality-monitor, vk-arcgis-map-tool, vk-infrastructure).

These tests run the real navigator template and the shipped lineage corpus. The twin is the
MarketForge base excerpt (template-generated values only).
"""

from __future__ import annotations

import re

from agentteams import fence_twins
from agentteams.emit import _normalize_generated_content
from agentteams.fence_twin_acks import TwinAck
from agentteams.fence_twins import TwinSource
from agentteams.fences import _merge_fenced_content

TEMPLATE = "universal/navigator.template.md"
VALUES = {"PROJECT_NAME": "Acme", "PRIMARY_OUTPUT_DIR": "src/", "FIGURES_DIR": "docs/figures/",
          "REFERENCE_DB_PATH": "N/A - no citation database configured for this project",
          "WORKSTREAM_SOURCE_MAP": "No components defined."}
SOURCE = TwinSource(TEMPLATE, VALUES)
RAW = (fence_twins._TEMPLATES_DIR / TEMPLATE).read_text(encoding="utf-8")
RENDER = fence_twins.resolve_placeholders(RAW, VALUES)

HEAD = """## Invariant Core

> ⛔ **Do not modify or omit.**

### Core Responsibilities

1. **Maintain the Project Map** — Keep `.github/agents/references/project-map.md` current whenever the project structure changes. Document: directory structure and purpose, deliverable files with status, references inventory, agent files.

2. **Answer Structural Queries** — When asked "where is X?" or "what contains Y?":
   - Check `.github/agents/references/project-map.md` first
   - If not found, search the file system
   - Return precise file path and relevant context

3. **Track Component Dependencies** — Each workstream depends on specific source files. Maintain this mapping in the project map. Flag broken dependencies immediately.

"""
STRUCTURE = """### Project Structure

**Primary output directory:** `src/`
**Reference/dependency database:** `N/A - no citation database configured for this project`
**Figures directory:** `docs/figures/`
**Agent files:** `.github/agents/`

### Workstream → Source File Mapping

"""
_IC_BEGIN = "<!-- AGENTTEAMS:BEGIN invariant_core"
_IC_END = "<!-- AGENTTEAMS:END invariant_core -->\n"
_WS_BEGIN = "<!-- AGENTTEAMS:BEGIN workstream_source_map"


def _base(structure: str = STRUCTURE, head: str = HEAD, render: str = RENDER) -> str:
    """The MarketForge base layout: the unfenced twin directly above the fence an earlier merge added,
    and no unfenced Project Structure anywhere else."""
    after_ic = render.index(_IC_END) + len(_IC_END)
    deployed = render[:after_ic] + "\n" + render[render.index(_WS_BEGIN):]
    return deployed.replace(_IC_BEGIN, head + structure + _IC_BEGIN, 1)


def _merge(existing: str, render: str = RENDER, *, rel: str = "navigator.agent.md", drop: bool = True):
    return _merge_fenced_content(render, existing, rel_path=rel, twin_source=SOURCE, twin_drop=drop)


def _count(text: str, heading: str) -> int:
    return len(re.findall(rf"^[ \t]*{re.escape(heading)}[ \t]*$", text, re.MULTILINE))


def _assert_every_template_block(text: str) -> None:
    for heading in ("## Invariant Core", "### Project Structure", "### Workstream → Source File Mapping",
                    "### Team Topology Graph", "## Invariant Rules"):
        assert _count(text, heading) == 1, heading


# --- the fence_twins path ---------------------------------------------------------------------

def test_the_dropped_twin_keeps_project_structure_and_moves_it_below_the_fence():
    res = _merge(_base())
    merged = res.merged_content
    assert list(res.twins_dropped) == ["invariant_core"]
    _assert_every_template_block(merged)
    assert merged.index(_IC_END) < merged.index("### Project Structure") < merged.index(_WS_BEGIN), \
        "the kept blocks sit where the template puts them"
    assert "### Project Structure" not in res.twins_dropped["invariant_core"], "the sidecar holds only the head"
    notice = " ".join(res.duplicate_section_notices)
    assert "moved below fence 'invariant_core'" in notice and "'### Project Structure'" in notice


def test_the_merge_is_idempotent():
    first = _merge(_base()).merged_content
    second = _merge(first)
    assert second.merged_content == first and not second.twins_dropped and not second.twins_kept


def test_an_edited_project_structure_no_longer_keeps_a_second_invariant_core():
    """healthResearch: hand-edited values used to make the whole twin unprovable, so it was kept."""
    edited = STRUCTURE.replace("`src/`", "`Projects/` (`reports/` where present)")
    res = _merge(_base(structure=edited))
    _assert_every_template_block(res.merged_content)
    assert "`Projects/` (`reports/` where present)" in res.merged_content, "project-owned text is kept"


def test_an_unprovable_head_keeps_the_whole_twin_and_flags_it():
    """Fail safe: nothing is cut unless the head is proven template text."""
    res = _merge(_base(head=HEAD.replace("immediately.", "immediately, and tell the lead.")))
    assert res.twins_kept == ["## Invariant Core"] and not res.twins_dropped
    assert _count(res.merged_content, "### Project Structure") == 1
    assert fence_twins.KEPT_PREFIX in " ".join(res.duplicate_section_notices)


def test_a_tail_away_from_its_fence_stays_in_place():
    deployed = _base().replace(STRUCTURE + _IC_BEGIN, STRUCTURE + "<!-- AGENTTEAMS:BEGIN zz v=1 -->\nz\n"
                               "<!-- AGENTTEAMS:END zz -->\n\n" + _IC_BEGIN, 1)
    render = RENDER.replace(_IC_BEGIN, "<!-- AGENTTEAMS:BEGIN zz v=1 -->\nz\n<!-- AGENTTEAMS:END zz -->\n\n"
                            + _IC_BEGIN, 1)
    res = _merge(deployed, render=render)
    _assert_every_template_block(res.merged_content)
    assert res.merged_content.index("### Project Structure") < res.merged_content.index("BEGIN zz")
    assert "left in place" in " ".join(res.duplicate_section_notices)


def test_without_a_fresh_render_the_twin_is_kept():
    deployed = _base()
    out = fence_twins.resolve(deployed, fence_twins._extract_fenced_regions(RENDER), SOURCE, allow_drop=True)
    assert out.content == deployed and out.kept == ["## Invariant Core"]
    assert "no fresh render" in " ".join(out.notices)


def test_twin_head_ignores_its_own_heading_and_code_blocks():
    outside = frozenset({"### Project Structure"})
    text = "### Project Structure\n\n```\n### Project Structure\n```\n\nx\n"
    assert fence_twins.twin_head(text, outside) == text
    assert fence_twins.twin_head(HEAD + STRUCTURE, outside) == HEAD
    assert "### Project Structure" in fence_twins.outside_headings(RENDER)
    assert "### Core Responsibilities" not in fence_twins.outside_headings(RENDER)


def test_a_tail_the_file_already_carries_stays_in_place_and_is_flagged():
    """A hand-restored Project Structure below the fence: the kept tail is not moved next to it."""
    deployed = _base().replace(_IC_END, _IC_END + "\n" + STRUCTURE, 1)
    res = _merge(deployed)
    assert list(res.twins_dropped) == ["invariant_core"] and _count(res.merged_content, "## Invariant Core") == 1
    assert _count(res.merged_content, "### Project Structure") == 2, "kept, never dropped"
    notices = " ".join(res.duplicate_section_notices)
    assert "left in place" in notices and "duplicate section '### Project Structure'" in notices


def test_an_acknowledged_twin_the_cut_would_split_is_kept_whole():
    edited = STRUCTURE.replace("`src/`", "`Projects/`")
    deployed = _base(structure=edited)
    rel = "navigator.agent.md"
    span = fence_twins.unfenced_section(deployed, "## Invariant Core")
    digest = fence_twins.ack_digest(rel, "## Invariant Core", deployed[span[0]:span[1]])
    ack = TwinAck(rel, "## Invariant Core", digest, "kept on purpose after review by the operator")
    source = TwinSource(TEMPLATE, VALUES, rel, (ack,))
    res = _merge_fenced_content(RENDER, deployed, rel_path=rel, twin_source=source, twin_drop=True)
    assert res.merged_content == deployed and res.twins_kept == ["## Invariant Core"]
    assert fence_twins.ACKED_PREFIX in " ".join(res.duplicate_section_notices)


# --- frameworks: claude (fence added by this merge) and goose -----------------------------------

def test_claude_file_whose_fence_this_merge_adds():
    """sc_collectors .claude: the base has only the unfenced section; the merge adds the fence."""
    claude = "---\nname: navigator\ndescription: Navigator\ntools: Read, Grep\n---\n\n"
    render = claude + RENDER.split("---\n", 2)[2].lstrip("\n")
    lines = render.split(_IC_BEGIN, 1)
    body_ic = lines[1][lines[1].index(_IC_END) + len(_IC_END):]
    base = lines[0] + HEAD + STRUCTURE + body_ic[body_ic.index(_WS_BEGIN):]
    base = re.sub(r"<!-- AGENTTEAMS:BEGIN code_index_consultation.*?END code_index_consultation -->\n",
                  "", base, flags=re.S)
    res = _merge(base, render=render, rel="navigator.md")
    assert "invariant_core" in res.sections_added
    _assert_every_template_block(res.merged_content)
    again = _merge(res.merged_content, render=render, rel="navigator.md")
    assert again.merged_content == res.merged_content


def _goose(body: str) -> str:
    indented = "".join("  " + line if line.strip() else line for line in body.splitlines(keepends=True))
    return ('version: "1.0.0"\ntitle: "Navigator — Acme"\ndescription: "Navigator"\ninstructions: |\n'
            + indented + "extensions:\n  - type: builtin\n    name: developer\n")


def test_goose_recipe_keeps_project_structure_and_its_indent():
    body = RENDER.split("---\n", 2)[2]
    render, base = _goose(body), _goose(_base(render=body))
    res = _merge(base, render=render, rel="navigator.yaml")
    merged = res.merged_content
    assert list(res.twins_dropped) == ["invariant_core"]
    _assert_every_template_block(merged)
    head, rest = merged.split("instructions: |\n", 1)
    block, tail = rest.split("\nextensions:", 1)
    assert head == base.split("instructions: |\n", 1)[0] and tail == base.split("\nextensions:", 1)[1]
    assert all(not line.strip() or line.startswith("  ") for line in block.splitlines())
    assert _merge(merged, render=render, rel="navigator.yaml").merged_content == merged


# --- the whole-body migration path --------------------------------------------------------------

def test_whole_body_migration_keeps_the_template_blocks_after_a_kept_title():
    """portal: the deployed title replaced the render's, and its continuations were skipped."""
    old = ("---\nname: Navigator\n---\n\n# Navigator Agent — Acme\n\nHand-written intro for this repo.\n\n"
           "## Custom Map\n\nmine\n")
    deployed = _normalize_generated_content("navigator.agent.md", old)
    assert "AGENTTEAMS:BEGIN content" in deployed
    res = _merge(deployed)
    merged = res.merged_content
    assert res.migrated
    assert "Hand-written intro for this repo." in merged and "## Custom Map" in merged
    _assert_every_template_block(merged)
    assert _merge(merged).merged_content == merged


def test_whole_body_continuation_keeps_deeper_headings_inside_their_block():
    """A ``####`` the deployed title section happens to carry must not be cut out of an added block."""
    render = RENDER.replace("### Team Topology Graph\n", "### Team Topology Graph\n\n#### Legend\n\nlegend text\n", 1)
    old = ("---\nname: Navigator\n---\n\n# Navigator Agent — Acme\n\nIntro.\n\n#### Legend\n\nmine\n\n"
           "## Custom Map\n\nmine\n")
    res = _merge(_normalize_generated_content("navigator.agent.md", old), render=render)
    merged = res.merged_content
    _assert_every_template_block(merged)
    block = merged[merged.index("### Team Topology Graph"):]
    assert "#### Legend\n\nlegend text" in block
