"""#454: the work-summary entry schema and the closed trigger catalog (book Ch 16 §4, §4.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentteams.analyze import build_manifest
from agentteams.render import render_all
from agentteams.unfenced import unfenced_lines

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "agentteams" / "templates"
REFERENCE = TEMPLATES / "universal" / "work-summary-trigger-catalog.reference.template.md"
SUMMARIZER = TEMPLATES / "domain" / "work-summarizer.template.md"
REF_PATH = "references/work-summary-trigger-catalog.reference.md"

TRIGGERS = [
    "workflow-step-completion",
    "blocker",
    "contradiction-resolution",
    "cross-repository-impact",
    "security-clearance",
    "deployment-outcome",
    "boundary-decision",
    "unclassified-constitutional-event",
]
FIELDS = [
    "Trigger",
    "Timestamp",
    "Contributing agent",
    "Affected scope",
    "Disposition",
    "Unresolved obligations",
    "Sources",
]


def _catalog_rows(text: str) -> list[list[str]]:
    section = text.split("## Trigger Catalog", 1)[1].split("## Rules", 1)[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("| `")]
    return [[c.strip() for c in ln.strip("|").split("|")] for ln in rows]


def test_reference_is_planned_and_rendered_for_every_team() -> None:
    manifest = build_manifest({"project_goal": "x"})
    assert REF_PATH in {f["path"] for f in manifest["output_files"]}
    rendered = dict(render_all(manifest, templates_dir=TEMPLATES))
    [text] = [c for p, c in rendered.items() if p.endswith("work-summary-trigger-catalog.reference.md")]
    assert "{PROJECT_NAME}" not in text


def test_catalog_is_closed_with_eight_typed_rows() -> None:
    rows = _catalog_rows(REFERENCE.read_text(encoding="utf-8"))
    assert [r[0].strip("`") for r in rows] == TRIGGERS
    for row in rows:
        assert len(row) == 5 and all(row), row  # type, initiator, required fields, destination, closure


def test_entry_block_carries_the_seven_fields_in_order() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    block = text.split("```markdown", 1)[1].split("```", 1)[0]
    assert re.findall(r"^- \*\*(.+?):\*\*", block, flags=re.M) == FIELDS


def test_summarizer_table_matches_the_reference_fields() -> None:
    text = SUMMARIZER.read_text(encoding="utf-8")
    section = text.split("## Entry Schema", 1)[1].split("## Workflows", 1)[0]
    assert re.findall(r"^\| `(.+?)` \|", section, flags=re.M) == FIELDS
    assert REF_PATH in section
    assert "stays valid" in section


def test_rules_cover_provisional_entry_carry_forward_mirror_and_compatibility() -> None:
    rules = REFERENCE.read_text(encoding="utf-8").split("## Rules", 1)[1]
    assert "`unclassified-constitutional-event` entry in the same session" in rules
    assert "only under reversible\n   dispositions" in rules
    assert "**Obligations carry forward.**" in rules
    assert "**Boundary decisions are mirror records.**" in rules
    assert "**Existing summaries stay valid.**" in rules


def test_daily_and_weekly_workflows_carry_open_obligations() -> None:
    text = SUMMARIZER.read_text(encoding="utf-8")
    daily = text.split("### Daily Summary", 1)[1].split("#### Synthesized daily body", 1)[0]
    weekly = text.split("### Weekly Summary", 1)[1].split("### Monthly Summary", 1)[0]
    assert "## Open Obligations" in daily and "typed" in daily
    assert "obligations still open at\n   week end" in weekly


def test_every_reference_rule_is_fenced() -> None:
    # Only the SECTION MANIFEST comment sits outside the trigger_catalog fence.
    outside = [ln for ln in unfenced_lines(REFERENCE.read_text(encoding="utf-8")) if ln.strip()]
    assert all(ln.startswith(("<!--", "SECTION MANIFEST", "|", "-->")) for ln in outside), outside


@pytest.mark.parametrize("example", ["software-project", "research-project", "data-pipeline", "project-repositories"])
def test_goldens_ship_the_reference(example: str) -> None:
    assert (ROOT / "examples" / example / "expected" / REF_PATH).is_file()
