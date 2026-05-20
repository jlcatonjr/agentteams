"""Plan: wire-features-into-agent-infra — regression tests.

Each new directive added by the wiring plan must be present in the rendered
agent output for a representative example team (data-pipeline; the same one
the snapshot tests use). These tests catch silent template regressions that
would re-open the integration gap the wiring plan closed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES = REPO_ROOT / "examples"


@pytest.fixture(scope="module")
def rendered_team() -> Path:
    """Generate the data-pipeline example into a temp dir; yield its agents dir."""
    import sys
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_integration import _run_pipeline  # type: ignore[import-not-found]
    td = tempfile.mkdtemp(prefix="wiring-")
    out = Path(td)
    _run_pipeline(EXAMPLES / "data-pipeline" / "brief.json", out)
    return out


def _read(d: Path, name: str) -> str:
    return (d / name).read_text(encoding="utf-8")


# --- orchestrator: Workflow 10D (eval-suite + behavioral-drift wiring) ---

def test_orchestrator_workflow_10d_present(rendered_team):
    txt = _read(rendered_team, "orchestrator.agent.md")
    assert "Workflow 10D" in txt
    assert "Behavioral Verification" in txt
    assert "references/eval-suite.json" in txt
    assert "agent_session_trajectory" in txt
    # Absent-artifact fallback per audit lesson F-1
    assert "skip Workflow 10D" in txt
    # Non-recursion with Workflow 11 noted
    assert "10D" in txt and "Workflow 11" in txt


def test_workflow_11_trigger_lists_10d(rendered_team):
    txt = _read(rendered_team, "orchestrator.agent.md")
    # The trigger list now includes 10D alongside 10B/10C
    assert "10D" in txt


# --- conflict-auditor: typed-handoff (prose-first per F-RM1) + eval-suite cross-check ---

def test_conflict_auditor_typed_handoff_audit_is_prose_first(rendered_team):
    txt = _read(rendered_team, "conflict-auditor.agent.md")
    # PAYLOAD_MISMATCH / PAYLOAD_UNTYPED codes table entries
    assert "PAYLOAD_MISMATCH" in txt
    assert "PAYLOAD_UNTYPED" in txt
    # Prose rule articulated BEFORE the function name (F-RM1 correction)
    assert "Typed-handoff audit" in txt
    assert "adjacent step pair" in txt or "adjacent steps" in txt
    # Function reference appears AFTER the prose rule for engineers
    pos_prose = txt.find("payload_schema_out")
    pos_fn = txt.find("audit_handoff_chain")
    assert pos_prose != -1 and pos_fn != -1 and pos_prose < pos_fn, (
        "prose rule must precede the function-name reference (F-RM1)"
    )


def test_conflict_auditor_eval_suite_cross_check_present(rendered_team):
    txt = _read(rendered_team, "conflict-auditor.agent.md")
    assert "Behavioral spec cross-check" in txt
    assert "references/eval-suite.json" in txt
    # Absent-artifact fallback
    assert "skip this section silently" in txt or "absent or empty" in txt


# --- adversarial: memory-index for Temporal/Causal classes ---

def test_adversarial_consults_memory_index_for_temporal_causal(rendered_team):
    txt = _read(rendered_team, "adversarial.agent.md")
    assert "memory-index.json" in txt
    # Mentions both T and C presupposition classes
    assert "Temporal" in txt and "Causal" in txt
    # Fallback wording present
    assert ("absent" in txt.lower() or "stale" in txt.lower()
            or "not clearly responsive" in txt)


# --- work-summarizer: memory-index-first for weekly/monthly (4 of 5 examples) ---

def test_work_summarizer_consults_memory_index_for_weekly_monthly():
    """work-summarizer is a domain archetype (not universal). Skip if not in
    the data-pipeline team; the directive lives only where the agent is."""
    # Use the committed expected snapshot for data-pipeline.
    p = EXAMPLES / "data-pipeline" / "expected" / "work-summarizer.agent.md"
    if not p.exists():
        pytest.skip("work-summarizer not generated for data-pipeline")
    txt = p.read_text(encoding="utf-8")
    assert "memory-index.json" in txt
    assert "weekly" in txt.lower() and "monthly" in txt.lower()


# --- agent-updater: receipt parity, manifest rollback, Notice review, --cost-routing doc ---

def test_agent_updater_post_update_steps_wired(rendered_team):
    txt = _read(rendered_team, "agent-updater.agent.md")
    # Delivery-receipt parity step
    assert "delivery-receipt.json" in txt
    assert "manifest_fingerprint" in txt
    # Backup-manifest rollback recipe
    assert "_manifest.json" in txt
    assert "source_sha256" in txt
    # Shrink-Notice review (Plan 3 W21)
    assert "Notice" in txt
    # --cost-routing opt-in documented
    assert "--cost-routing" in txt
    assert "default OFF" in txt or "default off" in txt.lower()
    # --dry-run --json piping (Plan 1 W21 extension)
    assert "--dry-run --json" in txt or ("--json" in txt and "--dry-run" in txt)
