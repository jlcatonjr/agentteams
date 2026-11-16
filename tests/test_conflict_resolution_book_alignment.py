"""#452: the conflict-resolution template uses Chapter 10's dispositions and its presumption.

Book (``docs_src/book/chapters/10-conflict-resolution.html``): REJECT = the higher-authority side
stands and the other file is corrected; ACCEPT = the lower-authority side is correct, which needs
affirmative evidence; REVISE = both change. Under ambiguous evidence the resolver defaults to
REJECT, and escalation is a route for a case the hierarchy cannot settle, not a disposition.

The fixture ``tests/fixtures/conflict_resolution/pre-452.agent.md`` is the software-project golden
render as it stood before #452: what an existing team has deployed today.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentteams.fence_twins import TwinSource
from agentteams.fences import _merge_fenced_content

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "agentteams" / "templates" / "universal" / "conflict-resolution.template.md"
GOLDEN = ROOT / "examples" / "software-project" / "expected" / "conflict-resolution.agent.md"
PRE_452 = ROOT / "tests" / "fixtures" / "conflict_resolution" / "pre-452.agent.md"
REL = "conflict-resolution.agent.md"


def _fence(text: str, sid: str) -> str:
    match = re.search(rf"<!-- AGENTTEAMS:BEGIN {sid} v=(\d+) -->\n(.*?)<!-- AGENTTEAMS:END {sid} -->", text, re.S)
    assert match, f"no {sid} fence"
    return match.group(2)


def _version(text: str, sid: str) -> int:
    match = re.search(rf"<!-- AGENTTEAMS:BEGIN {sid} v=(\d+) -->", text)
    assert match, f"no {sid} fence"
    return int(match.group(1))


def _table_rows(section: str) -> dict[str, str]:
    rows = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 2 and not set(cells[0]) <= {"-"}:
            rows[cells[0]] = " | ".join(cells[1:])
    return rows


def test_the_disposition_table_has_the_books_meanings_and_no_escalate_row() -> None:
    rows = _table_rows(_fence(TEMPLATE.read_text(), "dispositions"))
    assert set(rows) == {"Disposition", "REJECT", "ACCEPT", "REVISE", "NO-CONFLICT"}
    assert "higher-authority side is correct" in rows["REJECT"]
    assert "Correct the lower-authority file" in rows["REJECT"]
    assert "lower-authority side is correct" in rows["ACCEPT"]
    assert "affirmative evidence" in rows["ACCEPT"]
    assert "Correct both files" in rows["REVISE"]
    assert "Change neither file" in rows["NO-CONFLICT"]


def test_the_presumption_and_escalation_route_are_fenced() -> None:
    body = _fence(TEMPLATE.read_text(), "dispositions")
    assert "When the evidence is ambiguous, the disposition is REJECT" in body
    assert "Escalation is a routing step, not a disposition" in body
    assert "`ESCALATE: <why the REJECT presumption does not settle it>`" in body
    assert "keep them" in body and "now `NO-CONFLICT`" in body and "now `REJECT`" in body


def test_rules_carry_the_presumption_and_are_bumped() -> None:
    text = TEMPLATE.read_text()
    assert _version(text, "rules") == 2
    rules = _fence(text, "rules")
    assert "Under ambiguous evidence, REJECT" in rules
    assert "ESCALATE only when" not in rules


def test_per_code_defaults_never_use_revise_for_correct_the_lower_file() -> None:
    rows = _table_rows(_fence(TEMPLATE.read_text(), "decision_rules"))
    codes = {k for k in rows if k.startswith("`")}
    assert codes == {f"`{c}` ({n})" for c, n in [
        ("TM", "Term Mismatch"), ("CC", "Claim Conflict"), ("AE", "Attribution Error"), ("SD", "Source Drift"),
        ("CN", "Count Mismatch"), ("HC", "Hierarchy Conflict"), ("SR", "Stale Reference"), ("PE", "Phantom Entry")]}
    for code, rule in rows.items():
        assert "REVISE the lower" not in rule, code
        assert "REVISE the deliverable" not in rule, code


def test_new_fences_are_in_the_section_manifest() -> None:
    manifest = TEMPLATE.read_text().split("-->", 1)[0]
    for sid in ("decision_rules", "dispositions", "rules", "memory_index_consultation"):
        assert re.search(rf"\|\s*{sid}\s*\|\s*FENCED", manifest), sid


def test_update_merge_replaces_the_old_unfenced_steps_in_a_deployed_team() -> None:
    """The old Step 2 and Step 3 copies are dropped (lineage corpus), not kept beside the new fences."""
    source = TwinSource(template="universal/conflict-resolution.template.md",
                        placeholders={"CONFLICT_LOG_PATH": ".github/agents/references/conflict-log.csv"},
                        rel_path=REL)
    result = _merge_fenced_content(GOLDEN.read_text(), PRE_452.read_text(), twin_drop=True, rel_path=REL,
                                   twin_source=source)
    merged = result.merged_content
    assert merged.count("### Step 2: Apply Decision Rules") == 1
    assert merged.count("### Step 3: Apply Decision") == 1
    assert "not actually a conflict after investigation" not in merged
    assert "REVISE the lower" not in merged
    assert _fence(merged, "dispositions") == _fence(GOLDEN.read_text(), "dispositions")
    assert _version(merged, "rules") == 2
    assert result.shrink_notices == []
