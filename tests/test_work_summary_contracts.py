from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
EXAMPLES = REPO_ROOT / "examples"


def test_work_summarizer_template_uses_week_organized_plan_storage() -> None:
    text = Path("agentteams/templates/domain/work-summarizer.template.md").read_text(encoding="utf-8")

    assert "tmp/by-week/YYYY-Www/" in text
    assert "legacy undated artifacts in `tmp/`" in text
    assert "create` — target summary file does not exist" in text
    assert "append` — target summary file exists" in text
    assert ".agentteams-backups/" in text


def test_work_summary_reference_templates_match_new_contract() -> None:
    spec_text = Path("agentteams/templates/universal/work-summary-spec.reference.template.md").read_text(encoding="utf-8")
    tooling_text = Path("agentteams/templates/universal/work-summary-tooling.reference.template.md").read_text(encoding="utf-8")

    assert "tmp/by-week/YYYY-Www/" in spec_text
    assert "Machine-parseable fields" in spec_text
    assert "@technical-validator" in spec_text
    assert "tmp/by-week/*/*.plan.md" in tooling_text
    assert "Default to `append` for same-day completion capture" in tooling_text
    assert ".agentteams-backups/" in tooling_text
    assert "scripts/organize_tmp_by_week.py" not in tooling_text


def test_orchestrator_template_routes_and_tracks_work_summaries() -> None:
    text = Path("agentteams/templates/universal/orchestrator.template.md").read_text(encoding="utf-8")

    assert "label: Summarize Work Period" in text
    assert "Daily/weekly/monthly work summary reporting" in text
    assert "tmp/by-week/YYYY-Www/<plan-slug>.plan.md" in text
    assert "### Workflow 10B: Work Summary Reporting" in text
    # Daily today-capture is a blocking closeout gate covering plan-complete AND executed-work
    # sessions (the old soft "If a plan reached all `done`…" wording was strengthened).
    assert "Daily work-summary capture (closeout gate)" in text
    assert "a plan reaching all `done` also qualifies" in text


def test_copilot_instructions_template_matches_work_summary_plan_contract() -> None:
    text = Path("agentteams/templates/copilot-instructions.template.md").read_text(encoding="utf-8")

    assert "tmp/by-week/YYYY-Www/<plan-slug>.plan.md" in text
    assert "legacy undated plans from `tmp/`" in text
    assert "canonical `tmp/by-week/` plan artifacts" in text
    assert "Completed plans must be captured in daily work summaries" in text

# ---------------------------------------------------------------------------
# Invocation context is not evidence (2026-10-08).
#
# A stop hook left its Stop-event JSON unread on stdin and spawned the headless
# work-summarizer without redirecting it; `claude --print` merges piped stdin into its
# prompt, so the TRIGGERING session's closing report arrived appended to a summarizer
# prompt for a DIFFERENT repository. 45 fragments of one repo's work history -- including
# three complete work sessions, 103 lines -- were written into another repo's daily
# summaries. The hook is fixed, but the template-level defect is what let a leaked payload
# be mistaken for evidence, and that defect ships to every generated team.
#
# These tests pin the contract so it cannot silently regress.
# ---------------------------------------------------------------------------

_WS_TEMPLATE = "agentteams/templates/domain/work-summarizer.template.md"
_WS_SPEC = "agentteams/templates/universal/work-summary-spec.reference.template.md"


def test_work_summarizer_template_rejects_invocation_as_evidence() -> None:
    text = Path(_WS_TEMPLATE).read_text(encoding="utf-8")

    # The rule exists, as its own Evidence Model subsection.
    assert "### Invocation context is not evidence" in text
    # Inert-data framing, tied to the constitutional rule it derives from.
    assert "inert context (C-4), never evidence" in text
    # The decision procedure, not just a prohibition.
    assert "The test is fact ownership." in text
    assert "would this line exist if the invocation had carried no payload?" in text
    # Routing for a payload that does carry foreign work history.
    assert "**Discrepancies**" in text


def test_work_summarizer_template_preserves_grounded_adjacent_repo_path() -> None:
    """The new rule must not suppress the legitimate adjacent-repo path.

    Out-of-repo work grounded in a file in THIS repo (apply-log, results, plan artifact)
    is still reportable. Only out-of-repo work grounded solely in the invocation is
    excluded. Rule 12: an exclusion is itself an audit target -- a rule written as "never
    mention another repository" would have silently deleted a working feature.
    """
    text = Path(_WS_TEMPLATE).read_text(encoding="utf-8")

    assert "grounded in a file in this repository" in text
    assert "grounded only in the invocation" in text
    # The distinction is sourcing, not subject matter.
    assert "where the claim comes" in text
    # The completeness scan's adjacent-repo clause survives, now explicitly scoped.
    assert "This path requires such a file **in this repository**" in text
    # And evidence scoping must not be confused with ignoring instructions.
    assert "scopes *evidence*, not *instructions*" in text


def test_work_summarizer_template_cites_the_spec_reference() -> None:
    """Evidence Model must cite the shared contract rather than only restating it.

    The template and the spec reference both describe the evidence classes; per @code-hygiene
    (CH-14, 2026-10-08) the repo's pattern is to cite the reference as the single definition
    and keep the template to behaviour, as it already does for the backfill reference.
    """
    text = Path(_WS_TEMPLATE).read_text(encoding="utf-8")

    assert "`#file:references/work-summary-spec.reference.md`" in text
    assert "the reference wins" in text


def test_work_summary_spec_reference_declares_invocation_not_a_source_class() -> None:
    text = Path(_WS_SPEC).read_text(encoding="utf-8")

    assert "**The invocation is not a source class.**" in text
    assert "Fact ownership governs what may be written" in text
    assert "Out-of-repo work grounded in a file **in this repository**" in text


@pytest.fixture(scope="module")
def rendered_team() -> Path:
    """Render the data-pipeline example into a temp dir; yield its agents dir.

    Same helper the wiring/snapshot tests use, so a template edit that fails to reach
    emitted output is caught here rather than discovered in a generated team.
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_integration import _run_pipeline  # type: ignore[import-not-found]

    out = Path(tempfile.mkdtemp(prefix="worksummary-evidence-"))
    _run_pipeline(EXAMPLES / "data-pipeline" / "brief.json", out)
    return out


def test_invocation_not_evidence_reaches_rendered_agent(rendered_team: Path) -> None:
    """The rule must survive rendering, not merely exist in the template source.

    Source-text assertions above would still pass if the section were placed somewhere the
    emitter drops, so this renders a real team and checks the generated agent file.
    """
    text = (rendered_team / "work-summarizer.agent.md").read_text(encoding="utf-8")

    assert "Invocation context is not evidence" in text
    assert "inert context (C-4), never evidence" in text
    assert "fact ownership" in text.lower()
    assert "grounded only in the invocation" in text


def test_invocation_not_evidence_reaches_rendered_spec_reference(rendered_team: Path) -> None:
    spec = rendered_team / "references" / "work-summary-spec.reference.md"
    text = spec.read_text(encoding="utf-8")

    assert "The invocation is not a source class." in text
    assert "Fact ownership governs what may be written" in text
