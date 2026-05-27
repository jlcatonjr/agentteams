"""Tests for agentteams.budget (3.1 + 3.4 efficiency lints)."""

from __future__ import annotations

from pathlib import Path

from agentteams import budget


def _write_agent(tmp_path: Path, name: str, lines: int, *, prefix_date: bool = False, html_comment_date: bool = False) -> Path:
    p = tmp_path / name
    body_lines = ["---", "name: x", "description: y", "---", ""]
    if prefix_date:
        body_lines.append("Recorded on 2026-05-27.")
    if html_comment_date:
        body_lines.append("<!-- generated: 2026-05-27 -->")
    body_lines.extend([f"Line {i}" for i in range(lines)])
    p.write_text("\n".join(body_lines), encoding="utf-8")
    return p


def test_small_agent_no_findings(tmp_path):
    _write_agent(tmp_path, "small.agent.md", 50)
    report = budget.scan_directory(tmp_path)
    assert report.scanned_files == 1
    assert report.findings == []
    assert not report.has_failures


def test_warn_threshold_fires(tmp_path):
    _write_agent(tmp_path, "biggish.agent.md", budget.BUDGET_WARN_LINES + 5)
    report = budget.scan_directory(tmp_path)
    cats = {f.category for f in report.findings}
    assert "budget-warn" in cats
    # Warn-only must not fail the report.
    assert not report.has_failures


def test_fail_threshold_fires(tmp_path):
    _write_agent(tmp_path, "huge.agent.md", budget.BUDGET_FAIL_LINES + 5)
    report = budget.scan_directory(tmp_path)
    cats = {(f.category, f.severity) for f in report.findings}
    assert ("budget-fail", "fail") in cats
    assert report.has_failures


def test_orchestrator_gets_higher_budget(tmp_path):
    # 700 lines: above BUDGET_FAIL_LINES (600) but under ORCHESTRATOR_FAIL_LINES (1000).
    _write_agent(tmp_path, "orchestrator.agent.md", 700)
    report = budget.scan_directory(tmp_path)
    # Non-orchestrator would have produced a budget-fail at 700 lines;
    # orchestrator must not.
    fails = [f for f in report.findings if f.severity == "fail"]
    assert fails == []


def test_orchestrator_can_still_fail(tmp_path):
    _write_agent(tmp_path, "orchestrator.agent.md", budget.ORCHESTRATOR_FAIL_LINES + 10)
    report = budget.scan_directory(tmp_path)
    assert report.has_failures


def test_prefix_volatile_date_flagged(tmp_path):
    _write_agent(tmp_path, "stamped.agent.md", 30, prefix_date=True)
    report = budget.scan_directory(tmp_path)
    cats = {f.category for f in report.findings}
    assert "prefix-volatile" in cats


def test_prefix_date_in_html_comment_is_exempt(tmp_path):
    _write_agent(tmp_path, "stamped.agent.md", 30, html_comment_date=True)
    report = budget.scan_directory(tmp_path)
    cats = {f.category for f in report.findings}
    assert "prefix-volatile" not in cats


def test_skips_non_agent_md_files(tmp_path):
    (tmp_path / "readme.md").write_text("# x\n", encoding="utf-8")
    _write_agent(tmp_path, "agent.agent.md", 30)
    report = budget.scan_directory(tmp_path)
    assert report.scanned_files == 1


def test_missing_dir_returns_empty(tmp_path):
    report = budget.scan_directory(tmp_path / "does-not-exist")
    assert report.scanned_files == 0
    assert report.findings == []
