"""Tests for the work-summary backfill integration (Workflow D + Past-Day Backfill
Obligation + the single-source-of-truth backfill reference)."""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_integration import _run_pipeline, EXAMPLES_DIR  # noqa: E402

_EXAMPLES = ["software-project", "research-project", "data-pipeline", "project-repositories"]
# collector-management-specific literals that must NOT leak into the generic template.
_CM_LEAKS = (
    "tmp/by-week/2026-", "BLUE", "0995", "1166", "1126", "1156", "1116",
    "a17b3d72", "4054651d", "8f0b47d6",
)


def _render(example: str) -> dict[str, str]:
    tmp = Path(tempfile.mkdtemp())
    out = _run_pipeline(EXAMPLES_DIR / example / "brief.json", tmp)
    return dict(out["rendered"])


# ---------------------------------------------------------------------------
# Registration / emission
# ---------------------------------------------------------------------------

def test_backfill_reference_registered_unconditionally():
    """The reference is appended in analyze.py with no archetype guard — it is
    emitted for every team (the spec/tooling precedent)."""
    from agentteams import ingest, analyze
    desc = ingest.load(EXAMPLES_DIR / "software-project" / "brief.json", scan_project=False)
    manifest = analyze.build_manifest(desc, framework="copilot-vscode")
    paths = {f["path"] for f in manifest["output_files"]}
    assert "references/work-summary-backfill.reference.md" in paths


@pytest.mark.parametrize("example", _EXAMPLES)
def test_backfill_reference_emitted(example):
    rendered = _render(example)
    assert any("work-summary-backfill.reference.md" in p for p in rendered)


# ---------------------------------------------------------------------------
# Single-source-of-truth (DRY) — the cap literal lives in exactly one place
# ---------------------------------------------------------------------------

def test_cap_literal_defined_once_in_reference():
    rendered = _render("software-project")
    ref = next(c for p, c in rendered.items() if "work-summary-backfill.reference" in p)
    # The literal 14 appears exactly once, on the constant's definition line.
    lines_with_14 = [ln for ln in ref.splitlines() if re.search(r"\b14\b", ln)]
    assert len(lines_with_14) == 1, lines_with_14
    assert "Default" in lines_with_14[0] and "14" in lines_with_14[0]
    assert "AUTO_BACKFILL_LOOKBACK_CAP_DAYS" in ref


def test_pointer_surfaces_use_name_not_literal():
    """Orchestrator + work-summarizer reference the constant by NAME; they do not
    restate the cap value or the window/partition (avoids same-fact drift)."""
    rendered = _render("software-project")
    orch = next(c for p, c in rendered.items() if p.endswith("orchestrator.agent.md"))
    ws = next(c for p, c in rendered.items() if "work-summarizer" in p)
    for doc in (orch, ws):
        assert "AUTO_BACKFILL_LOOKBACK_CAP_DAYS" in doc
        # Must point at the reference for semantics rather than restating them.
        assert "work-summary-backfill.reference.md" in doc


# ---------------------------------------------------------------------------
# Presence of the three coordinated pieces
# ---------------------------------------------------------------------------

def test_orchestrator_has_obligation_rule_and_step8():
    orch = next(c for p, c in _render("software-project").items()
                if p.endswith("orchestrator.agent.md"))
    assert "Past-Day Backfill Obligation" in orch          # constitutional rule
    assert "Past-day backfill (Past-Day Backfill Obligation)" in orch  # closeout step 8
    assert "Workflow D — Automatic Backfill Sweep" in orch


def test_work_summarizer_has_workflow_d():
    ws = next(c for p, c in _render("software-project").items() if "work-summarizer" in p)
    assert "Workflow D — Automatic Backfill Sweep" in ws
    assert "create` mode only" in ws or "create mode only" in ws
    assert "work-summary-backfill.reference.md" in ws


# ---------------------------------------------------------------------------
# No collector-management-specific content leaked into the generic template
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("example", _EXAMPLES)
def test_no_cm_specific_leak_in_reference(example):
    ref = next(c for p, c in _render(example).items() if "work-summary-backfill.reference" in p)
    leaked = [t for t in _CM_LEAKS if t in ref]
    assert leaked == [], f"collector-management literals leaked: {leaked}"
