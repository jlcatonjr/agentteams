"""test_script_consolidation_reference.py — the script-consolidation reference every team receives.

The document exists because two repositories ran the same script review differently on the same
day, and both reported tools as missing that existed on the default branch. These tests pin what
makes it worth shipping: it reaches every team, it requires the reference ref beside each coverage
verdict, it makes no conformance claim, and the project's placement rule survives regeneration.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "agentteams" / "templates"
TEMPLATE = TEMPLATES / "universal" / "script-consolidation.reference.template.md"
EXAMPLES = sorted(p.parent.name for p in (REPO / "examples").glob("*/brief.json"))


def _rendered(example: str) -> dict[str, str]:
    from agentteams import analyze, ingest, render

    description = ingest.load(REPO / "examples" / example / "brief.json", scan_project=False)
    manifest = analyze.build_manifest(description, framework="copilot-vscode")
    return dict(render.render_all(manifest, templates_dir=TEMPLATES))


@pytest.mark.parametrize("example", EXAMPLES)
def test_every_team_receives_the_reference_and_its_auditor_points_to_it(example: str) -> None:
    files = _rendered(example)
    doc = files["references/script-consolidation.reference.md"]
    assert "{PROJECT_NAME}" not in doc and "{MANUAL:" not in doc
    assert "#file:references/script-consolidation.reference.md" in files["code-hygiene.agent.md"]


def test_a_coverage_verdict_must_carry_the_reference_ref() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "python -m agentteams.ref_branch" in text
    assert "Reference branch:" in text
    # The reviewer brief and the accuracy audit both repeat the requirement, not only the preamble.
    brief = text.split("## Reviewer brief", 1)[1].split("## Audit prompts", 1)[0]
    audit = text.split("## Audit prompts", 1)[1].split("## What is written down", 1)[0]
    assert "Reference branch:" in brief and "default branch" in audit


def test_it_claims_no_conformance() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "not a\nconformance check" in text or "not a conformance check" in text
    assert not re.search(r"\bCH-(?:08|27)\b[^.\n]*\b(?:PASS|passes|compliant|violation)\b", text)


def test_the_placement_rule_is_user_editable_and_outside_the_fence() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    manifest = text.split("-->", 1)[0]
    assert re.search(r"\|\s*script_consolidation\s*\|\s*FENCED\s*\|", manifest)
    assert re.search(r"\|\s*placement_decision_rule\s*\|\s*USER-EDITABLE\s*\|", manifest)
    begin = text.index("<!-- AGENTTEAMS:BEGIN script_consolidation v=1 -->")
    end = text.index("<!-- AGENTTEAMS:END script_consolidation -->")
    section = text.index("## Placement Decision Rule")
    assert begin < end < section
    tail = text[section:]
    assert "USER-EDITABLE" in tail and "AGENTTEAMS:" not in tail
    # Questions only: a rule stated outside a fence would not be restored if a project deleted it.
    assert not re.search(r"\b(?:must|never|always|shall)\b", tail.split("\n\n", 2)[2], re.I)


def test_the_orchestrator_gained_no_lines_for_it() -> None:
    orchestrator = (TEMPLATES / "universal" / "orchestrator.template.md").read_text(encoding="utf-8")
    assert "script-consolidation" not in orchestrator
