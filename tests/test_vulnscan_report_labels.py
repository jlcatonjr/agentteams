"""Report labels: a pre-release "Upgrade to" target is marked, and the held count says when it was taken."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentteams.vulnscan import effectiveness
from agentteams.vulnscan.report import Finding, Report, _fix_cell


@pytest.mark.parametrize("fix, marked", [
    ("26.10.0rc1", True), ("2.0.0b2", True), ("1.0.dev3", True), ("v3.1.0-rc.2", True), ("1.0.0-alpha.1", True),
    ("1.2.3", False), ("1.2.post1", False), ("2.3-4ubuntu1", False), ("1:2.3-4", False),
])
def test_pre_release_targets_are_marked(fix: str, marked: bool) -> None:
    cell = _fix_cell(fix)
    assert cell.startswith(fix)
    assert ("(pre-release; the advisory names no stable fix)" in cell) is marked


def test_no_fix_renders_a_dash() -> None:
    assert _fix_cell(None) == "—"
    assert _fix_cell("") == "—"


def test_markdown_table_marks_the_twisted_rc_target() -> None:
    rep = Report(target="r")
    rep.findings.append(Finding(kind="package", target="vk-prod", name="Twisted", version="24.11.0",
                                ecosystem="PyPI", fix="26.10.0rc1", severity=3))
    assert "| 26.10.0rc1 (pre-release; the advisory names no stable fix) |" in rep.markdown()


def test_held_count_is_labelled_at_scan_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTTEAMS_VULNSCAN_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(effectiveness, "staged_without_pr", lambda: 0)
    monkeypatch.setattr(effectiveness.retry_guard, "held_count", lambda: 6)
    md, _ = effectiveness.section(Report(target="r"), None, stats=effectiveness.PrStats())
    assert "| Repositories held at scan time (nothing fixable without a major upgrade) | 6 |" in md
