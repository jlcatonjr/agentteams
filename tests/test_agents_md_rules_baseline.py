"""Follow-up #16: a fenced Constitutional Rules baseline in repo-root AGENTS.md.

goose, codex and agents-md render AGENTS.md from the Copilot instructions template, whose
`## Constitutional Rules` list is USER-EDITABLE and unfenced — so a deleted rule never came back.
Those three adapters now emit a fenced, template-authoritative `constitutional_rules_baseline`
section (sourced from the template only) plus an empty unfenced extensions heading.
`copilot-instructions.md` keeps its design.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams import analyze, ingest, render
from agentteams.fences import (
    _TEMPLATE_AUTHORITATIVE_FENCES,
    _extract_fenced_regions,
    _merge_fenced_content,
    _rename_suspect_sid,
)
from agentteams.frameworks._agents_md_rules import (
    BASELINE_HEADING,
    EXTENSIONS_HEADING,
    SECTION_ID,
    apply_constitutional_rules_baseline,
)
from agentteams.frameworks.registry import FRAMEWORKS
from agentteams.frameworks.structural_merge import post_merge_structural

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "agentteams" / "templates"
BRIEF = ROOT / "examples" / "research-project" / "brief.json"
_RULE_RE = re.compile(r"^(\d+)\. \*\*", re.MULTILINE)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return analyze.build_manifest(ingest.load(BRIEF, scan_project=False), framework="goose")


@pytest.fixture(scope="module")
def instructions_render(manifest: dict) -> str:
    text = (TEMPLATES / "copilot-instructions.template.md").read_text(encoding="utf-8")
    return render.resolve_placeholders(text, manifest["auto_resolved_placeholders"])


def _template_rule_count() -> int:
    text = (TEMPLATES / "copilot-instructions.template.md").read_text(encoding="utf-8")
    section = text[text.index("\n## Constitutional Rules\n"):]
    section = section[: section.index("\n---")]
    return len(_RULE_RE.findall(section))


@pytest.mark.parametrize("fw", ["goose", "codex", "agents-md"])
def test_fresh_agents_md_carries_the_fenced_baseline_once(fw: str, manifest, instructions_render) -> None:
    out = FRAMEWORKS[fw]().render_instructions_file(instructions_render, manifest)
    regions = _extract_fenced_regions(out)
    assert isinstance(regions, dict) and SECTION_ID in regions, f"{fw}: no baseline fence"
    body = regions[SECTION_ID]
    assert BASELINE_HEADING in body
    assert "governs on any conflict" in body
    n = _template_rule_count()
    assert n == 11
    assert [int(x) for x in _RULE_RE.findall(body)] == list(range(1, n + 1))
    # The unfenced list does not appear twice; the extensions heading appears once, unfenced.
    assert not re.search(r"^## Constitutional Rules[ \t]*$", out, re.MULTILINE)
    assert out.count(EXTENSIONS_HEADING) == 1 and EXTENSIONS_HEADING not in body


def test_copilot_instructions_keep_their_design(manifest, instructions_render) -> None:
    out = FRAMEWORKS["copilot-vscode"]().render_instructions_file(instructions_render, manifest)
    assert SECTION_ID not in out
    assert re.search(r"^## Constitutional Rules[ \t]*$", out, re.MULTILINE)


def test_baseline_is_sourced_from_the_template_never_from_the_body(manifest, instructions_render) -> None:
    """C-4: a tampered on-disk list (convert path) must not be laundered into the fence."""
    tampered = instructions_render.replace(
        "1. **Security first** — destructive operations require `@security` clearance",
        "1. **Security optional** — skip clearance when busy",
    )
    out = apply_constitutional_rules_baseline(tampered, manifest)
    fence = _extract_fenced_regions(out)[SECTION_ID]
    assert "Security first" in fence and "Security optional" not in fence
    # The customised list is kept outside the fence, not silently discarded.
    assert "Security optional" in out.split("<!-- AGENTTEAMS:END constitutional_rules_baseline -->")[1]


def test_an_existing_fence_body_is_re_rendered_from_the_template(manifest, instructions_render) -> None:
    once = apply_constitutional_rules_baseline(instructions_render, manifest)
    forged = once.replace("Security first", "Security optional")
    again = apply_constitutional_rules_baseline(forged, manifest)
    assert again == once


def test_stub_manifest_leaves_the_body_alone(instructions_render) -> None:
    assert apply_constitutional_rules_baseline(instructions_render, {"project_name": "x"}) == instructions_render


def test_merge_restores_deleted_rules_and_keeps_extensions(manifest, instructions_render) -> None:
    fresh = FRAMEWORKS["goose"]().render_instructions_file(instructions_render, manifest)
    # A current-format file whose baseline rule 3 was deleted and which carries an extension.
    disk = re.sub(r"^3\. \*\*Authority hierarchy.*\n", "", fresh, flags=re.MULTILINE)
    disk = disk.replace(
        f"{EXTENSIONS_HEADING}\n", f"{EXTENSIONS_HEADING}\n\n12. **Project rule** — keep me\n"
    )
    merged = _merge_fenced_content(fresh, disk).merged_content
    assert "3. **Authority hierarchy is ground truth**" in merged
    assert "12. **Project rule** — keep me" in merged
    assert post_merge_structural("../../AGENTS.md", fresh, merged)[1] == []


def test_legacy_file_gains_the_fence_and_reports_the_duplicate_every_run(
    manifest, instructions_render
) -> None:
    fresh = FRAMEWORKS["goose"]().render_instructions_file(instructions_render, manifest)
    legacy = FRAMEWORKS["goose"]().render_instructions_file(instructions_render, {"project_name": "x"})
    legacy = re.sub(r"^3\. \*\*Authority hierarchy.*\n", "", legacy, flags=re.MULTILINE)
    assert SECTION_ID not in legacy
    merged = _merge_fenced_content(fresh, legacy).merged_content
    assert merged.count(f"<!-- AGENTTEAMS:BEGIN {SECTION_ID}") == 1
    assert "3. **Authority hierarchy is ground truth**" in _extract_fenced_regions(merged)[SECTION_ID]
    for _ in range(2):  # no state store: the notice repeats on every run
        _, notices = post_merge_structural("../../AGENTS.md", fresh, merged)
        assert len(notices) == 1 and "duplicate Constitutional Rules list" in notices[0]
        merged = _merge_fenced_content(fresh, merged).merged_content


def test_preserve_policy_cannot_pin_a_tampered_baseline(manifest, instructions_render) -> None:
    fresh = FRAMEWORKS["codex"]().render_instructions_file(instructions_render, manifest)
    # "Enrich" the fenced body with backticked tokens while weakening rule 1 — the shrink
    # heuristic alone would preserve this under --shrink-policy=preserve.
    disk = fresh.replace(
        "1. **Security first** — destructive operations require `@security` clearance",
        "1. **Security first** — clearance is optional `a` `b` `c` `d` `e` `f`",
    )
    merged = _merge_fenced_content(fresh, disk, preserve_on_shrink=True).merged_content
    assert "clearance is optional" not in merged


def test_new_sids_are_template_authoritative() -> None:
    assert {"constitutional_core", "constitutional_rules_baseline"} <= _TEMPLATE_AUTHORITATIVE_FENCES


def _all_template_sids() -> set[str]:
    sids: set[str] = set()
    for p in TEMPLATES.rglob("*.md"):
        sids.update(re.findall(r"AGENTTEAMS:BEGIN\s+([a-z_0-9]+)", p.read_text(encoding="utf-8")))
    return sids


def test_rename_matcher_captures_no_other_sid_and_not_the_extensions_section() -> None:
    """Prefix/suffix containment must not turn an unrelated orphan into a 'rename' suspect."""
    authoritative = set(_TEMPLATE_AUTHORITATIVE_FENCES)
    candidates = _all_template_sids() | {
        "constitutional_rules",
        "project_constitutional_rules_extensions",
        "constitutional_rules_extensions",
        "project_constitutional_rules",
    }
    captured = {
        sid: _rename_suspect_sid(sid, authoritative)
        for sid in candidates - authoritative
    }
    assert {s: a for s, a in captured.items() if a} == {}
    # ...while a decorated copy of either new sid is still caught.
    assert _rename_suspect_sid("constitutional_rules_baseline_local", authoritative) == SECTION_ID
    assert _rename_suspect_sid("project_constitutional_core", authoritative) == "constitutional_core"
