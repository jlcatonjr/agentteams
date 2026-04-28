from pathlib import Path


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
    assert "If a plan reached all `done` during this session" in text


def test_copilot_instructions_template_matches_work_summary_plan_contract() -> None:
    text = Path("agentteams/templates/copilot-instructions.template.md").read_text(encoding="utf-8")

    assert "tmp/by-week/YYYY-Www/<plan-slug>.plan.md" in text
    assert "legacy undated plans from `tmp/`" in text
    assert "canonical `tmp/by-week/` plan artifacts" in text
    assert "Completed plans must be captured in daily work summaries" in text